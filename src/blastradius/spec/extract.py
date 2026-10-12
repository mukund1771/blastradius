"""Per-page requirement extraction: the LLM proposes, the quote check disposes (D07, D30; cases 22, 25, 27).

One `LLM.structured` call per page chunk (chunks are cut at heading boundaries to `page_char_budget`). The
model returns `ExtractedPage`; nothing else it says is used. Every item must carry a quote that, after
`normalize`, is a substring of the chunk the model was shown, otherwise it is dropped. `section` is computed
from where the quote sits, never taken from the model. A non-default `edition` that the page does not back
with an explicit qualifier is downgraded to `unknown`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from ..llm import LLM
from ..models import Strict
from ..safety import UNTRUSTED_PREAMBLE, untrusted
from .normalize import normalize, render

PROMPT_VERSION = "spec-extract-1"
MIN_QUOTE_CHARS = 16
MIN_QUOTE_WORDS = 4
_CONTEXT_CHARS = 1200      # page-level description and callouts repeated on every later chunk of a long page

Edition = Literal["cloud-web", "desktop", "self-host", "all", "unknown"]


class ExtractedItem(Strict):
    text: str = Field(description='One atomic statement that starts with "User can", one capability.')
    quote: str = Field(description="One sentence or line copied character for character from the page.")
    edition: Edition = Field(description="Only from an explicit qualifier in the document, else unknown.")
    polarity: Literal["must_exist", "must_not_exist"] = Field(
        description="must_not_exist only when the document says it is not available or coming soon.")
    ui_observable: Literal["yes", "no", "unknown"] = Field(
        description="Can a person verify it by looking at the product's screens, menus or dialogs.")
    requires_login: bool = Field(description="True when the document says an account or workspace is needed.")


class ExtractedPage(Strict):
    requirements: list[ExtractedItem]


def system_prompt(product: str) -> str:
    return f"""You turn one chunk of {product}'s public documentation into a list of testable product \
requirements for a QA team.

{UNTRUSTED_PREAMBLE}

The documentation chunk is inside <untrusted_data> tags. Reply only with the structured output.

A requirement is a capability that the documentation tells a user of the product's interface they have, \
or tells them they do not have. Emit one item per capability, in document order. Skip introductions, \
marketing, release notes, installation and deployment steps, API, CLI and SDK usage, code, configuration, \
pricing, legal and security statements. If nothing qualifies, return an empty list. Never invent a \
capability.

Fields:
- text: one atomic statement that starts with "User can" and names ONE capability in plain words. Split \
sentences that join two capabilities. Use "User can ..." even when the capability is said to be unavailable.
- quote: ONE sentence or line copied character for character from the chunk that states the capability. \
At least five words. No paraphrase, no ellipsis, no joining of separate sentences. If you cannot copy it \
exactly, do not emit the item. Items whose quote is not found verbatim in the chunk are discarded.
- edition: which edition the capability belongs to, taken ONLY from an explicit qualifier in the chunk (a \
callout such as "Warning: only available in the Desktop App", a tab or section title, or the sentence \
itself): cloud-web (hosted web app / Cloud), desktop (Desktop App only), self-host (self-hosted editions \
only), all (explicitly available on every edition), unknown (no explicit qualifier: this is the default, \
do not guess). A callout near the top of the page applies to the whole page.
- polarity: must_exist, or must_not_exist when the chunk says the capability is not available, not \
supported, removed or coming soon.
- ui_observable: yes when a person using the product's screens could confirm it (a button, menu, dialog, \
setting, label); no for API, CLI, backend, self-hosting, configuration, security, performance, pricing or \
legal statements; unknown when you cannot tell.
- requires_login: true when the chunk says the capability needs an account, sign-in, workspace, team or \
organisation."""


# ------------------------------------------------------------------------------------------------ chunks
@dataclass(frozen=True)
class Block:
    path: tuple[str, ...]      # page title, then the heading stack
    text: str                  # rendered text including its own heading line


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str                                        # what the model reads
    flat: str                                        # normalize(text): the quote check runs against this
    blocks: tuple[tuple[tuple[str, ...], str], ...]  # (heading path, flat block text) for section lookup


_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_LABELLED = re.compile(r"^(?:Note|Tip|Warning|Info|Danger|Check): ")    # render() joins a label to its text


def split_blocks(title: str, rendered: str) -> list[Block]:
    """Cut rendered text at heading lines. The first block (before any heading) is the page preamble."""
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    path: tuple[str, ...] = (title,)
    current: list[str] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            blocks.append(Block(path, text))

    for line in rendered.split("\n"):
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2)))
            path = (title, *(h for _, h in stack))
            current = [line]
        else:
            current.append(line)
    flush()
    return blocks


def _split_oversize(block: Block, budget: int) -> list[Block]:
    """Paragraph boundaries first, then a hard cut at whitespace; never splits inside a word."""
    pieces: list[str] = []
    for para in block.text.split("\n\n"):
        while len(para) > budget:
            cut = para.rfind(" ", 0, budget)
            cut = cut if cut > budget // 2 else budget
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            pieces.append(para)
    out: list[str] = []
    for piece in pieces:
        if out and len(out[-1]) + 2 + len(piece) <= budget:
            out[-1] += "\n\n" + piece
        else:
            out.append(piece)
    return [Block(block.path, text) for text in out]


def _page_context(blocks: list[Block]) -> str:
    """Description and callouts of the preamble, repeated on later chunks so edition detection sees them."""
    if not blocks or len(blocks[0].path) != 1:
        return ""
    paras = blocks[0].text.split("\n\n")
    keep = [paras[0], *(p for p in paras[1:] if _LABELLED.match(p))]
    return "\n\n".join(keep)[:_CONTEXT_CHARS]


def chunk_page(title: str, body: str, budget: int) -> list[Chunk]:
    """Chunks of at most `budget` characters, cut at heading boundaries, each starting with the page title."""
    blocks = split_blocks(title, render(body))
    if not blocks:
        return []
    head = f"# {title}\n\n"
    context = _page_context(blocks)[: budget // 4]
    room = max(budget - len(head) - (len(context) + 2 if context else 0), 200)
    groups: list[list[Block]] = []
    size = 0
    for block in blocks:
        for piece in (_split_oversize(block, room) if len(block.text) > room else [block]):
            if groups and size + 2 + len(piece.text) <= room:
                groups[-1].append(piece)
                size += 2 + len(piece.text)
            else:
                groups.append([piece])
                size = len(piece.text)
    chunks = []
    for i, group in enumerate(groups):
        parts = list(group)
        if i > 0 and context and context not in group[0].text:
            parts.insert(0, Block((title,), context))
        text = head + "\n\n".join(b.text for b in parts)
        chunks.append(Chunk(i, text, normalize(text), tuple((b.path, normalize(b.text)) for b in parts)))
    return chunks


# ------------------------------------------------------------------------------------------------ extract
def extract_chunk(llm: LLM, product: str, url: str, chunk: Chunk) -> list[ExtractedItem]:
    """One structured call. Page text is wrapped as untrusted data; only the schema'd output is used."""
    user = (f"Extract the requirements from this documentation chunk of {product}.\n\n"
            + untrusted(chunk.text, source=url, max_chars=len(chunk.text) + 1))
    out = llm.structured(purpose="extract_requirements", tier="mid", effort="low",
                         system=system_prompt(product), user=user, schema=ExtractedPage,
                         prompt_version=PROMPT_VERSION)
    return out.requirements


# ------------------------------------------------------------------------------------------------ verify
_EDITION_EVIDENCE: dict[str, re.Pattern[str]] = {
    "desktop": re.compile(r"(?i)\bdesktop\b"),
    "self-host": re.compile(r"(?i)self[- ]?host|enterprise edition|community edition|on[- ]prem"),
    "cloud-web": re.compile(r"(?i)\b(?:cloud|web|browser|hosted|online)\b"),
    "all": re.compile(r"(?i)\b(?:all|every|both|each)\b"),
}


def quote_verdict(quote: str, chunk_flat: str) -> tuple[str | None, str]:
    """(normalised quote, "") when verbatim in the chunk; (None, "too_short" | "not_in_source") otherwise."""
    q = normalize(quote)
    if len(q) > 2 and q[0] == q[-1] and q[0] in "\"'" and q not in chunk_flat:
        q = q[1:-1].strip()                  # a model that wrapped its quote in quotation marks
    if len(q) < MIN_QUOTE_CHARS or len(q.split()) < MIN_QUOTE_WORDS:
        return None, "too_short"
    if q not in chunk_flat:
        return None, "not_in_source"
    return q, ""


def _holding_block(chunk: Chunk, quote: str) -> tuple[tuple[str, ...] | None, str]:
    for path, flat in chunk.blocks:
        if quote in flat:
            return path, flat
    return None, ""


def section_for(chunk: Chunk, quote: str, title: str) -> str:
    """Heading path ("Page > Section > Subsection") of the block that holds the quote."""
    path, _ = _holding_block(chunk, quote)
    return " > ".join(path) if path else title


def supported_edition(claimed: str, quote: str, chunk: Chunk) -> str:
    """Keep a non-unknown edition only if the quote's section or a page callout says so explicitly."""
    if claimed == "unknown":
        return claimed
    path, flat = _holding_block(chunk, quote)
    callouts = " ".join(line for line in chunk.text.split("\n") if _LABELLED.match(line))
    evidence = f"{quote} {flat} {' '.join(path or ())} {callouts}"
    return claimed if _EDITION_EVIDENCE[claimed].search(evidence) else "unknown"
