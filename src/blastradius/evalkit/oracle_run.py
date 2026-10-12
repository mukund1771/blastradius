"""Run the source-map oracle against a run's artifacts and score the linker (D23; cases 53, 63, 71).

Flow: harvest (live, <= 6 page visits) -> attribute components -> label elements -> join to the crawl and
score the Link rows. When the live harvest is unavailable (no browser, no network, robots, an exception) the
same scoring runs on hand labels from `<run>/eval/hand_labels.json` and the output says so; with no labels
either, a 25-element template is written next to the results and the status is `needs_labels` (never a
made-up number).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..failures import FailureCode
from ..ids import sha
from ..models import CodeIndex, Link, ScreenState
from ..run import RunContext
from ..targets import TargetConfig
from .artifacts import indexed_paths, load_index, load_links, load_screens
from .oracle_score import Label, metrics, norm_name, score_units
from .oracle_sourcemap import (
    DEFAULT_PATHS,
    MAX_VISITS,
    ORACLE_STATEMENT,
    Harvest,
    build_labels,
    harvest,
    harvest_summary,
)

HAND_LABELS = "hand_labels.json"
HAND_TEMPLATE = "hand_labels.template.json"
TEMPLATE_SIZE = 25
_LABELLABLE_ROLES = frozenset({"button", "link", "tab", "menuitem", "checkbox", "switch", "radio", "textbox",
                               "combobox", "searchbox", "option"})

CAVEATS = (
    "Test-only oracle: it exists because this target publishes source maps; no other target has it.",
    "It labels only UI present in the deployed build: UI introduced by an open PR has no label (case 71).",
    "Deployed is not main: scoring is restricted to files that exist on the indexed main; drift is stated.",
    "Elements with no component instance and unattributed chains are left unlabelled and counted.",
    "The unit is a distinct (file, role, name, ordinal): a layout control seen on four pages counts once.",
    "A 'pattern' join matches by url_pattern only and can conflate two states' ordinals; 'state' is exact.",
    ("The oracle names the file whose template contains the element; the linker names the file that "
     "displays its text. They differ when text arrives as a prop, so top-1 is a conservative measure and "
     "hit@3 less so."),
    "Small n: read the Wilson interval, not the point estimate.",
)


# ------------------------------------------------------------------------------------ hand labels
def hand_label_template(screens: Sequence[ScreenState], n: int = TEMPLATE_SIZE) -> list[dict[str, Any]]:
    """n named interactive crawl elements, spread over screens deterministically, with `oracle_file: null`.

    A human fills `oracle_file` (repo-relative path of the .vue that renders the element) and the scorer uses
    the entries that have one."""
    pool: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for s in sorted(screens, key=lambda s: s.id):
        if s.url_pattern == "/*":
            continue                                    # the soft-404 shell
        for el in s.elements:
            if el.role not in _LABELLABLE_ROLES or not el.name.strip():
                continue
            key = (s.url_pattern, el.role, norm_name(el.name), el.ordinal)
            pool.setdefault(key, {
                "url_pattern": s.url_pattern, "active_tabs": sorted(s.active_tabs),
                "dialog_title": s.dialog_title, "role": el.role, "name": el.name, "ordinal": el.ordinal,
                "oracle_file": None, "screen_id": s.id, "selector": el.selector})
    ranked = sorted(pool.items(), key=lambda kv: sha(*kv[0]))
    return [row for _, row in ranked[:n]]


def load_hand_labels(path: Path) -> list[Label]:
    """Labelled entries of a hand-label file (entries with `oracle_file: null` are skipped)."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[Label] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("oracle_file"):
            continue
        out.append(Label(url_pattern=str(r["url_pattern"]), active_tabs=tuple(r.get("active_tabs") or ()),
                         dialog_title=r.get("dialog_title"), role=str(r["role"]), name=str(r["name"]),
                         ordinal=int(r.get("ordinal", 0)), oracle_file=str(r["oracle_file"]),
                         owner_chain=("hand label",)))
    return out


def crawled_paths(screens: Sequence[ScreenState], wanted: Sequence[str] = DEFAULT_PATHS,
                  cap: int = MAX_VISITS) -> list[str]:
    """The pages worth visiting, taken from what the crawl actually saw: the wanted paths it covered, else the
    first concrete page routes (no catch-all, no id segments) in discovery order. A page the crawl never saw
    cannot be joined to anything, so visiting it would only spend a visit."""
    seen: dict[str, int] = {}
    for s in sorted(screens, key=lambda s: (s.depth, s.id)):
        if s.kind in ("page", "tab") and "*" not in s.url_pattern and ":id" not in s.url_pattern \
                and "#" not in s.url_pattern:
            seen.setdefault(s.url_pattern, len(seen))
    hit = [p for p in wanted if p in seen]
    return (hit or sorted(seen, key=lambda p: seen[p]))[:cap]


# ----------------------------------------------------------------------------------------- the run
def _write_rows(run: RunContext, rows: list[dict[str, Any]]) -> str:
    path = run.path("eval", "oracle_elements.jsonl")
    path.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return "eval/oracle_elements.jsonl"


def _score(run: RunContext, labels: list[Label], screens: list[ScreenState], links: list[Link],
           index: CodeIndex | None) -> dict[str, Any]:
    results = score_units(labels, screens, links, indexed_paths(index))
    out = metrics(results, len(labels))
    out["rows"] = _write_rows(run, [r.as_dict() for r in results])
    scored = [r for r in results if r.status == "scored"]
    out["misses_sample"] = [
        {"role": r.role, "name": r.name, "oracle_file": r.oracle_file, "predicted_top1": r.top1.path if r.top1
         else None, "owner_hops": r.owner_hops}
        for r in sorted((r for r in scored if not r.hit3),
                        key=lambda r: (r.oracle_file, r.role, r.name))[:15]]
    return out


def run_oracle(run: RunContext, cfg: TargetConfig, *, live: bool = True,
               harvest_fn: Callable[..., Harvest] = harvest, cache_dir: Path | None = None,
               paths: Sequence[str] | None = None) -> dict[str, Any]:
    """The oracle section of results.json. Never raises for an unavailable browser or network."""
    screens = load_screens(run, cfg.name)
    links = load_links(run)
    index = load_index(run)
    base: dict[str, Any] = {"statement": ORACLE_STATEMENT, "caveats": list(CAVEATS)}
    if not screens or not index:
        return {**base, "status": "skipped",
                "reason": "needs crawl/screens.jsonl and code/index.json (run `blastradius crawl`, `index`)"}
    if not links:
        return {**base, "status": "skipped",
                "reason": "no links/links.jsonl: nothing to score (run `blastradius link`)"}
    out: dict[str, Any] = {**base, "n_crawl_screens": len(screens), "n_links": len(links)}
    labels: list[Label] = []
    if live:
        kwargs: dict[str, Any] = {"cache_dir": cache_dir, "paths": list(paths) if paths is not None
                                  else crawled_paths(screens)}
        out["visit_paths"] = kwargs["paths"]
        try:
            with run.tracer.span("eval", "oracle.harvest"):
                h = harvest_fn(cfg, run.settings, **kwargs)
            built = build_labels(h, index, cfg)
            labels = built.pop("labels")
            summary = harvest_summary(h)
            out.update(source=f"source-map oracle (harvest: {summary['page_visits']} page visits, "
                              f"{len(summary['pinned'])} chunks pinned by ETag and sha256)",
                       harvest=summary, oracle=built)
            if not labels:
                out["harvest_error"] = (f"the harvest produced no labels after {summary['page_visits']} "
                                        f"visits: {'; '.join(summary['notes']) or 'no notes'}")
        except Exception as exc:  # noqa: BLE001 - reported, then the hand-label fallback runs
            run.failures.record(FailureCode.DEP_MISSING, f"oracle harvest unavailable: {type(exc).__name__}",
                                stage="eval")
            first = str(exc).splitlines()[0][:200] if str(exc) else ""
            out["harvest_error"] = f"{type(exc).__name__}: {first}"
    if not labels:
        labels, out["source"] = _fallback_labels(run, screens, out)
        if not labels:
            return {**out, "status": "needs_labels", "scoring": None,
                    "reason": f"no oracle labels; fill eval/{HAND_TEMPLATE}, save it as eval/{HAND_LABELS}"}
        out["status"] = "fallback_hand_labels"
    else:
        out["status"] = "ok"
    out["scoring"] = _score(run, labels, screens, links, index)
    return out


def _fallback_labels(run: RunContext, screens: list[ScreenState],
                     out: dict[str, Any]) -> tuple[list[Label], str]:
    given = run.run_dir / "eval" / HAND_LABELS
    if given.exists():
        labels = load_hand_labels(given)
        if labels:
            return labels, f"hand labels (eval/{HAND_LABELS}, {len(labels)} labelled)"
    template = hand_label_template(screens)
    run.path("eval", HAND_TEMPLATE).write_text(
        json.dumps(template, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    out["template"] = {"path": f"eval/{HAND_TEMPLATE}", "entries": len(template)}
    return [], "none"
