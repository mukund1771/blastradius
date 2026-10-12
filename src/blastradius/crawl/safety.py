"""Explorer safety: egress allowlist, action classes, send gating. Every decision is traced by the caller.

Implements D28 and cases 1 (egress escape: Hoppscotch is an API client, so a typed URL plus Send could reach
localhost or a metadata IP), 2 (third-party widgets are never interacted with), 3 (deny patterns keep the
sitemap's /admin and /orgs out), 7 (a control with no name at all is not clicked) and 13 (politeness is
enforced by the explorer, not here).

Destructive intent is judged on the control (label, tooltip, href) and on the dialog it sits in: a dialog
whose title or prose says "delete" or "are you sure" makes every control but Cancel/No/Close destructive,
whatever the control is called. The built-in word floor below is added to the target's own list, so a target
file can add words but never remove these.

Everything here is a pure function over strings so it is testable without a browser.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from ..targets import CrawlConfig
from .perceive import Element

PROBE_TEXT = "blastradius-probe"  # the only string the explorer ever types

_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")
_INTERNAL_NAMES = frozenset({"localhost", "metadata", "metadata.google.internal", "instance-data"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_EXTRA_AUTH_HINTS = ("continue with", "sign in with", "log in with", "login with", "single sign", "sso")
_WIDGET_WORDS = ("chat with us", "live chat", "crisp", "intercom", "talk to us", "contact support")
# Added to CrawlConfig.destructive_words (targets.py defaults lack these); matched on whole words only.
BUILTIN_DESTRUCTIVE_WORDS = (
    "discard", "leave", "wipe", "erase", "empty", "disconnect", "unlink", "terminate", "uninstall",
    "unsubscribe", "close account", "cancel subscription", "permanently", "deactivate", "revoke",
    "delete", "remove", "clear", "reset", "sign out", "log out", "logout", "purchase", "pay",
)
# A dialog's prose is judged on the target's words plus these (narrower than the label floor: "empty" or
# "leave" in a sentence is ordinary English, "delete" and "are you sure" are not).
_DESTRUCTIVE_PROSE = (
    "are you sure", "cannot be undone", "can not be undone", "this action is permanent", "delete", "remove",
    "discard", "wipe", "erase", "permanently", "terminate", "uninstall",
)
_DISMISS = re.compile(
    r"(?i)^(cancel|no|nope|close|dismiss|back|go back|not now|never mind|keep|keep it|skip|later|x|✕|×)$"
)
# Icon signatures (svg path hash, perceive.iconSig) of tooltip-less controls that are known to be harmless.
# Each entry says what it is and where it was seen; a signature never goes on this list on a guess.
SAFE_ICON_SIGNATURES: dict[str, str] = {
    "95e94a38": "lucide grip-vertical: the drag handle of key/value rows on hoppscotch.io (a click without a "
    "drag does nothing); seen with an empty tippy tooltip on /, /graphql and /realtime, 2026-10-12",
}
# Roles that are entered or adjusted rather than pressed: an unnamed one is not "an unknown action".
_ENTRY_ROLES = frozenset({"textbox", "searchbox", "combobox", "slider", "spinbutton", "listbox"})
_SEND_VERBS = re.compile(r"(?i)^(send|run|execute|connect|subscribe|publish)\b")
_WRITE_VERB = re.compile(r"(?i)^(add new|new|add|create|save)\b")
_WRITE_NOUNS = re.compile(r"(?i)\b(collection|environment|request|folder|variable|tab|response|example)s?\b")


class ActionClass(StrEnum):
    NAVIGATE = "NAVIGATE"  # same-origin link
    UNKNOWN_CONTROL = "UNKNOWN_CONTROL"  # no name, tooltip or safe-listed icon: cannot tell what it does
    CLICK_SAFE = "CLICK_SAFE"
    LOCAL_WRITE = "LOCAL_WRITE"  # creates local (localStorage) state only
    NETWORK_SIDE_EFFECT = "NETWORK_SIDE_EFFECT"  # Send / Run / Connect
    AUTH = "AUTH"
    DESTRUCTIVE = "DESTRUCTIVE"
    EXTERNAL_WIDGET = "EXTERNAL_WIDGET"
    EXTERNAL_LINK = "EXTERNAL_LINK"  # off-origin: recorded, not followed
    DENIED_URL = "DENIED_URL"


@dataclass(frozen=True)
class Decision:
    action_class: ActionClass
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class EgressVerdict:
    allowed: bool
    host: str
    reason: str = ""


# ------------------------------------------------------------------------------------------ egress
def _numeric_host(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse IP literals including the decimal and hex IPv4 spellings a URL parser may accept."""
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        addr = None
        try:
            if re.fullmatch(r"0[xX][0-9a-fA-F]+", host):
                addr = ipaddress.IPv4Address(int(host, 16))
            elif re.fullmatch(r"\d{4,10}", host):
                addr = ipaddress.IPv4Address(int(host))
        except (ValueError, ipaddress.AddressValueError):
            addr = None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def is_private_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host in _INTERNAL_NAMES or host.endswith(_INTERNAL_SUFFIXES):
        return True
    addr = _numeric_host(host)
    if addr is None:
        return "." not in host and ":" not in host  # single-label names resolve on the local network
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def host_allowed(host: str, allow: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == a.lower() or host.endswith("." + a.lower()) for a in allow)


def egress_verdict(url: str, allow: Sequence[str]) -> EgressVerdict:
    """data:/blob:/about: pass; http(s) passes only for an allowlisted host (or subdomain) on a default port
    that is not a private, loopback or link-local address. Anything else is aborted and counted."""
    scheme = urlsplit(url).scheme.lower()
    if scheme in ("data", "blob", "about"):
        return EgressVerdict(True, scheme)
    parts = urlsplit(url)
    if scheme not in ("http", "https", "ws", "wss"):
        return EgressVerdict(False, scheme or "?", f"scheme {scheme or 'none'}")
    try:
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return EgressVerdict(False, "?", "unparseable authority")
    if not host:
        return EgressVerdict(False, "?", "no host")
    if is_private_host(host):
        return EgressVerdict(False, host, "private or loopback")
    default = _DEFAULT_PORTS.get(scheme.replace("ws", "http"))
    if port is not None and port != default:
        return EgressVerdict(False, host, f"port {port}")
    if not host_allowed(host, allow):
        return EgressVerdict(False, host, "not in egress_allow")
    return EgressVerdict(True, host)


# --------------------------------------------------------------------------------- word matching
def _has_word(text: str, words: Sequence[str]) -> str | None:
    low = f" {re.sub(r'[^a-z0-9]+', ' ', text.lower())} "
    for word in words:
        needle = f" {re.sub(r'[^a-z0-9]+', ' ', word.lower()).strip()} "
        if needle.strip() and needle in low:
            return word
    return None


def is_auth_text(text: str, cfg: CrawlConfig) -> bool:
    """True when text carries an auth word or a federated-login hint ("Continue with Google")."""
    return bool(_has_word(text, [*cfg.auth_words, *_EXTRA_AUTH_HINTS]))


def _same_origin(url: str, base_url: str) -> bool:
    a, b = urlsplit(url), urlsplit(base_url)
    return (a.scheme, a.hostname, a.port or _DEFAULT_PORTS.get(a.scheme)) == (
        b.scheme,
        b.hostname,
        b.port or _DEFAULT_PORTS.get(b.scheme),
    )


def url_denied(url: str, cfg: CrawlConfig) -> str | None:
    for pattern in cfg.deny_url_patterns:
        if re.search(pattern, url):
            return pattern
    return None


def normalise_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or ""
    return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{path}"


def send_url_allowed(request_url: str | None, cfg: CrawlConfig) -> bool:
    """Send is allowed only when the request URL field equals an entry of send_allowed_urls."""
    if not request_url:
        return False
    return normalise_url(request_url) in {normalise_url(u) for u in cfg.send_allowed_urls}


# ---------------------------------------------------------------------------------- action classes
def _label(el: Element) -> str:
    o = el.obs
    return " ".join(x for x in (o.name, o.text, o.aria_label or "", o.title_attr or "", el.tooltip) if x)


def destructive_words(cfg: CrawlConfig) -> list[str]:
    """The target's list plus the built-in floor (a config can add words, never take these away)."""
    return [*cfg.destructive_words, *(w for w in BUILTIN_DESTRUCTIVE_WORDS if w not in cfg.destructive_words)]


def dialog_is_destructive(title: str | None, prose: str, cfg: CrawlConfig) -> str | None:
    """The word that makes a dialog destructive, judged on its title and its prose (not its buttons).

    A title is short and imperative ("Leave team?", "Discard changes"), so it is judged on the full label
    vocabulary. Prose is ordinary English where "leave" and "empty" are everyday words ("Leave empty to inherit"),
    so it is judged only on the narrower prose list.
    """
    return (_has_word(title or "", [*cfg.destructive_words, *BUILTIN_DESTRUCTIVE_WORDS])
            or _has_word(f"{title or ''} {prose}", _DESTRUCTIVE_PROSE))


def is_dismiss_label(name: str) -> bool:
    """Cancel / No / Close and friends: the only controls a destructive dialog leaves clickable."""
    return bool(_DISMISS.match(name.strip()))


def is_unknown_control(el: Element) -> bool:
    """A pressable control with no name, no tooltip and no safe-listed icon: what it does is unknowable."""
    o = el.obs
    if o.href or o.role in _ENTRY_ROLES or el.input_type is not None or el.editor:
        return False
    if o.name or o.text or o.aria_label or o.title_attr or el.tooltip:
        return False
    return not (el.icon and el.icon in SAFE_ICON_SIGNATURES)


def _is_local_write(name: str) -> bool:
    """New/Add/Create/Save with no object, or with a local object (collection, environment, request, ...)."""
    m = _WRITE_VERB.match(name)
    if not m:
        return False
    rest = name[m.end() :].strip()
    return not rest or rest.lower() == "new" or bool(_WRITE_NOUNS.search(rest))


def classify_action(
    el: Element,
    cfg: CrawlConfig,
    *,
    base_url: str,
    page_url: str,
    popup_is_auth: bool = False,
    popup_destructive: str | None = None,
    request_url: str | None = None,
) -> Decision:
    """Decide what an element would do if clicked and whether the explorer may do it.

    Order matters: the most restrictive classes are checked first. `request_url` is the current value of the
    request URL field (None when unknown, which is treated as not allowlisted). `popup_destructive` is the
    word that made the enclosing dialog destructive (see `dialog_is_destructive`)."""
    o = el.obs
    label = _label(el)
    if _has_word(label, _WIDGET_WORDS):
        return Decision(ActionClass.EXTERNAL_WIDGET, False, "third-party widget")
    if o.href:
        scheme = urlsplit(o.href).scheme.lower()
        if scheme not in ("http", "https"):
            return Decision(ActionClass.EXTERNAL_LINK, False, f"{scheme or 'no'} scheme")
        denied = url_denied(o.href, cfg)
        if denied:
            return Decision(ActionClass.DENIED_URL, False, f"matches deny pattern {denied}")
        if not _same_origin(o.href, base_url):
            return Decision(ActionClass.EXTERNAL_LINK, False, "off-origin link recorded, not followed")
        if el.target == "_blank":
            return Decision(ActionClass.EXTERNAL_LINK, False, "opens a new window, not followed")
    auth = _has_word(" ".join([label, o.href or ""]), [*cfg.auth_words, *_EXTRA_AUTH_HINTS])
    if auth or popup_is_auth:
        return Decision(ActionClass.AUTH, False, f"auth word {auth!r}" if auth else "inside an auth dialog")
    destructive = _has_word(" ".join([label, o.href or ""]), destructive_words(cfg))
    if destructive:
        return Decision(ActionClass.DESTRUCTIVE, False, f"destructive word {destructive!r}")
    if popup_destructive and not is_dismiss_label(o.name or o.text or o.aria_label or ""):
        return Decision(
            ActionClass.DESTRUCTIVE, False, f"inside a destructive dialog ({popup_destructive!r})"
        )
    if is_unknown_control(el):
        return Decision(
            ActionClass.UNKNOWN_CONTROL, False, "no name, no tooltip and the icon is not on the safe list"
        )
    name = o.name or o.text
    if _SEND_VERBS.match(name):
        if send_url_allowed(request_url, cfg):
            return Decision(ActionClass.NETWORK_SIDE_EFFECT, True, "request url is in send_allowed_urls")
        return Decision(ActionClass.NETWORK_SIDE_EFFECT, False, "request url is not in send_allowed_urls")
    if _is_local_write(name):
        if cfg.allow_local_writes:
            return Decision(ActionClass.LOCAL_WRITE, True, "creates local state only")
        return Decision(ActionClass.LOCAL_WRITE, False, "allow_local_writes is false")
    if o.href and el.target != "_blank" and o.href.split("#", 1)[0] != page_url.split("#", 1)[0]:
        return Decision(ActionClass.NAVIGATE, True, "same-origin link")
    return Decision(ActionClass.CLICK_SAFE, True, "")
