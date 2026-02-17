# Himalaya Email CLI

You have access to the `himalaya` CLI to manage emails. Himalaya is a stateless CLI
email client — each command is independent, no session state.

## Configuration

Himalaya is pre-configured. The available accounts are defined in its TOML config file.
Always specify the account with `-a <account_name>`.

To see which accounts are available:

```bash
himalaya account list
```

## Reading emails

### List recent emails (envelopes)

```bash
# List last 10 emails in INBOX (default folder)
himalaya -a personal envelope list -s 10 -o json

# List emails in a specific folder
himalaya -a work envelope list --folder "Archives" -s 20 -o json

# Page through results (page 2)
himalaya -a personal envelope list -s 10 -p 2 -o json
```

The JSON output is an array of envelope objects with fields like id, subject, from, date, and flags.

### Read a specific email

```bash
# Read email by ID (returns plain text body with headers)
himalaya -a personal message read 123

# Read specific headers only
himalaya -a personal message read 123 --header From --header Subject --header Date
```

### Search emails

Himalaya uses IMAP search queries after `--`:

```bash
# Search by subject
himalaya -a work envelope list -o json -- "subject:invoice"

# Search by sender
himalaya -a personal envelope list -o json -- "from:alice@example.com"

# Search for unseen emails
himalaya -a personal envelope list -o json -- "unseen"

# Combined search
himalaya -a work envelope list -o json -- "from:ikea subject:contract unseen"
```

## Sending emails

### Send a new email

Construct the message as headers + blank line + body, then pipe to himalaya:

```bash
printf 'From: matteo@example.com\nTo: recipient@example.com\nSubject: Hello\n\nThis is the body.' | himalaya -a personal message send
```

For multi-line bodies:

```bash
printf 'From: matteo@example.com\nTo: recipient@example.com\nSubject: Meeting follow-up\n\nHi Alice,\n\nThanks for the meeting today.\n\nBest regards,\nMatteo' | himalaya -a personal message send
```

With CC and BCC:

```bash
printf 'From: matteo@example.com\nTo: alice@example.com\nCc: bob@example.com\nBcc: carol@example.com\nSubject: Project update\n\nPlease see attached notes.' | himalaya -a work message send
```

### Reply to an email

```bash
# Reply to sender only
printf 'Thanks for your email.\n\nBest regards,\nMatteo' | himalaya -a personal message reply 123

# Reply to all recipients
printf 'Thanks everyone.\n\nBest,\nMatteo' | himalaya -a personal message reply --all 123
```

### Forward an email

```bash
# Forward with added text
printf 'FYI — see below.' | himalaya -a work message forward 123
```

## Managing emails

```bash
# Move to a folder
himalaya -a personal message move 123 "Archives"

# Copy to a folder
himalaya -a personal message copy 123 "Important"

# Delete (moves to Trash)
himalaya -a personal message delete 123

# Add a flag
himalaya -a personal flag add 123 Seen
himalaya -a personal flag add 123 Flagged

# Remove a flag
himalaya -a personal flag remove 123 Seen

# List all folders
himalaya -a personal folder list -o json
```

## Important notes

- Always use `-o json` when you need to parse results programmatically.
- Email IDs are folder-relative — always specify `--folder` when not using INBOX.
- Use `printf` with `\n` for newlines when constructing email messages to pipe to himalaya. Never use bare `echo` with literal newlines as it can break in shell.
- When sending on behalf of the user, always include the correct `From:` header matching the account's email address.
- Before sending, always present the draft to the user for approval.
