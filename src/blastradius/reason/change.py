"""What a pull request's diff says about a file, without reading the whole file (D17; cases 35, 36, 40, 76).

Two pure analyses, no I/O:

`analyse_patch` reads a unified diff and decides the change kind of one file (the CRAG-inspired grader's
"change kind" signal): ADDITIVE_API (only added lines, each block a self-contained declaration: a new exported
symbol or a new class member), TYPE_ONLY (every changed line is a comment, a blank or inside a type
declaration: nothing can change at runtime), DELETE, SIGNATURE, BODY, COPY_ONLY (a locale file), TEST,
GENERATED. The asymmetry matters: ADDITIVE_API and TYPE_ONLY only ever *narrow* (downgrade, case 76), so they
are assigned on strong evidence; everything unclear is BODY, which keeps every edge at full strength.
`touched` is the set of exported names the diff edits: an `export` line that is itself changed, plus the
nearest enclosing top-level `export` of each hunk (git's hunk header names it, context lines say the rest).
Importer edges carry the named imports, so touched names are what the symbol filter compares them with.

`diff_locale` flattens two versions of a locale file and reports added, changed and removed keys. A diff that
is only a trailing newline or CRLF/LF is empty (the flattened maps are equal): #6641 changes 24 leaf strings,
not 26, and ends with an EOL-only hunk.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

from ..code.i18n import flatten_messages
from ..models import FileClass


class ChangeKind(StrEnum):
    ADDITIVE_API = "ADDITIVE_API"
    SIGNATURE = "SIGNATURE"
    BODY = "BODY"
    COPY_ONLY = "COPY_ONLY"
    TYPE_ONLY = "TYPE_ONLY"
    TEST = "TEST"
    GENERATED = "GENERATED"
    DELETE = "DELETE"
    OTHER = "OTHER"                 # lockfile, docs, CI, build configuration: not UI code


# ----------------------------------------------------------------------------------------- patches
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")
_EXPORT_DECL = re.compile(
    r"^export\s+(?:declare\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(function\*?|class|const|let|var|enum|interface|type)\s+([A-Za-z_$][\w$]*)")
_EXPORT_DEFAULT = re.compile(r"^export\s+default\b")
_EXPORT_LIST = re.compile(r"^export\s+(?:type\s+)?\{([^}]*)\}")
_EXPORT_STAR = re.compile(r"^export\s+\*")
_TYPE_DECL = re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:type|interface)\s+[A-Za-z_$]")
_IMPORT_TYPE = re.compile(r"^import\s+type\b")
_DECL_START = re.compile(
    r"^\s*(?:export\s+|import\s+|(?:public|private|protected|static|readonly|abstract|override|async|get|set)\s+)"
    r"|^\s*(?:function|class|const|let|var|enum|interface|type)\s+[A-Za-z_$]"
    r"|^\s*(?:async\s+)?[A-Za-z_$][\w$]*\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[^{=]+)?\{")
_COMMENT = re.compile(r"^\s*(?://|/\*|\*|\*/|<!--|-->)")
_CLOSER = re.compile(r"^[\s})\]]*[;,]?\s*$")


@dataclass(frozen=True)
class PatchFacts:
    kind: ChangeKind
    additions: int = 0
    deletions: int = 0
    touched: tuple[str, ...] = ()           # exported names the diff edits (sorted)
    added: tuple[str, ...] = ()             # exported names that exist only after the change
    removed: tuple[str, ...] = ()           # exported names that exist only before it
    symbols_known: bool = False             # the diff named at least one exported symbol
    patch_missing: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


def exported_names(line: str) -> tuple[list[str], str | None]:
    """Exported names declared on one top-level source line, and the declaration keyword ('const', 'class', ...)."""
    m = _EXPORT_DECL.match(line)
    if m:
        return [m.group(2)], m.group(1).rstrip("*")
    if _EXPORT_DEFAULT.match(line):
        return ["default"], "default"
    m = _EXPORT_LIST.match(line)
    if m:
        names = []
        for part in m.group(1).split(","):
            bits = part.replace("type ", "", 1).split(" as ")
            name = bits[-1].strip()
            if name:
                names.append(name)
        return names, "list"
    if _EXPORT_STAR.match(line):
        return ["*"], "star"
    return [], None


@dataclass
class _Hunk:
    context: str
    lines: list[tuple[str, str]] = field(default_factory=list)       # (marker ' ', '+', '-'), text


def _hunks(patch: str) -> list[_Hunk]:
    out: list[_Hunk] = []
    for raw in patch.splitlines():
        m = _HUNK.match(raw)
        if m:
            out.append(_Hunk(m.group(1)))
            continue
        if not out or not raw or raw[0] not in " +-":
            continue            # "\ No newline at end of file" and anything before the first hunk
        out[-1].lines.append((raw[0], raw[1:]))
    return out


def _enclosing_export(hunk: _Hunk, index: int) -> list[str]:
    """Exported names of the nearest top-level declaration above hunk line `index` (header context first)."""
    for i in range(index, -1, -1):
        text = hunk.lines[i][1]
        if text[:1] in ("", " ", "\t"):
            continue                    # indented or blank
        if _CLOSER.match(text):
            return []                   # a closed block sits between: the enclosing declaration is unknown
        names, _ = exported_names(text)
        return names
    names, _ = exported_names(hunk.context)
    return names


def _is_type_context(hunk: _Hunk, index: int) -> bool:
    for i in range(index, -1, -1):
        text = hunk.lines[i][1]
        if text[:1] in ("", " ", "\t"):
            continue
        if _CLOSER.match(text):
            return False
        return bool(_TYPE_DECL.match(text) or _IMPORT_TYPE.match(text))
    return bool(_TYPE_DECL.match(hunk.context))


def _is_noise(text: str) -> bool:
    return not text.strip() or bool(_COMMENT.match(text))


def _added_blocks_are_declarations(hunk: _Hunk) -> bool:
    """Every contiguous run of added lines starts (after comments) with a declaration and is brace-balanced."""
    block: list[str] = []

    def closed() -> bool:
        body = [t for t in block if not _is_noise(t)]
        if not body:
            return True
        depth = sum(t.count("{") + t.count("(") - t.count("}") - t.count(")") for t in body)
        return depth == 0 and bool(_DECL_START.match(body[0]))

    for marker, text in hunk.lines:
        if marker == "+":
            block.append(text)
        elif block:
            if not closed():
                return False
            block = []
    return closed()


def analyse_patch(path: str, status: str, file_class: FileClass, patch: str | None, additions: int,
                  deletions: int) -> PatchFacts:
    """The change kind and exported-symbol facts of one changed file (module docstring)."""
    if file_class is FileClass.TEST:
        return PatchFacts(ChangeKind.TEST, additions, deletions)
    if file_class is FileClass.GENERATED:
        return PatchFacts(ChangeKind.GENERATED, additions, deletions)
    if file_class is FileClass.LOCALE:
        return PatchFacts(ChangeKind.COPY_ONLY, additions, deletions)
    if file_class in (FileClass.LOCKFILE, FileClass.DOCS, FileClass.CI, FileClass.BUILD_CONFIG):
        return PatchFacts(ChangeKind.OTHER, additions, deletions)
    if status == "removed":
        return PatchFacts(ChangeKind.DELETE, additions, deletions, reasons=("file removed",))
    if not patch:
        # binary, or a diff GitHub withheld as too large: nothing narrows what cannot be read
        return PatchFacts(ChangeKind.BODY, additions, deletions, patch_missing=True,
                          reasons=("diff not available",))

    is_script = not path.endswith(".vue")
    hunks = _hunks(patch)
    added, removed, touched = set(), set(), set()
    changed: list[tuple[_Hunk, int]] = []
    for hunk in hunks:
        for i, (marker, text) in enumerate(hunk.lines):
            if marker == " ":
                continue
            changed.append((hunk, i))
            names, _ = exported_names(text)
            if names:
                (added if marker == "+" else removed).update(names)
                touched.update(names)
            elif is_script:
                touched.update(_enclosing_export(hunk, i))
    modified = added & removed
    added, removed = added - modified, removed - modified
    symbols_known = bool(touched - {"*"}) and is_script
    base = {"additions": additions, "deletions": deletions, "touched": tuple(sorted(touched)),
            "added": tuple(sorted(added)), "removed": tuple(sorted(removed)), "symbols_known": symbols_known}

    code_changes = [(h, i) for h, i in changed if not _is_noise(h.lines[i][1])]
    if is_script and not code_changes:
        return PatchFacts(ChangeKind.TYPE_ONLY, reasons=("only comments or blank lines change",), **base)
    if is_script and all(_is_type_context(h, i) for h, i in code_changes):
        return PatchFacts(ChangeKind.TYPE_ONLY, reasons=("every changed line is inside a type declaration",),
                          **base)
    only_added = all(h.lines[i][0] == "+" for h, i in changed)
    if status == "added" or (is_script and only_added and all(_added_blocks_are_declarations(h) for h in hunks)):
        reason = "new file" if status == "added" else "only added declarations"
        return PatchFacts(ChangeKind.ADDITIVE_API, reasons=(reason,), **base)
    if removed and not added and is_script:
        return PatchFacts(ChangeKind.DELETE, reasons=("exported symbol removed",), **base)
    if modified:
        return PatchFacts(ChangeKind.SIGNATURE, reasons=("exported declaration edited",), **base)
    return PatchFacts(ChangeKind.BODY, **base)


# ------------------------------------------------------------------------------------------ locales
@dataclass(frozen=True)
class LocaleDiff:
    path: str
    added: dict[str, str] = field(default_factory=dict)
    changed: dict[str, tuple[str, str]] = field(default_factory=dict)       # key -> (before, after)
    removed: dict[str, str] = field(default_factory=dict)
    problem: str | None = None                                              # base_unreadable | head_unreadable | ...
    eol_only: bool = False                                                  # texts differ, messages do not

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    @property
    def keys(self) -> list[str]:
        return sorted({*self.added, *self.changed, *self.removed})


def _flatten(text: str | None) -> dict[str, str] | None:
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    flat, _ = flatten_messages(data)
    return {k: v.replace("\r\n", "\n").replace("\r", "\n") for k, v in flat.items()}


def diff_locale(path: str, base_text: str | None, head_text: str | None) -> LocaleDiff:
    """Added, changed and removed message keys between two versions of a locale file.

    A version that is absent or not a JSON object is `problem` (the caller says what it could not determine);
    an absent *base* with a readable head is an added file: every key is added."""
    base = _flatten(base_text) if base_text is not None else {}
    head = _flatten(head_text)
    if head_text is None or head is None:
        return LocaleDiff(path, problem="head_unreadable")
    if base is None:
        return LocaleDiff(path, problem="base_unreadable")
    added = {k: head[k] for k in sorted(head) if k not in base}
    removed = {k: base[k] for k in sorted(base) if k not in head}
    changed = {k: (base[k], head[k]) for k in sorted(head) if k in base and base[k] != head[k]}
    eol_only = not (added or removed or changed) and base_text is not None and base_text != head_text
    return LocaleDiff(path, added, changed, removed, eol_only=eol_only)
