"""Report vocabulary shared by the renderers and the lint (D29; docs/REPORT_SPEC.md; cases 57, 61, 62).

One place for the fixed section titles and order, the band words and the per-band verb table, so that what
the renderer writes and what the lint accepts cannot drift apart. `reason.verify` is expected to import
`verb_violations` from here (or from `report.lint`) rather than keep its own copy of the table.
"""

from __future__ import annotations

import re

from ..models import Band
from .model import ReqStatus, RiskLevel

# Section keys in the fixed order of REPORT_SPEC (the headline is section 1 and has no h2).
SECTION_KEYS = ("checklist", "direct", "indirect", "global", "stored_data", "requirements", "undetermined",
                "appendix")
OPTIONAL_SECTIONS = frozenset({"global", "stored_data"})

SECTION_TITLES: dict[str, str] = {
    "checklist": "What to re-test first",
    "direct": "Directly changed",
    "indirect": "Indirect, through shared code",
    "global": "Global change",
    "stored_data": "Stored data",
    "requirements": "Requirements at risk",
    "undetermined": "What we could not determine, and who should look",
    "appendix": "Appendix: evidence",
}
NOT_CHECKED_TITLE = "What this report did not look at"
FINDING_KEYS = ("direct", "indirect", "global", "stored_data", "requirements")  # sections 3-7

CHECKLIST_MAX = 8            # REPORT_SPEC section 2
TIER2_CAP = 10               # REPORT_SPEC section 4; the uncapped list stays in impact.json (case 61)
UNDETERMINED_TOP = 5         # case 61: top rows in the body, the rest in the appendix
MAIN_BODY_WORD_BUDGET = 600  # REPORT_SPEC lint: about 1.5 pages of prose, tables not counted
HEADLINE_MAX_LINES = 3

MIGRATION_STEPS = (
    "Export your collections and environments from the previous build, then import that file into this build.",
    "If you use cloud sync, sync from this build and confirm the data arrives on a second browser or device.",
    "Open data saved by the previous build in this build and confirm it loads without loss.",
)
GLOBAL_SENTENCE = ("This PR changes something every screen shares, so every screen may be affected and the "
                   "lists above are not exhaustive.")

BAND_WORD: dict[Band, str] = {
    Band.CONFIRMED: "confirmed", Band.LIKELY: "likely", Band.POSSIBLE: "possible", Band.UNKNOWN: "unknown",
}
BAND_WORDS = frozenset(BAND_WORD.values())
BAND_RANK: dict[Band, int] = {Band.CONFIRMED: 0, Band.LIKELY: 1, Band.POSSIBLE: 2, Band.UNKNOWN: 3}
BAND_GLYPH: dict[Band, str] = {Band.CONFIRMED: "●", Band.LIKELY: "◕", Band.POSSIBLE: "◔", Band.UNKNOWN: "○"}
BAND_MEANING: dict[Band, str] = {
    Band.CONFIRMED: "the PR's own code changes it",
    Band.LIKELY: "reached through code it shares",
    Band.POSSIBLE: "a weaker connection, worth a look",
    Band.UNKNOWN: "we cannot tell",
}
BAND_RANGE: dict[Band, str] = {
    Band.CONFIRMED: ">= 0.8", Band.LIKELY: "0.5 to 0.8", Band.POSSIBLE: "0.25 to 0.5", Band.UNKNOWN: "< 0.25",
}

RISK_WORDS: tuple[RiskLevel, ...] = ("LOW", "MEDIUM", "HIGH", "UNKNOWN")
RISK_GLYPH: dict[str, str] = {"LOW": "▾", "MEDIUM": "◆", "HIGH": "▲", "UNKNOWN": "◌"}

STATUS_WORD: dict[ReqStatus, str] = {
    "loses_only_realization": "Loses its only realization",
    "at_risk": "At risk",
    "cannot_verify": "Cannot verify",
    "may_close_gap": "This PR may close a gap",
}
STATUS_RANK: dict[ReqStatus, int] = {k: i for i, k in enumerate(STATUS_WORD)}

# ----------------------------------------------------------------------------------------- verb table
# Allowed phrases (REPORT_SPEC language rules). Documentation for authors; the lint enforces the forbidden side.
BAND_ALLOWED: dict[Band, tuple[str, ...]] = {
    Band.CONFIRMED: ("will change", "is changed by this PR"),
    Band.LIKELY: ("probably affected", "likely changes"),
    Band.POSSIBLE: ("might be affected", "worth a quick look"),
    Band.UNKNOWN: ("we cannot tell", "not determined"),
}

# Forbidden for each band: the spec table first, then the close synonyms that would say the same thing.
BAND_FORBIDDEN: dict[Band, tuple[str, ...]] = {
    Band.CONFIRMED: ("might", "could", "may", "possibly", "perhaps", "maybe", "probably", "likely", "seems to",
                     "appears to"),
    Band.LIKELY: ("will", "is certain", "certainly", "definitely", "guaranteed", "is changed by this PR",
                  "confirmed"),
    Band.POSSIBLE: ("will", "probably", "likely", "is certain", "certainly", "definitely", "confirmed",
                    "is changed by this PR"),
    Band.UNKNOWN: ("will", "would", "probably", "likely", "certainly", "definitely", "confirmed",
                   "might be affected", "worth a quick look", "is changed by this PR"),
}
# "any claim of effect" for the unknown band: passive effect phrases, unless the sentence is itself framed as
# not knowing ("we cannot tell whether the screen is affected" is not a claim).
_EFFECT = re.compile(
    r"\b(?:is|are|was|were|gets?|got)\s+(?:changed|affected|broken|impacted|modified|updated|altered|replaced|"
    r"removed|added)\b|\bchanges?\s+(?:the|how|what)\b", re.IGNORECASE)
_UNKNOWN_FRAME = re.compile(
    r"\b(?:cannot|can't|could not|couldn't|unable to|not determined|undetermined|whether|no way to tell|"
    r"no evidence|not known|unknown)\b", re.IGNORECASE)

_QUOTED = re.compile(r"“[^”]*”|\"[^\"]*\"")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _phrase_re(phrase: str) -> re.Pattern[str]:
    return re.compile(r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b", re.IGNORECASE)


_FORBIDDEN_RE: dict[Band, tuple[tuple[str, re.Pattern[str]], ...]] = {
    band: tuple((p, _phrase_re(p)) for p in phrases) for band, phrases in BAND_FORBIDDEN.items()
}


def verb_violations(text: str, band: Band) -> list[str]:
    """The forbidden phrases for `band` that occur in `text` (sorted, unique).

    Quoted material (a spec quote, a PR title) is not ours and is skipped. A "confirmed" sentence may not hedge,
    a "likely" one may not promise, a "possible" one may not say "will" or "probably", and an "unknown" one may
    not claim an effect at all.
    """
    plain = _QUOTED.sub(" ", text)
    found = {phrase for phrase, rx in _FORBIDDEN_RE[band] if rx.search(plain)}
    if band is Band.UNKNOWN:
        for sentence in _SENTENCE.split(plain):
            if not _UNKNOWN_FRAME.search(sentence):
                effects = {m.group(0).lower() for m in _EFFECT.finditer(sentence)}
                found.update(e for e in effects if not any(e in phrase for phrase in found))
    return sorted(found)


# ------------------------------------------------------------------------------------ body hygiene
ID_PREFIXES = ("ev", "req", "scr", "uie", "clm", "el", "cap", "flow", "gap", "snap", "tr")
RAW_ID = re.compile(r"\b(?:" + "|".join(ID_PREFIXES) + r")_[0-9A-Za-z]{3,}\b")
RAW_SHA = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
FILE_PATH = re.compile(
    r"(?:[\w@.~-]+/)+[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,4}\b"                  # dir/file.ext
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:vue|tsx?|jsx|mjs|cjs|json|ya?ml|scss|cypher|toml)\b"  # bare file name
    r"|\b(?:packages|src|apps|node_modules)/[\w@./-]+"                       # known source roots
    r"|(?<![\w@])@[\w-]+/[\w.-]+")                                           # @scope/package
JARGON = re.compile(r"\b(?:nodes?|edges?|graphs?|closures?|hops?)\b", re.IGNORECASE)
HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>|<!--|<[!?][A-Za-z]")
REMOTE_URL = re.compile(r"(?i)\b(?:https?|ftp)://|!\[")


def has_html_tag(text: str) -> bool:
    return bool(HTML_TAG.search(text))
