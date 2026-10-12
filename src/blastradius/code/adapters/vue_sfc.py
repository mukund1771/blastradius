"""Vue single-file-component scanning without the Vue compiler (D10; cases 33, 35).

`split_sfc` cuts a `.vue` file into top-level blocks (template, script, style, custom). Nested
`<template v-if>` tags are balanced by the same quote-aware tokenizer that `scan_template` uses, so a `>`
inside an attribute value or a `<` inside `{{ }}` cannot end a block early.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

_NAME = re.compile(r"[A-Za-z][^\s/>=<\"']*")
_ATTR_NAME = re.compile(r"[^\s\"'<>/=]+")
_WS = re.compile(r"\s+")
_VISIBLE_ATTRS = frozenset({"title", "placeholder", "aria-label", "label", "alt"})


@dataclass(frozen=True)
class Block:
    tag: str
    attrs: dict[str, str | None]
    content: str

    @property
    def lang(self) -> str | None:
        return self.attrs.get("lang")


@dataclass(frozen=True)
class Token:
    kind: str                   # open | close | text | interp
    value: str                  # tag name, text, or expression
    attrs: tuple[tuple[str, str | None], ...] = ()
    self_closing: bool = False


def _skip_ws(src: str, i: int) -> int:
    n = len(src)
    while i < n and src[i].isspace():
        i += 1
    return i


def _read_open_tag(src: str, i: int) -> tuple[tuple[tuple[str, str | None], ...], bool, int]:
    """Attributes, self-closing flag and the offset just past `>`, starting right after the tag name."""
    attrs: list[tuple[str, str | None]] = []
    n = len(src)
    while i < n:
        i = _skip_ws(src, i)
        if i >= n:
            break
        if src[i] == ">":
            return tuple(attrs), False, i + 1
        if src.startswith("/>", i):
            return tuple(attrs), True, i + 2
        m = _ATTR_NAME.match(src, i)
        if m is None:           # stray character inside a tag: skip it rather than loop forever
            i += 1
            continue
        name, i = m.group(0), m.end()
        j = _skip_ws(src, i)
        value: str | None = None
        if j < n and src[j] == "=":
            j = _skip_ws(src, j + 1)
            if j < n and src[j] in "\"'":
                end = src.find(src[j], j + 1)
                end = n if end == -1 else end
                value, i = src[j + 1:end], end + 1
            else:
                k = j
                while k < n and not src[k].isspace() and src[k] != ">":
                    if src.startswith("/>", k):
                        break
                    k += 1
                value, i = src[j:k], k
        attrs.append((name, value))
    return tuple(attrs), False, n


def tokenize(src: str, pos: int = 0) -> Iterator[tuple[Token, int]]:
    """Yield `(token, end_offset)` for the HTML-ish template language: tags, text, and `{{ }}` interpolations."""
    n = len(src)
    i = pos
    text_start = i
    while i < n:
        c = src[i]
        if src.startswith("{{", i):
            if text_start < i:
                yield Token("text", src[text_start:i]), i
            end = src.find("}}", i + 2)
            end = n if end == -1 else end
            yield Token("interp", src[i + 2:end]), min(end + 2, n)
            i = text_start = min(end + 2, n)
        elif c == "<" and src.startswith("<!--", i):
            if text_start < i:
                yield Token("text", src[text_start:i]), i
            end = src.find("-->", i + 4)
            i = text_start = n if end == -1 else end + 3
        elif c == "<" and i + 1 < n and src[i + 1] == "/" and _NAME.match(src, i + 2):
            if text_start < i:
                yield Token("text", src[text_start:i]), i
            m = _NAME.match(src, i + 2)
            assert m is not None
            end = src.find(">", m.end())
            i = text_start = n if end == -1 else end + 1
            yield Token("close", m.group(0)), i
        elif c == "<" and i + 1 < n and _NAME.match(src, i + 1):
            if text_start < i:
                yield Token("text", src[text_start:i]), i
            m = _NAME.match(src, i + 1)
            assert m is not None
            attrs, self_closing, i = _read_open_tag(src, m.end())
            text_start = i
            yield Token("open", m.group(0), attrs, self_closing), i
        else:
            i += 1
    if text_start < n:
        yield Token("text", src[text_start:n]), n


def split_sfc(source: str) -> list[Block]:
    """Top-level blocks in document order. Unterminated blocks run to the end of the file."""
    blocks: list[Block] = []
    n = len(source)
    i = 0
    while i < n:
        j = source.find("<", i)
        if j == -1:
            break
        if source.startswith("<!--", j):
            end = source.find("-->", j + 4)
            i = n if end == -1 else end + 3
            continue
        m = _NAME.match(source, j + 1)
        if m is None:
            i = j + 1
            continue
        tag = m.group(0)
        attrs, self_closing, body = _read_open_tag(source, m.end())
        if self_closing:
            blocks.append(Block(tag, dict(attrs), ""))
            i = body
            continue
        if tag == "template":
            depth, end, after = 1, n, n
            for tok, stop in tokenize(source, body):
                if tok.kind == "open" and tok.value == "template" and not tok.self_closing:
                    depth += 1
                elif tok.kind == "close" and tok.value == "template":
                    depth -= 1
                    if depth == 0:
                        end, after = source.rfind("</", body, stop), stop
                        break
            blocks.append(Block(tag, dict(attrs), source[body:end]))
            i = after
        else:
            close = re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE).search(source, body)
            end = close.start() if close else n
            blocks.append(Block(tag, dict(attrs), source[body:end]))
            i = close.end() if close else n
    return blocks


@dataclass
class TemplateScan:
    tags: list[str] = field(default_factory=list)          # component tag names as written (first-seen order)
    dynamic_is: list[str] = field(default_factory=list)    # expressions of <component :is="expr">
    expressions: list[str] = field(default_factory=list)   # interpolations and bound attribute values
    texts: list[str] = field(default_factory=list)         # hardcoded visible text nodes
    attr_texts: list[str] = field(default_factory=list)    # hardcoded title/placeholder/aria-label/label/alt


_LITERAL_IS = re.compile(r"""^\s*(['"])([A-Za-z][\w-]*)\1\s*$""")
_RAW_ELEMENTS = frozenset({"script", "style"})


def _normalise_text(text: str) -> str:
    return _WS.sub(" ", html.unescape(text)).strip()


def _is_visible_text(text: str) -> bool:
    """Words, not glyphs or single-key hints: at least two letters."""
    return sum(ch.isalpha() for ch in text) >= 2


def scan_template(template: str, *, is_native: frozenset[str]) -> TemplateScan:
    """Collect component tags, `<component :is>` holes, expression text and hardcoded visible text.

    `is_native` holds HTML/SVG element names in their exact case (`Button` is a component, `button` is not);
    everything else is a component candidate.
    `<template>`, `<slot>` and `<component>` are structural and never reported as tags.
    """
    scan = TemplateScan()
    seen: set[str] = set()
    raw_depth = 0
    for tok, _ in tokenize(template):
        if tok.kind == "interp":
            scan.expressions.append(tok.value)
        elif tok.kind == "text":
            if raw_depth == 0:
                text = _normalise_text(tok.value)
                if _is_visible_text(text):
                    scan.texts.append(text)
        elif tok.kind == "close":
            if tok.value in _RAW_ELEMENTS and raw_depth:
                raw_depth -= 1
        else:
            name = tok.value
            if name in _RAW_ELEMENTS and not tok.self_closing:
                raw_depth += 1
            is_expr: str | None = None
            for key, value in tok.attrs:
                if value is None:
                    continue
                if key in (":is", "v-bind:is") and name == "component":
                    is_expr = value
                if key.startswith((":", "@", "v-", "#")) or key == "is":
                    scan.expressions.append(value)
                elif key in _VISIBLE_ATTRS:
                    text = _normalise_text(value)
                    if _is_visible_text(text):
                        scan.attr_texts.append(text)
            if name == "component":
                static_is = next((v for k, v in tok.attrs if k == "is" and v), None)
                literal = _LITERAL_IS.match(is_expr) if is_expr is not None else None
                if literal:
                    name = literal.group(2)
                elif static_is and re.fullmatch(r"[A-Za-z][\w-]*", static_is):
                    name = static_is
                else:
                    scan.dynamic_is.append(is_expr if is_expr is not None else "")
                    continue
            elif name == "template" or name in is_native:
                continue
            if name not in seen:
                seen.add(name)
                scan.tags.append(name)
    return scan
