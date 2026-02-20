# wacli -- WhatsApp CLI

You have access to `wacli`, a CLI tool for interacting with WhatsApp. Use it for
**all** read operations: listing messages, searching conversations, looking up
contacts, browsing chats, and inspecting groups. All read commands are
pre-approved and run without user confirmation.

wacli keeps a local SQLite database synced from WhatsApp. Read commands query
this local database and are fast. You can and should use wacli freely for any
non-write WhatsApp interaction.

## Critical rules

1. **Always sync before checking for new messages.** Before reading messages,
   checking if someone replied, or looking for recent conversations, run a sync
   first to pull the latest data from WhatsApp:

   ```bash
   wacli --json sync --once --idle-exit 5s
   ```

   This connects to WhatsApp, pulls new messages, and exits after 5 seconds of
   idle. It typically completes in under 10 seconds. **Do this every time** the
   user asks about new or recent messages.

2. **Do NOT use `run_command` with `wacli send` to send WhatsApp messages.**
   Use the `send_message` tool instead (channel="whatsapp"). It handles delivery
   through the proper channel.

3. **NEVER read wacli's internal SQLite database directly.** Do not use
   `sqlite3` to query `~/.wacli/wacli.db` or `~/.wacli/session.db` or any
   database inside the wacli store directory. Always use the `wacli` CLI
   commands instead — they provide the correct interface to the data with proper
   formatting and field names. The internal database schema is an implementation
   detail and may change without notice.

## Reading messages

### List recent messages

```bash
# Last 20 messages across all chats
wacli --json messages list --limit 20

# Messages in a specific chat
wacli --json messages list --chat 41772909259@s.whatsapp.net --limit 30

# Messages after a date
wacli --json messages list --after 2026-02-18 --limit 50

# Messages in a date range
wacli --json messages list --after 2026-02-01 --before 2026-02-15
```

### Search messages (full-text)

```bash
# Search across all chats
wacli --json messages search "meeting tomorrow" --limit 20

# Search in a specific chat
wacli --json messages search "invoice" --chat 41772909259@s.whatsapp.net

# Search from a specific sender
wacli --json messages search "hello" --from 41772909259@s.whatsapp.net

# Filter by media type
wacli --json messages search "report" --type document
```

### Show a specific message

```bash
wacli --json messages show --chat 41772909259@s.whatsapp.net --id 3EB0ABC123
```

### Get context around a message

```bash
wacli --json messages context --chat 41772909259@s.whatsapp.net --id 3EB0ABC123 --before 5 --after 5
```

## Looking up contacts

### Search contacts in wacli

```bash
# Search contacts by name, phone, or alias
wacli --json contacts search "Marco"

# Show a specific contact by JID
wacli --json contacts show --jid 41772909259@s.whatsapp.net
```

### Fallback: use the contacts tool

If you cannot find a contact via `wacli contacts search` (e.g. the person is not
in WhatsApp contacts, or you only have a name without a phone number), use the
**contacts tool** (`python3 /app/tools/contacts.py`) to search across Google
Contacts or CardDAV. This can help you find phone numbers or email addresses
that you can then use to construct the WhatsApp JID.

```bash
# Search contacts tool for phone number
python3 /app/tools/contacts.py search --provider <NAME> --query "Marco" --output json
```

### Refresh contacts from WhatsApp (live query)

```bash
wacli --json contacts refresh
```

## Chats

```bash
# List all chats (sorted by last message)
wacli --json chats list --limit 30

# Search chats by name
wacli --json chats list --query "Family"

# Show chat details
wacli --json chats show --jid 41772909259@s.whatsapp.net
```

## Groups

```bash
# List known groups
wacli --json groups list

# Get group info
wacli --json groups info --jid 123456789@g.us

# Refresh groups from WhatsApp (live query)
wacli --json groups refresh

# Rename a group
wacli --json groups rename --jid 123456789@g.us --name "New Name"
```

## JID format

WhatsApp identifies users and groups by JID (Jabber ID):

- **Users**: `<phone>@s.whatsapp.net` — phone number without `+` (e.g. `41772909259@s.whatsapp.net`)
- **Groups**: `<id>@g.us` (e.g. `120363001234567890@g.us`)

When the user gives you a phone number like "+41 77 290 92 59", strip spaces
and the leading `+` to form the JID: `41772909259@s.whatsapp.net`.

## Allowed operations (no user approval needed)

All of these run immediately without asking the user:

- `wacli sync` — sync latest messages from WhatsApp
- `wacli messages list` / `search` / `show` / `context` — read messages
- `wacli contacts search` / `show` — look up contacts
- `wacli chats list` / `show` — browse chats
- `wacli groups list` / `info` — view groups

## Operations requiring approval

These require user confirmation before running:

- `wacli groups refresh` / `rename` / `participants` / `invite` / `join` / `leave`
- `wacli contacts refresh`
- Sending messages (use `send_message` tool, not `wacli send`)

## Important notes

- Always use `--json` when you need to parse results programmatically.
- Read commands (`messages`, `contacts search/show`, `chats`, `groups list`) query the local DB and do not require a WhatsApp connection.
- Write commands (`sync`, `contacts refresh`, `groups refresh/info/rename`) connect to WhatsApp and acquire an exclusive file lock.
- Only one wacli process can hold the lock at a time. If another is running, the command will fail immediately.
- When the user says "check my WhatsApp" or "any new messages", **always sync first**, then list recent messages.
