"""evalkit.invariants: each invariant fires on its own violation and stays quiet on a clean report."""

from __future__ import annotations

import copy
import importlib.util
from typing import Any

import pytest
from test_eval_support import claim, evidence, item, report

from blastradius.evalkit.invariants import check_report

EV = set(evidence(6))


def run(data: dict[str, Any], ev: set[str] | None = None):
    return check_report(data, EV if ev is None else ev, subject="t")


def failed(data: dict[str, Any]) -> dict[str, list[str]]:
    res = run(data)
    return {r.name: list(r.offenders) for r in res.results if not (r.evaluated and r.passed)}


def test_clean_report_passes_every_invariant():
    res = run(report())
    assert res.ok and res.failed == []
    assert [r.name for r in res.results] == [
        "schema_valid",
        "citations_exist",
        "claims_cite_evidence",
        "band_verb_consistent",
        "no_ids_or_paths_in_body",
        "ids_unique",
        "counts_distinct",
    ]


def test_result_is_deterministic_and_serialisable():
    a, b = run(report()).as_dict(), run(copy.deepcopy(report())).as_dict()
    assert a == b and a["ok"] is True


# ------------------------------------------------------------------------------------------ schema
def test_extra_field_and_missing_field_fail_the_schema_and_stop_the_rest():
    bad = report(surprise=1)
    res = run(bad)
    assert not res.ok and res.failed[0] == "schema_valid"
    assert any("surprise" in o for o in res.results[0].offenders)
    assert all(not r.evaluated for r in res.results[1:])  # content invariants did not run
    missing = report()
    del missing["headline"]
    assert "schema_valid" in run(missing).failed


def test_schema_offenders_never_echo_input_values():
    bad = report(risk_level="CATASTROPHIC <script>")
    offenders = run(bad).results[0].offenders
    assert offenders and not any("CATASTROPHIC" in o for o in offenders)


# ---------------------------------------------------------------------------------------- citations
def test_cited_id_missing_from_the_ledger_is_named_with_its_location():
    data = report(claims=[claim(1, evidence=["ev_000000000001", "ev_deadbeef0000"]), claim(2)])
    out = failed(data)
    assert list(out) == ["citations_exist"]
    assert any("claims[0]" in o and "ev_deadbeef0000" in o for o in out["citations_exist"])


def test_a_claim_or_item_citing_nothing_fails_even_when_every_cited_id_exists():
    data = report(claims=[claim(1, evidence=[]), claim(2)], direct=[item(1, evidence=[]), item(2)])
    out = failed(data)
    assert "claims_cite_evidence" in out and len(out["claims_cite_evidence"]) == 2
    assert "citations_exist" not in out


# ---------------------------------------------------------------------------------------- band/verb
@pytest.mark.parametrize(
    ("band", "text"),
    [
        ("confirmed", "The value editor might be changed by this PR."),
        ("confirmed", "The value editor could be changed by this PR."),
        ("likely", "The value editor will change and is probably affected."),
        ("possible", "The value editor will change; worth a quick look."),
        ("possible", "The value editor is probably affected."),
        ("unknown", "The value editor will change: we cannot tell."),
        ("confirmed", "The value editor is probably affected."),  # another band's wording
        ("possible", "The value editor is changed by this PR."),  # overstated
        ("unknown", "The value editor is changed by this PR."),  # a claim of effect
    ],
)
def test_wording_that_does_not_match_the_band_is_flagged(band: str, text: str):
    out = failed(report(claims=[claim(1, text, band=band), claim(2)]))
    assert "band_verb_consistent" in out and any("claims[0]" in o for o in out["band_verb_consistent"])


@pytest.mark.parametrize(
    ("band", "text"),
    [
        ("confirmed", "The value editor is changed by this PR."),
        ("confirmed", "The sidebar will change."),
        ("likely", "The selector is probably affected."),
        ("likely", "The value input likely changes."),
        ("possible", "The history list might be affected."),
        ("possible", "The history list is worth a quick look."),
        ("unknown", "We cannot tell what the footer does."),
        ("unknown", "The effect on the console is not determined."),
    ],
)
def test_wording_that_matches_the_band_passes(band: str, text: str):
    assert run(report(claims=[claim(1, text, band=band), claim(2)])).ok


def test_a_claim_with_no_band_phrase_at_all_fails():
    out = failed(report(claims=[claim(1, "Environment values were edited."), claim(2)]))
    assert any("no confirmed phrase" in o for o in out["band_verb_consistent"])


def test_quoted_page_text_is_not_our_wording():
    quoted = "“Will it blend” is changed by this PR."  # a control literally named that
    assert run(report(claims=[claim(1, quoted), claim(2)])).ok


def test_item_text_with_overstated_wording_is_flagged_but_missing_phrase_is_not():
    data = report(
        direct=[
            item(1, band="possible", what_changed="The editor will change."),
            item(2, what_changed="Edit value input."),
        ]
    )
    out = failed(data)
    assert any("item itm_000000000001" in o for o in out["band_verb_consistent"])
    assert not any("itm_000000000002" in o for o in out["band_verb_consistent"])


# --------------------------------------------------------------------------------------------- body
@pytest.mark.parametrize(
    "text",
    [
        "Changed in ev_a1b2c3d4e5f6.",
        "The file packages/hoppscotch-common/src/components/Env.vue is changed by this PR.",
        "CurrentValueInput.vue is changed by this PR.",
        "The graph shows this is changed by this PR.",
        "A closure of two hops is changed by this PR.",
        "components/environments/Editor.vue is changed by this PR.",
    ],
)
def test_ids_paths_and_graph_words_in_the_body_are_flagged(text: str):
    out = failed(report(direct=[item(1, what_changed=text), item(2)]))
    assert "no_ids_or_paths_in_body" in out
    assert any("direct[0].what_changed" in o for o in out["no_ids_or_paths_in_body"])


def test_appendix_fields_may_hold_paths_and_ids():
    data = report(
        direct=[
            item(
                1, flows=["Edit an environment"], files=["packages/hoppscotch-common/src/components/Env.vue"]
            ),
            item(2),
        ]
    )
    assert run(data).ok


def test_spec_quotes_may_mention_graphs_and_file_names_but_not_raw_ids():
    req = {
        "requirement_id": "req_000000000001",
        "statement": "User can export collection.json.",
        "quote": "Export your graph of requests as collection.json",
        "section": "Collections",
        "status": "at_risk",
        "band": "likely",
        "evidence_ids": ["ev_000000000001"],
    }
    assert run(report(requirements=[req])).ok
    req_bad = {**req, "quote": "see ev_a1b2c3d4e5f6 for more"}
    assert "no_ids_or_paths_in_body" in failed(report(requirements=[req_bad]))


def test_an_undetermined_subject_that_is_a_file_path_is_flagged():
    und = {
        "code": "UNMAPPED_CODE",
        "subject": "packages/hoppscotch-common/src/helpers/diff.ts",
        "why": "not mapped to any screen",
        "what_to_check": "Ask the author.",
        "evidence_ids": [],
    }
    out = failed(report(undetermined=[und]))
    assert any("undetermined[0].subject" in o for o in out["no_ids_or_paths_in_body"])


# ------------------------------------------------------------------------------------------ counts
def test_headline_counts_must_be_over_distinct_screens():
    same_screen = [item(i, screen="Environments sidebar") for i in range(1, 5)]
    data = report(
        direct=same_screen,
        headline="Risk: MEDIUM. Touches Environments. 4 screens.",
        claims=[claim(1)],
        checklist=[],
    )
    out = failed(data)
    assert "counts_distinct" in out and any("headline says 4 screens" in o for o in out["counts_distinct"])
    ok = report(
        direct=same_screen,
        headline="Risk: MEDIUM. Touches Environments. 1 screen.",
        claims=[claim(1)],
        checklist=[],
    )
    assert run(ok).ok


def test_headline_counts_of_flows_and_requirements_use_distinct_ids():
    data = report(
        direct=[item(1, flows=["Edit an environment"]), item(2, flows=["Edit an environment"])],
        headline="Risk: LOW. 2 screens, 2 user flows, 0 requirements.",
    )
    out = failed(data)
    assert any("headline says 2 flows" in o for o in out["counts_distinct"])


def test_one_id_for_two_rows_is_a_collision_even_when_the_rows_differ():
    """Seen on real product output: two claims about different screens hashed to one `clm_` id."""
    first = claim(
        1, "Home page: This screen might be affected by this PR; worth a quick look.", band="possible"
    )
    second = {
        **first,
        "text": "Socket.IO page: This screen might be affected by this PR; worth a quick look.",
        "evidence_ids": ["ev_000000000002"],
    }
    out = failed(report(claims=[first, second, claim(2)]))
    assert list(out) == ["ids_unique"]
    assert out["ids_unique"] == [f"claim id used for 2 rows: {first['id']}"]
    dup_items = failed(report(direct=[item(1), item(1, screen="Another screen"), item(2)]))
    assert any("item id used for 2 rows" in o for o in dup_items["ids_unique"])
    req = {
        "requirement_id": "req_000000000001",
        "statement": "S.",
        "quote": "Q.",
        "section": "X",
        "status": "at_risk",
        "band": "likely",
        "evidence_ids": ["ev_000000000001"],
    }
    assert any(
        "requirement id used for 2 rows" in o
        for o in failed(report(requirements=[req, dict(req)]))["ids_unique"]
    )


def test_the_same_screen_and_label_listed_twice_is_not_a_distinct_count():
    dup = item(1)
    data = report(
        direct=[dup, {**dup, "id": "itm_00000000000f"}], claims=[claim(1)], headline="Risk: LOW. 1 screen."
    )
    offenders = failed(data)["counts_distinct"]
    assert any("(tier, screen, label) repeated 2 times" in o for o in offenders)
    cited_twice = item(1, evidence=["ev_000000000001", "ev_000000000001"])
    assert any(
        "cites ev_000000000001 2 times" in o
        for o in failed(report(direct=[cited_twice, item(2)]))["counts_distinct"]
    )


def test_checklist_must_point_at_existing_items():
    data = report(checklist=[{"text": "Open it.", "why": "Changed.", "item_ids": ["itm_ffffffffffff"]}])
    assert any("unknown item" in o for o in failed(data)["counts_distinct"])


def test_report_without_numbers_in_the_headline_has_nothing_to_miscount():
    assert run(report(headline="Blast radius: PR #6645. Risk: MEDIUM.")).ok


def test_offenders_say_which_rule_book_raised_them():
    """The spec-literal check and the product's own rules are separate sources, so a divergence is visible."""
    out = failed(
        report(claims=[claim(1, "The editor might be changed by this PR.", band="confirmed"), claim(2)])
    )
    got = " ".join(out["band_verb_consistent"])
    assert "forbidden word 'might' for band confirmed" in got  # the spec's table, evaluated here
    if importlib.util.find_spec("blastradius.reason.verify"):
        assert "reason.verify:" in got  # the product's verifier, when it imports
    if importlib.util.find_spec("blastradius.report.vocab"):
        assert "report.vocab forbids 'might'" in got
