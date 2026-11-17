-- ScholarForge project database, schema version 1.
-- Applied by scholarforge.storage.migrate inside a single transaction.

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Append-only journal: provenance of every state change.
CREATE TABLE events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    entity    TEXT,
    entity_id TEXT,
    data      TEXT
);
CREATE INDEX idx_events_entity ON events(entity, entity_id);

-- Book tree: volume > part > chapter > section > block.
CREATE TABLE nodes (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT REFERENCES nodes(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('volume','part','chapter','section','block')),
    position        INTEGER NOT NULL,
    title           TEXT NOT NULL,
    slug            TEXT NOT NULL,
    summary         TEXT,
    plan            TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'planned',
    content_path    TEXT,
    content_hash    TEXT,
    version         INTEGER NOT NULL DEFAULT 0,
    word_count      INTEGER NOT NULL DEFAULT 0,
    revision_rounds INTEGER NOT NULL DEFAULT 0,
    written_summary TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_nodes_parent ON nodes(parent_id, position);
CREATE INDEX idx_nodes_kind ON nodes(kind, status);

CREATE TABLE block_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id      TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    path         TEXT NOT NULL,
    origin       TEXT NOT NULL,
    note         TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (node_id, version)
);

-- Work queue shared by the host AI (actor='host'), the local engine ('engine') and the user ('user').
CREATE TABLE tasks (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT NOT NULL UNIQUE,
    kind             TEXT NOT NULL,
    actor            TEXT NOT NULL CHECK (actor IN ('host','engine','user')),
    status           TEXT NOT NULL CHECK (status IN ('pending','leased','done','failed','cancelled','blocked')),
    title            TEXT NOT NULL DEFAULT '',
    priority         INTEGER NOT NULL DEFAULT 100,
    target_id        TEXT,
    payload          TEXT NOT NULL DEFAULT '{}',
    depends_on       TEXT NOT NULL DEFAULT '[]',
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    lease_token      TEXT,
    lease_expires_at TEXT,
    result           TEXT,
    feedback         TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    completed_at     TEXT
);
CREATE INDEX idx_tasks_status ON tasks(status, actor, priority, seq);
CREATE INDEX idx_tasks_target ON tasks(target_id, kind);

CREATE TABLE sources (
    id           TEXT PRIMARY KEY,
    citekey      TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL DEFAULT 'article',
    title        TEXT NOT NULL,
    authors      TEXT NOT NULL DEFAULT '[]',
    year         INTEGER,
    venue        TEXT,
    volume       TEXT,
    issue        TEXT,
    pages        TEXT,
    publisher    TEXT,
    doi          TEXT,
    arxiv_id     TEXT,
    isbn         TEXT,
    url          TEXT,
    openalex_id  TEXT,
    status       TEXT NOT NULL DEFAULT 'proposed',
    verification TEXT NOT NULL DEFAULT '{}',
    abstract     TEXT,
    relevance    TEXT,
    proposed_by  TEXT NOT NULL DEFAULT 'host',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX idx_sources_doi ON sources(doi);
CREATE INDEX idx_sources_arxiv ON sources(arxiv_id);

CREATE TABLE claims (
    id            TEXT PRIMARY KEY,
    node_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    label         TEXT,
    kind          TEXT NOT NULL,
    text          TEXT NOT NULL,
    citations     TEXT NOT NULL DEFAULT '[]',
    check_ids     TEXT NOT NULL DEFAULT '[]',
    speculative   INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'unverified',
    status_reason TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_claims_node ON claims(node_id);

-- Machine-checkable assertions submitted with a block (SymPy, numeric, dimensional, Lean).
CREATE TABLE assertions (
    id           TEXT PRIMARY KEY,
    node_id      TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    local_id     TEXT NOT NULL,
    spec         TEXT NOT NULL,
    last_outcome TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (node_id, local_id)
);

-- Result of every verification step, with method and independence flag (provenance).
CREATE TABLE checks (
    id             TEXT PRIMARY KEY,
    subject_type   TEXT NOT NULL,
    subject_id     TEXT NOT NULL,
    check_type     TEXT NOT NULL,
    method         TEXT NOT NULL,
    independent    INTEGER NOT NULL,
    outcome        TEXT NOT NULL,
    details        TEXT NOT NULL DEFAULT '{}',
    engine_version TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_checks_subject ON checks(subject_type, subject_id);

CREATE TABLE terms (
    id         TEXT PRIMARY KEY,
    term       TEXT NOT NULL,
    normalized TEXT NOT NULL UNIQUE,
    definition TEXT NOT NULL,
    defined_in TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    aliases    TEXT NOT NULL DEFAULT '[]',
    version    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE term_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id    TEXT NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    definition TEXT NOT NULL,
    changed_in TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE term_usage (
    term_id TEXT NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    PRIMARY KEY (term_id, node_id)
);

CREATE TABLE notation (
    id         TEXT PRIMARY KEY,
    symbol     TEXT NOT NULL,
    normalized TEXT NOT NULL,
    meaning    TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'global',
    defined_in TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_notation_norm ON notation(normalized);

-- Cross-reference targets (thm-, eq-, fig-, sec-...) and their uses.
CREATE TABLE labels (
    label   TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    node_id TEXT REFERENCES nodes(id) ON DELETE CASCADE,
    title   TEXT
);
CREATE TABLE refs (
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    label   TEXT NOT NULL,
    PRIMARY KEY (node_id, label)
);

CREATE TABLE issues (
    id          TEXT PRIMARY KEY,
    node_id     TEXT REFERENCES nodes(id) ON DELETE CASCADE,
    severity    TEXT NOT NULL,
    category    TEXT NOT NULL,
    code        TEXT NOT NULL,
    message     TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'open',
    origin      TEXT NOT NULL DEFAULT 'engine',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_issues_node ON issues(node_id, status);

CREATE TABLE figures (
    id         TEXT PRIMARY KEY,
    node_id    TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    label      TEXT NOT NULL UNIQUE,
    mode       TEXT NOT NULL,
    renderer   TEXT NOT NULL,
    spec       TEXT NOT NULL DEFAULT '{}',
    caption    TEXT NOT NULL,
    alt_text   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'requested',
    outputs    TEXT NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL DEFAULT '{}',
    license    TEXT,
    qc         TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE builds (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    formats     TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL,
    outputs     TEXT NOT NULL DEFAULT '{}',
    report      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE checkpoints (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    reason     TEXT NOT NULL,
    auto       INTEGER NOT NULL DEFAULT 1,
    path       TEXT NOT NULL,
    manifest   TEXT NOT NULL DEFAULT '{}'
);
