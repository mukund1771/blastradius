"""Deterministic perception: one `page.evaluate` call turns the DOM into elements, popups and active tabs.

Implements D04 (own explorer; perception is code, not an LLM), D28/case 2 (third-party widgets are never
perceived), case 5 (keyboard-shortcut glyphs and chords are stripped before anything is hashed or matched),
case 7 (nameless icon buttons are told apart by an SVG signature), case 9 (CodeMirror/Monaco editors are
recorded once and their lines are never enumerated) and case 10 (relative times, durations, sizes and counters
are masked so a clock tick does not create a new screen).

Case 7 is closed from two sides: a nameless icon button is named by its tooltip when the page exposes one
(tippy keeps the content on `el._tippy`, a snapshot keeps it as `data-br-tip`), and the explorer refuses to
click what stays nameless unless its icon signature is on the documented safe list
(`safety.SAFE_ICON_SIGNATURES`). `owner_hint` carries the scoped-style attribute (`data-v-<8hex>`) of the
element or its nearest ancestor (D23: a drift detector and canonical key, never a link rung). Element names,
text and placeholders pass the secret scrubber before they are persisted or prompted (case 58).

The page script returns raw strings; every normalisation rule lives in Python so it can be unit tested without
a browser (`clean_label`, `strip_shortcuts`, `mask_volatile`, `accessible_name`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..ids import stable_id
from ..models import UIElementObs
from .scrub import scrub_text, scrub_url

MAX_ELEMENTS = 400
MAX_LABEL = 160
MAX_BODY_TEXT = 30_000
NAME_LIMIT_FOR_ID = 120

# Component kits often stamp every control with a generic aria-label ("button", "Link"): not a name.
DEGENERATE_LABELS = frozenset({"button", "link", "icon", "image", "img", "svg", "input", "item", "menu"})

# ------------------------------------------------------------------------------ text normalisation
_GLYPHS = "⌘⌥⇧⌃↵⏎⎋⌫⌦⇥⇪⌤"
_ARROWS = "↑↓←→"
_WORD_MODS = r"(?:ctrl|control|cmd|meta|alt|shift)"
_NAMED_KEYS = r"(?:esc|escape|enter|return|tab|del|delete|backspace|space|home|end|pgup|pgdn|f\d{1,2})"
_ONE_KEY = rf"[A-Za-z0-9/?.,;'\[\]\\`=+\-{_ARROWS}{_GLYPHS}]"
_CHORD_BODY = rf"(?:(?:{_WORD_MODS}|[{_GLYPHS}])\s*[+\-]?\s*)+(?:{_NAMED_KEYS}|{_ONE_KEY})(?![A-Za-z0-9])"
# a chord at the end ("Search Ctrl K", "Theme alt+T", "Send Ctrl ↵", "Zoom in Ctrl +") or at the start
# ("⌘ K Search")
_CHORD_TAIL = re.compile(rf"(?:^|\s+){_CHORD_BODY}\s*$", re.IGNORECASE)
_CHORD_HEAD = re.compile(rf"^\s*{_CHORD_BODY}(?:\s+|$)", re.IGNORECASE)
# a parenthesised chord of any spelling ("Open (Ctrl+O)"), and a glyph chord anywhere ("Search (⌘ K)")
_PAREN_CHORD = re.compile(rf"\(\s*{_CHORD_BODY}\s*\)", re.IGNORECASE)
_GLYPH_CHORD = re.compile(
    rf"\(?[{_GLYPHS}]+\s*\+?\s*(?:{_NAMED_KEYS}|{_ONE_KEY})?\)?(?![A-Za-z0-9])", re.IGNORECASE
)
# the Linux spelling of the macOS escape glyph: "Close Esc" and "Close ⎋" are one name
_BARE_ESC = re.compile(r"(?<=\S)\s+esc$", re.IGNORECASE)
_STRAY_GLYPHS = re.compile(f"[{_GLYPHS}{_ARROWS}]")

_REL_TIME = re.compile(
    r"(?i)\b(?:just now|yesterday|today|an? (?:second|minute|hour|day|week|month|year)s? ago"
    r"|\d+\s*(?:s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|wks?|weeks?|mo|months?"
    r"|y|yrs?|years?)\s+ago)\b"
)
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?)?\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:am|pm)?\b",
    re.IGNORECASE,
)
_DURATION = re.compile(r"(?i)\b\d+(?:\.\d+)?\s?(?:ms|milliseconds?|s|secs?|seconds?)\b")
_SIZE = re.compile(r"(?i)\b\d+(?:\.\d+)?\s?(?:b|kb|mb|gb|kib|mib)\b")
_ABBREV_COUNT = re.compile(r"\b\d+(?:\.\d+)?[kKmM]\b")
_PAREN_COUNT = re.compile(r"([(\[])\s*\d+\s*([)\]])")
_COUNTED_NOUN = re.compile(
    r"(?i)\b\d+\s+(items?|results?|requests?|collections?|environments?|tabs?|variables?|headers?|params?"
    r"|stars?)\b"
)
_WS = re.compile(r"\s+")
_TEXT_NODE = re.compile(r">([^<>]+)<")


def strip_shortcuts(text: str) -> str:
    """Remove keyboard-shortcut chords (case 5): glyphs, `Ctrl K`, `alt+T`, `Ctrl + Enter`, `Ctrl ↵`,
    `Ctrl +`, `(Ctrl+O)` and a trailing `Esc`, so the macOS and Linux spellings of a control are one name."""
    out = _WS.sub(" ", text).strip()
    out = _PAREN_CHORD.sub(" ", out)
    for _ in range(2):  # a name can carry both a leading and a trailing chord
        out = _CHORD_TAIL.sub("", out)
        out = _CHORD_HEAD.sub("", out)
    out = _GLYPH_CHORD.sub(" ", out)
    out = _BARE_ESC.sub("", _STRAY_GLYPHS.sub("", out).strip())
    return _WS.sub(" ", out).strip(" +·|-")


def mask_volatile(text: str) -> str:
    """Replace values that change between runs or between ticks (case 10) with fixed tokens. Durations of
    any unit share one token: the same response time flips between `999 ms` and `1.0 s` from run to run."""
    text = _REL_TIME.sub("<time>", text)
    text = _TIMESTAMP.sub("<time>", text)
    text = _DURATION.sub("<ms>", text)
    text = _SIZE.sub("<size>", text)
    text = _ABBREV_COUNT.sub("<n>", text)
    text = _PAREN_COUNT.sub(r"\1#\2", text)
    return _COUNTED_NOUN.sub(r"# \1", text)


def clean_label(raw: str | None, *, limit: int = MAX_LABEL) -> str:
    """Accessible-name normalisation: whitespace, shortcut chords, volatile values, length."""
    if not raw:
        return ""
    return mask_volatile(strip_shortcuts(raw))[:limit].strip()


def accessible_name(raw: dict[str, Any]) -> str:
    """aria-label > aria-labelledby > title > (label for inputs) > visible text > tooltip > placeholder > alt.

    A generic aria-label such as "button" is skipped so the visible text can name the control. The tooltip
    (tippy content, `aria-describedby` target) names an icon-only control that has nothing else (case 7)."""
    for key in ("aria", "labelledby", "title", "label", "text", "tooltip", "placeholder", "alt"):
        value = clean_label(raw.get(key))
        if value and not (key == "aria" and value.lower() in DEGENERATE_LABELS):
            return value
    return ""


# ------------------------------------------------------------------------------------- page script
# Runs inside the page. `opts` = {widgets: [css], maxElements, maxText}. Never reads input values.
PERCEIVE_JS = r"""
(opts) => {
  const WIDGETS = (opts.widgets || []).filter(Boolean);
  const inWidget = (el) => WIDGETS.some((s) => {
    try { return !!el.closest(s); } catch (e) { return false; }
  });
  const SEL = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=tab]', '[role=menuitem]', '[role=menuitemcheckbox]', '[role=menuitemradio]',
    '[role=link]', '[role=checkbox]', '[role=switch]', '[role=combobox]', '[role=option]', '[role=radio]',
    '[role=treeitem]', '[contenteditable=""]', '[contenteditable=true]', '[tabindex]', '[onclick]',
  ].join(',');
  const EDITOR = '.cm-editor, .monaco-editor, .CodeMirror';
  const DIALOGS = '[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open], [role=menu]';
  const FLOATING = '[data-tippy-root], .tippy-box, [role=tooltip], [role=listbox]';
  const LANDMARK = 'nav, header, aside, [role=navigation], [role=banner], [role=menubar], [role=toolbar]';
  const PROSE_SKIP = 'a[href], button, input, select, textarea, summary, [role=button], [role=menuitem], '
    + '[role=menuitemcheckbox], [role=menuitemradio], [role=option], [role=tab], [role=link], '
    + '[role=checkbox], [role=switch], [role=radio], [onclick]';
  const CONTAINER_ROLES = new Set(['dialog', 'alertdialog', 'tooltip', 'menu', 'tablist', 'toolbar', 'group',
    'presentation', 'none', 'region', 'main', 'navigation', 'banner', 'contentinfo', 'list', 'tree',
    'listbox']);
  const vis = (el) => {
    if (!el.isConnected) return false;
    if (el.closest('[data-br-hidden]') || el.closest('head')) return false;
    if (el.checkVisibility) {
      if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    } else {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'CANVAS', 'IFRAME', 'KBD']);
  const cls = (el) => (typeof el.className === 'string' ? el.className : '').split(/\s+/);
  const isShortcut = (n) => n.tagName === 'KBD' || cls(n).some((c) => /^(shortcut-key|kbd|hotkey)$/.test(c));
  const textOf = (root, cap) => {
    let out = '';
    const walk = (n) => {
      if (out.length > cap) return;
      if (n.nodeType === 3) { out += n.nodeValue; return; }
      if (n.nodeType !== 1) return;
      if (SKIP_TAGS.has(n.tagName.toUpperCase()) || isShortcut(n)) return;
      if (n.getAttribute('aria-hidden') === 'true' || n.hasAttribute('hidden')
          || n.hasAttribute('data-br-hidden') || inWidget(n)) return;
      if (n !== root) {
        const cs = getComputedStyle(n);
        if (cs.display === 'none' || cs.visibility === 'hidden') return;
      }
      if (n.matches && n.matches(EDITOR)) return;
      const block = n !== root
        && /^(DIV|P|LI|H[1-6]|SECTION|HEADER|FOOTER|TR|UL|OL|NAV|BUTTON|LABEL)$/.test(n.tagName);
      if (block) out += ' ';
      for (const c of n.childNodes) walk(c);
      if (block) out += ' ';
    };
    walk(root);
    return out.replace(/\s+/g, ' ').trim();
  };
  const labelledBy = (el) => {
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    return ids.map((id) => { const t = document.getElementById(id); return t ? textOf(t, 200) : ''; })
      .join(' ').trim();
  };
  const implicitRole = (el) => {
    const explicit = (el.getAttribute('role') || '').split(/\s+/)[0];
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'range') return 'slider';
      if (t === 'search') return 'searchbox';
      if (['submit', 'button', 'reset', 'image'].includes(t)) return 'button';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'generic';
  };
  const volatileId = (id) => /[:]|\d{3,}|^(headlessui|radix|v-|vue|tippy|el-|__|ember|react)/i.test(id);
  const q = (s) => { try { return document.querySelectorAll(s); } catch (e) { return []; } };
  const selectorOf = (el) => {
    const tid = el.getAttribute('data-testid') || el.getAttribute('data-test');
    if (tid) {
      const attr = el.hasAttribute('data-testid') ? 'data-testid' : 'data-test';
      const s = `[${attr}="${CSS.escape(tid)}"]`;
      if (q(s).length === 1) return s;
    }
    if (el.id && !volatileId(el.id)) {
      const s = '#' + CSS.escape(el.id);
      if (q(s).length === 1) return s;
    }
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && n !== document.documentElement) {
      let part = n.tagName.toLowerCase();
      if (n !== el && n.id && !volatileId(n.id) && q('#' + CSS.escape(n.id)).length === 1) {
        parts.unshift('#' + CSS.escape(n.id));
        break;
      }
      const p = n.parentElement;
      if (p) {
        const same = Array.from(p.children).filter((c) => c.tagName === n.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(n) + 1})`;
      }
      parts.unshift(part);
      n = p;
    }
    return parts.join(' > ');
  };
  const iconSig = (el) => {
    const svg = el.tagName.toLowerCase() === 'svg' ? el : el.querySelector('svg');
    if (!svg) return null;
    const mark = svg.getAttribute('data-br-icon');
    if (mark) return mark;
    let acc = '';
    svg.querySelectorAll('path, circle, rect, line, polyline, polygon, ellipse').forEach((p) => {
      acc += p.tagName + ['d', 'points', 'cx', 'cy', 'r', 'x', 'y', 'width', 'height', 'x1', 'y1', 'x2', 'y2']
        .map((a) => p.getAttribute(a) || '').join(',');
    });
    if (!acc) return null;
    let h = 2166136261;
    for (let i = 0; i < acc.length; i += 1) { h ^= acc.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return h.toString(16).padStart(8, '0');
  };
  const isTab = (el) => el.getAttribute('role') === 'tab' || cls(el).includes('tab');
  const isActive = (el) => el.getAttribute('aria-selected') === 'true'
    || cls(el).some((c) => c === 'active' || c === 'is-active' || c === 'selected');
  const zOf = (el) => {
    let n = el;
    while (n && n.nodeType === 1) {
      const z = parseInt(getComputedStyle(n).zIndex, 10);
      if (!Number.isNaN(z)) return z;
      n = n.parentElement;
    }
    return 0;
  };
  // A role=menu is a popup layer only when it floats; one that sits in the flow of a landmark is the page's
  // own navigation (hiding the rest of the page behind it would blind the crawl).
  const floating = (el) => {
    if (el.matches(LANDMARK)) return false;
    for (let n = el, d = 0; n && n.nodeType === 1 && d < 8; n = n.parentElement, d += 1) {
      if (n !== el && n.matches(LANDMARK)) break;
      if (n.hasAttribute('data-br-float') || n.matches('[data-tippy-root], .tippy-box, dialog')) return true;
      const pos = getComputedStyle(n).position;
      if (pos === 'absolute' || pos === 'fixed') return true;
    }
    return false;
  };
  const persistentBar = (el) => el.matches('[role=menu]') && !floating(el) && !!el.closest(LANDMARK);
  const stripTags = (t) => t.replace(/<kbd[^>]*>.*?<\/kbd>/gis, ' ').replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ').trim();
  const tooltipOf = (el) => {
    const tp = el._tippy;
    if (tp && tp.props) {
      const c = tp.props.content;
      if (typeof c === 'string' && c.trim()) return stripTags(c).slice(0, 120);
      if (c && c.nodeType === 1) return textOf(c, 120);
    }
    const attr = el.getAttribute('data-br-tip') || el.getAttribute('data-tippy-content')
      || el.getAttribute('data-tooltip') || '';
    if (attr.trim()) return stripTags(attr).slice(0, 120);
    const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
    const d = ids.map((id) => { const t = document.getElementById(id); return t ? textOf(t, 120) : ''; });
    return d.join(' ').trim().slice(0, 120);
  };
  const ownerOf = (el) => {
    for (let n = el, d = 0; n && n.nodeType === 1 && d < 60; n = n.parentElement, d += 1) {
      const baked = n.getAttribute('data-br-owner');
      if (baked) return baked;
      for (const a of n.attributes) { if (/^data-v-[0-9a-f]{8}$/.test(a.name)) return a.name; }
    }
    return null;
  };
  // visible prose of a popup: its text without the text of its controls (those are judged one by one)
  const proseOf = (root, cap) => {
    let out = '';
    const walk = (n) => {
      if (out.length > cap) return;
      if (n.nodeType === 3) { out += n.nodeValue; return; }
      if (n.nodeType !== 1) return;
      if (SKIP_TAGS.has(n.tagName.toUpperCase()) || isShortcut(n) || inWidget(n)) return;
      if (n.getAttribute('aria-hidden') === 'true' || n.hasAttribute('hidden')
          || n.hasAttribute('data-br-hidden')) return;
      if (n !== root) {
        const cs = getComputedStyle(n);
        if (cs.display === 'none' || cs.visibility === 'hidden') return;
        if (n.matches(PROSE_SKIP)) return;
      }
      out += ' ';
      for (const c of n.childNodes) walk(c);
      out += ' ';
    };
    walk(root);
    return out.replace(/\s+/g, ' ').trim();
  };
  const titleOf = (d) => {
    const al = d.getAttribute('aria-label');
    if (al) return al.trim();
    const lb = labelledBy(d);
    if (lb) return lb;
    const h = d.querySelector('h1, h2, h3, h4, [role=heading], [class*=title], header');
    if (h) { const t = textOf(h, 120); if (t) return t; }
    const input = d.querySelector('input[placeholder], textarea[placeholder]');
    return input ? (input.getAttribute('placeholder') || '').trim() : '';
  };

  // --- popups, bottom to top: dialogs and menus, plus floating (tippy) panels that hold controls
  const popups = [];
  for (const d of q(DIALOGS)) {
    if (vis(d) && !inWidget(d) && !persistentBar(d)) popups.push({el: d, kind: 'dialog'});
  }
  for (const d of q(FLOATING)) {
    if (!vis(d) || inWidget(d) || popups.some((p) => p.el === d || p.el.contains(d))) continue;
    if (d.querySelector('a[href], button, input, select, textarea, [role=menuitem], [role=option]')) {
      popups.push({el: d, kind: 'menu'});
    }
  }
  popups.sort((a, b) => zOf(a.el) - zOf(b.el) || (a.el.compareDocumentPosition(b.el) & 4 ? -1 : 1));
  const popupInfo = popups.map((p) => ({
    title: p.kind === 'menu' ? '' : titleOf(p.el).slice(0, 120),
    role: p.kind === 'menu' ? 'menu' : (p.el.getAttribute('role') || p.el.tagName.toLowerCase()),
    selector: selectorOf(p.el),
    text: proseOf(p.el, 600).slice(0, 600),
  }));

  // --- elements
  const seen = new Set();
  const out = [];
  let truncated = 0;
  const editors = [];
  for (const ed of q(EDITOR)) {
    if (vis(ed) && !inWidget(ed) && !ed.parentElement.closest(EDITOR)) editors.push(ed);
  }
  const insideEditor = (el) => editors.some((ed) => ed !== el && ed.contains(el));
  const collect = (el, editorRole) => {
    if (seen.has(el)) return null;
    seen.add(el);
    if (!vis(el) || inWidget(el) || el.closest('[inert]')) return null;
    if (out.length >= opts.maxElements) { truncated += 1; return null; }
    const tag = el.tagName.toLowerCase();
    const role = editorRole || implicitRole(el);
    if (!editorRole && CONTAINER_ROLES.has(role)) return null;
    const aria = (el.getAttribute('aria-label') || '').trim();
    const lby = labelledBy(el);
    const titleAttr = (el.getAttribute('title') || '').trim();
    let text = '';
    if (tag === 'select') {
      const o = el.selectedOptions && el.selectedOptions[0];
      text = o ? o.text.trim() : '';
    } else if (tag !== 'input' && tag !== 'textarea') {
      text = textOf(el, 300);
    }
    let labelText = '';
    if (el.labels && el.labels.length) {
      labelText = Array.from(el.labels).map((l) => textOf(l, 120)).join(' ').trim();
    }
    const placeholder = (el.getAttribute('placeholder') || '').trim();
    let alt = (el.getAttribute('alt') || '').trim();
    if (!alt) {
      const im = el.querySelector('img[alt], [role=img][aria-label]');
      if (im) alt = (im.getAttribute('alt') || im.getAttribute('aria-label') || '').trim();
    }
    const icon = iconSig(el);
    const tooltip = tooltipOf(el);
    const nameless = !aria && !text && !titleAttr && !icon && !tooltip;
    if (!editorRole && role === 'generic' && (el.querySelector(SEL) || nameless)) return null;
    const r = el.getBoundingClientRect();
    let popup = -1;
    for (let i = popups.length - 1; i >= 0; i -= 1) { if (popups[i].el.contains(el)) { popup = i; break; } }
    const rec = {
      tag, role,
      aria, labelledby: lby, title: titleAttr, text, label: labelText, placeholder, alt, tooltip,
      owner: ownerOf(el), autocomplete: el.getAttribute('autocomplete') || null,
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || null,
      domId: el.id || null,
      href: tag === 'a' ? (el.href || null) : null,
      target: el.getAttribute('target') || null,
      inputType: tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : null,
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      tab: isTab(el), tabActive: isTab(el) && isActive(el), icon,
      popup,
      bbox: [Math.round((r.left + scrollX) * 10) / 10, Math.round((r.top + scrollY) * 10) / 10,
             Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10],
      selector: selectorOf(el),
      editor: !!editorRole,
    };
    out.push(rec);
    return rec;
  };
  for (const ed of editors) {
    const cm = ed.querySelector('[role=textbox], .cm-content') || ed;
    const rec = collect(ed, 'textbox');
    if (rec) {
      rec.aria = (cm.getAttribute('aria-label') || '').trim();
      const ph = ed.querySelector('.cm-placeholder');
      rec.placeholder = ph ? ph.textContent.trim() : (cm.getAttribute('data-placeholder') || '').trim();
      rec.text = '';
      rec.icon = null;
    }
  }
  for (const el of q(SEL)) {
    if (insideEditor(el)) continue;
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && parseInt(tabindex, 10) < 0
        && !el.matches('a[href], button, input, select, textarea, [role]')) continue;
    collect(el, null);
  }
  // pointer-cursor leaves: clickable things with no semantics (a Vue @click on a div)
  for (const el of Array.from(q('div, span, li, label, img, svg')).slice(0, 6000)) {
    if (seen.has(el) || out.length >= opts.maxElements || insideEditor(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.cursor !== 'pointer') continue;
    if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer') continue;
    if (el.querySelector(SEL)) continue;
    if (el.closest('a[href], button, [role=button], [role=tab], [role=menuitem], summary')) continue;
    if (!textOf(el, 200) && !el.getAttribute('aria-label') && !el.getAttribute('title')) continue;
    collect(el, null);
  }

  // --- active tabs: role=tab + aria-selected, or a `.tab.active` class pair (Hoppscotch's own tab strips).
  // Each tab is tagged with the popup that holds it so a modal's identity ignores the page behind it.
  const tabs = [];
  for (const t of q('[role=tab], .tab')) {
    if (!isTab(t) || !isActive(t) || !vis(t) || inWidget(t)) continue;
    const label = (t.getAttribute('aria-label') || '').trim() || textOf(t, 80);
    let popup = -1;
    for (let i = popups.length - 1; i >= 0; i -= 1) { if (popups[i].el.contains(t)) { popup = i; break; } }
    tabs.push({label: label || ('icon:' + (iconSig(t) || '')), popup});
  }

  const body = textOf(document.body, opts.maxText);
  return {
    url: location.href, title: document.title, popups: popupInfo, tabs, body: body.slice(0, opts.maxText),
    elements: out, truncated, editors: editors.length,
    interactive: out.filter((o) => !o.disabled).length,
  };
}
"""

# Runs inside the page: a scripts-stripped, input-values-stripped, widget-free copy of the DOM. Hidden nodes
# are marked data-br-hidden (an offline replay keeps their visibility without the CSS), svg icons keep a
# data-br-icon signature (nameless icon buttons stay distinguishable offline), and only the class tokens that
# carry meaning for perception are kept.
SNAPSHOT_JS = r"""
(opts) => {
  const WIDGETS = (opts.widgets || []).filter(Boolean);
  const KEEP = new Set(['tab', 'active', 'is-active', 'selected', 'shortcut-key', 'kbd', 'hotkey',
    'cm-editor', 'cm-content', 'cm-placeholder', 'monaco-editor', 'CodeMirror']);
  const root = document.documentElement;
  const live = Array.from(root.querySelectorAll('*'));
  const copy = root.cloneNode(true);
  const dup = Array.from(copy.querySelectorAll('*'));
  const sig = (svg) => {
    let acc = '';
    svg.querySelectorAll('path, circle, rect, line, polyline, polygon, ellipse').forEach((p) => {
      acc += p.tagName + ['d', 'points', 'cx', 'cy', 'r', 'x', 'y', 'width', 'height', 'x1', 'y1', 'x2', 'y2']
        .map((a) => p.getAttribute(a) || '').join(',');
    });
    if (!acc) return null;
    let h = 2166136261;
    for (let i = 0; i < acc.length; i += 1) { h ^= acc.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return h.toString(16).padStart(8, '0');
  };
  const drop = [];
  live.forEach((el, i) => {
    const c = dup[i];
    if (!c) return;
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'noscript', 'iframe', 'template', 'canvas', 'object', 'embed', 'meta', 'link']
        .includes(tag)) { drop.push(c); return; }
    if (WIDGETS.some((w) => { try { return el.matches(w); } catch (e) { return false; } })) {
      drop.push(c);
      return;
    }
    const tp = el._tippy && el._tippy.props ? el._tippy.props.content : null;
    const tip = typeof tp === 'string' ? tp : (el.getAttribute('data-tippy-content') || '');
    if (tip.trim()) c.setAttribute('data-br-tip', tip.slice(0, 200));
    for (const a of Array.from(el.attributes)) {
      if (/^data-v-[0-9a-f]{8}$/.test(a.name)) { c.setAttribute('data-br-owner', a.name); break; }
    }
    if (el.getAttribute('role') === 'menu' && ['absolute', 'fixed'].includes(getComputedStyle(el).position)) {
      c.setAttribute('data-br-float', '1');
    }
    if (tag === 'svg') {
      const s = sig(el);
      if (s) c.setAttribute('data-br-icon', s);
      c.textContent = '';
    }
    if (tag === 'input' || tag === 'textarea') {
      c.removeAttribute('value');
      if (tag === 'textarea') c.textContent = '';
    }
    if (el.matches('.cm-editor, .monaco-editor, .CodeMirror')) {
      const cm = el.querySelector('[role=textbox], .cm-content');
      const ph = el.querySelector('.cm-placeholder');
      c.textContent = '';
      const stub = document.createElement('div');
      stub.className = 'cm-content';
      stub.setAttribute('role', 'textbox');
      const label = (cm && cm.getAttribute('aria-label')) || '';
      if (label) stub.setAttribute('aria-label', label);
      if (ph) stub.setAttribute('data-placeholder', ph.textContent.trim().slice(0, 80));
      stub.textContent = '\u200b';      // an empty div has no height offline; the editor would vanish
      c.appendChild(stub);
    }
    if (typeof c.className === 'string' && c.className) {
      const kept = c.className.split(/\s+/).filter((k) => KEEP.has(k));
      if (kept.length) c.className = kept.join(' '); else c.removeAttribute('class');
    }
    for (const attr of Array.from(c.attributes)) {
      if (/^data-v-|^data-tippy|^data-tauri/.test(attr.name)) c.removeAttribute(attr.name);
    }
    if (tag !== 'head' && tag !== 'html' && !el.closest('head')) {
      // not checkVisibility(): it is false for `display: contents` wrappers whose children are visible
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0
          || el.hasAttribute('hidden')) c.setAttribute('data-br-hidden', '1');
    }
  });
  drop.forEach((n) => n.remove());
  const walker = document.createTreeWalker(copy, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_COMMENT);
  const kill = [];
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (n.nodeType === 8 || !n.nodeValue.trim()) { kill.push(n); continue; }
    n.nodeValue = n.nodeValue.replace(/\s+/g, ' ').slice(0, 300);
  }
  kill.forEach((n) => n.remove());
  return '<!DOCTYPE html>' + copy.outerHTML;
}
"""


# ------------------------------------------------------------------------------------------ model
@dataclass
class Element:
    """A perceived element: the contract shape plus what the explorer needs and the graph does not."""

    obs: UIElementObs
    popup: int = -1  # index of the topmost popup that contains it, -1 = the page itself
    icon: str | None = None  # svg path signature; identifies nameless icon buttons (case 7)
    input_type: str | None = None
    disabled: bool = False
    tab: bool = False
    tab_active: bool = False
    target: str | None = None
    editor: bool = False
    tooltip: str = ""  # harvested tooltip text: names an icon-only control (case 7)
    autocomplete: str | None = None


@dataclass
class Perception:
    url: str
    pattern: str
    title: str
    elements: list[Element] = field(default_factory=list)
    popups: list[dict[str, Any]] = field(default_factory=list)
    active_tabs: list[str] = field(default_factory=list)
    body_text: str = ""
    interactive_count: int = 0
    truncated: int = 0
    editors: int = 0

    @property
    def top_popup(self) -> int:
        return len(self.popups) - 1

    @property
    def dialog_title(self) -> str | None:
        if not self.popups:
            return None
        return popup_label(self.popups[-1]) or "dialog"

    @property
    def dialog_prose(self) -> str:
        """Visible text of the topmost popup without the text of its controls (secrets scrubbed)."""
        if not self.popups:
            return ""
        return scrub_text(str(self.popups[-1].get("text") or ""))

    def reachable(self) -> list[Element]:
        """What the user can reach right now: the topmost popup when one is open, else the page."""
        top = self.top_popup
        return [e for e in self.elements if e.popup == top]


def popup_label(popup: dict[str, Any]) -> str:
    return scrub_text(clean_label(popup.get("title"), limit=120)) or str(popup.get("role") or "")


def _identity(name: str, icon: str | None) -> str:
    return name[:NAME_LIMIT_FOR_ID] or (f"icon:{icon}" if icon else "")


def raw_keys(raw: dict[str, Any], *, layer: int | None = None) -> frozenset[tuple[str, str]]:
    """(role, name-or-icon) keys of one layer of a raw page record (default: the topmost), before ids."""
    top = len(raw.get("popups") or []) - 1 if layer is None else layer
    keys = set()
    for rec in raw.get("elements") or []:
        key = (str(rec.get("role") or "generic"), _identity(accessible_name(rec), rec.get("icon")))
        if key[1] and rec.get("popup", -1) == top:
            keys.add(key)
    return frozenset(keys)


def build(raw: dict[str, Any], *, target: str, pattern: str) -> Perception:
    """Raw page record -> Perception with deterministic ids and per-(role, name) ordinals."""
    popups = raw.get("popups") or []
    ordinal_by_name: dict[tuple[int, str, str], int] = {}
    ordinal_by_identity: dict[tuple[int, str, str], int] = {}
    elements: list[Element] = []
    for rec in raw.get("elements") or []:
        name = scrub_text(accessible_name(rec))
        role = str(rec.get("role") or "generic")
        popup = int(rec.get("popup", -1))
        icon = rec.get("icon")
        identity = _identity(name, icon)
        ordinal = ordinal_by_name.get((popup, role, name), 0)
        ordinal_by_name[(popup, role, name)] = ordinal + 1
        id_ordinal = ordinal_by_identity.get((popup, role, identity), 0)
        ordinal_by_identity[(popup, role, identity)] = id_ordinal + 1
        scope = pattern if popup < 0 else f"{pattern}|{popup_label(popups[popup])}"
        bbox = rec.get("bbox")
        aria = scrub_text(clean_label(rec.get("aria")))
        tooltip = scrub_text(clean_label(rec.get("tooltip")))
        owner = rec.get("owner")
        obs = UIElementObs(
            id=stable_id("el", target, scope, role, identity, id_ordinal),
            role=role,
            name=name,
            text=scrub_text(clean_label(rec.get("text"))),
            tag=str(rec.get("tag") or ""),
            testid=rec.get("testid"),
            dom_id=rec.get("domId"),
            href=scrub_url(rec["href"]) if rec.get("href") else None,
            placeholder=scrub_text(clean_label(rec.get("placeholder"))) or None,
            title_attr=scrub_text(clean_label(rec.get("title"))) or tooltip or None,
            aria_label=aria if aria and aria.lower() not in DEGENERATE_LABELS else None,
            bbox=tuple(float(v) for v in bbox) if bbox and len(bbox) == 4 else None,  # type: ignore[arg-type]
            selector=str(rec.get("selector") or ""),
            visible=True,
            ordinal=ordinal,
            owner_hint=str(owner) if owner else None,
        )
        elements.append(
            Element(
                obs=obs,
                popup=popup,
                icon=icon,
                input_type=rec.get("inputType"),
                disabled=bool(rec.get("disabled")),
                tab=bool(rec.get("tab")),
                tab_active=bool(rec.get("tabActive")),
                target=rec.get("target"),
                editor=bool(rec.get("editor")),
                tooltip=tooltip,
                autocomplete=(rec.get("autocomplete") or None),
            )
        )
    top = len(popups) - 1
    return Perception(
        url=raw.get("url", ""),
        pattern=pattern,
        title=scrub_text(clean_label(raw.get("title"), limit=200)),
        elements=elements,
        popups=popups,
        active_tabs=[
            t
            for t in (
                scrub_text(clean_label(x.get("label")))
                for x in (raw.get("tabs") or [])
                if x.get("popup", -1) == top
            )
            if t
        ],
        body_text=str(raw.get("body") or "")[:MAX_BODY_TEXT],
        interactive_count=int(raw.get("interactive") or 0),
        truncated=int(raw.get("truncated") or 0),
        editors=int(raw.get("editors") or 0),
    )


def collect(page: Any, widget_selectors: list[str]) -> dict[str, Any]:
    """Run the page script once and return the raw page record."""
    return page.evaluate(
        PERCEIVE_JS, {"widgets": widget_selectors, "maxElements": MAX_ELEMENTS, "maxText": MAX_BODY_TEXT}
    )


def snapshot_dom(page: Any, widget_selectors: list[str]) -> str:
    """Masked DOM snapshot: no scripts, input values or widgets; secrets scrubbed, volatile text masked."""
    html = scrub_text(page.evaluate(SNAPSHOT_JS, {"widgets": widget_selectors}))
    return _TEXT_NODE.sub(lambda m: ">" + mask_volatile(m.group(1)) + "<", html)
