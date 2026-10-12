"""i18n: flatten a locale file and find `t('key')` uses in script and template text (D10; cases 32, 33).

Static keys are the signal the linker leans on (key -> English value -> files). Template literals such as
`t(`tab.${name}`)` become dynamic uses carrying the static prefix; arguments that are neither (a variable,
a call) are only counted, because there is no key to record.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# `t(`, `$t(`, `this.t(`, `i18n.t(`; a preceding word char or dot is some other function or member.
_CALL = re.compile(r"(?<![\w$.])(?<!function )(?:(?:this|i18n|\$i18n)\.)?\$?t\(\s*")
_STRING = re.compile(r"""(['"])((?:\\.|(?!\1)[^\\\n])*)\1""")


@dataclass
class KeyScan:
    static: list[str] = field(default_factory=list)
    dynamic_prefixes: list[str] = field(default_factory=list)
    unresolved: int = 0             # first argument is not a literal: nothing to record


def flatten_messages(data: Mapping[str, Any]) -> tuple[dict[str, str], int]:
    """Nested message tree -> {dotted.key: text}. Returns the map and the count of non-string leaves skipped."""
    out: dict[str, str] = {}
    skipped = 0

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        nonlocal skipped
        for key, value in node.items():
            path = f"{prefix}{key}"
            if isinstance(value, Mapping):
                walk(value, path + ".")
            elif isinstance(value, str):
                out[path] = value
            else:
                skipped += 1

    walk(data, "")
    return out, skipped


def _first_argument(text: str, start: int) -> str:
    """Source of the first call argument: up to the top-level `,` or the closing `)`."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            end = i + 1
            while end < n and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            i = end + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        i += 1
    return text[start:i].strip()


def scan_t_calls(text: str) -> KeyScan:
    scan = KeyScan()
    for m in _CALL.finditer(text):
        arg = _first_argument(text, m.end())
        if not arg:
            continue
        literal = _STRING.fullmatch(arg)
        if literal:
            scan.static.append(literal.group(2))
        elif arg.startswith("`") and arg.endswith("`") and len(arg) >= 2:
            body = arg[1:-1]
            cut = body.find("${")
            if cut == -1:
                scan.static.append(body)
            elif cut > 0:
                scan.dynamic_prefixes.append(body[:cut])
            else:
                scan.unresolved += 1
        elif "?" in arg and "(" not in arg and len(_STRING.findall(arg)) >= 2:
            # `cond ? 'a.b' : 'c.d'`: every branch is a literal, so each is a static use
            scan.static.extend(s for _, s in _STRING.findall(arg))
        else:
            scan.unresolved += 1
    return scan
