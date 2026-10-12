"""blastradius CLI.

Stage commands resolve their implementation lazily by name, so each stage package owns its function and
this file never changes when a stage is implemented. Contracts (all take a RunContext first):

  crawl   blastradius.crawl.explorer:run_crawl(run, cfg, llm, *, goals=None, source="live", recorded_dir=None,
                                               max_actions=None)            -> CrawlSummary
  ingest  blastradius.spec.ingest:run_ingest(run, cfg, llm)                 -> list[Requirement]
  index   blastradius.code.index:run_index(run, cfg, *, ref=None, pr_number=None) -> CodeIndex
  load    blastradius.graph.load:run_load(run, cfg, store)                  -> dict
  link    blastradius.link.link:run_link(run, cfg, store, llm)              -> dict
  gaps    blastradius.link.gaps:run_gaps(run, cfg, store, llm, *, recrawl=False) -> dict
  flows   blastradius.link.flows:run_flows(run, cfg, store)                 -> list[Flow]
  pr      blastradius.reason.pipeline:run_pr(run, cfg, pr_number, store, llm) -> dict
  eval    blastradius.evalkit.run:run_eval(run, cfg, store, llm, which)     -> dict
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .config import RUNS_DIR
from .run import RunContext
from .targets import TargetConfig
from .trace import load_trace, render_tree

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Intent-aware change-impact agent: crawl, ingest, graph, and reason about a PR.")
trace_app = typer.Typer(no_args_is_help=True, help="Inspect a run's trace.")
app.add_typer(trace_app, name="trace")
console = Console()


def _resolve(dotted: str) -> Any:
    module, _, func = dotted.partition(":")
    try:
        return getattr(importlib.import_module(module), func)
    except (ImportError, AttributeError) as exc:
        console.print(f"[red]not implemented yet:[/red] {dotted} ({exc})")
        raise typer.Exit(2) from exc


def _ctx(target: str, run: str | None, *, create: bool = False, new: bool = False) -> tuple[RunContext, TargetConfig]:
    """Resolve the run a stage works in.

    Explicit --run wins (created if absent and create=True). With no --run, a stage attaches to the latest run
    when it belongs to the same target, so `crawl` then `ingest` then `index` share one run directory; `new`
    forces a fresh run. Stages that only read (load, link, ...) never create.
    """
    cfg = TargetConfig.load(target)
    if run is not None:
        if create and not (RUNS_DIR / run / "manifest.json").exists():
            return RunContext.create(target, run_id=run), cfg
        return RunContext.attach(run), cfg
    if not new:
        try:
            latest = RunContext.attach("latest")
            if latest.target == target:
                return latest, cfg
        except FileNotFoundError:
            pass
    if create or new:
        return RunContext.create(target), cfg
    raise typer.BadParameter("no run to attach to; run `blastradius crawl` (or ingest/index) first")


def _llm(ctx: RunContext, offline: bool) -> Any:
    from .llm import LLM

    return LLM(ctx, offline=offline)


def _store(ctx: RunContext) -> Any:
    return _resolve("blastradius.graph.store:GraphStore").from_settings(ctx.settings, ctx)


TargetOpt = typer.Option("hoppscotch", "--target", "-t", help="targets/<name>.yaml")
RunOpt = typer.Option(None, "--run", "-r", help="run id (default: latest)")
OfflineOpt = typer.Option(False, "--offline", help="never call the API; use cached responses only")
GoalOpt = typer.Option([], "--goal", help="requirement text to search for (repeatable)")
WhichOpt = typer.Option([], "--which", help="subset: invariants, recall, oracle, judge, stability")


@app.command()
def doctor(target: str = TargetOpt, no_models: bool = typer.Option(False, "--no-models"),
           no_browser: bool = typer.Option(False, "--no-browser"),
           no_network: bool = typer.Option(False, "--no-network"),
           as_json: bool = typer.Option(False, "--json")) -> None:
    """Preflight: environment, key, models, Docker, Neo4j, browser, target reachability. No spend."""
    from .doctor import run_checks

    checks = run_checks(target, with_models=not no_models, with_browser=not no_browser, with_network=not no_network)
    if as_json:
        console.print_json(json.dumps([c.__dict__ for c in checks]))
    else:
        table = Table(show_lines=False)
        for col in ("check", "status", "detail", "fix"):
            table.add_column(col)
        style = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}
        for c in checks:
            table.add_row(c.name, f"[{style[c.status]}]{c.status}[/{style[c.status]}]", c.detail, c.fix)
        console.print(table)
    if any(c.status == "FAIL" for c in checks):
        raise typer.Exit(1)


@trace_app.command("tree")
def trace_tree(run: str | None = typer.Argument(None, help="run id (default: latest)")) -> None:
    """Print the span tree for a run; spans with no end are marked OPEN (a crash)."""
    ctx = RunContext.attach(run or "latest")
    console.print(render_tree(load_trace(ctx.run_dir / "trace.jsonl")))
    console.print(f"\nledger: {ctx.ledger.summary()}  failures: {len(ctx.failures.items)}")


@app.command()
def crawl(target: str = TargetOpt, run: str | None = RunOpt, offline: bool = OfflineOpt,
          source: str = typer.Option("live", help="live | recorded"),
          recorded_dir: str | None = typer.Option(None, help="folder of html/png captures (source=recorded)"),
          max_actions: int | None = typer.Option(None),
          new: bool = typer.Option(False, "--new", help="start a fresh run even if a latest run exists"),
          goal: list[str] = GoalOpt) -> None:
    """Explore the live app and capture screens, elements and transitions."""
    ctx, cfg = _ctx(target, run, create=True, new=new)
    fn = _resolve("blastradius.crawl.explorer:run_crawl")
    with ctx.stage("crawl"):
        summary = fn(ctx, cfg, _llm(ctx, offline), goals=goal or None, source=source, recorded_dir=recorded_dir,
                     max_actions=max_actions)
    console.print(f"run {ctx.run_id}: {summary}")


@app.command()
def ingest(target: str = TargetOpt, run: str | None = RunOpt, offline: bool = OfflineOpt) -> None:
    """Fetch the spec and extract atomic, quote-verified requirements."""
    ctx, cfg = _ctx(target, run, create=True)
    fn = _resolve("blastradius.spec.ingest:run_ingest")
    with ctx.stage("ingest"):
        reqs = fn(ctx, cfg, _llm(ctx, offline))
    console.print(f"run {ctx.run_id}: {len(reqs)} requirements")


@app.command()
def index(target: str = TargetOpt, run: str | None = RunOpt, ref: str | None = typer.Option(None),
          pr: int | None = typer.Option(None, help="record this PR number in the index stats; the head overlay itself is applied by `pr`")) -> None:
    """Index the target's frontend source into files, render/import edges, routes and i18n keys."""
    ctx, cfg = _ctx(target, run, create=True)
    fn = _resolve("blastradius.code.index:run_index")
    with ctx.stage("index"):
        idx = fn(ctx, cfg, ref=ref, pr_number=pr)
    console.print(f"run {ctx.run_id}: {len(idx.files)} files, {len(idx.edges)} edges, snapshot {idx.snapshot_id[:10]}")


@app.command()
def load(target: str = TargetOpt, run: str | None = RunOpt) -> None:
    """Load crawl, spec and code artifacts into Neo4j."""
    ctx, cfg = _ctx(target, run)
    fn = _resolve("blastradius.graph.load:run_load")
    with ctx.stage("load"), _store(ctx) as store:
        out = fn(ctx, cfg, store)
    console.print(out)


@app.command()
def link(target: str = TargetOpt, run: str | None = RunOpt, offline: bool = OfflineOpt) -> None:
    """Link UI to code and requirements to UI; write evidence-bearing edges."""
    ctx, cfg = _ctx(target, run)
    fn = _resolve("blastradius.link.link:run_link")
    with ctx.stage("link"), _store(ctx) as store:
        out = fn(ctx, cfg, store, _llm(ctx, offline))
    console.print(out)


@app.command()
def gaps(target: str = TargetOpt, run: str | None = RunOpt, offline: bool = OfflineOpt,
         recrawl: bool = typer.Option(False, help="bounded goal-directed re-crawl for unresolved requirements")) -> None:
    """Materialise Gap nodes (absence with recorded search effort)."""
    ctx, cfg = _ctx(target, run)
    fn = _resolve("blastradius.link.gaps:run_gaps")
    with ctx.stage("gaps"), _store(ctx) as store:
        out = fn(ctx, cfg, store, _llm(ctx, offline), recrawl=recrawl)
    console.print(out)


@app.command()
def flows(target: str = TargetOpt, run: str | None = RunOpt) -> None:
    """Mine user flows from the transition graph."""
    ctx, cfg = _ctx(target, run)
    fn = _resolve("blastradius.link.flows:run_flows")
    with ctx.stage("flows"), _store(ctx) as store:
        out = fn(ctx, cfg, store)
    console.print(f"{len(out)} flows")


@app.command()
def pr(number: int = typer.Argument(..., help="PR number in the target's repo"), target: str = TargetOpt,
       run: str | None = RunOpt, offline: bool = OfflineOpt) -> None:
    """Blast-radius report for a pull request."""
    ctx, cfg = _ctx(target, run)
    fn = _resolve("blastradius.reason.pipeline:run_pr")
    with ctx.stage("pr", pr=number), _store(ctx) as store:
        out = fn(ctx, cfg, number, store, _llm(ctx, offline))
    console.print(out)


@app.command(name="eval")
def eval_cmd(target: str = TargetOpt, run: str | None = RunOpt, offline: bool = OfflineOpt,
             which: list[str] = WhichOpt) -> None:
    """Run the evaluation suite and print honest numbers."""
    ctx, cfg = _ctx(target, run)
    fn = _resolve("blastradius.evalkit.run:run_eval")
    with ctx.stage("eval"), _store(ctx) as store:
        out = fn(ctx, cfg, store, _llm(ctx, offline), which or None)
    console.print(out)


if __name__ == "__main__":
    app()
