# Gmail Server - CLAUDE.md

## What this is

A mock Gmail server for benchmarking, matching the tool surface of Google's official Gmail MCP server (`gmailmcp.googleapis.com/mcp/v1`). It serves the synthetic mailbox in `email_json_data/` (messages, labels, attachments) through one interface:

1. **MCP Server** (`mcp_server.py`) - Streamable HTTP MCP tool interface on port 8014

**`server.py` has no FastAPI app.** Every sibling's `server.py` does two jobs in one file: the data layer at the top (`DATA_DIR`, the loader, the store, the query engine) and a REST API bolted on at the bottom. This one has only the first half - nothing in the agent path calls a REST port for mail, so routes, `_check_auth` and `/health` would be dead code. The name still matches, because the half that `mcp_server.py` imports is the half that is here.

## Architecture

```
server.py                    - Data layer: SQLite schema, loaders, SQL predicate builders, mutations
mcp_server.py                - MCP HTTP server (13 tools) + the Gmail query-syntax parser
requirements.txt             - Python deps: mcp, fastmcp, tzdata
Dockerfile                   - Python 3.12-slim container, mounts /data volume
mcp-config.json              - MCP client connection config for agents
gmail-mcp-toolset-spec.md    - The wire contract: all 13 tools, request/response shapes, fixtures
gmail-mcp-server.md          - The implementation plan: storage decisions, algorithms, rationale
```

The two markdown documents are the **specification** and take precedence over anything inferred
from the code; the tool descriptions in `mcp_server.py` are copied from the toolset spec verbatim.

The authoritative storage schema is **`docs/ontology/ontology.md` §A.5.1 `Email`** (with §A.5.2
`EmailLabel` and the integrity rules in §A.5.5), machine-enforced by
`integrations/data/versioning/validate_email_calendar.py` in a pre-commit hook. Citations of
`email_schema.md` in those documents and in code comments refer to the standalone construction
worksheet §A.5 was folded from in ontology v1.3 (2026-08-11); it is now an untracked local file, and
the citations remain accurate about every field they name.

The split is deliberate and load-bearing: `server.py` knows about columns and never about Gmail syntax; `mcp_server.py` knows about Gmail syntax and never writes SQL. Adding an operator means touching both - the parser branch that recognizes it, and the `FILTERS` row that turns it into a predicate. The write tools keep the same line: `add_labels`/`remove_labels`/`insert_draft` take names and records, and every Gmail-specific decision (what a draft's date should be, that `TRASH` also removes `INBOX`, that `label_*` rejects what `unlabel_*` accepts) is made in `mcp_server.py`.

## Tools

| Tool | Kind | Returns |
|------|------|---------|
| `search_threads` | read | `{threads, resultCountEstimate, nextPageToken?, unsupportedOperators?}` |
| `get_thread` | read | one `Thread` |
| `get_message` | read | one `Message` |
| `list_labels` | read | `{labels, nextPageToken?}` |
| `list_drafts` | read | `{drafts, nextPageToken?}` |
| `create_draft` | write | the created `Draft` |
| `label_thread` / `unlabel_thread` | write | `{}` |
| `label_message` / `unlabel_message` | write | `{}` |
| `apply_sensitive_thread_label` | write | `{}` |
| `apply_sensitive_message_label` | write | `{}` |
| `create_label` | write | the created `Label` |

`list_drafts` is `readOnlyHint: true` but `idempotentHint: false` - the only read tool with that combination, copied from the real toolset rather than corrected.

## Data flow

1. On startup, `setup_db()` reads the three files in `$DATA_DIR/email_json_data/`
2. Records are inserted into an in-process SQLite database (`:memory:`), inside one transaction
3. `_assert_dataset_integrity()` re-reads the rows and fails startup if anything did not round-trip
4. A tool call arrives as one query string -> `_parse_gmail_query()` -> a flat clause list -> `build_where()` -> parameterized SQL
5. Rows are shaped into the official response schema by the `_message` / `_thread` / `_label` / `_draft` builders

A write tool call takes the same path in reverse: the tool validates its arguments and resolves label IDs to names, then calls one of `server.py`'s mutation functions, which runs the `UPDATE`/`INSERT` under `_DB_LOCK` and commits.

## Writes and the read-only mount

`/data` is mounted `:ro`, and the write tools still work, because nothing writes there. The store is `:memory:` - a private copy of the dataset rebuilt from the JSON at every startup - so a mutation lands in this process's database and nowhere else.

What follows from that:

- **Mutations last for the life of the container and vanish on restart.** That is the isolation a benchmark trial wants: every run starts from the same authored mailbox, with no reset step and no fixture to restore.
- **A verifier has to observe an effect through a read tool**, not by inspecting `$DATA_DIR`. Trash a thread and `search_threads` stops returning it; the JSON on disk is untouched either way.
- **`_DB_LOCK` is not optional.** Two connections to `":memory:"` are two different empty databases, so one shared connection is the only option, and `sqlite3` then requires `check_same_thread=False` - which removes the check without making the connection safe. Overlapping statements on one connection interleave and corrupt each other's fetches with no exception raised, just a `SELECT` returning the wrong number of rows. FastMCP hands each sync tool function to a different threadpool worker, so one assistant turn emitting two mail tool calls is enough to hit it.

## Mutation layer

`server.py`'s write half is **one function per query shape, not one per tool** - the same rule its read half follows:

| Function | Used by |
|----------|---------|
| `add_labels(scope, id, names)` | `label_thread`, `label_message`, both `apply_sensitive_*` |
| `remove_labels(scope, id, names)` | `unlabel_thread`, `unlabel_message`, both `apply_sensitive_*` |
| `insert_draft(record, attachments, header_id_for)` | `create_draft` |
| `insert_label(name, color)` | `create_label`, once per missing ancestor |
| `draft_rows(clauses)` | `list_drafts` |

`scope` is `"message"` or `"thread"`, which is the only difference between the two label tools - one `WHERE id = ?`, the other `WHERE thread_id = ?`. `apply_sensitive_*` is `add_labels` plus `remove_labels(["INBOX"])`, because the spec calls it a *move*: without the removal, `in:inbox` keeps returning the thread.

Three details worth not rediscovering:

- **Array edits preserve authored order.** Adding a label is `json_insert(labels, '$[#]', ?)`; removing one rebuilds the array with `json_group_array(j.value) ... ORDER BY j.key`. The `ORDER BY` is what keeps the order the fixture authored - which is why this is a JSON array and not a junction table.
- **Idempotency is a `NOT EXISTS` guard in the `UPDATE`**, not a read-then-write. `labels` is a JSON array with no uniqueness constraint of its own, and `idempotentHint: true` has to mean something.
- **`execute()` exists for the commit, not the rowcount.** `query("UPDATE ...")` runs the statement but never commits, leaving it in an open transaction on the shared connection where an unrelated tool's `rollback()` silently reverts it.

Writing a derived label writes its column: `DERIVED_LABEL_WRITES` maps `UNREAD`/`IMPORTANT`/`STARRED` to `is_read`/`importance`/`flag.status` fragments. Removing `IMPORTANT` is lossy - Gmail has two states and the schema has three (`low`/`normal`/`high`), so it lands on `normal`.

## Why SQLite and not a dict

The sibling servers keep their data in a `dict` and filter it in Python, which is the right call for a few hundred files. Two of this tool's operators do not reduce to a dict scan:

- **Full-text search.** A bare word in a Gmail query searches subject, body, and attachment text at once, with phrase support. That is an inverted index - FTS5 gives it for free, and hand-rolling one is the only alternative.
- **Date ranges.** `after:`/`before:`/`newer_than:` are among the operators an agent reaches for most, and a date index is exactly what serves them. (`from:` is *not* on this list any more - it became a substring match, which no index can serve. See "Address matching" below.)

See `gmail-mcp-server.md` section 1.5.

## Schema

Three tables plus one FTS5 virtual table:

| Table | Rows | Notes |
|-------|------|-------|
| `messages` | 50 | One column per source field, same names, same order |
| `labels` | 16 | 8 user labels from `labels.json` + 8 synthesized system labels |
| `attachments` | 8 | FK to `messages`; also stored inline on the message |
| `search_index` | 50 | FTS5 over subject + body + attachment text |

**`messages` is 1:1 with the source record.** Same field names, same order, arrays kept as JSON arrays in a single TEXT column. No `to_1`/`to_2` columns, no junction tables. This is what makes `to_record(row)` reconstruct the source JSON byte for byte, which is what `_assert_dataset_integrity` checks at every startup - so a schema drift fails loudly at load rather than quietly at query time.

## Key design decisions

- **1:1 with the source.** The schema mirrors `email_schema.md` exactly rather than normalizing. Arrays stay arrays; nested objects stay JSON. Queried with `json_each` / `json_extract`.
- **All 13 tools, reads and writes.** The dataset is read-only; the database is not. See "Writes and the read-only mount" above.
- **Writes are validated before the first statement.** One bad label ID in a batch applies none of them, so a partial batch never leaves the agent guessing which half landed.
- **No auth.** Matching the MCP HTTP siblings, which take no bearer token on the MCP port.
- **`label_*` rejects TRASH/SPAM; `unlabel_*` accepts them.** Reproduced from the real toolset rather than smoothed over: adding TRASH is a move that needs a companion change, removing it is not.
- **Address matching is a substring over name *and* address.** `from:`/`to:`/`cc:`/`bcc:` are `LIKE '%term%'` against both halves of the Person object, because the spec defines the argument as "a specific person", not an address - so `from:amara`, `from:"Amara Nwosu"` and the full address all have to find her. Three things fall out of that. It is fuzzy on purpose (`from:a` matches 17 of 18 addresses here), which is Gmail's behavior and not a defect. `from:` covers the `sender` column too, since `sender` is the address the wire reports and therefore the one an agent copies back into a query - they differ on EMAIL-0014 and matching only `from` left that message unreachable by the address it advertised. And there is **no `lower()`**: LIKE already folds ASCII case, and SQLite's `lower()` does not fold anything else, so it would be pure noise. `label:` stays exact - substring matching there would make `label:Board` silently also mean `Board Prep`.
- **Derived labels are projections, never stored.** `UNREAD`, `IMPORTANT` and `STARRED` are computed from `is_read`, `importance` and `flag.status`. Writing them into `messages.labels` would contradict `email_schema.md` ("keep state out of `labels[]`") and put the same fact in two places.
- **Two-phase thread search.** Phase 1 finds which threads contain a matching message; phase 2 re-reads each of those threads with the query dropped. That is what makes a whole conversation come back for a one-message match - and it is why the real tool's own description warns that `-is:starred` can return threads containing starred messages. Phase 1 also ranks by the newest message in each thread rather than its newest *matching* message, so the order is a property of the conversation and not of the query - see the trap below.
- **Reproducible "now".** `newer_than:`/`older_than:` resolve against the newest message date, not the wall clock. The dataset is fixed in mid-2026, so against a real clock the same query would return everything or nothing depending on when the benchmark ran.
- **Ignored operators are reported.** Anything recognized but unanswerable (`larger:`, `category:`, `OR`) comes back in `unsupportedOperators`. Silently dropping a filter makes "no results" indistinguishable from "that filter did nothing".

## Response schema notes

Things that look like bugs and are not:

- **`resultCountEstimate` is a JSON string** (`"19"`, not `19`). proto3 serializes int64 as a string.
- **`labelIds` holds IDs, not display names.** A message labeled `Verano` reports `Label_1`.
- **Label objects use `labelId`**, not `id`.
- **`color` is omitted on system labels**, not present-and-null.
- **`snippet` is the stored `body_preview`**, never recomputed from the body - the two differ (the preview flattens newlines) and the stored one is what the real response shows.
- **`sender` comes from the `sender` column, not `from`.** They differ on exactly one record (EMAIL-0014, sent by Verano's ops address on Amara's behalf) and `sender` is the one that says who transmitted it. `from:` therefore searches both columns - see "Address matching" above.
- **`Message` has no `threadId`, but `Draft` does.** `get_message`'s prose mentions one for `METADATA_ONLY`, but the schema has no such field. `Draft` is a separate wire type and carries it.
- **`create_draft` returns a whole `Draft`**, though its description says "returns only the unique ID". Its `outputSchema` is a full `Draft` and the schema wins. Same call: attachments are implemented, though the prose calls them "not supported yet".
- **The write tools return `{}`**, not a status or the modified record - except `create_label`, which has to return the allocated ID.

## Query parser

`_parse_gmail_query()` turns one query string into `(clauses, options)`. Supported:

`from:` `to:` `cc:` `bcc:` `deliveredto:` `subject:` `label:` `filename:` `rfc822msgid:` `is:read` `is:unread` `is:starred` `is:important` `has:attachment` `has:userlabels` `has:nouserlabels` `in:inbox` `in:sent` `in:spam` `in:trash` `in:archive` `in:anywhere` `after:` `before:` `newer_than:` `older_than:` `"exact phrase"` bare words, and `-` negation on any of them.

**v1 is AND-only.** A flat clause list, same shape as the Jira adapter's `_parse_jql` and the file server's `_parse_query`. `OR` and `{}` need a predicate tree; they are reported unsupported and their contents applied as AND, because dropping them would turn `{from:amy from:david}` into a match-everything query - a worse failure than over-narrowing.

### Traps worth knowing about

- **`before:` is exclusive of the named day.** Both date operators resolve to the *start* of their day.
- **Bare dates are calendar days, not instants**, resolved against `ACCOUNT_TIMEZONE` (`Europe/London`). In BST, `2026/07/01` begins at `2026-06-30T23:00:00Z`. A naive `"T00:00:00Z"` suffix mis-buckets every message in the first hour of a summer day.
- **Never compare against a bare date string.** `date < '2026-07-16'` matches only the midnight instant, because a bare date is a prefix that sorts before anything extending it.
- **`NOT MATCH 'a b'` means `NOT (a AND b)`**, which still admits a message containing only `a`. Each negated free-text term therefore needs its own separately-negated clause.
- **Free-text terms must be quoted for FTS5.** Its expression language reads `-` as negation and `:` as a column filter, so an unescaped `All-hands` is a syntax error, not a search.
- **An address operator's value must be wrapped by `_like()` in the parser.** The `FILTERS` fragment is a `LIKE`, so a bare value reaching it is compared against the pattern `amara` with no `%` and matches nothing - the same silent zero the substring change was made to remove. `_like()` also escapes `_` and `%`, without which an address containing an underscore would match addresses that merely resemble it.
- **`label:` on a derived label must route to the column predicate.** `label:IMPORTANT` cannot test `messages.labels`, because derived labels are never stored there - it has to resolve to the same predicate `is:important` uses, or it silently returns zero on a label `list_labels` advertises with a non-zero count.
- **Label exclusions are eligibility, not filtering**, so they re-apply in phase 2. Without that, a trashed message sharing a thread with a live one is visible by default and `include_trash` is unobservable.
- **Thread ordering must not use the `MAX(m.date)` the `GROUP BY` hands you for free.** That aggregate only sees rows surviving the `WHERE`, so it is the newest *matching* message, not the newest message in the thread - and the two silently agree on an unfiltered search, which is the worst case for noticing. They diverge the moment a filter excludes a thread's latest reply: `label:Verano -is:starred` sorted THR-0101 **last** despite it being the most recently active conversation, because its only unstarred messages are old. `find_thread_ids` therefore ranks by a correlated subquery that re-reads the whole thread. The golden suite pins this case as a regression test; every other golden there is a subsequence of the unfiltered order, which is the invariant the subquery buys.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATA_DIR` | `/data` | Root data directory (must contain an `email_json_data/` subdir) |
| `MCP_PORT` | `8014` | MCP HTTP listen port |
| `MAIL_NOW` | newest message date | RFC 3339 instant that `newer_than:`/`older_than:` measure from |

No `ACCESS_TOKEN`: the MCP HTTP endpoints take no auth.

## How to extend

### Adding a query operator

1. Add a branch in `_parse_gmail_query()` that emits `add("<field>", value, negated)`
2. Add a `"<field>"` row to `FILTERS` in `server.py` mapping it to a SQL fragment
3. If it needs a new column predicate, check whether an index would be used - `EXPLAIN QUERY PLAN` will say

The two steps are separate on purpose: the SQL lives with the schema and the syntax lives with the tool.

### Adding a derived label

Add it to `DERIVED_LABELS` in `server.py` **and** to `_DERIVED_LABEL_FILTERS` in `mcp_server.py`. An import-time assertion fails if you do only one - otherwise the label would be countable by `list_labels` but unsearchable by `label:`.

### Adding a write tool

1. Reuse a mutation function if the query shape already exists - `add_labels`/`remove_labels` take a `scope`, so a new label-ish tool usually needs no new SQL
2. If it does need new SQL, put it in `server.py` and route every statement through `execute()` or `_transaction()`, never `query()` - `query()` does not commit
3. Validate all arguments before the first write, and return `_error(...)` rather than raising
4. Multi-statement writes go in one `_transaction()`: a draft that inserted its message row but not its `search_index` row would be invisible to every free-text query while looking fine in a `SELECT`
5. Add the tool name to `expected_tools` in `tests/test_gmail_server.py` - and in `tests/test_gmail_reads_golden.py` too if you have that local suite, since it never calls a write tool but does assert the full registered set, so a new tool missing from that list fails discovery there too
6. Either restore what the test changed or assert a delta - the test client is class-scoped, so one database is shared by every test in the class. If a write leaks, it breaks the golden suite rather than your own test, because that suite's exact thread and message IDs assume an unmutated mailbox

## Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `0 messages loaded` | `DATA_DIR` has no `email_json_data/` subdir | Check that `$DATA_DIR/email_json_data/messages.json` exists |
| `ZoneInfoNotFoundError: Europe/London` | `tzdata` missing | `python:3.12-slim` ships no system zoneinfo; `tzdata` is in `requirements.txt` for this reason |
| `no such module: fts5` | SQLite built without FTS5 | Rare on Debian-based images; check `sqlite3.connect(":memory:").execute("PRAGMA compile_options")` |
| `no such column: <word>` | An unquoted term reached FTS5 | Every free-text term must go through `_fts_phrase()` |
| `label:X` returns 0 but `list_labels` shows a count | `X` is a derived label with no `_DERIVED_LABEL_FILTERS` entry | See "Adding a derived label" |
| `from:X` returns 0 for an address you can see in a result | The value was not wrapped by `_like()`, so a bare term met a `LIKE` fragment | Wrap it: the `FILTERS` entry is a substring match |
| `from:X` returns far more than expected | Substring matching, by design - `from:uk` matches every `.co.uk` address | Pass more of the address; this is Gmail's behavior |
| Relative dates return everything or nothing | `MAIL_NOW` set to a wall-clock instant | Unset it, or set it inside the dataset's range |
| A write "worked" but the JSON is unchanged | Expected - the store is `:memory:` and `/data` is `:ro` | Verify through a read tool, not by reading `$DATA_DIR` |
| A write vanished between runs | Same cause; the database is rebuilt at every startup | Nothing to fix - this is what keeps trials isolated |
| A label applied but `label:` returns 0 | The label is derived and has no `_DERIVED_LABEL_FILTERS` entry | See "Adding a derived label" |
| `label_thread` rejects `TRASH` | Intended; matches the real tool | Use `apply_sensitive_thread_label` |
| A trashed thread still answers `in:inbox` | `INBOX` was not removed alongside the add | Use `apply_sensitive_*`, not `label_*` |

## Testing locally

```bash
# Start the MCP server
DATA_DIR=/path/to/integrations/data python3 mcp_server.py

# The tracked suite (from docker/mcp-servers-2/tests/, no Docker needed)
pytest test_gmail_server.py -v

# Against the container
pytest test_gmail_server.py -v --mode integration

# With the local golden suite too, if you have it (see note below)
pytest test_gmail_server.py test_gmail_reads_golden.py -v
```

**Two suites, opposite strategies, and both are wanted.**

> **`test_gmail_reads_golden.py` is not in the repo.** It is a local-only suite: its assertions pin
> the mailbox exactly as authored, so it fails by design whenever mail is added, which makes it a
> poor gate on `main`. `test_gmail_server.py` is the tracked one. Everything below describes the
> golden suite because the split is the point — keep it if you have it, and expect a fresh clone not
> to.

`test_gmail_server.py` covers all 13 tools and asserts by *shape*: field types, envelope
keys, and one tool cross-checked against another (a thread `search_threads` returned is
fetchable by `get_thread`; a label's `threadsTotal` matches what `label:` returns). It
hardcodes no message IDs on purpose, so authoring new mail does not break it.

`test_gmail_reads_golden.py` covers the five read tools and asserts *exact* thread IDs,
message IDs and whole payloads for the mailbox as authored. It is the one that can tell
`from:amara` returning the wrong three conversations from it returning the right ones -
which shape assertions cannot. The tradeoff is deliberate: adding mail is expected to
fail some of its goldens, and the diff is then a report of what the new records did to
every query. **Re-verify a changed golden against `messages.json`, never by pasting in
what the server now returns** - that turns the suite into a rubber stamp. It also pins
`MAIL_NOW`, so its relative-date cases do not move when a later-dated message is added.

It calls no write tool, which is what keeps it exact: generated IDs (`DRAFT-0001`,
`Label_9`) are only predictable on a database no test has mutated, and the client is
class-scoped.

## Dependencies

- Python 3.12 (Dockerized); needs SQLite with FTS5, which the stock build has
- `fastmcp` for Streamable HTTP transport
- `tzdata` because the slim image has no system timezone database
- No database server, no external services - SQLite is in-process and in-memory
