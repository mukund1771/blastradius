"""The product's own words (REPORT_SPEC "Language rules"): screen labels, element phrases, band wording.

Everything a QA lead reads in sections 1-8 is built from these helpers or from text the crawl and the spec
already contain (screen and dialog titles, control names, requirement sections). A file name is turned into
words only as a last resort and is then flagged as "component name" wording, never presented as a screen title.

Band wording is the verb table of REPORT_SPEC. Names taken from the page are always wrapped in “ ” by
`quoted`, so the verifier can strip them before it checks verbs (a button called "Will it blend" must not make a
confirmed sentence fail, and cannot make a possible one pass).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from ..models import Band

# REPORT_SPEC language-rules table: allowed phrases per band and forbidden words per band
ALLOWED: dict[Band, tuple[str, ...]] = {
    Band.CONFIRMED: ("will change", "is changed by this pr"),
    Band.LIKELY: ("probably affected", "likely changes"),
    Band.POSSIBLE: ("might be affected", "worth a quick look"),
    Band.UNKNOWN: ("we cannot tell", "not determined"),
}
FORBIDDEN: dict[Band, tuple[str, ...]] = {
    Band.CONFIRMED: ("might", "could"),
    Band.LIKELY: ("will", "is certain"),
    Band.POSSIBLE: ("will", "probably"),
    # "any claim of effect": the unknown band may only say that it cannot tell
    Band.UNKNOWN: ("will", "might", "could", "probably", "likely", "affected", "changes", "changed", "breaks"),
}
BAND_ORDER = (Band.UNKNOWN, Band.POSSIBLE, Band.LIKELY, Band.CONFIRMED)    # weakest first

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_QUOTED = re.compile("“[^”]*”")


def lowest_band(bands: Iterable[Band]) -> Band:
    seen = list(bands)
    return min(seen, key=BAND_ORDER.index) if seen else Band.UNKNOWN


def quoted(text: str) -> str:
    """A name taken from the page or the spec, set in typographic quotes (stripped before verb checks)."""
    cleaned = " ".join(text.replace("“", '"').replace("”", '"').split())
    return f"“{cleaned}”"


def strip_quoted(text: str) -> str:
    return _QUOTED.sub(" ", text)


_GENERIC_DIRS = frozenset({
    "components", "pages", "layouts", "services", "helpers", "src", "composables", "newstore", "store", "lib",
    "utils", "modules", "platform", "functional", "packages", "app", "common",
})


def humanize_path(path: str) -> str:
    """`components/environments/CurrentValueInput.vue` -> `environments current value input`: the file name in
    words, behind its directory unless that is generic (`components`) or already the start of the name;
    `index.vue` is its directory."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return path
    stem = parts[-1].split(".")[0]
    dirs = parts[:-1]
    if stem in ("index", "default") and dirs:
        while len(dirs) > 1 and dirs[-1] == "src":      # `pkg/src/index.ts` is the package, not "src"
            dirs.pop()
        stem = dirs.pop()
    parent = dirs[-1] if dirs else ""
    words = _CAMEL.sub(" ", stem.replace("-", " ").replace("_", " ")).split()
    if parent and parent.casefold() not in _GENERIC_DIRS and parent != stem:
        lead = _CAMEL.sub(" ", parent.replace("-", " ").replace("_", " ")).split()
        if lead and [w.lower() for w in lead] != [w.lower() for w in words[: len(lead)]]:
            words = lead + words
    return " ".join(w.lower() for w in words) or stem


def sentence(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def count_phrase(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def join_names(names: Sequence[str], limit: int = 3, *, more: str = "more") -> str:
    """`“A”, “B” and 2 more` from already-quoted names."""
    shown = list(names[:limit])
    rest = len(names) - len(shown)
    if not shown:
        return ""
    if rest > 0:
        return ", ".join(shown) + f" and {rest} {more}"
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " and " + shown[-1]


_ROLE_VERB = {
    "button": "click", "link": "open", "textbox": "type in", "searchbox": "search with", "checkbox": "toggle",
    "radio": "choose", "menuitem": "choose", "tab": "switch to", "switch": "toggle", "combobox": "pick from",
    "option": "pick", "slider": "adjust", "spinbutton": "adjust",
}


def element_action(role: str, name: str) -> str:
    """`click “Send”`: an action a person can take, in the control's own words."""
    return f"{_ROLE_VERB.get(role, 'use')} {quoted(name)}"


def screen_label(*, title: str, dialog_title: str | None, kind: str, active_tabs: Sequence[str], url_pattern: str,
                 brand_words: Iterable[str] = ()) -> str:
    """How the QA lead names a screen: the dialog title for a dialog, else the page's name from its title or its
    route, plus the open tab for a tab state. Never a path or an id."""
    brands = {b.casefold() for b in brand_words if b}
    if dialog_title and kind == "modal":
        return f"{sentence(dialog_title.strip())} dialog"
    pattern = (url_pattern or "/").strip() or "/"
    if pattern == "/":
        base = "Home page"
    elif pattern.endswith("/*"):
        base = "Not-found page"
    else:
        segments = [s.strip() for s in re.split(r"\s[•|·–-]\s", title or "") if s.strip()]
        names = [s for s in segments if s.casefold() not in brands]
        base = names[0] if names else " ".join(w.capitalize() for w in pattern.strip("/").split("/") if w)
        base = f"{base} page"
    if kind == "tab" and active_tabs:
        return f"{base}, {active_tabs[-1]} tab"
    return base
