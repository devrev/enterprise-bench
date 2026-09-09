"""
Mail Server - data layer.

Loads the vendor-neutral email dataset (integrations/data/email_json_data/) into an
in-process SQLite database and holds the connection for mcp_server.py.

Named server.py to match its siblings, and for the same reason: this is the module
that owns DATA_DIR, the load function, the module-level store, and the query engine
that mcp_server.py imports. What it does *not* have is the FastAPI app the siblings
bolt onto the bottom of their own server.py - nothing in the agent path calls a REST
port for mail, so there are no routes, no _check_auth, and no /health. mcp_server.py
is the only front door, and it reaches this engine by in-process function call rather
than over HTTP - exactly as the sibling mcp_server.py files reach theirs.

Also unlike its siblings, the store is SQLite rather than a dict. Two things need it:
FTS5 full-text search across subject + body + attachment text, and indexed
date-range / sender lookups. See gmail-mcp-server.md section 1.5.

Schema: 3 tables + 1 FTS5 index. `messages` has exactly one column per field in a
source record - same names, same order, nesting kept intact as JSON - so a row and a
source record are the same thing in two encodings. `labels` and `attachments` are
separate only because they are separate source files carrying fields a message does
not have (type/color, and extracted_text).

The database is writable; the dataset is not. `:memory:` means the store is a private
per-process copy, so the write tools mutate rows here and the JSON under the `:ro`
mount is never touched. Mutations therefore last for the life of the container and
vanish on restart, which is the isolation a benchmark trial wants: every run starts
from the same mailbox. Nothing is ever written back to $DATA_DIR.

Usage:
    from server import DATA_DIR, setup_db, query, query_one, execute

Environment:
    DATA_DIR - path to the data directory (default: /data)
"""

import json
import os
import re
import sqlite3
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

#: The subdirectory of DATA_DIR holding the three source files.
EMAIL_SUBDIR = "email_json_data"


def email_dir() -> Path:
    """
    Where the source JSON lives, resolved at call time rather than at import.

    Deliberately a function and not an `EMAIL_DIR` constant: the test suite configures
    a server by assigning `module.DATA_DIR = data_dir` and then calling the loader
    (see tests/base/base_mcp_test.py), which a derived constant computed at import time
    would silently ignore - it would still point at the default /data.
    """
    return DATA_DIR / EMAIL_SUBDIR

# ---------------------------------------------------------------------------
# In-memory store (SQLite, this process only)
# ---------------------------------------------------------------------------

#: The one connection, opened by setup_db(). Starlette runs sync tool functions in a
#: worker threadpool, so check_same_thread=False plus a lock is required - a plain
#: connection raises ProgrammingError the first time two tool calls overlap.
DB: sqlite3.Connection | None = None

#: Guards every execute, and every multi-statement write via _transaction().
#:
#: Not optional, and not just for the writes. A :memory: database is private to its
#: connection - two connections to ":memory:" are two different empty databases - so
#: one shared connection is the only option, and sqlite3 then forbids cross-thread use
#: unless check_same_thread=False, which only removes the check rather than making the
#: connection safe. Cursors share connection state, so overlapping executes interleave
#: and corrupt each other's fetches: no exception, just a SELECT returning the wrong
#: number of rows. One assistant turn emitting two mail tool calls is enough to hit it,
#: since FastMCP hands each sync tool function to a different threadpool worker.
_DB_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

#: Column order mirrors the source field order, so this reads as the record's field
#: list. Columns holding serialized JSON are marked `-- J`. "from" and "references"
#: are SQL reserved words: they MUST be double-quoted everywhere, including when
#: qualified (m."from"). A bare `from` is a syntax error.
SCHEMA = """
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    message_id      TEXT NOT NULL,               -- RFC 2822 Message-ID, "<token@domain>"
    thread_id       TEXT NOT NULL,
    in_reply_to     TEXT,                        -- NULL on thread-openers
    "references"    TEXT NOT NULL CHECK(json_valid("references")),        -- J array
    "from"          TEXT NOT NULL CHECK(json_valid("from")),              -- J {name,email}
    sender          TEXT NOT NULL CHECK(json_valid(sender)),              -- J {name,email}
    "to"            TEXT NOT NULL CHECK(json_valid("to")),                -- J array
    cc              TEXT NOT NULL CHECK(json_valid(cc)),                  -- J array
    bcc             TEXT NOT NULL CHECK(json_valid(bcc)),                 -- J array
    reply_to        TEXT NOT NULL CHECK(json_valid(reply_to)),            -- J array
    subject         TEXT NOT NULL,
    date            TEXT NOT NULL,               -- RFC 3339, always ...Z
    received_date   TEXT NOT NULL,
    body_preview    TEXT NOT NULL,               -- stored, never recomputed
    body            TEXT NOT NULL                 -- J {content_type, content}
        CHECK(json_extract(body, '$.content_type') IN ('text', 'html')),
    is_read         INTEGER NOT NULL CHECK(is_read IN (0, 1)),
    is_draft        INTEGER NOT NULL CHECK(is_draft IN (0, 1)),
    importance      TEXT NOT NULL CHECK(importance IN ('low', 'normal', 'high')),
    flag            TEXT NOT NULL
        CHECK(json_extract(flag, '$.status') IN ('flagged', 'notFlagged', 'complete')),
    has_attachments INTEGER NOT NULL CHECK(has_attachments IN (0, 1)),
    labels          TEXT NOT NULL CHECK(json_valid(labels)),              -- J array of names
    attachments     TEXT CHECK(attachments IS NULL OR json_valid(attachments))
                                                 -- J array; NULL when the key is omitted
);

CREATE TABLE labels (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,                  -- the string appearing in messages.labels
    type  TEXT NOT NULL CHECK(type IN ('system', 'user')),
    color TEXT CHECK(color IS NULL OR json_valid(color))
                                                 -- J {text_color,background_color}; NULL = system
);

CREATE TABLE attachments (
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
"""

#: Created after the inserts - building an index first only slows the load.
INDEXES = [
    'CREATE INDEX idx_m_date   ON messages(date)',
    'CREATE INDEX idx_m_thread ON messages(thread_id)',
    'CREATE INDEX idx_m_msgid  ON messages(message_id)',
    # No index on the address fields. from:/to:/cc:/bcc: are substring matches (see
    # address_like), and a LIKE pattern opening with % gives the planner no prefix to
    # seek on, so an expression index over json_extract("from", '$.email') would be
    # built at every startup and never used. Those clauses scan 50 rows instead.
    'CREATE INDEX idx_a_parent ON attachments(parent_message_id)',
]

#: messages column order == source field order. Used to build the INSERT and, in
#: reverse, to reconstruct a source record.
MESSAGE_COLUMNS = [
    "id", "message_id", "thread_id", "in_reply_to", "references", "from", "sender",
    "to", "cc", "bcc", "reply_to", "subject", "date", "received_date", "body_preview",
    "body", "is_read", "is_draft", "importance", "flag", "has_attachments", "labels",
    "attachments",
]

#: Fields stored as serialized JSON rather than as a scalar.
JSON_COLUMNS = {
    "references", "from", "sender", "to", "cc", "bcc", "reply_to", "body", "flag",
    "labels", "attachments",
}

#: Fields stored as INTEGER 0/1 - SQLite has no BOOLEAN type.
BOOL_COLUMNS = {"is_read", "is_draft", "has_attachments"}

#: The only omittable field in the source (email_schema.md): absent means "no files".
#: Stored as SQL NULL, which is what distinguishes it from an authored empty array.
OMITTABLE_COLUMN = "attachments"

#: labels.json holds only user labels, but messages[].labels references system labels
#: that have no record. Synthesized at load so label lookups resolve and list_labels
#: can enumerate them. id == name for system labels.
SYSTEM_LABELS = [
    "INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT",
]

#: Gmail labels that are projections of scalar columns, not stored strings. Never
#: written into messages.labels - that would corrupt the record and contradict
#: email_schema.md ("keep state out of labels[]"). Merged in at response time.
#: Iteration order is the order they are appended to labelIds, chosen to match the
#: real server's output (INBOX, Label_1, Label_5, IMPORTANT, STARRED on EMAIL-0001).
DERIVED_LABELS = {
    "UNREAD": "is_read = 0",
    "IMPORTANT": "importance = 'high'",
    "STARRED": "json_extract(flag, '$.status') = 'flagged'",
}

#: The write half of DERIVED_LABELS: (SET clause to add the label, SET clause to remove
#: it). label_message("EMAIL-0001", ["UNREAD"]) cannot append to messages.labels the way
#: every other label does, because a derived label is never stored there - it has to
#: write the column the label is projected from, or the read side would keep reporting
#: the old value from a column nobody updated.
#:
#: Removing IMPORTANT is lossy, and unavoidably so: Gmail has two states where
#: email_schema.md has three ('low', 'normal', 'high'), so un-importanting a message
#: lands it on 'normal' and a message that was 'low' cannot be restored. Choosing
#: 'normal' over 'low' keeps it out of the low bucket, which is a real filter value.
DERIVED_LABEL_WRITES = {
    "UNREAD": ("is_read = 0", "is_read = 1"),
    "IMPORTANT": ("importance = 'high'", "importance = 'normal'"),
    "STARRED": (
        "flag = json_set(flag, '$.status', 'flagged')",
        "flag = json_set(flag, '$.status', 'notFlagged')",
    ),
}

# A derived label that can be read but not written would make label_message succeed and
# change nothing. Asserted at import rather than left to a test, matching the same
# pairing check mcp_server.py makes for the query side.
assert set(DERIVED_LABEL_WRITES) == set(DERIVED_LABELS), (
    "DERIVED_LABEL_WRITES and DERIVED_LABELS disagree: "
    f"{sorted(set(DERIVED_LABELS) ^ set(DERIVED_LABEL_WRITES))}"
)

#: Adding TRASH or SPAM to a message is documented as *moving* it there, so INBOX comes
#: off at the same time. Without this the thread stays in the inbox and `in:inbox` keeps
#: returning it, which makes "moved to Trash" observably false.
SENSITIVE_LABELS = ["TRASH", "SPAM"]

_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# Record <-> row conversion
# ---------------------------------------------------------------------------

def _to_row(record: dict) -> list:
    """
    Flatten a source message record into a messages row, in MESSAGE_COLUMNS order.

    JSON-valued fields are serialized; bools become 0/1; an omitted `attachments`
    key becomes None (SQL NULL). Everything else passes through untouched.
    """
    row = []
    for column in MESSAGE_COLUMNS:
        if column == OMITTABLE_COLUMN:
            # Omitted key -> NULL. An authored empty array would be '[]', which is
            # a different thing; the source never writes one for attachments.
            value = record.get(column)
            row.append(json.dumps(value) if value is not None else None)
        elif column in JSON_COLUMNS:
            row.append(json.dumps(record[column]))
        elif column in BOOL_COLUMNS:
            row.append(int(record[column]))
        else:
            row.append(record[column])
    return row


def to_record(row: sqlite3.Row) -> dict:
    """
    Rebuild the exact source record from a messages row - the inverse of _to_row().

    This is the only place that knows which columns hold JSON, and it is what the
    round-trip assertion checks. Key insertion order matches MESSAGE_COLUMNS, so the
    reconstructed dict compares equal to the source record key-for-key.
    """
    record = {}
    for column in MESSAGE_COLUMNS:
        value = row[column]
        if column == OMITTABLE_COLUMN:
            if value is not None:      # NULL -> omit the key entirely
                record[column] = json.loads(value)
        elif column in JSON_COLUMNS:
            record[column] = json.loads(value)
        elif column in BOOL_COLUMNS:
            record[column] = bool(value)
        else:
            record[column] = value
    return record


# ---------------------------------------------------------------------------
# Query helpers (thread-safe wrappers around the shared connection)
# ---------------------------------------------------------------------------

def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Run a SELECT and return all rows."""
    with _DB_LOCK:
        return DB.execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    """Run a SELECT and return the first row, or None."""
    with _DB_LOCK:
        return DB.execute(sql, params).fetchone()


def scalar(sql: str, params: tuple = ()) -> object:
    """Run a SELECT and return the first column of the first row."""
    row = query_one(sql, params)
    return row[0] if row is not None else None


def execute(sql: str, params: tuple = ()) -> int:
    """
    Run one INSERT/UPDATE/DELETE, commit it, and return the number of rows changed.

    The commit is the point. query() would run the same statement - DB.execute takes
    any SQL - but it never commits, leaving the write in an open transaction on the
    shared connection. Any later rollback() then reverts it, including one raised by an
    unrelated tool call that failed: two tools sharing one uncommitted transaction means
    one tool's error handling silently undoes the other's successful write. Committing
    per statement makes each write independently durable.

    rowcount never reaches the wire (the label tools return {}), but it says whether the
    statement matched anything, which is how a caller distinguishes a real change from a
    no-op and how the tests assert one happened.
    """
    with _DB_LOCK:
        cursor = DB.execute(sql, params)
        DB.commit()
        return cursor.rowcount


@contextmanager
def _transaction():
    """
    Hold the lock across several statements and commit once, or roll all of them back.

    For writes that are only correct as a unit - create_draft inserts a messages row,
    its attachments rows and its FTS index row, and a half-applied version of that
    would leave a message whose body is unsearchable or whose has_attachments column
    disagrees with the attachments table (load assertions 2 and 3).

    Yields the connection so the body can call DB.execute directly: going through
    execute() here would deadlock on the non-reentrant lock, and would commit
    mid-transaction besides.
    """
    with _DB_LOCK:
        try:
            yield DB
            DB.commit()
        except Exception:
            DB.rollback()
            raise


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def _read_json(filename: str) -> list[dict]:
    """Read one source file from the email data directory. Missing file -> empty list."""
    path = email_dir() / filename
    if not path.exists():
        return []
    with path.open() as handle:
        return json.load(handle)


def _load_labels(labels: list[dict]) -> None:
    """
    Insert label records: the synthesized system labels first, then labels.json.

    System labels are inserted before the user labels so a user label that happens to
    share a system name would collide loudly on the UNIQUE(name) constraint rather
    than shadowing it.
    """
    for name in SYSTEM_LABELS:
        DB.execute(
            "INSERT INTO labels (id, name, type, color) VALUES (?, ?, 'system', NULL)",
            (name, name),
        )

    for label in labels:
        color = label.get("color")
        DB.execute(
            "INSERT INTO labels (id, name, type, color) VALUES (?, ?, ?, ?)",
            (
                label["id"],
                label["name"],
                label["type"],
                json.dumps(color) if color else None,
            ),
        )


def _load_messages(messages: list[dict]) -> None:
    """Insert one row per message record, columns in source field order."""
    columns = ", ".join(f'"{c}"' for c in MESSAGE_COLUMNS)
    placeholders = ", ".join("?" for _ in MESSAGE_COLUMNS)
    sql = f"INSERT INTO messages ({columns}) VALUES ({placeholders})"
    for record in messages:
        DB.execute(sql, _to_row(record))


def _load_attachments(attachments: list[dict]) -> None:
    """
    Insert attachment records from attachments.json.

    Runs after messages: parent_message_id is a real foreign key, the only one in
    the schema (a JSON array cannot be a foreign key, so messages.labels has none -
    that check lives in _assert_dataset_integrity instead).
    """
    for attachment in attachments:
        DB.execute(
            "INSERT INTO attachments "
            "(parent_message_id, id, filename, mime_type, size, extracted_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                attachment["parent_message_id"],
                attachment["id"],
                attachment["filename"],
                attachment["mime_type"],
                attachment["size"],
                attachment["extracted_text"],
            ),
        )


def _load_search_index(messages: list[dict], attachments: list[dict]) -> None:
    """
    Build the FTS5 index last: it reads from both messages and attachments.

    A plain FTS5 table rather than an external-content one, because external-content
    can only mirror a single table and this indexes subject + body from messages plus
    extracted_text from attachments. The data is read-only after load, so no sync
    triggers are needed.
    """
    text_by_parent: dict[str, list[str]] = {}
    for attachment in attachments:
        text_by_parent.setdefault(attachment["parent_message_id"], []).append(
            attachment["extracted_text"]
        )

    for record in messages:
        DB.execute(
            "INSERT INTO search_index (message_id, subject, body, attachment_text) "
            "VALUES (?, ?, ?, ?)",
            (
                record["id"],
                record["subject"],
                record["body"]["content"],
                " ".join(text_by_parent.get(record["id"], [])),
            ),
        )


# ---------------------------------------------------------------------------
# Load-time assertions
# ---------------------------------------------------------------------------

class DatasetError(RuntimeError):
    """Raised when the loaded data violates an invariant from email_schema.md."""


def _assert_dataset_integrity(messages: list[dict]) -> None:
    """
    Six checks, all cheap at this size, each catching a distinct class of bug.

    Failing loudly at startup is the point: a silent load bug surfaces later as an
    empty result set from a tool call, which is far harder to trace.
    """
    problems: list[str] = []

    # 1. Timestamps are UTC with a literal Z, which is what makes lexicographic
    #    comparison equal chronological comparison - the basis for every date filter.
    for record in messages:
        for field in ("date", "received_date"):
            if not _UTC_TIMESTAMP.match(record[field]):
                problems.append(f"{record['id']}: {field} is not UTC ...Z: {record[field]!r}")

    # 2. has_attachments agrees with the presence of the attachments key.
    for row in query("SELECT id, has_attachments, attachments FROM messages"):
        if bool(row["has_attachments"]) != (row["attachments"] is not None):
            problems.append(
                f"{row['id']}: has_attachments={row['has_attachments']} but "
                f"attachments is {'present' if row['attachments'] else 'NULL'}"
            )

    # 3. The source stores attachment metadata twice - inline on the message and in
    #    attachments.json. 1:1 fidelity keeps both, so they must agree.
    orphans = query(
        """
        SELECT m.id AS message_id, json_extract(j.value, '$.id') AS attachment_id
        FROM messages m, json_each(m.attachments) j
        LEFT JOIN attachments a
               ON a.parent_message_id = m.id
              AND a.id = json_extract(j.value, '$.id')
        WHERE a.id IS NULL
        """
    )
    for row in orphans:
        problems.append(
            f"{row['message_id']}: inline attachment {row['attachment_id']} "
            "has no attachments.json record"
        )

    # 4. Stands in for the foreign key a JSON array cannot have.
    unknown = query(
        """
        SELECT DISTINCT j.value AS name
        FROM messages m, json_each(m.labels) j
        WHERE j.value NOT IN (SELECT name FROM labels)
        """
    )
    for row in unknown:
        problems.append(f"label {row['name']!r} on a message has no labels row")

    # 5. is_draft and the DRAFT label have to tell the same story.
    for record in messages:
        if record["is_draft"] != ("DRAFT" in record["labels"]):
            problems.append(
                f"{record['id']}: is_draft={record['is_draft']} but "
                f"DRAFT {'is' if 'DRAFT' in record['labels'] else 'is not'} in labels"
            )

    # 6. The strongest check, and the one that subsumes the rest: every row rebuilds
    #    its source record exactly - same values, same keys present, same key order.
    #    Set-based checks pass happily while an array is being silently reordered;
    #    only strict ordered comparison catches that.
    for record in messages:
        row = query_one("SELECT * FROM messages WHERE id = ?", (record["id"],))
        rebuilt = to_record(row)
        if json.dumps(rebuilt) != json.dumps(record):
            problems.append(f"{record['id']}: does not round-trip to its source record")

    if problems:
        raise DatasetError(
            f"{len(problems)} dataset integrity problem(s):\n  "
            + "\n  ".join(problems[:20])
            + ("\n  ..." if len(problems) > 20 else "")
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_db(verify: bool = True) -> sqlite3.Connection:
    """
    Build the in-process database and load the dataset. Call once, at startup.

    Runs at runtime rather than import time (matching the sibling servers, which call
    _load_data() / _load_files() inside `if __name__ == "__main__"`), and inside a
    single transaction, so a mid-load failure leaves no half-populated database.
    """
    global DB

    messages = _read_json("messages.json")
    labels = _read_json("labels.json")
    attachments = _read_json("attachments.json")

    DB = sqlite3.connect(":memory:", check_same_thread=False)
    DB.row_factory = sqlite3.Row
    DB.executescript(SCHEMA)
    DB.execute("PRAGMA foreign_keys = ON")

    try:
        _load_labels(labels)
        _load_messages(messages)
        _load_attachments(attachments)     # after messages: FK parent must exist
        _load_search_index(messages, attachments)
        for statement in INDEXES:
            DB.execute(statement)
        DB.commit()
    except Exception:
        DB.rollback()
        raise

    if verify:
        _assert_dataset_integrity(messages)

    return DB


def stats() -> dict[str, int]:
    """Row counts per table, for the startup banner and the health check."""
    return {
        table: scalar(f"SELECT count(*) FROM {table}")
        for table in ("messages", "labels", "attachments", "search_index")
    }


# ---------------------------------------------------------------------------
# Query layer - SQL predicates
# ---------------------------------------------------------------------------
# Everything below turns already-parsed filter clauses into SQL. Parsing Gmail
# query syntax is mcp_server.py's job; this module only knows about columns.

def in_array(column: str, path: str | None) -> str:
    """
    SQL fragment testing whether a JSON array column contains a value (one ? param).

    `path` is None for an array of plain strings (labels, references), or a JSON path
    like '$.email' for an array of objects. The comparison is exact, which is why
    `label:` uses this and the address operators use _like_in_array() instead: a
    substring `label:Board` would silently also mean `Board Prep`.

    No index can serve this - json_each has to walk the array - so these clauses scan.
    At 50 rows that is microseconds, and it is the accepted cost of keeping the arrays
    1:1 with the source instead of exploding them into junction tables.
    """
    target = "value" if path is None else f"json_extract(value, '{path}')"
    return f'EXISTS (SELECT 1 FROM json_each(m."{column}") WHERE {target} = ?)'


def _like_in_array(column: str, *paths: str) -> str:
    """
    Same as in_array() but LIKE: for filename:, and for the recipient arrays.

    Several paths are ORed *inside* the EXISTS, because a recipient matches when either
    half of that one recipient matches. Hoisting the OR out would ask whether some
    recipient's address matches or some recipient's name does - a different, wronger
    question that a two-recipient message could satisfy by halves.
    """
    tests = [f"json_extract(value, '{path}') LIKE ? ESCAPE '\\'" for path in paths]
    return f'EXISTS (SELECT 1 FROM json_each(m."{column}") WHERE {" OR ".join(tests)})'


#: Both halves of a Person object. Gmail's from: takes "a specific person", not an
#: address, so `from:amara`, `from:"Amara Nwosu"` and the full address all have to find
#: her - which means testing the display name as well as the mailbox.
ADDRESS_PATHS = ("$.email", "$.name")


def address_like(*columns: str) -> str:
    """
    Person-substring fragment over object columns - `from` and `sender`.

    The array counterpart is `_like_in_array(column, *ADDRESS_PATHS)`; this one exists
    because `from`/`sender` hold a single Person rather than a list, so there is no
    json_each to walk and the OR is flat.

    Two consequences worth stating plainly, both Gmail's behavior rather than accidents:

      - It is deliberately fuzzy. `from:uk` matches six people in this dataset and
        `from:a` matches seventeen. A query can match more than its author intended.
      - It cannot be index-served. A LIKE pattern with a leading % has no prefix for the
        planner to seek on, so these clauses scan - which is why idx_m_from is gone.

    LIKE is already case-insensitive for ASCII, so there is no lower() here. Adding one
    would be dead weight: it changes nothing for ASCII and SQLite's lower() does not fold
    non-ASCII either, so `lower(x) LIKE lower(y)` is exactly as blind to `É`/`é` as the
    bare comparison. Only `=` needs the folding, and no address filter uses `=` any more.

    Every param is the same term repeated once per placeholder; build_where() does that.
    """
    tests = [
        f"json_extract(m.\"{column}\", '{path}') LIKE ? ESCAPE '\\'"
        for column in columns
        for path in ADDRESS_PATHS
    ]
    return "(" + " OR ".join(tests) + ")"


#: One entry per supported filter field: (sql_fragment, value_transform).
#: A transform of None passes the parsed value through unchanged. Adding an operator
#: to the parser means adding a row here - the two are deliberately separate so the
#: SQL lives with the schema and the syntax lives with the tool.
#:
#: The four address operators are substring matches over both the address and the display
#: name - see address_like(). `from` covers the `sender` column too, because `sender` is
#: what the wire reports and is therefore what an agent copies back into a query: they
#: differ on EMAIL-0014, sent by an ops address on Amara's behalf, and matching only
#: `from` would make that message unreachable by the one address the agent was shown.
FILTERS: dict[str, tuple[str, object]] = {
    "from":        (address_like("from", "sender"), None),
    "to":          (_like_in_array("to", *ADDRESS_PATHS), None),
    "cc":          (_like_in_array("cc", *ADDRESS_PATHS), None),
    "bcc":         (_like_in_array("bcc", *ADDRESS_PATHS), None),
    "label":       (in_array("labels", None), None),
    "subject":     ("m.subject LIKE ? ESCAPE '\\'", None),
    "filename":    (_like_in_array("attachments", "$.filename"), None),
    "rfc822msgid": ("m.message_id = ?", None),
    # Half-open interval, and both take the *start* of the named day because Gmail's
    # `before:` is exclusive of it. The parser resolves the day to an instant before we
    # see it: comparing against the bare text (`m.date < '2026-07-16'`) would match only
    # messages sent at exactly midnight, since a bare date is a prefix and sorts before
    # anything extending it. See gmail-mcp-server.md section 1.6.
    "after":       ("m.date >= ?", None),
    "before":      ("m.date < ?", None),
    "has_attachment": ("m.has_attachments = ?", int),
    "is_read":     ("m.is_read = ?", int),
    # No Gmail operator emits this one: `is:draft` is not in the query language, and
    # `in:draft` resolves to the DRAFT label like any other folder. It exists for
    # list_drafts, which restricts to drafts by its own contract rather than by anything
    # the user typed - so the tool appends this clause itself after parsing.
    "is_draft":    ("m.is_draft = ?", int),
    "importance":  ("m.importance = ?", None),
    "flag_status": ("json_extract(m.flag, '$.status') = ?", None),
    "has_userlabels": (
        (
            "EXISTS (SELECT 1 FROM json_each(m.labels) "
            f"WHERE value NOT IN ({', '.join('?' * len(SYSTEM_LABELS))}))"
        ),
        None,
    ),
    "text": (
        "m.id IN (SELECT message_id FROM search_index WHERE search_index MATCH ?)",
        None,
    ),
}

#: Labels that make a message ineligible unless the query asks for them. Gmail hides
#: spam and trash from every search, and this tool's description adds drafts to that
#: list ("Drafts are explicitly excluded by default by the tool").
DEFAULT_EXCLUDED_LABELS = ["SPAM", "TRASH", "DRAFT"]


def build_where(clauses: list[dict]) -> tuple[str, list]:
    """
    Turn a flat clause list into a WHERE body and its parameters.

    Each clause is {"field": str, "value": Any, "negated": bool}. Clauses are ANDed;
    a negated clause is wrapped in NOT (...), which is why every fragment above is a
    self-contained expression rather than something that only works positionally.

    An unknown field is skipped rather than raising: the operator catalog is much
    larger than what this dataset can answer (no byte sizes, no Gmail categories), and
    silently ignoring an unsupported operator is what Gmail itself does. The tool layer
    is what reports which operators it dropped.
    """
    where: list[str] = []
    params: list = []

    for clause in clauses:
        entry = FILTERS.get(clause["field"])
        if entry is None:
            continue
        fragment, transform = entry
        value = clause["value"]

        # A fragment may take several params or none at all. has_userlabels is the one
        # case whose params differ from each other (one per system label); everywhere
        # else a multi-placeholder fragment is the same term tested several ways - the
        # address operators OR over $.email and $.name - so the value is repeated.
        placeholders = fragment.count("?")
        if placeholders == 0:
            values = []
        elif clause["field"] == "has_userlabels":
            values = list(SYSTEM_LABELS)
        else:
            values = [transform(value) if transform else value] * placeholders

        where.append(f"NOT ({fragment})" if clause["negated"] else fragment)
        params.extend(values)

    return " AND ".join(where), params


def find_thread_ids(
    clauses: list[dict],
    excluded_labels: list[str] | None = None,
) -> list[str]:
    """
    Phase 1 of a thread search: thread IDs with at least one matching message.

    Returns them most-recently-active first, where "active" is the newest date of any
    message in the thread - not of the matching message. A thread whose only match is
    old but which is still being replied to sorts as recent, which is what a mail
    client shows.

    That distinction is why the sort key is a correlated subquery rather than the
    MAX(m.date) the GROUP BY makes available for free: the aggregate only sees the rows
    that survived the WHERE, so it is the newest *matching* message, not the newest
    message. The two agree on an unfiltered search and diverge the moment a filter
    excludes a thread's latest reply - `label:Verano -is:starred` sorted THR-0101 last
    despite it being the most recent conversation, because its only unstarred messages
    are old. The subquery re-reads the whole thread, so the ranking is a property of the
    conversation and not of the query, which is what a mail client shows.

    Note the asymmetry this creates, and that the real tool's description warns about
    explicitly: filters select *messages*, results are whole *threads*. A thread comes
    back if any one message matches, so `-is:starred` returns threads that also contain
    starred messages. Phase 2 (thread_messages) deliberately re-queries unfiltered.
    """
    where, params = build_where(clauses)
    conditions = [where] if where else []

    for label in excluded_labels if excluded_labels is not None else DEFAULT_EXCLUDED_LABELS:
        conditions.append(f"NOT ({in_array('labels', None)})")
        params.append(label)

    sql = (
        "SELECT m.thread_id, ("
        "SELECT MAX(a.date) FROM messages a WHERE a.thread_id = m.thread_id"
        ") AS last_date FROM messages m"
        + (" WHERE " + " AND ".join(conditions) if conditions else "")
        + " GROUP BY m.thread_id ORDER BY last_date DESC, m.thread_id"
    )
    return [row["thread_id"] for row in query(sql, tuple(params))]


def thread_messages(
    thread_id: str,
    excluded_labels: list[str] | None = None,
) -> list[sqlite3.Row]:
    """
    Phase 2: the messages in a thread, chronologically, with no *query* filter applied.

    Ascending by date because Thread.messages is specified as "ordered
    chronologically". Ties break on id so the order is total and stable.

    `excluded_labels` is not a query filter - it is the same eligibility rule phase 1
    applied, re-applied per message. Without it a thread returned by a search would
    display its own trashed and spam messages, so `include_trash=False` would be
    unobservable whenever a trashed message shares a thread with a live one. Passing
    None (the default) applies no exclusion, which is what an explicit get_thread by ID
    wants: that tool is documented as returning every message in the thread.
    """
    sql = "SELECT * FROM messages m WHERE thread_id = ?"
    params: list = [thread_id]
    for label in excluded_labels or []:
        sql += f" AND NOT ({in_array('labels', None)})"
        params.append(label)
    return query(sql + " ORDER BY date, id", tuple(params))


def get_message_row(message_id: str) -> sqlite3.Row | None:
    """One message by its id (EMAIL-0001), or None."""
    return query_one("SELECT * FROM messages WHERE id = ?", (message_id,))


def thread_exists(thread_id: str) -> bool:
    """
    Whether any message carries this thread_id.

    There is no threads table - a thread is an emergent grouping - so existence is a
    question about messages, and an empty thread cannot exist by construction.
    """
    return scalar("SELECT 1 FROM messages WHERE thread_id = ? LIMIT 1", (thread_id,)) is not None


def label_rows(label_id: str | None = None) -> list[sqlite3.Row]:
    """
    Every label with its four computed message/thread counts, or just one of them.

    The counts are derived, never stored, so they cannot drift from the messages. Two
    kinds of label are counted two different ways:

      - stored labels match on the name appearing in messages.labels
      - the three DERIVED_LABELS match on a column predicate instead (a derived label
        never appears in messages.labels - see the DERIVED_LABELS comment)

    A CASE per derived label is built into the join condition rather than run as
    separate queries, so one pass over messages produces every count.

    `label_id` narrows to one label, for create_label's response - the only write tool
    that returns a populated body, and it returns a full Label with counts. Filtering
    here rather than building a second query keeps one definition of what a Label's
    counts mean; a fresh label's counts are all zero, but by computation rather than by
    a hardcoded zero that would be wrong the moment the label is used.
    """
    derived_cases = " ".join(
        f"WHEN l.name = '{name}' THEN ({predicate.replace('is_read', 'm.is_read')})"
        for name, predicate in DERIVED_LABELS.items()
    )
    # A HAVING-free filter on the outer table: applied before the GROUP BY, so the
    # counts for the surviving label are unaffected.
    where = "WHERE l.id = ?" if label_id is not None else ""
    sql = f"""
        SELECT l.id, l.name, l.type, l.color,
               COUNT(m.id)                                                  AS messages_total,
               COUNT(CASE WHEN m.is_read = 0 THEN 1 END)                    AS messages_unread,
               COUNT(DISTINCT m.thread_id)                                  AS threads_total,
               COUNT(DISTINCT CASE WHEN m.is_read = 0 THEN m.thread_id END) AS threads_unread
        FROM labels l
        LEFT JOIN messages m
               ON CASE {derived_cases}
                       ELSE EXISTS (
                           SELECT 1 FROM json_each(m.labels) WHERE value = l.name
                       )
                  END
        {where}
        GROUP BY l.id, l.name, l.type, l.color
        ORDER BY l.type DESC, l.id
    """
    return query(sql, (label_id,) if label_id is not None else ())


def label_id_by_name() -> dict[str, str]:
    """
    Map every stored label name to its label id ("Verano" -> "Label_1").

    The wire format reports labelIds, not display names, but messages.labels stores
    names (that is what the source JSON has), so every response has to translate. The
    map is small and rebuilt per call rather than cached - and now that create_label
    exists, caching would also need invalidating on every write, not just by setup_db().
    """
    return {row["name"]: row["id"] for row in query("SELECT id, name FROM labels")}


def resolve_label(name_or_id: str) -> str | None:
    """
    Map a label id or display name to the name stored in messages.labels.

    The real `label:` operator takes IDs ("accepts label IDs, not display names"), but
    an agent that skipped list_labels will send the display name, and Gmail's own web
    search accepts it. Both resolve; the id is tried first so an id always wins.
    Matching is case-insensitive on the name, since "verano" is what a model tends to
    emit for a label displayed as "Verano".
    """
    row = query_one(
        "SELECT name FROM labels WHERE id = ? OR lower(name) = lower(?)",
        (name_or_id, name_or_id),
    )
    return row["name"] if row else None


def draft_rows(clauses: list[dict]) -> list[sqlite3.Row]:
    """
    Messages matching the clauses, newest first - list_drafts' query shape.

    Deliberately not the two-phase thread search. A draft is a standalone record, not a
    conversation: list_drafts returns Drafts, not Threads, so there is no phase 2 that
    re-reads a whole thread with the filter dropped. One query, one row per result.

    Restricting to drafts is the caller's job, via an is_draft clause - this function
    applies the clause list it is given and nothing else, exactly like find_thread_ids.
    Ordering is newest-first because a draft list is a work queue; ties break on id so
    the order is total and pagination cannot repeat or skip a row.
    """
    where, params = build_where(clauses)
    sql = "SELECT * FROM messages m"
    if where:
        sql += f" WHERE {where}"
    return query(sql + " ORDER BY m.date DESC, m.id", tuple(params))


# ---------------------------------------------------------------------------
# Mutation layer - label writes
# ---------------------------------------------------------------------------
# The write tools' SQL, kept here with the schema for the same reason the read
# predicates are: mcp_server.py knows Gmail semantics and never writes SQL. Every
# function below takes label *names* (the string stored in messages.labels), because
# resolving an agent-supplied id or display name to a name is the tool layer's job -
# that is where resolve_label() is already called from for `label:`.
#
# Scope is a WHERE clause, not a function: "label a thread" and "label a message" are
# the same statement over a different row set, so they share one implementation rather
# than getting one function per tool.

#: (column, sql) for each scope a label write can target.
_SCOPES = {
    "message": "id = ?",
    "thread": "thread_id = ?",
}


def _scope_clause(scope: str) -> str:
    """The WHERE body selecting the rows a labelling call applies to."""
    try:
        return _SCOPES[scope]
    except KeyError:
        raise ValueError(f"Unknown scope {scope!r}; expected one of {sorted(_SCOPES)}") from None


def add_labels(scope: str, target_id: str, names: list[str]) -> int:
    """
    Add labels to one message or to every message in a thread. Returns rows changed.

    Two different writes hide behind one verb, because two kinds of label are stored
    two different ways:

      - a stored label is appended to the messages.labels JSON array
      - a derived label (UNREAD/IMPORTANT/STARRED) is a projection of a scalar column,
        so it writes that column instead - appending the string would corrupt the record
        and still leave the column, which the read side actually reads, unchanged

    Both are idempotent, which the spec requires (`idempotentHint: true` on label_thread
    and label_message). The array append carries a NOT EXISTS guard so a repeat call is
    a no-op instead of producing ["INBOX", "INBOX"]; a column assignment is idempotent
    by nature.

    json_insert with '$[#]' appends, preserving the authored order of the existing
    entries - order is real data here (email_schema.md fixes it), and the junction-table
    design this schema replaced was rejected precisely because it scrambled it.
    """
    where = _scope_clause(scope)
    changed = 0
    with _transaction() as db:
        for name in names:
            if name in DERIVED_LABEL_WRITES:
                set_clause = DERIVED_LABEL_WRITES[name][0]
                sql = f"UPDATE messages SET {set_clause} WHERE {where}"
                params: tuple = (target_id,)
            else:
                sql = (
                    "UPDATE messages SET labels = json_insert(labels, '$[#]', ?) "
                    f"WHERE {where} "
                    "AND NOT EXISTS (SELECT 1 FROM json_each(labels) WHERE value = ?)"
                )
                params = (name, target_id, name)
            changed += db.execute(sql, params).rowcount
    return changed


def remove_labels(scope: str, target_id: str, names: list[str]) -> int:
    """
    Remove labels from one message or from every message in a thread.

    The array case rebuilds the array without the named value rather than deleting an
    element by index, because an index would have to be looked up first and would shift
    under a second removal in the same call. ORDER BY j.key is what preserves the order
    of the entries that survive - json_each yields rows, and a rebuilt array is only in
    the original order if the rows are consumed in key order.

    coalesce(..., json('[]')) handles removing the last label: json_group_array over
    zero rows is NULL, and the column is NOT NULL with a json_valid CHECK, so the empty
    array has to be written explicitly.

    Derived labels invert their column write. Removing IMPORTANT is lossy (see
    DERIVED_LABEL_WRITES); removing UNREAD means marking read, which is the sense Gmail
    gives it.
    """
    where = _scope_clause(scope)
    changed = 0
    with _transaction() as db:
        for name in names:
            if name in DERIVED_LABEL_WRITES:
                set_clause = DERIVED_LABEL_WRITES[name][1]
                sql = f"UPDATE messages SET {set_clause} WHERE {where}"
                params: tuple = (target_id,)
            else:
                sql = (
                    "UPDATE messages SET labels = ("
                    "  SELECT coalesce(json_group_array(j.value), json('[]')) "
                    "  FROM json_each(messages.labels) j WHERE j.value <> ? "
                    "  ORDER BY j.key"
                    f") WHERE {where}"
                )
                params = (name, target_id)
            changed += db.execute(sql, params).rowcount
    return changed


def target_exists(scope: str, target_id: str) -> bool:
    """Whether the message id / thread id a write is aimed at exists at all."""
    where = _scope_clause(scope)
    return scalar(f"SELECT 1 FROM messages WHERE {where} LIMIT 1", (target_id,)) is not None


# ---------------------------------------------------------------------------
# Mutation layer - inserts
# ---------------------------------------------------------------------------

#: Width of the numeric part of every generated id, matching the dataset's own
#: zero-padded 4-digit form (EMAIL-0001, THR-0101, ATT-0001, Label_1 is the exception).
_ID_DIGITS = 4


def _next_number(table: str, column: str, prefix: str) -> int:
    """
    One past the highest number currently used by ids of this prefix.

    Reads MAX rather than keeping a counter, so it stays correct across a rollback: a
    counter incremented inside a transaction that then failed would leak the number and
    the next insert would skip an id. MUST be called with the lock already held (it uses
    the raw connection, not query()) - the read and the insert that consumes its result
    have to be atomic, or two concurrent create_draft calls both see the same MAX and
    the second collides on the primary key.
    """
    pattern = f"{prefix}%"
    highest = DB.execute(
        f"SELECT MAX(CAST(substr({column}, ?) AS INTEGER)) FROM {table} "
        f"WHERE {column} LIKE ?",
        (len(prefix) + 1, pattern),
    ).fetchone()[0]
    return (highest or 0) + 1


def _next_id(table: str, column: str, prefix: str, digits: int = _ID_DIGITS) -> str:
    """Allocate the next id for a prefix ('DRAFT-' -> 'DRAFT-0001'). Lock must be held."""
    return f"{prefix}{_next_number(table, column, prefix):0{digits}d}"


def insert_draft(
    record: dict,
    attachments: list[dict],
    header_id_for: Callable[[str], str] | None = None,
) -> str:
    """
    Insert a draft as a messages row, plus its attachment rows and its FTS index row.

    One transaction for three writes, because a partial version breaks an invariant the
    load asserts: a messages row with has_attachments = 1 and no attachments rows fails
    assertion 3, and a row with no search_index entry is invisible to every free-text
    query while looking perfectly fine in a SELECT.

    The caller builds the record (mcp_server.py owns what a draft's date, sender and
    preview should be); this function owns id allocation and the writes. The id is
    allocated inside the transaction so it cannot be handed out twice - messages.id is
    the primary key, so two concurrent callers reserving it beforehand would be a real
    collision, not the harmless one allocate_attachment_ids() tolerates.

    `header_id_for` exists because of that ordering: the RFC 2822 message_id is derived
    from the row id, which the caller cannot know before this function picks it. Rather
    than move the naming convention down here - the dataset builds it from the message
    number and the thread number, which is a dataset fact, not a column fact - the caller
    passes a function of the allocated id and gets called back. Omit it to keep whatever
    message_id the record already carries.

    Returns the allocated message id.
    """
    with _transaction() as db:
        message_id = _next_id("messages", "id", "DRAFT-")
        record = {"id": message_id, **{k: v for k, v in record.items() if k != "id"}}
        if header_id_for is not None:
            record["message_id"] = header_id_for(message_id)

        columns = ", ".join(f'"{c}"' for c in MESSAGE_COLUMNS)
        placeholders = ", ".join("?" for _ in MESSAGE_COLUMNS)
        db.execute(
            f"INSERT INTO messages ({columns}) VALUES ({placeholders})", _to_row(record)
        )

        # Attachment ids are allocated from the attachments table, not from the inline
        # array, so they stay unique against the dataset's own ATT-#### series.
        for attachment in attachments:
            db.execute(
                "INSERT INTO attachments "
                "(parent_message_id, id, filename, mime_type, size, extracted_text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    attachment["id"],
                    attachment["filename"],
                    attachment["mime_type"],
                    attachment["size"],
                    attachment["extracted_text"],
                ),
            )

        # Same three fields the loader indexes, so a draft is searchable exactly as a
        # dataset message is. A plain FTS5 table takes ordinary INSERTs - there is no
        # external-content table to keep in sync and no triggers involved.
        db.execute(
            "INSERT INTO search_index (message_id, subject, body, attachment_text) "
            "VALUES (?, ?, ?, ?)",
            (
                message_id,
                record["subject"],
                record["body"]["content"],
                " ".join(a["extracted_text"] for a in attachments),
            ),
        )
    return message_id


def allocate_attachment_ids(count: int) -> list[str]:
    """
    Reserve `count` attachment ids ahead of an insert_draft() call.

    Separate from insert_draft because the caller has to put the same ids in two places:
    the attachments rows and the inline messages.attachments array, which the schema
    keeps as duplicated metadata and load assertion 3 checks agree. There is a race in
    principle - two concurrent create_draft calls could reserve overlapping ids, since
    the reservation and the insert are separate transactions - but the ids are only
    unique per parent message (the attachments primary key is composite), so two drafts
    both using ATT-0009 is not a collision.
    """
    with _DB_LOCK:
        start = _next_number("attachments", "id", "ATT-")
    return [f"ATT-{start + offset:0{_ID_DIGITS}d}" for offset in range(count)]


def next_thread_id() -> str:
    """
    Allocate a thread id for a draft that is not a reply.

    Continues the dataset's THR-#### series rather than starting a new prefix: a draft
    with no parent still needs a thread, and Gmail gives every draft one.
    """
    with _DB_LOCK:
        return _next_id("messages", "thread_id", "THR-")


def insert_label(name: str, color: dict | None) -> str:
    """
    Create a user label, returning its allocated id.

    Ids continue the dataset's Label_N series, which is not zero-padded - hence digits=0
    rather than the _ID_DIGITS default that every other id uses.

    Raises sqlite3.IntegrityError if the name is taken: labels.name is UNIQUE, which is
    the schema enforcing what Gmail enforces. The tool layer turns that into a clean
    error rather than letting it surface as a traceback.
    """
    with _transaction() as db:
        label_id = _next_id("labels", "id", "Label_", digits=0)
        db.execute(
            "INSERT INTO labels (id, name, type, color) VALUES (?, ?, 'user', ?)",
            (label_id, name, json.dumps(color) if color else None),
        )
    return label_id


def label_exists(name: str) -> bool:
    """Whether a label with this exact display name already exists."""
    return scalar("SELECT 1 FROM labels WHERE name = ? LIMIT 1", (name,)) is not None
