"""TwigAdapter on Sylius-shaped templates: tokenizer, references, hooks, routes, i18n and the index (D10, D15).

Two inputs: tiny trees written per test (one behaviour, one known answer) and `tests/fixtures/sylius`, a trimmed
copy of the real 2.2 shop templates, hook wiring, routing and catalogues with the real target config
(`targets/sylius.yaml`) so the file that ships is the file that is tested. One slow test indexes the real
checkout in `data/cache/repos` when it is there.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from blastradius.code.adapter import CodeAdapter, PartialIndexer, get_adapter
from blastradius.code.adapters.twig import (
    TwigAdapter,
    eval_list,
    parse_wiring,
    regions,
    route_templates,
    scan_template,
)
from blastradius.code.index import reverse_closure
from blastradius.models import CodeIndex, FileClass
from blastradius.targets import CodeConfig, TargetConfig

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "sylius"
SHA = "a" * 40
SHOP = "src/Sylius/Bundle/ShopBundle"
T = f"{SHOP}/templates"
C = f"{SHOP}/Resources/config"
UI = "src/Sylius/Bundle/UiBundle/templates"
BASE = f"{T}/shared/layout/base.html.twig"


def sylius_code(**update) -> CodeConfig:
    return TargetConfig.load("sylius").code.model_copy(update=update)


def build(tmp_path: Path, files: dict[str, str | bytes], **update) -> CodeIndex:
    """Index a tree made of `files` with the real Sylius code config (or `update`d fields of it)."""
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return TwigAdapter().index(tmp_path, sylius_code(**update), SHA, target="sylius", ref="2.2")


def edges(idx: CodeIndex, kind: str | None = None, src: str | None = None) -> set[tuple[str, str, str]]:
    return {(e.kind, e.src, e.dst) for e in idx.edges
            if (kind is None or e.kind == kind) and (src is None or e.src == src)}


def kinds(idx: CodeIndex) -> dict[str, FileClass]:
    return {f.path: f.kind for f in idx.files}


@pytest.fixture(scope="module")
def fixture_index() -> CodeIndex:
    return TwigAdapter().index(FIXTURE, sylius_code(), SHA, target="sylius", ref="2.2")


# ------------------------------------------------------------------------------------ file classes
def test_a_template_named_like_a_doc_file_is_still_a_template(tmp_path: Path):
    """`security.html.twig` is the header's login menu in Sylius; classify_file's NAME rule calls it DOCS."""
    idx = build(tmp_path, {
        f"{T}/shared/layout/base/header/content/security.html.twig": "<a>{{ 'sylius.ui.log_in'|trans }}</a>\n",
        f"{T}/shared/notice.html.twig": "<p>{{ 'sylius.ui.notice'|trans }}</p>\n",
        f"{T}/docs/index.html.twig": "<p>Docs</p>\n",
        f"{C}/routing/security.yml": "sylius_shop_login:\n    path: /login\n",
        f"{T}/shared/__tests__/fake.html.twig": "<p>test</p>\n",
    })
    got = kinds(idx)
    assert got[f"{T}/shared/layout/base/header/content/security.html.twig"] is FileClass.COMPONENT
    assert got[f"{T}/shared/notice.html.twig"] is FileClass.COMPONENT
    assert got[f"{T}/docs/index.html.twig"] is FileClass.COMPONENT
    assert got[f"{C}/routing/security.yml"] is FileClass.SOURCE_OTHER
    assert got[f"{T}/shared/__tests__/fake.html.twig"] is FileClass.TEST          # real exclusions still apply


# ---------------------------------------------------------------------------------------- tokenizer
def test_regions_split_text_print_tag_and_comment_and_keep_unclosed_openers_as_text():
    got = list(regions("a{{ b }}c{% d %}e{# f #}g{{ unclosed"))
    assert got == [("text", "a"), ("print", " b "), ("text", "c"), ("tag", " d "), ("text", "e"),
                   ("comment", " f "), ("text", "g{{"), ("text", " unclosed")]


def test_scan_reads_attributes_that_precede_a_twig_print_in_the_same_tag():
    """`alt` before `src="{{ asset() }}"` sits in a text region with no `>`: it must still be read."""
    facts = scan_template('<img alt="Product image" src="{{ asset(x) }}" title="{{ t }}">'
                          '<input placeholder="Search..." value="{{ q }}"><a href="{{ u }}" aria-label="Open">x</a>')
    assert facts.attr_texts == ["Product image", "Search...", "Open"]


def test_scan_keeps_each_text_node_and_the_merged_inline_run():
    """Two sibling buttons are two literals (a page element is named by one), `Hello <b>world</b>` is also whole."""
    facts = scan_template("<button>Add</button><button>Remove</button><h1>Hello <b>world</b></h1>")
    assert {"Add", "Remove", "Hello", "world", "Hello world"} <= set(facts.texts)


def test_a_template_named_in_an_expression_is_rendered_by_the_template_that_names_it(tmp_path: Path):
    """`pagerfanta(p, 'twig', {template: '...'})` hands the named template to a vendor function that renders it."""
    idx = build(tmp_path, {
        f"{T}/shared/macro/pagination.html.twig":
            "{% macro simple(paginator, options) %}\n"
            "{{ pagerfanta(paginator, 'twig', options|default({'template': '@SyliusShop/shared/macro/pagination/pagerfanta.html.twig'})) }}\n"
            "{% endmacro %}\n",
        f"{T}/shared/macro/pagination/pagerfanta.html.twig": "{%- extends '@BabDevPagerfanta/default.html.twig' -%}\n",
        f"{T}/shared/once.html.twig": "{% include '@SyliusShop/shared/macro/pagination/pagerfanta.html.twig' %}"
                                      "{% if t == '@SyliusShop/shared/macro/pagination/pagerfanta.html.twig' %}x{% endif %}\n",
    })
    pagerfanta = f"{T}/shared/macro/pagination/pagerfanta.html.twig"
    got = [e for e in idx.edges if e.dst == pagerfanta]
    assert {(e.kind, e.src) for e in got} == {("RENDERS", f"{T}/shared/macro/pagination.html.twig"),
                                              ("RENDERS", f"{T}/shared/once.html.twig")}
    stats = idx.stats                                  # the include and the mention, plus the vendor `extends`
    assert (stats["template_refs"], stats["template_refs_resolved"], stats["template_refs_external"]) == (3, 2, 1)


# ------------------------------------------------------------------------------- references and wiring
def test_scan_template_collects_structure_references_by_kind():
    facts = scan_template(
        "{% extends '@SyliusShop/a.html.twig' %}"
        "{% include '@SyliusShop/b.html.twig' with {x: 1} only %}"
        "{% include ['@SyliusShop/c.html.twig', '@SyliusShop/d.html.twig'] ignore missing %}"
        "{{ include('@SyliusShop/e.html.twig') }}"
        "{% embed '@SyliusUi/f.html.twig' with {a: 1} %}{% block b %}x{% endblock %}{% endembed %}"
        "{% import '@SyliusShop/m.html.twig' as m %}"
        "{% from '@SyliusShop/n.html.twig' import a, b as c %}{% from _self import z %}{% import _self as s %}"
        "{% form_theme form '@SyliusShop/ft.html.twig' 'plain.html.twig' %}"
        "{{ component('sylius_shop:main_image', {template: '@SyliusShop/g.html.twig'}) }}{{ component('a:b') }}")
    assert facts.extends == ["@SyliusShop/a.html.twig"]
    assert [n for n, _ in facts.renders] == [f"@SyliusShop/{c}.html.twig" for c in "bcde"]
    assert [(n, how, syms) for n, how, syms in facts.imports] == [
        ("@SyliusUi/f.html.twig", "embed", ()),                    # a base to override: IMPORTS, not RENDERS
        ("@SyliusShop/m.html.twig", "import", ()),
        ("@SyliusShop/n.html.twig", "from", ("a", "b")),
        ("@SyliusShop/ft.html.twig", "form_theme", ()),
        ("plain.html.twig", "form_theme", ())]
    assert facts.components == [("sylius_shop:main_image", ["@SyliusShop/g.html.twig"]), ("a:b", [])]


def test_scan_template_records_dynamic_names_as_holes_and_keeps_literal_fallbacks():
    facts = scan_template(
        "{% include '@SyliusAttribute/Types/' ~ t ~ '.html.twig' %}"
        "{% include [(x ~ '/y.html.twig'), '@SyliusUi/z.html.twig'] %}"
        "{% extends tpl %}{% include tpl %}{{ component(name) }}")
    assert [n for n, _ in facts.renders] == ["@SyliusUi/z.html.twig"]
    assert [kind for kind, _ in facts.dynamic] == ["include", "include", "extends", "include", "component"]


def test_scan_template_hook_calls_with_prefixes_lists_variables_and_whitespace_control():
    facts = scan_template("{% hook 'a' %}{%- hook ['b', 'c#d'] with { _prefixes: p, x } -%}"
                          "{% hook homepage_hook %}{% hook 'e' with { f } only %}{% hook '#meta' with { _prefixes: prefixes } %}")
    assert [(h.expr, h.prefixes_expr) for h in facts.hooks] == [
        ("'a'", None), ("['b', 'c#d']", "p"), ("homepage_hook", None), ("'e'", None), ("'#meta'", "prefixes")]


def test_scan_template_translation_keys_in_every_shape_the_templates_use():
    facts = scan_template("""
        {% import '@SyliusShop/shared/messages.html.twig' as messages %}
        {{ 'sylius.ui.a'|trans }} {{ "sylius.ui.b"|trans({'%n%': 1}) }}
        {{ (x ? 'sylius.ui.c' : 'sylius.ui.d')|trans }} {{ ('sylius.ui.' ~ type)|trans }}
        {{ message|trans }} {{ messages.info('sylius.ui.e') }} {{ other.info('sylius.ui.skipped') }}""")
    assert facts.keys == ["sylius.ui.a", "sylius.ui.b", "sylius.ui.c", "sylius.ui.d", "sylius.ui.e"]
    assert facts.key_prefixes == ["sylius.ui."] and facts.key_unresolved == 1


def test_eval_list_follows_literals_variables_default_and_merge_and_refuses_the_rest():
    env = {"homepage_hook": ["sylius_shop.homepage.index"], "x": ["q"]}
    assert eval_list("'a'", env) == ["a"]
    assert eval_list("['a', 'b']|merge(x)|default(['z'])", env) == ["a", "b", "q"]
    assert eval_list("prefixes|default([])|merge([homepage_hook])", env) == ["sylius_shop.homepage.index"]
    assert eval_list("a ~ b", env) is None and eval_list("f(x)", env) is None


def test_parse_wiring_reads_hookables_disabled_ones_component_only_and_configuration_keys():
    doc = {"sylius_twig_hooks": {"hooks": {
        "sylius_shop.base.header": {
            "logo": {"template": "@SyliusShop/logo.html.twig", "priority": 1},
            "gone": {"template": "@SyliusShop/gone.html.twig", "enabled": False},
            "cart": {"component": "sylius_shop:cart:widget", "props": {"template": "@SyliusShop/cart.html.twig"}},
            "bare": {"component": "sylius_shop:bare"},
            "menu": {"template": "@SyliusShop/menu.html.twig",
                     "configuration": {"title": "sylius.ui.menu_title", "n": 3, "plain": "word"}}}}}}
    wiring = parse_wiring(doc, "w.yaml")
    assert wiring is not None
    got = [(h.hook, h.name, h.template, h.component) for h in wiring.hookables]
    assert got == [("sylius_shop.base.header", "bare", None, "sylius_shop:bare"),
                   ("sylius_shop.base.header", "cart", "@SyliusShop/cart.html.twig", "sylius_shop:cart:widget"),
                   ("sylius_shop.base.header", "logo", "@SyliusShop/logo.html.twig", None),
                   ("sylius_shop.base.header", "menu", "@SyliusShop/menu.html.twig", None)]
    assert wiring.config_keys == ["sylius.ui.menu_title"]
    assert parse_wiring({"framework": {"secret": "x"}}, "w.yaml") is None and parse_wiring([1], "w.yaml") is None


def test_route_templates_reads_sylius_defaults_and_symfony_template_controller():
    assert route_templates({"defaults": {"_sylius": {"template": "@S/a.html.twig"}}}) == ["@S/a.html.twig"]
    assert route_templates({"defaults": {"template": "@S/b.html.twig"}}) == ["@S/b.html.twig"]
    assert route_templates({"defaults": {"_sylius": {"template": "not-a-template"}}, "path": "/"}) == []


# ------------------------------------------------------------------------------------ the fixture index
def test_get_adapter_returns_the_twig_adapter_behind_the_same_protocol_as_vue():
    adapter = get_adapter("twig")
    assert isinstance(adapter, TwigAdapter) and adapter.name == "twig"
    protocol: CodeAdapter = adapter                       # structural: index(repo_root, cfg, snapshot, *, target, ref)
    partial: PartialIndexer = adapter                     # the PR overlay's entry point
    assert protocol is partial and partial.exts == frozenset({".twig", ".yaml", ".yml"})


def test_index_stamps_target_ref_snapshot_and_adapter(fixture_index: CodeIndex):
    assert (fixture_index.target, fixture_index.ref, fixture_index.snapshot_id) == ("sylius", "2.2", SHA)
    assert fixture_index.adapter == "twig" and fixture_index.repo == "Sylius/Sylius"
    bare = TwigAdapter().index(FIXTURE, sylius_code(), SHA)                 # a bare call falls back to the config
    assert (bare.target, bare.ref) == ("Sylius/Sylius", "2.2")


def test_files_are_classified_by_role_with_the_git_blob_sha(fixture_index: CodeIndex):
    got = kinds(fixture_index)
    assert got[BASE] is FileClass.LAYOUT
    for page in ("homepage/index", "cart/index", "product/show", "contact/contact_request"):
        assert got[f"{T}/{page}.html.twig"] is FileClass.PAGE, page
    assert got[f"{T}/cart/index/content/form.html.twig"] is FileClass.COMPONENT
    assert got[f"{T}/shared/macro/money.html.twig"] is FileClass.UTIL
    assert got[f"{UI}/macro/rtl.html.twig"] is FileClass.UTIL
    assert got[f"{C}/app/twig_hooks/base.yaml"] is FileClass.COMPONENT               # hook wiring renders things
    assert got[f"{C}/routing/cart.yml"] is FileClass.SOURCE_OTHER
    assert got[f"{SHOP}/Resources/translations/messages.en.yml"] is FileClass.LOCALE
    assert got["@SyliusCore/Email/contactRequest.html.twig"] is FileClass.EXTERNAL
    one = next(f for f in fixture_index.files if f.path == f"{T}/homepage/banner.html.twig")
    git_sha = subprocess.run(["git", "hash-object", str(FIXTURE / one.path)], capture_output=True, text=True,
                             check=True).stdout.strip()
    assert one.sha == git_sha and one.package == "ShopBundle" and one.loc == 9


def test_render_edges_extends_includes_components_and_hooks(fixture_index: CodeIndex):
    idx = fixture_index
    renders = edges(idx, "RENDERS")
    wiring_base, wiring_cart = f"{C}/app/twig_hooks/base.yaml", f"{C}/app/twig_hooks/cart/index.yaml"
    assert ("RENDERS", f"{T}/cart/index.html.twig", BASE) in renders                      # extends
    assert ("RENDERS", f"{T}/shared/layout/base/footer/content/copy.html.twig", f"{T}/shared/logo.html.twig") in renders
    assert ("RENDERS", f"{T}/cart/index/content/form/sections/general/items/body/item.html.twig",
            f"{T}/shared/components/order_item/product_info.html.twig") in renders         # component('a:b:c')
    assert ("RENDERS", f"{T}/shared/components/order_item/product_info.html.twig",
            f"{T}/shared/components/main_image.html.twig") in renders
    # a page renders the wiring file of the hook it calls, and the wiring file renders what it wires
    assert ("RENDERS", f"{T}/cart/index.html.twig", wiring_cart) in renders
    assert ("RENDERS", wiring_cart, f"{T}/cart/index/content.html.twig") in renders
    assert ("RENDERS", wiring_base, f"{T}/shared/layout/base/footer/content/payment_methods.html.twig") in renders
    # a component hookable names its template in props
    assert ("RENDERS", wiring_cart, f"{T}/cart/index/content/form.html.twig") in renders
    via = {(e.src, e.dst): e.via for e in idx.edges if e.kind == "RENDERS"}
    assert via[(wiring_cart, f"{T}/cart/index/content/form.html.twig")] == "hookable:sylius_shop.cart.index.content/form"


def test_import_edges_resolve_namespaces_and_never_point_at_the_importer(fixture_index: CodeIndex):
    imports = edges(fixture_index, "IMPORTS")
    form = f"{T}/cart/index/content/form.html.twig"
    assert ("IMPORTS", form, f"{T}/shared/messages.html.twig") in imports                 # {% import %}
    assert ("IMPORTS", form, f"{T}/form/theme.html.twig") in imports                       # {% form_theme %}
    assert ("IMPORTS", BASE, f"{UI}/macro/rtl.html.twig") in imports                       # @SyliusUi/ namespace
    assert ("IMPORTS", f"{T}/email/contact_request.html.twig", "@SyliusCore/Email/contactRequest.html.twig") in imports
    assert ("IMPORTS", f"{T}/form/theme.html.twig", "bootstrap_5_layout.html.twig") in imports   # vendor, no alias
    assert not any(src == dst for _, src, dst in edges(fixture_index))                    # `import _self as messages`
    assert fixture_index.stats["imports_external"] == 2


def test_hook_names_resolve_relative_prefixed_variable_and_hash_variants(fixture_index: CodeIndex):
    wiring_cart = f"{C}/app/twig_hooks/cart/index.yaml"
    wiring_home = f"{C}/app/twig_hooks/homepage/index.yaml"
    wiring_show = f"{C}/app/twig_hooks/product/show.yaml"
    wiring_base = f"{C}/app/twig_hooks/base.yaml"
    via = {(e.src, e.dst): e.via for e in fixture_index.edges if e.dst.endswith((".yaml",))}
    # `{% hook 'index' with { _prefixes: prefixes } %}` + `{% set prefixes = ['sylius_shop.cart'] %}`
    assert via[(f"{T}/cart/index.html.twig", wiring_cart)] == "hook:sylius_shop.cart.index"
    # `{% hook homepage_hook %}` with `{% set homepage_hook = 'sylius_shop.homepage.index' %}`
    assert via[(f"{T}/homepage/index.html.twig", wiring_home)] == "hook:sylius_shop.homepage.index"
    # an absolute name
    assert via[(f"{T}/product/show.html.twig", wiring_show)] == "hook:sylius_shop.product.show"
    # relative to the hook this template is wired under: `content` under sylius_shop.cart.index
    assert via[(f"{T}/cart/index/content.html.twig", wiring_cart)] == "hook:sylius_shop.cart.index.content"
    # `general#left` is wired as `...sections.general#left`: the variant is kept in the name
    assert via[(f"{T}/cart/index/content/form/sections/general.html.twig", wiring_cart)] == \
        "hook:sylius_shop.cart.index.content.form.sections.general#left"
    # the layout's `footer` hook gets its prefix from the layout itself (`sylius_shop.base`)
    assert via[(BASE, wiring_base)] == "hook:sylius_shop.base.footer"


def test_a_page_prefix_reaches_hooks_the_layout_calls_with_it(tmp_path: Path):
    """`{% set prefixes = ['sylius_shop.cart'] %}` makes the layout's `footer` hook also `sylius_shop.cart.footer`."""
    files = {
        f"{T}/shared/layout/base.html.twig":
            "{% set prefixes = prefixes|default([])|merge(['sylius_shop.base']) %}{% hook 'footer' with { _prefixes: prefixes } %}\n",
        f"{T}/cart/index.html.twig": "{% extends '@SyliusShop/shared/layout/base.html.twig' %}"
                                     "{% set prefixes = ['sylius_shop.cart'] %}\n",
        f"{T}/cart/footer_note.html.twig": "<p>Cart note</p>\n",
        f"{T}/shared/footer.html.twig": "<p>Footer</p>\n",
        f"{C}/app/twig_hooks/cart.yaml": "sylius_twig_hooks:\n  hooks:\n    sylius_shop.cart.footer:\n      note:\n"
                                         "        template: '@SyliusShop/cart/footer_note.html.twig'\n",
        f"{C}/app/twig_hooks/base.yaml": "sylius_twig_hooks:\n  hooks:\n    sylius_shop.base.footer:\n      f:\n"
                                         "        template: '@SyliusShop/shared/footer.html.twig'\n",
    }
    idx = build(tmp_path, files)
    renders = edges(idx, "RENDERS")
    assert ("RENDERS", f"{T}/cart/index.html.twig", f"{C}/app/twig_hooks/cart.yaml") in renders
    assert ("RENDERS", f"{T}/shared/layout/base.html.twig", f"{C}/app/twig_hooks/base.yaml") in renders
    assert ("RENDERS", f"{T}/shared/layout/base.html.twig", f"{C}/app/twig_hooks/cart.yaml") not in renders


def test_hook_calls_nobody_defines_are_counted_as_extension_points_not_edges(fixture_index: CodeIndex):
    s = fixture_index.stats
    assert s["hook_calls"] == s["hook_calls_resolved"] + s["hook_calls_unresolved"] + s["hook_calls_dynamic"]
    unresolved = {(h["file"].rsplit("/", 1)[-1], h["hook"]) for h in s["unresolved_hooks_sample"]}
    assert ("general.html.twig", "'general#right'") in unresolved          # defined by a plugin, not in core
    assert ("base.html.twig", "'head'") in unresolved
    assert 0 < s["resolved_hook_rate"] < 1 and s["hookables_unresolved"] == 0
    assert (s["hook_calls"], s["hook_calls_resolved"]) == (25, 16)


def test_a_missing_template_is_an_unresolved_reference_with_a_reason(fixture_index: CodeIndex):
    s = fixture_index.stats
    assert s["unresolved_reasons"] == {"template: missing": 1}
    assert s["unresolved_samples"] == [{
        "file": f"{T}/cart/index/content/form/sections/general.html.twig",
        "name": "@SyliusShop/shared/macro/loader.html.twig", "reason": "missing"}]
    assert s["template_refs_unresolved"] == 1 and 0.9 < s["resolved_ref_rate"] < 1


# ------------------------------------------------------------------------------------ routes
def test_routes_follow_resource_mounts_prefixes_and_locale_placeholders(fixture_index: CodeIndex):
    got = {(r.pattern, r.file, r.layout) for r in fixture_index.routes}
    assert got == {
        ("/:_locale/", f"{T}/homepage/index.html.twig", BASE),
        ("/:_locale/cart/", f"{T}/cart/index.html.twig", BASE),
        ("/:_locale/cart/checkout", f"{T}/cart/index.html.twig", BASE),
        ("/:_locale/products/:slug", f"{T}/product/show.html.twig", BASE),
        ("/:_locale/contact/", f"{T}/contact/contact_request.html.twig", BASE),     # template found by route name
    }
    s = fixture_index.stats
    assert (s["route_definitions"], s["routes"], s["routes_template_by_name"]) == (7, 5, 1)
    assert s["routes_without_template"] == 1 and s["routes_template_missing"] == 1       # switch-locale; product index


def test_a_requirement_with_a_greedy_slug_adds_the_catch_all_pattern(tmp_path: Path):
    idx = build(tmp_path, {
        f"{C}/routing.yml": "sylius_shop_product:\n    resource: \"@SyliusShopBundle/Resources/config/routing/product.yml\"\n"
                            "    prefix: /taxons-x\n",
        f"{C}/routing/product.yml": "sylius_shop_product_index:\n    path: /taxons/{slug}\n    defaults:\n"
                                    "        _sylius:\n            template: \"@SyliusShop/product/index.html.twig\"\n"
                                    "    requirements:\n        slug: .+(?<!/)\n",
        f"{T}/product/index.html.twig": "<p>List</p>\n",
    })
    assert {r.pattern for r in idx.routes} == {"/taxons-x/taxons/:slug", "/taxons-x/taxons/:slug(.*)"}


# ------------------------------------------------------------------------------------ i18n
def test_catalogues_flatten_in_bundle_order_and_the_last_file_wins(fixture_index: CodeIndex):
    messages = fixture_index.i18n
    assert messages["sylius.ui.your_store"] == "your store (shop)"                # ShopBundle is listed after UiBundle
    assert messages["sylius.footer.payment_partners"] == "Payment Partners"
    assert messages["sylius.ui.contact_us"] == "Contact us" and len(messages) == 19
    assert list(messages) == sorted(messages)                                     # canonical order


def test_key_uses_follow_trans_filters_macro_arguments_and_prefixes(fixture_index: CodeIndex):
    uses = {(u.file, u.key, u.dynamic) for u in fixture_index.key_uses}
    assert (f"{T}/cart/index.html.twig", "sylius.ui.your_shopping_cart", False) in uses
    # the key is a literal argument of an imported macro: it is the caller's use
    assert (f"{T}/cart/index/content/form.html.twig", "sylius.ui.your_cart_is_empty", False) in uses
    # a key concatenated with a suffix inside another expression is still one literal
    assert (f"{T}/cart/index/content/form/sections/general/coupon.html.twig", "sylius.ui.enter_your_code", False) in uses
    assert (f"{T}/shared/layout/base/header/top_bar/info_message.html.twig", "sylius.top_bar", False) in uses
    s = fixture_index.stats
    assert s["keys_used_distinct"] == 19 and s["keys_used_not_in_locale"] == 0
    assert s["key_calls_unresolved"] == 6                                          # `{{ message|trans }}` in macros


def test_a_dynamic_key_prefix_is_recorded_as_a_dynamic_use(tmp_path: Path):
    idx = build(tmp_path, {f"{T}/x.html.twig": "{{ ('sylius.ui.state.' ~ s)|trans }}{{ 'sylius.ui.ok'|trans }}"})
    assert {(u.key, u.dynamic) for u in idx.key_uses} == {("sylius.ui.state.", True), ("sylius.ui.ok", False)}


def test_hook_configuration_keys_are_uses_of_the_wiring_file(tmp_path: Path):
    idx = build(tmp_path, {
        f"{T}/m.html.twig": "<p>Menu</p>\n",
        f"{C}/app/twig_hooks/menu.yaml": "sylius_twig_hooks:\n  hooks:\n    sylius_shop.base.menu:\n      m:\n"
                                         "        template: '@SyliusShop/m.html.twig'\n"
                                         "        configuration:\n          label: sylius.ui.menu_label\n"},
        locale_files=[])
    assert {(u.file, u.key) for u in idx.key_uses} == {(f"{C}/app/twig_hooks/menu.yaml", "sylius.ui.menu_label")}


def test_literals_are_hard_coded_visible_text_and_attributes_only(fixture_index: CodeIndex):
    texts = {(l.file.rsplit("/", 1)[-1], l.text) for l in fixture_index.literals}
    assert texts == {("copy.html.twig", "Sylius"), ("adyen.html.twig", "Adyen"), ("paypal.html.twig", "PayPal"),
                     ("logo.html.twig", "sylius logo")}
    assert fixture_index.stats["text_literals"] == 4


# ----------------------------------------------------------------------------------- hostile input
def test_a_tree_of_hostile_files_is_indexed_quickly_and_reports_what_it_could_not_read(tmp_path: Path):
    """A fork PR head is untrusted (D06): nothing in it may stall, crash or leak into the index."""
    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [" + ", ".join(["*" + chr(97 + i)] * 9) + "]\n" for i in range(8))
    files = {
        f"{T}/open_print.html.twig": "{{ " * 100_000,
        f"{T}/open_tags.html.twig": "<div " * 100_000 + "{% include " * 50_000,
        f"{T}/many_includes.html.twig": "{% include '@SyliusShop/ok.html.twig' %}" * 20_000,
        f"{T}/many_hooks.html.twig": "{% hook 'x' with { _prefixes: p } %}" * 20_000,
        f"{T}/trans.html.twig": "{{ ('a.b.' ~ x)|trans }}" * 20_000,
        f"{T}/ok.html.twig": "<p>{{ 'sylius.ui.fine'|trans }}</p>\n",
        f"{C}/app/twig_hooks/deep.yaml": "[" * 20_000 + "]" * 20_000,
        f"{C}/app/twig_hooks/bomb.yaml": bomb,
        f"{C}/app/twig_hooks/broken.yaml": "sylius_twig_hooks: [\n  hooks: : :\n",
        f"{SHOP}/Resources/translations/messages.en.yml": "a: [unclosed\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    (tmp_path / T / "latin1.html.twig").write_bytes(b"<p>caf\xe9 {{ 'sylius.ui.latin'|trans }}</p>")
    started = time.perf_counter()
    idx = TwigAdapter().index(tmp_path, sylius_code(), SHA, target="sylius", ref="2.2")
    assert time.perf_counter() - started < 20
    assert any(u.key == "sylius.ui.fine" for u in idx.key_uses)               # the good file was still read
    kinds_ = idx.stats["parse_error_kinds"]
    assert kinds_["yaml_parse_error"] >= 2 and kinds_["locale_parse_error"] == 1
    assert kinds_["undecodable_bytes"] == 1
    assert any(u.key == "sylius.ui.latin" for u in idx.key_uses)               # decoded with replacement, flagged
    assert idx.stats["parse_errors"] == sum(kinds_.values())


def test_symlinks_are_never_followed_and_are_reported(tmp_path: Path):
    secret = tmp_path / "secret.html.twig"
    secret.write_text("{{ 'leaked.key.here'|trans }}{% include '@SyliusShop/x.html.twig' %}\n")
    tree = tmp_path / "repo"
    (tree / T / "shared").mkdir(parents=True)
    (tree / T / "shared" / "ok.html.twig").write_text("<p>ok</p>\n")
    os.symlink(secret, tree / T / "shared" / "linked.html.twig")
    idx = TwigAdapter().index(tree, sylius_code(), SHA)
    assert f"{T}/shared/linked.html.twig" not in {f.path for f in idx.files}
    assert not any(u.key == "leaked.key.here" for u in idx.key_uses)
    assert idx.stats["symlinks_skipped"] == 1


def test_only_allow_listed_files_are_indexed(tmp_path: Path):
    idx = build(tmp_path, {
        f"{T}/in.html.twig": "<p>in</p>\n",
        f"{SHOP}/Resources/private/other.html.twig": "{{ 'not.a.root.key'|trans }}\n",       # outside every root
        f"{SHOP}/Resources/translations/messages.fr.yml": "sylius:\n  ui:\n    home: Accueil\n",
        "src/Sylius/Bundle/AdminBundle/templates/dashboard.html.twig": "{{ 'admin.only'|trans }}\n",
    })
    paths = {f.path for f in idx.files if f.kind is not FileClass.EXTERNAL}
    assert paths == {f"{T}/in.html.twig"}
    assert not any(u.key in ("not.a.root.key", "admin.only") for u in idx.key_uses)


# ------------------------------------------------------------------------------------ determinism
_SNIPPET = """
import hashlib, sys
from pathlib import Path
from blastradius.code.adapters.twig import TwigAdapter
from blastradius.targets import TargetConfig
cfg = TargetConfig.load("sylius").code
idx = TwigAdapter().index(Path("tests/fixtures/sylius"), cfg, "a" * 40, target="sylius", ref="2.2")
print(hashlib.sha256(idx.model_dump_json().encode()).hexdigest())
"""


def test_the_index_is_byte_identical_across_hash_seeds():
    digests = set()
    for seed in (0, 1, 2, 7):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(REPO / "src")}
        out = subprocess.run([sys.executable, "-c", _SNIPPET], cwd=REPO, env=env, capture_output=True, text=True,
                             timeout=120, check=False)
        assert out.returncode == 0, out.stderr[-600:]
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1


def test_an_alias_bomb_in_a_catalogue_is_cut_off_and_flagged_not_expanded(tmp_path: Path):
    """`yaml.safe_load` shares aliased nodes, so a 1 KB catalogue is 9^9 leaves to a naive flatten: a PR-head DoS."""
    lines = ["a: &a {k1: x, k2: x, k3: x, k4: x, k5: x, k6: x, k7: x, k8: x, k9: x}"]
    for i in range(1, 9):
        prev, cur = chr(96 + i), chr(97 + i)
        lines.append(f"{cur}: &{cur} {{" + ", ".join(f"k{j}: *{prev}" for j in range(1, 10)) + "}")
    lines.append("sylius:\n  ui:\n    fine: Still read")
    root = tmp_path / "bomb"
    catalogue = root / SHOP / "Resources/translations/messages.en.yml"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text("\n".join(lines) + "\n")
    (root / T).mkdir(parents=True)
    (root / T / "a.html.twig").write_text("<p>x</p>\n")
    script = (f"import sys; sys.path.insert(0, {str(REPO / 'src')!r})\n"
              "from pathlib import Path\n"
              "from blastradius.code.adapters.twig import TwigAdapter\n"
              "from blastradius.targets import TargetConfig\n"
              f"idx = TwigAdapter().index(Path({str(root)!r}), TargetConfig.load('sylius').code, 'a' * 40)\n"
              "import json; print(json.dumps({'n': len(idx.i18n), 'kinds': idx.stats['parse_error_kinds']}))\n")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False,
                         cwd=REPO)
    assert out.returncode == 0, out.stderr[-500:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert 0 < got["n"] <= 50_000 and got["kinds"] == {"locale_truncated": 1}


# ---------------------------------------------------------------------------------- the PR head overlay
PAYMENT = f"{T}/shared/layout/base/footer/content/payment_methods"
HEAD_CHANGES = {
    # wiring change: a new hookable (PR #19170 adds the Stripe logo to the footer)
    f"{C}/app/twig_hooks/base.yaml": (FIXTURE / C / "app/twig_hooks/base.yaml").read_text() + (
        "    sylius_shop.base.footer.content.payment_methods.providers.more:\n"
        "      stripe:\n"
        "        template: '@SyliusShop/shared/layout/base/footer/content/payment_methods/providers/stripe.html.twig'\n"
        "        priority: 300\n"),
    f"{PAYMENT}/providers/stripe.html.twig": '<div><img src="{{ asset(\'stripe.svg\') }}" alt="Stripe" /></div>\n',
    # a template whose hook is defined in a wiring file the PR does not touch
    f"{T}/cart/index/content.html.twig": '<div class="container">{{ \'sylius.ui.cart_title\'|trans }}{% hook \'content\' %}</div>\n',
    f"{SHOP}/Resources/translations/messages.en.yml": (
        (FIXTURE / SHOP / "Resources/translations/messages.en.yml").read_text() + "  more: Added copy\n"),
}


def _copy_tree(src: Path, dst: Path) -> None:
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _head_world(tmp_path: Path) -> tuple[CodeIndex, CodeIndex, Path, set[str]]:
    """(base index, fresh index of the head, a worktree with only changed + context files, changed paths)."""
    base = TwigAdapter().index(FIXTURE, sylius_code(), SHA, target="sylius", ref="2.2")
    full = tmp_path / "head"
    _copy_tree(FIXTURE, full)
    for rel, text in HEAD_CHANGES.items():
        (full / rel).parent.mkdir(parents=True, exist_ok=True)
        (full / rel).write_text(text)
    fresh = TwigAdapter().index(full, sylius_code(), SHA, target="sylius", ref="2.2")
    changed = set(HEAD_CHANGES)
    part_dir = tmp_path / "worktree"
    for rel in sorted(changed | set(TwigAdapter().context_paths(sylius_code(), base.files))):
        if (full / rel).is_file():
            (part_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (part_dir / rel).write_bytes((full / rel).read_bytes())
    return base, fresh, part_dir, changed


def test_context_paths_are_every_non_template_file_the_base_index_read_plus_the_catalogues():
    base = TwigAdapter().index(FIXTURE, sylius_code(), SHA, target="sylius", ref="2.2")
    got = TwigAdapter().context_paths(sylius_code(), base.files)
    assert f"{C}/app/twig_hooks/cart/index.yaml" in got and f"{C}/routing/cart.yml" in got
    assert f"{SHOP}/Resources/translations/messages.en.yml" in got and "config/routes/sylius_shop.yaml" in got
    assert not any(p.endswith(".twig") for p in got)
    assert set(sylius_code().locale_files) <= set(got) and got == sorted(got)    # even a catalogue absent from the base


def test_a_changed_template_resolves_hooks_against_wiring_the_pr_did_not_change(tmp_path: Path):
    base, _fresh, worktree, changed = _head_world(tmp_path)
    part = TwigAdapter().index_part(worktree, sylius_code(), known_paths={f.path for f in base.files if f.sha},
                                    only=changed)
    content = f"{T}/cart/index/content.html.twig"
    got = {(e.kind, e.src, e.dst, e.via) for e in part.edges if e.src == content}
    assert got == {("RENDERS", content, f"{C}/app/twig_hooks/cart/index.yaml", "hook:sylius_shop.cart.index.content")}


def test_overlay_part_plus_unchanged_base_edges_equals_a_fresh_index_of_the_head(tmp_path: Path):
    base, fresh, worktree, changed = _head_world(tmp_path)
    known = {f.path for f in base.files if f.sha}
    part = TwigAdapter().index_part(worktree, sylius_code(), known_paths=known | {f"{PAYMENT}/providers/stripe.html.twig"},
                                    only=changed)
    kept = [e for e in base.edges if e.src not in changed]
    assert sorted((e.kind, e.src, e.dst) for e in [*kept, *part.edges]) == \
        sorted((e.kind, e.src, e.dst) for e in fresh.edges)
    assert part.i18n == fresh.i18n and part.i18n["sylius.more"] == "Added copy" and "sylius.more" not in base.i18n
    assert {f.path for f in part.files if f.sha} == changed
    fresh_uses = {(u.file, u.key, u.dynamic) for u in fresh.key_uses if u.file in changed}
    assert {(u.file, u.key, u.dynamic) for u in part.key_uses} == fresh_uses
    assert {(l.file, l.text) for l in part.literals} == {(l.file, l.text) for l in fresh.literals if l.file in changed}
    assert part.routes == [r for r in fresh.routes if r.file in changed] == []      # no route's template changed


def test_without_context_the_part_keeps_the_old_contract(tmp_path: Path):
    """The overlay as it is today fetches only changed files: the catalogue is then not rebuilt (None)."""
    base, _fresh, worktree, changed = _head_world(tmp_path)
    bare = tmp_path / "bare"
    for rel in changed:
        (bare / rel).parent.mkdir(parents=True, exist_ok=True)
        (bare / rel).write_bytes((worktree / rel).read_bytes())
    part = TwigAdapter().index_part(bare, sylius_code(), known_paths={f.path for f in base.files if f.sha},
                                    only=changed)
    assert part.i18n is None


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class _OverlayWorld:
    """A local origin with the fixture on `main` and HEAD_CHANGES on `refs/pull/7/head`, plus a base index."""

    def __init__(self, tmp_path: Path):
        from blastradius.code.clone import ensure_repo

        origin = tmp_path / "origin"
        _copy_tree(FIXTURE, origin)
        _git(origin.parent, "init", "-q", "-b", "main", str(origin))
        _git(origin, "config", "uploadpack.allowFilter", "true")
        _git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "base")
        self.base_sha = _git(origin, "rev-parse", "HEAD")
        _git(origin, "checkout", "-q", "-b", "pr")
        for rel, text in HEAD_CHANGES.items():
            (origin / rel).parent.mkdir(parents=True, exist_ok=True)
            (origin / rel).write_text(text)
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "head")
        self.head_sha = _git(origin, "rev-parse", "HEAD")
        _git(origin, "update-ref", "refs/pull/7/head", self.head_sha)
        _git(origin, "checkout", "-q", "main")
        self.cfg = TargetConfig.load("sylius")
        self.cache, self.url = tmp_path / "cache", f"file://{origin}"
        self.keep = list(self.cfg.code.locale_files)
        baseline, _ = ensure_repo(self.cfg.code.repo, "main", self.cfg.code.roots, files=self.keep,
                                  cache_root=self.cache, remote=self.url, user_agent="t")
        self.base_index = TwigAdapter().index(baseline, self.cfg.code, self.base_sha, target="sylius", ref="main")
        self.tmp = tmp_path

    def overlay(self) -> CodeIndex:
        from blastradius.code.overlay import run_overlay
        from blastradius.config import Settings
        from blastradius.run import RunContext

        run = RunContext.create("sylius", run_id="ov", runs_dir=self.tmp / "runs", settings=Settings())
        return run_overlay(run, self.cfg, self.base_index, 7, "forker/Sylius", self.head_sha, sorted(HEAD_CHANGES),
                           cache_root=self.cache, remote=self.url)

    def fresh(self) -> CodeIndex:
        from blastradius.code.clone import ensure_repo

        work, sha = ensure_repo(self.cfg.code.repo, self.head_sha, self.cfg.code.roots, files=self.keep,
                                cache_root=self.cache, remote=self.url, user_agent="t", worktree="full-head")
        return TwigAdapter().index(work, self.cfg.code, sha, target="sylius", ref=self.head_sha)


def _same_as_fresh(out: CodeIndex, fresh: CodeIndex) -> None:
    assert out.stats["pr_overlay"] == "applied"
    for name in ("edges", "i18n", "key_uses", "literals"):
        assert getattr(out, name) == getattr(fresh, name), name
    assert sorted(out.routes, key=lambda r: (r.pattern, r.file)) == fresh.routes      # the overlay sorts by file
    assert [f.path for f in out.files] == [f.path for f in fresh.files]


def test_run_overlay_equals_a_fresh_index_when_the_context_files_are_fetched(tmp_path: Path, monkeypatch):
    """The end-to-end form of the part equivalence, through the real `run_overlay` and a local git origin.

    `overlay.fetch_files` is wrapped to ask for `context_paths` as well: that is the one change the overlay needs.
    """
    from blastradius.code import overlay

    world = _OverlayWorld(tmp_path)
    real_fetch = overlay.fetch_files
    context = TwigAdapter().context_paths(world.cfg.code, world.base_index.files)

    def with_context(repo, ref, sha, paths, **kw):
        return real_fetch(repo, ref, sha, sorted({*paths, *context}), **kw)

    monkeypatch.setattr(overlay, "fetch_files", with_context)
    _same_as_fresh(world.overlay(), world.fresh())


def test_run_overlay_fetches_the_adapters_context_paths_so_changed_templates_keep_their_hook_edges(tmp_path: Path):
    """Was an xfail: `run_overlay` must ask the adapter for `context_paths(...)` beside the changed files."""
    world = _OverlayWorld(tmp_path)
    _same_as_fresh(world.overlay(), world.fresh())


# ------------------------------------------------------------------------------------------- real tree
RECON_SHA = "28c4862bbc0264a25d35cd9837a42134b51a5812"      # Sylius 2.2 tip when this slice was measured


@pytest.fixture(scope="module")
def real() -> tuple[CodeIndex, float]:
    from blastradius.code.clone import CloneError, ensure_repo

    cfg = TargetConfig.load("sylius").code
    try:
        repo, sha = ensure_repo(cfg.repo, RECON_SHA, cfg.roots, files=cfg.locale_files)
    except (CloneError, FileNotFoundError) as exc:
        pytest.skip(f"Sylius/Sylius@{RECON_SHA[:10]} is not cached and could not be fetched: {exc}")
    started = time.monotonic()
    idx = TwigAdapter().index(repo, cfg, sha, target="sylius", ref="2.2")
    return idx, time.monotonic() - started


@pytest.mark.slow
def test_real_inventory_graph_and_resolution_rates(real):
    idx, seconds = real
    s = idx.stats
    assert seconds < 20 and s["parse_errors"] == 0 and not s["index_empty"]
    assert 600 <= s["twig_files"] <= 670 and 40 <= s["wiring_files"] <= 48       # RECON: 608 ShopBundle templates, 42-44 YAML
    assert s["files_by_class"]["layout"] == 1 and s["files_by_class"]["locale"] == 18
    assert s["resolved_ref_rate"] >= 0.99 and s["resolved_hook_rate"] >= 0.85 and s["hookables_unresolved"] <= 3
    assert 780 <= s["renders"] <= 900 and 130 <= s["imports"] <= 170
    assert 240 <= s["hook_calls"] <= 260 and 25 <= s["routes"] <= 35
    assert 1450 <= len(idx.i18n) <= 1650 and 170 <= s["keys_used_distinct"] <= 200
    assert set(s["unresolved_reasons"]) <= {"template: dynamic include", "template: dynamic extends",
                                            "component: template is in PHP", "hookable: component template is in PHP"}


@pytest.mark.slow
def test_real_routes_files_and_pull_request_chains(real):
    idx, _ = real
    shop = "src/Sylius/Bundle/ShopBundle"
    got = {r.pattern: r.file for r in idx.routes}
    assert got["/:_locale/"] == f"{shop}/templates/homepage/index.html.twig"
    assert got["/:_locale/cart/"] == f"{shop}/templates/cart/index.html.twig"
    assert got["/:_locale/products/:slug"] == f"{shop}/templates/product/show.html.twig"
    assert got["/:_locale/taxons/:slug(.*)"] == f"{shop}/templates/product/index.html.twig"
    kinds_ = kinds(idx)
    assert kinds_[f"{shop}/templates/shared/layout/base.html.twig"] is FileClass.LAYOUT
    assert kinds_[f"{shop}/templates/shared/layout/base/header/content/security.html.twig"] is FileClass.COMPONENT
    assert kinds_[f"{shop}/templates/product/index.html.twig"] is FileClass.PAGE
    # PR #19187 changes the offcanvas cart: two hops reach the wiring file and, through it, the global layout
    seed = f"{shop}/templates/shared/layout/base/offcanvas/cart.html.twig"
    reached = reverse_closure(idx.edges, [seed], 2)
    assert reached[f"{shop}/Resources/config/app/twig_hooks/base.yaml"] == 1
    assert reached[f"{shop}/templates/shared/layout/base.html.twig"] == 2
    # PR #19170 changes the footer's payment methods and the wiring that lists them
    assert reverse_closure(idx.edges, [f"{shop}/Resources/config/app/twig_hooks/base.yaml"], 1)[
        f"{shop}/templates/shared/layout/base.html.twig"] == 1
    # the pagerfanta view named in an option is rendered by the listing's pagination template
    assert any(e.src.endswith("shared/pagination/pagination.html.twig") and e.dst.endswith("pagination/bootstrap_5.html.twig")
               for e in idx.edges if e.kind == "RENDERS")
    assert idx.i18n["sylius.ui.add_to_cart"] == "Add to cart"
    assert any(u.key == "sylius.ui.add_to_cart" and u.file.endswith("add_to_cart/submit.html.twig")
               for u in idx.key_uses)


def test_the_catalogue_is_rebuilt_only_when_a_catalogue_changed(tmp_path: Path):
    """A head behind its base (case 41) lacks keys the base gained since: rebuilding on a template-only PR would
    drop them (measured on Sylius #19187: `sylius.footer.payment_partners`), so the base table stays."""
    base, _fresh, worktree, _changed = _head_world(tmp_path)
    only = {f"{T}/cart/index/content.html.twig"}
    part = TwigAdapter().index_part(worktree, sylius_code(), known_paths={f.path for f in base.files if f.sha},
                                    only=only)
    assert (worktree / SHOP / "Resources/translations/messages.en.yml").is_file()         # context is all there
    assert part.i18n is None and {f.path for f in part.files if f.sha} == only


def test_a_trans_block_is_a_key_use_when_it_holds_a_key_and_visible_text_when_it_holds_a_sentence():
    facts = scan_template("{% trans %}sylius.ui.from_block{% endtrans %}<p>{% trans with {'%n%': 1} from 'messages' %}"
                          "Plain sentence here{% endtrans %}</p>{{ 'sylius.ui.after'|trans }}")
    assert sorted(facts.keys) == ["sylius.ui.after", "sylius.ui.from_block"]
    assert "Plain sentence here" in facts.texts and "sylius.ui.from_block" not in facts.texts


# ================================================================================= close-out additions
def test_a_yaml_unicode_escape_for_half_a_pair_is_replaced_counted_and_the_index_serialises(tmp_path: Path):
    """PyYAML does not join `\\ud83d\\ude00`: both halves are lone surrogates and used to crash model_dump_json."""
    catalogue = f"{SHOP}/Resources/translations/messages.en.yml"
    idx = build(tmp_path, {
        catalogue: 'sylius:\n  ui:\n    half: "bad \\ud800 half"\n    pair: "emoji \\ud83d\\ude00"\n    ok: "caf\\u00e9"\n',
        f"{T}/page/index.html.twig": "<p>{{ 'sylius.ui.half'|trans }}</p>\n",
    })
    assert idx.i18n["sylius.ui.half"] == "bad � half"
    assert idx.i18n["sylius.ui.pair"] == "emoji ��"          # lossy, but representable
    assert idx.i18n["sylius.ui.ok"] == "café"
    assert idx.stats["yaml_surrogate_escapes_replaced"] == 3
    assert idx.stats["parse_error_kinds"] == {"yaml_lone_surrogate": 1}
    assert CodeIndex.model_validate_json(idx.model_dump_json()).i18n == idx.i18n


def _templates(n: int) -> dict[str, str]:
    return {f"{T}/page/p{i:03d}.html.twig": f"<p>page {i}</p>\n" for i in range(n)}


def test_twig_index_is_partial_only_for_hard_failures_or_many_parse_errors(tmp_path: Path):
    clean = build(tmp_path / "clean", _templates(120))
    assert clean.stats["parse_partial"] is False and clean.stats["parse_partial_reasons"] == []
    soft = build(tmp_path / "soft", {**_templates(120), f"{C}/routing/a.yml": "a: [unclosed\n",
                                     f"{C}/routing/b.yml": "b: [unclosed\n"})
    assert soft.stats["parse_errors"] == 2 and soft.stats["parse_partial"] is False
    many = build(tmp_path / "many", {**_templates(48), f"{C}/routing/a.yml": "a: [unclosed\n",
                                     f"{C}/routing/b.yml": "b: [unclosed\n"})
    assert many.stats["parse_partial"] is True and many.stats["parse_partial_reasons"] == ["parse_errors=2/50"]
    hard = build(tmp_path / "hard", {**_templates(300), f"{T}/page/bad.html.twig": b"<p>\xff\xfe</p>\n"})
    assert hard.stats["parse_errors"] == 1 and hard.stats["parse_partial"] is True
    assert hard.stats["parse_partial_reasons"] == ["undecodable_bytes=1"]
