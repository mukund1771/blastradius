"""File-class table: what kind of file a path is, decided before any graph lookup (D10; cases 29, 34, 35, 36, 40).

The first matching rule wins. The order matters: a test under `pages/` is a TEST, a generated `.d.ts` under
`components/` is GENERATED, and `tailwind.config.ts` is STYLE_GLOBAL although it is also configuration.
`classify_file` is pure (no filesystem access), so the PR stage can call it on paths that are not checked out.

Directory rules are read relative to the configured root a path lives under, never from the ancestors of the
root, and a code file inside a root keeps its role even when a component directory shares a name with a
special one (`components/docs/Viewer.vue` is a COMPONENT, `components/pages/Foo.vue` is not a PAGE). Names that are
configuration at a package root (`meta.ts`, `index.html`) are ordinary files inside a source root. PAGE and
LAYOUT mean directly under `<root>/pages` and `<root>/layouts`, the same test `VueAdapter.routes` applies.
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
# Default outputs of graphql-codegen / unplugin-* that are gitignored or committed-but-derived (case 35); the
# first two come from packages/hoppscotch-common/gql-codegen.yml; `cfg.generated_files` replaces them.
# Entries are repo-relative paths or path suffixes on a whole segment; imports of them stay resolvable.
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
# `license`, `Notice.txt` and `security.text` are prose whatever their case; with any other extension a doc name
# is only the literal upper-case convention (`LICENSE.dependencies`), never `Notice.vue` or `security.ts`.
_PROSE_EXTS = frozenset({"", ".txt", ".text", ".md"})
_TEMPLATE_EXTS = frozenset({".twig", ".html", ".htm", ".phtml", ".njk", ".hbs", ".ejs", ".erb", ".jinja", ".j2"})
_DOC_DIRS = frozenset({"docs", "doc"})

_LOCALE_DIRS = frozenset({"locales", "locale", "i18n", "translations", "lang"})
_LOCALE_EXTS = frozenset({".json", ".yaml", ".yml", ".po", ".pot", ".xlf", ".xliff", ".properties"})
_LOCALE_NAMES = frozenset({"languages.json", "locales.json"})

_STYLE_EXTS = frozenset({".css", ".scss", ".sass", ".less", ".styl", ".pcss"})
_STYLE_GLOBS = ("tailwind.config.*", "windi.config.*", "uno.config.*", "unocss.config.*")

# Configuration by file name. Only outside a source root: `meta.ts` or `index.html` below `src/` is source.
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
_BARE_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")     # `vue`, `lodash-es`: one segment, no extension


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
    root = root.strip("/").removeprefix("./").rstrip("/")
    return path == root or path.startswith(root + "/")


def _is_doc_name(name: str, ext: str, *, in_root: bool) -> bool:
    """Whether a file name is one of the conventional prose files (`LICENSE`, `NOTICE.txt`, `SECURITY`).

    Case is ignored only for extensionless and plain-text names. A source or template file keeps its role even
    when its stem spells a doc name (`Notice.vue`, `security.ts`, `security.html.twig`), and so does anything
    below a source root, whose extension is not a prose one.
    """
    if name in _DOC_NAMES:
        return True
    stem = name.split(".")[0]
    if ext in _PROSE_EXTS:
        return stem.upper() in _DOC_NAMES
    return (not in_root and ext not in _CODE_EXTS and ext not in _TEMPLATE_EXTS) and stem in _DOC_NAMES


def _is_codegen_output(norm: str, cfg: CodeConfig) -> bool:
    outputs = tuple(cfg.generated_files) or CODEGEN_OUTPUTS
    return any(norm == o or norm.endswith("/" + o) for o in outputs)


def classify_file(path: str, cfg: CodeConfig) -> FileClass:
    """Class of a repo-relative path, or of a bare package specifier (`vue`, `@scope/x`: EXTERNAL)."""
    norm = path.replace("\\", "/").removeprefix("./")
    if _SPECIFIER.match(norm):
        return FileClass.EXTERNAL
    p = PurePosixPath(norm)
    name, ext = p.name, p.suffix.lower()
    root = next((r for r in cfg.roots if _under(norm, r)), None)
    if root is None:        # nothing is known about the layout: every directory of the path counts
        rel_parts = p.parts[:-1]
    else:                   # only directories below the configured root say anything about the file
        rel_parts = PurePosixPath(norm[len(root.strip("/").removeprefix("./")):].lstrip("/")).parts[:-1]
    dirs = set(rel_parts)
    code = root is not None and ext in _CODE_EXTS      # a source file inside a source root keeps its role

    if name in LOCKFILES:
        return FileClass.LOCKFILE
    soft_test = bool(dirs & _SOFT_TEST_DIRS) and ext != ".vue" and "components" not in dirs
    if dirs & _TEST_DIRS or soft_test or _matches(name, _TEST_GLOBS):
        return FileClass.TEST
    if (norm == cfg.auto_import_dts or name in _GENERATED_NAMES or dirs & _GENERATED_DIRS
            or _matches(name, _GENERATED_GLOBS) or _is_codegen_output(norm, cfg)):
        return FileClass.GENERATED
    if dirs & _CI_DIRS or name in _CI_NAMES:
        return FileClass.CI
    doc_name = _is_doc_name(name, ext, in_root=root is not None)
    if ext in _DOC_EXTS or doc_name or (dirs & _DOC_DIRS and not code):
        return FileClass.DOCS
    if norm in cfg.locale_files or name in _LOCALE_NAMES or (dirs & _LOCALE_DIRS and ext in _LOCALE_EXTS):
        return FileClass.LOCALE
    if ext in _STYLE_EXTS or _matches(name, _STYLE_GLOBS):
        return FileClass.STYLE_GLOBAL
    if (name in _BUILD_NAMES and root is None) or _matches(name, _BUILD_GLOBS):
        return FileClass.BUILD_CONFIG

    if dirs & _MIGRATION_DIRS or ext in _SCHEMA_EXTS:
        return FileClass.DATA_SCHEMA
    if root is None:        # other packages' source, scripts: real code, but not under an indexed root
        bare = "/" not in norm and _BARE_PACKAGE.match(norm)
        return FileClass.EXTERNAL if bare else FileClass.SOURCE_OTHER
    if workspace_package(root) in cfg.hub_packages:
        return FileClass.DATA_SCHEMA
    if ext not in _CODE_EXTS:       # query documents, JSON data and the like: source, but no role by directory
        return FileClass.SOURCE_OTHER
    first = rel_parts[0] if rel_parts else ""
    if first == "pages" and ext == ".vue":
        return FileClass.PAGE
    if first == "layouts" and ext == ".vue":
        return FileClass.LAYOUT
    if dirs & {"services", "newstore", "store", "stores"}:
        return FileClass.SERVICE
    if first == "pages" or dirs & {"helpers", "utils", "composables"}:
        return FileClass.UTIL
    if "components" in dirs or ext == ".vue":
        return FileClass.COMPONENT
    return FileClass.SOURCE_OTHER
