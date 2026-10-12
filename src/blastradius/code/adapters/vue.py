"""Vue 3 adapter: import graph, render graph, routes, i18n keys and text literals (D10, D24; cases 29-36, 40).

Everything is deterministic and offline. `<script>` goes through tree-sitter-typescript, `<template>` through
a small quote-aware tokenizer, and component tags resolve through the committed `components.d.ts` table with
the `directoryAsNamespace` rule as a fallback (see vue_components.py).

How the reasoner learns what is global (case 36, D29): `stats["global_files"]` lists the configured
`cfg.code.global_files` that exist in the index (for example the default layout); style-global files carry
`kind == style_global`. `CodeFile.package` stays the npm workspace name (`@hoppscotch/data`) or, for an
external leaf, the package itself.

Edge conventions: IMPORTS `symbols` are names as exported by the target (`default`, `*`, or named); a barrel's
`export { A } from './a'` is an IMPORTS edge barrel -> a with `symbols=["A"]`, so a symbol filter can walk
importer -> barrel -> a. Bare package specifiers become EXTERNAL leaves (case 34) with IMPORTS edges into
them; tags that resolve to a package are counted in stats and never edged.

Nothing is silent: symlinks that were not followed, unreadable or undecodable files, scripts tree-sitter
had to recover from or give up on, template languages and `src=` blocks that were not scanned, and a locale
file that does not parse are counted in `stats` (`parse_errors`, `parse_error_kinds`, `parse_error_sample`,
`symlinks_skipped`), and every configured root is reported (`code_roots`) so `run_index` can turn an empty
tree into a failure. Output is byte-identical across processes: every ordered collection is built from
sorted keys.

`index_part` is the same pipeline over a subset of files resolved against a larger path universe; the PR head
overlay (overlay.py) uses it to re-parse only the files a pull request changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from ...models import CodeEdge, CodeFile, CodeIndex, FileClass, KeyUse, RouteDef, TextLiteral
from ...targets import CodeConfig
from ..adapter import IndexPart, SourceTree
from ..fileclass import classify_file, workspace_package
from ..i18n import KeyScan, flatten_messages, scan_t_calls
from ..jsmodules import (
    CODE_EXTS,
    ImportBinding,
    ModuleResolver,
    Resolved,
    extract_imports,
    package_name,
    without_comments,
)
from .vue_components import NATIVE_TAGS, ComponentResolver, parse_components_dts, pascal_case
from .vue_routes import build_routes
from .vue_sfc import Block, scan_template, split_sfc

INDEXED_EXTS = frozenset(CODE_EXTS) | {".graphql", ".gql", ".json", ".css", ".scss", ".sass", ".less"}
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_TSX = ("tsx", "jsx")
_UNLISTED_CAP = 50
EdgeKind = Literal["IMPORTS", "RENDERS"]
# Problem kinds recorded per file (stats.parse_error_kinds); each means the index is incomplete for that file.
SCRIPT_PARSE_ERROR = "script_parse_error"            # error nodes in the parse (a real syntax error, or valid
                                                     # TS the grammar lacks): imports after it may be lost
SCRIPT_PARSE_ABORTED = "script_parse_aborted"        # the parse exceeded its work budget: no imports read
UNDECODABLE_BYTES = "undecodable_bytes"              # not UTF-8: decoded with replacement characters
UNREADABLE_FILE = "unreadable_file"                  # became a link or vanished between listing and reading
OVERSIZE_FILE = "oversize_file"                      # above SourceTree's size cap: not read
TEMPLATE_LANG_SKIPPED = "template_lang_skipped"      # `<template lang="pug">`: tags and texts not scanned
TEMPLATE_SRC_NOT_SCANNED = "template_src_not_scanned"    # `<template src>`: the markup lives in another file
MULTIPLE_TEMPLATES = "multiple_template_blocks"      # invalid Vue; the blocks are merged
LOCALE_PARSE_ERROR = "locale_parse_error"            # the locale file is not JSON: no i18n keys


@dataclass
class _Facts:
    """What one source file contributes before resolution."""

    imports: list[ImportBinding] = field(default_factory=list)
    lazy: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    dynamic_is: list[str] = field(default_factory=list)
    keys: KeyScan = field(default_factory=KeyScan)
    texts: list[str] = field(default_factory=list)
    attr_texts: list[str] = field(default_factory=list)
    barrel: bool = False
    blocks: list[Block] = field(default_factory=list)
    script: str = ""
    nonliteral_dynamic_imports: int = 0
    issues: list[str] = field(default_factory=list)      # problem kinds, each once, in order of appearance


def _merge_keys(into: KeyScan, other: KeyScan) -> None:
    into.static += other.static
    into.dynamic_prefixes += other.dynamic_prefixes
    into.unresolved += other.unresolved


def _flag(facts: _Facts, kind: str) -> None:
    if kind not in facts.issues:
        facts.issues.append(kind)


def _analyse_vue(text: str) -> _Facts:
    facts = _Facts(blocks=split_sfc(text))
    scripts: list[str] = []
    templates = 0
    for block in facts.blocks:
        src = block.attrs.get("src")
        if block.tag == "script":
            if src:
                facts.imports.append(ImportBinding(str(src), (), (), "import"))
                continue
            parsed = extract_imports(block.content, tsx=block.lang in _TSX)
            facts.imports += parsed.imports
            facts.lazy.update(parsed.lazy_bindings)
            facts.nonliteral_dynamic_imports += parsed.dynamic_nonliteral
            if parsed.has_error:
                _flag(facts, SCRIPT_PARSE_ABORTED if parsed.aborted else SCRIPT_PARSE_ERROR)
            clean = without_comments(block.content, parsed)
            scripts.append(clean)
            _merge_keys(facts.keys, scan_t_calls(clean))
        elif block.tag == "style" and src:
            facts.imports.append(ImportBinding(str(src), (), (), "import"))
        elif block.tag == "template":
            if src:         # the markup is in another file: keep the dependency, flag the unread template
                facts.imports.append(ImportBinding(str(src), (), (), "import"))
                _flag(facts, TEMPLATE_SRC_NOT_SCANNED)
                continue
            if block.lang not in (None, "html"):
                _flag(facts, TEMPLATE_LANG_SKIPPED)
                continue
            templates += 1
            if templates > 1:
                _flag(facts, MULTIPLE_TEMPLATES)
            scan = scan_template(block.content, is_native=NATIVE_TAGS)
            facts.tags += [t for t in scan.tags if t not in facts.tags]
            facts.dynamic_is += scan.dynamic_is
            facts.texts += scan.texts
            facts.attr_texts += scan.attr_texts
            _merge_keys(facts.keys, scan_t_calls("\n".join(scan.expressions)))
    facts.script = "\n".join(scripts)
    return facts


def _analyse_script(text: str, ext: str) -> _Facts:
    parsed = extract_imports(text, tsx=ext in (".tsx", ".jsx"))
    facts = _Facts(
        imports=parsed.imports, lazy=parsed.lazy_bindings, nonliteral_dynamic_imports=parsed.dynamic_nonliteral,
        keys=scan_t_calls(without_comments(text, parsed)), barrel=parsed.reexports > 0 and not parsed.declares_locally)
    if parsed.has_error:
        _flag(facts, SCRIPT_PARSE_ABORTED if parsed.aborted else SCRIPT_PARSE_ERROR)
    return facts


def _decode(data: bytes) -> tuple[str, str | None]:
    """Text of a source file and the problem kind (None when it is clean UTF-8; a BOM is dropped)."""
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), UNDECODABLE_BYTES


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _loc(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


class VueAdapter:
    name = "vue"
    exts = INDEXED_EXTS

    def index(self, repo_root: Path, cfg: CodeConfig, snapshot_id: str, *, target: str | None = None,
              ref: str | None = None) -> CodeIndex:
        """`target` and `ref` stamp the result; a bare call falls back to the repo name and `cfg.ref`."""
        return _Build(repo_root, cfg).run(snapshot_id, target or cfg.repo, ref or cfg.ref)

    def index_part(self, repo_root: Path, cfg: CodeConfig, *, known_paths: Collection[str],
                   only: Collection[str], dangling: Sequence[CodeEdge] = ()) -> IndexPart:
        """Facts of the files in `only` (read from `repo_root`), resolved against `known_paths`.

        `known_paths` is every indexed file of the snapshot being described; `dangling` are edges of files
        that were not re-read whose target is gone (see `IndexPart`).
        """
        return _Build(repo_root, cfg, known_paths=known_paths, only=frozenset(only)).part(dangling)


class _Build:
    """One indexing pass. Methods run in order; each fills part of the result and its stats.

    `known_paths` (the overlay) is the complete set of indexed files the subset is resolved against; `only`
    limits which listed files are read. A normal pass has neither: every listed file is read and is the
    universe.
    """

    def __init__(self, repo_root: Path, cfg: CodeConfig, *, known_paths: Collection[str] | None = None,
                 only: frozenset[str] | None = None):
        self.cfg = cfg
        self.tree = SourceTree(repo_root, cfg, INDEXED_EXTS)
        self.only = only
        self.universe: list[str] = sorted(known_paths) if known_paths is not None else self.tree.paths
        self.texts: dict[str, str] = {}
        self.sha: dict[str, str] = {}
        self.kind: dict[str, FileClass] = {}
        self.facts: dict[str, _Facts] = {}
        self.issues: dict[str, list[str]] = {}
        self.resolver = ModuleResolver(self.universe, cfg)
        self.edges: dict[tuple[EdgeKind, str, str], tuple[set[str], str]] = {}
        self.externals: set[str] = set()
        self.generated_nodes: set[str] = set()
        self.barrels: set[str] = set()
        self.n: Counter[str] = Counter()
        self.tag_pairs: Counter[str] = Counter()
        self.tag_how: Counter[str] = Counter()
        self.extra: dict[str, Any] = {}
        self.unresolved_imports: list[dict[str, str]] = []
        self.unresolved_tags: list[dict[str, str]] = []
        self.unresolved_reasons: Counter[str] = Counter()
        self.hub_importers: dict[str, set[str]] = {pkg: set() for pkg in cfg.hub_packages}
        self.locale_loaded = False
        self._components: ComponentResolver | None = None
        self.pkg_dirs = sorted(
            ((str(PurePosixPath(r.rstrip("/")).parent) if PurePosixPath(r).name in ("src", "lib") else r.rstrip("/"),
              workspace_package(r) or "") for r in cfg.roots), key=lambda kv: -len(kv[0]))

    # ------------------------------------------------------------------------------------ load + parse
    def _note(self, path: str, kind: str) -> None:
        kinds = self.issues.setdefault(path, [])
        if kind not in kinds:
            kinds.append(kind)

    def _kind_of(self, path: str) -> FileClass:
        if path not in self.kind:
            self.kind[path] = classify_file(path, self.cfg)
        return self.kind[path]

    def load(self) -> None:
        for path in self.tree.oversize:
            self._note(path, OVERSIZE_FILE)
        for path in self.tree.paths:
            if self.only is not None and path not in self.only:
                continue
            try:
                data = self.tree.read_bytes(path)
            except PermissionError:     # swapped for a link or removed between the listing and the read
                self._note(path, UNREADABLE_FILE)
                continue
            text, problem = _decode(data)
            if problem:
                self._note(path, problem)
            self.texts[path], self.sha[path] = text, _blob_sha(data)
            kind = self._kind_of(path)
            ext = PurePosixPath(path).suffix.lower()
            if kind is FileClass.GENERATED or ext not in CODE_EXTS:
                continue
            facts = _analyse_vue(text) if ext == ".vue" else _analyse_script(text, ext)
            self.facts[path] = facts
            for issue in facts.issues:
                self._note(path, issue)
        self.barrels = {p for p, f in self.facts.items() if f.barrel}

    # ----------------------------------------------------------------------------------- imports
    def _add_edge(self, kind: EdgeKind, src: str, dst: str, symbols: tuple[str, ...], via: str) -> None:
        if src == dst:              # a recursive component references itself; that is no reachability
            self.n["self_references"] += 1
            return
        # Several statements/tags can link the same pair; keep the lexicographically smallest `via` so the
        # merged edge does not depend on the order the statements were met in.
        have, kept_via = self.edges.get((kind, src, dst), (set(), via))
        self.edges[(kind, src, dst)] = (have | set(symbols), min(kept_via, via))

    def _note_unresolved(self, path: str, spec: str, r: Resolved) -> None:
        self.n["unresolved_imports"] += 1
        self.unresolved_reasons[r.reason] += 1
        self.unresolved_imports.append({"file": path, "specifier": spec, "reason": r.reason})

    def resolve_imports(self) -> dict[str, dict[str, Resolved]]:
        """Fill IMPORTS edges; returns {file: {specifier: Resolved}} for tag resolution."""
        by_file: dict[str, dict[str, Resolved]] = {}
        for path, facts in sorted(self.facts.items()):
            is_test = self.kind[path] is FileClass.TEST
            seen: dict[str, Resolved] = by_file.setdefault(path, {})
            for b in facts.imports:
                r = seen.get(b.specifier) or self.resolver.resolve(b.specifier, path)
                seen[b.specifier] = r
                self.n["import_statements"] += 1
                self.n[f"imports_via_{r.via or r.kind}"] += 1
                self.n[f"imports_kind_{b.kind}"] += 1
                self.n["type_only_imports"] += b.type_only
                for hub, importers in self.hub_importers.items():
                    if package_name(b.specifier) == hub:
                        importers.add(path)
                if r.kind == "file":
                    self._add_edge("IMPORTS", path, r.target, b.symbols, b.specifier)
                    self.n["inferred_alias_imports"] += r.via == "inferred_alias"
                    if r.generated_missing:
                        self.generated_nodes.add(r.target)
                        self.n["generated_missing_imports"] += 1
                    if is_test:
                        self.n["import_statements_from_tests"] += 1
                elif r.kind == "external":
                    self.externals.add(r.target)
                    self._add_edge("IMPORTS", path, r.target, b.symbols, b.specifier)
                else:
                    self._note_unresolved(path, b.specifier, r)
            self.n["nonliteral_dynamic_imports"] += facts.nonliteral_dynamic_imports
        return by_file

    # -------------------------------------------------------------------------------------- tags
    def _component_resolver(self) -> ComponentResolver:
        if self._components is not None:
            return self._components
        dts_path = self.cfg.auto_import_dts
        entries = {}
        if dts_path and dts_path in self.texts:
            entries = parse_components_dts(self.texts[dts_path], dts_path)
        dirs = [d.rstrip("/") for d in self.cfg.components_dirs]
        if not dirs:
            if dts_path:
                dirs = [str(PurePosixPath(dts_path).parent / "components")]
            else:
                dirs = [f"{r.rstrip('/')}/components" for r in self.cfg.roots]
        self.extra["components_dts_entries"] = len(entries)
        self._components = ComponentResolver(self.universe, entries, dirs)
        return self._components

    def _bindings(self, path: str, resolved: dict[str, Resolved]) -> dict[str, tuple[str, str, str]]:
        """PascalCase local binding -> (kind, target, how) for imports and lazy component bindings."""
        out: dict[str, tuple[str, str, str]] = {}
        facts = self.facts[path]
        for b in facts.imports:
            r = resolved[b.specifier]
            for local, _imported in b.locals:
                if r.kind in ("file", "external"):
                    out[pascal_case(local)] = (r.kind, r.target, "import")
        for local, spec in facts.lazy.items():
            r = resolved.get(spec)
            if r is not None and r.kind in ("file", "external"):
                out[pascal_case(local)] = (r.kind, r.target, "lazy_import")
        return out

    def resolve_tags(self, imports: dict[str, dict[str, Resolved]]) -> None:
        comp = self._component_resolver()
        for path, facts in sorted(self.facts.items()):
            if not path.endswith(".vue"):
                continue
            bindings = self._bindings(path, imports.get(path, {}))
            tags = list(facts.tags)
            for expr in facts.dynamic_is:
                self.n["dynamic_components"] += 1
                ident = expr.strip()
                if _IDENT.fullmatch(ident) and pascal_case(ident) in bindings:
                    tags.append(ident)
                    self.n["dynamic_components_resolved"] += 1
            for tag in dict.fromkeys(tags):
                r = comp.resolve(tag, importer=path, bindings=bindings)
                if r.kind == "builtin":
                    continue
                self.tag_pairs[r.kind] += 1
                self.tag_how[r.how or "none"] += 1
                if r.kind == "file" and r.how != "self":
                    self._add_edge("RENDERS", path, r.target, (), tag)
                elif r.kind == "unresolved":
                    self.unresolved_reasons[f"tag: {r.reason}"] += 1
                    if len(self.unresolved_tags) < _UNLISTED_CAP:
                        self.unresolved_tags.append({"file": path, "tag": tag, "reason": r.reason})

    def redirect(self, dangling: Sequence[CodeEdge]) -> tuple[list[CodeEdge], list[CodeEdge]]:
        """Re-resolve edges whose target is gone from their `via` against this universe: (moved, dropped).

        A RENDERS edge is looked up by tag name (components table, then directory rule); an IMPORTS edge
        by its specifier. Both lose the import bindings of the importer, which was not re-read, so a tag that was
        bound by an explicit import is dropped rather than guessed.
        """
        comp = self._component_resolver()
        moved: list[CodeEdge] = []
        dropped: list[CodeEdge] = []
        for edge in dangling:
            target = None
            if edge.via and edge.kind == "IMPORTS":
                r = self.resolver.resolve(edge.via, edge.src)
                if r.kind in ("file", "external"):
                    target = r.target
                    if r.kind == "external":
                        self.externals.add(r.target)
                    elif r.generated_missing:
                        self.generated_nodes.add(r.target)
            elif edge.via:
                t = comp.resolve(edge.via, importer=edge.src, bindings={})
                target = t.target if t.kind == "file" and t.how != "self" else None
            if target is None or target in (edge.dst, edge.src):
                dropped.append(edge)
            else:
                moved.append(edge.model_copy(update={"dst": target}))
        return moved, dropped

    # ------------------------------------------------------------------------------------- i18n
    def i18n(self) -> tuple[dict[str, str], list[KeyUse]]:
        messages: dict[str, str] = {}
        if self.cfg.locale_files:
            path = self.cfg.locale_files[0]
            if path in self.texts:
                self.locale_loaded = True
                try:
                    messages, skipped = flatten_messages(json.loads(self.texts[path]))
                    self.extra["i18n_nonstring_leaves"] = skipped
                except (ValueError, AttributeError, RecursionError):   # not JSON/object, or absurdly nested
                    self.extra["i18n_parse_error"] = True
                    self._note(path, LOCALE_PARSE_ERROR)
        uses: set[tuple[str, str, bool]] = set()
        static_distinct: set[str] = set()
        for path, facts in self.facts.items():
            if self.kind[path] in (FileClass.TEST, FileClass.GENERATED):
                continue
            keys = facts.keys
            self.n["key_call_sites"] += len(keys.static) + len(keys.dynamic_prefixes) + keys.unresolved
            self.n["key_calls_unresolved"] += keys.unresolved
            for key in keys.static:
                uses.add((path, key, False))
                static_distinct.add(key)
            for prefix in keys.dynamic_prefixes:
                uses.add((path, prefix, True))
        values = Counter(messages.values())
        self.extra.update({
            "i18n_keys": len(messages), "i18n_distinct_values": len(values),
            "i18n_values_shared_by_keys": sum(1 for n in values.values() if n > 1),    # case 32: ambiguous copy
            "keys_used_distinct": len(static_distinct),
            "keys_used_in_locale": len(static_distinct & messages.keys()),
            "keys_used_not_in_locale": len(static_distinct - messages.keys()),
            "dynamic_key_uses": sum(1 for _, _, dyn in uses if dyn),
        })
        self.n["key_uses"] = len(uses)
        return messages, [KeyUse(file=f, key=k, dynamic=d) for f, k, d in sorted(uses)]

    def literals(self) -> list[TextLiteral]:
        out: set[tuple[str, str]] = set()
        attr_out: set[tuple[str, str]] = set()
        for path, facts in self.facts.items():
            if self.kind[path] in (FileClass.TEST, FileClass.GENERATED):
                continue
            out.update((path, t) for t in facts.texts)
            attr_out.update((path, t) for t in facts.attr_texts)
        self.extra["text_literals"], self.extra["attr_text_literals"] = len(out), len(attr_out)
        return [TextLiteral(file=f, text=t) for f, t in sorted(out)]

    # ------------------------------------------------------------------------------------ routes
    def routes(self) -> list[RouteDef]:
        """Routes of the loaded pages; layouts are looked up in the whole universe (path names only)."""
        pages: dict[str, str] = {}
        layouts: dict[str, str] = {}
        for root in self.cfg.roots:
            root = root.rstrip("/")
            for path in self.universe:
                if not path.endswith(".vue") or self._kind_of(path) is FileClass.TEST:
                    continue
                if path.startswith(f"{root}/pages/"):
                    if path in self.facts:
                        pages[path] = path[len(root) + len("/pages/"):]
                elif path.startswith(f"{root}/layouts/"):
                    layouts.setdefault(PurePosixPath(path).stem, path)
        blocks = {p: self.facts[p].blocks for p in pages}
        scripts = {p: self.facts[p].script for p in pages}
        routes = build_routes(pages, layouts, blocks, scripts, layouts.get("default"))
        self.extra["routes"] = len(routes)
        return routes

    # ------------------------------------------------------------------------------------ output
    def _package_of(self, path: str) -> str | None:
        for directory, name in self.pkg_dirs:
            if path.startswith(directory + "/"):
                return name or None
        return None

    def files(self) -> list[CodeFile]:
        out = [CodeFile(path=p, kind=self.kind[p], loc=_loc(self.texts[p]), sha=self.sha[p],
                        package=self._package_of(p)) for p in sorted(self.texts)]
        for path in sorted(self.generated_nodes - set(self.universe)):
            out.append(CodeFile(path=path, kind=FileClass.GENERATED, package=self._package_of(path)))
        out += [CodeFile(path=name, kind=FileClass.EXTERNAL, package=name) for name in sorted(self.externals)]
        return sorted(out, key=lambda f: f.path)

    def edge_list(self) -> list[CodeEdge]:
        return [CodeEdge(src=src, dst=dst, kind=kind, symbols=sorted(symbols), via=via)
                for (kind, src, dst), (symbols, via) in sorted(self.edges.items())]

    def _hub_roots(self) -> dict[str, str]:
        """Source directory of each in-repo hub package (case 30), by the workspace naming convention."""
        return {name: r.rstrip("/") for r in self.cfg.roots if (name := workspace_package(r)) in self.cfg.hub_packages}

    def _problem_stats(self) -> dict[str, Any]:
        pairs = [(f, k) for f, kinds in sorted(self.issues.items()) for k in sorted(kinds)]
        links = self.tree.symlinks
        return {
            "parse_errors": len(pairs), "parse_error_files": len(self.issues),
            "parse_error_kinds": dict(sorted(Counter(k for _, k in pairs).items())),
            "parse_error_sample": [{"file": f, "kind": k} for f, k in pairs[:_UNLISTED_CAP]],
            "symlinks_skipped": len(links), "symlinks_skipped_sample": links[:_UNLISTED_CAP],
        }

    def stats(self, files: list[CodeFile], edges: list[CodeEdge]) -> dict[str, Any]:
        by_class = Counter(f.kind.value for f in files)
        internal = [e for e in edges if e.kind == "IMPORTS" and e.dst not in self.externals]
        renders = sum(e.kind == "RENDERS" for e in edges)
        app = [p for p in self.texts if self.kind[p] not in (FileClass.TEST, FileClass.GENERATED)
               and PurePosixPath(p).suffix in (".vue", ".ts", ".tsx", ".js")]
        barrel_importers = {e.src for e in internal if e.dst in self.barrels and e.src not in self.barrels}
        total = sum(self.tag_pairs.values())
        resolved = self.tag_pairs["file"] + self.tag_pairs["external"]
        roots = [{"root": r.root, "status": r.status, "files": r.files} for r in self.tree.roots]
        return {
            **self.extra,
            **self._problem_stats(),
            "code_roots": roots, "configured_files": dict(self.tree.configured),
            "index_empty": sum(r["files"] for r in roots) == 0,
            "files_by_class": dict(sorted(by_class.items())),
            "files_total": len(files), "files_in_tree": len(self.tree.paths),
            "vue_files": sum(1 for f in files if f.path.endswith(".vue")),
            "app_source_files": len(app),
            "imports": len(internal), "imports_external": len(edges) - len(internal) - renders, "renders": renders,
            "import_statements": self.n["import_statements"],
            "import_statements_by_resolution": {
                k[len("imports_via_"):]: v for k, v in sorted(self.n.items()) if k.startswith("imports_via_")},
            "import_statements_by_kind": {
                k[len("imports_kind_"):]: v for k, v in sorted(self.n.items()) if k.startswith("imports_kind_")},
            "type_only_imports": self.n["type_only_imports"],
            "self_references": self.n["self_references"],
            "import_statements_from_tests": self.n["import_statements_from_tests"],
            "inferred_alias_imports": self.n["inferred_alias_imports"],
            "generated_missing_imports": self.n["generated_missing_imports"],
            "nonliteral_dynamic_imports": self.n["nonliteral_dynamic_imports"],
            "tag_pairs_total": total, "tag_pairs_internal": self.tag_pairs["file"],
            "tag_pairs_external": self.tag_pairs["external"], "tag_pairs_unresolved": self.tag_pairs["unresolved"],
            "resolved_tag_rate": round(resolved / total, 4) if total else None,   # None: nothing was measured
            "external_tag_count": self.tag_pairs["external"],
            "tag_resolution_by": dict(sorted(self.tag_how.items())),
            "dynamic_component_count": self.n["dynamic_components"],
            "dynamic_components_resolved": self.n["dynamic_components_resolved"],
            "unresolved_tags": self.unresolved_tags,
            "unresolved_imports": self.n["unresolved_imports"], "unresolved_imports_sample": sorted(
                self.unresolved_imports, key=lambda d: (d["file"], d["specifier"], d["reason"]))[:_UNLISTED_CAP],
            "unresolved_reasons": dict(sorted(self.unresolved_reasons.items(),
                                              key=lambda kv: (-kv[1], kv[0]))),
            "key_uses": self.n["key_uses"],
            "key_call_sites": self.n["key_call_sites"], "key_calls_unresolved": self.n["key_calls_unresolved"],
            # tests are excluded (they import the hub without being product code); both counts are reported
            "hub_importers": {pkg: sum(1 for p in paths if self.kind[p] is not FileClass.TEST)
                              for pkg, paths in self.hub_importers.items()},
            "hub_importers_with_tests": {pkg: len(paths) for pkg, paths in self.hub_importers.items()},
            "barrel_files": len(self.barrels), "barrel_importers": len(barrel_importers),
            "global_files": sorted(g for g in self.cfg.global_files if g in self.kind),
            "hub_package_roots": self._hub_roots(),
        }

    def _pipeline(self) -> tuple[dict[str, str], list[KeyUse], list[TextLiteral], list[RouteDef]]:
        self.load()
        imports = self.resolve_imports()
        self.resolve_tags(imports)
        messages, key_uses = self.i18n()
        return messages, key_uses, self.literals(), self.routes()

    def run(self, snapshot_id: str, target: str, ref: str) -> CodeIndex:
        messages, key_uses, literals, routes = self._pipeline()
        files, edges = self.files(), self.edge_list()
        return CodeIndex(target=target, repo=self.cfg.repo, ref=ref, snapshot_id=snapshot_id,
                         adapter="vue", files=files, edges=edges, routes=routes, i18n=messages,
                         key_uses=key_uses, literals=literals, stats=self.stats(files, edges))

    def part(self, dangling: Sequence[CodeEdge]) -> IndexPart:
        messages, key_uses, literals, routes = self._pipeline()
        moved, dropped = self.redirect(dangling)
        dts = self.cfg.auto_import_dts
        return IndexPart(
            files=self.files(), edges=self.edge_list(), routes=routes,
            i18n=messages if self.locale_loaded else None, key_uses=key_uses, literals=literals,
            issues={f: sorted(k) for f, k in sorted(self.issues.items())}, redirected=moved, dropped=dropped,
            dts_entries=self.extra.get("components_dts_entries") if dts and dts in self.texts else None,
            tag_pairs=sum(self.tag_pairs.values()), tag_pairs_unresolved=self.tag_pairs["unresolved"],
            symlinks=self.tree.symlinks, oversize=self.tree.oversize)
