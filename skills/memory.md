# Memory System (sqlite3)

You have access to a SQLite database at `/app/data/memory.db` via the `sqlite3` CLI.
This database stores your memories in two tiers.

## Schema

### long_term — permanent memories

Columns: `id`, `category`, `subject`, `content`, `source`, `confidence`, `created_at`, `updated_at`

Categories: `preference`, `relationship`, `fact`, `routine`, `work`, `health`, `travel`

### short_term — temporary context

Columns: `id`, `content`, `context`, `expires_at`, `created_at`

## Storing memories

### Long-term (things that stay true)

```bash
sqlite3 /app/data/memory.db "INSERT INTO long_term (category, subject, content, source) VALUES ('preference', 'matteo', 'Allergic to shellfish', 'conversation');"
```

### Short-term (temporary context, default 24h expiry)

```bash
sqlite3 /app/data/memory.db "INSERT INTO short_term (content, context, expires_at) VALUES ('Working from home today', 'morning chat', datetime('now', '+24 hours'));"
```

### Update an existing memory

```bash
sqlite3 /app/data/memory.db "UPDATE long_term SET content = 'New value', updated_at = datetime('now') WHERE id = 42;"
```

## Querying memories

### Search by subject

```bash
sqlite3 -json /app/data/memory.db "SELECT * FROM long_term WHERE subject = 'matteo';"
```

### Search by category

```bash
sqlite3 -json /app/data/memory.db "SELECT * FROM long_term WHERE category = 'preference';"
```

### Full-text search

```bash
sqlite3 -json /app/data/memory.db "SELECT * FROM long_term WHERE content LIKE '%coffee%';"
```

### Get all active short-term facts

```bash
sqlite3 -json /app/data/memory.db "SELECT * FROM short_term WHERE expires_at > datetime('now');"
```

### Get all long-term memories (summary)

```bash
sqlite3 -json /app/data/memory.db "SELECT id, category, subject, content FROM long_term ORDER BY updated_at DESC;"
```

## Deleting memories

```bash
# Delete a specific long-term memory
sqlite3 /app/data/memory.db "DELETE FROM long_term WHERE id = 42;"

# Delete a short-term fact
sqlite3 /app/data/memory.db "DELETE FROM short_term WHERE id = 7;"
```

## Important notes

- Always use `-json` flag when you need to parse results programmatically.
- Use `LIKE` with `%` wildcards for fuzzy content search.
- For long-term memories, always set `category` and `subject` — these are used for filtering.
- Short-term facts are auto-cleaned every 8 hours; set `expires_at` appropriately.
- When you learn something new that contradicts an existing memory, UPDATE the old one rather than inserting a duplicate.
- Before inserting a long-term memory, check if a similar one already exists.
- Use `source = 'conversation'` for things the user told you, `source = 'inferred'` for things you deduced.
