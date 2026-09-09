# Gmail MCP Server — Full Build Plan

## 0. What this is
An end-to-end plan for adding a `mail` MCP server to **`docker/mcp-servers-2/`** (see §3.0 for
why that stack and not `harbor/` — both are live), mocking
the real Gmail MCP server (`gmailmcp.googleapis.com/mcp/v1`) against the synthetic dataset in
`integrations/data/email_json_data/`. Same architectural family as the existing `pm`, `crm`,
and `file-server` mocks — a self-contained MCP server process (optionally paired with a
separate, independent REST twin process for manual testing — see §1's note). No external
services. Calendar (`gcal`) follows the identical pattern once this is built and is out of
scope for this doc.

**Real tool list status:** a full, untruncated `tools/list` capture against the real Gmail MCP
endpoint confirmed **13 tools total** — 5 read, 8 write. §5 below covers all 13.

---

## 1. Architecture, end to end

**The MCP server is one self-contained process.** It is not a client of anything else at
runtime — no other process, container, or port sits between it and the agent:

Startup (once, before any tool call):
```
integrations/data/email_json_data/*.json      (git, source of truth, read-only)
        │  docker-compose bind mount, :ro, at container start
        ▼
   /data inside the mail-mcp container
        │  read ONCE at process startup — _load_data()
        ▼
   SQLite connection, in-process (this same Python process, no separate DB container)
```

Runtime (every tool call, after startup has populated SQLite above):
```
   MCP client (Claude Code CLI, or whatever agent harness)
        │  Streamable HTTP tool call (NOT stdio — this container has no attached client,
        │  any MCP client reaches it over the network like a real API)
        ▼
   FastMCP tool layer (mcp_server.py) — container port 8014, host 8015
        │  query against the SQLite connection loaded at startup
        ▼
   result, serialized back up the same path to the client
```

That's the whole runtime path: agent → tool call → `mcp_server.py` → SQLite (same process) →
result back. Nothing else is in that loop.

> **Note on `server.py` (a REST twin, not a dependency):** every sibling server in both live
> stacks also ships a `server.py` — a separate FastAPI app, built into a *second* container from
> the same image (e.g. `salesforce-server` on port 9002, vs. `salesforce-mcp-http` on 8012 — see
> [docker/mcp-servers-2/docker-compose.yml](../../../docker/mcp-servers-2/docker-compose.yml)). It exists purely so a
> human or a curl/test script can hit a plain REST API directly to sanity-check the data and
> query logic, without going through MCP tool calls at all. **`mcp_server.py` does not call
> that process over the network.** It only imports `server.py`'s Python functions and data
> structures as a library (`from server import ISSUES, _parse_jql, ...`) at its own startup, so
> the query logic isn't duplicated by hand — then it loads and holds its *own* private copy of
> the data in its *own* process. The REST container, if it's even running, is a completely
> separate, independent process with its own separate in-memory copy of the same source files.
> **Decision for `mail`: skip the REST twin entirely.** Nothing in the actual agent pipeline
> (the agent's `register_mcp_http` calls) ever touches the REST ports — build
> `mcp_server.py` (with its SQLite load/query logic inline or in a plain module, never wired to
> FastAPI or a port) and stop there, one process, one container.

### 1.1 MCP client config (the "how does the agent even see this" layer)
No new code — registration happens in the agent's setup script, same `register_mcp_http` helper
the other three servers already use in
[claude-code-mcp-2-setup.sh.j2](../../../terminal_bench/agents/installed_agents/claude_code_mcp_2/claude-code-mcp-2-setup.sh.j2)
(it polls the URL up to 20 times before registering, then `claude mcp list` to verify):

```bash
register_mcp_http "mail" "http://bench-mail-mcp:8014/mcp"
```

The URL is **container-name-based**, resolved over the `mcp-servers-2_default` Docker network
that `_connect_to_mcp_network()` attaches the task container to — not `host.docker.internal`,
which is the harbor pattern. The client then calls `tools/list` against that URL once at startup
and gets back whatever `mcp_server.py` exposes — nothing hand-maintained on the client side.

There is **no `mcp.json`** in this stack; URLs live as module constants in the agent (see §3.0).
The `mail-server/mcp-config.json` file is an informational stdio-style descriptor that mirrors
its siblings' and is not read at runtime.

### 1.2 Transport
**Streamable HTTP**, not stdio. stdio is for a subprocess wired to one client's stdin/stdout —
that's what the unrelated tmux-control server (`docker/mcp-server/`) uses. This server needs
to be reachable by any client over the network like a real vendor API, so it's a long-running
HTTP server:

```python
mcp.run(transport="http", host="0.0.0.0", port=MCP_PORT)
```

Identical to `pm`, `crm`, `file-server` — no new transport pattern to introduce.

### 1.3 Tool definitions
FastMCP auto-derives the JSON Schema `tools/list` response from Python function signatures —
there's no hand-written tool-list JSON anywhere. Each tool is a plain function decorated with
`@mcp.tool(description=..., annotations={...})`:

```python
@mcp.tool(
    description="Lists email threads from the authenticated user's Gmail account. ...",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
def search_threads(query: str = "", page_size: int = 20, page_token: str = "", view: str = "THREAD_VIEW_MINIMAL") -> str:
    ...
```

Parameter types/defaults become the `inputSchema`; the `description=` string is what the agent
actually reads to decide how to call the tool — same prompt-engineering role it plays in
`pm/mcp_server.py` (e.g. "Project key is 'MPL'", "Parameter name is 'jql' not 'query'").
`annotations` mirrors the real Gmail MCP server's per-tool metadata (`readOnlyHint` etc. — seen
verbatim in the real `tools/list` dump) and is directly supported by FastMCP's `@mcp.tool()`.

### 1.4 Tool-call handler (the "turn JSON into an actual query" layer)
Every tool function's body does the same three-step thing every existing mock does:
1. Parse/validate its own arguments (query string, IDs, enums).
2. Call a query function, in the same file/module, that runs SQL against the SQLite
   connection — this is plain Python code, all in the `mcp_server.py` process, not a call
   to any other server.
3. Serialize the result with `json.dumps(...)` and return it as a string — MCP tool results
   are always text content blocks, structured data doesn't cross the wire as a live object.

This mirrors `pm_search_issues` → `_parse_jql` → `_issue_matches_query` exactly; `mail`'s
`search_threads` → `_parse_gmail_query` → SQL `WHERE` clause is the same shape, just with a
real query engine underneath instead of a Python dict scan.

### 1.5 Data layer — SQLite, in-process

**Design rule: 1:1 with the source JSON — the table mirrors the file.** `messages` has exactly
one column per field in a `messages.json` record, with the **same name, same order, and the same
nesting kept intact as JSON**. Nothing is flattened into prefixed columns, nothing is split into
a junction table, and nothing is dropped for being derivable (`has_attachments`, `body_preview`,
`sender`, and the inline `attachments[]` all stay). A row and a source record are the same thing
in two encodings.

Three tables total. Only two things earn a table of their own, and both because they are
**separate source files with fields the message doesn't carry**:

| Table | Source file | Why separate |
|---|---|---|
| `messages` | `messages.json` | The anchor — one column per field |
| `labels` | `labels.json` | Carries `type` + `color`, which never appear on a message |
| `attachments` | `attachments.json` | Carries `extracted_text` (what FTS5 indexes), which the inline metadata lacks |

Everything else — including the *references to* labels and attachments — stays inside
`messages`, exactly as the JSON has it: `labels` is a JSON array of names, `attachments` is the
inline JSON metadata array.

**Multi-valued fields are one column holding an array, not many columns and not a junction
table.** `to`, `cc`, `bcc`, `labels`, `references`, and `attachments` are each a single TEXT
column holding the source JSON array. No `to_1`/`to_2`, no `message_recipients`, no
`message_labels`. Two payoffs beyond matching the file:

- **Order is preserved for free.** An array stays an array. A junction table stores a *set* —
  `["SENT","Verano"]` comes back `["Verano","SENT"]` unless you add a `position` column to undo
  the damage. That was a real bug in the earlier six-table draft, and this shape cannot have it.
- **Absence needs no sentinel.** `"cc": []` stores as `'[]'`; the omitted `attachments` key
  stores as SQL `NULL`. Both distinctions survive the round-trip.

**Querying an array is `json_each`, not a join** — and it's exact-match, not substring, so the
`to:amara@x` matches `amara@xyz` false-positive doesn't arise:

```sql
-- to:someone@example.com
SELECT * FROM messages m WHERE EXISTS (
    SELECT 1 FROM json_each(m."to") WHERE json_extract(value, '$.email') = ?);

-- label:Verano   /   in:inbox
SELECT * FROM messages m WHERE EXISTS (
    SELECT 1 FROM json_each(m.labels) WHERE value = ?);
```

Same shape every time, so it lives in one helper (`_has_in_array(column, path, value)`) rather
than being retyped per operator. `json1` is compiled into every modern SQLite — verified present,
along with FTS5.

> **Reserved words: `from` and `references` must always be written `"from"` and `"references"`.**
> Both are SQL keywords. A bare `from` is a syntax error and so is a qualified `m.from` — only
> the double-quoted form parses. This is the standing cost of naming columns after the source
> fields instead of renaming them to `from_email`/`references_json`. It's a real papercut on
> every query touching those two columns, but a loud one: it fails immediately at the first
> query rather than silently returning wrong rows.

Column order below is the source field order, so the DDL reads as the record's field list. `-- J`
marks a column holding serialized JSON (object or array) rather than a scalar.

```sql
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    message_id      TEXT NOT NULL,               -- RFC 2822 Message-ID, "<token@domain>"
    thread_id       TEXT NOT NULL,
    in_reply_to     TEXT,                        -- NULL on the 19 thread-openers
    "references"    TEXT NOT NULL CHECK(json_valid("references")),        -- J array, '[]'
    "from"          TEXT NOT NULL CHECK(json_valid("from")),              -- J {name,email}
    sender          TEXT NOT NULL CHECK(json_valid(sender)),              -- J {name,email}
    "to"            TEXT NOT NULL CHECK(json_valid("to")),                -- J array
    cc              TEXT NOT NULL CHECK(json_valid(cc)),                  -- J array, '[]'
    bcc             TEXT NOT NULL CHECK(json_valid(bcc)),                 -- J array, '[]'
    reply_to        TEXT NOT NULL CHECK(json_valid(reply_to)),            -- J array, '[]'
    subject         TEXT NOT NULL,
    date            TEXT NOT NULL,               -- RFC 3339, always ...Z (see §1.6)
    received_date   TEXT NOT NULL,
    body_preview    TEXT NOT NULL,               -- stored, never recomputed
    body            TEXT NOT NULL CHECK(json_extract(body,'$.content_type') IN ('text','html')),
                                                 -- J {content_type,content}
    is_read         INTEGER NOT NULL CHECK(is_read IN (0,1)),
    is_draft        INTEGER NOT NULL CHECK(is_draft IN (0,1)),
    importance      TEXT NOT NULL CHECK(importance IN ('low','normal','high')),
    flag            TEXT NOT NULL
        CHECK(json_extract(flag,'$.status') IN ('flagged','notFlagged','complete')),
                                                 -- J {status}
    has_attachments INTEGER NOT NULL CHECK(has_attachments IN (0,1)),
    labels          TEXT NOT NULL CHECK(json_valid(labels)),              -- J array of names
    attachments     TEXT CHECK(attachments IS NULL OR json_valid(attachments))
                                                 -- J array; NULL when the key is omitted
);

CREATE TABLE labels (                            -- from labels.json
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,                  -- the string that appears in messages.labels
    type  TEXT NOT NULL CHECK(type IN ('system','user')),
    color TEXT CHECK(color IS NULL OR json_valid(color))
                                                 -- J {text_color,background_color}; NULL = system
);

CREATE TABLE attachments (                       -- from attachments.json
    parent_message_id TEXT NOT NULL REFERENCES messages(id),
    id                TEXT NOT NULL,             -- unique within its message, not globally
    filename          TEXT NOT NULL,
    mime_type         TEXT NOT NULL,
    size              INTEGER NOT NULL,
    extracted_text    TEXT NOT NULL,             -- what FTS5 indexes; '' when unauthored
    PRIMARY KEY (parent_message_id, id)
);

CREATE VIRTUAL TABLE search_index USING fts5(
    message_id UNINDEXED, subject, body, attachment_text
);
```

Note `attachments`'s composite primary key: `email_schema.md` says an attachment `id` is unique
**within its message, not globally**, so `id` alone would be wrong the moment two messages both
carry an `ATT-0001`. The composite key is the source's own key (`parent_message_id` + `id`).

Indexes, created *after* the inserts:

```sql
CREATE INDEX idx_m_date   ON messages(date);
CREATE INDEX idx_m_thread ON messages(thread_id);
CREATE INDEX idx_m_msgid  ON messages(message_id);
CREATE INDEX idx_m_from   ON messages(json_extract("from", '$.email'));   -- expression index
CREATE INDEX idx_a_parent ON attachments(parent_message_id);
```

`idx_m_from` is an **expression index** — SQLite indexes the *result* of
`json_extract("from",'$.email')`, so `from:` lookups are index-served even though the value lives
inside a JSON blob. The query has to spell the expression identically for the planner to use it.
No equivalent exists for the array columns (`to`/`cc`/`labels`) — those scan. At 50 rows that is
microseconds, and it is the honest tradeoff of this shape: array filters are scans, not seeks.

**SQLite has no BOOLEAN type** — `is_read`/`is_draft`/`has_attachments` are `INTEGER` 0/1. And
SQLite's type declarations are *advisory* (column affinity, not enforcement — a string will sit
happily in an `INTEGER` column), which is why the `CHECK` constraints above are worth the two
lines: they turn a loader typo into an insert-time failure instead of a mystery empty result set
three tools later.

#### Load order (`setup_db()`, called at runtime — never at import)
Per §1's startup path and the precedent in the target stack — `_load_data()` at
[salesforce-server/mcp_server.py:222](../../../docker/mcp-servers-2/salesforce-server/mcp_server.py#L222)
and `_load_files()` at
[file-server/mcp_server.py:300](../../../docker/mcp-servers-2/file-server/mcp_server.py#L300), both inside
`if __name__ == "__main__":` — the
whole load runs inside `if __name__ == "__main__":` before `mcp.run(...)`, in one transaction —
so a mid-load failure leaves no half-populated DB:

```
connect(":memory:", check_same_thread=False)
  → CREATE TABLEs → PRAGMA foreign_keys = ON
  → labels        (labels.json + synthesized system labels)
  → messages      (one INSERT per record; json.dumps() the 11 JSON columns)
  → attachments   (FK parent must exist, so after messages)
  → search_index  (built last; reads subject/body from messages, text from attachments)
  → CREATE INDEXes
  → assertions
```

Only one FK survives the collapse — `attachments.parent_message_id → messages(id)` — so load
order is nearly unconstrained. `labels` has **no** FK from `messages.labels`: a JSON array can't
be a foreign key. That check moves to load assertion #4 (every name in `messages[].labels`
resolves to a `labels` row), which is where it effectively already lived in the six-table draft,
since the FK there was redundant with the assertion.

**Thread safety:** Starlette runs sync tool functions in a worker threadpool, so a module-level
connection opened in `__main__` raises `ProgrammingError` the first time two tool calls overlap.
`check_same_thread=False` plus a `threading.Lock` around executes. The data is read-only, so
lock contention is nil.

#### System labels must be synthesized
`labels.json` contains only the 8 user labels, but `messages[].labels` references four labels
that aren't in it — `INBOX` (31), `SENT` (17), `DRAFT` (1), `SPAM` (1). Without rows for these,
`list_labels` under-reports and `label:INBOX` has nothing to resolve against. Insert them at load
with `type='system'`, `id == name`, `color = NULL`.

Three more system labels appear in no message's `labels[]` but are still part of Gmail's surface,
because they are projections of scalar columns:

| Gmail label | Derived from | Count in dataset |
|---|---|---|
| `UNREAD` | `is_read = 0` | 5 |
| `IMPORTANT` | `importance = 'high'` | 6 |
| `STARRED` | `json_extract(flag,'$.status') = 'flagged'` | 7 |

These are **not** written into any message's `labels` array — that would corrupt the stored record
and break the 1:1 rule. They live in a small `DERIVED_LABELS` map from label ID to a column
predicate, consulted by both the `labelIds` response builder and the `label:`/`is:` query parser
before it falls through to a `json_each(labels)` test. They *do* get rows in `labels` so
`list_labels` can enumerate them, with counts coming from the predicate.

That is the split worth keeping straight: **`messages.labels` is what the file says; the
`labelIds` a tool serves is that array plus the derived labels merged in.** Storage stays
faithful, the projection is where Gmail's view gets assembled — exactly the adapter split
`email_schema.md` §"Serving the data" describes.

Two source values have no Gmail equivalent and are dropped from the *projection only*:
`flag.status = 'complete'` (2 records — Gmail has no "completed flag", so these are simply not
`STARRED`) and `importance = 'low'` (6 records — no `UNIMPORTANT` label exists). Both are still
stored in full.

#### NULL policy — three distinct cases, don't conflate them

| Case | Example | Storage |
|---|---|---|
| Structurally absent | `in_reply_to` on a thread-opener — there *is* no parent | **NULL** |
| Key omitted entirely | `attachments` on the 42 message with no files | **NULL** |
| Present but empty | `EMAIL-0012`'s body is `""` — known, and blank | **`''`, not NULL** |
| Empty array | `"cc": []` | **`'[]'`, not NULL** |
| Not applicable to variant | `color` on a system label | **NULL** |

Rows two and four are the pair that matters, and this schema is what lets them stay distinct:
**`attachments` is SQL `NULL` because the source omits the key; `cc` is `'[]'` because the source
writes an empty array.** That is the one asymmetry in `email_schema.md` — `attachments` is the
only omittable field — and it survives the round-trip as itself rather than being normalized away.
Verified: 42 messages lack the key, 42 rows are NULL.

Row three is the one that gets fumbled. `EMAIL-0012` is an attachment-only reply with a genuinely
empty body; storing NULL would make it ambiguous between "blank message" and "loader bug."

Counts in this dataset: 30 messages have `cc: []`, 47 `reply_to: []`, 49 `bcc: []`,
19 `references: []` — all stored as `'[]'`, none as NULL.

**Array order needs no defending here.** An array column stores the array, so authored order is
preserved by construction. (Worth recording because the six-table draft got this wrong: splitting
`labels[]` into a junction table turned it into a *set*, and `["SENT","Verano"]` came back
`["Verano","SENT"]` on 16 of 50 messages — a bug that needed a `position` column to undo. This
shape cannot express it.)

#### Attachments are in the source twice — and stay that way
The 8 messages with attachments carry an inline `attachments[]` array *and* have rows in
`attachments.json`. The duplication is in the source, so 1:1 fidelity requires keeping both: the
inline array is stored verbatim in `messages.attachments`, and `attachments.json` becomes the
`attachments` table.

They are not redundant. `attachments.json` is the superset — it adds `parent_message_id` and
`extracted_text` (what FTS5 indexes) — so **the table is authoritative for serving**, and
`messages.attachments` exists to reproduce the file. Verified: every shared field matches across
all 8, and 0 inline entries lack a table row. That agreement becomes a load assertion, so drift
between the two fails at startup rather than showing up as a `get_attachment` that disagrees with
the `search_threads` result that led to it.

#### Everything else nested stays nested
`from`, `sender`, `body`, `flag`, and `color` are single-valued objects — the six-table draft
flattened them into `from_name`/`from_email`/`body_content`/`flag_status`/`text_color`. They are
now stored as their source JSON, read with `json_extract`:

| Field | Read as |
|---|---|
| `from` / `sender` | `json_extract("from", '$.email')`, `'$.name'` |
| `body` | `json_extract(body, '$.content_type')`, `'$.content'` |
| `flag` | `json_extract(flag, '$.status')` |
| `color` (labels) | `json_extract(color, '$.background_color')` |

`flag` is the clearest case for why: it is a scalar wearing an object costume, and flattening it
to `flag_status` reads better right up until you round-trip and have to remember it was
`{"status": ...}`. `json_extract` costs a few characters at each use site; the flattening cost a
reconstruction rule in the loader *and* the response builder.

#### Storage NULLs vs. omitted response keys — separate concerns
A NULL column and an absent JSON key are unrelated. Omission happens in the response builder,
driven by `MessageFormat`/`ThreadView`, not by storage. The output rules:

- **Format gating** (`plaintextBody` under `MINIMAL`) → **omit the key entirely**; the real
  contract says "does not include," not "includes as null."
- **Empty collection** (no cc, no attachments) → **emit `[]`**, so the model never has to branch
  on a missing key.
- **Never emit JSON `null`.** A field is present with a value, or absent. `"plaintextBody": null`
  forces a special case that absence already handles.

#### Load-time assertions
All cheap at 50 rows, and each catches a distinct class of bug:

```
date & received_date match ^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$   → string comparison stays valid
has_attachments == (messages.attachments IS NOT NULL)               → denormalization drift
inline attachments[] agrees with the attachments table              → duplicate-source drift
every name in messages.labels resolves to a labels row              → missing system label
                                        (replaces the FK a JSON array can't have)
is_draft == ('DRAFT' in labels)                                     → flag/label consistency
every message row round-trips byte-identical to its source record   → all of the above, plus
                                        (the FULL record, attachments[] included)   key order
```

Verified against the current dataset: all six hold, **50/50 on the round-trip**. That last
assertion subsumes the rest and is the real guard on the 1:1 rule — and note it now covers the
*whole* record. The six-table draft could only ever round-trip 22 of 23 fields, because it
discarded the inline `attachments[]` as redundant; this schema reproduces the source record
exactly, including which keys are absent and the order they appear in.

Worth knowing why that assertion is worth its keep: in the six-table draft every one of the five
set-based checks above passed while `labels` was silently being reordered. Only strict ordered
comparison against the source caught it.

#### What Google actually does — a deliberate divergence
Gmail is not relational. Storage is a document store (Bigtable/Spanner lineage): one
denormalized blob per message keyed by `(user, message_id)`, no normalization, because there are
no cross-user joins and one mailbox is small. Search is an **inverted index**, not SQL —
`from:amara@x` is a fielded index term with a postings list, and `label:Verano` is a
postings-list intersection, neither of which is a join. Threads are materialized, not computed
per query.

This three-table shape is *closer* to Google's than the six-table draft was: one row per message
holding the whole document, with search served by an FTS5 inverted index rather than by joins.
The remaining divergence is that we still get SQL `WHERE` clauses and Google gets postings-list
intersections — which is the right trade at 50 rows, where a scan is free and hand-rolled index
machinery would be pure cost.

The honest summary of what SQLite is still buying us over the plain-dict approach the sibling
servers use (`FILE_INDEX: dict[str, FileEntry]` in `file-server`): **FTS5 full-text search across
subject + body + attachment text**, and **indexed date-range and `from:` lookups**. Not the
normalization — that part turned out to be cost without benefit, which is why it's gone.

#### Query functions
Real SQL, replacing hand-rolled scan-and-compare loops (compare
[file-server's `_parse_query`](../../../docker/mcp-servers-2/file-server/mcp_server.py#L103)):

```python
# Array membership: one helper, every multi-valued operator. `path` is None for an
# array of strings (labels), or '$.email' for an array of {name,email} objects.
def _in_array(column: str, path: str | None) -> str:
    target = "value" if path is None else f"json_extract(value, '{path}')"
    return f'EXISTS (SELECT 1 FROM json_each(m."{column}") WHERE {target} = ?)'


def find_threads(from_email=None, to_email=None, cc_email=None, subject_term=None,
                 after=None, before=None, has_attachment=None, label=None,
                 filename_pattern=None, text=None) -> list[str]:
    sql = "SELECT DISTINCT m.thread_id FROM messages m"
    where, params = [], []
    if text:                                    # FTS5 is the only join left
        sql += " JOIN search_index s ON s.message_id = m.id"
        where.append("search_index MATCH ?"); params.append(text)
    if from_email:                              # served by idx_m_from (expression index)
        where.append("json_extract(m.\"from\", '$.email') = ?"); params.append(from_email)
    if to_email:
        where.append(_in_array("to", "$.email")); params.append(to_email)
    if cc_email:
        where.append(_in_array("cc", "$.email")); params.append(cc_email)
    if label:                                   # caller resolves DERIVED_LABELS first
        where.append(_in_array("labels", None)); params.append(label)
    if filename_pattern:
        where.append("EXISTS (SELECT 1 FROM json_each(m.attachments) "
                     "WHERE json_extract(value, '$.filename') LIKE ?)")
        params.append(filename_pattern)
    if subject_term:
        where.append("m.subject LIKE ?"); params.append(f"%{subject_term}%")
    if after:
        where.append("m.date >= ?"); params.append(after)   # half-open interval —
    if before:
        where.append("m.date < ?");  params.append(before)  # see §1.6, NOT <=
    if has_attachment is not None:
        where.append("m.has_attachments = ?"); params.append(int(has_attachment))
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [r[0] for r in DB.execute(sql, params)]
```

Note what the array columns bought: `to:`/`cc:`/`label:`/`filename:` no longer add a `JOIN`, so
the query is a single-table scan with `EXISTS` subqueries and there is no `DISTINCT`-inflation
risk from a message matching two joined rows. The only remaining join is `search_index`, which is
a genuinely separate structure. Every operator above was run against the real 50 records:

| Operator | Result | Operator | Result |
|---|---|---|---|
| `from:amara.nwosu@…` | 6 | `label:Verano` | 15 |
| `to:ellie.ashworth@…` | 28 | `in:inbox` | 31 |
| `cc:` (any) | 20 | `is:unread` | 5 |
| `subject:renewal` | 7 | `is:starred` | 7 |
| `has:attachment` | 8 | `filename:*.pdf` | 6 |
| `after:2026-07-01 before:2026-07-17` | 34 | full-text `settlement` | 15 |

`list_labels` was also verified end to end on this schema — name, type, color, and per-label
total/unread counts for all 16 labels, with the derived ones counted from their predicates.

`search_threads` filters *messages* but returns whole *threads*, so it runs in two phases: match
→ `DISTINCT thread_id` → re-query every message in those threads **unfiltered** → shape per
`view` → paginate at thread level.

### 1.6 Dates
`date` and `received_date` are RFC 3339 with a literal `Z` on all 50 records, which makes
**lexicographic order identical to chronological order** — verified, string sort and `datetime`
sort produce the same sequence. So no date functions anywhere: plain `TEXT` comparison, and
`date >= ? AND date < ?` is a range scan the `idx_m_date` index can serve — confirmed by
`EXPLAIN QUERY PLAN`: `SEARCH messages USING INDEX idx_m_date (date>? AND date<?)`. Wrapping the
column (`date(date) >= …`) or using `LIKE '2026-07-14%'` defeats that index.

Gmail's `before:` operator is **exclusive of the named day**, so it must become `< next_day`,
not `<= that_day`. A bare date is a *prefix*, and a prefix sorts before anything extending it —
so `date <= '2026-07-16'` only matches messages sent at exactly midnight. Measured on this
dataset: `<= '2026-07-16'` returns 44 rows, the correct `< '2026-07-17'` returns 47.

Bare dates in a query are resolved against a hardcoded **`Europe/London`**, matching the
sibling calendar dataset (`time_zone: "Europe/London"` on 96 of 100 event start/end pairs) and
the single-tenant POV of the mailbox (owner: Ellie Ashworth, `@maplesoftware.net`). This mirrors
how Gmail itself works: there is no timezone parameter anywhere in the MCP toolset *or* the
Gmail REST API — Google resolves a bare date against the authenticated account's timezone
setting, one layer below the tool arguments. With no account to read, ours is a constant.

---

## 2. Technologies (identical stack to `pm`/`crm`/`file-server` — no new dependency category)

- **Python 3.12**
- **FastMCP 2.x** (`from fastmcp import FastMCP`) — tool definitions, Streamable HTTP transport
- **`mcp>=1.0.0`** — underlying protocol package FastMCP builds on
- **`sqlite3`** (Python stdlib, no new dependency) — in-process data store + FTS5 full-text index
- **Docker** — same base image pattern (`python:3.12-slim`), same `:ro` bind mount for data
- *(FastAPI + uvicorn — only needed if the optional REST twin from §1's note is built; not
  required for the MCP server itself)*

---

## 3. Directory structure

**Target stack: `docker/mcp-servers-2/`** (see §3.0). Files to create:

```
docker/mcp-servers-2/mail-server/
├── mcp_server.py        # SQLite load (setup_db) + query functions + FastMCP tool wrappers + mcp.run(...)
├── mcp-config.json      # informational stdio-style descriptor (not what's used at runtime)
├── Dockerfile           # same shape as docker/mcp-servers-2/file-server/Dockerfile
├── requirements.txt     # mcp>=1.0.0, fastmcp>=2.3.0  (no fastapi/uvicorn — no REST twin)
├── CLAUDE.md            # every sibling server dir has one
└── README.md            # every sibling server dir has one
```

Plus one test file: `docker/mcp-servers-2/tests/test_mail_server.py`, subclassing
`BaseMCPServerTests` (which contributes three free tests: tool discovery, schema validity,
unknown-tool rejection).

Per §1's note, no `server.py`/REST twin — one process, one container. Every sibling *does* ship
one, so this is a deliberate divergence: nothing in the agent path calls the REST ports. It also
means `requirements.txt` can drop `fastapi`/`uvicorn`, unlike the siblings'.

### 3.0 Which stack, and the port
Three MCP server generations exist in this repo and **two are live**:

| Directory | Transport | Ports | Status |
|---|---|---|---|
| `harbor/shared/mcp-servers/` | Streamable HTTP | 9001-9003 REST, 8011-8013 MCP | live (`harbor/mcp.json`) |
| `docker/mcp-servers-2/` | Streamable HTTP | 9001-9003 REST, 8011/8012/**8014→8013** MCP | **live — this is the target** |
| `docker/mcp-servers/` | SSE | 8001-8003 | legacy |

`docker/mcp-servers-2/` is wired to two registered agents (`ClaudeCodeMcp2Agent`,
`ClaudeCodeLiveMcpAgent` — both in `AgentName` and `agent_factory.py`) and has its own pytest
suite with unit and integration modes.

**Port: `8015` (host) → `8014` (container).** Host 8014 is already taken —
`file-server-mcp-http` maps `"8014:8013"`. Taken across both stacks: 8001-8003, 8011-8014,
9001-9003.

Registration touchpoints (existing files, one new entry each):
- [docker/mcp-servers-2/docker-compose.yml](../../../docker/mcp-servers-2/docker-compose.yml) — add
  `mail-mcp-http` (container `bench-mail-mcp`, `command: python mcp_server.py`, `"8015:8014"`,
  `MCP_PORT=8014`, `:ro` data mount, socket healthcheck). No REST service.
- [docker/mcp-servers-2/CLAUDE.md](../../../docker/mcp-servers-2/CLAUDE.md) — structure tree + port table
- [docker/mcp-servers-2/README.md](../../../docker/mcp-servers-2/README.md) — server table, and add
  `email_json_data/` to the documented `DATA_DIR` layout (it currently lists only pm/crm/kb/docs/
  transcripts)
- [docker/mcp-servers-2/scripts/run-docker.sh](../../../docker/mcp-servers-2/scripts/run-docker.sh) —
  health-endpoint echo list
- [docker/mcp-servers-2/tests/conftest.py](../../../docker/mcp-servers-2/tests/conftest.py) — add
  `"mail-mcp-http": 9014` to `SERVER_PORTS`
- [claude_code_mcp_2_agent.py](../../../terminal_bench/agents/installed_agents/claude_code_mcp_2/claude_code_mcp_2_agent.py)
  — add `DEFAULT_MAIL_MCP_URL = "http://bench-mail-mcp:8014/mcp"`, a ctor arg, **and an override
  of `_get_template_variables()`**. That method lives on the base `ClaudeCodeMcpAgent` and
  hardcodes three URLs; override it in the subclass rather than editing the base, or the legacy
  SSE agent breaks.
- [claude-code-mcp-2-setup.sh.j2](../../../terminal_bench/agents/installed_agents/claude_code_mcp_2/claude-code-mcp-2-setup.sh.j2)
  — add `register_mcp_http "mail" "{{ mail_mcp_url }}"`

**No data-artifact work.** `integrations/data/CHANGELOG.md` and `data-manifest.json` already
record all 50 messages, 8 attachments, and 8 labels (2026-07-31). The `integrations-data-sync`
pre-commit hook is scoped `files: ^integrations/data/`, and this change touches only `docker/`
and `terminal_bench/` — it won't fire. Only `ruff-check`/`ruff-format` apply. We are adding a
reader, not changing data.

**Note the URL form:** container-name-based (`http://bench-mail-mcp:8014/mcp`), not
`host.docker.internal`. The agent joins the `mcp-servers-2_default` Docker network and resolves
sibling containers by name — this is what the existing three URLs do. §1.1's
`host.docker.internal` form is the harbor pattern and does not apply here.

---

## 4. Data source

**Authoring authority: [ontology.md](../../../docs/ontology/ontology.md) §A.5.1 `Email`** (with
§A.5.3 `CalendarEvent` for the sibling dataset, and the integrity rules in §A.5.5). It is
machine-enforced by
[validate_email_calendar.py](../../../integrations/data/versioning/validate_email_calendar.py), which
runs in the `integrations-data-sync` pre-commit hook. Read it before changing any loader assumption.

Citations of `email_schema.md` throughout this document refer to the construction worksheet §A.5 was
folded from (ontology v1.3, 2026-08-11). That file is untracked and local; the ontology section is
the tracked source of truth and says the same thing about every field named here.

Four of its rules directly constrain §1.5's design, and all four are honored there:

- **"Every field is present with its default when empty. The only field ever dropped is the
  `attachments` array."** So absent-vs-empty is *meaningful*: a missing `attachments` key means
  no files; everything else is present-but-empty. This is exactly the NULL-vs-`'[]'` split in
  §1.5: `attachments` is SQL NULL, `cc` is `'[]'`, and `body`'s `content` is `''`.
- **"Set `sender` equal to `from` unless deliberately modeling a delegate / shared-mailbox
  send."** `EMAIL-0014` is exactly that case, so `sender` carries authored signal and cannot be
  dropped as redundant.
- **"`in_reply_to` / `references` point at `message_id` values, NOT at `id` values"** — the schema
  calls this "the #1 trap." Both are `MessageId` (`<token@domain>`), never `EMAIL-0001`.
- **"Keep state (`is_read`, `importance`, `flag`) out of `labels[]`."** This is why the three
  derived labels are a *projection* at response time, never written into `messages.labels`.

The schema also explicitly sanctions this whole server design in §"Serving the data (adapters)":
*"The stored records are vendor-neutral and inclusive. A mock MCP server projects each record
into whichever API it emulates… Storage does not constrain the served API."*

Files:
- `integrations/data/email_json_data/messages.json` — email messages
- `integrations/data/email_json_data/labels.json` — labels (system + user)
- `integrations/data/email_json_data/attachments.json` — attachment metadata + extracted text,
  keyed by `parent_message_id`

No `account_id`/CRM linkage fields exist in this data and none should be added: entity resolution
(participant email domain → CRM `Account.domain`) is the capability under test, not a shortcut the
mail server should provide. Ontology §A.5.0 states the same rule from the schema side — participants
are not authored, and identity resolves from the addresses on each record.

**Resolved (was an open item):** every `messages.json` record carries a `labels[]` array of
label *names*, joining to `labels.json`'s `name` field — so `label:` filtering and
`list_labels`'s `messagesTotal`/`threadsTotal`/`*Unread` counts are all computable. The dataset
is richer than this plan originally assumed: it also has `is_read`, `is_draft`, `importance`, and
`flag{status}`, which cover `is:unread`/`is:read`/`is:important`/`is:starred` too. Caveat handled
in §1.5: `labels.json` holds only the 8 user labels, so the 4 system labels referenced by
messages (`INBOX`, `SENT`, `DRAFT`, `SPAM`) plus the 3 derived ones (`UNREAD`, `IMPORTANT`,
`STARRED`) must be synthesized at load.

---

## 5. Tool set — all 13 real tools, read and write, with their filters

Confirmed complete: **5 read tools, 8 write tools.** Scope call (unchanged from earlier
discussion): **implement the read tools now; skip the write tools entirely**, consistent with
every other mock in this repo (`:ro` mount, no real CRUD — `file-server`'s `create_file`
returns a "not supported" stub rather than faking a write). Write tools are documented below
anyway so the full real surface is visible, not silently dropped.

### READ tools (implement) — 5

**`search_threads`** — lists threads matching a query. This is the primary entry point for
almost every real Gmail workflow: an agent doesn't usually know a thread ID up front, so it
searches first, then calls `get_thread`/`get_message` on whatever comes back.

- **Filters:** `query` (string) is the whole filtering surface for this tool — everything else
  is pagination/formatting, not filtering. It's Gmail's search-operator syntax, and it's the
  single richest filter of any tool in this spec. Grouped by what each operator narrows down:
  - *Who* — `from:`, `to:`, `cc:`, `bcc:`, `deliveredto:`, `list:` restrict by sender/recipient.
  - *When* — `after:`/`newer:`, `before:`/`older:` pin an absolute date boundary;
    `older_than:<n><unit>`/`newer_than:<n><unit>` (e.g. `newer_than:7d`) express it relative to
    "now" instead of a fixed date — this is the one that answers "meetings this week"-style
    asks without the caller computing a date first.
  - *What it says* — `subject:` narrows to the subject line only (vs. the whole body);
    `"exact phrase"` and `+word` tighten a bare-term match into an exact phrase or an
    unstemmed exact word; `AROUND n` is a proximity filter (two terms within `n` words of each
    other); `rfc822msgid:` filters by the exact protocol-level Message-ID header, useful for
    "find the message this one is a reply to" lookups.
  - *What it's attached to* — `has:attachment`, `has:drive`, `has:youtube`, `has:document`,
    `filename:` filter on the presence/type of attached or embedded content.
  - *Where it lives* — `label:`, `category:`, `in:`, `has:userlabels`, `has:nouserlabels`,
    `has:<color>-star` filter by which label/tab/folder a message sits in.
  - *Its state* — `is:important/starred/unread/read/muted` filters on per-message status flags.
  - *Its size* — `size:`, `larger:`, `smaller:` filter on byte size.
  - *How clauses combine* — plain juxtaposition is implicit `AND`; `OR`/`{ }` is "match any";
    `-` excludes a clause; `( )` groups clauses so combinators apply to the group, not just the
    next token (e.g. `subject:(dinner film)` means "subject contains dinner OR film").
  - **What this dataset can actually filter on today** (revised after the §1.5 schema pass —
    considerably more than originally assumed): `from:`, `to:`, `cc:`, `bcc:`, `subject:`, the
    four date-range forms, `has:attachment`, `filename:`, quoted phrases, and bare terms via
    FTS5 — plus `label:` and `in:` via `json_each(m.labels)`, and
    `is:unread`/`is:read`/`is:important`/`is:starred` against `is_read`/`importance`/
    `json_extract(flag,'$.status')`. `rfc822msgid:` also works, against `messages.message_id`.
    All twelve were run against the real 50 records — see the results table in §1.5.
    Still genuinely absent from the dataset: `size:`/`larger:`/`smaller:` (no byte size on
    messages), `category:` (no Gmail tabs), `has:drive`/`has:youtube`/`has:document`,
    `deliveredto:`, `list:`, `is:muted`, `has:<color>-star`. `OR`/`-`/`( )`/`AROUND` need a real
    boolean-expression parser rather than a flat AND-only clause list — a bigger lift, worth
    deferring until a specific task actually requires one (`AROUND n` maps cleanly onto FTS5's
    `NEAR(a b, n)` when that day comes).
- Pagination/formatting (not filters): `pageSize` (int, default 20, max 50), `pageToken`
  (opaque cursor for the next page), `view` (`THREAD_VIEW_MINIMAL` returns id/snippet/subject/
  from/to/cc/bcc/date/labelIds; `THREAD_VIEW_METADATA_ONLY` drops snippet/subject — same data,
  smaller payload), `includeTrash` (bool, default false — whether Trash-labeled threads are
  eligible for the query above, not a filter on message content itself).

**`get_thread`** — fetches full detail for one already-known thread.
- **Filters:** none. This tool takes no query — it's a direct ID lookup, not a search. Every
  message currently in the thread is returned regardless of date, sender, or content; any
  narrowing has to happen upstream, via `search_threads`, before this tool is ever called.
- Its only real parameter is a formatting control, not a filter: `messageFormat` — `MINIMAL`
  (id, snippet, subject, sender, recipients, date, labelIds), `METADATA_ONLY` (same minus
  snippet/subject), `FULL_CONTENT` (adds attachmentIds, plaintextBody, htmlBody, attachments;
  the default). Plus the required `threadId` itself, which is the lookup key, not a filter.

**`get_message`** — fetches full detail for one already-known message.
- **Filters:** none, for the same reason as `get_thread` — it's an ID lookup. `messageFormat`
  is the same three-value formatting enum, and `messageId` is the required lookup key.

**`list_labels`** — enumerates every label on the account.
- **Filters:** none — this tool has no query/filter parameter at all in the real API, only
  `pageSize`/`pageToken` for pagination. It's meant to be called once to discover label IDs
  (since `label_thread`/`label_message`/etc. all require an ID, not a display name), not to
  narrow anything.
- The real response also carries `messagesTotal`/`messagesUnread`/`threadsTotal`/
  `threadsUnread` per label — **no longer blocked** (see §4): counts come from an
  `EXISTS (SELECT 1 FROM json_each(m.labels) WHERE value = l.name)` correlated subquery, and the
  `*Unread` variants from `AND m.is_read = 0`. Verified for all 16 labels. For the three derived
  labels (`UNREAD`/`IMPORTANT`/`STARRED`) the counts come from the column predicate instead,
  per §1.5.

**`list_drafts`** (real tool exists; excluded from this mock)
- **Filters:** `query` accepts the same operator family as `search_threads` — its own
  description explicitly calls out `subject:`, `from:`, `to:`, `newer_than:`, `has:attachment`,
  `is:unread` as supported — plus `pageSize`/`pageToken`/`view`
  (`DRAFT_VIEW_FULL`/`DRAFT_VIEW_METADATA_ONLY`) for formatting.
- **Excluded** — but not for the reason originally stated. A draft concept *does* exist:
  `EMAIL-0044` has `is_draft: true` and the `DRAFT` label. It's a single record, though, so
  implementing the tool would return a one-item list and exercise nothing — excluded as not worth
  the surface area, and trivially addable later if a task needs it. (Correction: the earlier claim
  that "no draft concept exists" was wrong.)

### WRITE tools (documented, not implemented) — 8

**`create_draft`** — composes a new draft.
- **Inputs (not filters — this tool takes no query, only the fields it writes):** `to[]`,
  `cc[]`, `bcc[]` (plain email addresses only, no display-name format), `subject`, `body`
  (plain text), `htmlBody`, `replyToMessageId` (threads the draft under an existing message),
  `attachments[]` (base64 `content`, `filename`, `mimeType`, `inline` flag; combined size
  capped at 25MB).
- **Excluded** — no mutation path exists in this mock; would require a write-capable store
  behind the `:ro` mount, contradicting the rest of this repo's design.

**`label_thread`** — adds one or more labels to an entire thread (all messages currently in it,
plus any future messages added to it).
- **Inputs:** `threadId` (required, the target), `labelIds[]` (required — accepts system label
  IDs like `INBOX`/`STARRED`/`UNREAD`/`IMPORTANT`, or user-defined label IDs looked up via
  `list_labels` first; the tool takes IDs, never display names).
- **Excluded** — mutating. (Not blocked on data: the label join exists per §4.)

**`unlabel_thread`** — removes one or more labels from an entire thread.
- **Inputs:** `threadId` (required), `labelIds[]` (required) — same ID rules as `label_thread`.
- **Excluded** — mutating. (Not blocked on data: the label join exists per §4.)

**`apply_sensitive_thread_label`** — moves a whole thread to Trash or marks it Spam.
- **Inputs:** `threadId` (required), `labelOption` (enum: `TRASH` | `SPAM`).
- **Excluded** — mutating. A `SPAM` label does exist (`EMAIL-0047`); `TRASH` does not.

**`label_message`** — adds one or more labels to a single message rather than its whole thread.
- **Inputs:** `messageId` (required), `labelIds[]` (required) — same system/user ID rules as
  `label_thread`. Real description explicitly points to `apply_sensitive_message_label` for
  Trash/Spam instead of using this tool for that.
- **Excluded** — mutating. (Not blocked on data: the label join exists per §4.)

**`unlabel_message`** — removes one or more labels from a single message.
- **Inputs:** `messageId` (required), `labelIds[]` (required).
- **Excluded** — mutating. (Not blocked on data: the label join exists per §4.)

**`apply_sensitive_message_label`** — moves a single message to Trash or marks it Spam.
- **Inputs:** `messageId` (required), `labelOption` (enum: `TRASH` | `SPAM`). Real description
  notes drafts can be targeted too, discoverable via `list_drafts`.
- **Excluded** — mutating. A `SPAM` label does exist (`EMAIL-0047`); `TRASH` does not.

**`create_label`** — creates a new label, with nested-label support via `/`-separated display
names (e.g. `Projects/Alpha/Sprint-1`), auto-creating parent labels by default.
- **Inputs:** `displayName` (required), `color` (optional — `backgroundColor`/`textColor`, both
  constrained to a fixed ~90-value hex palette the real API enforces, not free-form hex),
  `autoCreateParentLabels` (bool, default true).
- **Excluded** — mutating; also, this mock's `labels` table would need to be write-capable to
  support it, contradicting the `:ro`-mount convention this repo uses everywhere else.

---

## 6. Response envelope conventions
Match the real Gmail shapes exactly per-tool (`nextPageToken`, `resultCountEstimate` on
`search_threads`; flat `{id, messages}` on `get_thread`) rather than reusing this repo's
Jira-style (`startAt`/`total`) or Drive-style (`page`/`page_size`) envelopes — each mock in this
repo stays faithful to its own real vendor's response shape, and Gmail's is neither of those.

---

## 7. Build order
1. ~~Resolve the §4 open item (label↔message join key)~~ — **done**, see §4. Messages carry
   `labels[]`; `label:` filtering and `list_labels` counts are unblocked.
2. ~~Finalize SQLite schema~~ — **done**, see §1.5. **Three tables + FTS5**, DDL is final.
3. ~~Verify the schema loads the real data~~ — **done**. The §1.5 DDL was built and all 50
   messages / 8 labels / 8 attachments loaded. Verified:
   **all 50 messages round-trip byte-identical to their full source record** — `attachments[]`
   included, key order included, and NULL-vs-`'[]'` preserved (42 NULL `attachments`, matching
   the 42 messages that omit the key); all six load assertions pass; CHECK constraints reject bad
   rows; lexicographic date order == chronological; the `before:` trap reproduces (44 vs 47);
   `EXPLAIN QUERY PLAN` confirms `SEARCH … USING INDEX idx_m_date` for date ranges and
   `USING INDEX idx_m_from (<expr>=?)` for `from:`; `sender != from` on exactly one record
   (EMAIL-0014); FTS5 `MATCH`/`NEAR()`/`subject:` filters work; all 12 Gmail operators return
   correct counts (table in §1.5); `list_labels` produces name/type/color + total/unread for all
   16 labels; `get_attachment` resolves on the composite key.
   Row counts: **messages 50, labels 16 (8 user + 8 system), attachments 8, search_index 50.**

   **This step is what produced the three-table shape.** The schema went into verification as six
   tables (`messages` flattened into prefixed columns, plus `message_recipients` and
   `message_labels` junction tables) and came out as three. Two findings drove it:
   - **Splitting a JSON array into a junction table loses its order.** `labels: ["SENT","Verano"]`
     came back `["Verano","SENT"]` on 16 of 50 messages, because a junction table stores a *set*.
     It needed a `position` column purely to undo damage the split had caused. Notably, all five
     set-based assertions passed while this was live — only strict ordered comparison caught it.
   - **The junction tables bought nothing.** Their one real benefit, FK enforcement on label
     names, was already covered by load assertion #4 in Python. Meanwhile they cost two tables,
     extra load code, and that bug. Storing arrays as arrays removed all three and made the
     round-trip *stronger* (23 of 23 fields instead of 22).
4. Implement `mcp_server.py`: SQLite load + query functions, then the `@mcp.tool()` wrappers
   on top, then `mcp.run(...)`. No `server.py`/REST twin per §1's note.
   - `setup_db()` + the six load-time assertions from §1.5
   - `_in_array()` helper (§1.5) — every multi-valued operator routes through it
   - `_to_source_record(row)` — the inverse of the loader; the round-trip assertion needs it, and
     it is the single place that knows which 11 columns hold JSON
   - response builders `_message()` / `_thread()` / `_label()` / `_attachment_metadata()` —
     one per shared `$defs` type, per §8
   - `_label_ids()` projection: `messages.labels` array ∪ the three `DERIVED_LABELS` predicates
   - `_parse_gmail_query()`: flat AND-only clause list, `from:`/`to:`/`cc:`/`subject:`/
     `label:`/`is:`/`has:attachment`/`filename:`/the four date forms/quoted phrases/bare terms
   - the 5 read tools
5. Auth block, **default off** (`MAIL_REQUIRE_AUTH=false`), via FastMCP's `Middleware` +
   `add_middleware()` + `get_http_headers()` + a `contextvars.ContextVar` for the resolved
   principal. Note: **no MCP server in either live stack has auth today.** `docker/mcp-servers-2`
   does set a per-service `ACCESS_TOKEN` on its REST services (`_check_auth()`, a static bearer
   check), but it is not passed to any `*-mcp-http` service. Off by default means the tools work
   identically whether or not the `--header` registration plumbing (unverified — the setup
   script's `register_mcp_http` registers bare URLs) is ever wired.
6. Wire registration — the 7 touchpoints in §3.0.
7. Smoke test: `./scripts/run-docker.sh mail-mcp-http`, then `curl localhost:8015/mcp` and
   confirm `tools/list` matches [gmail-mcp-toolset-spec.md](gmail-mcp-toolset-spec.md).
   Then `uv run pytest docker/mcp-servers-2/tests/test_mail_server.py`.

---

## 8. Output schema — model on the real Gmail MCP `outputSchema`, not ad hoc

§5 above only documents each tool's *inputs*. The real `tools/list` capture also carries a full
`outputSchema` per tool, reusing a small set of shared `$defs` types across multiple tools —
`Message` (used by `get_thread`, `get_message`, `search_threads`), `Thread` (used by
`get_thread`, `search_threads`), `AttachmentMetadata`, `Label`, `LabelColor` (used by
`list_labels`, `create_label`). **When the output schema gets written up (deferred, per the
build order above — schema/implementation work, not part of this planning doc), model it
directly on the real Gmail MCP server's `outputSchema` shapes rather than inventing our own
response shape.** Concretely: one shared message-building function producing the same field
set as the real `Message` type, reused by every tool that returns a message, mirroring how the
real API reuses one `$defs.Message` across tools — not three slightly different message shapes
depending on which tool built them. Same for `Thread`/`Label`/`AttachmentMetadata`. This keeps
the mock's responses byte-for-byte consistent with what a real Gmail MCP client would expect,
the same fidelity goal §6 already states for envelope-level fields like `nextPageToken`.
