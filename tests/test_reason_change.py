"""reason.change and reason.thresholds: what a diff says about one file, and the pre-registered numbers.

The patches are the real ones of the three demo PRs (cached under tests/fixtures/reason), so the expectations
below are statements about Hoppscotch, not about hand-made diffs (cases 35, 40, 76).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_reason_support import COMMON, LOCALE, REASON, cfg

from blastradius.code.fileclass import classify_file
from blastradius.models import MAX_HOPS, FileClass
from blastradius.reason.change import ChangeKind, analyse_patch, diff_locale
from blastradius.reason.thresholds import THRESHOLDS_PATH, Thresholds, load_thresholds


def facts(pr: int, path: str):
    row = next(f for f in json.loads((REASON / f"files_{pr}.json").read_text()) if f["filename"] == path)
    return analyse_patch(path, row["status"], classify_file(path, cfg().code), row.get("patch"),
                         row["additions"], row["deletions"])


def test_additive_service_names_the_touched_class_but_adds_no_top_level_symbol():
    f = facts(6645, COMMON + "services/current-environment-value.service.ts")
    assert f.kind is ChangeKind.ADDITIVE_API and f.deletions == 0
    assert f.touched == ("CurrentValueService",) and f.symbols_known
    assert f.added == () and f.removed == ()


def test_new_file_is_additive_and_lists_its_exports():
    f = facts(6641, COMMON + "helpers/diff.ts")
    assert f.kind is ChangeKind.ADDITIVE_API
    assert f.added == ("DiffLine", "computeLineDiff", "getUtf8ByteSize")


def test_single_added_property_inside_a_function_is_body_not_additive():
    # `source: collection.source,` extends an object literal: callers' behaviour changes, so no narrowing
    f = facts(6658, COMMON + "helpers/collection/collection.ts")
    assert f.kind is ChangeKind.BODY and f.touched == ("transformCollectionForImport",)


def test_type_declaration_only_change_is_type_only():
    f = facts(6658, COMMON + "helpers/backend/helpers.ts")
    assert f.kind is ChangeKind.TYPE_ONLY and f.touched == ("CollectionDataProps",)


def test_edited_exported_constant_is_a_signature_change_with_touched_names():
    f = facts(6658, "packages/hoppscotch-data/src/collection/index.ts")
    assert f.kind is ChangeKind.SIGNATURE
    assert set(f.touched) == {"CollectionSchemaVersion", "HoppCollection"}


def test_vue_files_never_claim_symbols():
    f = facts(6645, COMMON + "components/environments/Selector.vue")
    assert f.kind is ChangeKind.BODY and f.touched == () and not f.symbols_known


@pytest.mark.parametrize(("path", "kind"), [
    (LOCALE, ChangeKind.COPY_ONLY),
    (COMMON + "components.d.ts", ChangeKind.GENERATED),
    (COMMON + "services/__tests__/current-environment-value.service.spec.ts", ChangeKind.TEST),
])
def test_file_class_decides_before_the_diff_is_read(path, kind):
    assert facts(6645, path).kind is kind


def test_removed_file_and_missing_patch_are_conservative():
    c = cfg().code
    assert analyse_patch(COMMON + "a.ts", "removed", FileClass.UTIL, None, 0, 9).kind is ChangeKind.DELETE
    missing = analyse_patch(COMMON + "a.ts", "modified", FileClass.UTIL, None, 5, 5)
    assert missing.kind is ChangeKind.BODY and missing.patch_missing        # never narrows what it cannot read
    assert classify_file(COMMON + "a.ts", c) is FileClass.SOURCE_OTHER


def test_comment_only_change_is_type_only():
    patch = "@@ -1,3 +1,3 @@\n // old\n-// note one\n+// note two\n export const a = 1\n"
    assert analyse_patch(COMMON + "x.ts", "modified", FileClass.UTIL, patch, 1, 1).kind is ChangeKind.TYPE_ONLY


def test_a_block_closed_between_the_type_and_the_change_is_not_type_context():
    patch = ("@@ -5,6 +5,6 @@\n export type A = {\n   a: string\n }\n \n export function f() {\n-  return 1\n"
             "+  return 2\n }\n")
    assert analyse_patch(COMMON + "x.ts", "modified", FileClass.UTIL, patch, 1, 1).kind is ChangeKind.BODY


# ------------------------------------------------------------------------------------------ locales
def _en(name: str) -> str:
    import gzip

    with gzip.open(REASON / name, "rt", encoding="utf-8") as fh:
        return fh.read()


def test_6641_adds_24_leaf_strings_not_26_and_ignores_the_eol_hunk():
    # RECON section 5: "+27/-1: 24 leaf strings + 2 braces; the -1/+1 is a trailing-newline removal"
    d = diff_locale(LOCALE, _en("locale_base_ac145e7.json.gz"), _en("locale_head_6641.json.gz"))
    assert len(d.added) == 24 and not d.changed and not d.removed
    assert d.added["response.compare"] == "Compare"
    assert d.problem is None


def test_6645_adds_two_strings():
    d = diff_locale(LOCALE, _en("locale_base_ac145e7.json.gz"), _en("locale_head_6645.json.gz"))
    assert sorted(d.added) == ["action.back", "environment.edit_current_value"]


def test_eol_only_difference_is_an_empty_diff():
    base = '{"a": {"b": "x\\ny"}}\n'
    head = '{"a": {"b": "x\\ny"}}'
    d = diff_locale("l.json", base, head)
    assert d.empty and d.eol_only and d.keys == []
    crlf = diff_locale("l.json", '{"a": "x\\r\\ny"}', '{"a": "x\\ny"}')
    assert crlf.empty


def test_changed_and_removed_keys_and_unreadable_versions():
    d = diff_locale("l.json", '{"a": "One", "b": "Two", "c": {"d": "Three"}}', '{"a": "One!", "c": {"d": "Three"}}')
    assert d.changed == {"a": ("One", "One!")} and d.removed == {"b": "Two"} and not d.added
    assert diff_locale("l.json", "{", '{"a": "x"}').problem == "base_unreadable"
    assert diff_locale("l.json", '{"a": "x"}', None).problem == "head_unreadable"
    assert diff_locale("l.json", None, '{"a": "x"}').added == {"a": "x"}      # a new file: every key is added


# ---------------------------------------------------------------------------------------- thresholds
def test_thresholds_file_is_valid_and_agrees_with_models():
    t = load_thresholds()
    assert t.retrieval.max_hops == MAX_HOPS == 2
    assert t.grade.breadth_fraction_h2 == 0.25 and t.retrieval.tier2_cap == 10 and t.retrieval.checklist_max == 8
    assert t.judge.max_candidates == 12 and t.judge.max_calls == 6 and t.judge.max_reason_words == 25
    # every cap is "possible" at most: a cap that reached "likely" would be a raise in disguise
    assert all(v < 0.5 for v in t.caps.model_dump().values())


def test_thresholds_loader_rejects_a_changed_hop_cap(tmp_path: Path):
    raw = json.loads(THRESHOLDS_PATH.read_text())
    raw["retrieval"]["max_hops"] = 3
    bad = tmp_path / "t.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        Thresholds.model_validate(json.loads(bad.read_text()))
    with pytest.raises(FileNotFoundError):
        load_thresholds(tmp_path / "missing.json")
