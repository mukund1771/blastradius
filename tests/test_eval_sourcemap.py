"""evalkit.sourcemap and the attribution half of the oracle: VLQ, spans, votes, ownership chains, drift."""

from __future__ import annotations

import random

import pytest
from test_eval_support import build_chunk, encode_mappings

from blastradius.evalkit import oracle_sourcemap as o
from blastradius.evalkit.sourcemap import SourceMap, Text, decode_mappings, git_blob_sha
from blastradius.models import CodeFile, CodeIndex, FileClass

BASE = "packages/app-web/dist/assets"


def _chunk(url: str, modules: list[tuple[str, str]]) -> o.LoadedChunk:
    text, data = build_chunk(modules)
    data["file"] = url.rsplit("/", 1)[-1]
    return o.LoadedChunk(
        url, Text(text), SourceMap(data), {"url": url, "etag": "W/1", "sha256": "0", "bytes": 1}
    )


# --------------------------------------------------------------------------------------------- VLQ
def test_vlq_roundtrip_with_negative_deltas_and_several_lines():
    rng = random.Random(3)
    lines = []
    for _ in range(4):
        col, segs = 0, []
        for _ in range(rng.randrange(0, 40)):
            col += rng.randrange(1, 300)
            segs.append((col, rng.randrange(0, 60), rng.randrange(0, 5000), rng.randrange(0, 200)))
        lines.append(segs)
    decoded = decode_mappings(encode_mappings(lines))
    assert len(decoded) == 4
    for want, got in zip(lines, decoded):
        assert list(got.cols) == [s[0] for s in want]
        assert list(got.srcs) == [s[1] for s in want]


def test_one_field_segments_are_skipped_and_names_are_consumed():
    # "A" = 1 field (dropped, but its column still counts); "CAAAA" = col +1, 4 fields; "CCAAA" = col +1,
    # source +1; "AAAAA" = 5 fields with a name delta
    lines = decode_mappings("A,CAAAA,CCAAA,AAAAA")
    assert list(lines[0].cols) == [1, 2, 2]
    assert list(lines[0].srcs) == [0, 1, 1]


def test_bad_mapping_character_is_an_error_not_silence():
    with pytest.raises(ValueError):
        decode_mappings("AA*A")


def test_source_map_version_and_sections_are_checked():
    with pytest.raises(ValueError):
        SourceMap({"version": 2, "sources": [], "mappings": ""})
    with pytest.raises(ValueError):
        SourceMap({"version": 3, "sections": [], "sources": [], "mappings": ""})


# -------------------------------------------------------------------------------------------- spans
def test_columns_are_utf16_units_not_python_offsets():
    t = Text("ab\n😀😀x😀y")
    assert t.position(t.text.index("b")) == (0, 1)
    assert t.position(t.text.index("x")) == (1, 4)  # two astral characters = 4 UTF-16 units
    assert t.position(t.text.index("y")) == (1, 7)  # three astral characters before it


def test_occurrences_stops_at_two_and_find_is_exact():
    t = Text("foo bar foo baz foo")
    assert t.occurrences("foo") == [0, 8]
    assert t.occurrences("bar") == [4] and t.occurrences("qux") == []


def test_vote_is_the_plurality_of_the_span_not_its_first_segment():
    text, data = build_chunk(
        [("../../src/a/Alpha.vue", "A" * 30), ("../../src/b/Beta.vue", "B" * 60)], step=6
    )
    sm, t = SourceMap(data), Text(text)
    start = t.position(24)  # starts inside Alpha: the first segment in the span is Alpha's
    end = t.position(90)
    votes = sm.votes(start, end)
    assert sm.sources[min(votes, key=lambda s: -votes[s])].endswith("Beta.vue")
    assert votes[0] == 1 and votes[1] == 10


def test_git_blob_sha_matches_git():
    assert git_blob_sha("") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"  # git's empty blob
    assert git_blob_sha("hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


# --------------------------------------------------------------------------------------- resolution
def test_resolve_source_strips_query_and_normalises():
    assert (
        o.resolve_source(BASE, "../../../hoppscotch-common/src/A.vue?vue&type=script")
        == "packages/hoppscotch-common/src/A.vue"
    )
    assert o.resolve_source(BASE, "../../src/B.vue") == "packages/app-web/src/B.vue"


def test_infer_base_lets_the_indexed_files_decide():
    sources = ["../../src/x.ts", "../../src/y.ts", "../../../shared/src/z.ts"]
    indexed = {"packages/app-web/src/x.ts", "packages/app-web/src/y.ts"}
    cands = o.base_candidates(["packages/shared/src", "packages/app-web/src"])
    assert cands == ["packages/shared/dist/assets", "packages/app-web/dist/assets"]
    assert o.infer_base(sources, indexed, cands) == "packages/app-web/dist/assets"
    assert o.infer_base(sources, set(), cands) == cands[0]  # nothing indexed: the first candidate


def test_library_and_repo_vue_classification():
    pnpm = "node_modules/.pnpm/@hoppscotch+ui@0.2.6_x/node_modules/@hoppscotch/ui/dist/index.js"
    assert o.library_of(pnpm) == "@hoppscotch/ui"
    assert o.library_of("node_modules/vue-router/dist/x.mjs") == "vue-router"
    assert o.library_of("packages/app/src/A.vue") is None
    assert o.is_repo_vue("packages/app/src/A.vue") and not o.is_repo_vue(pnpm)
    assert not o.is_repo_vue("../outside/A.vue") and not o.is_repo_vue("packages/app/src/a.ts")


# ------------------------------------------------------------------------------------------ attribute
SETUP_A = "setup(e){const t=useThing();return ()=>h('div',t.value+e.label+'alpha-alpha')}"
SETUP_B = "setup(e){const r=ref(0);return ()=>h('button',{onClick:()=>r.value++},'beta-beta-beta')}"


def _two_module_chunk() -> o.LoadedChunk:
    a = "const A={__name:'Alpha'," + SETUP_A + "};"
    b = "const B={__name:'Beta'," + SETUP_B + "};"
    return _chunk(
        "https://x.test/assets/index-1.js",
        [("../../../app/src/components/Alpha.vue", a), ("../../../app/src/components/Beta.vue", b)],
    )


def test_attribute_names_the_file_of_a_unique_setup_text():
    att = o.attribute([("setup", SETUP_B)], [_two_module_chunk()], "packages/app-web/dist/assets")
    assert att.status == "ok" and att.path == "packages/app/src/components/Beta.vue"
    assert att.repo_vue and att.vote_share == 1.0 and att.chunk == "index-1.js"


def test_attribute_reports_why_it_cannot_decide():
    chunk, base = _two_module_chunk(), BASE
    assert o.attribute([("setup", "x" * 60)], [chunk], base).status == "absent"
    assert o.attribute([("setup", "short")], [chunk], base).status == "no_text"
    twin = _chunk(
        "https://x.test/assets/other.js", [("../../../app/src/Other.vue", "const O={" + SETUP_A + "};")]
    )
    assert o.attribute([("setup", SETUP_A)], [chunk, twin], base).status == "ambiguous"  # two chunks
    dup = _chunk("https://x.test/assets/dup.js", [("../../../app/src/D.vue", SETUP_A + SETUP_A)])
    assert o.attribute([("setup", SETUP_A)], [dup], base).status == "ambiguous"  # twice in one
    chunk.map = None
    assert o.attribute([("setup", SETUP_B)], [chunk], base).status == "no_map"


def test_a_span_without_a_majority_source_is_not_decided():
    mixed = _chunk(
        "https://x.test/assets/mixed.js",
        [
            ("../../../app/src/A.vue", "a" * 20),
            ("../../../app/src/B.vue", "b" * 20),
            ("../../../app/src/C.vue", "c" * 20),
        ],
    )
    # anchor text that straddles all three modules: 1/3 of the segments each, no plurality above one half
    mixed.text = Text("a" * 20 + "b" * 20 + "c" * 20)
    att = o.attribute([("setup", "a" * 20 + "b" * 20 + "c" * 20)], [mixed], BASE)
    assert att.status == "no_majority" and att.vote_share < 0.5 and att.path is None


def test_attribute_falls_through_to_the_next_text_kind():
    chunk = _two_module_chunk()
    att = o.attribute([("setup", "z" * 50), ("render", SETUP_A)], [chunk], BASE)
    assert att.status == "ok" and att.kind == "render" and att.path.endswith("Alpha.vue")


# -------------------------------------------------------------------------------------------- chain
def _state(chain: list[str], types: dict[str, o.ComponentType]) -> o.State:
    el = o.SeenElement("button", "Save", 0, "el_1", True, 0, tuple(chain))
    return o.State("/ []", "https://x.test/", "/", (), None, [el], types)


def _att(
    path: str | None, *, status: str = "ok", repo: bool = False, pkg: str | None = None
) -> o.Attribution:
    return o.Attribution(status, path=path, repo_vue=repo, package=pkg, vote_share=1.0)


def _label(chain: list[tuple[str, o.Attribution]]):
    types = {
        f"t{i}": o.ComponentType(name, ((("setup"), f"text-{i}" * 10),)) for i, (name, _) in enumerate(chain)
    }
    atts = {o.type_key(types[f"t{i}"]): a for i, (_, a) in enumerate(chain)}
    return o.label_state(_state(list(types), types), atts)


def test_owner_is_the_first_repo_vue_on_the_chain_after_library_wrappers():
    labels, why = _label(
        [
            ("Button", _att("node_modules/ui/x.js", pkg="ui")),
            ("Link", _att("node_modules/ui/y.js", pkg="ui")),
            ("Header", _att("packages/app/src/Header.vue", repo=True)),
            ("Layout", _att("packages/app/src/Layout.vue", repo=True)),
        ]
    )
    assert not why and len(labels) == 1
    assert labels[0].oracle_file == "packages/app/src/Header.vue" and labels[0].owner_hops == 2
    assert labels[0].owner_chain[0].startswith("Button (ui)")


def test_an_unresolved_type_before_the_owner_makes_the_label_unsafe():
    labels, why = _label(
        [("Mystery", _att(None, status="absent")), ("Header", _att("packages/app/src/Header.vue", repo=True))]
    )
    assert labels == [] and why == {"unattributed_before_owner": 1}


def test_library_only_chain_and_missing_vnode_are_counted_not_labelled():
    labels, why = _label([("Button", _att("node_modules/ui/x.js", pkg="ui"))])
    assert labels == [] and why == {"library_only": 1}
    state = _state([], {})
    state.elements[0] = o.SeenElement("textbox", "", 0, "el_2", False, 0, ())
    assert o.label_state(state, {}) == ([], {"no_vnode": 1})
    state.elements[0] = o.SeenElement("textbox", "", 0, "el_3", True, 0, ())
    assert o.label_state(state, {}) == ([], {"no_owner_instance": 1})


# --------------------------------------------------------------------------------------------- drift
def _index(files: dict[str, str]) -> CodeIndex:
    return CodeIndex(
        target="t",
        repo="o/r",
        ref="main",
        snapshot_id="s",
        adapter="vue",
        files=[CodeFile(path=p, kind=FileClass.COMPONENT, sha=s) for p, s in files.items()],
    )


def test_drift_counts_same_differs_and_absent_against_the_indexed_blob_sha():
    same, changed = "<template/>\n", "<template>new</template>\n"
    text, data = build_chunk(
        [
            ("../../../app/src/Same.vue", "a" * 12),
            ("../../../app/src/Diff.vue", "b" * 12),
            ("../../../app/src/Gone.vue", "c" * 12),
            ("../../../app/src/util.ts", "d" * 12),
        ]
    )
    data["sourcesContent"] = [same, changed, "x", "y"]
    chunk = o.LoadedChunk("https://x.test/assets/i.js", Text(text), SourceMap(data), {"bytes": 1})
    index = _index(
        {
            "packages/app/src/Same.vue": git_blob_sha(same),
            "packages/app/src/Diff.vue": git_blob_sha("<template>old</template>\n"),
            "packages/app/src/util.ts": "whatever",
        }
    )
    out = o.drift([chunk], BASE, index)
    assert (out["vue_sources"], out["same"], out["differs"], out["absent_from_main"]) == (3, 1, 1, 1)
    assert out["differing"] == ["packages/app/src/Diff.vue"] and out["absent"] == [
        "packages/app/src/Gone.vue"
    ]
    assert "2 of 3" in out["summary"] and out["by_chunk"]["i.js"]["vue_sources"] == 3
    assert o.drift([chunk], BASE, None) == {"status": "no code index"}


def test_pairing_checks_flag_a_map_that_does_not_fit_its_script():
    chunk = _two_module_chunk()
    ok = o.pairing_checks([chunk])
    assert ok["mismatched"] == [] and ok["with_map"] == 1
    chunk.map.file = "someother.js"
    assert o.pairing_checks([chunk])["mismatched"] == [chunk.name]


def test_attribution_checks_report_basename_agreement_and_unanimity():
    t = {
        "t0": o.ComponentType("Header", (("setup", "h" * 50),)),
        "t1": o.ComponentType("Other", (("setup", "o" * 50),)),
    }
    atts = {
        o.type_key(t["t0"]): _att("packages/app/src/Header.vue", repo=True),
        o.type_key(t["t1"]): _att("packages/app/src/Different.vue", repo=True),
    }
    st = o.State("/", "u", "/", (), None, [], t)
    checks = o.attribution_checks([st], atts)
    assert checks["basename_equals_type_name"]["k"] == 1 and checks["basename_equals_type_name"]["n"] == 2
    assert checks["vote_share_unanimous"]["k"] == 2 and checks["attribution_status"] == {"ok": 2}


def test_owner_text_check_looks_in_the_file_source_and_in_the_keys_it_uses():
    from blastradius.evalkit.oracle_score import Label
    from blastradius.models import KeyUse

    text, data = build_chunk(
        [("../../../app/src/Editor.vue", "a" * 12), ("../../../app/src/Menu.vue", "b" * 12)]
    )
    data["sourcesContent"] = ["<button>Save changes</button>", "<template>{{ t('menu.open') }}</template>"]
    chunk = o.LoadedChunk("https://x.test/assets/i.js", Text(text), SourceMap(data), {"bytes": 1})
    index = _index({"packages/app/src/Editor.vue": "s1", "packages/app/src/Menu.vue": "s2"}).model_copy(
        update={
            "i18n": {"menu.open": "Open file", "menu.unused": "Never shown"},
            "key_uses": [KeyUse(file="packages/app/src/Menu.vue", key="menu.open")],
        }
    )

    def label(name: str, file: str, hops: int = 0) -> Label:
        return Label("/", (), None, "button", name, 0, file, hops)

    labels = [
        label("Save changes", "packages/app/src/Editor.vue"),  # literal in the source
        label("Open file", "packages/app/src/Menu.vue", 1),  # value of a key the file uses
        label("Never shown", "packages/app/src/Menu.vue", 1),  # key exists but this file does not use it
        label("Missing", "packages/app/src/Editor.vue"),  # not in the file at all
        label("", "packages/app/src/Editor.vue"),  # nameless: cannot be checked
    ]
    out = o.owner_text_check(labels, [chunk], "packages/app-web/dist/assets", index)
    assert (out["checked"], out["nameless_unchecked"]) == (4, 1)
    assert out["name_found_in_owner_file"]["k"] == 2 and out["name_found_in_owner_file"]["n"] == 4
    hops1 = out["by_owner_hops"]["1"]
    assert (hops1["k"], hops1["n"], hops1["p"]) == (1, 2, 0.5)
    assert hops1["wilson95_lo"] == pytest.approx(0.0945, abs=1e-3)
    assert "plausibility only" in out["note"] and len(out["not_found_sample"]) == 2
