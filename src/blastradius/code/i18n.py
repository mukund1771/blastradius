"""i18n: flatten a locale file and find `t('key')` uses in script and template text (D10; cases 32, 33).

Static keys are the signal the linker leans on (key -> English value -> files). Template literals such as
`t(`tab.${name}`)` become dynamic uses carrying the static prefix; arguments that are neither (a variable,
a call) are only counted, because there is no key to record.

The scanner reads fork-authored files (D06), so its cost is bounded: a first argument longer than
`_MAX_ARGUMENT` characters is not a key (counted as unresolved), and all calls of one text share a scan budget
proportional to its size, so `t(` repeated 20,000 times cannot make the work quadratic.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# `t(`, `$t(`, `this.t(`, `i18n.t(`; a preceding word char or dot is some other function or member.
_CALL = re.compile(r"(?<![\w$.])(?<!function )(?:(?:this|i18n|\$i18n)\.)?\$?t\(\s*")
_STRING = re.compile(r"""(['"])((?:\\.|(?!\1)[^\\\n])*)\1""")
_MAX_ARGUMENT = 1024                # characters of a first argument that are ever examined
_BUDGET_PER_CHAR = 8                # total characters examined across all calls, per character of text
_BUDGET_FLOOR = 50_000


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


def _first_argument(text: str, start: int, stop: int) -> tuple[str | None, int]:
    """Source of the first call argument: up to the top-level `,` or the closing `)`, within `text[start:stop]`.

    Returns `(argument, characters examined)`. The argument is None when the window ended before the argument
    did (and the window is not the end of the text): the caller counts it as unresolved.
    """
    depth = 0
    i = start
    while i < stop:
        c = text[i]
        if c in "'\"`":
            end = i + 1
            while end < stop and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            if end >= stop:
                i = stop
                break
            i = end + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return text[start:i].strip(), i - start
            depth -= 1
        elif c == "," and depth == 0:
            return text[start:i].strip(), i - start
        i += 1
    if stop >= len(text):
        return text[start:].strip(), stop - start
    return None, stop - start


def scan_t_calls(text: str) -> KeyScan:
    scan = KeyScan()
    budget = _BUDGET_PER_CHAR * len(text) + _BUDGET_FLOOR
    for m in _CALL.finditer(text):
        arg, spent = _first_argument(text, m.end(), min(len(text), m.end() + _MAX_ARGUMENT, m.end() + budget))
        budget -= spent
        if arg is None:
            scan.unresolved += 1
            continue
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


# ------------------------------------------------------------------------------------- lone surrogates
# A locale file can spell a UTF-16 half (`"\ud800"`): JSON parses it into a str that cannot be encoded as
# UTF-8, so the index could not be written or hashed. Halves become U+FFFD and are counted, never raised.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_YAML_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}|\\U0000[dD][89a-fA-F][0-9a-fA-F]{2}")


def scrub_surrogates(messages: Mapping[str, str]) -> tuple[dict[str, str], int]:
    """`(messages, strings changed)`: every lone surrogate in a key or a value replaced by U+FFFD.

    JSON joins a valid escape pair into one character before this runs, so what is left is a lone half.
    Keys that become equal keep the later one, which is document order for a JSON object.
    """
    out: dict[str, str] = {}
    changed = 0
    for key, value in messages.items():
        clean_key, clean_value = _LONE_SURROGATE.sub("�", key), _LONE_SURROGATE.sub("�", value)
        changed += (clean_key != key) + (clean_value != value)
        out[clean_key] = clean_value
    return out, changed


def scrub_yaml_surrogates(text: str) -> tuple[str, int]:
    """`(text, escapes replaced)`: `\\ud800`-style escapes of a YAML document turned into `\\ufffd`.

    PyYAML does not join a pair of escapes, so every half would otherwise reach the index as a lone surrogate.
    """
    return _YAML_SURROGATE_ESCAPE.subn(r"\\ufffd", text)
