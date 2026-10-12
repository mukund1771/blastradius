"""Source map v3 reading for the oracle (D23): which source file owns a span of generated code.

Only what the oracle needs. `SourceMap.votes(start, end)` returns, for a generated span, how many mapping
segments point at each source file: a first-segment lookup mis-attributes a span to the module that ends just
before it (docs/RECON.md section 6), so ownership is the plurality of all segments in the span.

Positions are (line, column) with columns in UTF-16 code units, as the source map spec defines them; `Text`
converts Python string offsets, so a non-BMP character in a long minified line does not shift later columns.
The mappings are decoded once, on first use (VLQ deltas are relative across lines, so a partial decode is not
possible); the 12 MB mappings of hoppscotch.io's entry chunk decode in about two seconds.
"""

from __future__ import annotations

import bisect
import hashlib
import re
from array import array
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}
_NON_BMP = re.compile("[\U00010000-\U0010ffff]")


def git_blob_sha(text: str) -> str:
    """The git blob id of `text` as UTF-8: what `CodeFile.sha` holds for an indexed file."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@dataclass
class _Line:
    cols: array = field(default_factory=lambda: array("I"))   # generated columns, ascending
    srcs: array = field(default_factory=lambda: array("i"))   # source index of each segment


def decode_mappings(mappings: str) -> list[_Line]:
    """VLQ `mappings` -> one `_Line` per generated line. Segments without a source (1 field) are dropped."""
    lines: list[_Line] = []
    src = 0
    for raw in mappings.split(";"):
        line = _Line()
        col = 0
        values: list[int] = []
        acc = shift = 0
        for ch in raw + ",":
            if ch == ",":
                if values:
                    col += values[0]
                    if len(values) >= 4:
                        src += values[1]
                        line.cols.append(col)
                        line.srcs.append(src)
                    values = []   # original line/column and name deltas are independent fields: not needed
                continue
            try:
                digit = _B64[ch]
            except KeyError as exc:
                raise ValueError(f"invalid base64 VLQ character {ch!r} in mappings") from exc
            acc |= (digit & 31) << shift
            if digit & 32:
                shift += 5
            else:
                values.append(-(acc >> 1) if acc & 1 else acc >> 1)
                acc = shift = 0
        lines.append(line)
    return lines


class Text:
    """A generated file with line starts and non-BMP positions indexed (offset -> line, UTF-16 column)."""

    def __init__(self, text: str):
        self.text = text
        self._starts = [0]
        for m in re.finditer("\n", text):
            self._starts.append(m.end())
        self._astral = [m.start() for m in _NON_BMP.finditer(text)]

    @property
    def lines(self) -> int:
        return len(self._starts)

    def position(self, offset: int) -> tuple[int, int]:
        """(line, column in UTF-16 units) of a string offset."""
        line = bisect.bisect_right(self._starts, offset) - 1
        start = self._starts[line]
        astral_before = bisect.bisect_left(self._astral, offset) - bisect.bisect_left(self._astral, start)
        return line, offset - start + astral_before

    def occurrences(self, needle: str, limit: int = 2) -> list[int]:
        """Up to `limit` start offsets of `needle` (two are enough to call a match ambiguous)."""
        out: list[int] = []
        at = self.text.find(needle)
        while at >= 0 and len(out) < limit:
            out.append(at)
            at = self.text.find(needle, at + 1)
        return out


class SourceMap:
    def __init__(self, data: dict[str, Any]):
        if int(data.get("version", 3)) != 3:
            raise ValueError(f"unsupported source map version {data.get('version')!r}")
        if "sections" in data:
            raise ValueError("indexed (sectioned) source maps are not supported")
        self.sources: list[str] = [str(s) for s in data.get("sources", [])]
        content = data.get("sourcesContent") or []
        self.contents: list[str | None] = [content[i] if i < len(content) else None
                                           for i in range(len(self.sources))]
        self.file: str | None = data.get("file")
        self._mappings = str(data.get("mappings", ""))
        self._lines: list[_Line] | None = None

    @property
    def lines(self) -> list[_Line]:
        if self._lines is None:
            self._lines = decode_mappings(self._mappings)
        return self._lines

    def votes(self, start: tuple[int, int], end: tuple[int, int]) -> Counter[int]:
        """Segment count per source index for generated positions in [start, end)."""
        out: Counter[int] = Counter()
        (l0, c0), (l1, c1) = start, end
        for ln in range(l0, min(l1, len(self.lines) - 1) + 1):
            line = self.lines[ln]
            lo = bisect.bisect_left(line.cols, c0) if ln == l0 else 0
            hi = bisect.bisect_left(line.cols, c1) if ln == l1 else len(line.cols)
            out.update(line.srcs[lo:hi])
        return out
