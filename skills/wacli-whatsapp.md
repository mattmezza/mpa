# wacli -- WhatsApp CLI

You have access to `wacli` to read WhatsApp messages, search conversations,
look up contacts, and manage groups. wacli keeps a local SQLite database
synced from WhatsApp — most read commands query locally and are fast.

## Important: sending messages

**Do NOT use `run_command` with `wacli send` to send WhatsApp messages.**
Use the `send_message` tool instead (channel="whatsapp"). It handles
delivery through the admin API.

## Syncing

wacli's local database may be stale. Before reading messages or contacts,
run a quick sync to pull the latest data:

```bash
wacli --json sync --once --idle-exit 5s
```

This connects to WhatsApp, pulls new messages, and exits after 5 seconds
of idle. It typically completes in under 10 seconds.

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

## Contacts

```bash
# Search contacts by name or phone
wacli --json contacts search "Marco"

# Show a specific contact
wacli --json contacts show --jid 41772909259@s.whatsapp.net

# Refresh contacts from WhatsApp into local DB
wacli --json contacts refresh
```

## Chats

```bash
# List all chats
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

# Refresh groups from WhatsApp (live query)
wacli --json groups refresh

# Get group info
wacli --json groups info --jid 123456789@g.us

# Rename a group
wacli --json groups rename --jid 123456789@g.us --name "New Name"
```

## JID format

WhatsApp identifies users and groups by JID (Jabber ID):

- **Users**: `<phone>@s.whatsapp.net` — phone number without `+` (e.g. `41772909259@s.whatsapp.net`)
- **Groups**: `<id>@g.us` (e.g. `120363001234567890@g.us`)

When the user gives you a phone number like "+41 77 290 92 59", strip spaces
and the leading `+` to form the JID: `41772909259@s.whatsapp.net`.

## Important notes

- Always use `--json` when you need to parse results programmatically.
- Run `wacli --json sync --once --idle-exit 5s` before reading if freshness matters.
- Read commands (`messages`, `contacts search/show`, `chats`, `groups list`) query the local DB and do not require a WhatsApp connection.
- Write commands (`sync`, `contacts refresh`, `groups refresh/info/rename`) connect to WhatsApp and acquire an exclusive file lock.
- Only one wacli process can hold the lock at a time. If another is running, the command will fail immediately.
- When the user says "check my WhatsApp" or "any new messages", sync first, then list recent messages.
