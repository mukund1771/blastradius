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

  flatten(rendered) the same comparison form for text that is already rendered (no second render pass).

Both are idempotent on every page of the real document (104 of 104) and on ordinary markup, which is what
lets the model quote the rendered text. Contrived input can still need a second pass (`!<b>[a]` loses its
tag, then reads as an image marker); such a quote simply fails the verbatim check, it is never accepted.
Invariant the quote check relies on: `render` output is a fixed point of `safety.clean_untrusted`, so the
text the model reads (`untrusted(chunk.text)`) is exactly the text the quote is checked against, including
for entity-encoded or re-formed comment openers (`&lt;!--`, `<<!---->!--`).
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
# restart at each candidate (a page of repeated `**a `, `[a](` or `<!--` must not stall ingestion). Emphasis
# spans longer than _SPAN are left to the final `**` / backtick sweep, which removes the markers anyway.
_SPAN = 500
_FENCE_OPEN = re.compile(r"[ \t]*(`{3,}|~{3,})")
# Possessive quantifiers: a run that does not end in the next delimiter cannot succeed by giving characters
# back, so the engine fails at once instead of retrying every shorter length (that was the bracket-soup cost).
_IMG_MD = re.compile(r"!\[[^\]\n]{0,300}+\]\([^)\n]{0,500}+\)")
_TAG = re.compile(r"""<(/?)([A-Za-z][A-Za-z0-9]*)((?:\s(?:[^<>"']|"[^"]{0,400}"|'[^']{0,400}')*)?)/?>""")
_TITLE_ATTR = re.compile(r"""\b(?:title|caption|label)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_LINK = re.compile(r"""\[([^\]\n]{1,300}+)\]\(\s*[^)\s]{0,500}+(?:\s+"[^"\n]{0,200}")?\s*\)""")
_AUTOLINK = re.compile(r"<(https?://[^>\s]{1,2000})>")
_BOLD = re.compile(rf"\*\*(?=\S)([^\n]{{1,{_SPAN}}}?)(?<=\S)\*\*")
_BOLD_U = re.compile(rf"(?<!\w)__(?=\S)([^_\n]{{1,{_SPAN}}}?)(?<=\S)__(?!\w)")
_ITALIC = re.compile(rf"(?<![\w*])\*(?![\s*])([^*\n]{{1,{_SPAN}}}?)(?<![\s*])\*(?![\w*])")
_CODE_SPAN = re.compile(rf"`([^`\n]{{0,{_SPAN}}})`")
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


def _strip_delimited(text: str, opener: str, closer: str, *, drop_unclosed: bool) -> str:
    """Remove `opener ... closer` spans in one linear pass that cannot be re-formed by its own removals.

    The output is built left to right and the opener is looked for at the tail of what has been emitted, so
    `<<!---->!--` loses both comments (a second look at the finished string would find a fresh opener).
    An unclosed opener is dropped when `drop_unclosed` (its text stays visible), else it is left as text.
    """
    if opener not in text:
        return text
    out: list[str] = []
    k, last = len(opener), opener[-1]
    closer_left = True              # once no closer exists after a position, none exists after a later one
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        out.append(ch)
        i += 1
        if ch != last or len(out) < k or "".join(out[-k:]) != opener:
            continue
        end = text.find(closer, i) if closer_left else -1
        if end >= 0:
            del out[-k:]
            i = end + len(closer)
        else:
            closer_left = False
            if drop_unclosed:
                del out[-k:]
    return "".join(out)


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
    # Entities are decoded first so `&lt;!--`, `&#123;/*` and `&#8203;` get exactly the treatment of their
    # literal forms; a second render pass then finds nothing new to remove.
    text = html.unescape(text.replace("\r\n", "\n").replace("\r", "\n"))
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT)
    text = _strip_fences(text)
    text = _IMG_MD.sub("", text).replace("![", "[")
    text = _strip_delimited(text, "<!--", "-->", drop_unclosed=True)
    text = _strip_delimited(text, "{/*", "*/}", drop_unclosed=False)
    text = clean_untrusted(text)    # control and zero-width chars: what the LLM never sees
    text = _TAG.sub(_tag, text)
    text = _LABEL_JOIN.sub(" ", text).replace("\x00", "\n")
    text = _LINK.sub(r"\1", text) if "](" in text else text
    text = _AUTOLINK.sub(r"\1", text) if "<http" in text else text
    text = _BOLD.sub(r"\1", text) if "**" in text else text
    text = _BOLD_U.sub(r"\1", text) if "__" in text else text
    text = _ITALIC.sub(r"\1", text) if "*" in text else text
    text = _CODE_SPAN.sub(r"\1", text) if "`" in text else text
    text = text.replace("**", "").replace("`", "")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join("" if _is_rule(line) else line for line in lines)
    return _settle(re.sub(r"\n{3,}", "\n\n", text).strip())


def _settle(text: str) -> str:
    """Finish with what `safety.untrusted` will do to the same text, so model view == quote-check view.

    Tag and link removal can join characters into a fresh `<!--` or `![a](b c)`; this is bounded work
    (a few linear passes) and a no-op for ordinary pages.
    """
    for _ in range(3):
        cleaned = _strip_delimited(clean_untrusted(text), "<!--", "-->", drop_unclosed=True)
        if cleaned == text:
            break
        text = cleaned
    return text.strip()


def flatten(rendered: str) -> str:
    """Comparison form of text that is already `render`ed: line markers and table pipes dropped, whitespace
    runs collapsed. `normalize(x) == flatten(render(x))`; chunk code uses this to avoid re-rendering."""
    return " ".join(_LINE_MARK.sub("", rendered).replace("|", " ").split())


def normalize(text: str) -> str:
    """Comparison form used on both the source page and the model's quote."""
    return flatten(render(text))


def quote_key(normalised_quote: str) -> str:
    """Dedupe key: hash of the normalised quote (case 26)."""
    return sha_full(normalised_quote)
