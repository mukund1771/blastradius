"""Run resolution: stages of one pipeline share a run directory; --new forces a fresh one."""

from __future__ import annotations

import pytest
import typer

import blastradius.run as runmod
from blastradius import cli


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runmod, "RUNS_DIR", tmp_path)
    return tmp_path


def test_stages_share_the_latest_run_for_the_same_target(runs):
    first, _ = cli._ctx("hoppscotch", None, create=True)
    second, _ = cli._ctx("hoppscotch", None, create=True)
    assert first.run_id == second.run_id


def test_new_forces_a_fresh_run_even_in_the_same_second(runs):
    first, _ = cli._ctx("hoppscotch", None, create=True)
    fresh, _ = cli._ctx("hoppscotch", None, create=True, new=True)
    assert fresh.run_id != first.run_id
    assert cli._ctx("hoppscotch", None, create=True)[0].run_id == fresh.run_id  # latest moved


def test_read_only_stage_without_a_run_refuses_instead_of_inventing_one(runs):
    with pytest.raises(typer.BadParameter):
        cli._ctx("hoppscotch", None)


def test_explicit_run_is_created_once_and_reattached(runs):
    a, _ = cli._ctx("hoppscotch", "my-run", create=True)
    b, _ = cli._ctx("hoppscotch", "my-run", create=True)
    assert a.run_dir == b.run_dir and (runs / "my-run" / "manifest.json").exists()
