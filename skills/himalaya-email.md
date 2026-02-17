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
himalaya envelope list -a personal -s 10 -o json

# List emails in a specific folder
himalaya envelope list -a work --folder "Archives" -s 20 -o json

# Page through results (page 2)
himalaya envelope list -a personal -s 10 -p 2 -o json
```

The JSON output is an array of envelope objects with fields like id, subject, from, date, and flags.

### Read a specific email

```bash
# Read email by ID (returns plain text body with headers)
himalaya message read -a personal 123

# Read specific headers only
himalaya message read -a personal 123 --header From --header Subject --header Date
```

### Search emails

Himalaya uses IMAP search queries after `--`:

```bash
# Search by subject
himalaya envelope list -a work -o json -- "subject invoice"

# Search by sender
himalaya envelope list -a personal -o json -- "from alice@example.com"

# Search for unseen emails
himalaya envelope list -a personal -o json -- "not flag seen"

# Combined search
himalaya envelope list -a work -o json -- "from ikea subject contract unseen"
```

## Sending emails

### Send a new email

Construct the message as headers + blank line + body, then pipe to himalaya:

```bash
printf 'From: matteo@example.com\nTo: recipient@example.com\nSubject: Hello\n\nThis is the body.' | himalaya message send -a personal
```

For multi-line bodies:

```bash
printf 'From: matteo@example.com\nTo: recipient@example.com\nSubject: Meeting follow-up\n\nHi Alice,\n\nThanks for the meeting today.\n\nBest regards,\nMatteo' | himalaya message send -a personal
```

With CC and BCC:

```bash
printf 'From: matteo@example.com\nTo: alice@example.com\nCc: bob@example.com\nBcc: carol@example.com\nSubject: Project update\n\nPlease see attached notes.' | himalaya message send -a work
```

### Reply to an email

```bash
# Reply to sender only
printf 'Thanks for your email.\n\nBest regards,\nMatteo' | himalaya message reply 123 -a personal

# Reply to all recipients
printf 'Thanks everyone.\n\nBest,\nMatteo' | himalaya message reply --all 123 -a personal
```

### Forward an email

```bash
# Forward with added text
printf 'FYI — see below.' | himalaya message forward 123 -a work
```

## Managing emails

```bash
# Move to a folder
himalaya message move 123 "Archives" -a personal

# Copy to a folder
himalaya message copy 123 "Important" -a personal

# Delete (moves to Trash)
himalaya message delete 123 -a personal

# Add a flag
himalaya flag add 123 Seen -a personal
himalaya flag add 123 Flagged -a personal

# Remove a flag
himalaya flag remove 123 Seen -a personal

# List all folders
himalaya folder list -o json -a personal
```

## Important notes

- Always use `-o json` when you need to parse results programmatically.
- Email IDs are folder-relative — always specify `--folder` when not using INBOX.
- Use `printf` with `\n` for newlines when constructing email messages to pipe to himalaya. Never use bare `echo` with literal newlines as it can break in shell.
- When sending on behalf of the user, always include the correct `From:` header matching the account's email address.
- Before sending, always present the draft to the user for approval.
