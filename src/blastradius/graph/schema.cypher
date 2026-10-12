// blastradius graph schema (D03, D08; plan section 6; cases 46-49).
//
// Applied by GraphStore.ensure_schema(). Every statement is IF NOT EXISTS, so applying it twice is a no-op.
// Statements are separated by ';'. Only `//` line comments are allowed and no statement may contain ';'.
//
// Every node carries `target` (the app under analysis), so two apps never collide. Neo4j Community has no
// NODE KEY constraint; composite uniqueness is available and is what the MERGE keys below rely on.
//
//   Requirement  (target, id)                    id = hash(quote)
//   Screen       (target, id)                    id = hash(fingerprint)
//   UIElement    (target, id)                    canonical: (role, name, ordinal), see graph/canonical.py
//   Flow         (target, id)
//   CodeFile     (target, path)                  repo-relative posix path, or a package name for externals
//   I18nKey      (target, key)
//   Route        (target, pattern)
//   PullRequest  (target, number)                written by the reason stage
//   Gap          (target, id, bundle_hash)       case 48: a gap is never reused across bundles
//   Run          (target, id)

CREATE CONSTRAINT requirement_key IF NOT EXISTS FOR (n:Requirement) REQUIRE (n.target, n.id) IS UNIQUE;
CREATE CONSTRAINT screen_key IF NOT EXISTS FOR (n:Screen) REQUIRE (n.target, n.id) IS UNIQUE;
CREATE CONSTRAINT ui_element_key IF NOT EXISTS FOR (n:UIElement) REQUIRE (n.target, n.id) IS UNIQUE;
CREATE CONSTRAINT flow_key IF NOT EXISTS FOR (n:Flow) REQUIRE (n.target, n.id) IS UNIQUE;
CREATE CONSTRAINT code_file_key IF NOT EXISTS FOR (n:CodeFile) REQUIRE (n.target, n.path) IS UNIQUE;
CREATE CONSTRAINT i18n_key_key IF NOT EXISTS FOR (n:I18nKey) REQUIRE (n.target, n.key) IS UNIQUE;
CREATE CONSTRAINT route_key IF NOT EXISTS FOR (n:Route) REQUIRE (n.target, n.pattern) IS UNIQUE;
CREATE CONSTRAINT pull_request_key IF NOT EXISTS FOR (n:PullRequest) REQUIRE (n.target, n.number) IS UNIQUE;
CREATE CONSTRAINT gap_key IF NOT EXISTS FOR (n:Gap) REQUIRE (n.target, n.id, n.bundle_hash) IS UNIQUE;
CREATE CONSTRAINT run_key IF NOT EXISTS FOR (n:Run) REQUIRE (n.target, n.id) IS UNIQUE;

// Lookup indexes for the read templates in queries.py.
CREATE INDEX code_file_kind IF NOT EXISTS FOR (n:CodeFile) ON (n.target, n.kind);
CREATE INDEX ui_element_owner IF NOT EXISTS FOR (n:UIElement) ON (n.target, n.owner_file);
CREATE INDEX screen_url_pattern IF NOT EXISTS FOR (n:Screen) ON (n.target, n.url_pattern);
CREATE INDEX gap_kind IF NOT EXISTS FOR (n:Gap) ON (n.target, n.kind);
