"""Spec-text normalisation: the single function behind the quote check (D07, D30; case 25).

A requirement quote is verified by code, never by the model: `normalize(quote)` must be a substring of
`normalize(page)`. Both sides go through the same function, so markup, whitespace, curly quotes and dashes
cannot make a true quote fail, and an invented quote cannot pass.

Two forms, one pipeline:
  render(text)     what the model reads. MDX/HTML component tags are removed but their inner text, their
                   `title` attribute and their qualifier label are kept (`<Warning>x</Warning>` becomes
                   `Warning: x`, `<Tab title="Desktop">` becomes `Desktop: `), so "only available in the
                   Desktop App" stays visible to edition detection. Links, emphasis and backticks are dropped,
                   fenced code becomes a marker (requirements are prose, not samples), headings and line
                   structure are kept.
  normalize(text)  comparison form: render() with line-start markers (#, -, 1.) and table pipes dropped and
                   every whitespace run collapsed to one space.

Both are idempotent (`render(render(x)) == render(x)`), which is what lets the model quote the rendered text.
"""

from __future__ import annotations

import html
import re
import unicodedata

from ..ids import sha_full
from ..safety import clean_untrusted

CODE_MARK = "[code sample]"

# MDX callouts keep a label so the qualifier survives; other components keep their title attribute only.
_CALLOUT_LABEL = {"Note": "Note", "Tip": "Tip", "Warning": "Warning", "Info": "Info", "Danger": "Danger",
                  "Check": "Check", "Callout": "Note"}
_COMPONENTS = frozenset({"Card", "CardGroup", "Columns", "Tabs", "Tab", "Steps", "Step", "Accordion",
                         "AccordionGroup", "Expandable", "Frame", "CodeGroup", "Icon", "Tooltip", "Badge",
                         "Update", "Panel", "Tile", "View", "ParamField", "ResponseField", *_CALLOUT_LABEL})
# Formatting HTML only: unknown lowercase tags (<base-url>, <testsuite>) are content and are left alone.
_BLOCK_HTML = frozenset({"br", "p", "div", "li", "ul", "ol", "tr", "hr", "table", "details", "summary",
                         "section", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"})
_INLINE_HTML = frozenset({"a", "b", "strong", "i", "em", "u", "ins", "del", "s", "kbd", "code", "span", "sub",
                          "sup", "small", "mark", "abbr", "center", "img", "td", "th", "thead", "tbody"})

_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
})

# Spec pages are untrusted input, so every pattern is bounded or linear: no unbounded lazy scans that
# restart at each candidate (a page of repeated `[a](` or `<!--` must not stall ingestion).
_FENCE_OPEN = re.compile(r"[ \t]*(`{3,}|~{3,})")
_IMG_MD = re.compile(r"!\[[^\]\n]{0,300}\]\([^)\n]{0,500}\)")
_MDX_COMMENT = re.compile(r"\{/\*.{0,5000}?\*/\}", re.DOTALL)
_TAG = re.compile(r"""<(/?)([A-Za-z][A-Za-z0-9]*)((?:\s(?:[^<>"']|"[^"]{0,400}"|'[^']{0,400}')*)?)/?>""")
_TITLE_ATTR = re.compile(r"""\b(?:title|caption|label)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_LINK = re.compile(r"""\[([^\]\n]{1,500})\]\(\s*[^)\s]{0,500}(?:\s+"[^"\n]{0,200}")?\s*\)""")
_AUTOLINK = re.compile(r"<(https?://[^>\s]{1,2000})>")
_BOLD = re.compile(r"\*\*(?=\S)([^\n]+?)(?<=\S)\*\*")
_BOLD_U = re.compile(r"(?<!\w)__(?=\S)([^_\n]+?)(?<=\S)__(?!\w)")
_ITALIC = re.compile(r"(?<![\w*])\*(?![\s*])([^*\n]+?)(?<![\s*])\*(?![\w*])")
_CODE_SPAN = re.compile(r"`([^`\n]*)`")
# A callout/tab label joins the plain text it qualifies; before a heading, list or table it stays alone.
_LABEL_JOIN = re.compile(r"\x00\s*+(?![#|>*+-]|\d+[.)][ \t])")
_LINE_MARK = re.compile(r"^(?:[ \t]*(?:#{1,6}|[*+-]|\d+[.)]|>)[ \t]+)+", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Balanced fenced code blocks become CODE_MARK; an unclosed fence stays as text and swallows nothing."""
    lines = text.split("\n")
    out: list[str] = []
    unclosed: set[str] = set()      # fence strings with no closer after them: later openers cannot have one
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if m and m.group(1) not in unclosed:
            fence = m.group(1)
            close = next((k for k in range(i + 1, len(lines)) if lines[k].strip() == fence), None)
            if close is not None:
                out.append(CODE_MARK)
                i = close + 1
                continue
            unclosed.add(fence)
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _strip_html_comments(text: str) -> str:
    """Remove `<!-- ... -->` in one linear pass; an unclosed opener is dropped, its text stays visible."""
    parts: list[str] = []
    pos = 0
    while (start := text.find("<!--", pos)) >= 0:
        parts.append(text[pos:start])
        end = text.find("-->", start + 4)
        if end < 0:
            pos = start + 4
            continue
        pos = end + 3
    parts.append(text[pos:])
    return "".join(parts)


def _tag(m: re.Match[str]) -> str:
    closing, name, attrs = m.group(1), m.group(2), m.group(3) or ""
    if name in _COMPONENTS:
        if closing:
            return "\n"
        label = _CALLOUT_LABEL.get(name)
        if label is None:
            found = _TITLE_ATTR.search(attrs)
            label = (found.group(1) or found.group(2)).strip() if found else ""
        return f"\n{label}:\x00" if label else "\n"
    low = name.lower()
    if low in _BLOCK_HTML:
        return "\n"
    if low in _INLINE_HTML:
        return ""
    return m.group(0)


def _is_rule(line: str) -> bool:
    """Horizontal rules (`---`, `***`) and table separator rows (`|---|:--|`)."""
    s = line.replace(" ", "").replace("\t", "")
    if len(s) >= 3 and len(set(s)) == 1 and s[0] in "-*_":
        return True
    return "-" in s and set(s) <= set("|-:")


def render(text: str) -> str:
    """Markup-free text that keeps headings, line structure and qualifier labels (the model's view)."""
    text = unicodedata.normalize("NFKC", text.replace("\r\n", "\n").replace("\r", "\n")).translate(_PUNCT)
    text = _strip_fences(text)
    text = _IMG_MD.sub("", text).replace("![", "[")
    text = _MDX_COMMENT.sub("", _strip_html_comments(text))
    text = clean_untrusted(text)    # control and zero-width chars: what the LLM never sees
    text = html.unescape(text)
    text = _TAG.sub(_tag, text)
    text = _LABEL_JOIN.sub(" ", text).replace("\x00", "\n")
    text = _LINK.sub(r"\1", text)
    text = _AUTOLINK.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    text = _BOLD_U.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _CODE_SPAN.sub(r"\1", text)
    text = text.replace("**", "").replace("`", "")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join("" if _is_rule(line) else line for line in lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize(text: str) -> str:
    """Comparison form used on both the source page and the model's quote."""
    return " ".join(_LINE_MARK.sub("", render(text)).replace("|", " ").split())


def quote_key(normalised_quote: str) -> str:
    """Dedupe key: hash of the normalised quote (case 26)."""
    return sha_full(normalised_quote)
