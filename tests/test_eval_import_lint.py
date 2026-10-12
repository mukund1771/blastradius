"""Isolation of the evaluation from the thing it evaluates (D21, D23; cases 53, 63): an AST import lint in
both directions. The product packages never import the eval kit, and the eval kit never imports the linker.

Dynamic imports (`importlib.import_module("...")`, `__import__`) are scanned too, so the rule is not
defeated by a string."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "blastradius"
PRODUCT = ("link", "reason", "graph")
FORBIDDEN_FOR_EVAL = ("blastradius.link",)


def _absolute(module_parts: list[str], node: ast.ImportFrom) -> str:
    """The absolute dotted module an ImportFrom refers to, given the importing module's dotted parts."""
    if node.level == 0:
        return node.module or ""
    package = module_parts[:-1]  # parts of the containing package
    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
    return ".".join([*base, *([node.module] if node.module else [])])


def imported_modules(source: str, module: str) -> set[str]:
    """Every module the source may import, as absolute dotted names (static and constant dynamic imports)."""
    tree = ast.parse(source)
    parts = module.split(".")
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(parts, node)
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)  # `from blastradius import evalkit`
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
    return out


def violations(package_dir: Path, forbidden: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for path in sorted(package_dir.rglob("*.py")):
        rel = path.relative_to(SRC.parent).with_suffix("")
        module = ".".join(rel.parts)
        for name in sorted(imported_modules(path.read_text(encoding="utf-8"), module)):
            if any(name == f or name.startswith(f + ".") for f in forbidden):
                found.append(f"{module} imports {name}")
    return found


@pytest.mark.parametrize("package", PRODUCT)
def test_product_packages_never_import_the_eval_kit(package: str):
    assert (SRC / package).is_dir()
    assert violations(SRC / package, ("blastradius.evalkit",)) == []


def test_eval_kit_never_imports_the_linker():
    assert (SRC / "evalkit").is_dir()
    assert violations(SRC / "evalkit", FORBIDDEN_FOR_EVAL) == []


def test_the_scoring_half_depends_only_on_shared_contracts():
    """oracle_score is the part that touches the linker's output: it may use models and stats, nothing else of
    the product, so it reads Link rows exactly as links/links.jsonl holds them."""
    path = SRC / "evalkit" / "oracle_score.py"
    mods = {
        m
        for m in imported_modules(path.read_text(encoding="utf-8"), "blastradius.evalkit.oracle_score")
        if m.startswith("blastradius")
    }
    assert mods <= {
        "blastradius.models",
        "blastradius.models.Link",
        "blastradius.models.ScreenState",
        "blastradius.evalkit",
        "blastradius.evalkit.stats",
        "blastradius.evalkit.stats.Proportion",
    }


# -------------------------------------------------------------------------------------- guard the guard
@pytest.mark.parametrize(
    ("source", "module", "hit"),
    [
        ("import blastradius.evalkit.run", "blastradius.link.x", True),
        ("from blastradius.evalkit import stats", "blastradius.reason.y", True),
        ("from blastradius import evalkit", "blastradius.graph.z", True),
        ("from ..evalkit import run", "blastradius.link.cascade", True),
        ("from ..evalkit.run import run_eval", "blastradius.reason.pipeline", True),
        ("from .. import evalkit", "blastradius.link.link", True),
        (
            "import importlib\nimportlib.import_module('blastradius.evalkit.run')",
            "blastradius.reason.a",
            True,
        ),
        ("x = __import__('blastradius.evalkit')", "blastradius.reason.a", True),
        ("from ..models import Link", "blastradius.link.link", False),
        ("import json", "blastradius.graph.store", False),
    ],
)
def test_the_lint_detects_each_import_spelling(source: str, module: str, hit: bool):
    mods = imported_modules(source, module)
    assert any(m == "blastradius.evalkit" or m.startswith("blastradius.evalkit.") for m in mods) is hit


@pytest.mark.parametrize(
    ("source", "module", "hit"),
    [
        ("from ..link.text import tokens", "blastradius.evalkit.oracle_score", True),
        ("from .. import link", "blastradius.evalkit.run", True),
        ("import blastradius.link.link", "blastradius.evalkit.run", True),
        ("from ..models import Link", "blastradius.evalkit.run", False),
        ("from .stats import jaccard", "blastradius.evalkit.run", False),
    ],
)
def test_the_lint_detects_linker_imports_from_the_eval_kit(source: str, module: str, hit: bool):
    mods = imported_modules(source, module)
    assert any(m == "blastradius.link" or m.startswith("blastradius.link.") for m in mods) is hit
