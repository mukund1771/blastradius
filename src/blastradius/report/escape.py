"""Neutralising untrusted text for the two output formats (case 57; case 55 for the PR title).

Every dynamic string in a report (PR title, element names, spec quotes, file paths, evidence summaries) came from
a fork author, a web page or a document. `clean` is the format-independent step; `md_text`/`md_code` make a
string inert in Markdown (no emphasis, links, images, tables, HTML, math or autolinks can be formed from it) and
`html_text` makes it inert in HTML. URLs are defanged (`https://` becomes `hxxps://`) so that neither format
contains a live or autolinkable address: that covers `scheme://` URLs, `www.` hosts, `mailto:` and `xmpp:` and
bare e-mail addresses, which GitHub's Markdown links without any markup.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

from ..safety import clean_untrusted

_WS = re.compile(r"\s+")
_SCHEME = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,15}):(?=//)")
_MAIL_SCHEME = re.compile(r"(?i)\b(mailto|xmpp):")
# GFM links `local@domain.tld` (the domain needs a period) on its own, so the "@" of one is broken: `a[@]b.example`
_EMAIL_AT = re.compile(r"(?<=[\w.+-])@(?=[\w-]+\.\w)")
# a "www." host links after whitespace or any of `*_~(`, and `_` is a word character, so `\b` would miss `_www.`
_WWW = re.compile(r"(?i)(?<![A-Za-z0-9])www\.")
# the defanged spellings map to themselves, so cleaning an already clean string changes nothing
_DEFANG = {"http": "hxxp:", "https": "hxxps:", "ftp": "fxp:", "hxxp": "hxxp:", "hxxps": "hxxps:", "fxp": "fxp:"}
_MD_PUNCT = re.compile(r"([\\`*\[\]<|~$])")
_MD_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")
_MD_ENTITY = re.compile(r"&(?=#?\w+;)")
_MD_LINE_START = re.compile(r"^(?:([#>+=-])|(\d+)([.)]))")


def defang_urls(text: str) -> str:
    text = _EMAIL_AT.sub("[@]", text)
    text = _MAIL_SCHEME.sub(r"\1[:]", text)
    text = _SCHEME.sub(lambda m: _DEFANG.get(m.group(1).lower(), m.group(1) + "[:]"), text)
    return _WWW.sub("www[.]", text)


def _drop_invisible(text: str) -> str:
    """Remove control (Cc) and format (Cf) characters; whitespace stays so that words keep their gaps.

    `safety.clean_untrusted` removes a fixed list that stops short of the bidi isolates U+2066-2069, U+061C,
    U+180E, the soft hyphen, the C1 controls (U+009B is a terminal's CSI) and the tag characters
    U+E0000-E007F. Every one of them is invisible or reorders what is visible, and none carries text a reader
    needs, so the whole category goes (asked of `unicodedata`, so the list cannot drift from the Unicode data).
    """
    if text.isprintable():
        return text
    return "".join(c for c in text if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf"))


def clean(value: Any) -> str:
    """One line of inert text: no hidden comments, images, control or bidi characters, no live URLs."""
    text = _drop_invisible(clean_untrusted("" if value is None else str(value)))
    return defang_urls(_WS.sub(" ", text).strip())


def md_text(value: Any) -> str:
    """Markdown-safe inline text (also safe inside a table cell)."""
    text = _MD_ENTITY.sub("&amp;", clean(value))
    text = _MD_PUNCT.sub(r"\\\1", text)
    text = _MD_UNDERSCORE.sub(r"\\_", text)
    return _MD_LINE_START.sub(lambda m: "\\" + m.group(1) if m.group(1) else m.group(2) + "\\" + m.group(3), text)


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
