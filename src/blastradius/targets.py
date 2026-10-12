"""Target configuration (targets/<name>.yaml). Adding a target must not touch the core pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import TARGETS_DIR


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnchorConfig(_Cfg):
    """Per-target sanity anchor: a capture that fails it is SUSPECT, whatever the HTTP status says."""

    title_regex: str | None = None
    min_interactive: int = 3
    required_selectors: list[str] = Field(default_factory=list)


class CrawlConfig(_Cfg):
    seed_urls: list[str]
    egress_allow: list[str]                      # hostnames; subdomains of an entry are allowed
    max_actions: int = 150
    max_depth: int = 4
    wall_clock_s: int = 900
    action_delay_ms: int = 700
    viewport: tuple[int, int] = (1280, 800)
    locale: str = "en-US"
    timezone: str = "UTC"
    block_service_workers: bool = True
    allow_local_writes: bool = True              # creating collections/envs/requests that live in localStorage
    send_allowed_urls: list[str] = Field(default_factory=list)   # the only URLs a "Send" action may hit
    widget_selectors: list[str] = Field(default_factory=list)    # EXTERNAL_WIDGET: never interact
    dismiss_selectors: list[str] = Field(default_factory=list)   # deterministic close buttons
    deny_url_patterns: list[str] = Field(default_factory=list)   # regexes never navigated to (/admin, /orgs)
    destructive_words: list[str] = Field(default_factory=lambda: [
        "delete", "remove", "clear", "reset", "sign out", "log out", "logout", "revoke", "deactivate", "purchase", "pay",
        "discard", "leave", "wipe", "erase", "empty", "disconnect", "unlink", "terminate", "uninstall"])
    auth_words: list[str] = Field(default_factory=lambda: ["login", "log in", "sign in", "sign up", "register"])
    anchor: AnchorConfig = Field(default_factory=AnchorConfig)
    version_regex: str | None = None             # group 1 = the deployed app version string, searched in the shell/bundle


class SpecConfig(_Cfg):
    kind: Literal["llms_full", "gherkin", "markdown_urls"]
    url: str | None = None
    repo: str | None = None                      # gherkin: owner/name
    ref: str | None = None
    paths: list[str] = Field(default_factory=list)            # gherkin dirs / globs
    include_pages: list[str] = Field(default_factory=list)    # regexes on page title or path
    exclude_pages: list[str] = Field(default_factory=list)
    edition_default: Literal["cloud-web", "desktop", "self-host", "all", "unknown"] = "unknown"
    max_requirements: int = 200
    page_char_budget: int = 14000


class CodeConfig(_Cfg):
    repo: str                                    # owner/name
    ref: str = "main"
    adapter: Literal["vue", "twig"]
    roots: list[str]                             # source roots to index
    locale_files: list[str] = Field(default_factory=list)
    auto_import_dts: str | None = None
    global_files: list[str] = Field(default_factory=list)   # layout/global style: widest radius
    hub_packages: list[str] = Field(default_factory=list)   # e.g. @hoppscotch/data
    alias: dict[str, str] = Field(default_factory=dict)     # import alias -> repo path prefix
    generated_files: list[str] = Field(default_factory=list)   # codegen outputs (imported, often gitignored)
    components_dirs: list[str] = Field(default_factory=list)   # auto-import component roots (namespace fallback)


class TargetConfig(_Cfg):
    name: str
    display_name: str
    base_url: str
    crawl: CrawlConfig
    spec: SpecConfig
    code: CodeConfig
    notes: str = ""

    @classmethod
    def load(cls, name: str, targets_dir: Path | None = None) -> "TargetConfig":
        path = (targets_dir or TARGETS_DIR) / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"no target config at {path}")
        return cls.model_validate(yaml.safe_load(path.read_text()))
