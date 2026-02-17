# Khard Contacts CLI

You have access to `khard` to look up and manage contacts via CardDAV.
Contacts are synced from the CardDAV server via vdirsyncer.

## Looking up contacts

### Search for a contact

```bash
# Search by name (partial match)
khard list "Marco"

# Show full details for a contact
khard show "Marco Rossi"

# Get just the email address
khard email "Marco"

# Get just the phone number
khard phone "Marco"
```

### List all contacts

```bash
khard list
```

Output format (tab-separated):

```
Name                Email                    Phone
Marco Rossi         marco@example.com        +39 333 1234567
```

## Creating contacts

```bash
khard new --vcard contact.vcf
```

## Editing and deleting

```bash
khard modify "Marco Rossi"
khard remove "Marco Rossi"
```

## Important notes

- Run `vdirsyncer sync` before lookups if contacts may be stale.
- When the user says "send a message to Marco", use `khard email` or `khard phone`
  to resolve the contact's address/number before composing.
- khard does not support JSON output — parse the tab-separated text output.
- If multiple contacts match a search, present the options to the user.
