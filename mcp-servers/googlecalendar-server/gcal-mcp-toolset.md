# Google Calendar MCP Server — Toolset & Wire Contract

Companion to [gcal-mcp-server.md](gcal-mcp-server.md) (architecture: process model, transport,
storage, directory layout, registration). **This** doc is the wire contract: for every tool — its
description, the exact request the LLM will emit, and the exact response we must return.

Source: untruncated `tools/list` capture against Google's Calendar MCP server. **9 tools — 5 read,
4 write.** The raw capture is not in the repo; every tool description reproduced below is quoted
from it verbatim, and `mcp_server.py` copies those descriptions from here.

Examples use real records from `integrations/data/calendar_json_data/` (50 events, 8 transcripts).
Field names on the storage side are the ones in the ontology's Communication domain,
[ontology.md](../../../docs/ontology/ontology.md) §A.5.3 `CalendarEvent`. References to
`calendar_schema.md` below are to the construction worksheet that content came from; it is an
untracked local file, and §A.5 is the tracked source of truth.

**All 9 tools are implementable against the authored schema.** None has to be stubbed. Six of them
need at least one field synthesized or one enum collapsed; §5 is the field-by-field ledger of what
maps, what is derived, what is invented, and what is dropped.

---

## 0. The one-paragraph orientation

Gmail's toolset put its entire filter surface in **one string** (`query`) and its entire response
surface in **one nested type** (`Thread` → `Message[]`). Calendar inverts both. Filtering is
**typed parameters** (`startTime`, `endTime`, `eventType[]`, `fullText`, `orderBy`) with no operator
mini-language to parse — so there is no `_parse_gmail_query()` analogue. But the response surface
is **much wider**: one `Event` type with 26 fields, of which our schema sources 15, and a
calendar-level envelope that has no authored record behind it at all. The work moves from *parsing*
to *projecting*.

---

## 1. Shared response types

The real server reuses these across tools via `$ref`. **One builder function per type, reused
everywhere** — never shape a response inline in a tool body. Same rule as the Gmail server's
`_message`/`_thread`/`_label`/`_draft` builders.

### `Event` — the anchor type
Used as the **whole response body** by `get_event`, `create_event`, `update_event`, `delete_event`,
`respond_to_event`; nested under `events[]` by `list_events` and `search_events`.

| Field | Type | Source | Populated when |
|---|---|---|---|
| `id` | string | `id` | always |
| `summary` | string | `subject` | always — **`summary`, not `subject`** |
| `description` | string | `description` | always (may be `""`) |
| `start` | `DateOrDateTime` | `start` | always |
| `end` | `DateOrDateTime` | `end` | always — **exclusive** |
| `location` | string | `location` | always (may be `""`) |
| `status` | string | `status` | always — enum-as-string, values map 1:1 |
| `organizer` | `Principal` | `organizer` | always (`readOnly`) |
| `creator` | `Principal` | `organizer` | always (`readOnly`) — **no authored creator; aliases organizer**, see §5.3 |
| `attendees` | `Attendee[]` | `attendees` | always |
| `availability` | enum | `show_as` | always — **lossy collapse**, see §5.2 |
| `visibility` | string | `sensitivity` | always — **lossy collapse**, see §5.2 |
| `transparency` | string | `show_as` | `deprecated` — mirror of `availability` |
| `conferenceUrl` | string | `online_meeting.join_url` | only when `is_online_meeting` (41/50) |
| `recurrence` | string[] | `recurrence` | only when non-null (8/50) — **array on the wire, scalar in storage** |
| `attachments` | `Attachment[]` | `attachments` | only when present (8/50) |
| `eventType` | enum | — | always `DEFAULT` (synthesized constant) |
| `htmlLink` | string | — | synthesized from `id` (`readOnly`) |
| `iCalUID` | — | `iCal_uid` | **not in this capture's `Event`** — see §5.4 |
| `recurringEventId` | string | — | only on expanded instances (§6.3) |
| `originalStartTime` | `DateOrDateTime` | — | only on expanded instances (§6.3) |
| `colorId` | string | — | **never** — no source |
| `created` | string | — | **never** — no source (`readOnly`) |
| `updated` | string | — | **never** on reads; set on writes (`readOnly`) |
| `guestPermissions` | `GuestPermissions` | — | **never** on reads; echoed on writes |
| `overrideReminders` | `Reminder[]` | — | **never** on reads; echoed on writes |
| `workingLocationProperties` | `WorkingLocationProperties` | — | **never** — requires `eventType: WORKING_LOCATION`, which no authored event is |

**There is no `subject` field and no `body`/`bodyPreview` field.** Our `subject` becomes `summary`;
our `description` becomes `description`. **`body_preview` has no wire home at all** — and it is
byte-identical to `description` on all 50 records, so nothing is lost. (Verified: `body_preview ==
description` for 50/50. Unlike Gmail, where `snippet` and the body genuinely differ and the stored
preview is what the wire shows, here the preview is redundant and simply has nowhere to go.)

### `DateOrDateTime` — "Set date or date_time, but not both"
| Field | Type | Notes |
|---|---|---|
| `date` | string | **all-day events only.** Capture says: *"ISO 8601 date at midnight UTC (for example, `'2019-11-20T00:00:00Z'`)"* |
| `dateTime` | string | **timed events only.** Capture says: *"ISO 8601 timestamp (for example, `'2019-11-20T08:19:06-07:00'`)"* — **offset-bearing** |
| `timeZone` | string | IANA zone. Capture: *"The original time zone… The returned `date_time` might be in a different display time zone"* |

**Two traps here, both load-bearing.**

1. **`date` is a full timestamp with `Z`, not a bare date.** The real Google Calendar v3 REST API
   uses `"date": "2019-11-20"`. This MCP capture explicitly documents
   `"date": "2019-11-20T00:00:00Z"`. **Follow the capture** — same rule as Gmail's
   `create_draft`-schema-beats-prose call. Affects our 2 all-day events (EVT-0107, EVT-0146).

2. **`dateTime` carries an offset; storage does not.** `calendar_schema.md` mandates local
   wall-clock with **no `Z`** plus a separate IANA zone, and the dataset validator enforces the
   absence of `Z`. The projection is `local wall-clock + IANA zone → offset-bearing ISO 8601`:

   | Event | Stored `date_time` | Stored `time_zone` | Wire `dateTime` | Wire `timeZone` |
   |---|---|---|---|---|
   | EVT-0103 | `2026-06-08T14:00:00` | `Europe/London` | `2026-06-08T14:00:00+01:00` | `Europe/London` |
   | EVT-0135 | `2026-07-07T09:00:00` | `Asia/Singapore` | `2026-07-07T09:00:00+08:00` | `Asia/Singapore` |
   | EVT-0136 | `2026-07-09T15:00:00` | `America/New_York` | `2026-07-09T15:00:00-04:00` | `America/New_York` |

   The offset is **computed per event from its own zone and its own date** — BST vs GMT for London,
   EDT vs EST for New York. A hardcoded `+01:00` would be wrong for 2 of 50 events and wrong for
   every London event outside BST.

### `Principal` — used by `organizer` and `creator`
| Field | Type | Source | Notes |
|---|---|---|---|
| `displayName` | string | `.name` | |
| `email` | string | `.email` | |
| `self` | boolean | derived | `readOnly` — true iff `email == ACCOUNT_ADDRESS`. Default `false` |

### `Attendee`
| Field | Type | Source | Notes |
|---|---|---|---|
| `email` | string | `.email` | **Required** |
| `displayName` | string | `.name` | |
| `responseStatus` | string | `.response_status` | **1:1 verbatim** — `needsAction` \| `declined` \| `tentative` \| `accepted`. camelCase preserved; these are the vendor's own constants and our schema already stores them in the vendor's casing |
| `organizer` | boolean | derived | `readOnly` — true iff this attendee's email `== organizer.email`. Default `false` |
| `self` | boolean | derived | `readOnly` — true iff `email == ACCOUNT_ADDRESS`. Default `false` |
| `id` | string | — | `readOnly` — no source. **Omit** |
| `comment` | string | — | `readOnly` — no source on reads; **`respond_to_event` writes it** (§4.4) |
| `optionalAttendee` | boolean | — | no source. Omit (defaults `false`) |
| `resource` | boolean | — | no source. Omit (defaults `false`) |
| `additionalGuests` | int32 | — | no source. Omit (defaults `0`) |

`organizer` and `self` are **derived projections, never stored** — the same rule that keeps
`UNREAD`/`IMPORTANT`/`STARRED` out of `messages.labels` in the Gmail server.
`calendar_schema.md` guarantees the organizer also appears in `attendees[]` (verified 50/50), so
exactly one attendee per event gets `organizer: true`.

### `Attachment`
| Field | Type | Source | Notes |
|---|---|---|---|
| `fileUrl` | string | **synthesized** | **Required** by the schema, and **we have no URL** — storage has `file_id` only. See §5.3 |
| `title` | string | `.title` | 1:1 |

**`mime_type` has no wire home.** Our `attachments[].mime_type` (all 8 are
`application/vnd.google-apps.document`) is dropped — `Attachment` has only `fileUrl` and `title`.

### `CalendarListItem` — `list_calendars` only
| Field | Type | Source |
|---|---|---|
| `id` | string | **synthesized** — the person's email (`x-google-identifier: true`) |
| `summary` | string | **synthesized** — the person's display name (`readOnly`) |
| `description` | string | **synthesized** (`readOnly`) |
| `timeZone` | string | **synthesized** (`readOnly`) |

No calendar record is authored. Per `calendar_schema.md` §"Serving the data": *"the adapter
synthesizes one implicit primary calendar per person (id = their email) from the addresses on
events."* See §3.3.

### `TimeSlot` — `suggest_time` only
| Field | Type | Notes |
|---|---|---|
| `start` | `DateOrDateTime` | |
| `end` | `DateOrDateTime` | |
| `startTime` | string | `deprecated` — use `start` |
| `endTime` | string | `deprecated` — use `end` |
| `durationMinutes` | int32 | `deprecated` — *"use `start` and `end` to compute duration instead"* |

### `Reminder`
`method` (string, **required** — `email` \| `popup`), `minutes` (int32, **required**).
**No source in the schema.** Input-only in practice: accepted by `create_event`/`update_event`,
echoed back, never present on a read.

### `GuestPermissions`
`guestsCanInviteOthers`, `guestsCanModify`, `guestsCanSeeGuests` — all boolean.
**No source.** Input-only, same as `Reminder`.

### `WorkingLocationProperties`
`type` (enum `WORKING_LOCATION_TYPE_UNSPECIFIED` \| `HOME_OFFICE` \| `CUSTOM_LOCATION`),
`customLocationLabel` (string, *"Required if type is `CUSTOM_LOCATION`"*).
**Never populated** — populated only when `eventType` is `WORKING_LOCATION`, and no authored event
is. Defined here for completeness of the input surface.

---

## 2. Enums

Every enum has an `_UNSPECIFIED` zero value aliasing to the default. **Accept it, don't error** —
same `_coerce_enum` treatment as the Gmail server (absorb empty / `*_UNSPECIFIED` / unrecognized →
default).

**`EventType`** — `list_events.eventType[]`, `Event.eventType`, `create_event.eventType`.
| Value | Google's own description | Our data |
|---|---|---|
| `EVENT_TYPE_UNSPECIFIED` | *"Treated as `DEFAULT`."* | — |
| `DEFAULT` | *"Regular event. Default value."* | **all 50** |
| `OUT_OF_OFFICE` | *"Out-of-office event."* | none |
| `FOCUS_TIME` | *"Focus-time event."* | none — **but see the trap below** |
| `WORKING_LOCATION` | *"Working location event."* | none |
| `BIRTHDAY` | *"Special all-day event with an annual recurrence."* | none |
| `FROM_GMAIL` | *"Event from Gmail. This type of event cannot be created."* | none |

**Trap — do not infer `FOCUS_TIME` from the subject line.** Two events are titled
`Focus block -- Q3 forecast` (EVT-0126) and `Focus block -- Verano board prep` (EVT-0139). Our
schema has **no event-type field**, so classifying them by title match would be the adapter
inventing data. Every event is `DEFAULT`. Consequence: `eventType` is a filter that can only ever
be satisfied by `DEFAULT`, and the default filter set
(`DEFAULT`, `OUT_OF_OFFICE`, `FOCUS_TIME`, `FROM_GMAIL`) already includes it — so the parameter is
accepted, honoured literally, and is a no-op unless the caller asks for a type we have none of.
(`eventType: ["FOCUS_TIME"]` correctly returns zero events.)

**`Availability`** — `Event.availability`, `create_event`, `update_event`.
| Value | Google's own description |
|---|---|
| `AVAILABILITY_UNSPECIFIED` | *"Default. Treated as `BUSY`."* |
| `AVAILABILITY_BUSY` | *"Blocks time on calendar."* |
| `AVAILABILITY_FREE` | *"Does not block time."* |

**Two values on the wire, three in storage.** See §5.2 — this is the first of two lossy collapses.

**`NotificationLevel`** — `create_event`, `update_event`, `delete_event`, `respond_to_event`.
| Value | Google's own description |
|---|---|
| `NOTIFICATION_LEVEL_UNSPECIFIED` | *"Default. Treated as `ALL`."* |
| `NONE` | *"No notifications."* |
| `EXTERNAL_ONLY` | *"External attendees only."* |
| `ALL` | *"All attendees."* |

**Accepted and ignored on all four write tools.** There is no mail transport in the mock. Worth
noting rather than silently swallowing: the parameter is validated (so a bad value is a clear
error) and then has no effect.

**`WorkingLocationType`** — `WORKING_LOCATION_TYPE_UNSPECIFIED` (*"Will be treated as
`HOME_OFFICE`"*) \| `HOME_OFFICE` \| `CUSTOM_LOCATION`. Never reached.

**Enum-shaped strings that are NOT declared enums.** Three fields carry a fixed value set in prose
but are typed `string` in the schema, so the wire accepts anything:
- `Event.status` — `confirmed` \| `tentative` \| `cancelled`. **Maps 1:1 to our `status`.**
- `Event.visibility` — `default` \| `public` \| `private`. **Lossy from our `sensitivity`**, §5.2.
- `Attendee.responseStatus` — `needsAction` \| `declined` \| `tentative` \| `accepted`.
  **Maps 1:1 to our `response_status`.**
- `Reminder.method` — `email` \| `popup`.
- `list_events.orderBy` — `default` \| `startTime` \| `startTimeDesc` \| `lastModified`.
- `list_events` response `accessRole` — `none` \| `freeBusyReader` \| `reader` \| `writer` \| `owner`.

---

## 3. READ tools

### 3.1 `list_events`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`
`title: "Lists calendar events in a given calendar."`

**Description** (verbatim — this is prompt text the model reads):
> Returns events on the given calendar matching all specified constraints. **Time constraints should
> not be specified unless requested by the user.** For open-ended keyword or topic-based searches on
> the primary calendar, **the `search_events` tool must be used instead**.

Two instructions worth keeping verbatim, because they shape which tool the model reaches for:
time bounds are opt-in, and open-ended keyword search is explicitly routed to `search_events`
(§3.5) even though `list_events` has a `fullText` parameter of its own. The division of labour:
`fullText` is for keyword *combined with* other constraints (a time window, a specific calendar, an
event type); `search_events` is for a bare bag of terms. Same matcher underneath — §6.4.

**Request:**
| Field | Type | Required | Default | Role |
|---|---|---|---|---|
| `calendarId` | string | no | primary | *"Email address - can be resolved using `list_calendars`"* |
| `startTime` | string | no | unbounded | *"lower bound… Must only be set when a specific timeframe is requested by the user. Must be an ISO 8601 timestamp less than `end_time`."* |
| `endTime` | string | no | unbounded | *"upper bound… Must only be set when a specific timeframe or a time in the past is requested"*, `> start_time` |
| `fullText` | string | no | `""` | *"Free-form case-insensitive search matching title, description, location, or attendees. Matches events containing all query terms verbatim (AND search)."* |
| `eventType` | string[] | no | `[DEFAULT, OUT_OF_OFFICE, FOCUS_TIME, FROM_GMAIL]` | *"If empty, only the following event types are returned"* |
| `eventTypeFilter` | string[] | no | — | **`deprecated`** — *"use `event_type` instead"* |
| `orderBy` | string | no | `default` | `default` (*"Unspecified, but deterministic ordering"*) \| `startTime` \| `startTimeDesc` \| `lastModified` |
| `pageSize` | int32 | no | **100**, max **250** | *"Recommended: `10`."* |
| `pageToken` | string | no | `""` | opaque cursor |
| `timeZone` | string | no | calendar's zone | *"used to resolve timezone-less dates"* |

Note how much narrower this is than Gmail's `query`: **ten typed parameters, no operator syntax, no
negation, no boolean grouping.** `fullText` is the only free-text surface and its semantics are
fully specified by its own description — case-insensitive, all terms, verbatim, AND.

```json
{
  "name": "list_events",
  "arguments": {
    "startTime": "2026-07-22T00:00:00Z",
    "endTime": "2026-07-23T00:00:00Z",
    "orderBy": "startTime",
    "pageSize": 10
  }
}
```

**Response — a calendar-shaped envelope with the events inside it:**
`{ events: Event[], nextPageToken?: string, summary, description, timeZone, updated, accessRole, defaultReminders }`

This is the single most surprising shape in the capture. `list_events` does not return
`{events: [...]}`; it returns **the calendar resource, with `events[]` as one of its fields.** Six of
its eight top-level fields describe the *calendar*, not the results — and none of them has an
authored source. They are synthesized from the resolved `calendarId` (§3.3).

The window above resolves to exactly three events. Verified against the dataset using **UTC
instants** and **overlap** semantics (`end > startTime AND start < endTime`):

```json
{
  "summary": "Ellie Ashworth",
  "description": "Primary calendar for Ellie Ashworth",
  "timeZone": "Europe/London",
  "accessRole": "owner",
  "defaultReminders": [],
  "events": [
    {
      "id": "EVT-0126",
      "summary": "Focus block -- Q3 forecast",
      "description": "",
      "location": "",
      "start": { "dateTime": "2026-07-22T08:00:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T09:00:00+01:00", "timeZone": "Europe/London" },
      "status": "confirmed",
      "eventType": "DEFAULT",
      "availability": "AVAILABILITY_BUSY",
      "visibility": "private",
      "transparency": "opaque",
      "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "attendees": [
        {
          "email": "ellie.ashworth@maplesoftware.net",
          "displayName": "Ellie Ashworth",
          "responseStatus": "accepted",
          "organizer": true,
          "self": true
        }
      ],
      "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0126"
    },
    {
      "id": "EVT-0113",
      "summary": "Loch Financial <> Maple weekly sync",
      "description": "Standing sync through the Q3 expansion. Integration progress and commercial track.",
      "location": "Google Meet",
      "start": { "dateTime": "2026-07-22T10:00:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T10:30:00+01:00", "timeZone": "Europe/London" },
      "status": "confirmed",
      "eventType": "DEFAULT",
      "availability": "AVAILABILITY_BUSY",
      "visibility": "default",
      "transparency": "opaque",
      "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=WE"],
      "conferenceUrl": "https://meet.google.com/uk-loch-113",
      "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "attendees": [
        { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true },
        { "email": "fiona.sinclair@lochfinancial.co.uk", "displayName": "Fiona Sinclair", "responseStatus": "accepted" },
        { "email": "callum.reid@lochfinancial.co.uk", "displayName": "Callum Reid", "responseStatus": "accepted" }
      ],
      "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0113"
    },
    {
      "id": "EVT-0124",
      "summary": "Deal desk office hours",
      "description": "Open session for pricing and contract structuring questions.",
      "location": "Google Meet",
      "start": { "dateTime": "2026-07-22T16:00:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T17:00:00+01:00", "timeZone": "Europe/London" },
      "status": "confirmed",
      "eventType": "DEFAULT",
      "availability": "AVAILABILITY_FREE",
      "visibility": "default",
      "transparency": "transparent",
      "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=WE"],
      "conferenceUrl": "https://meet.google.com/int-dealdesk",
      "organizer": { "displayName": "Maple Deal Desk", "email": "deals@maplesoftware.net" },
      "creator":   { "displayName": "Maple Deal Desk", "email": "deals@maplesoftware.net" },
      "attendees": [
        { "email": "deals@maplesoftware.net", "displayName": "Maple Deal Desk", "responseStatus": "accepted", "organizer": true },
        { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "tentative", "self": true }
      ],
      "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0124"
    }
  ]
}
```

Things to read off that response:
- **No `colorId`, `created`, `updated`, `guestPermissions`, `overrideReminders`.** Omitted, not
  nulled — they have no source (§5.3).
- **EVT-0126 shows `visibility: "private"`** from `sensitivity: "private"`, and **`description:
  ""`** — an authored empty string, present not dropped, per the schema's empty-value convention.
- **EVT-0124 is `AVAILABILITY_FREE` / `transparency: "transparent"`** from `show_as: "free"`, and
  its organizer is the deal-desk shared mailbox, so **no `self` on the organizer** — Ellie is a
  `tentative` attendee on someone else's event.
- **Both recurring events return their authored first instance**, with `recurrence` as a
  single-element array. No expansion. See §6.3.
- **`nextPageToken` is absent** — 3 results, `pageSize: 10`. Present *only* when more remain.

#### 3.1.1 Time-range semantics — the one thing that must not be got wrong

`startTime`/`endTime` are ISO 8601 **instants**. Storage is **local wall-clock with no offset plus a
separate IANA zone**. Comparing the caller's instant against the stored string is wrong, and it is
wrong in both directions on this dataset:

| Window | Naive string compare | Correct UTC compare |
|---|---|---|
| `2026-07-07T08:00:00Z` → `2026-07-07T23:59:59Z` | `[EVT-0135]` | `[]` |
| `2026-07-09T16:00:00Z` → `2026-07-09T23:59:59Z` | `[]` | `[EVT-0136]` |

A false positive and a false negative, caused by the two non-London events (EVT-0135
Asia/Singapore 09:00 = 01:00Z; EVT-0136 America/New_York 15:00 = 19:00Z). **Filter on a derived UTC
instant, never on the stored string.**

Worse, the failure is invisible to a sort-order test: ordering all 50 events by local string
happens to produce the same sequence as ordering them by UTC. A test that only checks `orderBy:
startTime` passes while range filtering is silently broken — the same class of false confidence as
the Gmail label-ordering bug, where five set-based assertions passed while array order was
corrupted.

**Overlap, not containment.** An event is in range if `end > startTime AND start < endTime`. A
09:00–10:00 meeting is returned by a window starting 09:30. Containment semantics would drop it.

**`orderBy` values, resolved:**
| Value | Implementation |
|---|---|
| `default` | *"Unspecified, but deterministic"* — ascending UTC start, `id` as tie-break |
| `startTime` | ascending derived-UTC start |
| `startTimeDesc` | descending derived-UTC start |
| `lastModified` | **no source** — no `updated` field exists. Falls back to `default` and is reported (§6.5) |

---

### 3.2 `get_event`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`
`title: "Returns a single event on the specified calendar."`

**Description:** *"Returns a single event on the given calendar."*

The shortest description in the capture — one line, no disambiguation from `list_events`, no example
prompts. (Contrast Gmail's `get_message`, most of whose description is "don't use this for whole
threads, use `get_thread`.")

**Request:**
| Field | Type | Required | Default |
|---|---|---|---|
| `eventId` | string | **yes** | — |
| `calendarId` | string | no | primary |

**No filters** — pure ID lookup. All narrowing happens upstream in `list_events`/`search_events`.

```json
{ "name": "get_event", "arguments": { "eventId": "EVT-0103" } }
```

**Response:** a **bare `Event`** at the top level — not wrapped, not in an array, no calendar
envelope. Note the asymmetry with `list_events`, which wraps its events in a calendar resource.

Using EVT-0103, the richest read in the dataset — attachments, an online meeting, five attendees,
and a mixed RSVP set:

```json
{
  "id": "EVT-0103",
  "summary": "Verano -- FX rounding root cause walkthrough",
  "description": "Engineering walks Verano through the reconciliation event stream defect and the mechanism behind the drift.",
  "location": "Google Meet",
  "start": { "dateTime": "2026-06-08T14:00:00+01:00", "timeZone": "Europe/London" },
  "end":   { "dateTime": "2026-06-08T15:00:00+01:00", "timeZone": "Europe/London" },
  "status": "confirmed",
  "eventType": "DEFAULT",
  "availability": "AVAILABILITY_BUSY",
  "visibility": "default",
  "transparency": "opaque",
  "conferenceUrl": "https://meet.google.com/uk-verano-103",
  "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "attendees": [
    { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true },
    { "email": "rohan.mehta@maplesoftware.net", "displayName": "Rohan Mehta", "responseStatus": "accepted" },
    { "email": "ravi.shah@veranotravel.com", "displayName": "Ravi Shah", "responseStatus": "accepted" },
    { "email": "amara.nwosu@veranotravel.com", "displayName": "Amara Nwosu", "responseStatus": "accepted" },
    { "email": "charles.pemberton@veranotravel.com", "displayName": "Charles Pemberton", "responseStatus": "declined" }
  ],
  "attachments": [
    {
      "fileUrl": "https://drive.google.com/file/d/drive-transcript-EVT-0103/view",
      "title": "Transcript - Verano FX rounding root cause walkthrough"
    }
  ],
  "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0103"
}
```

Note `fileUrl` is **synthesized** from `file_id` (§5.3), `mime_type` is **dropped** (no wire field),
and `recurrence` is **absent** rather than null — `recurrence: null` in storage means "one-off", and
the capture says *"Omitted for single events."*

**The all-day case** (EVT-0107) is the only other read shape worth writing out, because
`DateOrDateTime` switches keys:

```json
{
  "id": "EVT-0107",
  "summary": "Verano board meeting (external -- not attending)",
  "description": "Marker only. Verano's board reviews the payouts expansion. Deadline for the committed ISS-201 date.",
  "location": "",
  "start": { "date": "2026-07-24T00:00:00Z", "timeZone": "Europe/London" },
  "end":   { "date": "2026-07-25T00:00:00Z", "timeZone": "Europe/London" },
  "status": "confirmed",
  "eventType": "DEFAULT",
  "availability": "AVAILABILITY_FREE",
  "visibility": "default",
  "transparency": "transparent",
  "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "attendees": [
    { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true }
  ],
  "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0107"
}
```

`date` not `dateTime`, no offset, and **`end` is the exclusive next-day midnight** — which is what
the schema authored (`2026-07-25T00:00:00`) and what iCalendar means by an all-day end. No
adjustment needed; just don't "helpfully" subtract a day.

---

### 3.3 `list_calendars`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`
`title: "Returns the calendars on the user's calendar list."`

**Description:**
> Returns the calendars this user has access to (their calendar list). Use this tool to resolve
> calendar identifying data (for example, 'my family calendar') into its corresponding
> `calendar_id` (email identifier)

**Request:** `pageSize` (int32, default `100`, max `250`), `pageToken` (string).
**No query parameter — no filtering at all.** Structurally identical to Gmail's `list_labels`: a
pure discovery tool whose entire job is turning a human phrase into an ID the other tools accept.

```json
{ "name": "list_calendars", "arguments": {} }
```

**Response:** `{ calendars: CalendarListItem[], nextPageToken?: string }`

**Every field of every item is synthesized** — this is the tool with the least schema backing and
the clearest mandate to invent, straight from `calendar_schema.md`: *"We author no calendar
record… the adapter synthesizes one implicit primary calendar per person (id = their email) from
the addresses on events."*

The address set is fully determined by the data. All 14 distinct addresses across
`organizer.email` + `attendees[].email`, with the display name taken from the `Person` object (each
address has exactly one name across all 50 events — verified, no collisions):

| # | `id` (address) | `summary` (name) | organizer of | attendee on |
|---|---|---|---|---|
| 1 | `ellie.ashworth@maplesoftware.net` | Ellie Ashworth | 34 | **50** |
| 2 | `priya.deshpande@maplesoftware.net` | Priya Deshpande | 11 | 22 |
| 3 | `rohan.mehta@maplesoftware.net` | Rohan Mehta | 1 | 18 |
| 4 | `amara.nwosu@veranotravel.com` | Amara Nwosu | 0 | 5 |
| 5 | `helen.voss@thornburyretail.co.uk` | Helen Voss | 0 | 5 |
| 6 | `fiona.sinclair@lochfinancial.co.uk` | Fiona Sinclair | 0 | 4 |
| 7 | `callum.reid@lochfinancial.co.uk` | Callum Reid | 0 | 4 |
| 8 | `ravi.shah@veranotravel.com` | Ravi Shah | 0 | 3 |
| 9 | `charles.pemberton@veranotravel.com` | Charles Pemberton | 0 | 3 |
| 10 | `ian.fletcher@thornburyretail.co.uk` | Ian Fletcher | 0 | 3 |
| 11 | `morag.bell@lochfinancial.co.uk` | Morag Bell | 0 | 2 |
| 12 | `deals@maplesoftware.net` | Maple Deal Desk | 2 | 2 |
| 13 | `invites@fintechsummit.example` | FinTech Summit London | 2 | 2 |
| 14 | `priyanka.rao@camdenmobility.co.uk` | Priyanka Rao | 0 | 1 |

**Ellie is an attendee on all 50 events** and organizer on 34 — she is unambiguously the account
owner, so `"primary"` aliases to her address and `Principal.self`/`Attendee.self` are true for her.

```json
{
  "calendars": [
    {
      "id": "ellie.ashworth@maplesoftware.net",
      "summary": "Ellie Ashworth",
      "description": "Primary calendar for Ellie Ashworth",
      "timeZone": "Europe/London"
    },
    {
      "id": "priya.deshpande@maplesoftware.net",
      "summary": "Priya Deshpande",
      "description": "Calendar for Priya Deshpande",
      "timeZone": "Europe/London"
    },
    {
      "id": "deals@maplesoftware.net",
      "summary": "Maple Deal Desk",
      "description": "Calendar for Maple Deal Desk",
      "timeZone": "Europe/London"
    }
  ]
}
```

**Whether to return all 14 or only the 3 internal `maplesoftware.net` addresses is an open
architectural decision** — the real `list_calendars` returns calendars the user has *access to*,
which would not normally include a customer's personal calendar. Flagged in §7.

**`timeZone` is synthesized as `Europe/London` for every calendar.** There is no per-person zone in
the schema; 48 of 50 events are `Europe/London` and the account is a UK one. The two non-London
events carry their own zone at the *event* level, which is where the wire reads it from anyway — so
this synthesis is not doing load-bearing work. Deriving it per person (e.g. mode of their events'
zones) would make EVT-0135's Singapore zone leak into Priya's calendar record, which is worse.

---

### 3.4 `suggest_time`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`
`title: "Suggests time periods across one or more calendars."`

**Description:** *"Suggests time periods across one or more calendars."*
Request message name: `SuggestTime`.

This is the tool with **no Gmail analogue whatsoever** — it is a computation over the data, not a
retrieval from it. It is also the tool `calendar_schema.md` anticipated: *"`freebusy` comes from
each event's `show_as` + `start`/`end`."*

**Request:**
| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `attendeeEmails` | string[] | **yes** | — | *"Attendee emails to find free time for."* |
| `startTime` | string | **yes** | — | *"Query interval start (ISO 8601)."* |
| `endTime` | string | **yes** | — | *"Query interval end (ISO 8601)."* |
| `durationMinutes` | int32 | no | **30** | *"Min duration of free slot in minutes."* |
| `timeZone` | string | no | offset of `start_time`, else user's primary | IANA ID |
| `preferences` | `Preferences` | no | — | |

**`Preferences`** (an input-only `$defs` type, the only one in the capture):
| Field | Type | Default | Notes |
|---|---|---|---|
| `startHour` | string | — | *"Preferred start hour as `\"HH:mm\"` (24-hour format)"* |
| `endHour` | string | — | *"Preferred end hour as `\"HH:mm\"`"* |
| `excludeWeekends` | boolean | — | |
| `pageSize` | int32 | **5** | *"Max number of slots to return."* — **`pageSize` lives inside `preferences`, not at the top level.** No `pageToken` anywhere; this tool is not paginated |

```json
{
  "name": "suggest_time",
  "arguments": {
    "attendeeEmails": [
      "ellie.ashworth@maplesoftware.net",
      "priya.deshpande@maplesoftware.net"
    ],
    "startTime": "2026-07-22T09:00:00+01:00",
    "endTime": "2026-07-22T18:00:00+01:00",
    "durationMinutes": 30,
    "timeZone": "Europe/London",
    "preferences": { "startHour": "09:00", "endHour": "18:00", "excludeWeekends": true }
  }
}
```

**Response:** `{ timeSlots: TimeSlot[] }` — **no pagination field of any kind.**

The busy set for that window, computed from the dataset: exactly one blocking event —
**EVT-0113 10:00–10:30** (`show_as: busy`). Three other events touch 22 July but do not block:
EVT-0126 08:00–09:00 is outside the preferred hours, and EVT-0124 16:00–17:00 is `show_as: free`.
Free intervals ≥ 30 min: **09:00–10:00** and **10:30–18:00**. Sliced into `durationMinutes`
candidates and capped at the default `pageSize: 5`:

```json
{
  "timeSlots": [
    {
      "start": { "dateTime": "2026-07-22T09:00:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T09:30:00+01:00", "timeZone": "Europe/London" }
    },
    {
      "start": { "dateTime": "2026-07-22T09:30:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T10:00:00+01:00", "timeZone": "Europe/London" }
    },
    {
      "start": { "dateTime": "2026-07-22T10:30:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T11:00:00+01:00", "timeZone": "Europe/London" }
    },
    {
      "start": { "dateTime": "2026-07-22T11:00:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T11:30:00+01:00", "timeZone": "Europe/London" }
    },
    {
      "start": { "dateTime": "2026-07-22T11:30:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-22T12:00:00+01:00", "timeZone": "Europe/London" }
    }
  ]
}
```

The three deprecated `TimeSlot` fields (`startTime`, `endTime`, `durationMinutes`) are omitted.
Emitting them would be defensible for compatibility but they are marked `deprecated` in the
capture, and the Gmail server's precedent is to reproduce the real surface rather than pad it.

**Four semantics this tool needs pinned down, all of which our data actually exercises:**

1. **What counts as busy.** `show_as: busy` blocks. `show_as: free` does not (10 events). **`show_as:
   tentative` (3 events: EVT-0106, EVT-0136, EVT-0149) has no wire equivalent** and must pick a
   side — see §5.2. Treating it as blocking is the conservative read and matches
   `AVAILABILITY_UNSPECIFIED → BUSY`.
2. **Cancelled events must not block.** EVT-0112 is `status: cancelled` and also `show_as: free`, so
   on *this* dataset the two rules agree and either alone gets the right answer — which is exactly
   why the check has to be explicit. A future cancelled-but-busy event would otherwise block time
   on a meeting that isn't happening.
3. **Slot granularity is unspecified.** The capture says "min duration of free slot", so a free
   interval longer than `durationMinutes` could be returned whole (2 maximal slots here) or sliced
   (5 candidates, above). Google returns discrete suggestions; the example follows that. **This is
   a decision to record in the architecture doc, not a fact from the capture.**
4. **Recurrence changes the answer.** See §6.3 — this is where non-expansion is not merely
   incomplete but *wrong*.

---

### 3.5 `search_events`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`
`title: "Searches for events on the user's primary calendar."`

**Description** — this is prompt text the model reads, and it is what tells the model to reduce a
question to search terms before calling:
> Searches events on the user's primary calendar using free text search terms. Free text search
> terms match against the event's title, description, location, and the display names and email
> addresses of its organizer and attendees. Terms are case-insensitive and an event must contain
> **all** of them to match. For searches that also need a time range, a specific calendar, or a
> particular event type, use `list_events` instead.

Request message name: `SearchEvents`.

This is the free-text retrieval tool. The matching rule is the **`q` semantics of the v3 REST
`events.list` endpoint**, which is where every field in the match scope below comes from
(§3.5.1). It is *lexical*, not conceptual: terms are matched as substrings, all of them must be
present, and there is no synonym, stemming, or embedding step anywhere in the path.

**Request:**
| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `query` | string | **yes** | — | *"Free text search terms to find events that match these terms"* — case-insensitive, all terms must match (AND) |
| `pageSize` | int32 | no | **unstated** | *"Maximum number of entries returned on one result page."* — no default, no max given, unlike `list_events` |
| `pageToken` | string | no | `""` | opaque cursor |

**No `calendarId`.** Primary calendar only — the parameter does not exist. **No time bounds
either**: this is pure keyword retrieval, which is why `list_events`'s description routes
open-ended searches here and keeps its own time parameters opt-in. Anything beyond a bag of terms
belongs on `list_events`, which has the typed parameters for it.

```json
{ "name": "search_events", "arguments": { "query": "Verano board", "pageSize": 10 } }
```

**Response:** `{ events: Event[], nextPageToken?: string }`

**A different envelope from `list_events` for identical content** — no `summary`, `timeZone`,
`accessRole`, or `defaultReminders`. Two response builders are needed over one `Event` builder.

#### 3.5.1 The match scope — exactly which fields `query` reads

The v3 REST `events.list` `q` parameter is documented against a **named list of fields**, and that
list is the specification for this tool. Mapped onto our storage:

| REST `q` field | Our storage field | Authored? |
|---|---|---|
| `summary` | `subject` | 50/50 |
| `description` | `description` | 50/50 (some `""`) |
| `location` | `location` | 50/50 (some `""`) |
| attendee's `displayName` | `attendees[].name` | 50/50, 1–5 per event |
| attendee's `email` | `attendees[].email` | 50/50 |
| organizer's `displayName` | `organizer.name` | 50/50 |
| organizer's `email` | `organizer.email` | 50/50 |
| `workingLocationProperties.officeLocation.{buildingId,deskId,label}` | — | **nothing authored** |
| `workingLocationProperties.customLocation.label` | — | **nothing authored** |

Seven of the eleven have a source; the four working-location fields have none, because no event is
a working-location event (§2 — `eventType` is the constant `DEFAULT`). REST also documents that `q`
matches *predefined keywords against display-title translations* of working-location,
out-of-office, and focus-time events — searching `"Office"` or `"Bureau"` returns working-location
events. **Not implementable and not needed**: zero events of those types exist, so the correct
result for those keywords is the empty set, which plain substring matching already returns. The one
event that *reads* like a focus-time event, EVT-0126 (*"Focus block -- Q3 forecast"*), is an
ordinary `DEFAULT` event and is found by the literal term `focus`, not by keyword expansion.

**Concatenate, don't OR eleven predicates.** Build one lowercased match corpus per event from the
seven sourced fields, then require every term to be a substring of it. Measured: the whole corpus
across all 50 events is **13,055 characters** — the entire index is smaller than one transcript.

Two consequences of including the organizer, both real on this dataset:
- The organizer is in `attendees[]` on **all 50** events (the validator enforces it, `validate_email_calendar.py`
  lines 540-544). So on this dataset adding organizer name/email to the corpus **changes no result**
  — I measured 12 queries across both scopes and the match sets were identical. It still belongs in
  the corpus: a `create_event` call can produce an event where they diverge.
- A term that only appears in an *address* still matches. `q=priya` returns **23** events — every
  event Priya attends — because `priya.raman@maplesoftware.net` is in the corpus. That is correct
  REST behaviour and it is the reason a name search works at all without any name index.

**Cancelled events match.** EVT-0112 (*"Thornbury weekly check-in"*, `status: cancelled`) is one of
the 7 hits for `q=Thornbury`. There is no `showDeleted` parameter on this tool, and per §4.3
cancelled events stay visible with `status: "cancelled"` as the signal. The caller reads the enum.

`"Verano board"` as an AND search over that corpus matches 6 events in the dataset — EVT-0101,
EVT-0105, EVT-0107, EVT-0125, EVT-0139, EVT-0149. Abridged to 2:

```json
{
  "events": [
    {
      "id": "EVT-0125",
      "summary": "Verano board prep -- internal",
      "description": "Internal preparation for the Verano board review.",
      "location": "Google Meet",
      "start": { "dateTime": "2026-07-20T11:00:00+01:00", "timeZone": "Europe/London" },
      "end":   { "dateTime": "2026-07-20T12:00:00+01:00", "timeZone": "Europe/London" },
      "status": "confirmed",
      "eventType": "DEFAULT",
      "availability": "AVAILABILITY_BUSY",
      "visibility": "private",
      "transparency": "opaque",
      "conferenceUrl": "https://meet.google.com/int-verano-prep",
      "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "attendees": [
        { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true }
      ],
      "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0125"
    },
    {
      "id": "EVT-0107",
      "summary": "Verano board meeting (external -- not attending)",
      "description": "Marker only. Verano's board reviews the payouts expansion. Deadline for the committed ISS-201 date.",
      "location": "",
      "start": { "date": "2026-07-24T00:00:00Z", "timeZone": "Europe/London" },
      "end":   { "date": "2026-07-25T00:00:00Z", "timeZone": "Europe/London" },
      "status": "confirmed",
      "eventType": "DEFAULT",
      "availability": "AVAILABILITY_FREE",
      "visibility": "default",
      "transparency": "transparent",
      "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
      "attendees": [
        { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true }
      ],
      "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0107"
    }
  ]
}
```

#### 3.5.2 `search_events` vs `list_events(fullText=…)`

Both are free-text matching over the same corpus with the same AND-over-terms rule. They differ in
**scope and envelope, not in matching**:

| | `search_events(query=…)` | `list_events(fullText=…)` |
|---|---|---|
| Calendar | **primary only** — no `calendarId` parameter | any, via `calendarId` |
| Time bounds | **none** | `startTime` / `endTime` |
| Event type | **all** — no parameter | `eventType[]` |
| Ordering | server default | `orderBy`, 4 values |
| Envelope | `{ events, nextPageToken? }` | full calendar resource + `events[]` (§3.1) |
| `pageSize` | no documented default or max | default 100, max 250 |

So: **one match predicate, two entry points.** Implement the corpus builder and the term matcher
once; `search_events` is the thin path (no filters, thin envelope), `list_events` is the wide one.
`list_events`'s own description routes open-ended keyword asks here — honour that, but do not
duplicate the matcher.

**Measured matching behaviour on the dataset** (full 7-field corpus, terms ANDed):

| Query | Matches | Events |
|---|---|---|
| `reconciliation` | 5 | EVT-0102, 0103, 0104, 0131, 0134 |
| `Verano` | 10 | EVT-0101, 0102, 0103, 0104, 0105, 0106, 0107, 0125, 0139, 0149 |
| `Loch` | 6 | EVT-0113, 0114, 0115, 0116, 0117, 0141 |
| `Thornbury` | 7 | EVT-0108, 0109, 0110, 0111, **0112 (cancelled)**, 0127, 0144 |
| `review` | 8 | EVT-0101, 0106, 0107, 0116, 0119, 0129, 0133, 0138 |
| `sync` | 5 | EVT-0102, 0113, 0123, 0135, 0136 |
| `priya` | 23 | matches on `attendees[].email`, not on any title |
| `forecast` | 1 | EVT-0126 |
| `thames` | 2 | EVT-0132, EVT-0143 — matches `location: "Maple London -- Room Thames"` |
| `Verano board` (AND) | 6 | EVT-0101, 0105, 0107, 0125, 0139, 0149 |
| `Loch webhook` (AND) | 1 | EVT-0115 |
| `Maple Deal Desk` (AND) | 2 | matches the organizer of EVT-0124 |

Two of those rows are the ones worth writing a test against, because they exercise a field a naive
title-only implementation would miss: `priya` (23, address-only) and `thames` (2, location-only). A
title-only matcher returns **0** for both and still passes every `Verano`-style test.

**What lexical matching does not do**, stated plainly so a task author doesn't write a rubric
against it: `"who did I meet about the FX drift"` returns **nothing**. EVT-0103 is *"Verano — FX
rounding root cause walkthrough"*; `drift`, `who`, `did`, `I`, `meet`, and `about` are not all
present in its corpus, so the AND fails. The term-bag must overlap the authored words verbatim. A
model that has read the tool description will pre-reduce a question to terms (`FX rounding`) — that
is the model's job, and the description says so.

**Transcripts are out of scope for search.** The documented `q` field list covers title,
description, location, organizer, and attendees — **not** attachment content. Our 8 transcripts
(5,898 chars, the largest text in the dataset) are therefore **unreachable through any calendar
tool**, reachable only by following `attachments[].fileUrl` to a Drive-like server. See §7.

---

## 4. WRITE tools

Four write tools. All four mutate the in-process store only; the JSON on disk is never touched —
the same arrangement as the Gmail server, where `/data` is mounted `:ro` and mutations live and die
with the container. **A verifier must observe an effect through a read tool**, not by inspecting
`$DATA_DIR`.

The write surface here is meaningfully larger than Gmail's. Gmail's 8 write tools were almost all
label mutations over an existing array. These four **create, mutate, and destroy whole records** —
and `create_event` has to invent every field the schema requires but the request does not carry
(`iCal_uid`, `body_preview`, `has_attachments`, `source`).

### 4.1 `create_event`

`readOnlyHint: false` · `destructiveHint: false` · **`idempotentHint: false`** · `openWorldHint: false`
`title: "Creates a calendar event."`

**Description:** *"Creates an event on the given calendar."* Request message: `CreateEvent`.

**Request** — `summary`, `startTime`, `endTime` **required**:
| Field | Type | Required | Notes |
|---|---|---|---|
| `summary` | string | **yes** | *"Title."* |
| `startTime` | string | **yes** | *"ISO 8601, for example `2026-04-30T10:00:00Z`"* |
| `endTime` | string | **yes** | *"ISO 8601, for example `2026-04-30T11:00:00Z`"* |
| `calendarId` | string | no | default primary |
| `description` | string | no | *"Can contain HTML."* |
| `location` | string | no | |
| `allDay` | boolean | no | *"If true, start/end times are treated as midnight."* |
| `timeZone` | string | no | *"**Overrides offsets in `start_time` and `end_time`.**"* |
| `attendees` | `Attendee[]` | no | *"For events created on the user's primary calendar with at least one other attendee, **the current user will automatically be added as an attendee if not already included**."* |
| `attendeeEmails` | string[] | no | **`deprecated`** — *"use `attendees` instead"* |
| `availability` | enum | no | |
| `visibility` | string | no | |
| `eventType` | enum | no | |
| `colorId` | string | no | |
| `recurrenceData` | string[] | no | *"`RRULE`, `RDATE`, or `EXDATE` strings"* — **note the name: `recurrenceData` on input, `recurrence` on output** |
| `addGoogleMeetUrl` | boolean | no | default `false` |
| `googleMeetUrl` | string | no | *"**Overrides `add_google_meet_url`.**"* |
| `attachments` | `Attachment[]` | no | `fileUrl` required per item |
| `overrideReminders` | `Reminder[]` | no | |
| `guestPermissions` | `GuestPermissions` | no | |
| `notificationLevel` | enum | no | accepted and ignored (§2) |
| `workingLocationProperties` | — | no | *"if `eventType` is `WORKING_LOCATION`"* |

```json
{
  "name": "create_event",
  "arguments": {
    "summary": "Verano -- expansion decision follow-up",
    "startTime": "2026-08-11T14:00:00",
    "endTime": "2026-08-11T15:00:00",
    "timeZone": "Europe/London",
    "description": "Follow-up on the board outcome and the ISS-201 commitment.",
    "attendees": [
      { "email": "amara.nwosu@veranotravel.com", "displayName": "Amara Nwosu" },
      { "email": "rohan.mehta@maplesoftware.net", "displayName": "Rohan Mehta" }
    ],
    "addGoogleMeetUrl": true,
    "availability": "AVAILABILITY_BUSY"
  }
}
```

**Response:** a **full `Event`** — the created record as it would now read back. Not `{}`, not just
an ID.

```json
{
  "id": "EVT-0151",
  "summary": "Verano -- expansion decision follow-up",
  "description": "Follow-up on the board outcome and the ISS-201 commitment.",
  "location": "",
  "start": { "dateTime": "2026-08-11T14:00:00+01:00", "timeZone": "Europe/London" },
  "end":   { "dateTime": "2026-08-11T15:00:00+01:00", "timeZone": "Europe/London" },
  "status": "confirmed",
  "eventType": "DEFAULT",
  "availability": "AVAILABILITY_BUSY",
  "visibility": "default",
  "transparency": "opaque",
  "conferenceUrl": "https://meet.google.com/gen-evt-0151",
  "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "attendees": [
    { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true },
    { "email": "amara.nwosu@veranotravel.com", "displayName": "Amara Nwosu", "responseStatus": "needsAction" },
    { "email": "rohan.mehta@maplesoftware.net", "displayName": "Rohan Mehta", "responseStatus": "needsAction" }
  ],
  "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0151"
}
```

Six decisions visible in that response, each of which the request did not specify:

- **`id: "EVT-0151"`** — allocated by continuing the authored `EVT-01NN` sequence (max is EVT-0150).
  Same approach as the Gmail server's `next_thread_id` / `allocate_attachment_ids`.
- **Ellie was auto-added as an attendee, first, with `organizer: true` and
  `responseStatus: "accepted"`** — mandated by the `attendees` description, and it also satisfies
  the schema's cross-record rule that *"the organizer also appears in `attendees[]`"*.
- **The two supplied attendees default to `responseStatus: "needsAction"`** — the `Attendee`
  description calls it *"recommended for new events"*.
- **`status` defaults to `confirmed`** — no request field sets it; the `Event.status` description
  says `confirmed` is the default.
- **`conferenceUrl` was minted** because `addGoogleMeetUrl: true`, and storage now needs an
  `online_meeting` object — so the write must also set `is_online_meeting: true` and a
  `conference_id`, because the validator enforces that pairing.
- **`body_preview`, `has_attachments`, `source`, `iCal_uid` are set on the stored record and appear
  nowhere on the wire.** `iCal_uid` must be unique and contain `@`; `body_preview` mirrors
  `description` truncated to 255. A create that skips them writes a record that would fail the
  dataset validator, which is the load-time integrity contract this server is built on.

**`allDay` + `timeZone` interaction.** `allDay: true` means *"start/end times are treated as
midnight"*, and `timeZone` *"overrides offsets in `start_time` and `end_time`"*. So an all-day
create must floor both bounds to midnight in the resolved zone and store date-only midnights —
`is_all_day: true` with values ending `T00:00:00`, which the validator explicitly checks.

### 4.2 `update_event`

`readOnlyHint: false` · `destructiveHint: false` · **`idempotentHint: true`** · `openWorldHint: false`
`title: "Updates a calendar event."`

**Description:** *"Updates an event on the given calendar."*
Request message: *"Request message for UpdateEvent. **Fields that are not set will not be
updated.**"*

That last sentence is the whole contract: this is a **sparse patch, not a replace.** Distinguishing
"absent" from "explicitly empty" matters — `description: ""` must clear the description while an
absent `description` must leave it alone. In Python that means a sentinel default, not `None`,
because `None` is a legitimate incoming value for the nullable storage fields.

**Request** — `eventId` **required**, everything else optional:
| Field | Type | Notes |
|---|---|---|
| `eventId` | string | **required** |
| `calendarId` | string | |
| `summary` / `description` / `location` | string | new values |
| `startTime` | string | *"**Preserves duration if updating only start.**"* |
| `endTime` | string | |
| `allDay` | boolean | *"If set, `start_time`/`end_time` **must also be provided**."* |
| `timeZone` | string | overrides offsets |
| `availability` | enum | |
| `visibility` | string | |
| `colorId` | string | |
| `addedAttendees` | `Attendee[]` | **add**, not replace |
| `addedAttendeeEmails` | string[] | **`deprecated`** |
| `removedAttendeeEmails` | string[] | *"as email addresses"* |
| `addedAttachments` | `Attachment[]` | **add** |
| `removedAttachmentFileUrls` | string[] | remove **by `fileUrl`** |
| `overrideReminders` | `Reminder[]` | *"If set, **replaces all** existing reminders"* |
| `addGoogleMeetUrl` | boolean | |
| `googleMeetUrl` | string | *"Overrides the value of `addGoogleMeetUrl`."* |
| `guestPermissions` | `GuestPermissions` | |
| `notificationLevel` | enum | ignored |

**No `status` field, and no `recurrence` field.** An event's `status` cannot be changed by
`update_event` — `delete_event` is the only path to `cancelled` (§4.3). And a recurrence rule can
be **set at create time but never edited**, an asymmetry worth reproducing rather than smoothing:
`create_event` has `recurrenceData`, `update_event` has nothing.

Three mutation styles coexist here, and conflating them is the easy bug:
- **replace** — `summary`, `description`, `location`, times, enums
- **add/remove pairs** — attendees, attachments
- **wholesale replace of a collection** — `overrideReminders`

```json
{
  "name": "update_event",
  "arguments": {
    "eventId": "EVT-0149",
    "startTime": "2026-08-06T11:00:00",
    "timeZone": "Europe/London",
    "availability": "AVAILABILITY_BUSY",
    "addedAttendees": [
      { "email": "rohan.mehta@maplesoftware.net", "displayName": "Rohan Mehta" }
    ]
  }
}
```

**Response:** the full updated `Event`. EVT-0149 is authored `2026-08-06T10:00:00`–`10:30:00`,
`status: tentative`, `show_as: tentative`, with Ellie and Amara. Moving the start by an hour with
**no `endTime` given preserves the 30-minute duration** — `11:00`–`11:30`, not `11:00`–`10:30`:

```json
{
  "id": "EVT-0149",
  "summary": "Verano -- expansion decision checkpoint",
  "description": "Checkpoint on the expansion decision following the board review.",
  "location": "Google Meet",
  "start": { "dateTime": "2026-08-06T11:00:00+01:00", "timeZone": "Europe/London" },
  "end":   { "dateTime": "2026-08-06T11:30:00+01:00", "timeZone": "Europe/London" },
  "status": "tentative",
  "eventType": "DEFAULT",
  "availability": "AVAILABILITY_BUSY",
  "visibility": "default",
  "transparency": "opaque",
  "conferenceUrl": "https://meet.google.com/uk-verano-149",
  "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "attendees": [
    { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true },
    { "email": "amara.nwosu@veranotravel.com", "displayName": "Amara Nwosu", "responseStatus": "needsAction" },
    { "email": "rohan.mehta@maplesoftware.net", "displayName": "Rohan Mehta", "responseStatus": "needsAction" }
  ],
  "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0149"
}
```

Note **`status` stayed `tentative`** while `availability` became `BUSY` — the two are independent
fields that happened to agree in the authored record. Because storage collapses both onto
`show_as` for availability and keeps `status` separate, writing `AVAILABILITY_BUSY` onto an event
whose `show_as` was `tentative` **destroys the only record that it was a tentative hold** (§5.2).
Appending an attendee must also **preserve array order** — `json_insert(attendees, '$[#]', ?)`, the
same rule that keeps Gmail's `labels[]` in authored order.

**`removedAttachmentFileUrls` removes by `fileUrl`, but storage keys attachments by `file_id`** and
`fileUrl` is a value we synthesized (§5.3). The synthesis must therefore be **invertible** —
`fileUrl → file_id` has to be a reliable parse, not a lossy formatting step. That is the reason to
embed the raw `file_id` verbatim in the URL rather than slugify it.

### 4.3 `delete_event`

`readOnlyHint: false` · **`destructiveHint: true`** · `idempotentHint: true` · `openWorldHint: false`
`title: "Deletes a calendar event."`

**Description:** *"Deletes an event on the given calendar."* Request message: `DeleteEvent`.

**Request:** `eventId` (**required** — *"The ID of the event to delete"*), `calendarId`,
`notificationLevel`.

```json
{ "name": "delete_event", "arguments": { "eventId": "EVT-0112" } }
```

**Response: a full `Event`, not `{}`.** The `outputSchema` is the complete `Event` type — every
field, same as `get_event`. This is the most surprising write shape in the capture and the direct
analogue of Gmail's `create_draft`-returns-a-`Draft`-despite-the-prose call: **follow the schema.**

Which forces the semantics: a tool that returns the deleted event **must not hard-delete it.** The
only coherent reading is a soft delete — set `status: "cancelled"` and return the record. That also
matches the `Event.status` description (*"`cancelled` - Event is cancelled **or deleted**"*), makes
`idempotentHint: true` true (deleting twice yields the same cancelled event), and preserves the
round-trip integrity of every other row.

EVT-0112 is already `status: cancelled` in the authored data — which makes it the natural example
and a free idempotency test:

```json
{
  "id": "EVT-0112",
  "summary": "Thornbury weekly check-in",
  "description": "Cancelled by Thornbury following the spend freeze.",
  "location": "Google Meet",
  "start": { "dateTime": "2026-07-08T10:00:00+01:00", "timeZone": "Europe/London" },
  "end":   { "dateTime": "2026-07-08T10:30:00+01:00", "timeZone": "Europe/London" },
  "status": "cancelled",
  "eventType": "DEFAULT",
  "availability": "AVAILABILITY_FREE",
  "visibility": "default",
  "transparency": "transparent",
  "conferenceUrl": "https://meet.google.com/uk-thorn-112",
  "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "attendees": [
    { "email": "ellie.ashworth@maplesoftware.net", "displayName": "Ellie Ashworth", "responseStatus": "accepted", "organizer": true, "self": true },
    { "email": "helen.voss@thornburyretail.co.uk", "displayName": "Helen Voss", "responseStatus": "declined" }
  ],
  "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0112"
}
```

**Open question that follows immediately: do cancelled events still appear in `list_events`?** The
capture has **no `showDeleted` parameter** — unlike the real Calendar v3 REST API, which does. So
there is no caller-visible switch, and the server must pick one behaviour for all callers. Flagged
in §7; the consequence is that a delete is either observable (excluded) or invisible (included),
and EVT-0112 is authored `cancelled` from the start, so whichever we choose is immediately load-bearing.

### 4.4 `respond_to_event`

`readOnlyHint: false` · `destructiveHint: false` · `idempotentHint: true` · `openWorldHint: false`
`title: "Responds to an event."`

**Description:** *"Responds to an event on a calendar."* Request message: `RespondToEvent`.

**Request:**
| Field | Type | Required | Notes |
|---|---|---|---|
| `eventId` | string | **yes** | |
| `responseStatus` | string | **yes** | *"The **new user's** response status"* — `declined` \| `tentative` \| `accepted` |
| `responseComment` | string | no | *"The user's comment attached to the response."* |
| `calendarId` | string | no | |
| `notificationLevel` | enum | no | ignored |

**`needsAction` is not accepted here.** `Attendee.responseStatus` lists four values; this tool's
`responseStatus` lists **three** — you cannot un-respond. A small asymmetry, reproduced rather than
normalized (same spirit as Gmail's `label_*` rejecting `TRASH` while `unlabel_*` accepts it).

**This tool always writes the account owner's own RSVP** — "the user's" response. It takes no
attendee parameter, so it resolves to `ACCOUNT_ADDRESS` and updates that one element of
`attendees[]`. It is the only write tool that mutates an array element in place rather than
appending or replacing.

```json
{
  "name": "respond_to_event",
  "arguments": {
    "eventId": "EVT-0106",
    "responseStatus": "accepted",
    "responseComment": "Works for me -- will bring the updated pricing."
  }
}
```

**Response:** the full `Event`. EVT-0106 is authored `status: tentative` with Ellie `accepted`
already; using it shows the interesting part — `responseComment` lands in `Attendee.comment`, a
field marked `readOnly` on the wire and **with no home in `calendar_schema.md` at all**:

```json
{
  "id": "EVT-0106",
  "summary": "Verano -- solution review (proposed)",
  "description": "Proposed solution review ahead of the board decision.",
  "location": "Google Meet",
  "start": { "dateTime": "2026-07-27T16:30:00+01:00", "timeZone": "Europe/London" },
  "end":   { "dateTime": "2026-07-27T17:30:00+01:00", "timeZone": "Europe/London" },
  "status": "tentative",
  "eventType": "DEFAULT",
  "availability": "AVAILABILITY_BUSY",
  "visibility": "default",
  "transparency": "opaque",
  "conferenceUrl": "https://meet.google.com/uk-verano-106",
  "organizer": { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "creator":   { "displayName": "Ellie Ashworth", "email": "ellie.ashworth@maplesoftware.net", "self": true },
  "attendees": [
    {
      "email": "ellie.ashworth@maplesoftware.net",
      "displayName": "Ellie Ashworth",
      "responseStatus": "accepted",
      "comment": "Works for me -- will bring the updated pricing.",
      "organizer": true,
      "self": true
    },
    { "email": "amara.nwosu@veranotravel.com", "displayName": "Amara Nwosu", "responseStatus": "needsAction" }
  ],
  "htmlLink": "https://calendar.google.com/calendar/event?eid=EVT-0106"
}
```

**`responseComment` has nowhere schema-legal to go.** `calendar_schema.md`'s `Attendee` is exactly
`{name, email, response_status}`, and the dataset validator rejects unknown keys on events. Storing
a `comment` on the attendee object would make the stored record fail the validator that guards
load-time integrity. Three ways out, and the choice belongs in the architecture doc:
(a) hold comments in a side table keyed by `(event_id, email)` that never round-trips into the event
record; (b) accept and drop the comment, reporting it; (c) extend the schema. **(a) is the only one
that both honours the request and keeps the 1:1 storage rule intact.**

Also note: **`respond_to_event` does not change `Event.status`.** EVT-0106 stays `tentative` even
after Ellie accepts — event status and attendee RSVP are independent, and `status` is only reachable
via `delete_event`.

---

## 5. Schema → wire field ledger

This section is the Calendar equivalent of the Gmail spec's query-operator catalog: the thing you
read before writing the projection layer. Four categories.

### 5.1 Clean 1:1 mappings — 12 fields

| Storage (`calendar_schema.md`) | Wire (`Event`) | Note |
|---|---|---|
| `id` | `id` | |
| `subject` | `summary` | **rename** |
| `description` | `description` | |
| `location` | `location` | |
| `status` | `status` | enum values identical: `confirmed`/`tentative`/`cancelled` |
| `organizer.name` / `.email` | `organizer.displayName` / `.email` | |
| `attendees[].name` / `.email` | `attendees[].displayName` / `.email` | |
| `attendees[].response_status` | `attendees[].responseStatus` | **values verbatim, camelCase preserved** |
| `online_meeting.join_url` | `conferenceUrl` | |
| `recurrence` (scalar or null) | `recurrence` (array, omitted) | **scalar → 1-element array**; null → field absent |
| `attachments[].title` | `attachments[].title` | |
| `start` / `end` | `start` / `end` | shape changes (§1 `DateOrDateTime`) but no information is lost |

### 5.2 Lossy collapses — 2 fields, 5 events affected

**`show_as` → `availability`. Three values in, two out.**

| Storage | Count | Wire | Lossless? |
|---|---|---|---|
| `busy` | 37 | `AVAILABILITY_BUSY` | yes |
| `free` | 10 | `AVAILABILITY_FREE` | yes |
| `tentative` | **3** | `AVAILABILITY_BUSY` | **no** |

EVT-0106, EVT-0136, EVT-0149. Google's model has no tentative availability; `AVAILABILITY_UNSPECIFIED`
is *"Treated as `BUSY`"*, so `BUSY` is the right target — a tentative hold does block time.
`transparency` (deprecated) mirrors it as `opaque`/`transparent`.

**`sensitivity` → `visibility`. Three values in, three out, but not the same three.**

| Storage | Count | Wire | Lossless? |
|---|---|---|---|
| `normal` | 44 | `default` | yes |
| `private` | 4 | `private` | yes |
| `confidential` | **2** | `private` | **no** |

EVT-0125 and EVT-0133. Google's `visibility` is `default`/`public`/`private` — there is no
`confidential`, and `public` has no storage counterpart, so the mapping is neither onto nor
one-to-one.

**Both collapses are one-way, and that is the problem for writes.** A read projects
`tentative → BUSY`; a subsequent `update_event(availability: AVAILABILITY_BUSY)` — even one that
merely echoes back what it read — writes `busy` and **erases the fact that it was tentative.** Same
for `confidential` → `private` → `confidential` is unrecoverable. A read-modify-write round trip
through the wire is lossy on 5 of 50 events. Two defensible responses: keep the storage value when
the incoming wire value is the one it already projects to (idempotent echo, preserves data), or
write the collapse honestly. **The first is right**, and it is exactly the kind of decision that
must be stated in the architecture doc rather than discovered later.

### 5.3 Synthesized — no source in the schema

| Wire field | Synthesis | Risk |
|---|---|---|
| `eventType` | constant `DEFAULT` | none — but see the `FOCUS_TIME` trap in §2 |
| `creator` | aliases `organizer` | low — Google's own docs note they usually coincide |
| `htmlLink` | `https://calendar.google.com/calendar/event?eid={id}` | none; cosmetic |
| `attachments[].fileUrl` | `https://drive.google.com/file/d/{file_id}/view` | **must be invertible** — `update_event.removedAttachmentFileUrls` removes by URL (§4.2) |
| `Principal.self` / `Attendee.self` | `email == ACCOUNT_ADDRESS` | none — derived, correct by construction |
| `Attendee.organizer` | `email == organizer.email` | none — schema guarantees the organizer is in `attendees[]` (50/50) |
| `list_events` envelope: `summary`, `description`, `timeZone`, `accessRole`, `defaultReminders` | per §3.3 | invented wholesale; no authored calendar record exists |
| `CalendarListItem.*` | per §3.3 | same |
| `iCal_uid` on `create_event` | `{new_id}@maplesoftware.net` | must be unique and contain `@` (validator) |
| `online_meeting.conference_id` on create | minted | validator enforces `is_online_meeting` ↔ `online_meeting` pairing |

### 5.4 Dropped — storage fields with no wire home

| Storage field | Why | Impact |
|---|---|---|
| `body_preview` | no wire field | **none** — byte-identical to `description` on 50/50 |
| `iCal_uid` | **`Event` in this capture has no `iCalUID` field** | the cross-calendar identity that `calendar_schema.md` calls out as a distinct concept is simply not exposed. Real Calendar v3 has `iCalUID`; this MCP surface does not |
| `is_all_day` | encoded structurally | not lost — becomes `date` vs `dateTime` |
| `is_online_meeting` | encoded structurally | not lost — becomes presence/absence of `conferenceUrl` |
| `online_meeting.conference_id` | no wire field | **lost.** `calendar_schema.md` calls it *"the clean key linking this event to its transcript"* — so the transcript join key is invisible to the agent |
| `has_attachments` | encoded structurally | not lost — presence of `attachments[]` |
| `attachments[].mime_type` | `Attachment` is `{fileUrl, title}` only | lost; cosmetic |
| `source` | no wire field | **null on all 50 events**, so nothing is lost *today*. If the Calendar→email link is ever authored, it becomes invisible to the agent through this toolset |

Two of those matter for task design, not just fidelity: **`conference_id` and `source` are the two
join keys `calendar_schema.md` defines, and neither survives to the wire.** An agent cannot follow
event → transcript or event → email through Calendar tools alone. §7.

---

## 6. Filter and behaviour surface

The build spec for the query layer. Compare Gmail's §5, which catalogued ~40 operators in a
mini-language; this is ten typed parameters.

### 6.1 The complete filter surface

| Parameter | Tool | Semantics | Storage predicate |
|---|---|---|---|
| `calendarId` | `list_events`, `get_event`, writes | resolve email → person; `primary` → account owner | `organizer.email == id OR id ∈ attendees[].email` |
| `startTime` | `list_events` | lower bound, inclusive, **instant** | `end_utc > :t` (derived column) |
| `endTime` | `list_events` | upper bound, exclusive, **instant** | `start_utc < :t` |
| `fullText` | `list_events` | *"case-insensitive… all query terms verbatim (AND)"* over title, description, location, attendees | one `LIKE '%term%'` per term, ANDed, over 4 field groups ORed |
| `eventType[]` | `list_events` | membership; default set excludes `WORKING_LOCATION`/`BIRTHDAY` | constant `DEFAULT` — see §2 |
| `orderBy` | `list_events` | 4 values | sort on `start_utc`; `lastModified` unsupported |
| `pageSize` / `pageToken` | most | default 100, max 250 (`list_events`) | opaque integer offset |
| `timeZone` | `list_events`, writes | *"resolve timezone-less dates"* | zone for bare/naive inputs |
| `query` | `search_events` | free text search terms, case-insensitive, all terms (AND), over the 7-field corpus of §3.5.1 | same predicate as `fullText`, plus organizer name/email |
| `attendeeEmails` | `suggest_time` | free/busy union across people | `email ∈ attendees[].email` |

**`calendarId` is a filter, not a partition.** Every real Calendar endpoint is
`/calendars/{calendarId}/events`, but we have one flat event collection and no calendar records. So
`calendarId` resolves to a predicate over `organizer.email` + `attendees[].email`. Because Ellie
is an attendee on **all 50** events, `calendarId: "primary"` is a no-op filter on this dataset —
which means **a bug in `calendarId` handling is undetectable via the primary calendar.** Test it
with Priya (22 of 50) or Morag (2 of 50).

### 6.2 Address matching is exact here, unlike Gmail

Gmail's `from:`/`to:` became substring matches over name **and** address, deliberately fuzzy,
because the operator is documented as taking "a specific person". Calendar has no address
*operator* — `calendarId` and `attendeeEmails` are both documented as **email addresses**, resolved
via `list_calendars`. So they are **exact equality**, and the entire `_like()` / `_ADDRESS_OPERATORS`
apparatus from the Gmail server has no counterpart. (Names are still substring-searchable, but only
through `fullText`, whose scope explicitly includes attendees.)

### 6.3 Recurrence — 8 events, and the one place it changes answers

8 events carry a single RRULE; 6 distinct rules:

| Event | RRULE | Authored first instance | `show_as` |
|---|---|---|---|
| EVT-0102 | `FREQ=WEEKLY;BYDAY=TH` | Thu 2026-07-23 10:00 | busy |
| EVT-0113 | `FREQ=WEEKLY;BYDAY=WE` | Wed 2026-07-22 10:00 | busy |
| EVT-0119 | `FREQ=WEEKLY;BYDAY=MO` | Mon 2026-07-20 09:00 | busy |
| EVT-0120 | `FREQ=WEEKLY;BYDAY=TU` | Tue 2026-07-21 08:30 | busy |
| EVT-0121 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` | Mon 2026-07-20 08:45 | busy |
| EVT-0122 | `FREQ=MONTHLY;BYDAY=3TH` | Thu 2026-07-16 13:00 | free |
| EVT-0123 | `FREQ=WEEKLY;BYDAY=TH` | Thu 2026-07-23 09:00 | busy |
| EVT-0124 | `FREQ=WEEKLY;BYDAY=WE` | Wed 2026-07-22 16:00 | free |

`calendar_schema.md` forbids authoring occurrence records: *"Never author separate occurrence
records — recurrence is just this one field."* And this capture has **no `singleEvents` parameter**
— unlike the real v3 REST API. So there is no caller-visible switch and, unlike Gmail, no
`unsupportedOperators` channel to report the limitation through. The server picks one behaviour
silently.

**`list_events` without expansion is merely incomplete. `suggest_time` without expansion is
wrong.** Measured, Ellie + Priya, 09:00–18:00 London, 30 min:

| Day | Without expansion | With expansion |
|---|---|---|
| Wed 2026-07-22 | busy 10:00–10:30 → slots 09:00–10:00, 10:30–18:00 | **identical** (EVT-0113 is authored on this date) |
| Wed 2026-07-29 | busy 14:00–14:40 → slots 09:00–14:00, 14:40–18:00 | busy **10:00–10:30** + 14:00–14:40 → slots 09:00–10:00, **10:30–14:00**, 14:40–18:00 |
| Mon 2026-08-03 | busy none → slot **09:00–18:00 (whole day free)** | busy **09:00–10:00** → slots 10:00–18:00 |

The 22 July row is the trap: it agrees, because that is the RRULE's authored date. **A test written
against 22 July passes with no expansion at all**, and the tool then confidently offers a slot on 3
August that is already occupied by a weekly pipeline review. Same false-confidence shape as the
timezone bug in §3.1.1 and the Gmail label-ordering bug.

If expansion is implemented, it must live in the **projection layer, never in storage** — generated
occurrence rows would break the byte-for-byte round-trip assertion that guards load integrity, the
same reason Gmail's derived labels are never written into `messages.labels`. Expanded instances are
also where `recurringEventId` and `originalStartTime` finally get values.

### 6.4 `fullText` vs `search_events` — one matcher, two entry points

Both are free-text search over the same corpus with the same rule: lowercase, split on whitespace,
every term must be a substring. They differ only in **scope and envelope** — full comparison table
in §3.5.2. `list_events`'s description routes open-ended keyword asks to `search_events`; honour
that routing, but implement the predicate **once**.

The only substantive difference in the corpus itself: `list_events.fullText` is documented over
*"title, description, location, or attendees"* (4 groups), while `search_events` inherits the REST
`q` field list, which also names **organizer** name and email (7 groups, §3.5.1). On this dataset
that difference is invisible — the organizer is in `attendees[]` on all 50 events, validator-enforced
— so build the 7-field corpus and use it for both. Measured across 12 queries: identical match sets.

### 6.5 What cannot be honoured, and how to say so

Gmail returns `unsupportedOperators` in the response — silently dropping a filter makes "no
results" indistinguishable from "that filter did nothing". **No response type in this capture has
such a field.** `list_events`' envelope has 8 fields and none is diagnostic; `search_events`' has 2.
So there is nowhere fidelity-preserving to report:

| Unhonourable | Why | Current fallback |
|---|---|---|
| `orderBy: lastModified` | no `updated` field exists in storage | silently falls back to `default` |
| `eventType` other than `DEFAULT` | no event-type field authored | correctly returns 0 — honoured, not dropped |
| `q` working-location keyword expansion | no working-location events authored; `eventType` is constant `DEFAULT` | correctly returns 0 — honoured, not dropped |
| recurrence expansion | no `singleEvents` parameter to gate it | one silent global choice |
| `Reminder`, `GuestPermissions`, `colorId` on reads | no source | omitted from responses |

**Adding an extra field to the response envelope would break wire fidelity**, which is design
principle #4 of this stack. The alternative is to document these in the server's CLAUDE.md and log
them server-side. **This is a real decision, not an oversight to paper over** — it is the one place
where the Gmail server's best diagnostic habit does not transfer.

---

## 7. Open questions for the architecture doc

Not answerable from the capture or the schema. Each one changes the implementation.

1. **Storage: SQLite or a plain dict?** Total free-text corpus is 13,055 chars across 50 events —
   trivially scannable in Python. Gmail's two justifications for SQLite were FTS5 and indexed date
   ranges; here the text corpus is tiny and the date comparison key is **derived**, so no index on a
   source column serves it. The remaining arguments for SQLite are the load-time round-trip
   integrity assertion, the derived `start_utc`/`end_utc` columns, and consistency with the sibling
   that will be queried alongside it.
2. **Recurrence expansion: in or out of v1?** §6.3 — the largest net-new piece of logic, and
   `suggest_time` is provably wrong without it.
3. **Do cancelled events appear in `list_events`?** §4.3 — there is no `showDeleted` parameter, so
   one global choice. EVT-0112 is authored `cancelled`, so it is load-bearing on day one.
4. **`list_calendars`: all 14 addresses, or the 3 internal ones?** §3.3.
5. **Where does `responseComment` live?** §4.4 — the schema's `Attendee` has no `comment` field and
   the validator rejects unknown keys.
6. **`suggest_time` slot granularity** — maximal free intervals or discrete `durationMinutes`
   candidates? §3.4.
7. **Reporting unhonourable parameters** with no envelope field to put them in. §6.5.
8. **Two join keys don't reach the wire.** `online_meeting.conference_id` and `source` are the
   links to transcripts and to email respectively (§5.4). `source` is **null on all 50 events**, so
   the Calendar→email link is authored nowhere yet. And the 8 transcripts (5,898 chars) sit outside
   the documented `q` field list, reachable only by following a synthesized `fileUrl` to a
   Drive-like server — which raises whether `attachments[].fileUrl` should resolve against the
   existing `file-server` rather than a cosmetic `drive.google.com` URL.
9. **Reproducible "now."** Gmail derives it from `MAX(date)` with a `MAIL_NOW` override. No
   Calendar tool takes a relative date, so nothing *requires* it — but the data ends 2026-08-06 and
   "next week's meetings" is the most natural calendar ask there is, which means the model will
   compute a window from its own idea of today.
10. **Port, container name, and the 7 registration touchpoints.** Taken: host 9001-3, 8011, 8012,
    8014, 8015; container 8001-3, 8011-8014; test 9011-9014.
