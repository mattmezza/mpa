-- Memory database schema (data/memory.db)
-- Two-tier memory: long-term (permanent) + short-term (expiring)

CREATE TABLE IF NOT EXISTS long_term (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    confidence TEXT DEFAULT 'stated',
    created_at DATETIME DEFAULT (datetime('now')),
    updated_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS short_term (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    context TEXT,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_lt_category ON long_term(category);
CREATE INDEX IF NOT EXISTS idx_lt_subject ON long_term(subject);
CREATE INDEX IF NOT EXISTS idx_st_expires ON short_term(expires_at);
