# Himalaya Email CLI

You have access to the `himalaya` CLI to manage emails. Himalaya is a stateless CLI
email client — each command is independent, no session state.

## Configuration

Himalaya is pre-configured with these accounts:
- `personal` — Matteo's personal email
- `work` — Matteo's work email

Always specify the account with `-a <account_name>`.

## Reading emails

### List recent emails (envelopes)

```bash
# List last 10 emails in INBOX (default folder)
himalaya -a personal envelope list -s 10 -o json

# List emails in a specific folder
himalaya -a work envelope list --folder "Archives" -s 20 -o json
```

The JSON output is an array of envelope objects:

```json
[
  {
    "id": "123",
    "subject": "Meeting tomorrow",
    "from": {"name": "Alice", "addr": "alice@example.com"},
    "date": "2025-02-17T10:30:00Z",
    "flags": ["Seen"]
  }
]
```

### Read a specific email

```bash
# Read email by ID (returns plain text body)
himalaya -a personal message read 123

# Read as raw MIME (useful for attachments)
himalaya -a personal message read 123 --raw
```

### Search emails

```bash
# Search by subject
himalaya -a work envelope list --folder INBOX -o json -- "subject:invoice"

# Search by sender
himalaya -a personal envelope list -o json -- "from:simge"

# Combined search
himalaya -a work envelope list -o json -- "from:ikea subject:contract"
```

## Sending emails

### Send a new email

```bash
# Using MML (MIME Meta Language) format via stdin
echo 'From: matteo@example.com
To: recipient@example.com
Subject: Hello from the agent

This is the body of the email.' | himalaya -a personal message send
```

### Reply to an email

```bash
echo 'Thank you for your email.

Best regards,
Matteo' | himalaya -a personal message reply 123
```

### Forward an email

```bash
himalaya -a work message forward 123
```

## Managing emails

```bash
# Move to folder
himalaya -a personal message move 123 "Archives"

# Delete
himalaya -a personal message delete 123

# Flag/unflag
himalaya -a personal flag add 123 Seen
himalaya -a personal flag remove 123 Seen

# List folders
himalaya -a personal folder list -o json
```

## Important notes

- Always use `-o json` when you need to parse results programmatically.
- Email IDs are relative to the current folder — always specify `--folder` when not using INBOX.
- For multi-line email bodies, construct the full MML template and pipe it via echo.
- The `personal` account is for personal correspondence, `work` for professional.
