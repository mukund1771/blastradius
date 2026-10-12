"""Paths, environment, settings. Secrets are read from the environment at use time and never stored."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
CACHE_DIR = DATA_DIR / "cache"
TARGETS_DIR = ROOT / "targets"
PACKAGE_VERSION = "0.1.0"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader. Existing environment variables win; empty values are ignored."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


class Settings(BaseModel):
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "blastradius-local"
    max_usd: float = 15.0
    trace_content: Literal["hash", "local"] = "hash"
    contact: str = ""

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        env = os.environ.get
        return cls(
            neo4j_uri=env("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=env("NEO4J_USER", "neo4j"),
            neo4j_password=env("NEO4J_PASSWORD", "blastradius-local"),
            max_usd=float(env("BLASTRADIUS_MAX_USD", "15")),
            trace_content="local" if env("BLASTRADIUS_TRACE_CONTENT", "hash") == "local" else "hash",
            contact=env("BLASTRADIUS_CONTACT", ""),
        )

    @staticmethod
    def has_api_key() -> bool:
        load_dotenv()
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def user_agent(self) -> str:
        suffix = f" (+{self.contact})" if self.contact else ""
        return f"blastradius/{PACKAGE_VERSION} (QA change-impact research crawler{suffix})"
