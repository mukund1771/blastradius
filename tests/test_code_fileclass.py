"""File-class table (cases 29, 34, 35, 36, 40): classification happens before any graph lookup."""

from __future__ import annotations

import pytest

from blastradius.code.fileclass import classify_file, workspace_package
from blastradius.models import FileClass
from blastradius.targets import TargetConfig

CFG = TargetConfig.load("hoppscotch").code
C = "packages/hoppscotch-common"


@pytest.mark.parametrize(("path", "expected"), [
    # source roles by directory
    (f"{C}/src/pages/index.vue", FileClass.PAGE),
    (f"{C}/src/pages/view/_id/_version.vue", FileClass.PAGE),
    (f"{C}/src/layouts/default.vue", FileClass.LAYOUT),
    (f"{C}/src/components/http/Request.vue", FileClass.COMPONENT),
    (f"{C}/src/App.vue", FileClass.COMPONENT),
    (f"{C}/src/services/environment.service.ts", FileClass.SERVICE),
    (f"{C}/src/newstore/environments.ts", FileClass.SERVICE),
    (f"{C}/src/helpers/utils/uri.ts", FileClass.UTIL),
    (f"{C}/src/composables/i18n.ts", FileClass.UTIL),
    (f"{C}/src/pages/enter-redirect.ts", FileClass.UTIL),
    (f"{C}/src/platform/index.ts", FileClass.SOURCE_OTHER),
    (f"{C}/src/helpers/backend/gql/queries/Me.graphql", FileClass.SOURCE_OTHER),
    # the collection-runner UI lives in components/http/test: a directory called "test" is not a test
    (f"{C}/src/components/http/test/Runner.vue", FileClass.COMPONENT),
    # tests, including specs under pages/ and components/
    (f"{C}/src/services/__tests__/environment.spec.ts", FileClass.TEST),
    (f"{C}/src/pages/__tests__/enter-redirect.spec.ts", FileClass.TEST),
    (f"{C}/src/helpers/__tests__/postwomanTesting.sample", FileClass.TEST),
    (f"{C}/src/setupTests.ts", FileClass.TEST),
    # generated and derived (case 35): never evidence
    (f"{C}/src/components.d.ts", FileClass.GENERATED),
    (f"{C}/src/helpers/backend/graphql.ts", FileClass.GENERATED),
    (f"{C}/src/helpers/backend/backend-schema.json", FileClass.GENERATED),
    ("packages/hoppscotch-selfhost-web/src/api/generated/graphql.ts", FileClass.GENERATED),
    # persisted-data shape (D29): the hub package's source and migrations
    ("packages/hoppscotch-data/src/collection/v/13.ts", FileClass.DATA_SCHEMA),
    ("packages/hoppscotch-data/src/rest/index.ts", FileClass.DATA_SCHEMA),
    ("packages/hoppscotch-backend/prisma/migrations/2024_init/migration.sql", FileClass.DATA_SCHEMA),
    # locales (the configured file and its siblings)
    (f"{C}/locales/en.json", FileClass.LOCALE),
    (f"{C}/locales/fr.json", FileClass.LOCALE),
    # global styles (case 36)
    ("tailwind.config.ts", FileClass.STYLE_GLOBAL),
    ("packages/hoppscotch-selfhost-web/tailwind.config.ts", FileClass.STYLE_GLOBAL),
    (f"{C}/assets/scss/styles.scss", FileClass.STYLE_GLOBAL),
    # the rest of case 40
    ("pnpm-lock.yaml", FileClass.LOCKFILE),
    ("packages/hoppscotch-relay/Cargo.lock", FileClass.LOCKFILE),
    ("README.md", FileClass.DOCS),
    ("docs/guide/setup.txt", FileClass.DOCS),
    ("LICENSE", FileClass.DOCS),
    (".github/workflows/ci.yml", FileClass.CI),
    ("package.json", FileClass.BUILD_CONFIG),
    ("packages/hoppscotch-selfhost-web/vite.config.ts", FileClass.BUILD_CONFIG),
    ("packages/hoppscotch-common/tsconfig.json", FileClass.BUILD_CONFIG),
    ("prod.Dockerfile", FileClass.BUILD_CONFIG),
    # outside every indexed root: real code, no role
    ("packages/hoppscotch-backend/src/user/user.service.ts", FileClass.SOURCE_OTHER),
    # external package specifiers (case 34)
    ("@hoppscotch/ui", FileClass.EXTERNAL),
    ("@hoppscotch/kernel/index", FileClass.EXTERNAL),
    ("~icons", FileClass.EXTERNAL),
    ("node:fs", FileClass.EXTERNAL),
])
def test_classification_table(path: str, expected: FileClass):
    assert classify_file(path, CFG) is expected


def test_leading_dot_slash_and_backslashes_are_normalised():
    assert classify_file(f"./{C}/src/pages/index.vue", CFG) is FileClass.PAGE
    assert classify_file(f"{C}\\src\\pages\\index.vue".replace("/", "\\"), CFG) is FileClass.PAGE


def test_a_test_beats_every_other_rule():
    assert classify_file(f"{C}/src/layouts/__tests__/default.spec.ts", CFG) is FileClass.TEST
    assert classify_file("packages/hoppscotch-data/src/rest/__tests__/v1.spec.ts", CFG) is FileClass.TEST


def test_workspace_package_naming_convention():
    assert workspace_package("packages/hoppscotch-data/src") == "@hoppscotch/data"
    assert workspace_package("packages/hoppscotch-selfhost-web/src/") == "@hoppscotch/selfhost-web"
    assert workspace_package("packages/shared/src") == "shared"


# ------------------------------------------------------------------ wave-1 review: directory rules
@pytest.mark.parametrize(("path", "expected"), [
    # an ordinary component directory that shares a name with a special directory is still a component
    (f"{C}/src/components/docs/Viewer.vue", FileClass.COMPONENT),
    (f"{C}/src/components/pages/Foo.vue", FileClass.COMPONENT),
    (f"{C}/src/components/layouts/Foo.vue", FileClass.COMPONENT),
    (f"{C}/src/components/meta.ts", FileClass.COMPONENT),
    (f"{C}/src/components/app/index.html", FileClass.SOURCE_OTHER),
    (f"{C}/src/components/http/test/runner.ts", FileClass.COMPONENT),
    (f"{C}/src/components/lang/Picker.vue", FileClass.COMPONENT),
    (f"{C}/src/components/i18n/Switch.vue", FileClass.COMPONENT),
    # the same file names at the package root (outside the source roots) are configuration
    ("packages/hoppscotch-selfhost-web/meta.ts", FileClass.BUILD_CONFIG),
    ("packages/hoppscotch-selfhost-web/index.html", FileClass.BUILD_CONFIG),
    # PAGE and LAYOUT only directly under <root>/pages and <root>/layouts, like the router
    (f"{C}/src/pages/docs/guide.vue", FileClass.PAGE),
    (f"{C}/src/layouts/sub/Thing.vue", FileClass.LAYOUT),
    # a plain test directory outside components is still a test directory
    (f"{C}/src/test/helper.ts", FileClass.TEST),
    # bare package names are external, scoped or not; repo files without an extension keep their own rules
    ("vue", FileClass.EXTERNAL),
    ("lodash-es", FileClass.EXTERNAL),
    ("fp-ts", FileClass.EXTERNAL),
    ("Makefile", FileClass.BUILD_CONFIG),
    ("LICENSE", FileClass.DOCS),
    ("Procfile", FileClass.SOURCE_OTHER),
])
def test_directory_rules_are_relative_to_the_configured_root(path: str, expected: FileClass):
    assert classify_file(path, CFG) is expected


def test_ancestor_directories_of_a_root_do_not_classify_files_under_it():
    from blastradius.targets import CodeConfig

    cfg = CodeConfig(repo="o/r", adapter="vue", roots=["docs/site/src", "tests/e2e/app/src"])
    assert classify_file("docs/site/src/pages/index.vue", cfg) is FileClass.PAGE
    assert classify_file("docs/site/src/components/Nav.vue", cfg) is FileClass.COMPONENT
    assert classify_file("tests/e2e/app/src/components/A.vue", cfg) is FileClass.COMPONENT
    assert classify_file("docs/site/README.md", cfg) is FileClass.DOCS


def test_classify_file_is_importable_from_the_adapter_module():
    from blastradius.code import adapter

    assert adapter.classify_file is classify_file


def test_generated_files_come_from_the_config_and_fall_back_to_the_constants():
    from blastradius.targets import CodeConfig

    plain = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/a/src"])
    assert classify_file("pkg/a/src/helpers/backend/graphql.ts", plain) is FileClass.GENERATED   # constants
    custom = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/a/src"], generated_files=["pkg/a/src/gen/out.ts"])
    assert classify_file("pkg/a/src/gen/out.ts", custom) is FileClass.GENERATED
    assert classify_file("pkg/a/src/helpers/backend/graphql.ts", custom) is not FileClass.GENERATED
    suffix = CodeConfig(repo="o/r", adapter="vue", roots=["pkg/a/src"], generated_files=["gen/out.ts"])
    assert classify_file("pkg/a/src/gen/out.ts", suffix) is FileClass.GENERATED
    assert classify_file("pkg/a/src/xgen/out.ts", suffix) is not FileClass.GENERATED         # whole segments only


def test_the_hoppscotch_target_lists_its_codegen_outputs():
    assert f"{C}/src/helpers/backend/graphql.ts" in CFG.generated_files
    assert f"{C}/src/components" in CFG.components_dirs


# ------------------------------------------------------------------ close-out: doc names are not source names
SYLIUS = TargetConfig.load("sylius").code
SHOP_T = "src/Sylius/Bundle/ShopBundle/templates"


@pytest.mark.parametrize(("path", "expected"), [
    # a source file keeps its role when its stem spells a doc name, inside a configured source root
    (f"{C}/src/components/Notice.vue", FileClass.COMPONENT),
    (f"{C}/src/components/app/Security.vue", FileClass.COMPONENT),
    (f"{C}/src/components/NOTICE.vue", FileClass.COMPONENT),             # even in the upper-case spelling
    (f"{C}/src/pages/security.vue", FileClass.PAGE),
    (f"{C}/src/layouts/Notice.vue", FileClass.LAYOUT),
    (f"{C}/src/helpers/security.ts", FileClass.UTIL),
    (f"{C}/src/helpers/utils/license.ts", FileClass.UTIL),
    (f"{C}/src/composables/authors.ts", FileClass.UTIL),
    (f"{C}/src/services/security.service.ts", FileClass.SERVICE),
    (f"{C}/src/platform/security.ts", FileClass.SOURCE_OTHER),
    (f"{C}/src/helpers/backend/gql/queries/Security.graphql", FileClass.SOURCE_OTHER),
    # the same names are not prose below a source root either, unless they are plain text
    ("packages/other/src/security.ts", FileClass.SOURCE_OTHER),
    ("packages/other/src/NOTICE.vue", FileClass.SOURCE_OTHER),
])
def test_a_source_file_named_like_a_doc_file_keeps_its_role(path: str, expected: FileClass):
    assert classify_file(path, CFG) is expected


@pytest.mark.parametrize("path", [
    f"{SHOP_T}/shared/layout/base/header/content/security.html.twig",
    f"{SHOP_T}/shared/notice.html.twig",
    f"{SHOP_T}/account/NOTICE.html.twig",
    "src/Sylius/Bundle/ShopBundle/Resources/config/routing/security.yml",
    "config/packages/security.yaml",
    "config/packages/Security.yaml",
])
def test_templates_and_config_named_like_doc_files_are_not_docs(path: str):
    assert classify_file(path, SYLIUS) is not FileClass.DOCS


@pytest.mark.parametrize("path", [
    "LICENSE", "LICENCE", "NOTICE", "AUTHORS", "CODEOWNERS", "CODE_OF_CONDUCT", "SECURITY",
    "license", "License", "notice", "authors",                        # extensionless: case is ignored
    "LICENSE.txt", "license.txt", "Notice.txt", "authors.text", "security.text", "SECURITY.md", "License.md",
    "LICENSE.dependencies", "NOTICE.third-party",                      # the upper-case convention, any suffix
    "packages/hoppscotch-common/LICENSE", "packages/hoppscotch-common/src/license.txt",
    f"{C}/src/components/NOTICE", f"{C}/src/NOTICE.txt",              # prose inside a source root is still prose
    "docs/security.txt",
])
def test_prose_doc_names_are_still_docs(path: str):
    assert classify_file(path, CFG) is FileClass.DOCS
