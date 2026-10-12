"""JS/TS module graph pieces shared by framework adapters (D10; cases 30, 33, 34).

`extract_imports` reads import bindings from a script with tree-sitter-typescript (no Vue compiler, no
network): default, named (with symbol names), namespace, side-effect, `import type`, `export ... from`
re-exports, and `import("literal")`. `ModuleResolver` turns a specifier into a repo file using only the
set of paths the indexer was allowed to list: relative paths, `cfg.code.alias` prefixes, workspace packages
that are source roots here, and a documented fallback for tsconfig-style `@dir/*` aliases.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

import tree_sitter_typescript as _tst
from tree_sitter import Language, Node, Parser, Query, QueryCursor

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


@lru_cache(maxsize=2)
def _queries(tsx: bool) -> tuple[Parser, Query, Query]:
    lang = _TSX if tsx else _TS
    imports = Query(lang, "[(import_statement) (export_statement source: (string))] @stmt")
    other = Query(lang, """
        (call_expression function: (import)) @dyn
        (comment) @comment
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


def extract_imports(source: str, *, tsx: bool = False) -> ScriptFacts:
    parser, q_import, q_other = _queries(tsx)
    data = source.encode("utf-8")
    root = parser.parse(data).root_node
    facts = ScriptFacts()
    for node in QueryCursor(q_import).captures(root).get("stmt", []):
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
    for call in caps.get("dyn", []):
        args = call.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        literal = _string_value(first) if first is not None else None
        if literal is None:
            facts.dynamic_nonliteral += 1
            continue
        facts.imports.append(ImportBinding(literal, (), (), "dynamic"))
        _lazy_binding(call, literal, facts.lazy_bindings)
    facts.comment_spans = [(n.start_byte, n.end_byte) for n in caps.get("comment", [])]
    facts.declares_locally = any(
        c.type not in ("import_statement", "comment", "empty_statement")
        and not (c.type == "export_statement" and c.child_by_field_name("source") is not None)
        for c in root.children)
    return facts


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
        for ext in ("", ".ts", ".json"):    # a codegen output that is not committed: keep the edge, flag it
            if classify_file(base + ext, self.cfg) is FileClass.GENERATED and any(
                    base.startswith(r + "/") for r in self.roots):
                return Resolved("file", base + ext, via, generated_missing=True)
        outside = not any(base.startswith(r + "/") for r in self.roots)
        reason = "outside the indexed source roots (asset or config)" if outside else "no such file in the indexed tree"
        return Resolved("unresolved", spec, via, reason=reason)

    def resolve(self, spec: str, importer: str) -> Resolved:
        clean = spec.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("."):
            return self._file(posixpath.join(posixpath.dirname(importer), clean), "relative", spec)
        for prefix, target in self.aliases:
            if clean.startswith(prefix):
                return self._file(f"{target}/{clean[len(prefix):]}", "alias", spec)
        pkg = package_name(clean)
        if pkg in self.packages:
            sub = clean[len(pkg):].lstrip("/")
            root = self.packages[pkg]
            if sub.startswith("src/") and root.endswith("/src"):
                sub = sub[len("src/"):]     # the app aliases `@scope/pkg` to `@scope/pkg/src`
            return self._file(f"{root}/{sub}" if sub else f"{root}/index", "workspace", spec)
        if clean.startswith("@") and "/" in clean:
            top, _, rest = clean[1:].partition("/")
            for root in self.inferred.get(top, ()):
                hit = self._probe(f"{root}/{top}/{rest}")
                if hit is not None:
                    return Resolved("file", hit, "inferred_alias")
        if clean.startswith("/"):
            return Resolved("unresolved", spec, "absolute", reason="absolute path (public asset or server path)")
        return Resolved("external", package_name(clean), "package")
