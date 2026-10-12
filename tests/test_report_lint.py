"""Report lint (REPORT_SPEC "Required lint checks"; D22, D29; cases 57, 61, 62).

Every rule has a test that builds a report (or a Markdown/HTML text) that breaks exactly that rule, and a check
that a clean report does not trip it. The clean report is the hand-authored PR #6645 fixture or `minimal()`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from report_helpers import (
    claim,
    item,
    load_evidence,
    load_sample,
    minimal,
    requirement,
    undetermined,
)

from blastradius.models import Band
from blastradius.report.lint import (
    LintIssue,
    errors,
    lint,
    lint_html,
    lint_model,
    lint_rendered,
)
from blastradius.report.md import render_markdown
from blastradius.report.model import ChecklistItem
from blastradius.report.page import render_html
from blastradius.report.vocab import (
    CHECKLIST_MAX,
    MAIN_BODY_WORD_BUDGET,
    TIER2_CAP,
    UNDETERMINED_TOP,
    verb_violations,
)

REPO = Path(__file__).resolve().parents[1]


def rules(issues: list[LintIssue]) -> set[str]:
    return {i.rule for i in issues}


def md_of(**kw) -> str:
    return render_markdown(minimal(**kw))


# --------------------------------------------------------------------------------- clean baselines
def test_the_pr_6645_fixture_lints_clean_in_every_layer():
    r = load_sample()
    issues = lint(r, known_evidence=set(load_evidence()))
    assert issues == [], [str(i) for i in issues]
    assert lint_rendered(render_markdown(r, load_evidence())) == []
    assert lint_html(render_html(r, load_evidence())) == []


def test_minimal_report_lints_clean():
    assert lint(minimal()) == []


def test_a_report_in_the_producers_own_wording_lints_clean():
    """Wording and claim shapes taken from reason/assemble.py and reason/narrate.py (template path)."""
    ev = lambda n: f"ev_{n:012x}"
    home = item("home", label="Home page", screen="Home page", band=Band.CONFIRMED, confidence=0.9,
                what="This screen is changed by this PR. Controls involved: “Select environment”, "
                     "“Environments” and “Collections”.", evidence=(ev(1), ev(2)), via="changed wording")
    dlg = item("dlg", tier="indirect", label="New Collection dialog on the Home page",
               screen="New Collection dialog on the Home page", band=Band.LIKELY, confidence=0.6, section="Collections",
               what="This screen is probably affected by this PR. It uses the shared environment value input "
                    "code, which this PR edits (its behaviour changed). Controls involved: “Name”.",
               via="the shared environment value input code", evidence=(ev(3),))
    glob = item("glob", tier="global", label="The shared page frame (layout) around every screen", screen=None,
                band=Band.CONFIRMED, confidence=0.85, via="page layout", evidence=(ev(4),),
                what="This PR edits the page layout (default). Every screen is changed by this PR to some "
                     "degree. The lists in this report are therefore not exhaustive.")
    stored = item("sto", tier="stored_data", label="Saved collection data", screen=None, band=Band.CONFIRMED,
                  confidence=0.85, via="saved data format", evidence=(ev(5),),
                  what="The saved collection data format is changed by this PR. Data saved by older versions has "
                       "to keep opening in the new build.")
    items = [home, dlg, glob, stored]
    claims = [claim(f"{i.label}: {i.what_changed}", i.band, tuple(i.evidence_ids)) for i in items]
    req = requirement(statement="User can create a collection to organize requests.", band=Band.LIKELY,
                      evidence_ids=[ev(6)])
    claims.append(claim("“User can create a collection to organize requests.”: This requirement rests on a "
                        "screen that is probably affected by this PR.", Band.LIKELY, (ev(6), ev(7))))
    steps = ("Export your saved data, import the export again, and check nothing is lost.",)
    r = minimal(
        risk_level="HIGH", headline='Blast radius: PR #7 "Add a thing"\nRisk: HIGH. Touches Collections. 2 screens, '
        "0 user flows, 1 requirement. It also changes code shared across the app. It also changes how data is saved.",
        confidence_line="Confidence: 3 items confirmed, 1 probable and 0 possible. 2 things could not be "
                        "determined and are listed below with the same weight as the findings.",
        direct=[home], indirect=[dlg], global_changes=[glob], stored_data=[stored], requirements=[req],
        claims=claims,
        checklist=[ChecklistItem(text="On the Home page, click “Select environment” and check it still behaves "
                                      "as before.", why="the Home page is changed by this PR.", item_ids=[home.id]),
                   ChecklistItem(text=steps[0], why="the saved collection data format is changed by this PR.",
                                 item_ids=[stored.id])],
        undetermined=[undetermined(code="STALE_BASE", subject="the whole PR",
                                   why="The PR base is behind main and overlaps changed files: the diff may not "
                                       "apply as shown.", evidence_ids=[ev(8)]),
                      undetermined(code="NOT_MAPPED_TO_SCREEN", subject="2 items: “A”, “B”",
                                   why="No screen we observed shows them.", evidence_ids=[ev(9)])],
        not_checked=["Only the signed-out surface of the web app was observed.", "One viewport size was used.",
                     "Only English text (`en`) was compared.",
                     "Frontend files only: backend, scripts and build tooling are outside this analysis.",
                     "Cloud web edition only: desktop and self-hosted behaviour was not assessed.",
                     "Set aside as not user-facing: 2 test file(s)."])
    issues = lint(r, known_evidence={ev(n) for n in range(1, 10)} | {"ev_aaaaaaaaaaaa"})
    assert issues == [], [str(i) for i in issues]
    md = render_markdown(r)
    # every claim belongs to a sentence above (four items, one requirement), so none is left unmatched
    assert md.count("    - Claim `clm_") == 5 and "Claim not shown as a sentence above" not in md
    assert steps[0] in md.split("## Stored data", 1)[1]           # the producer's own migration step is reused


def test_lint_rendered_needs_only_text_and_never_raises():
    for text in ("", "plain text", "# Title\n\n| a |\n", "## Directly changed\n| x |\n|---|\n| y |"):
        assert isinstance(lint_rendered(text), list)
    assert "risk-first-lines" in rules(lint_rendered(""))


def test_issue_shape_is_rule_severity_message():
    issue = lint_rendered("")[0]
    assert issue.rule and issue.severity in ("error", "warning") and issue.message
    assert str(issue).startswith(issue.severity.upper())
    assert errors([issue, LintIssue(rule="r", severity="warning", message="m")]) == [issue]


# ------------------------------------------------------------------ risk level in the first 3 lines
def test_risk_level_must_be_within_the_first_three_lines():
    good = md_of()
    assert "risk-first-lines" not in rules(lint_rendered(good))
    bad = good.replace("**Risk: LOW.** Touches the sidebar. 1 screen.", "")
    bad = bad.replace("Confidence:", "A line.\n\nAnother line.\n\nRisk: LOW. Confidence:", 1)
    assert "risk-first-lines" in rules(lint_rendered(bad))
    assert "risk-first-lines" in rules(lint_rendered("# T\n\nFirst.\n\nSecond.\n\nThird.\n\nRisk: HIGH.\n"))


def test_risk_level_needs_a_level_word():
    assert "risk-first-lines" in rules(lint_rendered("# T\n\nRisk: considerable.\n"))


# ----------------------------------------------------------------------------------- checklist <= 8
def test_checklist_has_at_most_eight_items():
    ok = [ChecklistItem(text=f"Do step {n}.", why="because.") for n in range(CHECKLIST_MAX)]
    assert "checklist-max" not in rules(lint(minimal(checklist=ok)))
    too_many = ok + [ChecklistItem(text="One more.", why="because.")]
    bad = lint(minimal(checklist=too_many))
    assert "checklist-max" in rules(bad)
    assert "checklist-max" in rules(lint_rendered(md_of(checklist=too_many)))     # also from the text alone
    assert len([i for i in bad if i.rule == "checklist-max"]) == 1               # model and text agree: one issue


def test_checklist_lines_should_end_with_a_why():
    r = minimal(checklist=[ChecklistItem(text="Open the sidebar.", why="")])
    issues = [i for i in lint(r) if i.rule == "checklist-why"]
    assert issues and issues[0].severity == "warning"


# ---------------------------------------------------------------------------------- band word
def test_every_tier_row_carries_a_band_word():
    md = md_of()
    assert "band-word" not in rules(lint_rendered(md))
    for broken in ("| Maybe |", "|  |", "| **Certain** |", "| Unlikely |"):
        bad = md.replace("| Confirmed |", broken, 1)
        assert "band-word" in rules(lint_rendered(bad)), broken


def test_indirect_and_requirement_rows_need_a_band_word_too():
    r = load_sample()
    md = render_markdown(r)
    for needle in ("| Likely |", "| Possible |"):
        assert "band-word" in rules(lint_rendered(md.replace(needle, "| Dunno |", 1))), needle
    short = md.replace("| Confirmed |", "|", 1)                        # a row that lost its last cell
    assert "band-word" in rules(lint_rendered(short))


# --------------------------------------------------------------------------- every sentence cited
def test_a_sentence_without_evidence_is_flagged():
    bad = minimal(direct=[item("u", evidence=())])
    flagged = [i for i in lint(bad) if i.rule == "uncited-sentence"]
    assert flagged and "Environments sidebar" in flagged[0].message


def test_a_requirement_without_evidence_is_flagged():
    assert "uncited-sentence" in rules(lint(minimal(requirements=[requirement(evidence_ids=[])])))
    assert "uncited-sentence" not in rules(lint(minimal(requirements=[requirement()])))


def test_a_matching_claim_counts_as_the_citation():
    text = "The sidebar is changed by this PR."
    r = minimal(direct=[item("c", what=text, evidence=())],
                claims=[claim(f"Environments sidebar: {text}", evidence=("ev_dddddddddddd",))])
    assert "uncited-sentence" not in rules(lint(r))


# ------------------------------------------------------------------------------------- verb table
CASES = [
    # (band, sentence, forbidden phrases that must be flagged)
    (Band.CONFIRMED, "The sidebar might change.", {"might"}),
    (Band.CONFIRMED, "The sidebar could change.", {"could"}),
    (Band.CONFIRMED, "The sidebar may change, perhaps.", {"may", "perhaps"}),
    (Band.LIKELY, "The sidebar will change.", {"will"}),
    (Band.LIKELY, "The sidebar is certain to change.", {"is certain"}),
    (Band.LIKELY, "It is certain to change; certainly.", {"is certain", "certainly"}),
    (Band.POSSIBLE, "The sidebar will change.", {"will"}),
    (Band.POSSIBLE, "The sidebar is probably affected.", {"probably"}),
    (Band.UNKNOWN, "The sidebar will change.", {"will"}),
    (Band.UNKNOWN, "The sidebar is probably affected.", {"probably"}),
    (Band.UNKNOWN, "The sidebar is changed by this PR.", {"is changed by this PR"}),
    (Band.UNKNOWN, "The sidebar is affected.", {"is affected"}),
]
CLEAN = [
    (Band.CONFIRMED, "The sidebar will change."),
    (Band.CONFIRMED, "The sidebar is changed by this PR."),
    (Band.LIKELY, "The sidebar is probably affected."),
    (Band.LIKELY, "The sidebar likely changes."),
    (Band.POSSIBLE, "The sidebar might be affected; worth a quick look."),
    (Band.UNKNOWN, "We cannot tell what this PR does to the sidebar; not determined."),
    (Band.UNKNOWN, "We cannot tell whether the sidebar is affected."),
    (Band.CONFIRMED, "The “Might it blend” button is changed by this PR."),     # quoted names are not ours
    (Band.POSSIBLE, "The “Will it blend” button might be affected."),
]


@pytest.mark.parametrize(("band", "text", "expected"), CASES)
def test_forbidden_verbs_per_band(band, text, expected):
    assert set(verb_violations(text, band)) == expected


@pytest.mark.parametrize(("band", "text"), CLEAN)
def test_allowed_verbs_per_band(band, text):
    assert verb_violations(text, band) == []


@pytest.mark.parametrize(("band", "text", "expected"), [c for c in CASES if c[2]])
def test_the_lint_flags_a_forbidden_verb_in_a_rendered_row(band, text, expected):
    conf = {Band.CONFIRMED: 0.9, Band.LIKELY: 0.65, Band.POSSIBLE: 0.4, Band.UNKNOWN: 0.1}[band]
    bad = minimal(direct=[item("v", what=text, band=band, confidence=conf)])
    flagged = [i for i in lint(bad) if i.rule == "band-verb"]
    assert flagged and expected <= {i.where.rsplit(": ", 1)[-1] for i in flagged}


def test_requirement_quotes_are_the_specs_words_and_never_flagged():
    r = minimal(requirements=[requirement(quote="Users will see a list and could sort it.", band=Band.CONFIRMED)])
    assert "band-verb" not in rules(lint(r))


def test_items_cut_by_the_cap_and_unmatched_claims_are_checked_too():
    extra = [item(f"o{n}", tier="indirect", label=f"Screen {n}", screen=f"Page {n}", band=Band.POSSIBLE,
                  what="This screen will change.", section="S") for n in range(TIER2_CAP + 1)]
    flagged = [i for i in lint(minimal(indirect=extra)) if i.rule == "band-verb"]
    assert flagged                                                            # shown rows and the cut one
    only_claim = minimal(claims=[claim("This will change, perhaps.", band=Band.CONFIRMED)])
    assert "band-verb" in rules(lint(only_claim))


def test_checklist_lines_follow_the_weakest_band_of_their_items():
    weak = item("w", band=Band.POSSIBLE, what="The sidebar might be affected.")
    r = minimal(direct=[weak], checklist=[ChecklistItem(text="Open it.", why="it will break.", item_ids=[weak.id])])
    assert "band-verb" in rules(lint(r))
    ok = minimal(direct=[weak], checklist=[ChecklistItem(text="Open it.", why="it might be affected.",
                                                         item_ids=[weak.id])])
    assert "band-verb" not in rules(lint(ok))


# --------------------------------------------------------------------------- could not determine
def test_could_not_determine_section_must_exist_and_say_something():
    md = md_of()
    assert "undetermined-section" not in rules(lint_rendered(md))              # "Nothing outstanding because"
    title = "## What we could not determine, and who should look"
    head, rest = md.split(title)
    tail = "\n## Appendix" + rest.split("\n## Appendix", 1)[1]
    assert "undetermined-section" in rules(lint_rendered(head + tail))          # section missing
    assert "undetermined-section" in rules(lint_rendered(head + title + "\n" + tail))   # section empty
    scope_only = head + title + "\n\n### What this report did not look at\n\n- Only one viewport.\n" + tail
    assert "undetermined-section" in rules(lint_rendered(scope_only))           # scope bullets are not findings
    listed = head + title + "\n\n- Something nobody could map.\n" + tail
    assert "undetermined-section" not in rules(lint_rendered(listed))


def test_nothing_outstanding_must_give_a_reason():
    md = md_of().replace("Nothing outstanding because no failures", "Nothing outstanding.")
    md = md.replace(" no failures or items needing review were recorded for this PR.", "")
    assert "undetermined-section" in rules(lint_rendered(md))


def test_could_not_determine_may_not_be_a_deeper_heading_than_findings():
    md = render_markdown(load_sample())
    assert "undetermined-weight" not in rules(lint_rendered(md))
    soft = md.replace("## What we could not determine, and who should look",
                      "### What we could not determine, and who should look")
    assert "undetermined-weight" in rules(lint_rendered(soft))


def test_html_gives_could_not_determine_the_same_heading_level_too():
    page = render_html(load_sample())
    assert "undetermined-weight" not in rules(lint_html(page))
    soft = page.replace('<h2 id="h-undetermined">What we could not determine, and who should look</h2>',
                        '<h3 id="h-undetermined">What we could not determine, and who should look</h3>')
    assert "undetermined-weight" in rules(lint_html(soft))


def test_what_the_report_did_not_look_at_must_be_listed():
    assert "not-checked" not in rules(lint(minimal()))
    for empty in ([], ["", "  "]):
        assert "not-checked" in rules(lint(minimal(not_checked=empty)))
    md = md_of().replace("- Only the signed-out surface of the web app was observed.", "")
    assert "not-checked" in rules(lint_rendered(md))


# ---------------------------------------------------------------------------- body hygiene
@pytest.mark.parametrize(("rule", "text"), [
    ("raw-id", "The sidebar is changed by this PR (ev_0123456789ab)."),
    ("raw-id", "The sidebar is changed by this PR; see req_abcdef123456."),
    ("raw-id", "Screen scr_000000000001 is changed by this PR."),
    ("raw-id", "Element uie_0123456789ab and clm_0123456789ab."),
    ("file-path", "packages/hoppscotch-common/src/components/environments/Editor.vue is changed by this PR."),
    ("file-path", "The Editor.vue file is changed by this PR."),
    ("file-path", "The en.json text is changed by this PR."),
    ("file-path", "The @hoppscotch/data package is changed by this PR."),
    ("raw-sha", "Built from 64ba4ff75eff."),
    ("jargon", "Every node of the graph is changed by this PR."),
    ("jargon", "A hop through the closure is changed by this PR."),
    ("jargon", "The edge is changed by this PR."),
])
def test_no_ids_paths_hashes_or_graph_jargon_in_the_body(rule, text):
    bad = minimal(direct=[item("h", what=text)])
    assert rule in rules(lint(bad)), text
    assert rule in rules(lint_rendered(render_markdown(bad)))


@pytest.mark.parametrize("text", [
    "The sidebar and/or the list is changed by this PR.", "REST/GraphQL screens are changed by this PR.",
    "Version v2026.9.0 is changed by this PR.", "GraphQL is changed by this PR; see hoppscotch.io.",
    "The knowledge base is changed by this PR.", "The button said 2026-10-12 is changed by this PR.",
])
def test_ordinary_words_are_not_mistaken_for_paths_or_jargon(text):
    assert lint(minimal(direct=[item("p", what=text)])) == []


def test_ids_and_paths_are_fine_in_the_appendix():
    r = minimal(direct=[item("a", files=["packages/x/src/Editor.vue"], requirement_ids=["req_0123456789ab"])])
    assert lint(r) == []
    assert "packages/x/src/Editor.vue" in render_markdown(r)


def test_quoted_text_is_exempt_from_the_jargon_and_path_rules():
    r = minimal(pr_title="fix edge case in graph view for Editor.vue",
                requirements=[requirement(quote="A node in the graph is shown on the edge.")])
    assert lint(r) == []


def test_html_in_text_fields_is_flagged_even_though_the_renderer_escapes_it():
    bad = minimal(direct=[item("h", label="<b>Sidebar</b>", what="The sidebar is changed by this PR.")])
    flagged = [i for i in lint(bad) if i.rule == "html-in-field"]
    assert flagged and flagged[0].where.startswith("direct[0].label")
    assert "<b>" not in render_markdown(bad).replace("\\<b>", "") and "<b>" not in render_html(bad)
    for field in ("pr_title", "headline", "confidence_line"):
        assert "html-in-field" in rules(lint(minimal(**{field: "x <script>y</script>"})))
    assert "html-in-field" not in rules(lint(minimal(pr_title="a < b and c > d")))


def test_html_in_markdown_is_flagged_but_code_spans_are_not():
    good = md_of()
    assert "html-in-text" not in rules(lint_rendered(good))
    assert "html-in-text" in rules(lint_rendered(good + "\n<img src=x>\n"))
    assert "html-in-text" in rules(lint_rendered(good + "\n<!-- hidden -->\n"))
    assert "html-in-text" not in rules(lint_rendered(good + "\nuse `<details>` here\n"))
    assert "html-in-text" not in rules(lint_rendered(good + "\n\\<b\\>escaped\\</b\\>\n"))


def test_remote_urls_and_images_are_flagged_in_both_formats():
    good = md_of()
    assert "remote-url" in rules(lint_rendered(good + "\n![x](https://evil.example/x.png)\n"))
    assert "remote-url" in rules(lint_rendered(good + "\nsee http://evil.example/\n"))
    page = render_html(minimal())
    assert lint_html(page) == []
    bad_pages = [
        page.replace("</main>", '<img src="https://evil.example/x.png"></main>'),
        page.replace("</main>", '<img src="//evil.example/x.png"></main>'),
        page.replace("<style>", '<style>@import url(https://evil.example/a.css);'),
        page.replace("</main>", '<a href="https://evil.example">x</a></main>'),
        page.replace("</main>", '<div style="background:url(https://evil.example/x.png)"></div></main>'),
    ]
    for bad in bad_pages:
        assert "remote-url" in rules(lint_html(bad))
    assert lint_html(page.replace("</main>", '<img src="data:image/png;base64,AAAA"></main>')) == []


def test_html_active_content_and_missing_csp_are_flagged():
    page = render_html(minimal())
    assert "active-content" in rules(lint_html(page.replace("</main>", "<script>alert(1)</script></main>")))
    assert "active-content" in rules(lint_html(page.replace("<main", '<main onclick="x()"')))
    assert "active-content" in rules(lint_html(page.replace("</main>", '<iframe src="#"></iframe></main>')))
    no_csp = page.replace('<meta http-equiv="Content-Security-Policy"', '<meta name="x"')
    assert "csp-missing" in rules(lint_html(no_csp))
    weak = page.replace("default-src 'none'", "default-src *")
    assert "csp-missing" in rules(lint_html(weak))


def test_html_body_text_is_held_to_the_same_hygiene_as_markdown():
    page = render_html(minimal())
    assert "raw-id" in rules(lint_html(page.replace("</main>", "<p>See ev_0123456789ab</p></main>")))


# --------------------------------------------------------------------------------- word budget
def test_main_body_prose_stays_within_the_word_budget():
    words = "word " * (MAIN_BODY_WORD_BUDGET + 20)
    assert "word-budget" in rules(lint(minimal(confidence_line=words)))
    assert "word-budget" not in rules(lint(minimal(confidence_line=" ".join(["word"] * 300))))


def test_table_cells_and_the_appendix_do_not_count_toward_the_budget():
    long_text = "The sidebar is changed by this PR. " + " ".join(["detail"] * 700)
    assert "word-budget" not in rules(lint(minimal(direct=[item("t", what=long_text)])))
    many_files = [f"packages/x/{n}.vue" for n in range(400)]
    assert "word-budget" not in rules(lint(minimal(direct=[item("f", files=many_files)])))


def test_the_fixture_is_well_inside_the_budget():
    md = render_markdown(load_sample())
    assert "word-budget" not in rules(lint_rendered(md))


# ------------------------------------------------------------------------ structure of the text
def test_sections_must_be_present_in_the_fixed_order():
    md = render_markdown(load_sample())
    assert not rules(lint_rendered(md)) & {"section-order", "section-missing", "section-unexpected"}
    swapped = md.replace("## Directly changed", "## TMP").replace("## Indirect, through shared code",
                                                                   "## Directly changed").replace(
        "## TMP", "## Indirect, through shared code")
    assert "section-order" in rules(lint_rendered(swapped))
    extra = md.replace("## Requirements at risk", "## Summary\n\nhello\n\n## Requirements at risk")
    assert "section-unexpected" in rules(lint_rendered(extra))
    gone = md.replace("## Directly changed", "### Directly changed")
    assert "section-missing" in rules(lint_rendered(gone))
    renamed = md.replace("## What to re-test first", "## Re-test first")
    assert {"section-unexpected", "section-missing"} <= rules(lint_rendered(renamed))


def test_footnote_markers_must_resolve():
    md = render_markdown(load_sample(), load_evidence())
    assert "footnote-unresolved" not in rules(lint_rendered(md))
    bad = md.replace("[^1]:", "[^77]:")
    flagged = [i for i in lint_rendered(bad) if i.rule == "footnote-unresolved"]
    assert flagged and "[^1]" in flagged[0].message


# ---------------------------------------------------------------------------------- model rules
def test_band_must_match_the_confidence_value():
    bad = minimal(direct=[item("b", band=Band.CONFIRMED, confidence=0.3)])
    assert "band-confidence" in rules(lint(bad))
    assert "band-confidence" not in rules(lint(minimal(direct=[item("b", band=Band.POSSIBLE, confidence=0.3)])))


def test_headline_may_not_state_another_risk_level():
    bad = minimal(risk_level="HIGH", headline='Blast radius: PR #7 "x"\nRisk: LOW. Touches the sidebar.')
    assert "risk-consistency" in rules(lint(bad))
    assert "risk-consistency" not in rules(lint(minimal(risk_level="LOW")))


def test_headline_is_two_or_three_lines():
    long = 'Blast radius: PR #7 "x"\nRisk: LOW. One.\nTwo.\nThree.\nFour.'
    issue = [i for i in lint(minimal(headline=long)) if i.rule == "headline-lines"]
    assert issue and issue[0].severity == "warning"


def test_checklist_items_must_name_items_that_exist():
    bad = minimal(checklist=[ChecklistItem(text="Open it.", why="it changed.", item_ids=["itm_nope"])])
    assert "checklist-ref" in rules(lint(bad))
    good = minimal()
    ok = good.model_copy(update={"checklist": [ChecklistItem(text="Open it.", why="it changed.",
                                                             item_ids=[good.direct[0].id])]})
    assert "checklist-ref" not in rules(lint(ok))


def test_claims_must_cite_evidence_and_known_evidence_must_exist():
    assert "claim-no-evidence" in rules(lint(minimal(claims=[claim("A sentence.", evidence=())])))
    r = minimal()
    assert "evidence-unknown" in rules(lint(r, known_evidence={"ev_other0000000"}))
    assert "evidence-unknown" not in rules(lint(r, known_evidence={"ev_aaaaaaaaaaaa"}))
    assert "evidence-unknown" not in rules(lint(r))                           # not checked without a ledger


def test_caps_are_warnings_with_a_pointer_to_the_appendix():
    many = [item(f"c{n}", tier="indirect", label=f"S{n}", screen=f"P{n}", band=Band.LIKELY,
                 what="This screen is probably affected.", section="S") for n in range(TIER2_CAP + 1)]
    flagged = [i for i in lint(minimal(indirect=many)) if i.rule == "tier2-cap"]
    assert flagged and flagged[0].severity == "warning"
    und = [undetermined(subject=f"S{n}") for n in range(UNDETERMINED_TOP + 1)]
    assert any(i.rule == "undetermined-top" for i in lint(minimal(undetermined=und)))


def test_duplicate_item_ids_are_flagged():
    a = item("same", label="A")
    b = item("same", label="B")
    assert "duplicate-id" in rules(lint(minimal(direct=[a, b])))


def test_lint_model_alone_skips_rendering():
    assert lint_model(minimal(not_checked=[])) and rules(lint_model(minimal())) == set()


# ---------------------------------------------------------------------------------- determinism
_SNIPPET = """
import json, sys
sys.path.insert(0, "tests")
from report_helpers import minimal, item, undetermined
from blastradius.models import Band
from blastradius.report.lint import lint
r = minimal(direct=[item("a", what="ev_0123456789ab will change, perhaps, in Editor.vue graph", band=Band.CONFIRMED),
                    item("b", label="<i>x</i>", what="might be hop node", band=Band.LIKELY, confidence=0.9)],
            not_checked=[])
print(json.dumps([[i.rule, i.where, i.message] for i in lint(r)]))
"""


def test_lint_output_is_identical_across_hash_seeds_and_runs():
    outs = set()
    for seed in (0, 1, 3, 99):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(REPO / "src")}
        out = subprocess.run([sys.executable, "-c", _SNIPPET], cwd=REPO, env=env, capture_output=True, text=True,
                             timeout=120, check=False)
        assert out.returncode == 0, out.stderr[-600:]
        outs.add(out.stdout.strip().splitlines()[-1])
    assert len(outs) == 1 and "[" in next(iter(outs))
