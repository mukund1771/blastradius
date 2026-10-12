"""Regression tests for the QA-lead report defects on PR #6645 (handoff T2)."""

from __future__ import annotations

from pathlib import Path

from test_reason_support import new_run, sample_report

from blastradius.models import Band
from blastradius.reason.assemble import _controls, _listed_control, _strong_direct, spec_requirement_ids
from blastradius.reason.facts import Hit, Route
from blastradius.reason.narrate import template_checklist
from blastradius.reason.thresholds import load_thresholds

TH = load_thresholds()
REASON = Path(__file__).resolve().parents[1] / "src" / "blastradius" / "reason"


def _hit(name: str, conf: float, files: list[str], *, direction: str = "self", hops: int = 0) -> Hit:
    return Hit(
        screen_id="s", element_id=f"el_{name}", element_role="button", element_name=name, impl_files=files,
        routes=[Route(seed=files[0], direction=direction, hops=hops, file=files[0], impl_confidence=conf,
                      confidence=conf, admitted=True)])


def test_owner_file_is_not_read_as_evidence():
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(REASON.glob("*.py")))
    assert "owner_file" not in text


def test_checklist_skips_possible_and_caps_indirect_likely(tmp_path: Path):
    report, asm, _ = sample_report(tmp_path)
    report.indirect = [
        report.indirect[0].model_copy(update={"id": f"itm_{i:012x}", "band": Band.LIKELY, "label": f"Page {i}"})
        for i in range(4)
    ] + [report.indirect[1]]
    lines = template_checklist(report, asm, TH)
    cited = {i for line in lines for i in line.item_ids}
    bands = {item.id: item.band for item in [*report.direct, *report.indirect]}
    assert all(bands[i] in (Band.CONFIRMED, Band.LIKELY) for i in cited if i in bands)
    indirect_lines = [line for line in lines if any(i.startswith("itm_") and bands.get(i) is Band.LIKELY
                                                     and i != report.direct[0].id for i in line.item_ids)]
    assert len(indirect_lines) <= 2
    assert all("behaves as before" not in line.text and line.text.count(" and ") == 0 for line in lines
               if line.item_ids == [report.direct[0].id])
    direct = next(line for line in lines if report.direct[0].id in line.item_ids)
    assert "Home page" in direct.text and "Select environment" in direct.text
    assert direct.text.count("click") == 1


def test_weak_shared_controls_are_not_listed():
    strong = _hit("Environment Quick Peek", 0.9, ["Selector.vue"])
    weak = _hit("Add new", 0.3, [f"f{i}.vue" for i in range(18)])
    assert _listed_control(strong) and not _listed_control(weak)
    listed = " ".join(_controls([strong, weak]))
    assert "Environment Quick Peek" in listed and "Add new" not in listed
    assert "one of 18" not in listed


def test_direct_is_hop_zero_on_a_changed_file_not_an_importer():
    own = _hit("Save", 0.9, ["Editor.vue"])
    importer = _hit("Wiki", 0.9, ["index.vue"], hops=2)
    shared = _hit("Cancel", 0.3, [f"f{i}.vue" for i in range(36)])
    assert _strong_direct(own, own.routes[0])
    assert not _strong_direct(importer, importer.routes[0])
    assert not _strong_direct(shared, shared.routes[0])


def test_a_run_with_no_spec_cites_no_requirements(tmp_path: Path):
    run = new_run(tmp_path, "empty-spec")
    assert spec_requirement_ids(run) == set()
    (run.run_dir / "spec").mkdir()
    (run.run_dir / "spec" / "requirements.json").write_text("[]\n", encoding="utf-8")
    assert spec_requirement_ids(run) == set()


class _Store:
    def __init__(self, rows: list[dict], realizations: list[dict] | None = None):
        self.rows = rows
        self.realizations = realizations or []

    def read(self, name: str, **kwargs):
        ids = set(kwargs.get("requirement_ids") or [])
        if name == "requirements_by_id":
            return [r for r in self.rows if r["requirement_id"] in ids]
        if name == "requirement_realizations":
            return [r for r in self.realizations if r["requirement_id"] in ids]
        if name == "absence_near":
            return []
        raise AssertionError(name)


def _req_row(rid: str, text: str, section: str) -> dict:
    return {"requirement_id": rid, "text": text, "quote": f"quote of {rid}", "section": section}


def _assemble_reqs(tmp_path: Path, hits: list[Hit], rows: list[dict], *,
                   realizations: list[dict] | None = None, seeds: dict | None = None,
                   context: set[str] | None = None):
    from blastradius.reason.assemble import build_requirements
    from blastradius.reason.facts import Retrieval
    from blastradius.reason.pr import PRData

    run = new_run(tmp_path, "req-filter")
    (run.run_dir / "spec").mkdir()
    spec = [{"id": r["requirement_id"]} for r in rows]
    (run.run_dir / "spec" / "requirements.json").write_text(
        __import__("json").dumps(spec), encoding="utf-8")
    for row in rows:
        run.evidence.add("link.bm25_judge", row["requirement_id"], "test link")
    pr = PRData(number=1, repo="hoppscotch/hoppscotch", title="edit", base_sha="a" * 40, head_sha="b" * 40)
    ordered, _, _, held = build_requirements(
        run, _Store(rows, realizations), pr, Retrieval(hits=hits), seeds or {}, None, [], [],
        context if context is not None else {"environment"})
    return {r.requirement_id: r for r in ordered}, held


def test_a_weaker_hop_zero_route_still_lists_the_requirement(tmp_path: Path):
    """A shared-code route at 0.60 must not hide a hop-0 route at 0.30 on the same control."""
    hit = Hit(
        screen_id="s", element_id="el_save", element_role="button", element_name="Save", impl_files=["Editor.vue"],
        routes=[
            Route(seed="Editor.vue", direction="self", hops=0, file="Editor.vue", confidence=0.30, admitted=True),
            Route(seed="shared.ts", direction="reverse", hops=1, file="Editor.vue", confidence=0.60, admitted=True),
        ],
        requirements=[{"id": "req_hop0kept01", "confidence": 1.0, "via": "element", "evidence_id": "ev_x"}])
    by, held = _assemble_reqs(
        tmp_path, [hit],
        [_req_row("req_hop0kept01", "User can generate client code snippets", "Code generation")])
    assert "req_hop0kept01" in by and held == 0


def test_a_flow_requirement_needs_a_word_in_common_even_on_a_hop_zero_step(tmp_path: Path):
    hit = Hit(
        screen_id="s", element_id="el_peek", element_role="button", element_name="Environment Quick Peek",
        impl_files=["Peek.vue"],
        routes=[Route(seed="Peek.vue", direction="self", hops=0, file="Peek.vue", confidence=0.9, admitted=True)],
        requirements=[
            {"id": "req_flowunrel1", "confidence": 0.8, "via": "flow", "evidence_id": "ev_x"},
            {"id": "req_flowrelat1", "confidence": 0.8, "via": "flow", "evidence_id": "ev_x"},
        ])
    by, held = _assemble_reqs(tmp_path, [hit], [
        _req_row("req_flowunrel1", "User can create a sub-collection by clicking Add new", "Collections"),
        _req_row("req_flowrelat1", "User can rename an environment from the sidebar", "Environments"),
    ])
    assert "req_flowunrel1" not in by and held == 1
    assert by["req_flowrelat1"].status == "at_risk"


def test_a_removed_control_stays_listed_when_the_wording_does_not_overlap(tmp_path: Path):
    from blastradius.models import FileClass
    from blastradius.reason.change import ChangeKind
    from blastradius.reason.facts import SeedInfo

    path = "packages/app/Selector.vue"
    hit = Hit(
        screen_id="s", element_id="el_selector", element_role="button", element_name="Selector",
        impl_files=[path],
        routes=[Route(seed=path, direction="reverse", hops=1, file=path, confidence=0.6, admitted=True)],
        requirements=[{"id": "req_removed001", "confidence": 0.8, "via": "element", "evidence_id": "ev_x"}])
    seed = SeedInfo(path=path, display_path=path, file_class=FileClass.COMPONENT, status="removed",
                    kind=ChangeKind.DELETE)
    by, held = _assemble_reqs(
        tmp_path, [hit],
        [_req_row("req_removed001", "User can generate client code snippets", "Code generation")],
        realizations=[{"requirement_id": "req_removed001", "target_kind": "UIElement", "target_id": "el_selector"}],
        seeds={path: seed}, context=set())
    assert held == 0
    assert by["req_removed001"].status == "loses_only_realization"
