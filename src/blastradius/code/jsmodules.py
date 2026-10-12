"""JS/TS module graph pieces shared by framework adapters (D10; cases 30, 33, 34).

`extract_imports` reads import bindings from a script with tree-sitter-typescript (no Vue compiler, no
network): default, named (with symbol names), namespace, side-effect, `import type`, `export ... from`
re-exports, and `import("literal")`. `ModuleResolver` turns a specifier into a repo file using only the
set of paths the indexer was allowed to list: relative paths, `cfg.code.alias` prefixes, workspace packages
that are source roots here, and a documented fallback for tsconfig-style `@dir/*` aliases.

Tree-sitter's `QueryCursor.captures()` returns nodes in an order that differs between processes, so every
capture list is sorted by byte offset before anything is built from it: facts are in document order.

Parsing is bounded by work, not by the clock (so the result is the same on every machine): a 149-character
fuzz input sent tree-sitter-typescript into a parse that did not finish in 40 s, and a PR head is untrusted
(D06). The parser is driven through a read callback with a progress callback that cancels after a budget of
callbacks proportional to the size; real files use at most 13 callbacks per KB, the budget allows 60 per KB
plus a floor, and a cancelled parse yields no imports and `aborted=True`.
"""

from __future__ import annotations

import posixpath
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

import tree_sitter_typescript as _tst
from tree_sitter import Language, Node, Parser, Query, QueryCursor, Tree

from ..models import FileClass
from ..targets import CodeConfig
from .fileclass import classify_file, workspace_package

CODE_EXTS = (".ts", ".vue", ".js", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_PROBE_EXTS = (".ts", ".vue", ".js", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts", ".json", ".graphql", ".gql")
_JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}

_TS = Language(_tst.language_typescript())
_TSX = Language(_tst.language_tsx())


@dataclass(frozen=True)
class ImportBinding:
    """One import or re-export statement. `symbols` are the names as exported by the target module."""

    specifier: str
    symbols: tuple[str, ...] = ()       # "default", "*", or exported names; () for a side-effect import
    locals: tuple[tuple[str, str], ...] = ()   # (local binding, imported name)
    kind: str = "import"                # import | reexport | dynamic
    type_only: bool = False


@dataclass
class ScriptFacts:
    imports: list[ImportBinding] = field(default_factory=list)
    dynamic_nonliteral: int = 0         # import(expr) with a computed specifier: a known hole
    lazy_bindings: dict[str, str] = field(default_factory=dict)   # const Foo = defineAsyncComponent(() => import(..))
    comment_spans: list[tuple[int, int]] = field(default_factory=list)   # byte offsets, for key scanning
    declares_locally: bool = False      # any top-level declaration besides import/export-from
    reexports: int = 0
    has_error: bool = False             # tree-sitter recovered from a syntax error: imports may be missing
    aborted: bool = False               # the parse exceeded its work budget: no facts were extracted
    # name -> occurrences as a value outside import / re-export statements (only when asked for; types are not
    # values). An imported component that appears here is passed around, registered or rendered by this script.
    value_refs: dict[str, int] = field(default_factory=dict)


@lru_cache(maxsize=2)
def _queries(tsx: bool) -> tuple[Parser, Query, Query]:
    lang = _TSX if tsx else _TS
    imports = Query(lang, "[(import_statement) (export_statement source: (string))] @stmt")
    other = Query(lang, """
        (call_expression function: (import)) @dyn
        (comment) @comment
        [(identifier) (shorthand_property_identifier)] @ref
    """)
    return Parser(lang), imports, other


def _string_value(node: Node) -> str | None:
    """Literal value of a `string` node or a substitution-free template string; None when computed."""
    if node.type == "string":
        return "".join(c.text.decode() for c in node.children if c.type in ("string_fragment", "escape_sequence"))
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.children):
            return None
        return node.text.decode()[1:-1]
    return None


def _import_specs(clause: Node) -> tuple[list[str], list[tuple[str, str]]]:
    symbols: list[str] = []
    local: list[tuple[str, str]] = []
    for part in clause.children:
        if part.type == "identifier":
            symbols.append("default")
            local.append((part.text.decode(), "default"))
        elif part.type == "namespace_import":
            symbols.append("*")
            ident = next((c for c in part.children if c.type == "identifier"), None)
            if ident is not None:
                local.append((ident.text.decode(), "*"))
        elif part.type == "named_imports":
            for spec in part.children:
                if spec.type != "import_specifier":
                    continue
                name = spec.child_by_field_name("name")
                alias = spec.child_by_field_name("alias")
                if name is None:
                    continue
                imported = _string_value(name) or name.text.decode()
                symbols.append(imported)
                local.append(((alias or name).text.decode(), imported))
    return symbols, local


def _reexport_symbols(stmt: Node) -> tuple[list[str], bool]:
    """Names re-exported by `export ... from`; `*` for `export *` / `export * as ns`."""
    symbols: list[str] = []
    for child in stmt.children:
        if child.type == "*" or child.type == "namespace_export":
            symbols.append("*")
        elif child.type == "export_clause":
            for spec in child.children:
                if spec.type == "export_specifier":
                    name = spec.child_by_field_name("name")
                    if name is not None:
                        symbols.append(_string_value(name) or name.text.decode())
    return symbols, any(c.type == "type" for c in stmt.children)


def _lazy_binding(call: Node, literal: str, out: dict[str, str]) -> None:
    """`const Foo = defineAsyncComponent(() => import('./Foo.vue'))` or `const Foo = () => import(...)`."""
    node: Node | None = call
    for _ in range(6):
        node = node.parent if node else None
        if node is None:
            return
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                out[name.text.decode()] = literal
            return
        if node.type in ("statement_block", "program", "pair", "object", "class_body"):
            return


_PARSE_CHUNK = 64 * 1024
_PARSE_BUDGET_FLOOR = 200           # progress callbacks (one per ~100 parser operations) always allowed
_PARSE_BUDGET_PER_KB = 60           # measured maximum on 977 real script blocks: 12.6 per KB


def _parse(parser: Parser, data: bytes) -> Tree | None:
    """Parse `data`, or None when the work budget is spent (a hostile input, never a real source file)."""
    budget = _PARSE_BUDGET_FLOOR + _PARSE_BUDGET_PER_KB * (len(data) // 1024)
    calls = 0

    def progress(_offset: int, _has_error: bool) -> bool:
        nonlocal calls
        calls += 1
        return calls > budget       # True cancels the parse

    def read(offset: int, _point: object) -> bytes:
        return data[offset:offset + _PARSE_CHUNK]

    try:
        return parser.parse(read, progress_callback=progress)
    except ValueError:              # tree-sitter reports a cancelled parse as "Parsing failed"
        parser.reset()              # else the next parse() would resume this one, over a different document
        return None


def _in_document_order(nodes: Iterable[Node]) -> list[Node]:
    return sorted(nodes, key=lambda n: (n.start_byte, n.end_byte))


def extract_imports(source: str, *, tsx: bool = False, refs: bool = False) -> ScriptFacts:
    """Import bindings, lazy bindings and comment spans of a script; `refs` also fills `value_refs`."""
    parser, q_import, q_other = _queries(tsx)
    tree = _parse(parser, source.encode("utf-8"))
    if tree is None:
        return ScriptFacts(has_error=True, aborted=True)
    root = tree.root_node
    facts = ScriptFacts(has_error=root.has_error)
    statements = _in_document_order(QueryCursor(q_import).captures(root).get("stmt", []))
    for node in statements:
        src = node.child_by_field_name("source")
        spec = _string_value(src) if src is not None else None
        if spec is None:
            continue
        if node.type == "import_statement":
            clause = next((c for c in node.children if c.type == "import_clause"), None)
            symbols, local = _import_specs(clause) if clause is not None else ([], [])
            type_only = any(c.type == "type" for c in node.children)
            facts.imports.append(ImportBinding(spec, tuple(symbols), tuple(local), "import", type_only))
        else:
            symbols, type_only = _reexport_symbols(node)
            facts.imports.append(ImportBinding(spec, tuple(symbols), (), "reexport", type_only))
            facts.reexports += 1
    caps = QueryCursor(q_other).captures(root)
    for call in _in_document_order(caps.get("dyn", [])):
        args = call.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        literal = _string_value(first) if first is not None else None
        if literal is None:
            facts.dynamic_nonliteral += 1
            continue
        facts.imports.append(ImportBinding(literal, (), (), "dynamic"))
        _lazy_binding(call, literal, facts.lazy_bindings)
    facts.comment_spans = [(n.start_byte, n.end_byte) for n in _in_document_order(caps.get("comment", []))]
    if refs:
        facts.value_refs = _value_refs(caps.get("ref", []), [(n.start_byte, n.end_byte) for n in statements])
    facts.declares_locally = any(
        c.type not in ("import_statement", "comment", "empty_statement")
        and not (c.type == "export_statement" and c.child_by_field_name("source") is not None)
        for c in root.children)
    return facts


def _value_refs(nodes: Iterable[Node], statements: list[tuple[int, int]]) -> dict[str, int]:
    """Identifier occurrences outside the import / re-export statements (top level, so spans never nest)."""
    starts = [a for a, _ in statements]
    out: Counter[str] = Counter()
    for node in nodes:
        i = bisect_right(starts, node.start_byte) - 1
        if i >= 0 and node.start_byte < statements[i][1]:
            continue
        out[node.text.decode()] += 1
    return dict(sorted(out.items()))


def without_comments(source: str, facts: ScriptFacts) -> str:
    """`source` with every comment blanked out, so `// t('x')` is not mistaken for a call."""
    data = bytearray(source.encode("utf-8"))
    for start, end in facts.comment_spans:
        data[start:end] = b" " * (end - start)
    return data.decode("utf-8")


# ----------------------------------------------------------------------------------------- resolution
@dataclass(frozen=True)
class Resolved:
    """Where a specifier lands: an indexed `file`, an `external` package leaf, or `unresolved`."""

    kind: str                           # file | external | unresolved
    target: str                         # repo path | package name | the specifier
    via: str = ""                       # alias | relative | workspace | inferred_alias | package
    reason: str = ""                    # unresolved: why
    generated_missing: bool = False     # resolved to a path that should exist but is a codegen output


def package_name(spec: str) -> str:
    """npm package name of a bare specifier: `@scope/name` or the first segment; virtual modules by prefix."""
    spec = spec.split("?", 1)[0]
    if spec.startswith("node:"):
        return spec
    if spec.startswith(("~", "virtual:")):
        return spec.split("/", 1)[0]
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]


class ModuleResolver:
    """Resolve import specifiers against the listed repo paths. Never touches the filesystem."""

    def __init__(self, files: Iterable[str], cfg: CodeConfig):
        self.files = set(files)
        self.cfg = cfg
        self.roots = [r.strip("/") for r in cfg.roots]
        self.aliases = sorted(((k, v.rstrip("/")) for k, v in cfg.alias.items()), key=lambda kv: -len(kv[0]))
        self.packages: dict[str, str] = {}
        for root in self.roots:
            name = workspace_package(root)
            if name:
                self.packages.setdefault(name, root)
        self.inferred: dict[str, list[str]] = {}    # top-level directory name -> roots that contain it
        for root in self.roots:
            tops = {rel.split("/")[0] for rel in (p[len(root) + 1:] for p in self.files if p.startswith(root + "/"))
                    if "/" in rel}
            for top in sorted(tops):
                self.inferred.setdefault(top, []).append(root)

    def _probe(self, base: str) -> str | None:
        base = posixpath.normpath(base)
        if base in self.files:
            return base
        for ext in _PROBE_EXTS:
            if base + ext in self.files:
                return base + ext
        stem, ext = posixpath.splitext(base)
        for repl in _JS_TO_TS.get(ext, ()):
            if stem + repl in self.files:
                return stem + repl
        for ext in _PROBE_EXTS:
            if f"{base}/index{ext}" in self.files:
                return f"{base}/index{ext}"
        return None

    def _file(self, base: str, via: str, spec: str) -> Resolved:
        hit = self._probe(base)
        if hit is not None:
            return Resolved("file", hit, via)
        base = posixpath.normpath(base)
        # a codegen output that is not committed: keep the edge, flag it, and give the node an extension so
        # a PR path `.../generated/graphql.ts` lands on it
        candidates = ("",) if posixpath.splitext(base)[1] else (".ts", ".json")
        for ext in candidates:
            if classify_file(base + ext, self.cfg) is FileClass.GENERATED and any(
                    base.startswith(r + "/") for r in self.roots):
                return Resolved("file", base + ext, via, generated_missing=True)
        outside = not any(base.startswith(r + "/") for r in self.roots)
        reason = "outside the indexed source roots (asset or config)" if outside else "no such file in the indexed tree"
        return Resolved("unresolved", spec, via, reason=reason)

    def _alias_bases(self, clean: str) -> list[str]:
        """Candidate repo paths for every alias prefix that matches, longest prefix first.

        An alias that already ends in `/src` also gets the stripped form of a leading `src/` in the remainder
        (`@scope/pkg/src/x` -> `<target>/x`), because apps alias `@scope/pkg` to the package's `src`.
        """
        bases: list[str] = []
        for prefix, target in self.aliases:
            if not clean.startswith(prefix):
                continue
            rest = clean[len(prefix):]
            bases.append(f"{target}/{rest}")
            if target.endswith("/src") and rest.startswith("src/"):
                bases.append(f"{target}/{rest[len('src/'):]}")
        return bases

    def resolve(self, spec: str, importer: str) -> Resolved:
        """Rules in order: relative, alias (when a target file exists), workspace package, inferred `@dir/*`.

        An alias whose target file is missing does not end the search: the workspace and inferred rules still
        get their turn (`@hoppscotch/common/src/x` may be the alias *and* a package path). Only when nothing
        resolves is the first alias candidate reported, so a missing codegen output keeps its GENERATED node.
        """
        clean = spec.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("."):
            return self._file(posixpath.join(posixpath.dirname(importer), clean), "relative", spec)
        alias_bases = self._alias_bases(clean)
        for base in alias_bases:
            hit = self._probe(base)
            if hit is not None:
                return Resolved("file", hit, "alias")
        pkg = package_name(clean)
        if pkg in self.packages:
            sub = clean[len(pkg):].lstrip("/")
            root = self.packages[pkg]
            if sub.startswith("src/") and root.endswith("/src"):
                sub = sub[len("src/"):]     # the app aliases `@scope/pkg` to `@scope/pkg/src`
            hit = self._probe(f"{root}/{sub}" if sub else f"{root}/index")
            if hit is not None or not alias_bases:
                return self._file(f"{root}/{sub}" if sub else f"{root}/index", "workspace", spec)
        if clean.startswith("@") and "/" in clean:
            top, _, rest = clean[1:].partition("/")
            for root in self.inferred.get(top, ()):
                hit = self._probe(f"{root}/{top}/{rest}")
                if hit is not None:
                    return Resolved("file", hit, "inferred_alias")
        if alias_bases:
            return self._file(alias_bases[0], "alias", spec)
        if clean.startswith("/"):
            return Resolved("unresolved", spec, "absolute", reason="absolute path (public asset or server path)")
        return Resolved("external", package_name(clean), "package")
