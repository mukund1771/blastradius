"""`blastradius doctor`: preflight with no spend. Exits non-zero on FAIL and says how to fix each problem."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import RUNS_DIR, Settings, load_dotenv
from .redact import redact_text
from .targets import TargetConfig


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str
    fix: str = ""


def _python() -> Check:
    ok = sys.version_info >= (3, 12)
    return Check("python", "PASS" if ok else "FAIL", sys.version.split()[0], "" if ok else "Need Python >= 3.12")


def _packages() -> Check:
    missing = []
    for mod in ("anthropic", "pydantic", "playwright", "neo4j", "typer", "yaml", "tree_sitter",
                "tree_sitter_typescript", "rank_bm25", "httpx"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return Check("packages", "FAIL", f"missing: {', '.join(missing)}", "pip install -e '.[dev]'")
    return Check("packages", "PASS", "all importable")


def _api_key() -> Check:
    load_dotenv()
    if Settings.has_api_key():
        return Check("anthropic key", "PASS", "present (value never printed)")
    return Check("anthropic key", "FAIL", "ANTHROPIC_API_KEY is not set",
                 "Copy .env.example to .env and set ANTHROPIC_API_KEY (do not paste it into chat)")


def _models() -> list[Check]:
    if not Settings.has_api_key():
        return [Check("models", "WARN", "skipped: no API key")]
    import anthropic

    from .llm import TIER_PREFERENCE

    client = anthropic.Anthropic(max_retries=1, timeout=20.0)
    out = []
    for tier, prefs in TIER_PREFERENCE.items():
        found, tried = None, []
        for cand in prefs:
            try:
                client.models.retrieve(cand)
                found = cand
                break
            except anthropic.NotFoundError:
                tried.append(f"{cand}:404")
            except anthropic.AuthenticationError:
                return [Check("models", "FAIL", "authentication failed", "Check ANTHROPIC_API_KEY")]
            except anthropic.APIError as exc:
                tried.append(f"{cand}:{type(exc).__name__}")
        if found:
            note = f" (preferred unavailable: {', '.join(tried)})" if tried else ""
            out.append(Check(f"model[{tier}]", "PASS", f"{found}{note}"))
        else:
            out.append(Check(f"model[{tier}]", "FAIL", f"none of {prefs} retrievable ({', '.join(tried)})",
                             f"Set BLASTRADIUS_MODEL_{tier.upper()} to an id your key can use"))
    return out


def _docker() -> Check:
    if not shutil.which("docker"):
        return Check("docker", "FAIL", "docker CLI not found", "Install Docker Desktop")
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True,
                           timeout=15)
    except subprocess.TimeoutExpired:
        return Check("docker", "FAIL", "daemon not responding", "Start Docker Desktop")
    if r.returncode != 0:
        return Check("docker", "FAIL", "daemon is not running", "Start Docker Desktop, then `docker compose up -d`")
    return Check("docker", "PASS", f"server {r.stdout.strip()}")


def _neo4j(settings: Settings) -> Check:
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        driver.verify_connectivity()
        driver.close()
        return Check("neo4j", "PASS", settings.neo4j_uri)
    except Exception as exc:  # noqa: BLE001
        return Check("neo4j", "FAIL", f"{type(exc).__name__}: {redact_text(str(exc))[:120]}",
                     "docker compose up -d   (wait for the healthcheck), check NEO4J_PASSWORD in .env")


def _chromium() -> Check:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            version = browser.version
            browser.close()
        return Check("chromium", "PASS", f"headless launch ok ({version})")
    except Exception as exc:  # noqa: BLE001
        return Check("chromium", "FAIL", f"{type(exc).__name__}: {str(exc)[:120]}", "playwright install chromium")


def _reachability(target: str, settings: Settings) -> list[Check]:
    try:
        cfg = TargetConfig.load(target)
    except Exception as exc:  # noqa: BLE001
        return [Check("target config", "FAIL", str(exc)[:160], f"targets/{target}.yaml")]
    out = [Check("target config", "PASS", cfg.name)]
    urls = [("target", cfg.base_url)] + ([("spec", cfg.spec.url)] if cfg.spec.url else [])
    classify = None
    try:
        classify = importlib.import_module("blastradius.access.classify")
    except ImportError:
        pass
    for label, url in urls:
        try:
            r = httpx.get(url, headers={"User-Agent": settings.user_agent()}, timeout=20.0, follow_redirects=True)
        except Exception as exc:  # noqa: BLE001
            out.append(Check(f"{label} reachable", "FAIL", f"{type(exc).__name__}: {str(exc)[:100]}", "Check network"))
            continue
        detail = f"HTTP {r.status_code}, server={r.headers.get('server', '?')}"
        status = "PASS" if r.status_code < 400 else "FAIL"
        if classify is not None and hasattr(classify, "classify_http"):
            res = classify.classify_http(r.status_code, dict(r.headers), r.text[:20000], url)
            detail += f", access={res.verdict}"
            if str(res.verdict) not in ("AccessVerdict.OK", "OK"):
                status = "FAIL" if str(res.verdict).endswith("BLOCKED") else "WARN"
        out.append(Check(f"{label} reachable", status, detail,
                         "" if status == "PASS" else "Owner must allowlist the crawler or supply a recorded session"))
    return out


def _runs_dir() -> Check:
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=RUNS_DIR, delete=True):
            pass
        free_gb = shutil.disk_usage(RUNS_DIR).free / 1e9
        return Check("runs dir", "PASS" if free_gb > 2 else "WARN", f"{RUNS_DIR} writable, {free_gb:.0f} GB free")
    except OSError as exc:
        return Check("runs dir", "FAIL", str(exc), "Check permissions on data/runs")


def _redaction_canary() -> Check:
    canary = "sk-ant-api03-DOCTORCANARYDOCTORCANARY"
    ok = "CANARY" not in redact_text(f"key={canary}")
    return Check("redaction canary", "PASS" if ok else "FAIL", "secret patterns are scrubbed" if ok else "leak")


def run_checks(target: str = "hoppscotch", *, with_models: bool = True, with_browser: bool = True,
               with_network: bool = True) -> list[Check]:
    settings = Settings.load()
    checks = [_python(), _packages(), _api_key()]
    if with_models:
        checks += _models()
    docker = _docker()
    checks.append(docker)
    checks.append(_neo4j(settings) if docker.status == "PASS" else Check("neo4j", "WARN", "skipped: docker is down"))
    if with_browser:
        checks.append(_chromium())
    if with_network:
        checks += _reachability(target, settings)
    checks += [_runs_dir(), _redaction_canary()]
    return checks
