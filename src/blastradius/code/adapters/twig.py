"""Twig adapter: template render graph, hook wiring, routes and translations (D10, D15, D24; cases 20, 29,
32, 35, 36, 40, 73).

Everything is deterministic and offline, and it sits behind the same `CodeAdapter` protocol as `VueAdapter`.
Nothing here executes PHP or Twig: templates are scanned with a linear tokenizer, YAML with `safe_load`.

What a Sylius v2 storefront is made of, and where each piece lands in the `CodeIndex`:

  templates   `{% extends %}`, `{% include %}`, `include('x')` and `component('x', {template: ...})` are RENDERS
              edges (a page "renders" its layout); so is a template name that only appears as a string in an
              expression (`pagerfanta(p, 'twig', {template: '...'})`: the function renders it). `{% import %}`,
              `{% from %}`, `{% use %}`, `{% embed %}` and `form_theme` are IMPORTS edges (macros, themes, bases).
              `@Namespace/...` names resolve through `cfg.code.alias` (`"@SyliusShop/": "src/.../templates/"`); a
              name with no alias is an EXTERNAL leaf reached by an IMPORTS edge, as the Vue adapter treats a package.
  hooks       `{% hook 'name' with {_prefixes: p} %}` renders whatever the wiring YAML (`sylius_twig_hooks:
              hooks:`) registers under that hook. The wiring file is its own node: caller -RENDERS-> wiring
              file -RENDERS-> wired template. That keeps every file's edges a function of its own text, which
              is what the PR head overlay needs (it re-reads only changed files). The price is that a wiring
              file joins every caller of any hook it defines, so two templates wired under different hooks of
              one file are two hops apart (inside one page, or the layout, which is global anyway). A relative
              name (`{% hook 'content' %}`) resolves against the hook the template is wired under; `_prefixes`
              come from `{% set prefixes = [...] %}` in the page and its layouts; `name#variant` and absolute
              names work as written. Hook names that no YAML defines are extension points: counted
              (`hook_calls_unresolved`), never edged.
  routes      Symfony routing YAML (`path`, `defaults._sylius.template`, `defaults.template`), with `resource`
              + `prefix` mounts followed through the indexed files, `{_locale}` kept as the parameter `:_locale`
              and a `.+` requirement also emitted as a `:slug(.*)` catch-all; the file of a route is its template
              (found by route name when the YAML names none, because the controller renders it).
  i18n        every `cfg.code.locale_files` catalogue is flattened (`sylius: ui: add: x` -> `sylius.ui.add`),
              later files win like Symfony's bundle order. Uses are `'k'|trans`, a parenthesised
              concatenation or ternary before `|trans`, a `{% trans %}` block holding a key, and a literal
              key passed to an imported macro (`messages.info('sylius.ui.x')`); a full index additionally
              counts any string literal in a Twig expression that is a catalogue key. Keys in hook
              `configuration` values are uses of the wiring file.
  literals    hard-coded visible text and `title`/`alt`/`placeholder`/`aria-label` values of templates, one per
              text node and one per block's inline run.

File classes (decided here, since `classify_file` has no rule for `.twig`): LAYOUT for `cfg.global_files` and
a `base.html.twig` in a `layout` directory, PAGE for a route's template or one that extends a layout, UTIL for
macro directories, COMPONENT for the rest and for hook wiring, LOCALE for catalogues, SOURCE_OTHER for routing
YAML, and `classify_file` only for tests and generated files: a template's *name* (`security.html.twig`) is
never read as documentation.

Overlay mode (`index_part`): `context_paths` lists the wiring, routing and catalogue files a changed template
needs; when the overlay materialises them beside the changed files the part equals a fresh index of the head
(tested; measured on Sylius #19187 and #19170). Without them it falls back to the changed wiring files only
and `i18n=None`.

Known holes, counted in `stats` rather than hidden: dynamic template names, hook names built with `~`, a
`component()` whose template lives in a PHP attribute (resolved by name suffix when unique), vendor templates
(`@BabDevPagerfanta/...`, external leaves), `<twig:...>` syntax, `{% verbatim %}` bodies, a route whose
controller picks the template, and form labels and flash messages that PHP supplies.
"""

from __future__ import annotations

import hashlib
import html
import posixpath
import re
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

from ...models import CodeEdge, CodeFile, CodeIndex, FileClass, KeyUse, RouteDef, TextLiteral
from ...targets import CodeConfig
from ..adapter import IndexPart, SourceTree
from ..fileclass import classify_file

INDEXED_EXTS = frozenset({".twig", ".yaml", ".yml"})
EdgeKind = Literal["IMPORTS", "RENDERS"]

# Problem kinds recorded per file (stats.parse_error_kinds); each means the index is incomplete for that file.
UNDECODABLE_BYTES = "undecodable_bytes"
UNREADABLE_FILE = "unreadable_file"
OVERSIZE_FILE = "oversize_file"
YAML_PARSE_ERROR = "yaml_parse_error"
YAML_TOO_LARGE = "yaml_too_large"
LOCALE_PARSE_ERROR = "locale_parse_error"
LOCALE_TRUNCATED = "locale_truncated"

_UNLISTED_CAP = 50
_CATALOGUE_NODE_CAP = 50_000      # nodes walked in one catalogue: an alias bomb must not become a stall
_CONFIG_NODE_CAP = 10_000         # nodes walked in one hookable's `configuration`
_EXPR_CAP = 4_000                 # characters of one Twig expression that are ever examined
_MAX_TEXT = 200
_SNIPPET = 160

_OPEN = re.compile(r"\{[{%#]")
_CLOSER = {"{{": "}}", "{%": "%}", "{#": "#}"}
_STR = re.compile(r"""'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)\"""")
_TWIG_NAME = re.compile(r"^(?:@[\w.-]+/)?[\w-][\w./-]*\.twig$")
_KEY_SHAPE = re.compile(r"^[A-Za-z][\w-]*(?:\.[\w-]+)+$")
_VAR = re.compile(r"^[A-Za-z_]\w*$")
_SET = re.compile(r"^set\s+([A-Za-z_]\w*)\s*=\s*(.+)$", re.DOTALL)
_TRANS = re.compile(r"\|\s*trans\b")
_MACRO_CALL = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\(\s*(['\"])([^'\"]{1,200})\3")
_IMPORT_AS = re.compile(r"^import\s+.+?\s+as\s+([A-Za-z_]\w*)\s*$", re.DOTALL)
_FROM_IMPORT = re.compile(r"^from\s+(.+?)\s+import\s+(.+)$", re.DOTALL)
_INCLUDE_FN = re.compile(r"\b(?:include|source)\(")
_COMPONENT_FN = re.compile(r"\bcomponent\(")
_ATTR_TEXT = re.compile(r"""\b(?:title|alt|placeholder|aria-label|label)\s*=\s*(?:"([^"{}]{2,200})"|'([^'{}]{2,200})')""")
_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------------------------- tokenizer
def regions(text: str) -> Iterator[tuple[str, str]]:
    """Split a template into ("text" | "print" | "tag" | "comment", content), linear in the text size.

    An opener without a closer is plain text; once a closer kind is known to be absent every later opener of
    that kind is text too, so a file of repeated `{{` cannot cost a rescan each.
    """
    i, n = 0, len(text)
    absent: set[str] = set()
    kinds = {"{{": "print", "{%": "tag", "{#": "comment"}
    while i < n:
        m = _OPEN.search(text, i)
        if m is None:
            yield "text", text[i:]
            return
        opener = m.group(0)
        closer = _CLOSER[opener]
        if closer in absent:
            yield "text", text[i:]
            return
        end = text.find(closer, m.end())
        if end == -1:
            absent.add(closer)
            yield "text", text[i:m.end()]
            i = m.end()
            continue
        if m.start() > i:
            yield "text", text[i:m.start()]
        yield kinds[opener], text[m.end():end]
        i = end + 2


def _strings(expr: str) -> list[str]:
    return [a if a is not None and a != "" else b for a, b in _STR.findall(expr[:_EXPR_CAP])] if expr else []


def _literal(expr: str) -> str | None:
    m = _STR.fullmatch(expr.strip())
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else None


def _split_top(expr: str, sep: str) -> list[str]:
    """Split on `sep` outside quotes and brackets."""
    out, depth, quote, start = [], 0, "", 0
    for i, c in enumerate(expr[:_EXPR_CAP]):
        if quote:
            if c == quote and expr[i - 1] != "\\":
                quote = ""
        elif c in "'\"":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            out.append(expr[start:i])
            start = i + 1
    out.append(expr[start:_EXPR_CAP])
    return out


def _call_args(text: str, open_paren: int) -> str | None:
    """Text between the parenthesis at `open_paren` and its match (string aware, bounded), or None."""
    depth, quote = 0, ""
    for i in range(open_paren, min(len(text), open_paren + _EXPR_CAP)):
        c = text[i]
        if quote:
            if c == quote and text[i - 1] != "\\":
                quote = ""
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
    return None


def eval_list(expr: str, env: Mapping[str, list[str]]) -> list[str] | None:
    """Value of a small Twig expression that stands for strings: a literal, a list literal, a known variable,
    and the `|default(x)` / `|merge(x)` filters. None when anything else (`~`, calls) is involved."""
    parts = _split_top(expr.strip(), "|")
    cur = _term(parts[0].strip(), env)
    for flt in parts[1:]:
        flt = flt.strip()
        name, _, rest = flt.partition("(")
        arg = rest.rstrip(")").strip()
        if cur is None:
            return None
        if name == "default":
            cur = cur or _term(arg, env) or []
        elif name == "merge":
            more = _term(arg, env)
            if more is None:
                return None
            cur = [*cur, *more]
        else:
            return None
    return cur


def _term(term: str, env: Mapping[str, list[str]]) -> list[str] | None:
    term = term.strip()
    lit = _literal(term)
    if lit is not None:
        return [lit]
    if term.startswith("[") and term.endswith("]"):
        out: list[str] = []
        for item in _split_top(term[1:-1], ","):
            item = item.strip()
            if not item:
                continue
            got = _term(item, env)
            if got is None:
                return None
            out.extend(got)
        return out
    if _VAR.match(term):
        return list(env.get(term, []))
    return None


# ------------------------------------------------------------------------------------------ the facts
@dataclass
class HookCall:
    expr: str                          # the hook name expression as written (`'content'`, `homepage_hook`)
    prefixes_expr: str | None = None   # the `_prefixes:` expression passed with it


@dataclass
class TemplateFacts:
    sets: list[tuple[str, str]] = field(default_factory=list)          # (name, expression) in order
    extends: list[str] = field(default_factory=list)
    renders: list[tuple[str, str]] = field(default_factory=list)       # (template name, via)
    imports: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)   # (name, via, symbols)
    dynamic: list[tuple[str, str]] = field(default_factory=list)       # (kind, snippet)
    hooks: list[HookCall] = field(default_factory=list)
    components: list[tuple[str, list[str]]] = field(default_factory=list)   # (component key, templates)
    keys: list[str] = field(default_factory=list)
    key_prefixes: list[str] = field(default_factory=list)
    key_unresolved: int = 0
    mentions: list[str] = field(default_factory=list)                  # template names only named in expressions
    string_pool: set[str] = field(default_factory=set)                 # literals of Twig expressions
    texts: list[str] = field(default_factory=list)
    attr_texts: list[str] = field(default_factory=list)
    unterminated: bool = False


_CLASSY = re.compile(r"^[a-z0-9]+(?:[-_:][a-z0-9]+)+$")


def _visible(text: str) -> str | None:
    """Words a visitor reads: two letters at least, and not a run of CSS class names or an attribute."""
    text = _WS.sub(" ", html.unescape(text)).strip()
    if sum(ch.isalpha() for ch in text) < 2 or '="' in text:
        return None
    words = text.split(" ")
    if all(_CLASSY.match(w) for w in words) and any("-" in w or "_" in w for w in words):
        return None
    return text[:_MAX_TEXT]


_INLINE = frozenset({"a", "abbr", "b", "bdi", "bdo", "cite", "code", "em", "i", "kbd", "label", "mark", "s",
                     "small", "span", "strong", "sub", "sup", "u"})
_TAG_NAME = re.compile(r"/?([A-Za-z][\w:-]*)")


def _scan_markup(chunk: str, in_tag: bool, facts: TemplateFacts) -> tuple[bool, list[str]]:
    """Visible-text runs of one text region; `in_tag` carries a tag left open by a Twig print inside it.

    A run is the text of one block (`Hello <b>world</b>` is `Hello world`; a second `<button>` starts a new
    run), and a run of several nodes also yields each node. Attributes are read up to the end of the region
    even while the tag is still open, so `alt="x"` before `src="{{ asset(p) }}"` is not lost.
    """
    runs: list[str] = []
    group: list[str] = []

    def close_group() -> None:
        nodes = [x for x in (g.strip() for g in group) if x]
        if len(nodes) > 1:
            runs.append(" ".join(nodes))
        runs.extend(nodes)
        group.clear()

    i, n = 0, len(chunk)
    while i < n:
        if in_tag:
            end = chunk.find(">", i)
            for m in _ATTR_TEXT.finditer(chunk, i, n if end == -1 else end):
                text = _visible(m.group(1) or m.group(2) or "")
                if text:
                    facts.attr_texts.append(text)
            if end == -1:
                break
            i, in_tag = end + 1, False
            continue
        lt = chunk.find("<", i)
        if lt == -1 or lt + 1 >= n or not (chunk[lt + 1].isalpha() or chunk[lt + 1] in "/!"):
            if lt != -1 and lt + 1 < n:       # a stray `<` in text (`a < b`): keep scanning after it
                group.append(chunk[i:lt + 1])
                i = lt + 1
                continue
            group.append(chunk[i:])
            break
        group.append(chunk[i:lt])
        name = _TAG_NAME.match(chunk, lt + 1)
        if name is None or name.group(1).lower() not in _INLINE:
            close_group()                      # a block tag, a comment or a doctype ends the run
        i, in_tag = lt + 1, True
        if chunk.startswith("!--", i):
            end = chunk.find("-->", i)
            i, in_tag = (n, False) if end == -1 else (end + 3, False)
    close_group()
    return in_tag, runs


def _scan_keys(body: str, aliases: set[str], facts: TemplateFacts) -> None:
    """`|trans` uses and literal keys passed to imported macros, in one Twig expression."""
    for m in _TRANS.finditer(body[:_EXPR_CAP * 4]):
        before = body[:m.start()].rstrip()
        if before.endswith(("'", '"')):
            q = before[-1]
            start = before.rfind(q, 0, len(before) - 1)
            if start != -1:
                facts.keys.append(before[start + 1:-1])
                continue
        if before.endswith(")"):
            depth, i = 0, len(before) - 1
            while i >= 0 and len(before) - i < 400:
                c = before[i]
                depth += c == ")"
                depth -= c == "("
                if depth == 0:
                    break
                i -= 1
            group = before[i + 1:-1] if depth == 0 and i >= 0 else ""
            lits = _strings(group)
            if "~" in group and lits:
                facts.key_prefixes.append(lits[0])
                continue
            if "?" in group and len(lits) >= 2:
                facts.keys.extend(lits)
                continue
        facts.key_unresolved += 1
    for m in _MACRO_CALL.finditer(body[:_EXPR_CAP * 4]):
        if m.group(1) in aliases and _KEY_SHAPE.match(m.group(4)):
            facts.keys.append(m.group(4))


def _template_refs(expr: str, facts: TemplateFacts, via: str, snippet: str) -> list[str]:
    """Literal `.twig` names of an include-like expression; a dynamic element is recorded as a hole.

    `['a.twig', 'b.twig']` fallbacks are read element by element, so `[x ~ '.html.twig', 'b.twig']` still
    yields `b.twig` and one hole.
    """
    expr = re.split(r"\s+(?:with|only|ignore)\b", expr.strip(), maxsplit=1)[0].strip()
    inner = expr[1:-1] if expr.startswith("[") and expr.endswith("]") else expr
    names: list[str] = []
    dynamic = False
    for element in _split_top(inner, ","):
        literal = _literal(element)
        if literal is not None and _TWIG_NAME.match(literal):
            names.append(literal)
        elif element.strip():
            dynamic = True
    if dynamic or not names:
        facts.dynamic.append((via, snippet[:_SNIPPET]))
    return names


def _tag_import(head: str, body: str, rest: str, facts: TemplateFacts, aliases: set[str]) -> None:
    source = rest.split(" as ", 1)[0] if head == "import" else rest.split(" import ", 1)[0]
    target = source.strip() if source.strip() == "_self" else _literal(source)
    if target is None:
        facts.dynamic.append(("import", body[:_SNIPPET]))
        return
    if head == "import":
        m = _IMPORT_AS.match(body)
        if m:
            aliases.add(m.group(1))
    if target == "_self":
        return                          # macros of the same file: no edge
    symbols: tuple[str, ...] = ()
    if head == "from":
        m = _FROM_IMPORT.match(body)
        symbols = tuple(sorted(s.split(" as ")[0].strip() for s in (m.group(2) if m else "").split(",")
                               if s.strip()))
    facts.imports.append((target, head, symbols))


def _scan_tag(body: str, facts: TemplateFacts, aliases: set[str]) -> None:
    body = body.strip().strip("-").strip()
    head, _, rest = body.partition(" ")
    rest = rest.strip()
    if head == "set":
        m = _SET.match(body)
        if m:
            facts.sets.append((m.group(1), m.group(2).strip()))
    elif head == "extends":
        facts.extends.extend(_template_refs(rest, facts, "extends", body)[:1])
    elif head == "include":
        facts.renders.extend((n, head) for n in _template_refs(rest, facts, head, body))
    elif head == "embed":           # renders its template too, but the target is a base to override: IMPORTS
        facts.imports.extend((n, head, ()) for n in _template_refs(rest, facts, head, body))
    elif head in ("import", "from"):
        _tag_import(head, body, rest, facts, aliases)
    elif head == "use":
        target = _literal(rest.split(" with ", 1)[0])
        if target is not None:
            facts.imports.append((target, "use", ()))
    elif head == "form_theme":
        facts.imports.extend((n, "form_theme", ()) for n in _strings(rest) if _TWIG_NAME.match(n))
    elif head == "hook":
        facts.hooks.append(_hook_call(rest))


def _hook_call(rest: str) -> HookCall:
    head, sep, tail = re.sub(r"\s+only\s*$", "", rest.strip()).partition(" with ")
    call = HookCall(expr=head.strip()[:_EXPR_CAP])
    m = re.search(r"\b_prefixes\s*:\s*", tail) if sep else None
    if m:
        value = _split_top(tail[m.end():], ",")[0].strip().rstrip("}").strip()
        call.prefixes_expr = value[:_EXPR_CAP] or None
    return call


def _scan_functions(body: str, facts: TemplateFacts) -> None:
    """`include('x')`, `source('x')` and `component('key', {template: 'x'})` inside an expression."""
    for m in _INCLUDE_FN.finditer(body):
        args = _call_args(body, m.end() - 1)
        if args is None:
            continue
        first = _split_top(args, ",")[0]
        names = [s for s in _strings(first) if _TWIG_NAME.match(s)]
        if names and "~" not in first:
            facts.renders.extend((n, "include") for n in names)
        else:
            facts.dynamic.append(("include", args[:_SNIPPET]))
    for m in _COMPONENT_FN.finditer(body):
        args = _call_args(body, m.end() - 1)
        if args is None:
            continue
        parts = _split_top(args, ",")
        key = _literal(parts[0]) if parts else None
        if key is None:
            facts.dynamic.append(("component", args[:_SNIPPET]))
            continue
        facts.components.append((key, [s for s in _strings(",".join(parts[1:])) if _TWIG_NAME.match(s)]))


def scan_template(text: str, *, with_literals: bool = True) -> TemplateFacts:
    """Facts of one template: structure references, hooks, translation keys and visible text."""
    facts = TemplateFacts()
    aliases: set[str] = set()
    in_tag = in_trans = False
    code: list[str] = []

    for kind, content in regions(text):
        if kind == "comment":
            continue
        if kind == "text" and in_trans:             # `{% trans %}sylius.ui.x{% endtrans %}`: the id is the body
            body = content.strip()
            if _KEY_SHAPE.match(body):
                facts.keys.append(body)
            else:
                facts.texts.extend(t for t in [_visible(body)] if t)
            continue
        if kind == "text":
            in_tag, runs = _scan_markup(content, in_tag, facts)
            facts.texts.extend(t for t in (_visible(r) for r in runs) if t)
            continue
        code.append(content)
        if kind == "tag":
            in_trans = content.strip().strip("-").strip().split(" ", 1)[0] == "trans"
            _scan_tag(content, facts, aliases)
    for body in code:
        _scan_functions(body, facts)
        _scan_keys(body, aliases, facts)
        facts.string_pool.update(s for s in _strings(body) if 3 <= len(s) <= 200)
    known = {*facts.extends, *(n for n, _ in facts.renders), *(n for n, _, _ in facts.imports),
             *(t for _, ts in facts.components for t in ts)}
    facts.mentions = sorted(s for s in facts.string_pool if _TWIG_NAME.match(s) and s not in known)
    if not with_literals:
        facts.texts, facts.attr_texts = [], []
    return facts


# --------------------------------------------------------------------------------------------- wiring
@dataclass(frozen=True)
class Hookable:
    hook: str
    name: str
    file: str                     # the YAML file that last set this entry
    template: str | None = None   # a template name as written (`@SyliusShop/...`)
    component: str | None = None


@dataclass
class WiringFile:
    hookables: list[Hookable] = field(default_factory=list)
    config_keys: list[str] = field(default_factory=list)


def _walk_yaml(node: Any, budget: list[int]) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Leaves with their key path; stops when the node budget is spent (alias bombs)."""
    stack: list[tuple[tuple[str, ...], Any]] = [((), node)]
    while stack and budget[0] > 0:
        path, value = stack.pop()
        budget[0] -= 1
        if isinstance(value, Mapping):
            for key in sorted(value, key=str, reverse=True):
                stack.append(((*path, str(key)), value[key]))
        elif isinstance(value, list):
            for i in reversed(range(len(value))):
                stack.append(((*path, str(i)), value[i]))
        else:
            yield path, value


def flatten_catalogue(data: Mapping[Any, Any],
                      budget: int = _CATALOGUE_NODE_CAP) -> tuple[dict[str, str], int, bool]:
    """`({dotted.key: text}, non-string leaves skipped, cut)` of a message tree, in document order.

    YAML aliases share nodes, so a small file can describe 9^9 leaves: the walk stops after `budget` nodes
    and says so (`cut`). `code.i18n.flatten_messages` is for JSON, which cannot alias, and is not bounded.
    """
    out: dict[str, str] = {}
    skipped = seen = 0
    stack: list[tuple[str, Any]] = [("", data)]
    while stack:
        path, node = stack.pop()
        seen += 1
        if seen > budget:
            return out, skipped, True
        if isinstance(node, Mapping):
            for key in reversed(list(node)):
                stack.append((f"{path}.{key}" if path else str(key), node[key]))
        elif isinstance(node, str):
            out[path] = node
        else:
            skipped += 1
    return out, skipped, False


def parse_wiring(doc: Any, path: str) -> WiringFile | None:
    """Hookables of a `sylius_twig_hooks: {hooks: ...}` document, or None when the file is not one."""
    if not isinstance(doc, Mapping):
        return None
    block = next((v for k, v in doc.items() if str(k).endswith("twig_hooks") and isinstance(v, Mapping)), None)
    hooks = block.get("hooks") if isinstance(block, Mapping) else None
    if not isinstance(hooks, Mapping):
        return None
    out = WiringFile()
    for hook in sorted(hooks, key=str):
        entries = hooks[hook]
        if not isinstance(entries, Mapping):
            continue
        for name in sorted(entries, key=str):
            cfg = entries[name]
            if not isinstance(cfg, Mapping) or cfg.get("enabled") is False:
                continue
            props = cfg.get("props") if isinstance(cfg.get("props"), Mapping) else {}
            tpl = cfg.get("template") or props.get("template")
            comp = cfg.get("component")
            out.hookables.append(Hookable(str(hook), str(name), path, tpl if isinstance(tpl, str) else None,
                                          comp if isinstance(comp, str) else None))
            conf = cfg.get("configuration")
            for _, leaf in _walk_yaml(conf, [_CONFIG_NODE_CAP]):
                if isinstance(leaf, str) and _KEY_SHAPE.match(leaf) and leaf.count(".") >= 2:
                    out.config_keys.append(leaf)
    return out


# ----------------------------------------------------------------------------------------------- routes
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _route_pattern(path: str) -> str:
    return _PLACEHOLDER.sub(r":\1", path)


def _join(prefix: str, path: str) -> str:
    joined = "/" + "/".join(s for s in (prefix.strip("/"), path.strip("/")) if s)
    return joined + ("/" if path.endswith("/") and joined != "/" else "")


def route_templates(entry: Mapping[str, Any]) -> list[str]:
    """Template names a route definition names (`defaults._sylius.template`, `defaults.template`, `template`)."""
    out: list[str] = []
    defaults = entry.get("defaults") if isinstance(entry.get("defaults"), Mapping) else {}
    sylius = defaults.get("_sylius") if isinstance(defaults.get("_sylius"), Mapping) else {}
    for value in (sylius.get("template"), defaults.get("template"), entry.get("template")):
        if isinstance(value, str) and value.endswith(".twig"):
            out.append(value)
    return out


# ------------------------------------------------------------------------------------------- the build
def _decode(data: bytes) -> tuple[str, str | None]:
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), UNDECODABLE_BYTES


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _loc(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _strip_variant(name: str) -> str:
    return name.split("#", 1)[0]


def _bundle_of(path: str) -> str | None:
    """`ShopBundle` for `src/Sylius/Bundle/ShopBundle/...`: the `Bundle` directory itself is a namespace."""
    return next((p for p in PurePosixPath(path).parts if p.endswith("Bundle") and p != "Bundle"), None)


class TwigAdapter:
    name = "twig"
    exts = INDEXED_EXTS

    def index(self, repo_root: Path, cfg: CodeConfig, snapshot_id: str, *, target: str | None = None,
              ref: str | None = None) -> CodeIndex:
        """`target` and `ref` stamp the result; a bare call falls back to the repo name and `cfg.ref`."""
        return _Build(repo_root, cfg).run(snapshot_id, target or cfg.repo, ref or cfg.ref)

    def context_paths(self, cfg: CodeConfig, files: Iterable[CodeFile]) -> list[str]:
        """Files to materialise beside a PR's changed ones so `index_part` resolves them like a full index.

        A changed template's `{% hook 'content' %}` means whatever the *unchanged* wiring files say, a route
        needs its mount, and the English table is the merge of every catalogue: so the base index's wiring,
        routing and catalogue files are context. Templates are not (their facts do not depend on each other).
        """
        paths = {f.path for f in files if f.sha is not None and not f.path.endswith(".twig")}
        return sorted(paths | set(cfg.locale_files))

    def index_part(self, repo_root: Path, cfg: CodeConfig, *, known_paths: Collection[str],
                   only: Collection[str], dangling: Sequence[CodeEdge] = ()) -> IndexPart:
        """Facts of the files in `only`, resolved against `known_paths` (the PR head overlay's contract).

        Non-template files that are present in `repo_root` but not in `only` (see `context_paths`) are read as
        context: they feed the hook table, the route mounts and the catalogue, and produce nothing themselves.
        Without them a changed template's hook calls resolve against the wiring files that changed with it.
        `i18n` is the head's table when a catalogue changed and all of them were read, else None (the overlay
        keeps the base table).
        """
        return _Build(repo_root, cfg, known_paths=known_paths, only=frozenset(only)).part(dangling)


class _Build:
    def __init__(self, repo_root: Path, cfg: CodeConfig, *, known_paths: Collection[str] | None = None,
                 only: frozenset[str] | None = None):
        self.cfg = cfg
        self.tree = SourceTree(repo_root, cfg, INDEXED_EXTS)
        self.only = only
        self.universe: set[str] = set(known_paths) if known_paths is not None else set(self.tree.paths)
        self.texts: dict[str, str] = {}
        self.sha: dict[str, str] = {}
        self.issues: dict[str, list[str]] = {}
        self.facts: dict[str, TemplateFacts] = {}
        self.wiring: dict[str, WiringFile] = {}
        self.docs: dict[str, Any] = {}
        self.catalogue: dict[str, str] = {}
        self.kind: dict[str, FileClass] = {}
        self.edges: dict[tuple[EdgeKind, str, str], tuple[set[str], str]] = {}
        self.externals: set[str] = set()
        self.n: Counter[str] = Counter()
        self.unresolved: list[dict[str, str]] = []
        self.reasons: Counter[str] = Counter()
        self.hook_unresolved: list[dict[str, str]] = []
        self.routes_out: list[RouteDef] = []
        self.key_uses: list[KeyUse] = []
        self.aliases = sorted(((k, v.rstrip("/")) for k, v in cfg.alias.items()), key=lambda kv: -len(kv[0]))
        self.twig_paths = sorted(p for p in self.universe if p.endswith(".twig"))
        self._by_suffix: dict[str, list[str]] | None = None
        self.route_files: set[str] = set()
        self.hook_names = 0
        self.extra: dict[str, Any] = {}

    # ------------------------------------------------------------------------------------ load
    def _emits(self, path: str) -> bool:
        """Whether `path` is one this build reports facts about (every file, or only the overlay's changed ones)."""
        return self.only is None or path in self.only

    def _note(self, path: str, kind: str) -> None:
        kinds = self.issues.setdefault(path, [])
        if kind not in kinds:
            kinds.append(kind)

    def load(self) -> None:
        for path in self.tree.oversize:
            self._note(path, OVERSIZE_FILE)
        for path in self.tree.paths:
            if not self._emits(path) and path.endswith(".twig"):
                continue                # templates stand alone; wiring, routing and catalogues are context
            try:
                data = self.tree.read_bytes(path)
            except PermissionError:
                self._note(path, UNREADABLE_FILE)
                continue
            text, problem = _decode(data)
            if problem:
                self._note(path, problem)
            self.texts[path], self.sha[path] = text, _blob_sha(data)
        self.universe |= set(self.texts)
        self.twig_paths = sorted(p for p in self.universe if p.endswith(".twig"))
        for path in sorted(self.texts):
            if path.endswith(".twig"):
                self._read_template(path)
            elif path in self.cfg.locale_files:
                continue
            else:
                self._read_yaml(path)
        self._read_catalogues()

    def _read_template(self, path: str) -> None:
        self.facts[path] = scan_template(self.texts[path])

    def _read_yaml(self, path: str) -> None:
        text = self.texts[path]
        if len(text) > 2_000_000:
            self._note(path, YAML_TOO_LARGE)
            return
        try:
            doc = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError, ValueError):
            self._note(path, YAML_PARSE_ERROR)
            return
        wiring = parse_wiring(doc, path)
        if wiring is not None:
            self.wiring[path] = wiring
        elif isinstance(doc, Mapping):
            self.docs[path] = doc

    def _read_catalogues(self) -> None:
        for path in self.cfg.locale_files:
            if path not in self.texts:
                continue
            try:
                data = yaml.safe_load(self.texts[path]) or {}
            except (yaml.YAMLError, RecursionError, ValueError):
                self._note(path, LOCALE_PARSE_ERROR)
                continue
            flat, skipped, cut = flatten_catalogue(data) if isinstance(data, Mapping) else ({}, 0, False)
            if cut:
                self._note(path, LOCALE_TRUNCATED)
            self.n["i18n_nonstring_leaves"] += skipped
            self.catalogue.update(flat)

    # ----------------------------------------------------------------------------- classification
    def _kind_of(self, path: str) -> FileClass:
        if path in self.kind:
            return self.kind[path]
        base = classify_file(path, self.cfg)
        if path.endswith(".twig") or path in self.wiring:
            # A template's NAME decides nothing here: `security.html.twig` is the header's login menu and
            # `notice.html.twig` a flash message, not documentation (fileclass reads `SECURITY`/`NOTICE` as docs).
            kind = base if base in (FileClass.TEST, FileClass.GENERATED) else self._twig_kind(path)
        else:                       # routing and config YAML: never docs, whatever `security.yml` is called
            kind = FileClass.SOURCE_OTHER if base is FileClass.DOCS else base
        self.kind[path] = kind
        return kind

    def _twig_kind(self, path: str) -> FileClass:
        parts = PurePosixPath(path).parts
        name = parts[-1]
        if path in self.cfg.global_files or (name == "base.html.twig" and len(parts) > 1 and parts[-2] == "layout"):
            return FileClass.LAYOUT
        if path in self.wiring:
            return FileClass.COMPONENT
        if "macro" in parts or "macros" in parts:
            return FileClass.UTIL
        if path in self.route_files or self._extends_layout(path):
            return FileClass.PAGE
        return FileClass.COMPONENT

    def _extends_layout(self, path: str) -> bool:
        facts = self.facts.get(path)
        if facts is None or not facts.extends:
            return False
        target = self.resolve_name(facts.extends[0], path)[0]
        return bool(target) and (target in self.cfg.global_files or PurePosixPath(target).name == "base.html.twig"
                                 and PurePosixPath(target).parent.name == "layout")

    # ----------------------------------------------------------------------------------- names
    def suffix_index(self) -> dict[str, list[str]]:
        if self._by_suffix is None:
            idx: dict[str, list[str]] = {}
            for p in self.twig_paths:
                parts = PurePosixPath(p).parts
                for i in range(len(parts)):
                    idx.setdefault("/".join(parts[i:]), []).append(p)
            self._by_suffix = idx
        return self._by_suffix

    def resolve_name(self, name: str, importer: str) -> tuple[str | None, str]:
        """(path, how): `how` is file | external | missing | self | relative. The path is None unless file."""
        if name == "_self":
            return importer, "self"
        for prefix, target in self.aliases:
            if name.startswith(prefix):
                path = f"{target}/{name[len(prefix):]}"
                return (path, "file") if path in self.universe else (None, "missing")
        if name.startswith("@"):
            return None, "external"
        for root in self._template_roots(importer):
            candidate = f"{root}/{name}"
            if candidate in self.universe:
                return candidate, "file"
        return None, "external"         # a bare name the app does not own: a vendor (Symfony, KNP) template

    def _template_roots(self, importer: str) -> list[str]:
        roots = [r.rstrip("/") for r in self.cfg.roots if importer.startswith(r.strip("/") + "/")]
        return sorted(roots, key=len, reverse=True)

    # ---------------------------------------------------------------------------------- edges
    def _add_edge(self, kind: EdgeKind, src: str, dst: str, symbols: tuple[str, ...], via: str) -> None:
        if src == dst:
            self.n["self_references"] += 1
            return
        have, kept = self.edges.get((kind, src, dst), (set(), via))
        self.edges[(kind, src, dst)] = (have | set(symbols), min(kept, via))

    def _ref(self, src: str, name: str, kind: EdgeKind, via: str, symbols: tuple[str, ...] = ()) -> None:
        self.n["template_refs"] += 1
        path, how = self.resolve_name(name, src)
        if how == "external":
            self.externals.add(name)
            self.n["template_refs_external"] += 1
            self._add_edge("IMPORTS", src, name, symbols, name)
        elif path:
            self.n["template_refs_resolved"] += 1
            self._add_edge(kind, src, path, symbols, via)
        else:
            self.n["template_refs_unresolved"] += 1
            self.reasons[f"template: {how}"] += 1
            if len(self.unresolved) < _UNLISTED_CAP:
                self.unresolved.append({"file": src, "name": name, "reason": how})

    def resolve_structure(self) -> None:
        for path, facts in sorted(self.facts.items()):
            for name in facts.extends:
                self._ref(path, name, "RENDERS", name)
            for name, _how in facts.renders:
                self._ref(path, name, "RENDERS", name)
            for name, _how, symbols in facts.imports:
                self._ref(path, name, "IMPORTS", name, symbols)
            for name in facts.mentions:         # `pagerfanta(p, 'twig', {template: 'x.html.twig'})`: rendered by the callee
                self._ref(path, name, "RENDERS", name)
            for key, templates in facts.components:
                self.n["component_calls"] += 1
                if templates:
                    self.n["component_calls_literal_template"] += 1
                    for name in templates:
                        self._ref(path, name, "RENDERS", f"component:{key}")
                    continue
                target = self._component_by_name(key)
                if target:
                    self.n["component_calls_by_name"] += 1
                    self._add_edge("RENDERS", path, target, (), f"component:{key}")
                else:
                    self.n["component_calls_unresolved"] += 1
                    self.reasons["component: template is in PHP"] += 1
            for kind, snippet in facts.dynamic:
                self.n["dynamic_template_names"] += 1
                self.reasons[f"template: dynamic {kind}"] += 1
                if len(self.unresolved) < _UNLISTED_CAP:
                    self.unresolved.append({"file": path, "name": snippet, "reason": f"dynamic {kind}"})

    def _component_by_name(self, key: str) -> str | None:
        """`sylius_shop:order_item:product_info` -> the unique template ending `order_item/product_info.html.twig`
        (longest suffix first, never ambiguous)."""
        segments = key.split(":")[1:] or key.split(":")
        index = self.suffix_index()
        for start in range(len(segments)):
            hits = index.get("/".join(segments[start:]) + ".html.twig", [])
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return None
        return None

    # ------------------------------------------------------------------------------ hooks
    def hook_table(self) -> tuple[dict[str, list[Hookable]], dict[str, set[str]]]:
        """hook name -> hookables, and template path -> the hooks it is wired under (all wiring files)."""
        hooks: dict[str, list[Hookable]] = {}
        wired_in: dict[str, set[str]] = {}
        for path in sorted(self.wiring):
            for h in self.wiring[path].hookables:
                hooks.setdefault(h.hook, []).append(h)
                if h.template:
                    target = self.resolve_name(h.template, h.file)[0]
                    if target:
                        wired_in.setdefault(target, set()).add(h.hook)
        return dict(sorted(hooks.items())), wired_in

    def _defining_files(self, hooks: dict[str, list[Hookable]]) -> dict[str, list[str]]:
        defining: dict[str, set[str]] = {}
        for path in sorted(self.wiring):
            for h in self.wiring[path].hookables:
                defining.setdefault(h.hook, set()).add(path)
        for name in hooks:
            defining.setdefault(name, set())
        return {k: sorted(v) for k, v in defining.items()}

    def _env_for(self, chain: list[str]) -> dict[str, list[str]]:
        """Variables after the templates of `chain` (child first) ran their top-level `set`s in order."""
        env: dict[str, list[str]] = {}
        for tpl in chain:
            for name, expr in self.facts[tpl].sets if tpl in self.facts else []:
                value = eval_list(expr, env)
                if value is not None:
                    env[name] = value
        return env

    def _extends_chain(self, path: str) -> list[str]:
        chain, seen = [path], {path}
        while True:
            facts = self.facts.get(chain[-1])
            if facts is None or not facts.extends:
                return chain
            nxt = self.resolve_name(facts.extends[0], chain[-1])[0]
            if not nxt or nxt in seen:
                return chain
            chain.append(nxt)
            seen.add(nxt)

    @staticmethod
    def _names(call: HookCall, env: Mapping[str, list[str]], wired: set[str]) -> list[str] | None:
        """Hook names a call can mean in this context, or None when its name is computed."""
        args = eval_list(call.expr, env)
        if args is None:
            return None
        prefixes = (eval_list(call.prefixes_expr, env) if call.prefixes_expr else None) or []
        out: dict[str, None] = {}
        for arg in args:
            out[arg] = None
            for base in [*sorted(wired), *prefixes]:
                stem = _strip_variant(base)
                out[f"{stem}{arg}" if arg.startswith("#") else f"{stem}.{arg}"] = None
        return list(out)

    def resolve_hooks(self) -> None:
        hooks, wired_in = self.hook_table()
        defining = self._defining_files(hooks)
        for path in sorted(p for p in self.wiring if self._emits(p)):     # context wiring only feeds the tables
            for h in self.wiring[path].hookables:
                self.n["hookables"] += 1
                self._wire(h)
            for key in self.wiring[path].config_keys:
                self.key_uses.append(KeyUse(file=path, key=key))
        resolved: set[tuple[str, int]] = set()
        dynamic: set[tuple[str, int]] = set()

        def connect(src: str, names: Sequence[str], via_calls: tuple[str, int] | None) -> None:
            for nm in names:
                for yaml_file in defining.get(nm, ()):
                    if via_calls:
                        resolved.add(via_calls)
                    self._add_edge("RENDERS", src, yaml_file, (), f"hook:{nm}")

        for path in sorted(self.facts):
            chain = self._extends_chain(path)
            env = self._env_for(chain)
            for i, call in enumerate(self.facts[path].hooks):
                names = self._names(call, env, wired_in.get(path, set()))
                if names is None:
                    dynamic.add((path, i))
                    continue
                connect(path, names, (path, i))
            # hooks of the layouts this page extends that exist only because of the page's own prefixes
            for ancestor in chain[1:]:
                base_env = self._env_for(self._extends_chain(ancestor))
                for i, call in enumerate(self.facts[ancestor].hooks if ancestor in self.facts else ()):
                    if call.prefixes_expr is None:
                        continue
                    wired = wired_in.get(ancestor, set())
                    ours = self._names(call, env, wired) or []
                    theirs = set(self._names(call, base_env, wired) or [])
                    connect(path, [n for n in ours if n not in theirs], (ancestor, i))
        for path, facts in sorted(self.facts.items()):
            for i, call in enumerate(facts.hooks):
                self.n["hook_calls"] += 1
                if (path, i) in resolved:
                    self.n["hook_calls_resolved"] += 1
                elif (path, i) in dynamic:
                    self.n["hook_calls_dynamic"] += 1
                else:
                    self.n["hook_calls_unresolved"] += 1
                    if len(self.hook_unresolved) < _UNLISTED_CAP:
                        self.hook_unresolved.append({"file": path, "hook": call.expr})
        self.hook_names = len(hooks)

    def _wire(self, h: Hookable) -> None:
        if h.template:
            self.n["hookables_with_template"] += 1
            path, how = self.resolve_name(h.template, h.file)
            if path:
                self.n["hookables_resolved"] += 1
                self._add_edge("RENDERS", h.file, path, (), f"hookable:{h.hook}/{h.name}")
            elif how == "external":
                self.externals.add(h.template)
                self._add_edge("IMPORTS", h.file, h.template, (), h.template)
            else:
                self.n["hookables_unresolved"] += 1
                self.reasons["hookable: template missing"] += 1
        elif h.component:
            self.n["hookables_component_only"] += 1
            target = self._component_by_name(h.component)
            if target:
                self.n["hookables_resolved"] += 1
                self._add_edge("RENDERS", h.file, target, (), f"component:{h.component}")
            else:
                self.n["hookables_unresolved"] += 1
                self.reasons["hookable: component template is in PHP"] += 1

    # ------------------------------------------------------------------------------- routes
    def routes(self) -> list[RouteDef]:
        mounted: set[str] = set()
        mounts: dict[str, list[tuple[str, str]]] = {}
        for path, doc in sorted(self.docs.items()):
            for key in sorted(doc, key=str):
                entry = doc[key]
                if isinstance(entry, Mapping) and isinstance(entry.get("resource"), str):
                    target = self._resource_path(entry["resource"], path)
                    if target:
                        mounted.add(target)
                        mounts.setdefault(path, []).append((target, str(entry.get("prefix", ""))))
        out: dict[tuple[str, str], RouteDef] = {}

        def walk(path: str, prefix: str, depth: int) -> None:
            doc = self.docs.get(path)
            if doc is None or depth > 8:
                return
            for key in sorted(doc, key=str):
                entry = doc[key]
                if not isinstance(entry, Mapping):
                    continue
                if isinstance(entry.get("resource"), str):
                    continue
                if isinstance(entry.get("path"), str):
                    self._add_route(out, str(key), entry, _join(prefix, entry["path"]), path)
            for target, sub in mounts.get(path, ()):
                walk(target, _join(prefix, sub), depth + 1)

        for path in sorted(self.docs):
            if path not in mounted and (path in mounts or self._has_paths(self.docs[path])):
                walk(path, "", 0)
        return sorted(out.values(), key=lambda r: (r.pattern, r.file))

    @staticmethod
    def _has_paths(doc: Mapping[str, Any]) -> bool:
        return any(isinstance(v, Mapping) and isinstance(v.get("path"), str) for v in doc.values())

    def _resource_path(self, resource: str, importer: str) -> str | None:
        """`@SyliusShopBundle/Resources/config/routing.yml` -> the indexed file (bundle dir by suffix)."""
        if resource.startswith("@"):
            bundle, _, rest = resource[1:].partition("/")
            names = [bundle, re.sub(r"^[A-Z][a-z0-9]+", "", bundle)]
            for name in names:
                hits = sorted(p for p in self.docs if p.endswith(f"/{name}/{rest}"))
                if hits:
                    return hits[0]
            return None
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(importer), resource))
        return candidate if candidate in self.docs else None

    def _template_by_route_name(self, name: str) -> str | None:
        """`sylius_shop_contact_request` -> the unique template `contact_request.html.twig` (the controller
        renders it, so the YAML names none): longest remaining name first, never ambiguous."""
        parts = name.split("_")
        index = self.suffix_index()
        for start in range(1, len(parts) - 1):
            hits = index.get("_".join(parts[start:]) + ".html.twig", [])
            if len(hits) > 1:       # `contact/contact_request` and `email/contact_request`: the route's own dir
                hits = [h for h in hits if PurePosixPath(h).parent.name in parts]
            if len(hits) == 1:
                return hits[0]
            if hits:
                return None
        return None

    def _add_route(self, out: dict[tuple[str, str], RouteDef], name: str, entry: Mapping[str, Any],
                   path: str, importer: str) -> None:
        templates = route_templates(entry)
        self.n["route_definitions"] += 1
        file: str | None = None
        if templates:
            file, _how = self.resolve_name(templates[0], importer)
            if not file:
                self.n["routes_template_missing"] += 1
                return
        else:
            file = self._template_by_route_name(name)
            if file:
                self.n["routes_template_by_name"] += 1
        if not file:
            self.n["routes_without_template"] += 1
            return
        chain = self._extends_chain(file)
        layout = chain[-1] if len(chain) > 1 else None
        patterns = [_route_pattern(path)]
        reqs = entry.get("requirements") if isinstance(entry.get("requirements"), Mapping) else {}
        if any(isinstance(v, str) and v.startswith((".+", ".*")) for v in reqs.values()):
            patterns.append(re.sub(r":(\w+)$", r":\1(.*)", patterns[0].rstrip("/")))
        for pattern in patterns:
            out[(pattern, file)] = RouteDef(pattern=pattern, file=file, layout=layout)

    # ------------------------------------------------------------------------------- i18n
    def i18n(self) -> tuple[dict[str, str], list[KeyUse]]:
        uses: set[tuple[str, str, bool]] = {(u.file, u.key, u.dynamic) for u in self.key_uses}
        catalogue_keys = self.catalogue.keys()
        static_distinct: set[str] = set()
        for path, facts in sorted(self.facts.items()):
            if self._kind_of(path) in (FileClass.TEST, FileClass.GENERATED):
                continue
            found = set(facts.keys)
            if self.catalogue:
                found |= {s for s in facts.string_pool if s in catalogue_keys}
            self.n["key_call_sites"] += len(facts.keys) + len(facts.key_prefixes) + facts.key_unresolved
            self.n["key_calls_unresolved"] += facts.key_unresolved
            for key in sorted(found):
                uses.add((path, key, False))
                static_distinct.add(key)
            for prefix in facts.key_prefixes:
                uses.add((path, prefix, True))
        static_distinct |= {u.key for u in self.key_uses}
        values = Counter(self.catalogue.values())
        self.extra = {
            "i18n_keys": len(self.catalogue), "i18n_distinct_values": len(values),
            "i18n_values_shared_by_keys": sum(1 for n in values.values() if n > 1),
            "keys_used_distinct": len(static_distinct),
            "keys_used_in_locale": len(static_distinct & catalogue_keys),
            "keys_used_not_in_locale": len(static_distinct - catalogue_keys),
            "dynamic_key_uses": sum(1 for _, _, dyn in uses if dyn),
            "key_detection": "syntactic+catalogue" if self.catalogue else "syntactic",
        }
        self.n["key_uses"] = len(uses)
        return dict(sorted(self.catalogue.items())), [KeyUse(file=f, key=k, dynamic=d) for f, k, d in sorted(uses)]

    def literals(self) -> list[TextLiteral]:
        out: set[tuple[str, str]] = set()
        for path, facts in self.facts.items():
            if self._kind_of(path) in (FileClass.TEST, FileClass.GENERATED):
                continue
            out.update((path, t) for t in facts.texts)
            out.update((path, t) for t in facts.attr_texts)
        self.extra["text_literals"] = len(out)
        return [TextLiteral(file=f, text=t) for f, t in sorted(out)]

    # ------------------------------------------------------------------------------ output
    def files(self) -> list[CodeFile]:
        out = [CodeFile(path=p, kind=self._kind_of(p), loc=_loc(self.texts[p]), sha=self.sha[p],
                        package=_bundle_of(p)) for p in sorted(self.texts) if self._emits(p)]
        for name in sorted(self.externals):
            ns = name.split("/", 1)[0]
            out.append(CodeFile(path=name, kind=FileClass.EXTERNAL, package=ns))
        return sorted(out, key=lambda f: f.path)

    def edge_list(self) -> list[CodeEdge]:
        return [CodeEdge(src=src, dst=dst, kind=kind, symbols=sorted(symbols), via=via)
                for (kind, src, dst), (symbols, via) in sorted(self.edges.items())]

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
        roots = [{"root": r.root, "status": r.status, "files": r.files} for r in self.tree.roots]
        refs = self.n["template_refs"]
        good = self.n["template_refs_resolved"] + self.n["template_refs_external"]
        hook_calls = self.n["hook_calls"]
        return {
            **self.extra, **self._problem_stats(),
            "code_roots": roots, "configured_files": dict(self.tree.configured),
            "index_empty": sum(r["files"] for r in roots) == 0,
            "files_by_class": dict(sorted(by_class.items())),
            "files_total": len(files), "files_in_tree": len(self.tree.paths),
            "twig_files": sum(1 for f in files if f.path.endswith(".twig")),
            "wiring_files": len(self.wiring),
            "app_source_files": sum(1 for p in self.tree.paths if p.endswith(".twig")),
            "imports": sum(1 for e in edges if e.kind == "IMPORTS" and e.dst not in self.externals),
            "imports_external": sum(1 for e in edges if e.kind == "IMPORTS" and e.dst in self.externals),
            "renders": sum(e.kind == "RENDERS" for e in edges),
            "self_references": self.n["self_references"],
            "template_refs": refs, "template_refs_resolved": self.n["template_refs_resolved"],
            "template_refs_external": self.n["template_refs_external"],
            "template_refs_unresolved": self.n["template_refs_unresolved"],
            "dynamic_template_names": self.n["dynamic_template_names"],
            "resolved_ref_rate": round(good / refs, 4) if refs else None,
            "hook_names": self.hook_names, "hookables": self.n["hookables"],
            "hookables_with_template": self.n["hookables_with_template"],
            "hookables_component_only": self.n["hookables_component_only"],
            "hookables_resolved": self.n["hookables_resolved"],
            "hookables_unresolved": self.n["hookables_unresolved"],
            "hook_calls": hook_calls, "hook_calls_resolved": self.n["hook_calls_resolved"],
            "hook_calls_unresolved": self.n["hook_calls_unresolved"],
            "hook_calls_dynamic": self.n["hook_calls_dynamic"],
            "resolved_hook_rate": round(self.n["hook_calls_resolved"] / hook_calls, 4) if hook_calls else None,
            "unresolved_hooks_sample": self.hook_unresolved,
            "component_calls": self.n["component_calls"],
            "component_calls_literal_template": self.n["component_calls_literal_template"],
            "component_calls_by_name": self.n["component_calls_by_name"],
            "component_calls_unresolved": self.n["component_calls_unresolved"],
            "route_definitions": self.n["route_definitions"], "routes": len(self.routes_out),
            "routes_without_template": self.n["routes_without_template"],
            "routes_template_by_name": self.n["routes_template_by_name"],
            "routes_template_missing": self.n["routes_template_missing"],
            "unresolved_samples": sorted(self.unresolved, key=lambda d: (d["file"], d["name"], d["reason"])),
            "unresolved_reasons": dict(sorted(self.reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
            "key_uses": self.n["key_uses"], "key_call_sites": self.n["key_call_sites"],
            "key_calls_unresolved": self.n["key_calls_unresolved"],
            "global_files": sorted(g for g in self.cfg.global_files if g in self.kind or g in self.texts),
            "hub_importers": {}, "hub_package_roots": {},
        }

    def _pipeline(self) -> tuple[dict[str, str], list[KeyUse], list[TextLiteral], list[RouteDef]]:
        self.load()
        self.routes_out = self.routes()
        self.route_files = {r.file for r in self.routes_out}
        self.resolve_structure()
        self.resolve_hooks()
        messages, key_uses = self.i18n()
        return messages, key_uses, self.literals(), self.routes_out

    def run(self, snapshot_id: str, target: str, ref: str) -> CodeIndex:
        messages, key_uses, literals, routes = self._pipeline()
        files, edges = self.files(), self.edge_list()
        return CodeIndex(target=target, repo=self.cfg.repo, ref=ref, snapshot_id=snapshot_id, adapter="twig",
                         files=files, edges=edges, routes=routes, i18n=messages, key_uses=key_uses,
                         literals=literals, stats=self.stats(files, edges))

    def part(self, dangling: Sequence[CodeEdge]) -> IndexPart:
        messages, key_uses, literals, routes = self._pipeline()
        # The head's table only when a catalogue changed and every catalogue the head has was read. A head that
        # is behind its base lacks keys the base gained since (case 41): rebuilding for a template-only PR would
        # drop them, so then the overlay keeps the base's table.
        complete = any(p in self.texts and self._emits(p) for p in self.cfg.locale_files) and all(
            p in self.texts for p in self.cfg.locale_files if p in self.universe)
        moved: list[CodeEdge] = []
        dropped: list[CodeEdge] = []
        for edge in dangling:
            path = self.resolve_name(edge.via, edge.src)[0] if edge.via and edge.via.endswith(".twig") else None
            if path and path not in (edge.dst, edge.src):
                moved.append(edge.model_copy(update={"dst": path}))
            else:
                dropped.append(edge)
        return IndexPart(
            files=self.files(), edges=self.edge_list(), routes=[r for r in routes if self._emits(r.file)],
            i18n=messages if complete else None, key_uses=key_uses, literals=literals,
            issues={f: sorted(k) for f, k in sorted(self.issues.items()) if self._emits(f)},
            redirected=moved, dropped=dropped, dts_entries=None,
            tag_pairs=self.n["template_refs"], tag_pairs_unresolved=self.n["template_refs_unresolved"],
            symlinks=self.tree.symlinks, oversize=self.tree.oversize)
