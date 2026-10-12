"""File-class table: what kind of file a path is, decided before any graph lookup (D10; cases 29, 34, 35, 36, 40).

The first matching rule wins. The order matters: a test under `pages/` is a TEST, a generated `.d.ts` under
`components/` is GENERATED, and `tailwind.config.ts` is STYLE_GLOBAL although it is also configuration.
`classify_file` is pure (no filesystem access), so the PR stage can call it on paths that are not checked out.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

from ..models import FileClass
from ..targets import CodeConfig

LOCKFILES = frozenset({
    "pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lockb", "bun.lock", "npm-shrinkwrap.json",
    "composer.lock", "poetry.lock", "Pipfile.lock", "Gemfile.lock", "Cargo.lock", "symfony.lock", "devenv.lock",
})
# Outputs of graphql-codegen / unplugin-* that are gitignored or committed-but-derived (case 35). The first
# two come from packages/hoppscotch-common/gql-codegen.yml; imports of them stay resolvable as GENERATED.
CODEGEN_OUTPUTS = (
    "helpers/backend/graphql.ts", "helpers/backend/backend-schema.json", "api/generated/graphql.ts",
)
_GENERATED_NAMES = ("components.d.ts", "auto-imports.d.ts", "typed-router.d.ts", "routes.d.ts")
_GENERATED_DIRS = frozenset({"generated", "__generated__", "gql-gen", "dist"})
_GENERATED_GLOBS = ("*.generated.*", "*.gen.ts", "*.min.js", "*.min.css", "*.map", "*.tsbuildinfo")

_TEST_DIRS = frozenset({"__tests__", "__mocks__", "__fixtures__", "__snapshots__", "e2e", "cypress"})
# `components/http/test/*.vue` is the collection test-runner UI, so a plain `test(s)` directory only counts
# for non-component files.
_SOFT_TEST_DIRS = frozenset({"test", "tests"})
_TEST_GLOBS = ("*.spec.*", "*.test.*", "*.cy.*", "*.e2e.*", "setupTests.*", "vitest.d.ts", "vitest.setup.*")

_CI_DIRS = frozenset({".github", ".circleci", ".gitlab", ".buildkite", ".husky"})
_CI_NAMES = frozenset({".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", "codecov.yml", "renovate.json",
                       "dependabot.yml", "bitbucket-pipelines.yml", ".travis.yml"})

_DOC_EXTS = frozenset({".md", ".mdx", ".rst", ".adoc"})
_DOC_NAMES = frozenset({"LICENSE", "LICENCE", "CODEOWNERS", "NOTICE", "AUTHORS", "CODE_OF_CONDUCT", "SECURITY"})
_DOC_DIRS = frozenset({"docs", "doc"})

_LOCALE_DIRS = frozenset({"locales", "locale", "i18n", "translations", "lang"})
_LOCALE_EXTS = frozenset({".json", ".yaml", ".yml", ".po", ".pot", ".xlf", ".xliff", ".properties"})
_LOCALE_NAMES = frozenset({"languages.json", "locales.json"})

_STYLE_EXTS = frozenset({".css", ".scss", ".sass", ".less", ".styl", ".pcss"})
_STYLE_GLOBS = ("tailwind.config.*", "windi.config.*", "uno.config.*", "unocss.config.*")

_BUILD_NAMES = frozenset({
    "package.json", "pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json", "Makefile", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "netlify.toml", "vercel.json", "firebase.json", ".gitignore",
    ".gitattributes", ".npmrc", ".nvmrc", ".editorconfig", ".dockerignore", "jsconfig.json", "index.html",
    "meta.ts", "type-check.mjs", "healthcheck.sh", "gql-codegen.yml", "codegen.yml", "firestore.rules",
    "firestore.indexes.json", "Caddyfile", "devenv.nix", "devenv.yaml",
})
_BUILD_GLOBS = (
    "tsconfig*.json", "vite.config.*", "vitest.config.*", "postcss.config.*", "babel.config.*", "webpack.config.*",
    "rollup.config.*", "eslint.config.*", ".eslintrc*", ".prettierrc*", "commitlint.config.*", "Dockerfile.*",
    "*.Dockerfile", "docker-compose.*.yml", "*.Caddyfile", ".env*", "*.toml", "*.nix",
)
_MIGRATION_DIRS = frozenset({"migrations", "migration"})
_SCHEMA_EXTS = frozenset({".prisma", ".sql"})
_CODE_EXTS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue"})
_SPECIFIER = re.compile(r"^(@[\w.-]+/[\w./-]+|~[\w-]*(/.*)?|virtual:.*|node:.*|npm:.*)$")


def workspace_package(root: str) -> str | None:
    """npm name for a source root `<...>/<scope>-<name>/src` by the monorepo naming convention.

    `packages/hoppscotch-data/src` serves `@hoppscotch/data`; a directory without a dash serves itself. The
    convention stands in for reading each package.json, which is outside the read allow-list.
    """
    path = PurePosixPath(root.rstrip("/"))
    pkg_dir = path.parent.name if path.name in ("src", "lib") else path.name
    if not pkg_dir:
        return None
    scope, dash, rest = pkg_dir.partition("-")
    return f"@{scope}/{rest}" if dash else pkg_dir


def _matches(name: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def classify_file(path: str, cfg: CodeConfig) -> FileClass:
    """Class of a repo-relative path (or of a bare package specifier, which is EXTERNAL)."""
    norm = path.replace("\\", "/").removeprefix("./")
    if _SPECIFIER.match(norm):
        return FileClass.EXTERNAL
    p = PurePosixPath(norm)
    name, parts, ext = p.name, p.parts[:-1], p.suffix.lower()
    dirs = set(parts)

    if name in LOCKFILES:
        return FileClass.LOCKFILE
    if dirs & _TEST_DIRS or (dirs & _SOFT_TEST_DIRS and ext != ".vue") or _matches(name, _TEST_GLOBS):
        return FileClass.TEST
    if (norm == cfg.auto_import_dts or name in _GENERATED_NAMES or dirs & _GENERATED_DIRS
            or _matches(name, _GENERATED_GLOBS) or any(norm.endswith(o) for o in CODEGEN_OUTPUTS)):
        return FileClass.GENERATED
    if dirs & _CI_DIRS or name in _CI_NAMES:
        return FileClass.CI
    if ext in _DOC_EXTS or name in _DOC_NAMES or name.split(".")[0] in _DOC_NAMES or dirs & _DOC_DIRS:
        return FileClass.DOCS
    if norm in cfg.locale_files or name in _LOCALE_NAMES or (dirs & _LOCALE_DIRS and ext in _LOCALE_EXTS):
        return FileClass.LOCALE
    if ext in _STYLE_EXTS or _matches(name, _STYLE_GLOBS):
        return FileClass.STYLE_GLOBAL
    if name in _BUILD_NAMES or _matches(name, _BUILD_GLOBS):
        return FileClass.BUILD_CONFIG

    if dirs & _MIGRATION_DIRS or ext in _SCHEMA_EXTS:
        return FileClass.DATA_SCHEMA
    root = next((r for r in cfg.roots if _under(norm, r)), None)
    if root is None:                # other packages' source, scripts: real code, but not under an indexed root
        return FileClass.SOURCE_OTHER
    if workspace_package(root) in cfg.hub_packages:
        return FileClass.DATA_SCHEMA
    if ext not in _CODE_EXTS:       # query documents, JSON data and the like: source, but no role by directory
        return FileClass.SOURCE_OTHER
    rel_dirs = set(PurePosixPath(norm[len(root):].lstrip("/")).parts[:-1])
    if "pages" in rel_dirs and ext == ".vue":
        return FileClass.PAGE
    if "layouts" in rel_dirs and ext == ".vue":
        return FileClass.LAYOUT
    if rel_dirs & {"services", "newstore", "store", "stores"}:
        return FileClass.SERVICE
    if rel_dirs & {"helpers", "utils", "composables", "pages"}:
        return FileClass.UTIL
    if "components" in rel_dirs or ext == ".vue":
        return FileClass.COMPONENT
    return FileClass.SOURCE_OTHER
