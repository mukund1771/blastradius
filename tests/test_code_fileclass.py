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
