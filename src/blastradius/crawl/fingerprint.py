"""Screen identity: url pattern, structural fingerprint, near-duplicate merge, screen kind.

Implements D04 and cases 3, 10, 12, 14 (a state is its *rendered* identity, never an HTTP status, and a toast
or a clock tick must not create a new screen).

fingerprint = sha(url_pattern, topmost dialog title, active tab labels, up to 60 stable (role, name) keys)
Two states with the same (url_pattern, dialog title, active tabs) whose element sets overlap with
Jaccard >= 0.9 are one screen. A state whose only difference is the active tab is a distinct `tab` screen.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from typing import Literal
from urllib.parse import urlsplit

from ..ids import sha
from .perceive import Element

MERGE_JACCARD = 0.9
MAX_FINGERPRINT_ELEMENTS = 60
CATCH_ALL_PATTERN = "/*"

_NUMERIC = re.compile(r"^\d+$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX = re.compile(r"^[0-9a-fA-F]{8,}$")
_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_WORD = re.compile(r"[A-Za-z]{3,}")
_LETTER_DIGIT_EDGE = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def _is_random_token(seg: str) -> bool:
    """base64/nanoid/cuid shape: 16+ chars of [A-Za-z0-9_-] with letters and digits that are not words.

    A kebab-case slug (`getting-started-with-oauth2`) has the same alphabet and one digit, so the shape
    rule is explicit: a separator-free run needs several letter/digit edges, a mixed-case run needs both
    cases and a digit, and anything with two or more plain words between separators is a slug."""
    if not _TOKEN_CHARS.match(seg) or not re.search(r"\d", seg) or not re.search(r"[A-Za-z]", seg):
        return False
    parts = [p for p in re.split(r"[-_]", seg) if p]
    if sum(1 for p in parts if _WORD.fullmatch(p)) >= 2:
        return False
    if len(parts) == 1:
        return len(_LETTER_DIGIT_EDGE.findall(seg)) >= 3 or (
            bool(re.search(r"[a-z]", seg)) and bool(re.search(r"[A-Z]", seg))
        )
    return bool(re.search(r"[a-z]", seg)) and bool(re.search(r"[A-Z]", seg))


def _is_id_segment(seg: str) -> bool:
    """Numeric, UUID, hex digest (8+ with a digit) or a random-looking token. Never a plain slug (D04)."""
    if _NUMERIC.match(seg) or _UUID.match(seg):
        return True
    if _HEX.match(seg) and re.search(r"\d", seg):
        return True
    return _is_random_token(seg)


def url_pattern(url: str) -> str:
    """Path of the URL with numeric/uuid/hash segments replaced by :id; query and fragment dropped.

    A fragment is kept only when it is a hash route (`#/path`)."""
    parts = urlsplit(url)
    segs = [":id" if _is_id_segment(s) else s for s in parts.path.split("/") if s]
    path = "/" + "/".join(segs)
    if parts.fragment.startswith("/"):
        frag = [":id" if _is_id_segment(s) else s for s in parts.fragment.split("/") if s]
        path += "#/" + "/".join(frag)
    return path


def element_key(el: Element) -> tuple[str, str]:
    """(role, name) with a stable icon signature standing in for a missing name (case 7)."""
    name = el.obs.name or (f"icon:{el.icon}" if el.icon else "")
    return el.obs.role, name


def layout_key(el: Element) -> tuple[str, str, str]:
    """(role, name-or-icon, owner scope): what makes two controls "the same layout control" across screens.

    The owner scope (`owner_hint`, a `data-v-<8hex>` attribute) keeps repeated nameless icons that belong to
    different components apart, so one acted-on "more" icon does not hide the other."""
    role, name = element_key(el)
    return role, name, el.obs.owner_hint or ""


def stable_keys(elements: Iterable[Element]) -> list[tuple[str, str]]:
    """Distinct element keys that may enter a fingerprint: visible, named or icon-identified, and capped.

    The cap keeps the 60 keys with the smallest hash, so a small change in the element set moves the
    fingerprint only if it touches one of the kept keys (bottom-k selection)."""
    keys = {element_key(e) for e in elements if e.obs.visible and element_key(e)[1]}
    ranked = sorted(keys, key=lambda k: hashlib.sha1(f"{k[0]}\x1f{k[1]}".encode()).hexdigest())
    return sorted(ranked[:MAX_FINGERPRINT_ELEMENTS])


def fingerprint(
    pattern: str, dialog_title: str | None, active_tabs: Sequence[str], elements: Iterable[Element]
) -> str:
    keys = stable_keys(elements)
    return sha(
        pattern, dialog_title or "", "|".join(sorted(active_tabs)), *(f"{r}:{n}" for r, n in keys), n=16
    )


def jaccard(a: Iterable[tuple[str, str]], b: Iterable[tuple[str, str]]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def key_set(elements: Iterable[Element]) -> frozenset[tuple[str, str]]:
    """All distinct keys on a screen (uncapped) for similarity and merging."""
    return frozenset(k for k in (element_key(e) for e in elements) if k[1])


def group_key(
    pattern: str, dialog_title: str | None, active_tabs: Sequence[str]
) -> tuple[str, str, tuple[str, ...]]:
    return pattern, dialog_title or "", tuple(sorted(active_tabs))


ScreenKind = Literal["page", "modal", "tab", "embed"]


def screen_kind(
    *, pattern: str, has_popup: bool, active_tabs: Sequence[str], page_tabs_for_pattern: Sequence[str] | None
) -> ScreenKind:
    """modal when a popup is the topmost layer; embed for /e/*; tab when only the active tabs differ from
    the first page state seen at this url pattern; else page."""
    if has_popup:
        return "modal"
    if pattern.startswith("/e/") or pattern == "/e":
        return "embed"
    if page_tabs_for_pattern is not None and sorted(active_tabs) != sorted(page_tabs_for_pattern):
        return "tab"
    return "page"
