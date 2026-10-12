"""Report rendering (D29; REPORT_SPEC; cases 57, 61, 62): structure, language, escaping, determinism, caps.

The fixture is the hand-authored PR #6645 report (tests/fixtures/report/PROVENANCE.md), not a product output.
"""

from __future__ import annotations

import html.parser
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from report_helpers import (
    FIXTURES,
    appendix_of,
    body_of,
    claim,
    item,
    load_evidence,
    load_sample,
    minimal,
    requirement,
    sample_run,
    undetermined,
)

from blastradius.models import Band
from blastradius.report import page
from blastradius.report.escape import defang_urls, md_code, md_text
from blastradius.report.md import render_markdown
from blastradius.report.model import ChecklistItem, Report
from blastradius.report.page import render_html
from blastradius.report.render import cited_evidence_ids, render
from blastradius.report.vocab import (
    CHECKLIST_MAX,
    MAIN_BODY_WORD_BUDGET,
    SECTION_TITLES,
    TIER2_CAP,
    UNDETERMINED_TOP,
)
from blastradius.run import RunContext

REPO = Path(__file__).resolve().parents[1]
EXPECTED_H2 = [SECTION_TITLES[k] for k in ("checklist", "direct", "indirect", "requirements", "undetermined",
                                           "appendix")]


def h2_titles(markdown: str) -> list[str]:
    return [ln[3:].strip() for ln in markdown.splitlines() if ln.startswith("## ")]


def tokens(markdown: str) -> list[str]:
    markdown_it = pytest.importorskip("markdown_it")        # a dependency of rich, which the CLI already needs
    parser = markdown_it.MarkdownIt("commonmark").enable("table")
    out: list[str] = []

    def walk(toks) -> None:
        for t in toks:
            out.append(t.type)
            if t.children:
                walk(t.children)

    walk(parser.parse(markdown))
    return out


class _Tags(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def parsed(page_html: str) -> _Tags:
    p = _Tags()
    p.feed(page_html)
    return p


# ------------------------------------------------------------------------------------- structure
def test_sections_have_the_exact_titles_in_the_fixed_order():
    md = render_markdown(load_sample(), load_evidence())
    assert h2_titles(md) == EXPECTED_H2


def test_global_and_stored_data_sit_between_indirect_and_requirements():
    g = item("g", tier="global", label="The shared page frame", screen=None)
    s = item("s", tier="stored_data", label="Saved collection data", screen=None)
    md = render_markdown(load_sample().model_copy(update={"global_changes": [g], "stored_data": [s]}))
    assert h2_titles(md) == [SECTION_TITLES[k] for k in ("checklist", "direct", "indirect", "global",
                                                         "stored_data", "requirements", "undetermined",
                                                         "appendix")]
    assert "every screen may be affected" in md and "the lists above are not exhaustive" in md


def test_global_and_stored_sections_are_absent_when_not_present():
    md = render_markdown(load_sample())
    assert "## Global change" not in md and "## Stored data" not in md


def test_risk_level_is_in_the_first_three_lines_with_a_text_label():
    md = render_markdown(load_sample())
    first = [ln for ln in md.splitlines() if ln.strip()][:3]
    assert any("Risk: MEDIUM" in ln for ln in first)
    doc = render_html(load_sample())
    assert "Risk: MEDIUM" in "".join(parsed(doc).text) and 'class="risk risk-medium"' in doc


def test_a_pr_without_a_title_still_gets_a_clean_headline():
    md = render_markdown(minimal(pr_title="", headline="Risk: LOW. Touches the sidebar."))
    assert md.startswith("# Blast radius: PR #7\n") and '""' not in md.split("\n", 1)[0]
    assert "<h1>Blast radius: PR #7</h1>" in render_html(minimal(pr_title=""))


def test_risk_level_always_comes_from_the_model_not_the_headline_text():
    r = minimal(risk_level="HIGH", headline='Blast radius: PR #7 "x"\nRisk: LOW. Touches the sidebar.')
    md = render_markdown(r)
    assert "**Risk: HIGH.**" in md and "Risk: LOW" not in md


def test_checklist_renders_as_checkboxes_in_both_formats():
    r = load_sample()
    md = render_markdown(r)
    section = md.split("## What to re-test first", 1)[1].split("\n## ", 1)[0]
    assert section.count("- [ ] ") == len(r.checklist)
    boxes = [t for t in parsed(render_html(r)).tags if t[0] == "input"]
    assert len(boxes) == len(r.checklist) and all(a.get("type") == "checkbox" for _, a in boxes)


def test_tier_one_is_a_table_with_screen_what_changed_how_sure():
    md = render_markdown(load_sample())
    direct = md.split("## Directly changed", 1)[1].split("\n## ", 1)[0]
    assert "| Screen | What changed | How sure |" in direct
    rows = [ln for ln in direct.splitlines() if ln.startswith("|")][2:]
    assert len(rows) == len(load_sample().direct)
    assert all(row.rstrip().endswith("| Confirmed |") for row in rows)


def test_band_is_a_word_plus_a_badge_in_html():
    doc = render_html(load_sample())
    badges = re.findall(r'<span class="band band-(\w+)"><span aria-hidden="true">(.)</span>(\w+)</span>', doc)
    assert {w for w, _, _ in badges} == {"confirmed", "likely", "possible"}
    assert all(word.lower() == cls for cls, _, word in badges)
    assert len({glyph for _, glyph, _ in badges}) == 3          # shape differs per band, not only colour


def test_indirect_items_are_grouped_by_spec_section_with_the_shared_piece():
    md = render_markdown(load_sample())
    indirect = md.split("## Indirect, through shared code", 1)[1].split("\n## ", 1)[0]
    assert "### Environments" in indirect and "### Collections" in indirect
    assert "Connected through: the shared current-value service; the shared secret-value service." in indirect
    assert indirect.index("### Environments") < indirect.index("### Collections")   # stronger group first


def test_stored_data_carries_a_manual_migration_checklist():
    s = item("s", tier="stored_data", label="Saved collection data", screen=None,
             what="The saved collection data format is changed by this PR.")
    md = render_markdown(minimal(stored_data=[s]))
    stored = md.split("## Stored data", 1)[1].split("\n## ", 1)[0]
    assert stored.count("- [ ] ") == 3
    assert "Export" in stored and "sync" in stored and "previous build" in stored


def test_migration_steps_reuse_the_producers_checklist_wording_when_it_has_them():
    s = item("s", tier="stored_data", label="Saved collection data", screen=None)
    steps = [ChecklistItem(text="Export your saved data and import it again.", why="x", item_ids=[s.id])]
    md = render_markdown(minimal(stored_data=[s], checklist=steps))
    stored = md.split("## Stored data", 1)[1].split("\n## ", 1)[0]
    assert stored.count("- [ ] ") == 1 and "Export your saved data and import it again." in stored


def test_could_not_determine_has_the_same_heading_level_as_findings():
    md = render_markdown(load_sample())
    assert "\n## What we could not determine, and who should look\n" in md and "\n## Directly changed\n" in md
    doc = render_html(load_sample())
    assert re.search(r'<h2 id="h-undetermined">What we could not determine, and who should look</h2>', doc)
    assert re.search(r'<h2 id="h-direct">Directly changed</h2>', doc)


def test_what_the_report_did_not_look_at_is_always_stated():
    md = render_markdown(load_sample())
    assert "### What this report did not look at" in md
    assert "Only the signed-out surface of the web app was observed." in md


def test_empty_could_not_determine_says_nothing_outstanding_and_why():
    md = render_markdown(minimal())
    section = md.split("## What we could not determine, and who should look", 1)[1].split("###", 1)[0]
    assert "Nothing outstanding because no failures or items needing review were recorded" in section
    assert "not a claim that nothing was missed" in section


def test_empty_sections_say_so_instead_of_vanishing():
    r = minimal(direct=[], checklist=[], requirements=[])
    md = render_markdown(r)
    assert h2_titles(md) == EXPECTED_H2
    assert "Nothing in this PR's own code maps to a screen we could identify." in md
    assert "No other screen was reached through shared code." in md
    assert "No requirement was linked to an affected screen." in md


# ---------------------------------------------------------------------------- appendix and footnotes
def test_paths_ids_and_hashes_appear_only_in_the_appendix():
    r = load_sample()
    md = render_markdown(r, load_evidence())
    body, appendix = body_of(md), appendix_of(md)
    leaks = [r"\bev_[0-9a-f]{12}\b", r"\breq_[0-9a-f]{12}\b", r"\bclm_[0-9a-f]{12}\b", r"\bel_[0-9a-f]{12}\b",
             r"[0-9a-f]{40}", r"packages/", r"\.vue\b", r"en\.json"]
    for pattern in leaks:
        assert not re.search(pattern, body), (pattern, re.search(pattern, body).group(0))
        assert re.search(pattern, appendix), pattern          # and the appendix does carry them
    doc = render_html(r, load_evidence())
    visible = re.sub(r"(?s)<details class=\"appendix\".*?</details>|<style.*?</style>", "", doc)
    text = "".join(parsed(visible).text)
    for pattern in leaks[:5] + leaks[5:7]:
        assert not re.search(pattern, text), pattern


def test_every_footnote_marker_resolves_to_an_appendix_entry():
    md = render_markdown(load_sample(), load_evidence())
    used = set(re.findall(r"\[\^(\d+)\](?!:)", body_of(md)))
    defined = set(re.findall(r"^\[\^(\d+)\]:", appendix_of(md), re.MULTILINE))
    assert used and used <= defined
    doc = render_html(load_sample(), load_evidence())
    links = set(re.findall(r'<sup class="fn"><a href="#ev-(\d+)"', doc))
    targets = set(re.findall(r'<li id="ev-(\d+)"', doc))
    assert links and links <= targets and targets == defined


def test_footnotes_are_numbered_in_report_order():
    md = render_markdown(load_sample(), load_evidence())
    direct = md.split("## Directly changed", 1)[1].split("\n## ", 1)[0]
    numbers = [int(n) for n in re.findall(r"\[\^(\d+)\]", direct)]
    assert numbers == sorted(numbers) == list(range(1, len(numbers) + 1))


def test_appendix_entries_carry_evidence_details_from_the_ledger():
    md = render_markdown(load_sample(), load_evidence())
    appendix = appendix_of(md)
    assert "(pr.file): added packages/hoppscotch-common/src/components/environments/Editor.vue" in appendix
    assert "Trace span `" in appendix and "Files: `packages/" in appendix
    assert "Indexed commit: `63273f8e599e73f0fb38c0739314e8d46b6bc83d`" in appendix
    assert "Deployed vs indexed: 11 of 104 files differ" in appendix
    assert "Claim `clm_" in appendix


def test_evidence_missing_from_the_ledger_is_stated_not_invented():
    md = render_markdown(load_sample(), {})
    assert "not found in this run's evidence ledger" in appendix_of(md)


def test_matching_claims_are_linked_to_their_sentences():
    r = load_sample()
    md = render_markdown(r, load_evidence())
    # the producer writes claims as "<label>: <sentence>"; all twelve fixture claims belong to a statement above
    assert appendix_of(md).count("- Claim `clm_") == len(r.claims) == 12


def test_unmatched_claims_still_reach_the_appendix():
    r = minimal(claims=[claim("A sentence nobody shows.", evidence=("ev_dddddddddddd",))])
    md = render_markdown(r)
    assert "Claim not shown as a sentence above" in appendix_of(md) and "A sentence nobody shows." in md


def test_unsourced_sentence_is_marked_in_the_body():
    r = minimal(direct=[item("u", evidence=())])
    assert "(unsourced)" in render_markdown(r)
    assert 'class="unsourced"' in render_html(r)


def test_flow_names_that_look_like_ids_or_paths_stay_out_of_the_body():
    bad = item("f", flows=["Edit a variable", "flow_0123456789ab", "src/flows/edit.ts"])
    md = render_markdown(minimal(direct=[bad]))
    assert "Flows: Edit a variable." in body_of(md)
    assert "flow_0123456789ab" not in body_of(md) and "src/flows/edit.ts" not in body_of(md)
    assert "`flow_0123456789ab`" in appendix_of(md) and "`src/flows/edit.ts`" in appendix_of(md)


def test_snapshot_facts_in_the_header_are_counts_not_hashes():
    md = render_markdown(load_sample())
    head = md.split("## What to re-test first", 1)[0]
    assert "11 of 104 files differ" in head and "10 commits behind main" in head
    assert not re.search(r"[0-9a-f]{12}", head)


# ------------------------------------------------------------------------------------------- caps
def test_the_caps_are_the_ones_the_spec_names():
    # REPORT_SPEC sections 2 and 4, case 61 and the lint budget: written out so a change here is deliberate
    assert (CHECKLIST_MAX, TIER2_CAP, UNDETERMINED_TOP, MAIN_BODY_WORD_BUDGET) == (8, 10, 5, 600)


def test_indirect_items_beyond_the_cap_go_to_the_appendix():
    extra = [item(f"i{n}", tier="indirect", label=f"Screen {n:02d}", screen=f"Page {n:02d}",
                  what=f"Screen {n:02d} is probably affected.", band=Band.LIKELY, confidence=0.6 - n / 1000,
                  section="Things") for n in range(TIER2_CAP + 3)]
    md = render_markdown(minimal(indirect=extra))
    body, appendix = body_of(md), appendix_of(md)
    shown = [n for n in range(TIER2_CAP + 3) if f"Screen {n:02d}" in body]
    assert len(shown) == TIER2_CAP and shown == list(range(TIER2_CAP))      # strongest first
    assert f"Showing the {TIER2_CAP} strongest of {TIER2_CAP + 3}" in body
    for n in range(TIER2_CAP, TIER2_CAP + 3):
        assert f"Screen {n:02d}" in appendix
    assert appendix.count("Cut from the main report by its display cap.") == 3


def test_could_not_determine_beyond_the_top_rows_goes_to_the_appendix():
    many = [undetermined(subject=f"Subject {n}", why=f"Reason {n}.", evidence_ids=[f"ev_{n:012x}"])
            for n in range(UNDETERMINED_TOP + 2)]
    md = render_markdown(minimal(undetermined=many))
    body, appendix = body_of(md), appendix_of(md)
    assert all(f"Subject {n}" in body for n in range(UNDETERMINED_TOP))
    assert "Subject 5" not in body and "Subject 6" not in body
    assert "Subject 5" in appendix and "Subject 6" in appendix
    assert "2 more (top 5 shown) are listed in the appendix." in body


def test_checklist_is_not_silently_truncated():
    many = [ChecklistItem(text=f"Do step {n}.", why="because.") for n in range(11)]
    md = render_markdown(minimal(checklist=many))
    assert md.count("Do step ") == 11          # the lint reports 11 > 8; the renderer never hides work


# ------------------------------------------------------------------------------------ injection
HOSTILE = ('x <script>alert(1)</script> `tick` | pipe [click](https://evil.example/x) ![p](https://evil.example/p.png) '
           '<img src=https://evil.example/t.png> <!-- hidden --> https://bare.example/path www.bare.example '
           '**bold** _it_ &lt;b&gt; \u202e rtl $math$')


def hostile_report() -> Report:
    bad = item("h", label=HOSTILE, screen=HOSTILE, what=f"The sidebar is changed by this PR. {HOSTILE}",
               flows=[HOSTILE])
    return minimal(pr_title=HOSTILE, direct=[bad], headline=f'Blast radius: PR #7 "{HOSTILE}"\nRisk: LOW. {HOSTILE}',
                   confidence_line=HOSTILE, requirements=[requirement(statement=HOSTILE, quote=HOSTILE,
                                                                      section=HOSTILE)],
                   undetermined=[undetermined(subject=HOSTILE, why=HOSTILE, what_to_check=HOSTILE)],
                   not_checked=[HOSTILE])


def test_markdown_neutralises_injected_text():
    md = render_markdown(hostile_report())
    kinds = set(tokens(md))
    assert not kinds & {"html_inline", "html_block", "link_open", "image", "autolink"}, kinds
    assert not re.search(r"(?i)https?://|<!--", md)
    assert "\u202e" not in md and "hidden" not in md           # bidi control and the HTML comment are stripped


def test_markdown_table_rows_keep_their_cell_count_with_pipes_in_text():
    md = render_markdown(hostile_report())
    direct = md.split("## Directly changed", 1)[1].split("\n## ", 1)[0]
    rows = [ln for ln in direct.splitlines() if ln.startswith("|")]
    assert len(rows) == 3
    assert {len(re.split(r"(?<!\\)\|", ln.strip().strip("|"))) for ln in rows} == {3}


def test_html_escapes_injected_text_and_keeps_the_page_inert():
    doc = render_html(hostile_report())
    p = parsed(doc)
    names = {t for t, _ in p.tags}
    assert not names & {"script", "img", "iframe", "form", "link", "object", "embed", "base"}
    assert all(a.get("href", "#").startswith("#") for t, a in p.tags if t == "a")
    assert not re.search(r"(?i)https?://", doc) and "<script" not in doc.replace("<style", "")
    assert "\u202e" not in doc
    assert "alert(1)" in "".join(p.text)                       # shown as text, never executed


def test_defanged_urls_keep_their_colon_and_cleaning_twice_changes_nothing():
    # regressions: the first defang dropped the colon ("hxxps//host"), and a second pass turned it into "hxxps[:]//"
    once = defang_urls("see https://docs.example/x and http://a.b ftp://c.d")
    assert once == "see hxxps://docs.example/x and hxxp://a.b fxp://c.d"
    assert defang_urls(once) == once
    assert defang_urls("ssh://host") == "ssh[:]//host" and defang_urls("www.example.com") == "www[.]example.com"


def test_markdown_text_escapes_what_forms_syntax():
    out = md_text("a|b `c` [d](e) <f> *g* ~h~ $i$ #j")
    assert out == "a\\|b \\`c\\` \\[d\\](e) \\<f> \\*g\\* \\~h\\~ \\$i\\$ #j"
    assert md_text("# heading") == "\\# heading" and md_text("> quote") == "\\> quote"
    assert md_text("1. item") == "1\\. item" and md_text("snake_case and _em_") == "snake_case and \\_em\\_"
    assert md_text("&amp; &lt; & x") == "&amp;amp; &amp;lt; & x"


def test_code_spans_use_a_longer_fence_than_any_backtick_run():
    assert md_code("a`b") == "``a`b``" and md_code("`x`") == "`` `x` ``" and md_code("plain") == "`plain`"


# -------------------------------------------------------------------------------------------- html
def test_html_is_one_self_contained_file():
    doc = render_html(load_sample(), load_evidence())
    assert '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; ' \
           "img-src data:" in doc
    p = parsed(doc)
    names = {t for t, _ in p.tags}
    assert names.isdisjoint({"script", "link", "img", "iframe", "form", "base", "object", "embed"})
    assert not re.search(r"(?i)https?://|@import|url\(", doc)
    assert not any(k.startswith("on") for _, a in p.tags for k in a)
    assert any(t == "details" and a.get("class") == "appendix" for t, a in p.tags)
    meta = {a.get("name"): a.get("content") for t, a in p.tags if t == "meta"}
    assert "width=device-width" in (meta["viewport"] or "") and meta["color-scheme"] == "light dark"
    assert "prefers-color-scheme:dark" in doc and "@media print" in doc and "max-width:40rem" in doc


def test_html_text_matches_markdown_statements():
    r = load_sample()
    text = " ".join("".join(parsed(render_html(r)).text).split())
    for it in r.direct + r.indirect:
        assert " ".join(it.what_changed.split()) in text


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


@pytest.mark.parametrize("palette", ["LIGHT", "DARK"])
def test_text_colours_meet_wcag_aa_in_light_and_dark(palette):
    colours = getattr(page, palette)
    for fg, bg in page.CONTRAST_PAIRS:
        hi, lo = sorted((_luminance(colours[fg]), _luminance(colours[bg])), reverse=True)
        assert (hi + 0.05) / (lo + 0.05) >= 4.5, (palette, fg, bg)


def test_dark_palette_is_applied_by_media_query_and_print_forces_light():
    css = render_html(minimal()).split("<style>", 1)[1].split("</style>", 1)[0]
    root, dark, printed = css.split("\n")[1:4]
    assert f"--bg:{page.LIGHT['bg']}" in root
    assert dark.startswith("@media (prefers-color-scheme:dark)") and f"--bg:{page.DARK['bg']}" in dark
    assert printed.startswith("@media print") and f"--bg:{page.LIGHT['bg']}" in printed


# ----------------------------------------------------------------------------------- render() I/O
def test_render_writes_both_files_and_returns_their_paths(tmp_path):
    run = sample_run()
    paths = render(load_sample(), run, out_dir=tmp_path / "out")
    assert set(paths) == {"md", "html"}
    assert Path(paths["md"]).name == "report.md" and Path(paths["html"]).name == "report.html"
    md = Path(paths["md"]).read_text(encoding="utf-8")
    assert md == render_markdown(load_sample(), load_evidence())
    assert Path(paths["html"]).read_text(encoding="utf-8") == render_html(load_sample(), load_evidence())


def test_render_defaults_to_the_runs_reason_directory(tmp_path):
    run = RunContext.create("hoppscotch", run_id="r-render", runs_dir=tmp_path)
    paths = render(load_sample(), run)
    assert Path(paths["md"]) == tmp_path / "r-render" / "reason" / "6645" / "report.md"
    assert Path(paths["html"]).exists()


def test_render_records_the_lint_verdict_in_the_trace(tmp_path):
    run = sample_run()
    render(load_sample(), run, out_dir=tmp_path)
    end = next(r for r in run.tracer.records if r.get("name") == "report.render" and r["event"] == "end")
    assert end["attributes"]["lint_errors"] == 0
    assert end["attributes"]["found"] == end["attributes"]["cited"] == len(load_evidence())

    bad = minimal(not_checked=[])
    render(bad, run, out_dir=tmp_path / "bad")
    ends = [r for r in run.tracer.records if r.get("name") == "report.render" and r["event"] == "end"]
    # minimal() cites an evidence id this ledger does not hold: that is the second error
    assert ends[-1]["attributes"]["lint_rules"] == ["evidence-unknown", "not-checked"]


def test_cited_evidence_ids_are_sorted_unique_and_all_in_the_fixture_ledger():
    ids = cited_evidence_ids(load_sample())
    assert ids == sorted(set(ids)) and set(ids) == set(load_evidence())


# --------------------------------------------------------------------------------- determinism
def test_output_does_not_depend_on_input_order_of_items():
    r = load_sample()
    shuffled = r.model_copy(update={"direct": list(reversed(r.direct)), "indirect": list(reversed(r.indirect)),
                                    "claims": list(reversed(r.claims)),
                                    "requirements": list(reversed(r.requirements))})
    assert render_markdown(r, load_evidence()) == render_markdown(shuffled, load_evidence())
    assert render_html(r, load_evidence()) == render_html(shuffled, load_evidence())


_SNIPPET = """
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, "tests")
from report_helpers import load_sample, load_evidence
from blastradius.report.md import render_markdown
from blastradius.report.page import render_html
r, e = load_sample(), load_evidence()
print(hashlib.sha256((render_markdown(r, e) + render_html(r, e)).encode()).hexdigest())
"""


def test_output_is_byte_identical_across_hash_seeds():
    digests = set()
    for seed in (0, 1, 2, 7, 4242):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(REPO / "src")}
        out = subprocess.run([sys.executable, "-c", _SNIPPET], cwd=REPO, env=env, capture_output=True, text=True,
                             timeout=120, check=False)
        assert out.returncode == 0, out.stderr[-600:]
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1


def test_output_has_no_timestamps_or_run_ids():
    md = render_markdown(load_sample(), load_evidence())
    assert not re.search(r"\b20\d\d-\d\d-\d\dT\d\d:", md)
    assert "fixture-6645" not in md and "ephemeral" not in md


def test_fixture_is_labelled_as_a_fixture_in_its_own_text():
    r = load_sample()
    assert any(line.startswith("FIXTURE:") for line in r.not_checked)
    assert (FIXTURES / "PROVENANCE.md").read_text(encoding="utf-8").startswith("FIXTURE")
