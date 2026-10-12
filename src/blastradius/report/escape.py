"""Neutralising untrusted text for the two output formats (case 57; case 55 for the PR title).

Every dynamic string in a report (PR title, element names, spec quotes, file paths, evidence summaries) came from
a fork author, a web page or a document. `clean` is the format-independent step; `md_text`/`md_code` make a
string inert in Markdown (no emphasis, links, images, tables, HTML, math or autolinks can be formed from it) and
`html_text` makes it inert in HTML. URLs are defanged (`https://` becomes `hxxps://`) so that neither format
contains a live or autolinkable address.
"""

from __future__ import annotations

import html
import re
from typing import Any

from ..safety import clean_untrusted

_WS = re.compile(r"\s+")
_SCHEME = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,15}):(?=//)")
_WWW = re.compile(r"(?i)\bwww\.")
# the defanged spellings map to themselves, so cleaning an already clean string changes nothing
_DEFANG = {"http": "hxxp:", "https": "hxxps:", "ftp": "fxp:", "hxxp": "hxxp:", "hxxps": "hxxps:", "fxp": "fxp:"}
_MD_PUNCT = re.compile(r"([\\`*\[\]<|~$])")
_MD_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")
_MD_ENTITY = re.compile(r"&(?=#?\w+;)")
_MD_LINE_START = re.compile(r"^(?:([#>+=-])|(\d+)([.)]))")


def defang_urls(text: str) -> str:
    text = _SCHEME.sub(lambda m: _DEFANG.get(m.group(1).lower(), m.group(1) + "[:]"), text)
    return _WWW.sub("www[.]", text)


def clean(value: Any) -> str:
    """One line of inert text: no hidden comments, images, control or bidi characters, no live URLs."""
    text = clean_untrusted("" if value is None else str(value))
    return defang_urls(_WS.sub(" ", text).strip())


def md_text(value: Any) -> str:
    """Markdown-safe inline text (also safe inside a table cell)."""
    text = _MD_ENTITY.sub("&amp;", clean(value))
    text = _MD_PUNCT.sub(r"\\\1", text)
    text = _MD_UNDERSCORE.sub(r"\\_", text)
    return _MD_LINE_START.sub(lambda m: "\\" + m.group(0) if m.group(1) else m.group(2) + "\\" + m.group(3), text)


def md_code(value: Any) -> str:
    """An inline code span whose fence is longer than any backtick run inside (never a table cell)."""
    text = clean(value)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def html_text(value: Any) -> str:
    """Inert text for an element body (angle brackets and ampersands escaped)."""
    return html.escape(clean(value), quote=False)


def html_attr(value: Any) -> str:
    """Inert text for a double-quoted attribute value."""
    return html.escape(clean(value), quote=True)


def sentence(text: str) -> str:
    """Ends a sentence with a full stop unless it already ends with terminal punctuation."""
    text = text.strip()
    return text if not text or text[-1] in ".!?:" else text + "."
