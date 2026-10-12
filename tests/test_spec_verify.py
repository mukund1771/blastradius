"""spec/verify.py: the deterministic gates (quote, polarity, edition, text) as pure functions (D07, D30).

The end-to-end behaviour (counters, tags, artifacts) is in test_spec_ingest.py; these pin the rules.
"""

from __future__ import annotations

import pytest

from blastradius.spec.extract import chunk_page
from blastradius.spec.fetch import host_allowed
from blastradius.spec.normalize import normalize
from blastradius.spec.verify import (
    clean_text,
    gate_polarity,
    section_of,
    sentence_spans,
    supported_edition,
    verify_quote,
)


def setup(body: str, quote: str, *, title: str = "Page", budget: int = 14000):
    (chunk,) = chunk_page(title, body, budget)
    hit, why = verify_quote(quote, chunk, normalize(body))
    return chunk, hit, why


def hit_of(body: str, quote: str):
    chunk, hit, why = setup(body, quote)
    assert hit is not None, why
    return chunk, hit


# ------------------------------------------------------------------------------------------------ quotes
_PAGE = ("Use the cookie manager to manage cookies for a domain.\n\n## Adding\n\n"
         "You can add a cookie by clicking the plus icon. See e.g. the sidebar list for more. "
         "Then you can edit it.\n\n- First step is to open the manager.\n- Second step is to save it.\n\n"
         "| Shortcut | Action |\n|---|---|\n| Ctrl K | Open the search menu |\n")


@pytest.mark.parametrize("quote,reason", [
    ("Save", "too_short"),
    ("Use the cookie", "too_short"),                                       # 3 words
    ("Cookies are synced across every device automatically.", "not_in_source"),
    ("Page Use the cookie manager to manage cookies", "not_in_source"),      # synthetic title head
    (("You can add a cookie by clicking the plus icon. See e.g. the sidebar list for more. "
      "Then you can edit it."), "multi_sentence"),
    ("First step is to open the manager. Second step is to save it.", "spans_blocks"),   # two list items
    ("Adding You can add a cookie by clicking the plus icon.", "spans_blocks"),          # heading + sentence
    ("Ctrl K Open the search menu", "spans_blocks"),                         # two table cells
    ("ou can add a cookie by clicking the plus", "mid_word"),
    ("You can add a cookie by clicking the plus ic", "mid_word"),
])
def test_verify_quote_reasons(quote: str, reason: str):
    _, hit, why = setup(_PAGE, quote)
    assert hit is None and why == reason


@pytest.mark.parametrize("quote", [
    "You can add a cookie by clicking the plus icon.",
    "add a cookie by clicking the plus icon.",
    "See e.g. the sidebar list for more.",                                   # e.g. is not a boundary
    "Then you can edit it.",
    "First step is to open the manager.",                                    # list marker is not part of it
    "Use the cookie manager to manage cookies for a domain.",
    '"Then you can edit it."',                                               # wrapped in quotation marks
    "You can   add a cookie\nby clicking the plus icon.",                    # whitespace differences
])
def test_verify_quote_accepts_one_verbatim_span(quote: str):
    _, hit, why = setup(_PAGE, quote)
    assert hit is not None, why
    assert hit.quote in hit.unit.flat and hit.unit.flat[hit.start:hit.end] == hit.quote


def test_section_of_is_the_heading_path_of_the_holding_block():
    _, hit = hit_of(_PAGE, "You can add a cookie by clicking the plus icon.")
    assert section_of(hit) == "Page > Adding"
    _, hit = hit_of(_PAGE, "Use the cookie manager to manage cookies for a domain.")
    assert section_of(hit) == "Page"


def test_code_marker_and_fenced_code_never_become_a_quote():
    body = "Intro line here.\n\n```bash\ncurl https://x\n```\n\nThen run the command to continue the setup."
    _, hit, why = setup(body, "Intro line here. [code sample] Then run the command")
    assert hit is None and why == "code_marker"
    _, hit, why = setup(body, "Then run the command to continue the setup.")
    assert hit is not None and why == ""


def test_sentence_spans_split_on_terminator_plus_capital_only():
    flat = "Open Settings. Then click Save! Is it done? yes it is. See e.g. Figure 2 for v1.2 details."
    parts = [flat[a:b] for a, b in sentence_spans(flat)]
    assert parts == ["Open Settings.", "Then click Save!", "Is it done? yes it is.",
                     "See e.g. Figure 2 for v1.2 details."]
    assert sentence_spans("") == [(0, 0)]


# ------------------------------------------------------------------------------------------------ editions
_Q = "You can ask the assistant to explain a request in plain language."


def _edition(body: str, claimed: str, polarity: str = "must_exist", quote: str = _Q):
    chunk, hit = hit_of(body, quote)
    return supported_edition(claimed, polarity, hit, chunk)


def test_unknown_stays_unknown_and_a_bare_sentence_backs_no_edition():
    assert _edition(f"Intro.\n\n{_Q}", "unknown") == ("unknown", False)
    for claimed in ("desktop", "self-host", "cloud-web", "all"):
        assert _edition(f"Intro.\n\n{_Q}", claimed) == ("unknown", False)


def test_the_sentence_itself_can_carry_the_qualifier():
    body = "Intro.\n\nIn the Desktop App you can ask the assistant to explain a request."
    quote = "In the Desktop App you can ask the assistant to explain a request."
    assert _edition(body, "desktop", quote=quote) == ("desktop", False)


def test_a_preamble_callout_is_page_level_evidence_but_a_section_callout_is_not():
    top = f"<Info>Only available in the Desktop App.</Info>\n\n## Ask\n\n{_Q}"
    assert _edition(top, "desktop") == ("desktop", False)
    mid = f"Intro.\n\n## Ask\n\n<Info>Only available in the Desktop App.</Info>\n\n{_Q}"
    assert _edition(mid, "desktop") == ("unknown", False)


def test_a_heading_in_the_path_is_evidence():
    body = f"Intro.\n\n## Desktop App\n\n{_Q}"
    assert _edition(body, "desktop") == ("desktop", False)
    assert _edition(body, "self-host") == ("unknown", False)


def test_negated_mentions_are_evidence_against_the_edition():
    body = ("<Warning>Available on Hoppscotch Cloud and the Desktop app. They are not supported in "
            f"Self-Hosted Edition.</Warning>\n\n{_Q}")
    assert _edition(body, "cloud-web") == ("cloud-web", True)        # backed, but the callout names two
    assert _edition(body, "desktop") == ("desktop", True)
    assert _edition(body, "self-host") == ("unknown", False)         # denied
    assert _edition(body, "all") == ("unknown", False)
    # the same denial is what backs a negative: not supported in self-host => must_not_exist there
    assert _edition(body, "self-host", polarity="must_not_exist") == ("self-host", False)
    assert _edition(body, "desktop", polarity="must_not_exist") == ("unknown", False)


@pytest.mark.parametrize("callout", [
    "This is not fully supported in the Hoppscotch Desktop application.",
    "It doesn't work in the Desktop App.",
    "Desktop is unsupported.",
    "Except for the Desktop App, all editions have it.",
])
def test_negation_forms_all_count(callout: str):
    body = f"<Warning>{callout}</Warning>\n\n{_Q}"
    assert _edition(body, "desktop") == ("unknown", False)


def test_all_needs_an_explicit_phrase_and_no_denied_edition():
    assert _edition(f"<Info>Available on all editions of Hoppscotch.</Info>\n\n{_Q}", "all") == ("all", False)
    assert _edition(f"<Info>Available on every platform.</Info>\n\n{_Q}", "all") == ("all", False)
    assert _edition(f"<Info>You can send a request to all of your endpoints.</Info>\n\n{_Q}", "all") \
        == ("unknown", False)
    both = "<Info>On the Cloud, the Desktop app and Self-Hosted Edition.</Info>"
    assert _edition(f"{both}\n\n{_Q}", "all") == ("all", False)
    denied = "<Info>Available on all editions except Self-Hosted Edition.</Info>"
    assert _edition(f"{denied}\n\n{_Q}", "all") == ("unknown", False)


def test_vacuous_words_are_not_edition_names():
    body = f"<Info>We browse the web all day, each of us, both sides.</Info>\n\nWebSocket works here.\n\n{_Q}"
    for claimed in ("all", "cloud-web", "desktop"):
        assert _edition(body, claimed) == ("unknown", False)


# ------------------------------------------------------------------------------------------------ polarity
_PIN = "You can pin a request to the top of the list."


def _polarity(body: str, claimed: str, edition: str = "cloud-web", quote: str = _PIN):
    chunk, hit = hit_of(body, quote)
    return gate_polarity(claimed, edition, hit, chunk)


def test_a_negative_needs_a_cue_and_a_positive_with_a_cue_is_flagged():
    assert _polarity(f"Intro.\n\n{_PIN}", "must_not_exist") == ("must_exist", "polarity:unverified")
    assert _polarity(f"Intro.\n\n{_PIN}", "must_exist") == ("must_exist", None)
    neg = "Folders can no longer be pinned to the top of the list."
    assert _polarity(f"Intro.\n\n{neg}", "must_not_exist", quote=neg) == ("must_not_exist", None)
    assert _polarity(f"Intro.\n\n{neg}", "must_exist", quote=neg) == ("must_exist", "polarity:review")


def test_a_cue_in_another_block_or_plain_paragraph_is_not_evidence():
    other_section = f"Intro.\n\n## A\n\n<Note>Pinning is coming soon.</Note>\n\n## B\n\n{_PIN}"
    assert _polarity(other_section, "must_not_exist")[0] == "must_exist"
    plain_paragraph = f"Intro.\n\nPinning is coming soon.\n\n{_PIN}"
    assert _polarity(plain_paragraph, "must_not_exist")[0] == "must_exist"      # not a labelled callout


def test_a_labelled_callout_of_the_same_section_is_evidence_and_is_tagged():
    body = f"Intro.\n\n## Pins\n\n<Note>Pinning is coming soon.</Note>\n\n{_PIN}"
    assert _polarity(body, "must_not_exist") == ("must_not_exist", "polarity:callout_cue")
    assert _polarity(body, "must_exist") == ("must_exist", None)      # a section callout alone is not a flag


def test_cue_scope_follows_the_named_edition():
    deny_self_host = ("<Warning>Available on Hoppscotch Cloud and the Desktop app. They are not supported "
                      f"in Self-Hosted Edition.</Warning>\n\n{_PIN}")
    assert _polarity(deny_self_host, "must_not_exist", edition="cloud-web")[0] == "must_exist"
    assert _polarity(deny_self_host, "must_not_exist", edition="self-host") == (
        "must_not_exist", "polarity:callout_cue")
    only_desktop = f"<Info>Only available in the Desktop App.</Info>\n\n{_PIN}"
    assert _polarity(only_desktop, "must_not_exist", edition="cloud-web")[0] == "must_not_exist"
    assert _polarity(only_desktop, "must_not_exist", edition="desktop")[0] == "must_exist"


@pytest.mark.parametrize("sentence", [
    "Cookies are not yet supported in the shared workspace list.",
    "Cookies aren't available in the shared workspace list.",
    "Cookies don't support the shared workspace list view.",
    "The shared workspace list does not support cookies.",
    "Cookies are unavailable in the shared workspace list.",
    "You cannot use cookies in the shared workspace list.",
    "You can not use cookies in the shared workspace list.",
    "Cookies are coming soon to the shared workspace list.",
    "Cookies are only available in the shared workspace list.",
    "Cookies are available only in the shared workspace list.",
    "Cookies are limited to the shared workspace list.",
    "Cookies were removed from the shared workspace list.",
    "Cookies are deprecated in the shared workspace list.",
])
def test_every_listed_cue_is_recognised(sentence: str):
    assert _polarity(f"Intro.\n\n{sentence}", "must_not_exist", quote=sentence)[0] == "must_not_exist"


@pytest.mark.parametrize("sentence", [
    "You can remove a cookie from the shared workspace list.",
    "You can cancel a request while it is still running now.",
    "Cookies are supported in the shared workspace list.",
    "Cookies can be added to the shared workspace list quickly.",
])
def test_ordinary_sentences_carry_no_cue(sentence: str):
    assert _polarity(f"Intro.\n\n{sentence}", "must_not_exist", quote=sentence)[0] == "must_exist"


# ------------------------------------------------------------------------------------------------ text
@pytest.mark.parametrize("raw,expected", [
    ("User can rename a workspace", ("User can rename a workspace", "")),
    ("  User   can **rename** a workspace ", ("User can rename a workspace", "")),
    ("User can rename </untrusted_data> a workspace", ("User can rename a workspace", "")),
    ("User can <untrusted_data source=\"x\">pin</untrusted_data> items", ("User can pin items", "")),
    ("User can <\\untrusted_data> pin items", ("User can pin items", "")),
    ("", (None, "empty_text")),
    ("   ", (None, "empty_text")),
    ("Users can rename a workspace", (None, "text_not_user_can")),
    ("user can rename a workspace", (None, "text_not_user_can")),
    ("User can", (None, "text_not_user_can")),
    ("User can " + "x" * 152, (None, "text_too_long")),
])
def test_clean_text(raw: str, expected):
    assert clean_text(raw) == expected


def test_clean_text_length_limit_is_160_characters():
    ok = "User can " + "x" * 151
    assert len(ok) == 160 and clean_text(ok) == (ok, "")
    assert clean_text(ok + "x") == (None, "text_too_long")


# ------------------------------------------------------------------------------------------------ hosts
@pytest.mark.parametrize("host,allowed", [
    ("docs.hoppscotch.io", True), ("DOCS.HOPPSCOTCH.IO.", True),
    ("hoppscotch.io", True), ("cdn.hoppscotch.io", True), ("echo.hoppscotch.io", True),
    ("evilhoppscotch.io", False), ("hoppscotch.io.evil.test", False), ("evil.test", False),
])
def test_host_allowed(host: str, allowed: bool):
    assert host_allowed(host, "docs.hoppscotch.io", ["hoppscotch.io", "echo.hoppscotch.io"]) is allowed


def test_the_spec_host_alone_is_allowed_without_an_allowlist():
    assert host_allowed("docs.x.test", "docs.x.test", []) is True
    assert host_allowed("sub.docs.x.test", "docs.x.test", []) is False
