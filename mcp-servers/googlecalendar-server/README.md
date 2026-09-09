# Google Calendar Server (Google Calendar MCP Replica)

A mock of Google's official Google Calendar MCP server (`calendarmcp.googleapis.com`) that serves a synthetic calendar for benchmarking. All 9 tools - 5 read, 4 write - with the same request parameters and the same response envelopes, without needing a Google account or OAuth.

Like `gmail-server`, and unlike the other siblings in this folder, there is **no REST interface**: nothing in the agent path calls a REST port for calendar, so the server is MCP-only.

## Quick start

### Option 1: Run locally (no Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Point at your data directory and start
DATA_DIR=/path/to/integrations/data python3 mcp_server.py
```

The server starts on `http://localhost:8016/mcp` (Streamable HTTP transport).

### Option 2: Docker

```bash
# From the mcp-servers-2 root
docker compose up gcal-mcp-http
```

Published on `http://localhost:8016/mcp` (container port 8016 - this server has no port shift, unlike file-server and mail).

## Authentication

None. Like the other MCP HTTP endpoints in this folder, the calendar server takes no bearer token.

## MCP tools

### Read

| Tool | Description |
|------|-------------|
| `list_events` | Events on a calendar, filtered by time window, free text, and event type. Expands recurring events into occurrences |
| `get_event` | Read one event by ID |
| `list_calendars` | The calendars the user has access to - call this to turn "my calendar" into an email address |
| `suggest_time` | Free time slots common to several attendees, with working-hours preferences |
| `search_events` | Free-text search over the primary calendar. No time parameters |

### Write

| Tool | Description | Returns |
|------|-------------|---------|
| `create_event` | Create an event, optionally recurring, with attendees and a Meet link | the created `Event` |
| `update_event` | Sparse-patch an event; unset fields are left alone | the updated `Event` |
| `delete_event` | Cancel an event (soft delete - `status` becomes `cancelled`) | the cancelled `Event` |
| `respond_to_event` | Set the *user's own* RSVP, with an optional comment | the updated `Event` |

All four writes return the whole `Event`, unlike the Gmail server's writes which mostly return `{}`.

Two asymmetries carried over from the real toolset rather than smoothed out:

- **`list_events` has a time window; `search_events` has none.** The official descriptions push each way explicitly: `list_events` says to use `search_events` for open-ended keyword searches, and `search_events` says to use `list_events` when a time range is needed.
- **`respond_to_event` accepts three RSVP values, but `Attendee.responseStatus` has four.** `needsAction` is not in the parameter's list, so an RSVP cannot be undone once set.

`create_event` is the only non-idempotent tool: calling it twice creates two events. `update_event`, `delete_event` and `respond_to_event` are all idempotent - re-sending the same patch, cancel or RSVP changes nothing.

### Writes do not touch the dataset

`/data` is mounted read-only, and the write tools still work. The store is an in-process dict rebuilt from the JSON at every startup, so a mutation lands there and nowhere else:

- Effects last for the life of the container and are gone after a restart, which is what keeps benchmark trials isolated - every run starts from the same authored calendar, with no reset step.
- **Verify a write through a read tool**, not by reading `$DATA_DIR`. Cancel an event and `get_event` reports `status: "cancelled"`; the JSON on disk is identical either way.

### `list_events`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `calendar_id` | string | primary | Email address. Resolve a phrase like "my calendar" with `list_calendars` |
| `start_time` | string | none | ISO 8601 lower bound. **Only set when the user asked for a timeframe** |
| `end_time` | string | none | ISO 8601 upper bound, must be after `start_time` |
| `full_text` | string | `""` | Case-insensitive AND search over title, description, location, attendee names and addresses |
| `event_type` | string[] | 4 of 7 types | Defaults exclude `WORKING_LOCATION` and `BIRTHDAY` |
| `order_by` | enum | `default` | `default`, `startTime`, `startTimeDesc`, `lastModified` |
| `page_size` | int | `100` | Clamped to 1-250. Counts **occurrences**, not distinct events |
| `page_token` | string | `""` | Cursor from a previous response's `nextPageToken` |
| `time_zone` | string | calendar's | IANA zone used to resolve timezone-less `start_time`/`end_time` |

The response is the **calendar resource, with `events[]` as one of its fields** - six of its eight top-level keys describe the calendar, not the results. This is the single most surprising shape in the toolset and it is reproduced as captured:

```json
{
  "summary": "Ellie Ashworth",
  "description": "Primary calendar for Ellie Ashworth",
  "timeZone": "Europe/London",
  "updated": "2026-08-06T09:30:00.000Z",
  "accessRole": "owner",
  "defaultReminders": [],
  "events": [
    {
      "id": "EVT-0113",
      "summary": "Verano <> Maple weekly sync",
      "start": {"dateTime": "2026-07-28T10:00:00+01:00", "timeZone": "Europe/London"},
      "end": {"dateTime": "2026-07-28T10:30:00+01:00", "timeZone": "Europe/London"},
      "status": "confirmed",
      "eventType": "DEFAULT",
      "availability": "AVAILABILITY_BUSY",
      "visibility": "default",
      "transparency": "opaque",
      "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU"],
      "recurringEventId": "EVT-0113",
      "originalStartTime": {"dateTime": "2026-07-21T10:00:00+01:00", "timeZone": "Europe/London"},
      "attendees": [ ... ],
      "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0113"
    }
  ]
}
```

**Recurring events are expanded into occurrences.** 8 of the 50 events recur, so the counts are not what a naive read of the dataset predicts: a call with no arguments returns **75 occurrences across all 50 distinct events**, and the week of 27 July 2026 returns **13 occurrences across 9 distinct events**. Occurrences carry `recurringEventId` and `originalStartTime`; a non-recurring event carries neither, which is how you tell them apart.

**Windows overlap; they do not contain.** A five-minute probe inside a one-hour meeting finds the meeting.

### `get_event`

| Parameter | Type | Default |
|-----------|------|---------|
| `event_id` | string | required |
| `calendar_id` | string | primary |

Returns a bare `Event` - no wrapper, no pagination fields. It returns the **base record**, never an occurrence, so a recurring event comes back at its authored start with `recurrence` set and no `recurringEventId`. An event the given calendar has no part in is a not-found error rather than an empty result.

### `list_calendars`

Takes `page_size` and `page_token`. Returns **one** calendar, the owner's:

```json
{
  "calendars": [
    {
      "id": "ellie.ashworth@maplesoftware.net",
      "summary": "Ellie Ashworth",
      "description": "Primary calendar for Ellie Ashworth",
      "timeZone": "Europe/London"
    }
  ]
}
```

Attendee addresses are not synthesized into calendars - the real tool returns calendars the user has *access to*, which would not include a customer's personal calendar. Those addresses are discoverable from `attendees[]` on any event, and `suggest_time` accepts them directly.

### `suggest_time`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `attendee_emails` | string[] | required | Emails to find common free time for |
| `start_time` | string | required | ISO 8601 interval start |
| `end_time` | string | required | ISO 8601 interval end |
| `duration_minutes` | int | `30` | Minimum slot length |
| `time_zone` | string | calendar's | IANA zone the preference hours are interpreted in |
| `preferences` | object | none | `startHour`, `endHour` (`"HH:mm"`), `excludeWeekends`, `pageSize` |

`pageSize` lives **inside `preferences`**, not at the top level, and there is no `pageToken` - this tool is not paginated. Returns `{"timeSlots": [{start, end}, ...]}`.

```json
{
  "timeSlots": [
    {"start": {"dateTime": "2026-07-29T09:00:00+01:00", "timeZone": "Europe/London"},
     "end":   {"dateTime": "2026-07-29T09:30:00+01:00", "timeZone": "Europe/London"}}
  ]
}
```

Busy time is the union of every event each named attendee is on, **with recurrence expanded** - which is the whole reason expansion is not optional. Ask for 3 August 2026 without it and the day reads as entirely free, because the weekly pipeline review that occupies 09:00 is authored on 21 July.

An attendee address with no events contributes no busy time rather than being an error, which matches how the real tool treats an external invitee whose calendar it cannot see.

### `search_events`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `query` | string | required | Free text; every term must match (AND), case-insensitive |
| `page_size` | int | `100` | Clamped to 1-250 |
| `page_token` | string | `""` | Cursor from a previous response |

Returns a thin `{"events": [...], "nextPageToken"?: "..."}` - not the calendar envelope `list_events` uses.

Primary calendar only; there is no `calendar_id` parameter. Terms match title, description, location, and the display names **and email addresses** of the organizer and attendees, so `priya` finds the 23 events she is on and `thames` finds the 2 events mentioning the Thames.

**This is the one tool that does not expand recurrence**, and counts are therefore per distinct event. It has no time parameters, so there is no window an occurrence count could be relative to. Use `list_events` with `full_text` if you want instances.

### `create_event`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `summary` | string | required | Title |
| `start_time` / `end_time` | string | required | ISO 8601; `start_time` must be earlier |
| `calendar_id` | string | primary | |
| `description` / `location` | string | `""` | |
| `all_day` | bool | `false` | Start and end are floored to midnight in the resolved zone |
| `time_zone` | string | calendar's | **Overrides** any offset in `start_time`/`end_time` |
| `attendees` | object[] | `[]` | `{email, displayName?}`. The owner is auto-added as `accepted` |
| `availability` | enum | `AVAILABILITY_BUSY` | Or `AVAILABILITY_FREE` |
| `visibility` | enum | `default` | Or `public`, `private` |
| `event_type` | enum | `DEFAULT` | Anything else is an error - see below |
| `recurrence_data` | string[] | none | One RRULE string. `RDATE`/`EXDATE` are rejected |
| `add_google_meet_url` | bool | `false` | Mints a `meet.google.com` link |
| `google_meet_url` | string | `""` | An explicit URL, which wins over the flag |
| `attachments` | object[] | `[]` | `{fileUrl, title}` |
| `notification_level` | enum | `ALL` | Accepted and dropped - the mock sends no mail |

Returns the created `Event` with its assigned ID (`EVT-0151` onward). The owner is added to `attendees[]` automatically with an `accepted` RSVP, which is both what the parameter's own description promises and what keeps the record valid against the schema (ontology §A.5.3, which requires the organizer in `attendees[]`).

An `event_type` other than `DEFAULT` is an **error**, not a silently-downgraded value: storage has no event-type field, so accepting `OUT_OF_OFFICE` would mean confirming a property that nothing can subsequently report.

### `update_event`

Same field set as `create_event`, plus `added_attendees` / `removed_attendee_emails` and `added_attachments` / `removed_attachment_file_urls`. Returns the updated `Event`.

**It is a sparse patch: fields you do not pass are left alone.** Passing an empty string *clears* the field, so `description=""` removes the description while omitting it keeps it.

Two things to know:

- **Patching only `start_time` preserves the duration.** A 14:00-15:30 event moved to 16:00 ends at 17:30. Pass `end_time` too if you want to change the length.
- **Attachments are removed by URL**, using the `fileUrl` a read tool reported, so a read-modify-write round-trips.

### `delete_event`

| Parameter | Type | Default |
|-----------|------|---------|
| `event_id` | string | required |
| `calendar_id` | string | primary |
| `notification_level` | enum | `ALL` |

A **soft** delete: `status` becomes `cancelled` and the event stays readable and stays in listings. There is no `showDeleted` parameter, so the enum value is the only signal - and one event in the dataset (`EVT-0112`) is authored `cancelled` already, so a consumer has to handle the value from the first request regardless. This is also why the tool can return the deleted `Event` at all.

### `respond_to_event`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `event_id` | string | required | |
| `response_status` | enum | required | `declined`, `tentative`, or `accepted` |
| `response_comment` | string | `""` | Attached to the owner's attendee entry as `comment` |
| `calendar_id` | string | primary | |
| `notification_level` | enum | `ALL` | |

Sets **only the owner's** RSVP - there is no attendee parameter, because the tool is "respond as the user". It does **not** change `Event.status`: a tentative event stays tentative after the owner accepts, and status is only reachable through `delete_event`. Responding on an event the owner is not an attendee of is an error.

### MCP client config

Over Streamable HTTP (how the benchmark agents connect):

```json
{
  "mcpServers": {
    "gcal": {
      "type": "http",
      "url": "http://bench-gcal-mcp:8016/mcp"
    }
  }
}
```

## Recurrence

8 of the 50 events recur, using 6 distinct rules. Supported: `FREQ` (`DAILY`, `WEEKLY`, `MONTHLY`), optional `BYDAY` including ordinals (`3TH` = the third Thursday), and optional `COUNT` or `UNTIL`.

```
RRULE:FREQ=WEEKLY;BYDAY=TU
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR
RRULE:FREQ=MONTHLY;BYDAY=3TH
RRULE:FREQ=DAILY;COUNT=5
```

Occurrences are **generated per query and never stored**. All 8 authored rules are infinite - no `COUNT`, no `UNTIL` - so there is no finite set to precompute, and a query with no `end_time` is bounded by the store's own far edge instead.

**Anything outside that subset is rejected, not ignored.** `create_event` with `INTERVAL=2`, `FREQ=YEARLY`, `RDATE`, `EXDATE`, or two RRULEs returns an error naming what it could not honour. An ignored token would produce an event that exists and recurs wrongly, with nothing in the response to say so.

## Times

Three different representations, which is where most of the subtlety lives:

| Layer | Form | Example |
|-------|------|---------|
| Storage | naive local wall clock + a separate IANA zone | `"2026-07-28T10:00:00"` + `"Europe/London"` |
| Tool arguments | ISO 8601, offset-bearing or bare | `"2026-07-28T09:00:00Z"` |
| Wire responses | `dateTime` with a per-event offset, or `date` for all-day | `"2026-07-28T10:00:00+01:00"` |

- **The offset is computed per event, from its own zone and its own date.** A weekly London event holds `10:00` local across the BST boundary while its offset moves `+01:00` -> `+00:00`. Two events are authored in `Asia/Singapore` (`+08:00`) and `America/New_York` (`-04:00`).
- **All-day events report `date`, not `dateTime`** - and as a full midnight timestamp (`"2026-07-24T00:00:00Z"`), which is what the capture shows rather than the bare `YYYY-MM-DD` the REST API uses. It is the *local* calendar date: as an instant, midnight on 24 July in London is 23:00Z on the 23rd, so a UTC conversion would report the event a day early.
- **`updated` carries milliseconds** (`"2026-08-06T09:30:00.000Z"`), unlike the Gmail server's second-precision dates.
- **Relative reasoning is anchored to the data, not the wall clock.** `datetime.now()` appears nowhere in either module: `updated` stamps come from the newest authored event end (`2026-08-06T09:30:00Z`), frozen at load, so the same call returns the same bytes on every run.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `/data` | Path to the data root (must contain a `calendar_json_data/` subdir) |
| `MCP_PORT` | `8016` | MCP HTTP listen port |

No `ACCESS_TOKEN`: the MCP HTTP endpoints take no auth.

## Data format

```
$DATA_DIR/
└── calendar_json_data/
    ├── events.json         (50 events, 8 of them recurring)
    └── transcripts.json    (meeting transcripts, attached by event ID)
```

Records are loaded into a plain dict keyed by event ID, with a derived UTC start/end computed per record for range queries and a lowercased text corpus per record for the free-text matcher. Nothing is written back to disk and everything is rebuilt at startup. Source-of-truth field definitions live in `docs/ontology/ontology.md` §A.5.3 (`CalendarEvent`), which a pre-commit validator enforces.

Startup assertions check that each record round-trips and that the invariants the query engine relies on hold - `recurrence` is a scalar string or null, the organizer appears in `attendees[]`, an all-day event's times are midnight. A schema drift is therefore a startup failure rather than a wrong answer at query time.

## Things that look wrong and are not

- **`list_events` returns the calendar, with `events[]` inside it.** Not `{"events": [...]}` - that is `search_events`' envelope. Two envelope shapes over one `Event` builder.
- **A bare `list_events` returns 75 rows for 50 events.** Recurrence is expanded; `page_size` counts occurrences.
- **`search_events` returns 23 rows for `priya`, not 40.** It is the one tool that does not expand - see above.
- **An all-day `start` has no `dateTime` key at all**, and its `date` is a full timestamp ending `T00:00:00Z`.
- **`get_event` on a recurring event has no `recurringEventId`.** That field marks a *generated occurrence*; `get_event` returns the base record.
- **`eventType` is always `DEFAULT`.** Two events are titled "Focus block -- ..."; classifying them as `FOCUS_TIME` by title match would be inventing data the schema has no field for. A `FOCUS_TIME` filter is honoured literally and returns nothing.
- **`transparency` duplicates `availability`.** It is deprecated in the real schema and mirrors it here.
- **`creator` equals `organizer`.** No creator is authored, and Google's own docs note the two usually coincide.
- **`availability` has three values and storage has three, but they are not the same three.** `show_as: tentative` has no wire equivalent and reports `AVAILABILITY_BUSY` - a tentative hold does block time. Likewise `sensitivity: confidential` reports `visibility: private`. Both collapses are one-way, and `update_event` will not let an echoed value overwrite the finer stored one.
- **`fileUrl` is synthesized** from the stored `file_id` - no URL is authored - and the synthesis embeds the ID verbatim so `removed_attachment_file_urls` can invert it.
- **A filter matching nothing returns an empty list, not an error.** Errors are reserved for genuine faults: unknown ID, unparseable timestamp, unsupported RRULE, inverted interval.
- **Cancelled events still appear in listings.** There is no `showDeleted` parameter; check `status`.
- **`order_by: "lastModified"` returns the default order.** Storage has no modification timestamp, so there is nothing to sort by. The other three values are honoured.
- **A write survives until the container restarts, and never reaches the JSON.** See "Writes do not touch the dataset" above.

## Troubleshooting

**Server says "0 events loaded"**
- `DATA_DIR` must contain a `calendar_json_data/` subdirectory. Check that `$DATA_DIR/calendar_json_data/events.json` exists.

**`ZoneInfoNotFoundError: Europe/London`**
- `python:3.12-slim` ships no system timezone database. `tzdata` is in `requirements.txt` for exactly this reason - reinstall dependencies. Every timestamp here resolves through a named zone, so the load fails outright rather than degrading.

**Times are off by an hour**
- Check whether you are reading the stored local time or the wire offset. A London event in July is `10:00` local and `09:00Z`; both are correct, and the wire form carries `+01:00` to say which is which.

**An all-day event looks like it is on the wrong day**
- If it appears one day early you are converting the local midnight to UTC. The `date` field is built from the local calendar date on purpose.

**`suggest_time` offers a slot that is already busy**
- The blocking event is probably a recurring one. Confirm with `list_events` over the same window, which expands; if the occurrence shows there but the slot is still offered, that is a bug in the free/busy merge, not in the data.

**`suggest_time` returns nothing**
- `preferences.startHour`/`endHour` are interpreted in `time_zone` (or the calendar's), and a slot must fit **entirely** inside the window. Widen the window or shorten `duration_minutes`.

**`search_events` finds nothing but `list_events` does**
- `search_events` is primary-calendar only and has no time window; `full_text` on `list_events` uses the same matcher over a possibly different calendar. Also check every term - matching is AND, not OR.

**`create_event` rejects a recurrence rule**
- The supported subset is `FREQ` + optional `BYDAY` + optional `COUNT`/`UNTIL`. `INTERVAL`, `FREQ=YEARLY`, `RDATE`, `EXDATE` and multiple RRULEs are refused by design; the error names the part that could not be honoured.

**A write "worked" but the JSON is unchanged**
- Expected - the store is in-process and `/data` is `:ro`. Verify through a read tool.

**Server exits immediately / nothing on the port**
- This server speaks Streamable HTTP, not stdio. It listens on `$MCP_PORT`; there is no REST port and no `/health` endpoint to curl.

## Testing

```bash
# The tracked suite (from docker/mcp-servers-2/tests/, no Docker needed)
pytest test_gcal_server.py -v

# Against the container
pytest test_gcal_server.py -v --mode integration

# With the local golden suite too, if you have it (see note below)
pytest test_gcal_server.py test_gcal_reads_golden.py -v
```

104 cases across two suites, split by what they assert:

| Suite | Cases | Asserts | Covers |
|---|---|---|---|
| `test_gcal_server.py` | 57 | Shapes, field types, one tool cross-checked against another | All 9 tools, including the 4 write tools |
| `test_gcal_reads_golden.py` | 47 | Exact values - whole response bodies, exact counts, exact ID sets | `list_events`, `get_event`, `list_calendars`, `search_events` |

**`test_gcal_reads_golden.py` is not in the repo** - it is local-only. Its assertions pin the calendar exactly as authored, so it fails by design whenever events are added, which makes it a poor gate on `main`. `test_gcal_server.py` is the tracked suite. The rest of this section describes both, because the split is the point.

Together they pin the measured fixtures - the 75/50 and 13/9 occurrence counts, 21 search queries with their exact ID sets, the two all-day dates, the non-London offsets, the DST-crossing event, and the free/busy and `suggest_time` examples - because those are what distinguish a correct query engine from one that returns a plausible number.

The golden suite is the one that would notice a *wrong but well-formed* answer: `search_events("Verano")` returning the wrong ten events passes every shape assertion. Because of that, adding events to the dataset is expected to fail some of its counts, and the diff is the report of what changed. When updating one, re-derive it from `events.json` - not from what the server now returns.

Both suites pass under shuffled collection order, and the golden suite calls no write tool: the test client is class-scoped, so a create would move `HORIZON` and change the bare-listing count for every later test.
