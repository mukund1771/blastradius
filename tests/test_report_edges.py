"""Edge cases of the report writers and lint that a mutation run showed nothing else pinned down.

Each test names the behaviour a one-token change (a flipped comparison, an off-by-one, a dropped branch, an
unsorted iteration) used to leave unnoticed. They sit beside the golden files of test_report_render and
test_report_lint, which pin the exact text; these pin the decisions behind it.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
import sys
from pathlib import Path

import pytest
from report_helpers import (
    appendix_of,
    body_of,
    claim,
    edge_evidence,
    edge_report,
    item,
    minimal,
    requirement,
    sample_run,
    undetermined,
)

from blastradius.models import Band
from blastradius.report import lint as lint_mod
from blastradius.report import view as view_mod
from blastradius.report.escape import html_attr, html_text, md_code, md_text, sentence
from blastradius.report.lint import LintIssue, lint_html, lint_model, lint_rendered
from blastradius.report.md import render_markdown
from blastradius.report.model import Claim, SnapshotInfo
from blastradius.report.page import render_html
from blastradius.report.render import render
from blastradius.report.vocab import (
    BAND_ALLOWED,
    BAND_FORBIDDEN,
    BAND_RANGE,
    BAND_RANK,
    BAND_WORD,
    HEADLINE_MAX_LINES,
    MAIN_BODY_WORD_BUDGET,
    MIGRATION_STEPS,
    SECTION_TITLES,
    STATUS_RANK,
    STATUS_WORD,
    TIER2_CAP,
    UNDETERMINED_TOP,
    has_html_tag,
    verb_violations,
)

REPO = Path(__file__).resolve().parents[1]


def rules(issues) -> list[str]:
    return [i.rule for i in issues]


# --------------------------------------------------------------------------------------------- escape
def test_a_sentence_gets_a_full_stop_unless_it_already_ends_one():
    assert sentence("") == "" and sentence("   ") == ""
    assert sentence("Open it") == "Open it."
    assert [sentence(t) for t in ("Done.", "Done!", "Done?", "Why:")] == ["Done.", "Done!", "Done?", "Why:"]
    assert sentence("  padded  ") == "padded."


def test_a_code_span_is_padded_when_the_text_starts_or_ends_with_a_backtick():
    assert md_code("`x") == "`` `x ``"
    assert md_code("x`") == "`` x` ``"
    assert md_code("a`b") == "``a`b``"


def test_html_text_keeps_quotes_and_html_attr_escapes_them():
    assert html_text("a \"q\" <b> & 'c'") == "a \"q\" &lt;b&gt; &amp; 'c'"
    assert html_attr("a \"q\" <b> & 'c'") == "a &quot;q&quot; &lt;b&gt; &amp; &#x27;c&#x27;"


def test_line_start_markers_are_escaped_only_at_the_start():
    assert md_text("- item") == "\\- item" and md_text("+ item") == "\\+ item" and md_text("= x") == "\\= x"
    assert md_text("a - b") == "a - b" and md_text("12) go") == "12\\) go"


# ------------------------------------------------------------------------------------- lint plumbing
def test_issue_text_has_a_location_only_when_there_is_one():
    plain = LintIssue(rule="r", severity="warning", message="m")
    placed = LintIssue(rule="r", severity="error", message="m", where="here")
    assert str(plain) == "WARNING r: m" and str(placed) == "ERROR r [here]: m"
    with pytest.raises(Exception):  # noqa: B017 - pydantic's frozen-instance error
        plain.rule = "other"  # type: ignore[misc]


def test_dedupe_keys_on_rule_and_location_and_falls_back_to_the_message():
    a = LintIssue(rule="r", severity="error", message="one", where="x")
    same_place = LintIssue(rule="r", severity="error", message="two", where="x")
    no_place_1 = LintIssue(rule="r", severity="error", message="first")
    no_place_2 = LintIssue(rule="r", severity="error", message="second")
    assert lint_mod._dedupe([a, same_place, no_place_1, no_place_2, no_place_1]) == [a, no_place_1, no_place_2]


def test_the_excerpt_keeps_sixty_characters_and_marks_the_cut():
    assert lint_mod._excerpt("x" * 60) == "x" * 60
    cut = lint_mod._excerpt("x" * 61)
    assert cut == "x" * 59 + "…" and len(cut) == 60
    assert lint_mod._excerpt("a [^3] \\*b\\*") == "a  *b*"


def test_the_headline_block_of_the_markdown_is_held_to_the_body_rules():
    # regression: the level-1 block (risk line, confidence, note) was skipped by the markdown hygiene, so only
    # the HTML pass saw an id or a path there
    md = render_markdown(minimal(headline='Blast radius: PR #7 "x"\nRisk: LOW. See ev_0123456789ab in Editor.vue.'))
    found = rules(lint_rendered(md))
    assert "raw-id" in found and "file-path" in found
    md = render_markdown(minimal(pr_title="fix ev_0123456789ab in Editor.vue"))
    assert "raw-id" not in rules(lint_rendered(md))              # the title quotes the PR title, which is not ours
    note = render_markdown(minimal(confidence_line="Confidence: see packages/x/y.ts."))
    assert "file-path" in rules(lint_rendered(note))


def test_headings_inside_a_code_fence_are_not_sections():
    good = render_markdown(minimal())
    fenced = good + "\n```\n## Not a section\n```\n"
    assert "section-unexpected" not in rules(lint_rendered(fenced))
    unfenced = good + "\n## Not a section\n"
    assert "section-unexpected" in rules(lint_rendered(unfenced))
    tilde = good + "\n~~~\n## Not a section\n~~~\n"
    assert "section-unexpected" not in rules(lint_rendered(tilde))


def test_table_rows_are_read_with_or_without_spaces_and_a_closing_pipe():
    base = "# T\n\n**Risk: LOW.** x\n\n## Directly changed\n"
    compact = "|Screen|What changed|How sure|\n|---|---|---|\n|**A**|It is changed by this PR. [^1]|Confirmed|\n"
    open_end = "| Screen | What changed | How sure |\n|---|---|---|\n| **A** | It is changed by this PR. [^1] | Confirmed\n"
    two = compact + "\ntext\n\n" + compact
    for table in (compact, open_end, two):
        assert "band-word" not in rules(lint_rendered(base + table)), table
    bad = "|Screen|What changed|How sure|\n|---|---|---|\n|**A**|It is changed by this PR. [^1]|Dunno|\n"
    assert rules(lint_rendered(base + bad)).count("band-word") == 1
    both = bad + "\ntext\n\n" + bad
    assert rules(lint_rendered(base + both)).count("band-word") == 1       # the same row twice is one issue
    first_data_row = "|Screen|What changed|How sure|\n|---|---|---|\n|**A**|x [^1]|Dunno|\n|**B**|y [^1]|Dunno|\n"
    assert len([i for i in lint_rendered(base + first_data_row) if i.rule == "band-word"]) == 2


def test_a_headline_level_one_could_not_determine_section_still_counts():
    md = render_markdown(minimal())
    title = "## What we could not determine, and who should look"
    assert title in md
    as_h1 = md.replace(title, "# What we could not determine, and who should look")
    assert "undetermined-section" not in rules(lint_rendered(as_h1))
    assert "undetermined-weight" not in rules(lint_rendered(as_h1))
    findings_h1 = md.replace("## Directly changed", "# Directly changed")
    assert "undetermined-weight" in rules(lint_rendered(findings_h1))


def test_the_word_budget_is_exactly_the_budget():
    def words_flagged(n: int) -> list[LintIssue]:
        md = render_markdown(minimal(confidence_line=" ".join(["word"] * n)))
        return [i for i in lint_rendered(md) if i.rule == "word-budget"]

    first = next(n for n in range(1, MAIN_BODY_WORD_BUDGET + 40) if words_flagged(n))
    assert f"has {MAIN_BODY_WORD_BUDGET + 1} words" in words_flagged(first)[0].message
    assert not words_flagged(first - 1)


def test_unresolved_footnotes_are_reported_in_numeric_order():
    base = render_markdown(minimal())
    md = base.replace("## What to re-test first", "## What to re-test first\n\nSee [^10], [^2], [^33] and [^1].")
    flagged = [i.where for i in lint_rendered(md) if i.rule == "footnote-unresolved"]
    assert [w for w in flagged if w != "[^1]"] == ["[^2]", "[^10]", "[^33]"]


def test_caps_warn_only_above_the_cap_and_the_headline_may_be_three_lines():
    at_cap = [item(f"c{n}", tier="indirect", label=f"S{n}", screen=f"P{n}", band=Band.LIKELY,
                   what="This screen is probably affected.", section="S") for n in range(TIER2_CAP)]
    assert "tier2-cap" not in rules(lint_model(minimal(indirect=at_cap)))
    assert "tier2-cap" in rules(lint_model(minimal(indirect=[*at_cap, item("c+", tier="indirect", band=Band.LIKELY,
                                                                           what="Likely changes.")])))
    exact = [undetermined(subject=f"S{n}") for n in range(UNDETERMINED_TOP)]
    assert "undetermined-top" not in rules(lint_model(minimal(undetermined=exact)))
    three = 'Blast radius: PR #7 "x"\nRisk: LOW. One.\nTwo.'
    assert HEADLINE_MAX_LINES == 3
    assert "headline-lines" not in rules(lint_model(minimal(headline=three)))
    assert "headline-lines" in rules(lint_model(minimal(headline=three + "\nFour.")))


def test_multi_valued_issues_come_out_in_a_fixed_order():
    twins = [item(f"t{k}", label=f"L{k}", evidence=("ev_aaaaaaaaaaaa",)) for k in (3, 1, 2)]
    dup = [*twins, *[item(f"t{k}", label=f"M{k}") for k in (3, 1, 2)]]
    flagged = [i.where for i in lint_model(minimal(direct=dup)) if i.rule == "duplicate-id"]
    assert len(flagged) == 3 and flagged == sorted(flagged)
    unknown = [item(f"u{k}", label=f"U{k}", evidence=(f"ev_{k:012x}",)) for k in (9, 4, 7, 1)]
    cited = lint_model(minimal(direct=unknown), known_evidence={"ev_aaaaaaaaaaaa"})
    owners = [i.where for i in cited if i.rule == "evidence-unknown"]
    assert len(owners) == 4 and owners == sorted(owners)
    mixed = minimal(risk_level="MEDIUM", headline='Blast radius: PR #7 "x"\nRisk: LOW. Risk: HIGH. Risk: low.')
    message = next(i.message for i in lint_model(mixed) if i.rule == "risk-consistency")
    assert "['HIGH', 'LOW']" in message


def test_a_claim_prefix_is_stripped_by_label_or_screen_and_the_longest_first():
    shown = "The sidebar will change."
    by_screen = minimal(
        direct=[item("a", label="Sidebar", screen="Home page", what=shown, evidence=())],
        claims=[claim(f"Home page: {shown}", Band.LIKELY, ("ev_aaaaaaaaaaaa",))])
    assert "band-verb" not in rules(lint_model(by_screen))
    by_label = minimal(
        direct=[item("a", label="Sidebar", screen="Home page", what=shown, evidence=())],
        claims=[claim(f"Sidebar: {shown}", Band.LIKELY, ("ev_aaaaaaaaaaaa",))])
    assert "band-verb" not in rules(lint_model(by_label))
    # "Edit" is a label and also the start of the label "Edit: advanced": the longer prefix must win
    sentence_ = "The advanced editor is changed by this PR."
    nested = minimal(
        direct=[item("a", label="Edit", what="Plain.", evidence=()),
                item("b", label="Edit: advanced", what=sentence_, evidence=())],
        claims=[claim(f"Edit: advanced: {sentence_}", Band.LIKELY, ("ev_aaaaaaaaaaaa",))])
    assert "band-verb" not in rules(lint_model(nested))
    stray = minimal(direct=[item("a", label="Sidebar", what="Plain.", evidence=())],
                    claims=[claim(f"Sidebar: {shown}", Band.LIKELY, ("ev_aaaaaaaaaaaa",))])
    assert "band-verb" in rules(lint_model(stray))              # a claim that no row shows is checked as written


def test_long_matches_are_cut_in_the_messages_but_not_in_the_rule():
    tag = '<div class="' + "a" * 60 + '">'
    md = render_markdown(minimal()) + f"\n{tag}\n"
    issue = next(i for i in lint_rendered(md) if i.rule == "html-in-text")
    assert issue.where == tag[:40] and len(issue.where) == 40
    page = render_html(minimal()).replace("</main>", "<a href=" + " " * 70 + '"https://evil.example"></a></main>')
    issue = next(i for i in lint_html(page) if i.rule == "remote-url")
    assert len(issue.where) == 50
    long_claim = "This claim will change and keep changing until the end of the run, perhaps."
    only = minimal(claims=[claim(long_claim, Band.CONFIRMED)])
    flagged = next(i for i in lint_model(only) if i.rule == "band-verb")
    assert flagged.message.endswith(long_claim[:50]) and long_claim[:51] not in flagged.message
    said = "This screen will change and keep changing until the end of the run."
    cut = [item(f"o{n}", tier="indirect", label=f"S{n}", screen=f"P{n}", band=Band.POSSIBLE, section="S", what=said)
           for n in range(TIER2_CAP + 1)]
    entry = next(i for i in lint_model(minimal(indirect=cut)) if i.where.startswith("entry"))
    assert entry.message.endswith(said[:50]) and said[:51] not in entry.message
    field = minimal(headline="Blast radius: PR #7\nRisk: LOW. <i>" + "x" * 80 + "</i>")
    html = next(i for i in lint_model(field) if i.rule == "html-in-field")
    assert html.message.split("field: ")[1] == repr(field.headline[:40])


# ----------------------------------------------------------------------------------------- vocabulary
def test_the_vocabulary_is_the_one_the_spec_names():
    assert SECTION_TITLES == {
        "checklist": "What to re-test first", "direct": "Directly changed",
        "indirect": "Indirect, through shared code", "global": "Global change", "stored_data": "Stored data",
        "requirements": "Requirements at risk",
        "undetermined": "What we could not determine, and who should look",
        "appendix": "Appendix: evidence"}
    assert STATUS_WORD == {"loses_only_realization": "Loses its only realization", "at_risk": "At risk",
                           "cannot_verify": "Cannot verify", "may_close_gap": "This PR may close a gap"}
    assert list(STATUS_RANK) == ["loses_only_realization", "at_risk", "cannot_verify", "may_close_gap"]
    assert [STATUS_RANK[k] for k in STATUS_RANK] == [0, 1, 2, 3]
    assert BAND_RANK == {Band.CONFIRMED: 0, Band.LIKELY: 1, Band.POSSIBLE: 2, Band.UNKNOWN: 3}
    assert BAND_WORD == {Band.CONFIRMED: "confirmed", Band.LIKELY: "likely", Band.POSSIBLE: "possible",
                         Band.UNKNOWN: "unknown"}
    assert BAND_RANGE == {Band.CONFIRMED: ">= 0.8", Band.LIKELY: "0.5 to 0.8", Band.POSSIBLE: "0.25 to 0.5",
                          Band.UNKNOWN: "< 0.25"}
    assert BAND_ALLOWED == {
        Band.CONFIRMED: ("will change", "is changed by this PR"), Band.LIKELY: ("probably affected", "likely changes"),
        Band.POSSIBLE: ("might be affected", "worth a quick look"), Band.UNKNOWN: ("we cannot tell", "not determined")}
    assert len(MIGRATION_STEPS) == 3 and all(s.endswith(".") for s in MIGRATION_STEPS)
    assert MIGRATION_STEPS[0].startswith("Export your collections") and "cloud sync" in MIGRATION_STEPS[1]
    assert MIGRATION_STEPS[2].startswith("Open data saved by the previous build")


@pytest.mark.parametrize("band", list(Band))
def test_every_forbidden_phrase_is_flagged_for_its_band_and_only_that_band_s_list(band):
    table = {
        Band.CONFIRMED: ("might", "could", "may", "possibly", "perhaps", "maybe", "probably", "likely", "seems to",
                         "appears to"),
        Band.LIKELY: ("will", "is certain", "certainly", "definitely", "guaranteed", "is changed by this PR",
                      "confirmed"),
        Band.POSSIBLE: ("will", "probably", "likely", "is certain", "certainly", "definitely", "confirmed",
                        "is changed by this PR"),
        Band.UNKNOWN: ("will", "would", "probably", "likely", "certainly", "definitely", "confirmed",
                       "might be affected", "worth a quick look", "is changed by this PR"),
    }
    assert BAND_FORBIDDEN[band] == table[band]
    for phrase in table[band]:
        assert phrase in verb_violations(f"The sidebar {phrase} here.", band), phrase
        assert phrase in verb_violations(f"The sidebar {phrase.upper()} here.", band), phrase


def test_verb_violations_come_back_sorted_and_unique():
    text = "It will, would, could, might, may, probably, likely, certainly, definitely, confirmed, will again."
    for band in Band:
        got = verb_violations(text, band)
        assert got == sorted(set(got))
    assert len(verb_violations(text, Band.UNKNOWN)) >= 6


def test_html_tag_detection_is_a_boolean():
    assert has_html_tag("a <b>x</b>") is True and has_html_tag("a < b and c > d") is False


# ------------------------------------------------------------------------------------------- the view
def _view(report=None, evidence=None):
    return view_mod.build_view(report or edge_report(), edge_evidence() if report is None else evidence)


def test_the_view_is_made_of_immutable_records():
    v = _view()
    samples = [v, v.direct[0], v.indirect[0], v.indirect[0].rows[0], v.checklist[0], v.requirements[0],
               v.undetermined[0], v.units[0], v.units[0].evidence[0]]
    assert {type(s).__name__ for s in samples} == {"View", "FindingRow", "Group", "CheckRow", "RequirementRow",
                                                   "UndeterminedRow", "Unit", "EvidenceView"}
    for sample in samples:
        field = dataclasses.fields(sample)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(sample, field, "changed")


def test_a_row_names_its_screen_only_when_it_differs_from_the_label_ignoring_case():
    r = minimal(direct=[item("a", label="Quick peek", screen="quick PEEK"),
                        item("b", label="Quick peek two", screen="Quick peek"),
                        item("c", label="", screen="Only screen"), item("d", label="", screen=None)])
    rows = {row.title: row.where for row in _view(r, {}).direct}
    assert rows == {"Quick peek": None, "Quick peek two": "Quick peek", "Only screen": None, "(unnamed)": None}
    md = render_markdown(r)
    assert "**(unnamed)**" in md and "(on quick PEEK)" not in md
    units = {u.title for u in _view(r, {}).units}
    assert {"Quick peek", "Quick peek two (on Quick peek)", "Only screen", "(unnamed)"} <= units


def test_a_requirement_without_a_section_is_titled_requirement():
    r = minimal(requirements=[requirement(section="")])
    assert [u.title for u in _view(r, {}).units if u.section == "requirements"] == ["Requirement"]


def test_snapshot_counts_use_the_singular_and_skip_what_is_unknown():
    def fields(**kw):
        snap = SnapshotInfo(target="t", repo="r", indexed_commit="c", **kw)
        return {k: v for k, v, _ in _view(minimal(snapshot=snap), {}).snapshot}

    assert "Crawl size" not in fields()
    assert fields(crawl_actions=1)["Crawl size"] == "1 action"
    assert fields(crawl_actions=3)["Crawl size"] == "3 actions"
    assert fields(crawl_screens=1)["Crawl size"] == "1 screen"
    assert fields(crawl_screens=2)["Crawl size"] == "2 screens"
    assert fields(crawl_actions=0, crawl_screens=0)["Crawl size"] == "0 actions and 0 screens"
    assert fields(crawl_actions=2, crawl_screens=1)["Crawl size"] == "2 actions and 1 screen"
    assert "PR base behind main" not in fields()
    assert fields(base_behind_main_commits=0)["PR base behind main"] == "0 commits"
    assert fields(base_behind_main_commits=1)["PR base behind main"] == "1 commit"
    flags = {k: code for k, _, code in _view(edge_report(), edge_evidence()).snapshot}
    assert flags == {"Target": True, "Repository": True, "Indexed commit": True, "PR base": True, "PR head": True,
                     "PR base behind main": False, "Deployed vs indexed": False, "Crawl bundle": True,
                     "Crawl size": False, "Spec sha": True, "Documentation version": False, "App version": False}


def test_the_note_names_staleness_only_when_there_is_some():
    clean_snap = SnapshotInfo(target="t", repo="r", indexed_commit="c")
    assert _view(minimal(snapshot=clean_snap), {}).note == ""
    behind = clean_snap.model_copy(update={"base_behind_main_commits": 1})
    assert _view(minimal(snapshot=behind), {}).note == (
        "The PR branch is 1 commit behind main, so its diff may not apply as shown.")
    zero = clean_snap.model_copy(update={"base_behind_main_commits": 0})
    assert _view(minimal(snapshot=zero), {}).note == ""
    both = clean_snap.model_copy(update={"base_behind_main_commits": 3, "deployed_vs_indexed": "2 of 9 files differ"})
    assert _view(minimal(snapshot=both), {}).note == (
        "Deployed app compared with the indexed code: 2 of 9 files differ. The PR branch is 3 commits behind main, "
        "so its diff may not apply as shown.")


def test_an_empty_headline_does_not_break_the_view():
    v = _view(minimal(headline=""), {})
    assert v.summary == ""
    assert render_markdown(minimal(headline="")).splitlines()[2] == "**Risk: LOW.**"
    only_title = minimal(headline='Blast radius: PR #7 "x"')
    assert _view(only_title, {}).summary == ""


def test_footnote_markers_of_a_checklist_line_are_sorted_numbers():
    items = [item(f"n{k:02d}", label=f"Item {k:02d}", what=f"Item {k:02d} is changed by this PR.",
                  evidence=(f"ev_{k:012x}",)) for k in range(1, 13)]
    ids = {k: items[k - 1].id for k in range(1, 13)}
    from blastradius.report.model import ChecklistItem
    line = ChecklistItem(text="Do it.", why="because.", item_ids=[ids[9], ids[3], ids[12], ids[3], "itm_unknown"])
    r = minimal(direct=items, checklist=[line])
    assert _view(r, {}).checklist[0].markers == (3, 9, 12)
    assert "- [ ] Do it. Why: because. [^3][^9][^12]" in render_markdown(r)
    assert 'href="#ev-3"' in render_html(r).split('id="s-checklist"')[1].split("</section>")[0]


def test_a_checklist_line_without_evidence_gets_no_markers():
    from blastradius.report.model import ChecklistItem
    bare = item("bare", evidence=())
    r = minimal(direct=[bare], checklist=[ChecklistItem(text="Do it.", why="because.", item_ids=[bare.id])])
    assert _view(r, {}).checklist[0].markers == ()


def test_equal_strength_items_with_no_section_are_cut_first():
    same = [item(f"s{n:02d}", tier="indirect", label=f"Zed {n:02d}", screen=None, band=Band.LIKELY, confidence=0.6,
                 what=f"Zed {n:02d} is probably affected.", section="Things") for n in range(TIER2_CAP)]
    loner = item("loner", tier="indirect", label="Aaa loner", screen=None, band=Band.LIKELY, confidence=0.6,
                 what="Aaa loner is probably affected.", section=None)
    md = render_markdown(minimal(indirect=[loner, *same]))
    assert "Aaa loner" not in body_of(md) and "Aaa loner" in appendix_of(md)
    assert "Showing the 10 strongest of 11" in md


def test_global_and_stored_rows_are_listed_strongest_first():
    low = item("g1", tier="global", label="Low global", screen=None, confidence=0.81)
    high = item("g2", tier="global", label="High global", screen=None, confidence=0.95)
    mid_b = item("s1", tier="stored_data", label="B saved", screen=None, confidence=0.9)
    mid_a = item("s2", tier="stored_data", label="A saved", screen=None, confidence=0.9)
    top = item("s3", tier="stored_data", label="Top saved", screen=None, confidence=0.99)
    v = _view(minimal(global_changes=[low, high], stored_data=[mid_b, mid_a, top]), {})
    assert [r.title for r in v.global_rows] == ["High global", "Low global"]
    assert [r.title for r in v.stored_rows] == ["Top saved", "A saved", "B saved"]


def test_at_most_five_migration_steps_are_shown():
    from blastradius.report.model import ChecklistItem
    stored = item("s", tier="stored_data", label="Saved data", screen=None)
    steps = [ChecklistItem(text=f"Step {n}.", why="x", item_ids=[stored.id]) for n in range(7)]
    steps.append(ChecklistItem(text="Not a stored-data step.", why="x", item_ids=[]))
    assert _view(minimal(stored_data=[stored], checklist=steps), {}).migration_steps == tuple(
        f"Step {n}." for n in range(5))
    assert _view(minimal(stored_data=[stored]), {}).migration_steps == MIGRATION_STEPS


def test_the_lowest_numbered_matching_claim_is_the_one_linked():
    text = "The sidebar is changed by this PR."
    a = Claim(id="clm_aaaaaaaaaaaa", text=f"Environments sidebar: {text}", evidence_ids=["ev_aaaaaaaaaaaa"],
              band=Band.CONFIRMED)
    b = Claim(id="clm_bbbbbbbbbbbb", text=f"Environments sidebar: {text}", evidence_ids=["ev_aaaaaaaaaaaa"],
              band=Band.CONFIRMED)
    for order in ([a, b], [b, a]):
        v = _view(minimal(claims=order), {})
        assert [u.claim_id for u in v.units if u.section == "direct"] == ["clm_aaaaaaaaaaaa"]


def test_a_claim_matches_an_item_by_text_or_by_the_same_band_and_cited_evidence_only():
    own = ("ev_1000000000aa", "ev_1000000000bb")
    row = item("m", what="The sidebar is changed by this PR.", evidence=own)
    claims = [
        Claim(id="clm_000000000001", text="Reworded.", evidence_ids=[own[0]], band=Band.CONFIRMED),        # subset
        Claim(id="clm_000000000002", text="Other band.", evidence_ids=[own[0]], band=Band.LIKELY),         # band
        Claim(id="clm_000000000003", text="Extra evidence.", evidence_ids=[*own, "ev_1000000000cc"],
              band=Band.CONFIRMED),                                                                         # more
        Claim(id="clm_000000000004", text="No evidence.", evidence_ids=[], band=Band.CONFIRMED),
        Claim(id="clm_000000000005", text="Environments sidebar: The sidebar is changed by this PR.",
              evidence_ids=["ev_1000000000dd"], band=Band.LIKELY),                                          # text
    ]
    v = _view(minimal(direct=[row], claims=claims), {})
    unit = next(u for u in v.units if u.section == "direct")
    assert unit.claim_id == "clm_000000000001"
    orphans = sorted(u.claim_id for u in v.units if u.section == "claim")
    assert orphans == ["clm_000000000002", "clm_000000000003", "clm_000000000004"]
    assert {e.id for e in unit.evidence} == {*own, "ev_1000000000dd"}


def test_a_requirement_claim_may_quote_the_statement():
    req = requirement(statement="User can do the thing.", evidence_ids=["ev_bbbbbbbbbbbb"])
    quoted = Claim(id="clm_000000000001", text="“User can do the thing.”: This requirement rests on a screen.",
                   evidence_ids=["ev_cccccccccccc"], band=Band.LIKELY)
    v = _view(minimal(requirements=[req], claims=[quoted]), {})
    unit = next(u for u in v.units if u.section == "requirements")
    assert unit.claim_id == quoted.id and not [u for u in v.units if u.section == "claim"]


def test_could_not_determine_rows_that_differ_only_in_evidence_order_are_one_entry():
    a = undetermined(subject="Same", evidence_ids=["ev_aaaaaaaaaaaa", "ev_bbbbbbbbbbbb"])
    b = undetermined(subject="Same", evidence_ids=["ev_bbbbbbbbbbbb", "ev_aaaaaaaaaaaa"])
    v = _view(minimal(undetermined=[a, b]), {})
    assert [r.marker for r in v.undetermined] == [v.undetermined[0].marker] * 2
    assert len([u for u in v.units if u.section == "undetermined"]) == 1


def test_an_orphan_claim_without_evidence_is_still_listed():
    v = _view(minimal(claims=[claim("Nobody shows this.", evidence=())]), {})
    assert [u.section for u in v.units if u.claim_id] == ["claim"]
    assert "No evidence was cited for this statement." in render_markdown(minimal(
        claims=[claim("Nobody shows this.", evidence=())]))


def test_requirements_come_out_by_status_then_band_then_section_then_id():
    reqs = [
        requirement(requirement_id="req_000000000009", status="at_risk", band=Band.LIKELY, section="B"),
        requirement(requirement_id="req_000000000003", status="at_risk", band=Band.LIKELY, section="a"),
        requirement(requirement_id="req_000000000001", status="at_risk", band=Band.CONFIRMED, section="Z"),
        requirement(requirement_id="req_000000000002", status="loses_only_realization", band=Band.POSSIBLE,
                    section="Z"),
        requirement(requirement_id="req_000000000008", status="may_close_gap", band=Band.CONFIRMED, section="A"),
        requirement(requirement_id="req_000000000004", status="cannot_verify", band=Band.UNKNOWN, section="A"),
        requirement(requirement_id="req_000000000007", status="at_risk", band=Band.LIKELY, section="a"),
    ]
    shown = _view(minimal(requirements=reqs), {}).requirements
    order = [(r.status, r.section) for r in shown]
    assert order == [("Loses its only realization", "Z"), ("At risk", "Z"), ("At risk", "a"), ("At risk", "a"),
                     ("At risk", "B"), ("Cannot verify", "A"), ("This PR may close a gap", "A")]
    ids = [u.requirement_ids[0] for u in _view(minimal(requirements=reqs), {}).units if u.section == "requirements"]
    assert ids == ["req_000000000002", "req_000000000001", "req_000000000003", "req_000000000007",
                   "req_000000000009", "req_000000000004", "req_000000000008"]


# ------------------------------------------------------------------------------------------ render()
def test_render_can_write_into_the_same_directory_twice_and_reports_warning_counts(tmp_path):
    run = sample_run()
    report = minimal(confidence_line="", headline='Blast radius: PR #7 "x"\nRisk: LOW. One.\nTwo.\nThree.',
                     direct=[item("a", evidence=("ev_aaaaaaaaaaaa", "ev_0000000000ff")),
                             item("b", label="Other", what="Ev_0123456789ab is changed by this PR.",
                                  band=Band.CONFIRMED, confidence=0.3)],
                     not_checked=[])
    first = render(report, run, out_dir=tmp_path / "out")
    again = render(report, run, out_dir=tmp_path / "out")
    assert first == again
    ends = [r for r in run.tracer.records if r.get("name") == "report.render" and r["event"] == "end"]
    attrs = ends[-1]["attributes"]
    assert attrs["lint_warnings"] == 2                        # headline lines and the empty confidence line
    assert attrs["lint_rules"] == sorted(attrs["lint_rules"]) and len(attrs["lint_rules"]) >= 3
    assert attrs["units"] == len(set(re.findall(r"^\[\^(\d+)\]:", Path(first["md"]).read_text("utf-8"),
                                                re.MULTILINE)))


def test_the_package_has_no_import_time_side_effects():
    code = ("import blastradius.report.render, sys; "
            "print(sorted(m for m in sys.modules if m.startswith('playwright')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env={"PYTHONPATH": str(REPO / "src")},
                         capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stderr[-400:]
