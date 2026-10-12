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
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from ...models import CodeEdge, CodeFile, CodeIndex, FileClass, KeyUse, RouteDef, TextLiteral
from ...targets import CodeConfig
from ..adapter import SourceTree
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


def _merge_keys(into: KeyScan, other: KeyScan) -> None:
    into.static += other.static
    into.dynamic_prefixes += other.dynamic_prefixes
    into.unresolved += other.unresolved


def _analyse_vue(text: str) -> _Facts:
    facts = _Facts(blocks=split_sfc(text))
    scripts: list[str] = []
    for block in facts.blocks:
        if block.tag == "script":
            if block.attrs.get("src"):
                facts.imports.append(ImportBinding(str(block.attrs["src"]), (), (), "import"))
                continue
            parsed = extract_imports(block.content, tsx=block.lang in _TSX)
            facts.imports += parsed.imports
            facts.lazy.update(parsed.lazy_bindings)
            facts.nonliteral_dynamic_imports += parsed.dynamic_nonliteral
            clean = without_comments(block.content, parsed)
            scripts.append(clean)
            _merge_keys(facts.keys, scan_t_calls(clean))
        elif block.tag == "template" and block.lang in (None, "html"):
            scan = scan_template(block.content, is_native=NATIVE_TAGS)
            facts.tags, facts.dynamic_is = scan.tags, scan.dynamic_is
            facts.texts, facts.attr_texts = scan.texts, scan.attr_texts
            _merge_keys(facts.keys, scan_t_calls("\n".join(scan.expressions)))
    facts.script = "\n".join(scripts)
    return facts


def _analyse_script(text: str, ext: str) -> _Facts:
    parsed = extract_imports(text, tsx=ext in (".tsx", ".jsx"))
    return _Facts(
        imports=parsed.imports, lazy=parsed.lazy_bindings, nonliteral_dynamic_imports=parsed.dynamic_nonliteral,
        keys=scan_t_calls(without_comments(text, parsed)), barrel=parsed.reexports > 0 and not parsed.declares_locally)


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _loc(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


class VueAdapter:
    name = "vue"

    def index(self, repo_root: Path, cfg: CodeConfig, snapshot_id: str) -> CodeIndex:
        """`target` is stamped with the repo name; `run_index` replaces it with the target name."""
        return _Build(repo_root, cfg).run(snapshot_id)


class _Build:
    """One indexing pass. Methods run in order; each fills part of the result and its stats."""

    def __init__(self, repo_root: Path, cfg: CodeConfig):
        self.cfg = cfg
        self.tree = SourceTree(repo_root, cfg, INDEXED_EXTS)
        self.texts: dict[str, str] = {}
        self.sha: dict[str, str] = {}
        self.kind: dict[str, FileClass] = {}
        self.facts: dict[str, _Facts] = {}
        self.resolver = ModuleResolver(self.tree.paths, cfg)
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
        self.pkg_dirs = sorted(
            ((str(PurePosixPath(r.rstrip("/")).parent) if PurePosixPath(r).name in ("src", "lib") else r.rstrip("/"),
              workspace_package(r) or "") for r in cfg.roots), key=lambda kv: -len(kv[0]))

    # ------------------------------------------------------------------------------------ load + parse
    def load(self) -> None:
        for path in self.tree.paths:
            data = self.tree.read_bytes(path)
            text = data.decode("utf-8", errors="replace")
            self.texts[path], self.sha[path] = text, _blob_sha(data)
            self.kind[path] = classify_file(path, self.cfg)
            ext = PurePosixPath(path).suffix.lower()
            if self.kind[path] is FileClass.GENERATED or ext not in CODE_EXTS:
                continue
            self.facts[path] = _analyse_vue(text) if ext == ".vue" else _analyse_script(text, ext)
        self.barrels = {p for p, f in self.facts.items() if f.barrel}

    # ----------------------------------------------------------------------------------- imports
    def _add_edge(self, kind: EdgeKind, src: str, dst: str, symbols: tuple[str, ...], via: str) -> None:
        if src == dst:              # a recursive component references itself; that is no reachability
            self.n["self_references"] += 1
            return
        # Several statements/tags can link the same pair; encounter order comes from set iteration (hash-seed
        # dependent), so keep the lexicographically smallest `via`: the merged edge is order-independent.
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
        dts_path = self.cfg.auto_import_dts
        entries = {}
        if dts_path and dts_path in self.texts:
            entries = parse_components_dts(self.texts[dts_path], dts_path)
        if dts_path:
            dirs = [str(PurePosixPath(dts_path).parent / "components")]
        else:
            dirs = [f"{r.rstrip('/')}/components" for r in self.cfg.roots]
        self.extra["components_dts_entries"] = len(entries)
        return ComponentResolver(self.tree.paths, entries, dirs)

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

    # ------------------------------------------------------------------------------------- i18n
    def i18n(self) -> tuple[dict[str, str], list[KeyUse]]:
        messages: dict[str, str] = {}
        if self.cfg.locale_files:
            path = self.cfg.locale_files[0]
            if path in self.texts:
                try:
                    messages, skipped = flatten_messages(json.loads(self.texts[path]))
                    self.extra["i18n_nonstring_leaves"] = skipped
                except ValueError:
                    self.extra["i18n_parse_error"] = True
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
        pages: dict[str, str] = {}
        layouts: dict[str, str] = {}
        for root in self.cfg.roots:
            root = root.rstrip("/")
            for path in self.tree.paths:
                if not path.endswith(".vue") or self.kind[path] is FileClass.TEST:
                    continue
                if path.startswith(f"{root}/pages/"):
                    pages[path] = path[len(root) + len("/pages/"):]
                elif path.startswith(f"{root}/layouts/"):
                    layouts.setdefault(PurePosixPath(path).stem, path)
        blocks = {p: self.facts[p].blocks for p in pages if p in self.facts}
        scripts = {p: self.facts[p].script for p in pages if p in self.facts}
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
                        package=self._package_of(p)) for p in self.tree.paths]
        for path in sorted(self.generated_nodes - set(self.tree.paths)):
            out.append(CodeFile(path=path, kind=FileClass.GENERATED, package=self._package_of(path)))
        out += [CodeFile(path=name, kind=FileClass.EXTERNAL, package=name) for name in sorted(self.externals)]
        return sorted(out, key=lambda f: f.path)

    def edge_list(self) -> list[CodeEdge]:
        return [CodeEdge(src=src, dst=dst, kind=kind, symbols=sorted(symbols), via=via)
                for (kind, src, dst), (symbols, via) in sorted(self.edges.items())]

    def _hub_roots(self) -> dict[str, str]:
        """Source directory of each in-repo hub package (case 30), by the workspace naming convention."""
        return {name: r.rstrip("/") for r in self.cfg.roots if (name := workspace_package(r)) in self.cfg.hub_packages}

    def stats(self, files: list[CodeFile], edges: list[CodeEdge]) -> dict[str, Any]:
        by_class = Counter(f.kind.value for f in files)
        internal = [e for e in edges if e.kind == "IMPORTS" and e.dst not in self.externals]
        renders = sum(e.kind == "RENDERS" for e in edges)
        app = [p for p in self.tree.paths if self.kind[p] not in (FileClass.TEST, FileClass.GENERATED)
               and PurePosixPath(p).suffix in (".vue", ".ts", ".tsx", ".js")]
        barrel_importers = {e.src for e in internal if e.dst in self.barrels and e.src not in self.barrels}
        total = sum(self.tag_pairs.values())
        resolved = self.tag_pairs["file"] + self.tag_pairs["external"]
        return {
            **self.extra,
            "files_by_class": dict(sorted(by_class.items())),
            "files_total": len(files), "files_in_tree": len(self.tree.paths), "vue_files": sum(1 for f in files if f.path.endswith(".vue")),
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
            "resolved_tag_rate": round(resolved / total, 4) if total else 1.0,
            "external_tag_count": self.tag_pairs["external"],
            "tag_resolution_by": dict(sorted(self.tag_how.items())),
            "dynamic_component_count": self.n["dynamic_components"],
            "dynamic_components_resolved": self.n["dynamic_components_resolved"],
            "unresolved_tags": self.unresolved_tags,
            "unresolved_imports": self.n["unresolved_imports"], "unresolved_imports_sample": sorted(
                self.unresolved_imports, key=lambda d: (d["file"], d["specifier"], d["reason"]))[:_UNLISTED_CAP],
            "unresolved_reasons": dict(self.unresolved_reasons.most_common()),
            "key_uses": self.n["key_uses"],
            "key_call_sites": self.n["key_call_sites"], "key_calls_unresolved": self.n["key_calls_unresolved"],
            "hub_importers": {pkg: len(paths) for pkg, paths in self.hub_importers.items()},
            "barrel_files": len(self.barrels), "barrel_importers": len(barrel_importers),
            "global_files": sorted(g for g in self.cfg.global_files if g in self.kind),
            "hub_package_roots": self._hub_roots(),
        }

    def run(self, snapshot_id: str) -> CodeIndex:
        self.load()
        imports = self.resolve_imports()
        self.resolve_tags(imports)
        messages, key_uses = self.i18n()
        literals = self.literals()
        routes = self.routes()
        files, edges = self.files(), self.edge_list()
        return CodeIndex(target=self.cfg.repo, repo=self.cfg.repo, ref=self.cfg.ref, snapshot_id=snapshot_id,
                         adapter="vue", files=files, edges=edges, routes=routes, i18n=messages,
                         key_uses=key_uses, literals=literals, stats=self.stats(files, edges))
