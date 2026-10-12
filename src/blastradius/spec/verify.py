"""The deterministic half of D07: what the model proposed is kept only if the page itself backs it.

Four gates, all pure functions of the page text (no model, no network), each with a reason code that
`ingest` counts in stats.dropped_by_reason or in a tag (D30; cases 22, 25, 27):

  verify_quote      the quote is one verbatim span of one block of the normalised page: not the synthetic
                    title head, not the repeated page context, not two blocks or two sentences joined, not
                    the `[code sample]` marker, not a fragment of a word.
  gate_polarity     `must_not_exist` needs an unavailability cue (not (yet) available/supported, unsupported,
                    cannot, no longer, coming soon, only available in, limited to, removed, deprecated) in the
                    quote or in a labelled callout of its section; otherwise it is downgraded to must_exist
                    and tagged `polarity:unverified`. A negative cue the model did not flag is tagged
                    `polarity:review`.
  supported_edition a non-default edition survives only if the quote's sentence, its heading or tab label, or
                    a callout at the top of the page names it, and no mention of it is negated
                    ("not supported in Self-Hosted Edition" is evidence against self-host). When the
                    evidence names other editions too the item is tagged `edition:multi`.
  clean_text        Requirement.text starts with "User can ", is at most 160 characters and holds no
                    untrusted-data delimiter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import MAX_TEXT_CHARS, MIN_QUOTE_CHARS, MIN_QUOTE_WORDS, Chunk, Unit
from .normalize import CODE_MARK, normalize

TEXT_PREFIX = "User can "
_DELIMITER = re.compile(r"(?i)<\\?/?\s*untrusted_data\b[^>\n]{0,200}>?")    # our own prompt delimiter

# ------------------------------------------------------------------------------------------------ quotes
_BOUNDARY = re.compile(r"""[.!?]["')\]]*\s+(?=[A-Z0-9"'(\[])""")
_ABBREVIATIONS = frozenset({"e.g", "i.e", "vs", "approx", "incl", "fig"})    # "etc." usually does end one


@dataclass(frozen=True)
class QuoteHit:
    quote: str          # normalised, verbatim in `unit.flat`
    unit: Unit          # the single block of the page that holds it
    start: int          # offset in unit.flat
    end: int


def sentence_spans(flat: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence of normalised text; abbreviations like `e.g.` do not end one."""
    spans, start = [], 0
    for m in _BOUNDARY.finditer(flat):
        end = len(flat[:m.end()].rstrip())
        last_word = flat[start:m.start() + 1].rsplit(None, 1)[-1].lower().rstrip(".!?")
        if last_word in _ABBREVIATIONS:
            continue
        spans.append((start, end))
        start = m.end()
    spans.append((start, len(flat)))
    return spans


def _crosses_sentence(quote: str) -> bool:
    return len(sentence_spans(quote)) > 1


def _find(quote: str, units: list[Unit]) -> QuoteHit | None:
    """First occurrence that starts and ends on a word boundary, in document order."""
    for unit in units:
        pos = unit.flat.find(quote)
        while pos >= 0:
            end = pos + len(quote)
            before = unit.flat[pos - 1] if pos else " "
            after = unit.flat[end] if end < len(unit.flat) else " "
            glued_before = before.isalnum() and quote[0].isalnum()
            glued_after = after.isalnum() and quote[-1].isalnum()
            if not glued_before and not glued_after:
                return QuoteHit(quote, unit, pos, end)
            pos = unit.flat.find(quote, pos + 1)
    return None


def verify_quote(raw: str, chunk: Chunk, page_flat: str) -> tuple[QuoteHit | None, str]:
    """(hit, "") when `raw` is one verbatim sentence-or-fragment inside one block of the page.

    Otherwise (None, reason) with reason one of too_short, code_marker, not_in_source, mid_word,
    context_repeat, spans_blocks, multi_sentence. `page_flat` is normalize(page.body).
    """
    q = normalize(raw)
    if len(q) > 2 and q[0] == q[-1] and q[0] in "\"'" and q not in page_flat:
        q = q[1:-1].strip()                  # a model that wrapped its quote in quotation marks
    if len(q) < MIN_QUOTE_CHARS or len(q.split()) < MIN_QUOTE_WORDS:
        return None, "too_short"
    if CODE_MARK in q:
        return None, "code_marker"
    if q not in page_flat:
        return None, "not_in_source"
    own = [u for u in chunk.units if u.own]
    hit = _find(q, own)
    if hit is None:
        if any(q in u.flat for u in own):
            return None, "mid_word"
        if any(q in u.flat for u in chunk.units if not u.own):
            return None, "context_repeat"
        return None, "spans_blocks"
    if _crosses_sentence(q):
        return None, "multi_sentence"
    return hit, ""


def section_of(hit: QuoteHit) -> str:
    """Heading path ("Page > Section > Subsection") of the block that holds the quote, delimiter-free."""
    return " ".join(_DELIMITER.sub(" ", " > ".join(hit.unit.path)).split())


def _sentence_of(hit: QuoteHit) -> str:
    """The sentence(s) of the holding block that the quote overlaps."""
    spans = [s for s in sentence_spans(hit.unit.flat) if s[0] < hit.end and s[1] > hit.start]
    return hit.unit.flat[spans[0][0]:spans[-1][1]] if spans else hit.unit.flat


# ------------------------------------------------------------------------------------------------ editions
_EDITION_PATTERNS: dict[str, re.Pattern[str]] = {
    "desktop": re.compile(r"(?i)\bdesktop\b"),
    "self-host": re.compile(r"(?i)\bself[- ]?host(?:ed|ing)?\b|\benterprise edition\b|\bcommunity edition\b"
                            r"|\bon[- ]prem(?:ises?)?\b"),
    "cloud-web": re.compile(r"(?i)\bcloud\b|\bweb (?:app|client|version|browser)\b|\bbrowser\b"
                            r"|\bhoppscotch\.io\b"),
}
_ALL_EDITIONS = re.compile(r"(?i)\b(?:all|every|any) (?:editions?|platforms?|clients?)\b"
                           r"|\bboth (?:the )?(?:cloud|web)\b[^.]{0,60}\b(?:desktop|self[- ]?host)")
_NEGATION = re.compile(r"(?i)\b(?:not|never|cannot|without|except|excluding|unavailable|unsupported|nor)\b"
                       r"|n't\b|\bno longer\b")
_NEGATED_AFTER = re.compile(r"""(?ix)^(?:\s+(?:app|application|edition|client|version))?\s+
    (?: (?:is|are|does|do|will)\s+(?:not|never)\b | (?:is|are)\s+un(?:available|supported)\b
      | (?:isn|aren|doesn|don|won)'t\b )""")
_CLAUSE_BREAK = re.compile(r"(?i);|\bbut\b|\bhowever\b")
_WINDOW_WORDS = 10


def _mentions(sentence: str) -> tuple[set[str], set[str]]:
    """(editions named positively, editions named under a negation) in one sentence."""
    positive: set[str] = set()
    negated: set[str] = set()
    for edition, pattern in _EDITION_PATTERNS.items():
        for m in pattern.finditer(sentence):
            clause = _CLAUSE_BREAK.split(sentence[max(0, m.start() - 120):m.start()])[-1]
            window = " ".join(clause.split()[-_WINDOW_WORDS:])
            denied = _NEGATION.search(window) or _NEGATED_AFTER.match(sentence[m.end():m.end() + 40])
            (negated if denied else positive).add(edition)
    if _ALL_EDITIONS.search(sentence) or len(positive & set(_EDITION_PATTERNS)) == len(_EDITION_PATTERNS):
        positive.add("all")
    return positive, negated


def _evidence(hit: QuoteHit, chunk: Chunk) -> list[str]:
    """Where an edition qualifier may come from: the sentence, its tab/callout label, its heading path and
    the callouts at the top of the page. Nothing else in the section counts."""
    return [_sentence_of(hit), hit.unit.label, *hit.unit.path, *chunk.callouts]


def supported_edition(claimed: str, polarity: str, hit: QuoteHit, chunk: Chunk) -> tuple[str, bool]:
    """(edition, ambiguous): `claimed` if the page backs it for this polarity, else "unknown".

    A must_exist capability in edition X needs a positive mention of X and no negated one; a must_not_exist
    capability in X needs a negated mention of X ("not supported in X"). `all` needs an explicit all-editions
    phrase (or all three editions named) and no negated edition. `ambiguous` is True when the evidence names
    other editions alongside the claimed one ("available on Cloud and the Desktop app"): the model picked.
    """
    if claimed == "unknown":
        return "unknown", False
    positive: set[str] = set()
    negated: set[str] = set()
    for segment in _evidence(hit, chunk):
        for start, end in sentence_spans(segment):
            p, n = _mentions(segment[start:end])
            positive |= p
            negated |= n
    if claimed == "all":
        return ("all", False) if "all" in positive and not negated else ("unknown", False)
    if polarity == "must_exist":
        ok = claimed in positive and claimed not in negated
        named = positive
    else:
        ok = claimed in negated
        named = negated
    if not ok:
        return "unknown", False
    return claimed, bool(named - {claimed, "all"})


# ------------------------------------------------------------------------------------------------ polarity
_NEGATIVE_CUE = re.compile(r"""(?ix)
      \bnot\s+(?:yet\s+|currently\s+|fully\s+|natively\s+)?(?:available|supported)\b
    | \b(?:isn|aren|wasn|weren)'t\s+(?:yet\s+|currently\s+)?(?:available|supported)\b
    | \b(?:does|do|did)\s+not\s+support\b | \b(?:doesn|don|didn)'t\s+support\b
    | \bunsupported\b | \bunavailable\b
    | \bcan(?:not|'t)\b | \bcan\s+not\b
    | \bno\s+longer\b | \bcoming\s+soon\b
    | \bremoved\b | \bdeprecated\b
""")
_RESTRICTIVE_CUE = re.compile(r"""(?ix)
      \bonly\s+(?:be\s+)?(?:available|supported)\s+(?:in|on|for|to|with|from)\b
    | \b(?:available|supported)\s+only\s+(?:in|on|for|to|with|from)\b
    | \blimited\s+to\b
""")


def _cue_applies(sentence: str, edition: str, *, negative_only: bool = False) -> bool:
    """Does `sentence` say the capability is unavailable for `edition`?

    A negative cue that names editions ("not supported in Self-Hosted Edition") applies to those editions; a
    restrictive one ("only available in the Desktop App") applies to every other edition. Without a named
    edition a cue applies to all of them.
    """
    negative = bool(_NEGATIVE_CUE.search(sentence))
    restrictive = not negative_only and bool(_RESTRICTIVE_CUE.search(sentence))
    if not (negative or restrictive):
        return False
    positive, negated = _mentions(sentence)
    named = (positive | negated) - {"all"}
    if not named:
        return True
    return (negative and edition in named) or (restrictive and edition not in named)


def gate_polarity(claimed: str, edition: str, hit: QuoteHit, chunk: Chunk) -> tuple[str, str | None]:
    """(polarity, tag). `edition` is the edition the capability will carry (claimed or the target default).

    The cue must be in the quote or in a labelled callout of the quote's own section; text elsewhere on
    the page, and the model's say-so, are not evidence. A negative kept on a callout's cue alone is tagged
    `polarity:callout_cue`, since a callout can mix statements.
    """
    callouts = [u for u in chunk.units if u.own and u.callout and u.path == hit.unit.path]
    callout_sentences = [u.flat[a:b] for u in callouts for a, b in sentence_spans(u.flat)]
    if claimed == "must_not_exist":
        if _cue_applies(hit.quote, edition):
            return "must_not_exist", None
        if any(_cue_applies(s, edition) for s in callout_sentences):
            return "must_not_exist", "polarity:callout_cue"
        return "must_exist", "polarity:unverified"
    own = [hit.quote]
    if hit.unit.callout:
        own += [hit.unit.flat[a:b] for a, b in sentence_spans(hit.unit.flat)]
    flagged = any(_cue_applies(s, edition, negative_only=True) for s in own)
    return "must_exist", "polarity:review" if flagged else None


# ------------------------------------------------------------------------------------------------ text
def clean_text(raw: str) -> tuple[str | None, str]:
    """(text, "") or (None, reason): Requirement.text is model prose, so it is held to a fixed shape."""
    text = normalize(_DELIMITER.sub(" ", raw))
    if not text:
        return None, "empty_text"
    if not text.startswith(TEXT_PREFIX):
        return None, "text_not_user_can"
    if len(text) > MAX_TEXT_CHARS:
        return None, "text_too_long"
    return text, ""
