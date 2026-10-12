"""Pure shaping rules the graph writer applies before anything touches Neo4j (no I/O, no Neo4j import).

Implements D08 and cases 46 (canonical UIElement + APPEARS_ON) and 51 (counts are over distinct nodes).

Canonical UIElement rule:

  key = (owner_hint or "", role, normalised accessible name, ordinal, scope)

  owner_hint = the owning component/scope id when the page exposes one (UIElementObs.owner_hint, case 7 and
               46's "owner SFC"), "" when it does not. It is the first part, so two components that render a
               control with the same role, name and ordinal stay two elements.
  scope = ""                      for a named element on a page, tab or embed Screen
  scope = "dialog:<title>"        for a named element on a modal Screen (the crawler only lists the topmost
                                  dialog's elements there), so two dialogs' "Close" buttons stay apart
  scope = <observation's own id>  for a nameless element (case 7): "button, no name, ordinal 0" says nothing
                                  about identity across pages, so only the crawler's own id may merge it

A layout control that recurs on every Screen has the same owner, role, name and ordinal everywhere, so it
becomes one UIElement with one APPEARS_ON edge per Screen instead of one node per Screen. The id is a pure
function of the key (never of group membership), so it does not change when a later crawl observes the
element on another Screen; when `owner_hint` is empty the id is the one the wave-1 key produced. The
crawl-time ids survive as `obs_ids` so Transition.element_id, Flow.steps and Link.src written against an
observation id still resolve.

Residual risk, stated: when the crawler cannot name an owner, two different page-level components that both
render a control with the same role, name and ordinal on different pages still merge into one element (66 of
982 UI strings on Hoppscotch are shared between the http and graphql trees). The link stage records the
screens a link was derived on (`Link.screen_ids`, stored on IMPLEMENTED_BY/REALIZED_BY) and `impact` counts a
screen only when the edge's screen_ids is empty or contains it, so the merge does not light screens whose
component did not change. The link stage still fills `owner_file` from its IMPLEMENTED_BY edges.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..ids import stable_id
from ..models import ScreenState, UIElementObs

_SELECTOR_MAX = 300
_CATCH_ALL = re.compile(r"\(\.\*\)|^\*$|\.\.\.|catchall|pathmatch", re.IGNORECASE)


def _norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def element_key(obs: UIElementObs, screen: ScreenState | None = None) -> tuple[str, str, str, int, str]:
    """(owner_hint or "", role, normalised name, ordinal, scope): the canonical identity of an observation."""
    name = _norm_name(obs.name)
    if not name:
        scope = obs.id
    elif screen is not None and screen.kind == "modal" and screen.dialog_title:
        scope = "dialog:" + _norm_name(screen.dialog_title)
    else:
        scope = ""
    return (obs.owner_hint or "", obs.role.casefold(), name, obs.ordinal, scope)


def element_id(target: str, key: tuple[str, str, str, int, str]) -> str:
    """Pure function of the key. An empty owner is left out of the hash so owner-less ids are unchanged."""
    return stable_id("uie", target, *(key if key[0] else key[1:]))


@dataclass
class Canonical:
    elements: list[dict[str, Any]] = field(default_factory=list)  # {"id", "props", "obs_ids"}
    appearances: list[dict[str, Any]] = field(default_factory=list)  # one per (element, screen, obs id)
    alias: dict[str, str] = field(default_factory=dict)  # observation id -> canonical id
    observations: int = 0


def _first(values: Iterable[Any]) -> Any:
    return next((v for v in values if v), None)


def canonicalise(screens: Sequence[ScreenState], target: str) -> Canonical:
    """Group every UIElementObs of `screens` into canonical elements. Deterministic for any input order."""
    groups: dict[tuple[str, str, str, int, str], list[tuple[ScreenState, UIElementObs]]] = {}
    out = Canonical()
    for screen in sorted(screens, key=lambda s: s.id):
        for obs in sorted(screen.elements, key=lambda o: (o.ordinal, o.id)):
            groups.setdefault(element_key(obs, screen), []).append((screen, obs))
            out.observations += 1
    for key in sorted(groups):
        members = groups[key]
        eid = element_id(target, key)
        obs_all = [o for _, o in members]
        rep = obs_all[0]
        obs_ids = sorted({o.id for o in obs_all})
        out.elements.append(
            {
                "id": eid,
                "obs_ids": obs_ids,
                "props": {
                    "role": rep.role,
                    "name": rep.name,
                    "ordinal": rep.ordinal,
                    "owner_hint": rep.owner_hint,
                    "text": _first(o.text for o in obs_all) or "",
                    "tag": _first(o.tag for o in obs_all) or "",
                    "testid": _first(o.testid for o in obs_all),
                    "dom_id": _first(o.dom_id for o in obs_all),
                    "href": _first(o.href for o in obs_all),
                    "placeholder": _first(o.placeholder for o in obs_all),
                    "title_attr": _first(o.title_attr for o in obs_all),
                    "aria_label": _first(o.aria_label for o in obs_all),
                },
            }
        )
        seen: set[tuple[str, str]] = set()
        for screen, obs in members:
            out.alias[obs.id] = eid
            if (screen.id, obs.id) in seen:
                continue
            seen.add((screen.id, obs.id))
            out.appearances.append(
                {
                    "element": eid,
                    "screen": screen.id,
                    "obs_id": obs.id,
                    "ordinal": obs.ordinal,
                    "visible": obs.visible,
                    "bbox": list(obs.bbox) if obs.bbox else None,
                    "selector": obs.selector[:_SELECTOR_MAX] if obs.selector else None,
                }
            )
    return out


# ------------------------------------------------------------------------------------ screen -> route
def _norm_path(pattern: str) -> str:
    path = pattern.split("#", 1)[0].split("?", 1)[0].strip()
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _is_param(segment: str) -> bool:
    return segment.startswith(":") or (segment.startswith("[") and segment.endswith("]")) or segment == "*"


def _is_catch_all(pattern: str) -> bool:
    return bool(_CATCH_ALL.search(pattern))


def _segments_match(route_segs: Sequence[str], segs: Sequence[str]) -> bool:
    return len(route_segs) <= len(segs) and all(_is_param(r) or r == s for r, s in zip(route_segs, segs))


def match_route(url_pattern: str, routes: Iterable[str]) -> tuple[str, str] | None:
    """The Route pattern that serves a Screen's url_pattern, as (pattern, how) or None.

    Order: exact string, then same-length match where a route parameter segment (`:id`, `[id]`, `*`)
    accepts any segment (most static segments wins), then a catch-all route by longest static prefix.
    A catch-all never beats a real route, so a soft-404 Screen (case 3) lands on the catch-all page.
    """
    want = _norm_path(url_pattern)
    segs = want.strip("/").split("/") if want != "/" else []
    param: list[tuple[int, str]] = []
    catch: list[tuple[int, str]] = []
    for route in sorted(set(routes)):
        norm = _norm_path(route)
        rsegs = norm.strip("/").split("/") if norm != "/" else []
        if norm == want:
            return route, "exact"
        if _is_catch_all(route):
            prefix = rsegs[:-1]
            if _segments_match(prefix, segs):
                catch.append((sum(1 for s in prefix if not _is_param(s)), route))
        elif len(rsegs) == len(segs) and _segments_match(rsegs, segs):
            param.append((sum(1 for s in rsegs if not _is_param(s)), route))
    best = min(param, key=lambda t: (-t[0], t[1])) if param else None
    if best:
        return best[1], "param"
    best = min(catch, key=lambda t: (-t[0], t[1])) if catch else None
    return (best[1], "catch_all") if best else None
