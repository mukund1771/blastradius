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
