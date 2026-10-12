"""Text normalisation and tokenisation shared by the UI->code cascade and the requirement->UI search.

One rule for "the same visible text" on both sides of a match (D07: the code disposes, so the comparison must
be exact and explainable), plus a small deterministic tokenizer for BM25 candidate generation (D20).

`normalise_text` is applied to element names, attributes, i18n values and source literals alike:
NFKC (so a Unicode ellipsis equals three dots), whitespace collapsed, casefolded, keyboard-shortcut glyphs
removed (the crawler already strips them; locale values never carry them), trailing ellipsis and colon
removed ("Search..." and "Fields:" match "Search" and "Fields").
"""

from __future__ import annotations

import re
import unicodedata

from ..redact import redact_text

_KBD_GLYPHS = re.compile("[⌘⌥⇧⌃↵⏎⌫⌦⇥⎋⇪␣]")
_TRAILING = re.compile(r"(?:\s|[:：]|\.{2,})+$")
_WORD = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "for", "from", "has", "have", "how", "if",
    "in", "into", "is", "it", "its", "of", "on", "or", "so", "that", "the", "their", "then", "there", "these",
    "this", "to", "via", "was", "were", "when", "where", "which", "while", "who", "will", "with", "without",
    "you", "your", "user", "users", "able", "allow", "allows", "allowed", "also", "any", "each", "use", "used",
    "using",
})


def normalise_text(text: str | None) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = _KBD_GLYPHS.sub(" ", s)
    s = " ".join(s.split()).casefold()
    return _TRAILING.sub("", s)


def stem(word: str) -> str:
    """Conservative suffix folding: plural s, ies, ing, ed, trailing e. Deterministic and symmetric (query and
    document use the same function), so recall improves without a stemming library."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def tokens(text: str | None) -> list[str]:
    """Stemmed content tokens in order, stopwords and single characters removed (digits kept)."""
    out = []
    for w in _WORD.findall(normalise_text(text)):
        if w in STOPWORDS or len(w) < 2:
            continue
        out.append(stem(w))
    return out


PROMPT_TEXT_CHARS = 160


def prompt_text(text: str | None, limit: int = PROMPT_TEXT_CHARS) -> str:
    """Page-derived text as it may appear in an LLM prompt (case 58): known secret shapes replaced, whitespace
    collapsed, cut to `limit` characters. A DOM can hold environment values (#6645 is about editing them)."""
    return redact_text(" ".join((text or "").split()), truncate=False)[:limit]
