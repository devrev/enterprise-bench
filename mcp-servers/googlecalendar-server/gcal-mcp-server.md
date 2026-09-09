# Google Calendar MCP Server — Full Build Plan

## 0. What this is

An end-to-end plan for adding a `gcal` MCP server to **`docker/mcp-servers-2/`** (see §3.0 for why
that stack), mocking Google's Calendar MCP server against the synthetic dataset in
`integrations/data/calendar_json_data/`. Same architectural family as the existing `pm`, `crm`,
`file-server`, and `mail` mocks — a self-contained MCP server process, no external services.

**Read this together with [gcal-mcp-toolset.md](gcal-mcp-toolset.md).** That doc is the wire
contract: every tool's description, request table, response shape, example payload, the
schema→wire field ledger, and the filter surface. **This doc does not repeat it.** Where a
response shape or a field mapping matters here, it is referenced by section (`toolset §3.1`) and
not restated. This doc covers what the toolset deliberately leaves open: process model, transport,
storage, the algorithms, the module layout, the technologies, registration, and build order.

**Tool count: 9 — 5 read, 4 write.** All 9 are implementable against the authored schema; none is
stubbed. Enumerated in toolset §3 and §4.

**Storage decision up front, because it is the biggest divergence from the `mail` sibling: a plain
Python dict, not SQLite.** Rationale in §1.5.

---

## 1. Architecture, end to end

**The MCP server is one self-contained process.** Nothing sits between it and the agent at
runtime.

Startup (once, before any tool call):
```
integrations/data/calendar_json_data/*.json    (git, source of truth, read-only)
        │  docker-compose bind mount, :ro, at container start
        ▼
   /data inside the bench-gcal-mcp container
        │  read ONCE at process startup — _load_data()
        ▼
   EVENTS: dict[str, dict]  +  ORDER: list[str]  +  MAX_DUR: timedelta
   (plain Python objects, this same process, no database of any kind)
```

Runtime (every tool call):
```
   MCP client (Claude Code CLI, or any agent harness)
        │  Streamable HTTP tool call
        ▼
   FastMCP tool layer (mcp_server.py) — container port 8016, host 8016
        │  linear scan over EVENTS.values(), per-query occurrence generation
        ▼
   json.dumps(...) -> text content block, back up the same path
```

> **Note on `server.py` (a REST twin, not a dependency):** the `pm`, `crm`, and `file-server`
> siblings each ship a `server.py` — a separate FastAPI app built into a *second* container from
> the same image (e.g. `salesforce-server` on 9002 vs `salesforce-mcp-http` on 8012). Its purpose
> is manual curl-level sanity checking. `mcp_server.py` never calls it over the network; it
> imports its functions as a library (`from server import ...`) and holds its own private copy of
> the data.
> **Decision for `gcal`: keep the two-module split, skip the REST app** — exactly what
> `gmail-server` does. `server.py` is the data layer (loader, store, occurrence generation, filter
> predicates) and stops before `app = FastAPI(...)`. `mcp_server.py` imports it in-process and adds
> the vendor semantics. So: two modules, one process, one container, no REST port, no
> `_check_auth`, no `/health`.
>
> **Why keep the split at all when there is no REST consumer?** Because it is the one enforceable
> statement of the layering rule in §1.4: `server.py` must never contain a Google field name, and
> `mcp_server.py` must never contain a filter predicate. A single file makes that rule advisory; an
> import boundary makes it checkable.

### 1.1 MCP client config

No new code. Registration happens in the agent's setup script via the same `register_mcp_http`
helper the other four servers use, in
[claude-code-mcp-2-setup.sh.j2](../../../terminal_bench/agents/installed_agents/claude_code_mcp_2/claude-code-mcp-2-setup.sh.j2)
(it polls the URL up to 20 times, registers, then runs `claude mcp list` to verify):

```bash
register_mcp_http "gcal" "http://bench-gcal-mcp:8016/mcp"
```

Container-name-based, resolved over the `mcp-servers-2_default` Docker network that
`_connect_to_mcp_network()` attaches the task container to — **not** `host.docker.internal`, which
is the harbor pattern. There is no `mcp.json` in this stack; URLs are module constants in the
agent (§3.0). `googlecalendar-server/mcp-config.json` is an informational stdio-style descriptor
mirroring its siblings' and is not read at runtime.

### 1.2 Transport

**Streamable HTTP**, not stdio — this server must be reachable by any client over the network like
a real vendor API. Identical to all four siblings:

```python
mcp.run(transport="http", host="0.0.0.0", port=MCP_PORT)
```

### 1.3 Tool definitions

FastMCP auto-derives the `tools/list` JSON Schema from Python function signatures — no
hand-written tool-list JSON anywhere. Each tool is a plain function with a decorator:

```python
@mcp.tool(
    description=(...),          # verbatim from toolset §3.1 — this is prompt text the model reads
    annotations={
        "title": "Lists calendar events in a given calendar.",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
def list_events(
    calendar_id: str = "primary",
    start_time: str = "",
    end_time: str = "",
    full_text: str = "",
    event_type: list[str] | None = None,
    order_by: str = "default",
    page_size: int = 100,
    page_token: str = "",
    time_zone: str = "",
) -> str:
    ...
```

Three rules for this layer:

1. **Descriptions are copied from toolset §3 verbatim.** They are the model's only instruction on
   which tool to reach for, and the toolset records Google's own wording — including
   *"Time constraints should not be specified unless requested by the user"* and the routing of
   open-ended keyword search to `search_events`. Do not soften them to make our filters look
   better.
2. **`annotations` carries the per-tool hints from toolset §3/§4**, including `title`. FastMCP
   supports the dict directly.
3. **Parameter names are `snake_case` in Python and the model sees them as such.** The toolset
   tables use the capture's `camelCase` (`calendarId`, `pageSize`); FastMCP derives the schema from
   the Python signature, so the wire names will be `calendar_id`, `page_size`. This is what the
   `mail` server does (`page_size`, `message_format`) and what the Jira server does (its
   description tells the model *"Parameter name is 'jql' not 'query'"*). **Keep `snake_case` and
   keep the toolset's names in the descriptions**, so a model reading the description and the
   schema sees the same identifiers.

### 1.4 Tool-call handler — the layering rule

Every tool body does the same four steps:

1. **Validate and normalize its own arguments** — parse ISO instants, coerce enums, decode the page
   token. Anything unparseable returns a JSON error body (§1.7), never raises.
2. **Call a data-layer function in `server.py`** — `events_in_range()`, `matches_text()`,
   `free_busy()`. These take and return **storage-shaped records** (`snake_case`, `subject`,
   `show_as`).
3. **Project through a response builder** — `_event()`, `_calendar_list_item()`, `_time_slot()`.
   One builder per shared type in toolset §1, reused by every tool that returns that type. This is
   the only place Google field names appear.
4. **`json.dumps(...)` and return a string.** MCP tool results are text content blocks.

**The rule, stated once and enforced by the module boundary:**

| Module | Knows about | Must never contain |
|---|---|---|
| `server.py` | `subject`, `show_as`, `attendees[].response_status`, RRULE math, UTC derivation | the string `"summary"`, `"availability"`, `"responseStatus"`, `"AVAILABILITY_BUSY"` |
| `mcp_server.py` | `summary`, `availability`, `responseStatus`, enum aliasing, envelopes | any record-field filtering or date arithmetic |

This mirrors `pm_search_issues` → `_parse_jql` → `_issue_matches_jql` and the mail server's
`search_threads` → `_parse_gmail_query` → SQL. The shape is the same; only the engine differs.

### 1.5 Data layer — a plain dict, and why not SQLite

**Decision: `dict[str, dict]`, keyed by event id, holding the source records unmodified.**

The `mail` sibling uses in-process SQLite. Both of its justifications evaporate here:

| Gmail's reason for SQLite | Calendar |
|---|---|
| FTS5 full-text index over message bodies | the entire free-text corpus is **13,055 chars across 50 events** (toolset §3.5.1) — smaller than one email body. A Python `in` over a prebuilt lowercase string is the whole index. |
| `idx_m_date` range scan on an indexed `date` column | **the range key is derived, not stored.** Storage is local wall-clock with no offset plus a separate IANA zone; the comparable value is a computed UTC instant, so no index on a source column serves it. |

Measured, 20,000 randomized week-windows over the 50 records:

| Approach | Per query |
|---|---|
| dict full scan | **4.3 µs** |
| SQLite + 2 indexes | 6.7 µs (**1.6× slower**) |

`EXPLAIN QUERY PLAN` showed why: `SEARCH ev USING INDEX i_s (s<?)` — only one side of the overlap
predicate is indexable; SQLite filters the rest. At N=50 the index bookkeeping costs more than the
scan it avoids.

And the decisive reason, not a performance one: **occurrences of recurring events do not exist
until a query runs** (§1.6). There is no table for SQL to scan. A SQLite build would either
pre-materialize occurrences — which §1.6 rejects — or scan the base table and then generate in
Python anyway, paying for the engine and not using it.

```python
# server.py — the entire store
EVENTS: dict[str, dict] = {}        # event_id -> the source record, unmodified
ORDER: list[str] = []               # event ids sorted by derived UTC start, then id
TRANSCRIPTS: dict[str, dict] = {}   # file_id -> transcript record (metadata only, §4.2)
MAX_DUR: timedelta                  # longest authored duration; computed, never hardcoded
DATA_NOW: datetime                  # max(utc_end) over all events — the only "now" (§1.7)
_TEXT: dict[str, str] = {}          # event_id -> prebuilt lowercase match corpus (§1.8)
_DB_LOCK = threading.Lock()         # §1.9
```

**Design rule: 1:1 with the source JSON.** `EVENTS[id]` *is* the parsed record — same keys, same
order, same nesting. Nothing is flattened, split, or dropped for being derivable
(`body_preview`, `has_attachments`, `is_online_meeting` all stay). Verified: all 50 records carry
exactly the 19-field set in the schema's own order, with `attachments` as the only ever-absent key
(8 of 50 have it).

Derived values live **beside** the store, never inside a record:

| Derived | Where | Why not in the record |
|---|---|---|
| UTC start/end instants | computed in `_utc()` on demand, or cached in a parallel dict | writing `start_utc` into the record breaks the 1:1 rule and the load assertion |
| sort order | `ORDER` list of ids | same |
| match corpus | `_TEXT` dict | same |

#### Load order (`_load_data()`, called at runtime from `__main__` — never at import)

Matching the sibling pattern exactly (`_load_data()` in Jira/Salesforce, `_load_files()` in
file-server, `setup_db()` in gmail):

```
1. events.json       -> EVENTS[e["id"]] = e            (50 records)
2. transcripts.json  -> TRANSCRIPTS[t["file_id"]] = t  (8 records, metadata only — §4.2)
3. build _TEXT        (one lowercase corpus string per event, §1.8)
4. compute MAX_DUR    (max over all events of utc_end - utc_start)
5. compute DATA_NOW   (max over all events of utc_end -> 2026-08-06T09:30:00Z)
6. build ORDER        (sorted by (utc_start, id))
7. run the load-time assertions below
8. print a one-line ready banner, matching the siblings' format
```

The import-time-vs-runtime distinction matters: the test harness sets `DATA_DIR` and then calls
`_load_data()` itself (see `_build_app` in §5), so loading at import would read the wrong path.

#### Load-time assertions

All cheap at 50 rows; each catches a distinct class of bug. **Fail startup loudly** — a silently
half-loaded calendar produces confidently wrong answers.

```
every record's key list == the 19 schema fields, in schema order    -> schema drift
                           (`attachments` permitted as a 20th)
start/end date_time parse as naive ISO with NO trailing Z           -> the storage contract
every time_zone resolves through zoneinfo.ZoneInfo                  -> missing tzdata in the image
utc_end > utc_start on all 50                                       -> inverted ranges
organizer.email appears in attendees[].email on all 50              -> mirrors the data validator
every non-null recurrence parses to a FREQ we implement             -> unsupported RRULE
recurrence is a STRING, not a list                                  -> see the trap below
MAX_DUR > 0 and is finite                                           -> range-scan correctness
len(ORDER) == len(EVENTS)                                            -> sort/index drift
```

**Two traps this catches, both found while prototyping this plan:**

- **`recurrence` is a scalar string, not an array.** `calendar_schema.md:134` authors
  `"recurrence": "RRULE:FREQ=WEEKLY;BYDAY=TU"`. Google's wire `Event.recurrence` is
  `string[]` (toolset §1). A loader written from the wire type does `recurrence[0]` and silently
  gets `"R"` — the first *character* — which parses to no known FREQ and yields zero occurrences.
  **This exact bug cost a prototype run:** the week of 27 July returned 2 events instead of 13, and
  it looked like a plausible answer. The response builder wraps the scalar into a single-element
  array; the data layer never does.
- **All-day events store `date_time`, not `date`.** EVT-0107 and EVT-0146 are `is_all_day: true`
  and both store `{"date_time": "2026-07-24T00:00:00", "time_zone": "Europe/London"}` — midnight in
  the same object shape as every other event, per `calendar_schema.md:60`. So **`is_all_day` is the
  only signal**, and the `date`-vs-`dateTime` choice in the wire response (toolset §1
  `DateOrDateTime`) is driven by that boolean, not by inspecting which key is present. A builder
  that switches on key presence emits `dateTime` for all 50 and never produces a `date`. And once it
  does produce one, it must build it from the stored local date, not by converting to UTC — see
  `_all_day_date()` in §1.7.

#### NULL policy — three distinct cases

| Case | Storage | Wire |
|---|---|---|
| present-as-null by schema (`recurrence`, `online_meeting`, `source` when N/A) | `null` | **omit the key** |
| authored empty string (`description: ""`, `location: ""`) | `""` | **emit `""`** — present, not dropped |
| empty collection (`attendees` never is; `attachments` absent on 42) | absent | **emit `[]`** so the model never branches on a missing key |

**Never emit JSON `null`.** A field is present with a value or absent. `"recurrence": null` forces
a special case that absence already handles. Same rule the mail server states.

### 1.6 Recurrence — lazy copies, the core algorithm

**The rule: linear scan; for each record with a non-null `recurrence`, step the RRULE forward until
the computed occurrence leaves the caller's window; for each occurrence inside it, emit
`dict(record)` with `start`/`end` patched. No recurrence → yield the record itself. Copies are
response-layer only and die with the tool call. The stored record is never touched.**

Why not pre-expand at startup: **all 8 authored RRULEs lack `UNTIL` and `COUNT`, so they are
infinite** and there is no natural horizon. A chosen horizon fails silently and fails *before* its
edge — measured with a 1-year horizon, a window 3 days inside it returned a partial 7 of 11, and
3 days past it returned 0. Pre-expansion also breaks the 1:1 load rule, and `create_event` accepts
arbitrary recurrence with no way to require a bound, so the problem would return at runtime even if
the authored data were fixed.

```python
def occurrences(rec, t0, t1):
    """
    Yield rec, or patched shallow copies of it, for every occurrence overlapping [t0, t1).
    t0/t1 are aware UTC datetimes, or None for unbounded.
    """
```

Six things the implementation must get right, each verified in a prototype against the real data:

1. **Overlap, not containment.** An occurrence is in range iff `utc_end > t0 AND utc_start < t1`.
   Verified: a 5-minute window at 09:15–09:20 on 29 July correctly returns EVT-0113's 10:00 London
   occurrence — because 09:15Z falls inside it. Containment semantics return nothing.
2. **Patch the local wall-clock string, never a UTC instant.** Compute the occurrence date in the
   event's own zone and write it back as a naive ISO string. Verified across the BST→GMT boundary,
   EVT-0113 (`FREQ=WEEKLY;BYDAY=WE`, 10:00 Europe/London):

   | Occurrence | Patched local | Derived UTC |
   |---|---|---|
   | 2026-10-21 | `10:00:00` | 09:00Z |
   | 2026-10-28 | `10:00:00` | **10:00Z** |
   | 2026-11-04 | `10:00:00` | 10:00Z |

   The meeting stays at 10:00 for its attendees and the UTC instant moves — which is what a real
   calendar does. Patching a UTC instant instead would shift the meeting to 09:00 local after the
   clocks change.
3. **Preserve the duration, don't recompute the end.** `dur = utc_end - utc_start` from the base
   record, applied to each occurrence's start. Durations here span 15 minutes (EVT-0121) to 24
   hours (EVT-0146, EVT-0107).
4. **Start the walk early enough.** An occurrence beginning before `t0` can still overlap the
   window, so the emit test is on `start + dur > t0`, not on `start >= t0`. Do not skip candidates
   before `t0` — test them.
5. **Terminate on four conditions**, checked in this order: `COUNT` exhausted, past `UNTIL`, the
   candidate start is `>= t1`, or a hard iteration cap. **The cap is not optional.** With no `t1`
   and an unbounded rule the loop never exits — a prototype printed occurrence #100,000 dated
   Fri 06 Nov 2409 and was still going. §1.7 covers what `t1` defaults to.
6. **Never emit an occurrence before the base record's own start.** The authored `start` is
   `DTSTART`; the rule generates forward only.

**RRULE subset to implement**, which is exactly what `calendar_schema.md:137` sanctions
(*"FREQ (DAILY/WEEKLY/MONTHLY), optional BYDAY, and optional COUNT or UNTIL"*) and what the 8
authored rules use (6 distinct, table in toolset §6.3):

| Token | Support | In authored data |
|---|---|---|
| `FREQ=WEEKLY` + `BYDAY=MO,TU,…` | yes — multi-day expansion within each week | 7 of 8 rules |
| `FREQ=MONTHLY` + `BYDAY=3TH` | yes — nth weekday of month | EVT-0122 |
| `FREQ=DAILY` | yes | none authored; reachable via `create_event` |
| `COUNT` / `UNTIL` | yes — honoured as a terminator | **none authored** (all 8 are infinite) |
| `INTERVAL`, `BYMONTHDAY`, `BYSETPOS`, `EXDATE` | **no** — the schema excludes them | none |

An unsupported token on a `create_event` recurrence should be rejected at write time with a clear
error, not accepted and silently ignored — otherwise the event exists and never recurs.

**Do not reach for `bisect`.** Bisect needs a sorted list of concrete starts, which does not exist
when occurrences are generated per query. It is downstream of expansion, not a substitute for it.
`ORDER` and `MAX_DUR` exist for `order_by` and pagination, not for range filtering. The cost of
getting this backwards is not slowness but wrongness: **linear scan with no occurrence generation
returns 2 events for the week of 27 July where 13 is correct** — EVT-0121's stored end is
Mon 20 Jul 09:00, before that window starts, so no predicate over the stored columns can find its
5 occurrences.

Measured cost of the correct approach: **~112 µs per query**, against 0.7 µs for pre-expanded +
bisect. Irrelevant next to JSON serialization of the same result set.

**Escalation path if the dataset ever grows to thousands of events:** bisect the one-off records
(42 of 50) using `ORDER` + `MAX_DUR`, and lazily generate only the 8 rules. Correct, ~1.8× faster,
and costs two structures that `create_event` must route between. Not worth it at N=50.

**Two wire fields only exist on generated occurrences**: `recurringEventId` (the base event's id)
and `originalStartTime`. Toolset §1 has their shapes. A base record returned unexpanded carries
neither.

### 1.7 Dates, the query window, and reproducible "now"

**Storage is naive local wall-clock plus an IANA zone; the caller sends aware ISO instants.**
Comparing the caller's instant against the stored string is wrong in both directions on this
dataset — toolset §3.1.1 has the two-row proof (a false positive on EVT-0135 Asia/Singapore, a
false negative on EVT-0136 America/New_York). **Filter on a derived UTC instant, always.**

```python
def _utc(dov: dict) -> datetime:
    """DateOrDateTime storage dict -> aware UTC datetime."""
    zone = ZoneInfo(dov.get("time_zone") or DEFAULT_TZ)
    return datetime.fromisoformat(dov["date_time"]).replace(tzinfo=zone).astimezone(UTC)
```

`DEFAULT_TZ = "Europe/London"` — the account owner's zone, and the value on 96 of 100 authored
start/end pairs. It resolves the `time_zone` tool parameter's *"used to resolve timezone-less
dates"* role and any zone-less input.

**This is why `tzdata` is a dependency.** `python:3.12-slim` ships no system zoneinfo, so
`ZoneInfo("Europe/London")` raises. Same reason `gmail-server/requirements.txt` pins it.

#### `DATA_NOW` — the server never reads the wall clock

**The real clock is never consulted for anything.** `datetime.now()` must not appear in either
module. Instead one constant is computed at load, from the data's own horizon:

```python
DATA_NOW = max(_utc(e["end"]) for e in EVENTS.values())      # 2026-08-06T09:30:00+00:00
```

It is used in exactly **two** places:

1. **The unbounded-window bound.** `list_events` has zero required parameters and Google's own
   description says time constraints should not be specified unless the user asked, so
   `{"arguments": {}}` is a common call. Combined with infinite RRULEs the generation loop has no
   exit — a prototype printed occurrence #100,000 dated Fri 06 Nov 2409 and was still going. When
   `end_time` is absent, bound generation at `DATA_NOW`. Measured result of a bare call: **75
   occurrences across all 50 distinct events, one page at `page_size: 100`, ~68 KB** — a complete,
   honest answer to "what's on my calendar", and it terminates.
2. **The write-time `updated` stamp.** Toolset §1 has `updated` as *"never on reads; set on
   writes"*, so `create_event`/`update_event` must emit one. Using the wall clock would put
   **2026-08-10** (or whatever today happens to be) on an event in a dataset that ends 2026-08-06 —
   non-reproducible across runs, and dated outside the calendar's own universe. `DATA_NOW` is
   identical on every run and sits inside the authored timeline.

This is exactly what the mail server does: [mcp_server.py:1610](../../../docker/mcp-servers-2/gmail-server/mcp_server.py#L1610)
stamps `timestamp = now()`, where `now()` is `MAX(date)` over its messages — *"Using the data's own
horizon rather than the wall clock is what makes `newer_than:7d` mean the same thing on every
run."*

**Format: Google Calendar v3 `updated` carries milliseconds** — `"2026-08-06T09:30:00.000Z"`, not
Gmail's second-precision `"2026-08-06T09:30:00Z"`. This differs from the mail server's
`_to_utc_string()` helper (`strftime("%Y-%m-%dT%H:%M:%SZ")`), so **do not reuse it**; gcal needs its
own:

```python
def _rfc3339_ms(moment: datetime) -> str:
    """Calendar v3 timestamp format: UTC, milliseconds, literal Z."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
```

The same helper serves the `list_events` envelope's `updated` field (toolset §3.1).

**It must NOT be used for all-day events' `date` field.** The capture documents that as a full
midnight timestamp — `"2026-07-24T00:00:00Z"` (toolset §1, §8) — and converting the stored value to
UTC gives the wrong answer: EVT-0107's `2026-07-24T00:00:00` Europe/London is **23:00Z on 23 July**,
because London is `+01:00` in summer. Running it through the UTC formatter yields
`"2026-07-23T23:00:00.000Z"`, a day early. The `date` form is built from the stored **local calendar
date** with a literal midnight-Z suffix, never via a zone conversion:

```python
def _all_day_date(dov: dict) -> str:
    """Capture's all-day form: the stored local date at literal midnight Z."""
    return dov["date_time"][:10] + "T00:00:00Z"
```

Both all-day events are Europe/London, so today the bug shows up as a one-day shift in summer only.
An all-day event authored in Asia/Singapore would shift the other way. Assert this in the test
suite — EVT-0107 must serialize as `2026-07-24T00:00:00Z`.

**No `CAL_NOW` env var — internal constant only.** The mail server exposes `MAIL_NOW` as an
override because `newer_than:`/`older_than:` **filter** against it, so changing it changes which
messages match. **No Calendar tool takes a relative date** — the model converts "next week" into
instants before calling, so nothing here filters against `DATA_NOW`. An override would change only a
cosmetic timestamp while implying it does more.

The consequence belongs in the server's CLAUDE.md rather than in code: real-world today is past the
authored span, so a model asking for "this week" computes a window from *its own* idea of today and
gets a result in which **every event is a generated recurring occurrence and no authored event
appears** — cosmetically perfect, entirely synthetic. Task authors should pin explicit dates inside
2026-04-07 → 2026-08-06.

**No date functions, no string comparison.** Every range test is `datetime` arithmetic on derived
UTC values. Unlike the mail server, lexicographic order is *not* usable here: the stored strings
carry no offset, so two events in different zones sort by wall-clock rather than by instant. That
happens to produce the same sequence as UTC for all 50 authored events, which is exactly why a
sort-order test passes while range filtering is broken (toolset §3.1.1).

### 1.8 Free-text matching

One lowercase corpus string per event, built once at load from the seven sourced fields in
toolset §3.5.1 (title, description, location, attendee names, attendee emails, organizer name,
organizer email):

```python
_TEXT[event_id] = " ".join(parts).lower()      # 13,055 chars total across all 50
```

A query is `all(term in _TEXT[eid] for term in query.lower().split())` — case-insensitive,
substring, AND across terms. That is the documented `q` semantics and it serves **both**
`search_events(query=…)` and `list_events(full_text=…)`; toolset §6.4 explains why one corpus
covers both. Match counts per query are tabulated in toolset §3.5.2 — use them as test fixtures.

Two of those rows are the tests worth writing, because a title-only matcher passes every
`Verano`-style check and returns 0 for both: **`priya` → 23** (matches only on
`attendees[].email`) and **`thames` → 2** (matches only on `location`).

Generated occurrences inherit their base record's corpus — patching dates never changes text, so
match by base id before generating.

### 1.9 Writes — in-memory only, and the lock

Four write tools (toolset §4). All mutate `EVENTS` in this process only; `/data` is mounted `:ro`
and is never written. **A verifier must observe an effect through a read tool**, not by inspecting
`$DATA_DIR`. Mutations die with the container, which is what gives per-trial isolation for free.

```python
_DB_LOCK = threading.Lock()   # held for the whole of any mutation
```

**The lock is not optional just because there is no database.** FastMCP's HTTP transport serves
concurrent requests; `update_event` rebuilding `ORDER` while `list_events` iterates it raises
`RuntimeError: dictionary changed size during iteration`. The mail server holds `_DB_LOCK` around
its SQLite commits for the same reason.

Every mutation must, under the lock: apply the change, then **recompute `ORDER`, `MAX_DUR`, and the
affected `_TEXT` entry.** `MAX_DUR` in particular is derived from the data and a `create_event` with
a 3-day event invalidates it — the value is 24 h today, from EVT-0146 and EVT-0107.

Per-tool semantics are in toolset §4. Three decisions that are architecture, not wire contract:

- **`delete_event` soft-deletes to `status: "cancelled"`.** Forced by the contract: the tool
  returns the deleted `Event`, so it cannot hard-delete. Cancelled events **stay visible** in
  listings — there is no `showDeleted` parameter, and the enum value is the signal the caller
  reads. Idempotent for free. EVT-0112 is authored `cancelled`, so this is load-bearing on day one.
- **`suggest_time` skips cancelled events.** On this dataset EVT-0112 is also `show_as: free`, so
  both rules agree and either alone looks correct — which is exactly why the status check must be
  explicit. A cancelled-but-busy event created at runtime would otherwise block time on a meeting
  that isn't happening.
- **`respond_to_event` accepts and drops `responseComment`.** The authored `Attendee` has no
  comment field and the data validator enforces exact key-set equality
  ([validate_email_calendar.py:103](../../../integrations/data/versioning/validate_email_calendar.py#L103)),
  so there is nowhere to put it. Reversible in ~5 lines via a side dict keyed `(event_id, email)`
  if a task ever needs it. Note also that `needsAction` is not an accepted input value, so an RSVP
  cannot be undone, and the tool does not change `Event.status`.

### 1.10 Errors and unhonourable parameters

**Errors are returned, not raised.** An exception crossing the MCP boundary becomes a
protocol-level failure the model reads as a broken tool; a returned error is something it can act
on. Same helper the mail server uses:

```python
def _error(message: str) -> str:
    return json.dumps({"error": message})
```

Reserved for genuine faults: unknown event id, malformed ISO timestamp, `start_time >= end_time`,
an RRULE token we do not implement, `page_size` out of range.

**A filter that matches nothing is not an error** — it returns an empty result set with the normal
envelope. And a parameter we cannot honour returns normally too: **no response type in this capture
has an `unsupportedOperators`-style field** (toolset §6.5), and adding one would break the wire
fidelity that is design principle #4 of this stack. So the honest options are to document and log
server-side, which is what we do. The full list is short — the only real casualty is
`order_by: lastModified`, since no `updated` field exists in storage; it falls back to `default`.
Document it in the server's CLAUDE.md.

---

## 2. Technologies

Identical stack to `pm`/`crm`/`file-server`/`mail`. **No new dependency category, and two of the
siblings' dependencies are deliberately dropped.**

| Technology | Version | Role | Notes |
|---|---|---|---|
| **Python** | 3.12 (`python:3.12-slim`) | runtime | same base image as all four siblings |
| **FastMCP** | `fastmcp>=2.3.0` | tool definitions, `annotations`, Streamable HTTP transport | `from fastmcp import FastMCP`; `mcp.run(transport="http", ...)` |
| **`mcp`** | `mcp>=1.0.0` | underlying protocol package FastMCP builds on | present in every sibling's requirements |
| **`tzdata`** | `>=2024.1` | IANA zone database | **required** — the slim image has no system zoneinfo, so `ZoneInfo("Europe/London")` raises. `gmail-server` pins it for the same reason |
| **`zoneinfo`** | stdlib | zone resolution, DST-correct local↔UTC | `datetime.replace(tzinfo=…).astimezone(UTC)` |
| **`datetime`** | stdlib | all date arithmetic, RRULE stepping | `timedelta` walks; no third-party date library |
| **`json`** | stdlib | load and serialize | |
| **`threading`** | stdlib | `_DB_LOCK` (§1.9) | |
| **Docker / docker-compose** | — | one container, `:ro` bind mount at `/data` | |
| **pytest** | via `uv run pytest` | the test suite in §5 | |

**Deliberately not used:**

| Not used | Why |
|---|---|
| **`sqlite3`** | §1.5 — dict scan is 1.6× faster at N=50, the range key is derived so no index serves it, and generated occurrences have no table to scan |
| **`fastapi` / `uvicorn`** | no REST twin (§1's note). `requirements.txt` is 3 lines, like `gmail-server`'s |
| **`python-dateutil` / `icalendar`** | the sanctioned RRULE subset is FREQ + optional BYDAY + optional COUNT/UNTIL (§1.6). A ~40-line stepper covers it; a full RFC-5545 library would add a dependency no sibling has and support tokens the schema forbids |
| **`pytz`** | superseded by stdlib `zoneinfo` on 3.9+ |

---

## 3. Directory structure

**Target stack: `docker/mcp-servers-2/`** (§3.0). Files to create:

```
docker/mcp-servers-2/googlecalendar-server/
├── server.py            # data layer: _load_data(), EVENTS/ORDER/_TEXT/MAX_DUR, load assertions,
│                        #   occurrences(), events_in_range(), matches_text(), free_busy(),
│                        #   _utc()  — no FastAPI app, no port, no vendor field names
├── mcp_server.py        # from server import ...; response builders; the 9 @mcp.tool() wrappers;
│                        #   enum aliasing; pagination; mcp.run(transport="http", ...)
├── mcp-config.json      # informational stdio-style descriptor (not read at runtime)
├── Dockerfile           # python:3.12-slim, CMD ["python", "mcp_server.py"]
├── requirements.txt     # mcp>=1.0.0, fastmcp>=2.3.0, tzdata>=2024.1
├── CLAUDE.md            # every sibling server dir has one
└── README.md            # every sibling server dir has one
```

Plus one test file: `docker/mcp-servers-2/tests/test_gcal_server.py`, subclassing
`BaseMCPServerTests` (§5).

Directory name `googlecalendar-server/` rather than `gcal-server/` to match the
vendor-named siblings (`salesforce-server`, `jira-confluence-server`, `gmail-server`); the short
`gcal` is used for the service, container, and registered tool namespace.

### 3.0 Which stack, and the port

Three MCP server generations exist in this repo and **two are live**:

| Directory | Transport | Ports | Status |
|---|---|---|---|
| `harbor/shared/mcp-servers/` | Streamable HTTP | 9001-9003 REST, 8011-**8013** MCP | live (`harbor/mcp.json`) |
| `docker/mcp-servers-2/` | Streamable HTTP | 9001-9003 REST, 8011/8012/8014→8013/8015→8014 MCP | **live — this is the target** |
| `docker/mcp-servers/` | SSE | (no port mappings) | legacy |

`docker/mcp-servers-2/` is wired to two registered agents (`ClaudeCodeMcp2Agent`,
`ClaudeCodeLiveMcpAgent`) and has its own pytest suite with unit and integration modes. Harbor has
**no calendar or mail entry at all** — the mail server was never registered there, so calendar
follows suit. One stack, not two.

**Port: `8016` (host) → `8016` (container). Test port `9015`. Container `bench-gcal-mcp`, compose
service `gcal-mcp-http`.**

Occupancy verified across both live stacks and the test harness:

| | Taken |
|---|---|
| host | 8011, 8012, **8013** (harbor), 8014, 8015, 9001-9003 |
| container | 8011, 8012, 8013, 8014 |
| test (`conftest.py` `SERVER_PORTS`) | 9011-9014 |

Note **8013 is not free** despite `docker/mcp-servers-2/` leaving it open — harbor publishes it.
8016 → 8016 avoids the off-by-one publishes the other two servers carry (`8014:8013`,
`8015:8014`), which exist only for historical reasons and are a documented source of confusion.

Registration touchpoints — 7 existing files, one new entry each. Each insertion point has been read:

- [docker/mcp-servers-2/docker-compose.yml](../../../docker/mcp-servers-2/docker-compose.yml) — add
  `gcal-mcp-http` modelled on the `mail-mcp-http` block at the bottom of the file: `build:
  ./googlecalendar-server`, `image: bench-googlecalendar-server`, `container_name:
  bench-gcal-mcp`, `command: python mcp_server.py`, `ports: ["8016:8016"]`, `MCP_PORT=8016`,
  `DATA_DIR=/data`, the `:ro` data volume, and the socket healthcheck. **No REST service block.**
- [docker/mcp-servers-2/CLAUDE.md](../../../docker/mcp-servers-2/CLAUDE.md) — structure tree, the MCP HTTP
  port table, the `gmail-server`-exception note (now two exceptions), the data-source paragraph
  (add `calendar_json_data/`), and the dependencies paragraph (`tzdata` now applies to two servers).
- [docker/mcp-servers-2/README.md](../../../docker/mcp-servers-2/README.md) — server table and the
  documented `DATA_DIR` layout.
- [docker/mcp-servers-2/scripts/run-docker.sh](../../../docker/mcp-servers-2/scripts/run-docker.sh) — add
  `echo "  - http://localhost:8016/mcp  (gcal)"` after the mail line; no `/health` entry, since
  there is no REST twin.
- [docker/mcp-servers-2/tests/conftest.py](../../../docker/mcp-servers-2/tests/conftest.py) — add
  `"gcal-mcp-http": 9015` to `SERVER_PORTS` (line ~48), and update the module docstring's
  "9011–9013" range note.
- [claude_code_mcp_2_agent.py](../../../terminal_bench/agents/installed_agents/claude_code_mcp_2/claude_code_mcp_2_agent.py)
  — add `DEFAULT_GCAL_MCP_CONTAINER = "bench-gcal-mcp"`, `DEFAULT_GCAL_MCP_URL =
  f"http://{DEFAULT_GCAL_MCP_CONTAINER}:8016/mcp"`, a ctor arg, and one more line in the existing
  `_get_template_variables()` override. That override already exists for mail — extend it, do not
  edit the base `ClaudeCodeMcpAgent`, or the legacy SSE agent breaks.
- [claude-code-mcp-2-setup.sh.j2](../../../terminal_bench/agents/installed_agents/claude_code_mcp_2/claude-code-mcp-2-setup.sh.j2)
  — add `register_mcp_http "gcal" "{{ gcal_mcp_url }}"` after the mail line.

**No data-artifact work.** `events.json` and `transcripts.json` are already committed and recorded
in `data-manifest.json`. The `integrations-data-sync` pre-commit hook is scoped
`files: ^integrations/data/`; this change touches only `docker/` and `terminal_bench/`, so it will
not fire. Only `ruff-check`/`ruff-format` apply. **We are adding a reader, not changing data.**

---

## 4. Data source

**Authoring authority: [ontology.md](../../../docs/ontology/ontology.md) §A.5.3 `CalendarEvent`**
(plus §A.5.4 `MeetingFile` and the integrity rules in §A.5.5), machine-enforced by
[validate_email_calendar.py](../../../integrations/data/versioning/validate_email_calendar.py) in the
`integrations-data-sync` pre-commit hook. Read it before changing any loader assumption. The
schema→wire ledger is toolset §5; the storage-side profile is below.

Citations of `calendar_schema.md` throughout this document refer to the construction worksheet §A.5
was folded from (ontology v1.3, 2026-08-11). That file is untracked and local; the ontology section
is the tracked source of truth and says the same thing about every field named here.

```
integrations/data/calendar_json_data/
├── events.json        # 50 records, 19 fields each (+ optional attachments)
└── transcripts.json   # 8 records: file_id, event_id, kind, title, text
```

Profile that shapes the implementation (all measured):

| Property | Value | Implication |
|---|---|---|
| events | 50 | dict scan is the right engine (§1.5) |
| authored span | 2026-04-07T08:00Z → 2026-08-06T09:30Z (121 days) | the bare-call bound (§1.7) |
| recurring | 8, all infinite (no `COUNT`/`UNTIL`) | lazy generation is mandatory (§1.6) |
| zones | Europe/London 96, Asia/Singapore 2, America/New_York 2 | the 2 non-London events are the timezone test fixtures |
| `status` | confirmed 47, tentative 2, cancelled 1 | EVT-0112 exercises soft-delete on day one |
| `show_as` | busy 37, free 10, tentative 3 | `tentative` blocks in `suggest_time` |
| `is_all_day` | 2 (EVT-0107, EVT-0146) | both store `date_time`, not `date` (§1.5 trap) |
| `is_online_meeting` | 41, each with `online_meeting.join_url` | becomes `conferenceUrl` |
| `has_attachments` | 8 | §4.2 |
| `source` | **null on all 50** | the Calendar→email join key is authored nowhere yet |
| `body_preview` | byte-identical to `description` on 50/50 | dropping it loses nothing |
| attendees per event | 1–5 | `maxAttendees`-style truncation never triggers |
| free-text corpus | 13,055 chars | no index needed |
| longest duration | 24 h (EVT-0146, EVT-0107) | `MAX_DUR` today |
| shortest duration | 15 min (EVT-0121) | |

### 4.1 One calendar, one owner — and what that means for two tools

Ellie Ashworth (`ellie.ashworth@maplesoftware.net`) is an attendee on **all 50 events**. Next
highest is Priya at 22, then Rohan at 18; 14 distinct addresses appear in total. There are **zero
events with Priya but not Ellie**, and no `owner` field exists on an event — one events file is one
calendar.

Three consequences:

1. **`calendar_id` is a filter, not a partition** (toolset §6.1) — it resolves to
   `organizer.email == id OR id in attendees[].email`. Because Ellie is on all 50,
   `calendar_id: "primary"` is a **no-op on this dataset**, which means a bug in `calendar_id`
   handling is undetectable through the primary calendar. **Test with Priya (22) or Morag (2).**
2. **`list_calendars` returns one entry** — Ellie's, `accessRole: "owner"`. Synthesizing 14
   calendars from attendee addresses would advertise 13 calendars whose contents we cannot serve,
   and the addresses are already discoverable from `attendees[]` on any event.
3. **`suggest_time`'s `attendee_emails` is a no-op for anyone but Ellie.** Any non-owner contributes
   zero busy time we don't already have, so `suggest_time(["ellie","priya"])` returns byte-identical
   results to `suggest_time(["ellie"])`. **Implement the parameter faithfully** — union the busy
   intervals of every named attendee — and document that the union is currently degenerate. Do not
   write a task that depends on a non-owner's private schedule.

A future multi-person free/busy story needs a **small side schema** (`{person, start, end}` — the
2 fields Google's `freebusy.query` actually exposes), not copies of the 19-field event record with
a different organizer. You cannot leak a title that was never authored. Deferred; out of scope
here.

### 4.2 Transcripts: loaded as metadata, content served by `file-server` later

8 events carry `attachments[]` pointing at the 8 transcript records (5,898 chars total, the largest
text in the dataset). The wire `Attachment` type is `{fileUrl, title}` only (toolset §1), and
`fileUrl` is **required** while storage has only `file_id` — so it is synthesized.

**v1: synthesize a cosmetic `https://drive.google.com/file/d/<file_id>/view` and stop.** The
transcript body is not reachable through any calendar tool, and that is faithful: **neither of
Google's MCP servers exposes attachment content.** Gmail's capture is explicit — its
`Attachment.id` is annotated *"ID of an external attachment retrievable via a separate
GetMessageAttachment request (**not an exposed MCP tool**)"*. Google's design is that the bytes
live in Drive and you fetch them with a Drive tool. Our own `gmail-server` already ships with the
identical gap, emitting `attachmentIds` and `attachments[]` metadata for its 8 email attachments
with no way to read them.

Load `transcripts.json` into `TRANSCRIPTS` anyway — `title` and the `event_id` link are cheap, and
it keeps the follow-up to a one-line change here.

**The follow-up, scoped and named so it isn't rediscovered:** make
[file-server](../../../docker/mcp-servers-2/file-server/) the resolver for both siblings' attachments. Two
concrete mismatches block it today:

1. **Wrong directory.** [server.py:116-145](../../../docker/mcp-servers-2/file-server/server.py#L116-L145)
   loads `internal_docs/` and `transcripts/` as loose files. The calendar transcripts are a JSON
   array in `calendar_json_data/transcripts.json`.
2. **Wrong ID scheme.** file-server generates ids as `sha256("transcripts/" + filename)[:33]`
   (e.g. `c2af196493b1fef95abdd4d0bfd595bdb`); the authored ids are readable strings like
   `drive-transcript-EVT-0103`. Even pointed at the right directory, `read_file_content` misses.

That is a file-server change, not a calendar-server change — which is why it is not in this build.
Once done, `fileUrl` points at file-server and the cross-server multi-hop task becomes possible:
*find the FX meeting → read its transcript → did Rohan confirm it was our bug?* The answer is in
EVT-0103's transcript today, and unreachable.

**A second join key is also invisible on the wire:** `online_meeting.conference_id`, which
`calendar_schema.md` calls *"the clean key linking this event to its transcript."* The wire `Event`
has no field for it (toolset §5.4). So an agent cannot walk event → transcript through Calendar
tools alone even after the file-server work — it has to go through `attachments[].fileUrl`.

---

## 5. Tests

One file, `docker/mcp-servers-2/tests/test_gcal_server.py`, subclassing `BaseMCPServerTests`
following the `test_gmail_server.py` pattern. The base contributes three free tests — tool
discovery, schema validity, unknown-tool rejection — and requires:

```python
class TestGcalServer(BaseMCPServerTests):
    server_name = "gcal-mcp"
    server_port = 9015
    expected_tools = [
        "list_events", "get_event", "list_calendars", "suggest_time", "search_events",
        "create_event", "update_event", "delete_event", "respond_to_event",
    ]

    @classmethod
    def _build_app(cls, data_dir: Path):
        """importlib-load server.py under a unique name, register it as sys.modules["server"],
        set DATA_DIR, call _load_data(), then load mcp_server.py and return mcp_mod.mcp."""
```

Both modes work unchanged: **unit** (`PatchedFastMCPHarness`, in-process) and **integration**
(Docker + `FastMCPHTTPClient` against `http://localhost:9015/mcp`).

Write tests follow the mail server's two rules, since `mcp_test_client` is class-scoped and one
store is shared across the class: **reversible writes restore what they changed**, and **creates
assert deltas, never absolute counts**. Nothing reaches the dataset either way — the store is
in-memory and `/data` is `:ro`.

**Test the four bugs that are invisible to the obvious assertion.** Each is a real trap on this
data, and each has a specific fixture that exposes it:

| Bug | Passes anyway if you test… | Test this instead |
|---|---|---|
| range filtering on the stored string instead of derived UTC | `order_by: startTime` — local-string and UTC orderings coincide for all 50 | EVT-0135 (Asia/Singapore) and EVT-0136 (America/New_York); toolset §3.1.1 has both expected result sets |
| recurrence not generated | any window over **22 July** — that is the RRULEs' authored date, so stored values happen to be right | week of **27 July** → expect **13 occurrences, 9 distinct ids**; or Mon **3 Aug** → EVT-0119 must occupy 09:00–10:00 |
| `calendar_id` ignored | anything with `primary` — Ellie is on all 50 | `calendar_id` = Priya (expect 22) or Morag (expect 2) |
| free-text corpus too narrow | `Verano` (10) — matches titles | `priya` (23, email-only) and `thames` (2, location-only) |

Plus the invariants worth asserting directly because they are contract, not data: bare
`list_events` returns **75 occurrences over 50 distinct ids**; a patched occurrence keeps `10:00`
local across the BST→GMT boundary while its UTC instant shifts; `delete_event` twice on the same id
succeeds twice and leaves `status: "cancelled"`; and the source record is unchanged after a
generated occurrence has been returned.

---

## 6. Response envelope conventions

Per-tool shapes are in toolset §3 and §4 and are not repeated here. Three conventions that apply
across all nine tools:

1. **Match Google's shapes exactly, per tool.** Do not reuse this repo's Jira-style
   (`startAt`/`total`) or Drive-style (`page`/`page_size`) envelopes. Each mock stays faithful to
   its own vendor. Note that `list_events` returns the **calendar resource with `events[]` inside
   it** while `search_events` returns a thin `{events, nextPageToken?}` — two envelope builders
   over one `_event()` builder.
2. **One builder per shared type, reused everywhere** — `_event()`, `_calendar_list_item()`,
   `_time_slot()`, `_attendee()`, `_principal()`, `_attachment()`. Never shape a response inline in
   a tool body. This is the rule that keeps `get_event` and `list_events` from drifting into two
   slightly different `Event` shapes, and it mirrors how the real server reuses `$defs`.
3. **`nextPageToken` present only when more results remain.** The token is an opaque integer
   offset — legitimate, since the contract says opaque. An unparseable token starts from the
   beginning rather than erroring: a stale cursor should degrade, not fail the call. Note the
   real caveat, which real Gmail shares: offsets are only as stable as the result order, so a write
   between two pages can repeat or skip a row.

---

## 7. Build order

1. **`server.py` data layer, verified standalone before any MCP code exists.**
   - `_load_data()` + the load-time assertions in §1.5. Confirm 50 events / 8 transcripts load and
     all assertions pass.
   - `_utc()`, `MAX_DUR`, `ORDER`, `_TEXT`.
   - `occurrences(rec, t0, t1)` — the §1.6 algorithm. **This is the only genuinely new logic in the
     build; get it right here, in isolation, against the five checks in §1.6 and the four fixtures
     in §5.** A prototype of exactly this function produced the numbers quoted throughout this doc
     (13 for the week of 27 July, 75/50 for the bare call, DST-stable 10:00, the 5-minute-window
     overlap case) — reproduce them.
   - `events_in_range()`, `matches_text()`, `free_busy()`.
2. **`mcp_server.py` read half** — response builders first (`_event()` and friends, per toolset §1
   and §5), then the enum aliasing table (toolset §2), then `list_events`, `get_event`,
   `search_events`, `list_calendars`, `suggest_time`. Then `mcp.run(...)`.
3. **Write half** — `create_event`, `update_event`, `delete_event`, `respond_to_event`, all under
   `_DB_LOCK`, each recomputing `ORDER`/`MAX_DUR`/`_TEXT` (§1.9). `create_event` must invent the
   fields the schema requires but the request does not carry: `iCal_uid` (`<id>@maplesoftware.net`,
   matching the authored pattern), `body_preview` (= `description`, as on all 50),
   `has_attachments`, and `source` (null).
4. **Registration** — the 7 touchpoints in §3.0.
5. **`CLAUDE.md` + `README.md`** for the server dir, modelled on `gmail-server/CLAUDE.md` (219
   lines: env-var table, gotcha table, what-diverges-from-real notes). Record here, because they
   have no home in code: the dropped `responseComment`, `order_by: lastModified` falling back, the
   cosmetic `fileUrl`, the degenerate `attendee_emails` union, and the "today is past the data"
   caveat from §1.7.
6. **Smoke test** — `./scripts/run-docker.sh gcal-mcp-http`, then `curl localhost:8016/mcp` and
   confirm `tools/list` returns all 9 tools matching
   [gcal-mcp-toolset.md](gcal-mcp-toolset.md). Then
   `uv run pytest docker/mcp-servers-2/tests/test_gcal_server.py -v` in both modes.

**Not in this build, deliberately:** the file-server attachment resolver (§4.2), the multi-person
free/busy side schema (§4.1), and auth — **no MCP server in either live stack has auth today**, and
adding it only here would diverge for no benefit.

---

## 8. Output schema

Toolset §1 documents each shared type as a field table with a source column; toolset §3/§4 give
each tool's request table and a real example payload. **Model the implementation directly on those,
not on an ad-hoc shape** — one builder per type, reused by every tool that returns it, mirroring
how the real server reuses one `$defs.Event` across seven tools.

Two shape decisions from the capture that are easy to get backwards, both already recorded in the
toolset but worth flagging here because they are *implementation* traps:

- **`DateOrDateTime.date` is documented as a full `"...T00:00:00Z"` timestamp**, not the bare
  `"2026-07-24"` the real v3 REST API uses. Follow the capture, and select `date` vs `dateTime`
  from `is_all_day` — not from which storage key is present (§1.5). It carries no milliseconds and is
  built from the local date, never through a UTC conversion (§1.7).
- **`recurrence` is `string[]` on the wire and a scalar string in storage.** The builder wraps; the
  data layer never unwraps by index (§1.5).
