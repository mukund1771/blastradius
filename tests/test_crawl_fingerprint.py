"""Screen identity (D04; cases 3, 10, 14): url patterns, fingerprints, near-duplicate merge, screen kinds."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "crawl"))
import crawl_helpers  # noqa: F401  (puts src/ on sys.path when the editable install is hidden)

from blastradius.crawl.fingerprint import (
    MAX_FINGERPRINT_ELEMENTS,
    element_key,
    fingerprint,
    jaccard,
    key_set,
    screen_kind,
    stable_keys,
    url_pattern,
)
from blastradius.crawl.perceive import Element
from blastradius.models import UIElementObs


def el(name: str, role: str = "button", icon: str | None = None, popup: int = -1) -> Element:
    return Element(
        obs=UIElementObs(id=f"el_{role}_{name}_{icon}", role=role, name=name), icon=icon, popup=popup
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://hoppscotch.io/", "/"),
        ("https://hoppscotch.io/graphql", "/graphql"),
        ("https://hoppscotch.io/realtime/websocket?x=1#top", "/realtime/websocket"),
        ("https://hoppscotch.io/e/aB3dEf9GhJ12kLmN4pQ", "/e/:id"),
        ("https://x.test/users/12345/orders/9", "/users/:id/orders/:id"),
        ("https://x.test/doc/3f2504e0-4f89-11d3-9a0c-0305e82c3301", "/doc/:id"),
        ("https://x.test/blob/9f86d081884c7d659a2feaa0c55ad015", "/blob/:id"),
        ("https://ha.test/#/lovelace/0", "/#/lovelace/:id"),  # hash router keeps its route, id-normalised
        ("https://x.test/settings/", "/settings"),
    ],
)
def test_url_pattern(url: str, expected: str):
    assert url_pattern(url) == expected


def test_words_are_not_mistaken_for_ids():
    assert url_pattern("https://x.test/realtime/websocket/connection") == "/realtime/websocket/connection"
    assert (
        url_pattern("https://x.test/documentation/features/collections")
        == "/documentation/features/collections"
    )


def test_fingerprint_ignores_order_and_volatile_text():
    a = [el("Send"), el("Save"), el("Updated <time>", role="link")]
    b = [el("Updated <time>", role="link"), el("Save"), el("Send")]
    assert fingerprint("/", None, ["Params"], a) == fingerprint("/", None, ["Params"], b)


def test_fingerprint_separates_dialogs_tabs_and_routes():
    els = [el("Send"), el("Save")]
    base = fingerprint("/", None, ["Params"], els)
    assert base != fingerprint("/", "New Collection", ["Params"], els)
    assert base != fingerprint("/", None, ["Body"], els)
    assert base != fingerprint("/graphql", None, ["Params"], els)


def test_icon_signature_stands_in_for_a_missing_name():
    a, b = el("", icon="aaaa1111"), el("", icon="bbbb2222")
    assert element_key(a) != element_key(b)
    assert fingerprint("/", None, [], [a]) != fingerprint("/", None, [], [b])
    assert stable_keys([el("")]) == []  # nameless and iconless: no identity, not fingerprinted


def test_fingerprint_is_capped_at_sixty_and_bottom_k_is_stable():
    base = [el(f"Item {i}") for i in range(100)]
    keys = stable_keys(base)
    assert len(keys) == MAX_FINGERPRINT_ELEMENTS
    kept = {k[1] for k in keys}
    dropped = next(e for e in base if e.obs.name not in kept)
    assert fingerprint("/", None, [], base) == fingerprint(
        "/", None, [], [e for e in base if e is not dropped]
    )


def test_jaccard_merge_threshold():
    a = key_set([el(f"n{i}") for i in range(20)])
    nineteen = key_set([el(f"n{i}") for i in range(19)])
    half = key_set([el(f"n{i}") for i in range(10)])
    assert jaccard(a, a) == 1.0 and jaccard(a, nineteen) >= 0.9 and jaccard(a, half) < 0.9
    assert jaccard([], []) == 1.0


def test_screen_kind():
    assert screen_kind(pattern="/", has_popup=True, active_tabs=[], page_tabs_for_pattern=[]) == "modal"
    assert (
        screen_kind(pattern="/e/:id", has_popup=False, active_tabs=[], page_tabs_for_pattern=None) == "embed"
    )
    assert (
        screen_kind(pattern="/", has_popup=False, active_tabs=["Body"], page_tabs_for_pattern=["Params"])
        == "tab"
    )
    assert (
        screen_kind(pattern="/", has_popup=False, active_tabs=["Params"], page_tabs_for_pattern=["Params"])
        == "page"
    )
    assert screen_kind(pattern="/", has_popup=False, active_tabs=[], page_tabs_for_pattern=None) == "page"


# ------------------------------------------------------------------ review wave 1: slugs are not ids
@pytest.mark.parametrize(
    "path",
    [
        "/docs/getting-started-with-oauth2",
        "/docs/rest-api-v2-overview",
        "/p/Getting-Started-With-OAuth2",
        "/blog/2026-10-12-release-notes-v2",
        "/docs/internationalization-guide2",
    ],
)
def test_kebab_case_slugs_are_routes_not_ids(path: str):
    assert url_pattern(f"https://x.test{path}") == path


@pytest.mark.parametrize(
    "seg",
    [
        "aB3dEf9GhJ12kLmN4pQ",
        "9f86d081884c7d659a2feaa0c55ad015",
        "ckjz2h4g5f6d7s8a9q1w2",
        "V1StGXR8_Z5jdHi6B-myT",
    ],
)
def test_random_looking_tokens_are_still_ids(seg: str):
    assert url_pattern(f"https://x.test/o/{seg}") == "/o/:id"


def test_layout_key_separates_same_icon_controls_of_different_components():
    from blastradius.crawl.fingerprint import layout_key

    def icon_button(owner: str | None) -> Element:
        return Element(
            obs=UIElementObs(id=f"el_{owner}", role="button", name="", owner_hint=owner), icon="22d02b1e"
        )

    a, b, c = icon_button("data-v-aaaaaaaa"), icon_button("data-v-bbbbbbbb"), icon_button("data-v-aaaaaaaa")
    assert layout_key(a) != layout_key(b) and layout_key(a) == layout_key(c)
    assert element_key(a) == element_key(b)  # the fingerprint key stays owner-free
