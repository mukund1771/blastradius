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


# ------------------------------------------------------------------ wave-1 review: resolver precedence
def test_an_alias_whose_target_file_is_missing_falls_through_to_the_workspace_rule():
    cfg = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/o-app/src", "pkg/o-data/src"],
                     alias={"@o/app/": "pkg/o-app/lib/"}, hub_packages=["@o/data"])   # the alias target is wrong
    res = ModuleResolver(FILES, cfg)
    hit = res.resolve("@o/app/src/helpers/a", "pkg/o-data/src/index.ts")      # alias gives .../lib/src/helpers/a
    assert (hit.kind, hit.target, hit.via) == ("file", "pkg/o-app/src/helpers/a.ts", "workspace")
    ok = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/o-app/src"], alias={"@o/app/": "pkg/o-app/src/"})
    assert ModuleResolver(FILES, ok).resolve("@o/app/helpers/a", "pkg/o-app/src/index.ts").via == "alias"


def test_a_leading_src_after_an_alias_that_already_ends_in_src_is_stripped():
    cfg = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/o-app/src"], alias={"@x/": "pkg/o-app/src/"})
    hit = ModuleResolver(FILES, cfg).resolve("@x/src/helpers/a", "pkg/o-app/src/index.ts")
    assert (hit.kind, hit.target, hit.via) == ("file", "pkg/o-app/src/helpers/a.ts", "alias")


def test_a_shorter_alias_is_tried_when_the_longest_prefix_misses():
    cfg = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/o-app/src", "pkg/o-web/src"],
                     alias={"@a/": "pkg/o-app/src/", "@a/b/": "pkg/o-web/src/"})
    hit = ModuleResolver(FILES, cfg).resolve("@a/b/../helpers/a", "pkg/o-app/src/index.ts")
    assert hit.target == "pkg/o-app/src/helpers/a.ts"


def test_an_alias_to_a_codegen_output_is_still_flagged_after_the_fall_through():
    gen = ModuleResolver(FILES, RCFG).resolve("~/api/generated/graphql", "pkg/o-web/src/main.ts")
    assert gen.kind == "file" and gen.generated_missing and gen.target == "pkg/o-app/src/api/generated/graphql.ts"


# ------------------------------------------------------------------ wave-1 review: bounded scanners
def test_scan_t_calls_is_linear_on_hostile_input():
    import time

    hostile = {
        "nested calls": "t(" * 20000,
        "deep parens": "t(" + "(" * 20000,
        "long string": "t('" + "a" * 300000,
        "many commas": "t(" + "a," * 100000,
        "cross quotes": "t('" * 15000,
    }
    for name, text in hostile.items():
        started = time.perf_counter()
        scan_t_calls(text)
        assert time.perf_counter() - started < 1.0, name


def test_scan_t_calls_still_reads_every_call_in_a_large_file():
    text = "\n".join(f"const a{i} = t('group.key{i}', {{ n: {i} }})" for i in range(8000))
    scan = scan_t_calls(text)
    assert scan.static == [f"group.key{i}" for i in range(8000)] and scan.unresolved == 0


def test_an_overlong_first_argument_is_counted_as_unresolved_not_scanned_forever():
    scan = scan_t_calls("t('" + "a" * 5000 + "')\nt('ok.key')")
    assert scan.static == ["ok.key"] and scan.unresolved == 1


def test_define_page_layout_search_is_bounded():
    import time

    started = time.perf_counter()
    assert layout_name([], "definePage({ " * 8000) is None
    assert time.perf_counter() - started < 1.0
    deep = "x = 1\n" * 20000 + "definePage({ meta: { layout: 'empty' } })\n"
    assert layout_name([], deep) == "empty"


# ------------------------------------------------------------------ wave-1 review: template scan gaps
def test_a_pascal_case_component_is_the_same_dynamic_hole_as_the_lowercase_one():
    scan = scan_template('<component :is="Foo"/><Component :is="Bar"/><Component is="Baz"/>', is_native=NATIVE_TAGS)
    assert scan.tags == ["Baz"] and scan.dynamic_is == ["Foo", "Bar"]
    literal = scan_template("<Component :is=\"'Lit'\"/>", is_native=NATIVE_TAGS)
    assert literal.tags == ["Lit"] and literal.dynamic_is == []


def test_v_pre_content_is_not_scanned():
    tpl = '<div v-pre><NotAComponent :a="t(\'no.key\')"/>{{ raw }}</div><Real :b="t(\'yes.key\')"/>'
    scan = scan_template(tpl, is_native=NATIVE_TAGS)
    assert scan.tags == ["Real"]
    assert any("yes.key" in e for e in scan.expressions) and not any("no.key" in e for e in scan.expressions)
    nested = scan_template('<section v-pre><div><Inner/></div><Skipped/></section><After/>', is_native=NATIVE_TAGS)
    assert nested.tags == ["After"]


def test_two_template_blocks_are_merged_and_flagged():
    from blastradius.code.adapters.vue import _analyse_vue

    facts = _analyse_vue("<template><A/></template>\n<template><B/></template>\n")
    assert facts.tags == ["A", "B"] and "multiple_template_blocks" in facts.issues


def test_template_and_style_src_blocks_are_followed_or_flagged():
    from blastradius.code.adapters.vue import _analyse_vue

    facts = _analyse_vue('<template src="./t.html"></template>\n<style src="./s.scss"></style>\n')
    assert {b.specifier for b in facts.imports} == {"./t.html", "./s.scss"}
    assert facts.issues == ["template_src_not_scanned"]


def test_a_script_with_a_syntax_error_is_flagged():
    assert extract_imports("import a from './a'\nexport const = = = {{{{\n").has_error is True
    assert extract_imports("import a from './a'\nexport const b = 1\n").has_error is False
    from blastradius.code.adapters.vue import _analyse_script

    assert _analyse_script("export const = = =\n", ".ts").issues == ["script_parse_error"]


# 149 characters that sent tree-sitter-typescript into a parse that had not finished after 40 s (found by fuzzing).
HOSTILE_SCRIPT = ("export <![CDATA[b<script>-->\u00e9}}<style>\u00e9{\x00import #slot</layout: 'x'}}'export `}"
                  "</script></script>v-pre<!--{<!--@click=import <![CDATA[&amp; <Component")


def test_a_pathological_script_is_cancelled_by_a_work_budget_not_by_the_clock():
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    code = textwrap.dedent("""
        import sys, time
        from blastradius.code.adapters.vue import _analyse_script
        from blastradius.code.jsmodules import extract_imports
        text = sys.stdin.read()
        started = time.perf_counter()
        facts = extract_imports(text)
        print(facts.aborted, facts.has_error, len(facts.imports), _analyse_script(text, ".ts").issues,
              time.perf_counter() - started < 5)
    """)
    out = subprocess.run([sys.executable, "-c", code], input=HOSTILE_SCRIPT, capture_output=True, text=True,
                         timeout=60, check=False, env={**os.environ, "PYTHONPATH": str(src)})
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.split() == ["True", "True", "0", "['script_parse_aborted']", "True"]


def test_the_parser_is_usable_for_the_next_file_after_a_cancelled_parse():
    """A cancelled tree-sitter parse resumes on the next call unless the parser is reset; the next file must be
    parsed from its own first byte."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    code = textwrap.dedent("""
        import sys
        from blastradius.code.jsmodules import extract_imports
        hostile = sys.stdin.read()
        before = [b.specifier for b in extract_imports("import a from './before'").imports]
        assert extract_imports(hostile).aborted
        after = [b.specifier for b in extract_imports("import b from './after'\\nexport const x = 1\\n").imports]
        print(before, after)
    """)
    import os

    out = subprocess.run([sys.executable, "-c", code], input=HOSTILE_SCRIPT, capture_output=True, text=True,
                         timeout=60, check=False, env={**os.environ, "PYTHONPATH": str(src)})
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == "['./before'] ['./after']"


def test_a_large_legitimate_script_is_not_cancelled():
    body = "".join(f"import {{ a{i} }} from './m{i}'\nexport const v{i} = a{i} + {i}\n" for i in range(3000))
    facts = extract_imports(body)
    assert not facts.aborted and not facts.has_error and len(facts.imports) == 3000


def test_an_undecodable_file_is_flagged_and_still_scanned():
    from blastradius.code.adapters.vue import _decode

    text, issue = _decode(b"import z from './z'\n// \xff\xfe\n")
    assert issue == "undecodable_bytes" and "./z" in text
    assert _decode("héllo".encode()) == ("héllo", None)


def test_scanners_survive_random_fragments_quickly():
    """Seeded fuzz of every text scanner: no exception, and no input slower than a second."""
    import random
    import time

    from blastradius.code.adapters.vue_sfc import tokenize

    frags = ["<", ">", "</", "/>", "<template>", "</template>", "<script>", "</script>", "<style>", "<!--", "-->",
             "{{", "}}", '"', "'", "`", "=", " ", "\n", "<Foo", "<foo-bar", ":is=", "v-if=", "v-pre", "@click=",
             "t(", ")", "(", "import ", "from ", "export ", "{", "}", "a", "b", "<component", "<Component", "#slot",
             "<![CDATA[", "&amp;", "\x00", "é", "🙂", "definePage({ ", "layout: 'x'"]
    rng = random.Random(3)
    worst = 0.0
    for _ in range(1500):
        text = "".join(rng.choice(frags) for _ in range(rng.randint(1, 60)))
        started = time.perf_counter()
        for block in split_sfc(text):
            if block.tag == "template":
                scan_template(block.content, is_native=NATIVE_TAGS)
        list(tokenize(text))
        facts = extract_imports(text)
        scan_t_calls(without_comments(text, facts))
        layout_name(split_sfc(text), text)
        worst = max(worst, time.perf_counter() - started)
    assert worst < 1.0


def test_deeply_nested_route_blocks_do_not_crash_the_scan():
    deep = "[" * 100000
    assert layout_name(split_sfc(f'<route lang="yaml">{deep}</route>'), "") is None
    assert layout_name(split_sfc(f'<route lang="json">{deep}</route>'), "definePage({ meta: { layout: \'a\' } })") == "a"


def test_components_table_parse_is_linear_on_blank_lines():
    import time

    started = time.perf_counter()
    assert parse_components_dts("\n" * 200000 + "A: typeof import('./A.vue')['default']\n", "p/components.d.ts")[
        "A"].path == "p/A.vue"
    assert parse_components_dts((" " * 40 + "\n") * 20000, "p/components.d.ts") == {}
    assert time.perf_counter() - started < 1.0
