"""Vue/TS scanning units: SFC split, template scan, imports, resolver, tag resolution, i18n, routes."""

from __future__ import annotations

import pytest

from blastradius.code.adapters.vue_components import (
    NATIVE_TAGS,
    ComponentResolver,
    namespaced_name,
    parse_components_dts,
    pascal_case,
)
from blastradius.code.adapters.vue_routes import layout_name, route_pattern
from blastradius.code.adapters.vue_sfc import scan_template, split_sfc
from blastradius.code.i18n import flatten_messages, scan_t_calls
from blastradius.code.jsmodules import ModuleResolver, extract_imports, package_name, without_comments
from blastradius.targets import CodeConfig


# ------------------------------------------------------------------------------------------- SFC split
def test_split_sfc_handles_multiple_scripts_nested_templates_and_custom_blocks():
    src = """<template>
  <div :class="{ a: b > c }" @click="(n) => n > 1">
    <template v-if="ok"><template #inner><span>x</span></template></template>
    {{ a < b ? 'x' : 'y' }}
  </div>
</template>

<script lang="ts">
export default { name: "X" } // </template> inside a script is just text to the template scanner
</script>
<script setup lang="ts">
const a = 1
</script>
<route lang="yaml">
meta:
  layout: empty
</route>
<style scoped lang="scss">
.a { color: red; }
</style>
"""
    blocks = split_sfc(src)
    assert [b.tag for b in blocks] == ["template", "script", "script", "route", "style"]
    assert blocks[0].content.count("<template") == 2 and "{{ a < b" in blocks[0].content
    assert blocks[0].content.rstrip().endswith("</div>")
    assert blocks[1].lang == "ts" and blocks[2].attrs == {"setup": None, "lang": "ts"}
    assert "layout: empty" in blocks[3].content
    assert blocks[4].attrs["scoped"] is None


def test_split_sfc_skips_comments_and_tolerates_unterminated_blocks():
    blocks = split_sfc("<!-- <script>bad</script> -->\n<template><p>hi</p></template>\n<script>let x")
    assert [b.tag for b in blocks] == ["template", "script"]
    assert blocks[1].content == "let x"


# --------------------------------------------------------------------------------- template scanning
def test_scan_template_collects_tags_holes_text_and_expressions():
    tpl = """
    <div>
      <!-- <Commented /> -->
      <AppHeader />
      <app-footer></app-footer>
      <HoppSmartInput :placeholder="t('a.b')" title="Pick one" />
      <component :is="'AppHeader'" />
      <component :is="Dynamic" />
      <component is="StaticName" />
      <svg><linearGradient id="g" /><path d="M0 0" /></svg>
      <p>Hello {{ t("x.y") }} world</p>
      <kbd>K</kbd>
      <slot name="foo" />
      <template v-if="x"><Nested /></template>
    </div>
    """
    scan = scan_template(tpl, is_native=NATIVE_TAGS)
    assert scan.tags == ["AppHeader", "app-footer", "HoppSmartInput", "StaticName", "Nested"]
    assert scan.dynamic_is == ["Dynamic"]
    assert "Hello" in scan.texts and "world" in scan.texts
    assert not any(t == "K" for t in scan.texts)             # one letter is a key hint, not visible words
    assert scan.attr_texts == ["Pick one"]
    assert any("t('a.b')" in e for e in scan.expressions) and any('t("x.y")' in e for e in scan.expressions)


def test_scan_template_is_case_exact_for_native_tags():
    scan = scan_template("<Button /><button /><Label /><input />", is_native=NATIVE_TAGS)
    assert scan.tags == ["Button", "Label"]


# ------------------------------------------------------------------------------------ script imports
def test_extract_imports_all_binding_shapes():
    src = '''
import Foo, { a, b as c, type D } from "./foo"
import * as ns from '~/ns'
import type { T } from "@x/y"
import "./side-effect"
import json from "./data.json" with { type: "json" }
export { x, y as z } from "./x"
export * from "./all"
export * as nm from "./nm"
export const k = 1
// import("./commented")
const s = "import('./instring')"
const Lazy = defineAsyncComponent(() => import("./Lazy.vue"))
const route = () => import("./Route.vue")
const dyn = (n) => import(`./plugins/${n}.ts`)
'''
    facts = extract_imports(src)
    by_spec = {(b.specifier, b.kind): b for b in facts.imports}
    assert by_spec[("./foo", "import")].symbols == ("default", "a", "b", "D")
    assert dict(by_spec[("./foo", "import")].locals) == {"Foo": "default", "a": "a", "c": "b", "D": "D"}
    assert by_spec[("~/ns", "import")].symbols == ("*",)
    assert by_spec[("@x/y", "import")].type_only
    assert by_spec[("./side-effect", "import")].symbols == ()
    assert by_spec[("./x", "reexport")].symbols == ("x", "y")
    assert by_spec[("./all", "reexport")].symbols == ("*",)
    assert by_spec[("./nm", "reexport")].symbols == ("*",)
    assert by_spec[("./Lazy.vue", "dynamic")].symbols == ()
    assert facts.lazy_bindings == {"Lazy": "./Lazy.vue", "route": "./Route.vue"}
    assert facts.dynamic_nonliteral == 1
    assert "./commented" not in {b.specifier for b in facts.imports}
    assert "./instring" not in {b.specifier for b in facts.imports}


def test_barrel_detection_and_comment_blanking():
    barrel = extract_imports('export * from "./a"\nexport { b } from "./b"\n')
    assert barrel.reexports == 2 and not barrel.declares_locally
    mixed = extract_imports('export * from "./a"\nexport const v = 1\n')
    assert mixed.declares_locally
    src = '// t("gone")\nconst x = t("kept") /* t("also.gone") */\n'
    assert "gone" not in without_comments(src, extract_imports(src)).replace("also.gone", "")
    assert scan_t_calls(without_comments(src, extract_imports(src))).static == ["kept"]


# ----------------------------------------------------------------------------------------- resolver
FILES = [
    "pkg/o-app/src/index.ts", "pkg/o-app/src/composables/i18n.ts", "pkg/o-app/src/components/Foo.vue",
    "pkg/o-app/src/components/dir/index.ts", "pkg/o-app/src/helpers/a.ts", "pkg/o-app/src/helpers/b.d.ts",
    "pkg/o-data/src/index.ts", "pkg/o-data/src/rest/v1.ts", "pkg/o-web/src/main.ts",
]
RCFG = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/o-app/src", "pkg/o-data/src", "pkg/o-web/src"],
                  alias={"~/": "pkg/o-app/src/", "@web/": "pkg/o-web/src/"}, hub_packages=["@o/data"])


@pytest.mark.parametrize(("spec", "importer", "kind", "target"), [
    ("./a", "pkg/o-app/src/helpers/c.ts", "file", "pkg/o-app/src/helpers/a.ts"),
    ("./a.js", "pkg/o-app/src/helpers/c.ts", "file", "pkg/o-app/src/helpers/a.ts"),         # .js names a .ts file
    ("./b", "pkg/o-app/src/helpers/c.ts", "file", "pkg/o-app/src/helpers/b.d.ts"),
    ("../components/dir", "pkg/o-app/src/helpers/c.ts", "file", "pkg/o-app/src/components/dir/index.ts"),
    ("~/components/Foo.vue", "pkg/o-web/src/main.ts", "file", "pkg/o-app/src/components/Foo.vue"),
    ("@web/main", "pkg/o-app/src/index.ts", "file", "pkg/o-web/src/main.ts"),
    ("@o/data", "pkg/o-app/src/index.ts", "file", "pkg/o-data/src/index.ts"),               # workspace package
    ("@o/data/rest/v1", "pkg/o-app/src/index.ts", "file", "pkg/o-data/src/rest/v1.ts"),
    ("@o/app/src/helpers/a", "pkg/o-web/src/main.ts", "file", "pkg/o-app/src/helpers/a.ts"),
    ("@composables/i18n", "pkg/o-web/src/main.ts", "file", "pkg/o-app/src/composables/i18n.ts"),   # inferred @dir alias
    ("vue", "pkg/o-app/src/index.ts", "external", "vue"),
    ("@hoppscotch/ui", "pkg/o-app/src/index.ts", "external", "@hoppscotch/ui"),
    ("lodash-es/debounce", "pkg/o-app/src/index.ts", "external", "lodash-es"),
    ("~icons/lucide/x", "pkg/o-app/src/index.ts", "external", "~icons"),
    ("@workers/regex?worker", "pkg/o-app/src/index.ts", "external", "@workers/regex"),
    ("./missing", "pkg/o-app/src/helpers/c.ts", "unresolved", "./missing"),
    ("../../../package.json", "pkg/o-app/src/helpers/c.ts", "unresolved", "../../../package.json"),
])
def test_module_resolver(spec: str, importer: str, kind: str, target: str):
    r = ModuleResolver(FILES, RCFG).resolve(spec, importer)
    assert (r.kind, r.target) == (kind, target)


def test_resolver_reports_why_an_import_is_unresolved_and_flags_missing_codegen():
    res = ModuleResolver(FILES, RCFG)
    assert "outside" in res.resolve("../../../package.json", "pkg/o-app/src/helpers/c.ts").reason
    assert "no such file" in res.resolve("./nope", "pkg/o-app/src/helpers/c.ts").reason
    gen = res.resolve("~/helpers/backend/graphql", "pkg/o-web/src/main.ts")
    assert gen.kind == "file" and gen.generated_missing and gen.target == "pkg/o-app/src/helpers/backend/graphql.ts"


def test_package_name():
    assert package_name("@scope/pkg/sub/path") == "@scope/pkg"
    assert package_name("fp-ts/function") == "fp-ts"
    assert package_name("~icons/lucide/x") == "~icons"
    assert package_name("node:fs") == "node:fs"


# -------------------------------------------------------------------------------- tag resolution
DTS = """
declare module 'vue' {
  export interface GlobalComponents {
    AccessTokens: typeof import('./components/accessTokens/index.vue')['default']
    AccessTokensGenerateModal: typeof import('./components/accessTokens/GenerateModal.vue')['default']
    HttpTestResult: typeof import('./components/http/TestResult.vue')['default']
    HttpTestTestResult: typeof import('./components/http/test/TestResult.vue')['default']
    HoppSmartInput: typeof import('@hoppscotch/ui')['HoppSmartInput']
    Tippy: typeof import('vue-tippy')['Tippy']
  }
}
"""


def test_parse_components_dts_and_namespace_rule():
    table = parse_components_dts(DTS, "pkg/o-app/src/components.d.ts")
    assert table["AccessTokens"].path == "pkg/o-app/src/components/accessTokens/index.vue"
    assert table["HoppSmartInput"].path is None and table["HoppSmartInput"].module == "@hoppscotch/ui"
    assert table["Tippy"].export == "Tippy"
    # the fallback rule must reproduce every internal row of the committed table
    for name, entry in table.items():
        if entry.path:
            assert namespaced_name(entry.path.removeprefix("pkg/o-app/src/components/")) == name
    assert pascal_case("app-header") == "AppHeader" and pascal_case("appHeader") == "AppHeader"


def test_component_resolution_order():
    files = ["pkg/o-app/src/components/shared/Item.vue", "pkg/o-app/src/components/shared/Dup.vue",
             "pkg/o-app/src/components/shared/dup/index.vue", "pkg/o-app/src/components/http/TestResult.vue"]
    table = parse_components_dts(DTS, "pkg/o-app/src/components.d.ts")
    comp = ComponentResolver(files, table, ["pkg/o-app/src/components"])
    bind = {"Item": ("file", "pkg/other/Item.vue", "import")}
    imp = "pkg/o-app/src/pages/index.vue"
    assert comp.resolve("item", importer=imp, bindings=bind).target == "pkg/other/Item.vue"       # import wins
    assert comp.resolve("shared-item", importer=imp, bindings={}).how == "namespace"
    assert comp.resolve("HttpTestResult", importer=imp, bindings={}).how == "dts"
    assert comp.resolve("hopp-smart-input", importer=imp, bindings={}).kind == "external"
    assert comp.resolve("RouterView", importer=imp, bindings={}).kind == "external"
    assert comp.resolve("Transition", importer=imp, bindings={}).kind == "builtin"
    assert comp.resolve("Missing", importer=imp, bindings={}).kind == "unresolved"
    assert comp.resolve("SharedDup", importer=imp, bindings={}).kind == "unresolved"            # two files, one name
    assert comp.resolve("Item", importer="pkg/o-app/src/components/shared/Item.vue", bindings={}).how == "self"


# --------------------------------------------------------------------------------------------- i18n
def test_scan_t_calls_static_dynamic_and_noise():
    text = """
    t("a.b") + t('c.d') + $t("e.f") + this.t("g.h") + i18n.t("i.j")
    t(`k.${x}.l`) + t(`m.n`) + t(cond ? 'o.p' : "q.r") + t(someVar) + t(fn("z"))
    split("x") + format(2) + obj.t("not.a.key") + function t(key) {}
    t("with.args", { n: 1 })
    """
    scan = scan_t_calls(text)
    assert scan.static == ["a.b", "c.d", "e.f", "g.h", "i.j", "m.n", "o.p", "q.r", "with.args"]
    assert scan.dynamic_prefixes == ["k."]
    assert scan.unresolved == 2


def test_flatten_messages():
    flat, skipped = flatten_messages({"a": {"b": "B", "c": {"d": "D"}}, "e": "E", "n": 3, "l": ["x"]})
    assert flat == {"a.b": "B", "a.c.d": "D", "e": "E"} and skipped == 2


# ----------------------------------------------------------------------------------------- routes
@pytest.mark.parametrize(("rel", "pattern"), [
    ("index.vue", "/"),
    ("graphql.vue", "/graphql"),
    ("realtime/websocket.vue", "/realtime/websocket"),
    ("profile/index.vue", "/profile"),
    ("e/_id.vue", "/e/:id"),
    ("view/_id/_version.vue", "/view/:id/:version"),
    ("[param].vue", "/:param"),
    ("_.vue", "/:catchAll"),
    ("docs/_.vue", "/docs/:catchAll"),
])
def test_route_pattern(rel: str, pattern: str):
    assert route_pattern(rel) == pattern


def test_layout_from_route_block_or_define_page():
    yaml_block = split_sfc("<template/>\n<route lang=\"yaml\">\nmeta:\n  layout: empty\n</route>")
    assert layout_name(yaml_block, "") == "empty"
    json_block = split_sfc('<template/>\n<route lang="json">{"meta": {"layout": "bare"}}</route>')
    assert layout_name(json_block, "") == "bare"
    assert layout_name([], 'definePage({ meta: { layout: "narrow" } })') == "narrow"
    assert layout_name(split_sfc("<template/>\n<route lang=\"yaml\">: : bad</route>"), "") is None
