# Calendar Management (CalDAV)

Calendar operations use helper scripts that wrap Python's caldav library.

## Available calendars

- `google` — Matteo's Google Calendar (primary, work events)
- `icloud` — Shared family calendar

## Reading events

```bash
# Get today's events
python3 /app/tools/calendar_read.py --calendar google --today -o json

# Get events for a date range
python3 /app/tools/calendar_read.py --calendar google --from 2025-02-17 --to 2025-02-24 -o json

# Get next N events
python3 /app/tools/calendar_read.py --calendar google --next 5 -o json
```

JSON output:

```json
[
  {
    "uid": "abc123",
    "summary": "Team standup",
    "start": "2025-02-17T09:00:00+01:00",
    "end": "2025-02-17T09:30:00+01:00",
    "location": "Google Meet",
    "attendees": ["alice@example.com", "bob@example.com"]
  }
]
```

## Creating events

Use the `create_calendar_event` structured tool (requires permission). Provide:
- `calendar`: "google" or "icloud"
- `summary`: event title
- `start`: ISO datetime with timezone (e.g. "2025-02-20T14:00:00+01:00")
- `end`: ISO datetime with timezone
- `attendees`: optional list of email addresses (sends invites automatically)

## Important notes

- Always include timezone (Europe/Zurich = UTC+1, UTC+2 during DST).
- For all-day events, use date only: "2025-02-20".
- Use `google` calendar for work events, `icloud` for family/personal.
