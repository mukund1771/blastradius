"""Chunking and the one LLM call per chunk: the model proposes, `verify` disposes (D07, D30; cases 22-27).

One `LLM.structured` call per page chunk (chunks are cut at heading boundaries to `page_char_budget`). The
model returns `ExtractedPage`; nothing else it says is used. A chunk carries its text (what the model reads)
and its *units*: the paragraphs, list items, headings and table cells of that text, each normalised on its
own. `verify.py` checks every quote against one unit and against the whole page, so a quote can never be a
join of the synthetic title head, the repeated page context, a heading and a sentence, or two sentences.

Requirement.id is the hash of the quote (D27, D30), so a sentence carries exactly one requirement: the prompt
asks for one item per sentence and the text may name several capabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from ..llm import LLM
from ..models import Strict
from ..safety import UNTRUSTED_PREAMBLE, untrusted
from .fetch import MAX_TITLE_CHARS
from .normalize import CODE_MARK, flatten, render

PROMPT_VERSION = "spec-extract-2"
MIN_QUOTE_CHARS = 16
MIN_QUOTE_WORDS = 4
MAX_TEXT_CHARS = 160
MAX_HEADING_CHARS = 200
_CONTEXT_CHARS = 1200      # page-level description and callouts repeated on every later chunk of a long page

Edition = Literal["cloud-web", "desktop", "self-host", "all", "unknown"]


class ExtractedItem(Strict):
    text: str = Field(description='One statement starting with "User can"; it may name several capabilities.')
    quote: str = Field(description="One sentence or line copied character for character from the page.")
    edition: Edition = Field(description="Only from an explicit qualifier in the document, else unknown.")
    polarity: Literal["must_exist", "must_not_exist"] = Field(
        description="must_not_exist only when the quoted sentence or its callout says it is not available.")
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
or tells them they do not have. Emit one item per sentence that states a capability, in document order; \
when one sentence joins several capabilities, name them all in that single item. Skip introductions, \
marketing, release notes, installation and deployment steps, API, CLI and SDK usage, code, configuration, \
pricing, legal and security statements. If nothing qualifies, return an empty list. Never invent a \
capability.

Fields:
- text: one statement that starts with "User can" (at most {MAX_TEXT_CHARS} characters) and says in plain \
words what the quoted sentence says the user can do. Use "User can ..." even when the capability is said \
to be unavailable.
- quote: ONE sentence or line copied character for character from the chunk that states the capability. \
At least {MIN_QUOTE_WORDS} words. No paraphrase, no ellipsis, no joining of separate sentences, headings \
or list items. If you cannot copy it exactly, do not emit the item. Items whose quote is not found \
verbatim inside one sentence of the page are discarded.
- edition: which edition the capability belongs to, taken ONLY from an explicit qualifier in the quoted \
sentence, its heading or tab title, or a callout at the very top of the page (for example "Warning: only \
available in the Desktop App"): cloud-web (hosted web app / Cloud), desktop (Desktop App only), self-host \
(self-hosted editions only), all (explicitly available on every edition), unknown (no explicit qualifier: \
this is the default, do not guess). A qualifier that says a capability is NOT supported in an edition is \
not evidence for that edition.
- polarity: must_exist by default. must_not_exist ONLY when the quoted sentence itself, or the callout it \
sits in, says the capability is not available, not supported, no longer possible, removed, deprecated, \
coming soon or available only elsewhere. A must_not_exist without such wording is reset to must_exist.
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
    own: bool = True           # False for the page context repeated on later chunks (never a quote source)


@dataclass(frozen=True)
class Unit:
    """One paragraph, list item, heading, table cell or code marker, normalised on its own."""

    path: tuple[str, ...]
    flat: str                  # flatten(rendered unit): what a quote is looked up in
    own: bool = True
    label: str = ""            # leading "Warning" / "Desktop App" of a callout or tab, if any
    callout: bool = False      # a Note/Tip/Warning/Info/Danger/Check callout


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str                                        # what the model reads (starts with the page title)
    units: tuple[Unit, ...]                          # quote sources; the title head is not one of them
    callouts: tuple[str, ...] = ()                   # normalised preamble callouts of the page (qualifiers)


_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_LABELLED = re.compile(r"^(?:Note|Tip|Warning|Info|Danger|Check): ")    # render() joins a label to its text
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_LEAD_LABEL = re.compile(r"^([A-Z][^:.!?\n]{0,59}): ")


def split_blocks(title: str, rendered: str) -> list[Block]:
    """Cut rendered text at heading lines. The first block (before any heading) is the page preamble."""
    title = title[:MAX_TITLE_CHARS]
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
            stack.append((level, m.group(2)[:MAX_HEADING_CHARS]))
            path = (title, *(h for _, h in stack))
            current = [line]
        else:
            current.append(line)
    flush()
    return blocks


def _logical_lines(text: str) -> list[str]:
    """Paragraphs, list items, headings, table cells and code markers; soft-wrapped lines are joined."""
    out: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        if cur:
            out.append("\n".join(cur))
            cur.clear()

    for line in text.split("\n"):
        s = line.strip()
        if not s:
            flush()
        elif _HEADING.match(s) or s == CODE_MARK:
            flush()
            out.append(s)
        elif s.startswith("|"):
            flush()
            out.extend(cell.strip() for cell in s.strip("|").split("|") if cell.strip())
        elif _LIST_ITEM.match(line) or _LABELLED.match(s):
            flush()
            cur.append(line)
        else:
            cur.append(line)
    flush()
    return out


def _units(block: Block) -> list[Unit]:
    units = []
    for logical in _logical_lines(block.text):
        flat = flatten(logical)
        if not flat:
            continue
        label = _LEAD_LABEL.match(flat)
        units.append(Unit(block.path, flat, block.own, label.group(1) if label else "",
                          bool(_LABELLED.match(flat))))
    return units


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


def _preamble_callouts(blocks: list[Block]) -> tuple[str, ...]:
    """Normalised callouts of the preamble block: the only page-level qualifiers an edition may rest on."""
    if not blocks or len(blocks[0].path) != 1:
        return ()
    return tuple(u.flat for u in _units(blocks[0]) if u.callout)


def chunk_page(title: str, body: str, budget: int) -> list[Chunk]:
    """Chunks of at most `budget` characters, cut at heading boundaries, each starting with the page title."""
    title = title[:MAX_TITLE_CHARS]
    blocks = split_blocks(title, render(body))
    if not blocks:
        return []
    callouts = _preamble_callouts(blocks)
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
            parts.insert(0, Block((title,), context, own=False))
        text = head + "\n\n".join(b.text for b in parts)
        chunks.append(Chunk(i, text, tuple(u for b in parts for u in _units(b)), callouts))
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
