# Gmail Server (Gmail MCP Replica)

A mock of Google's official Gmail MCP server (`gmailmcp.googleapis.com/mcp/v1`) that serves a synthetic mailbox for benchmarking. All 13 tools - 5 read, 8 write - with the same request parameters, the same response envelopes, and the same Gmail query syntax in the `query` string, without needing a Google account or OAuth.

Unlike its siblings in this folder there is **no REST interface**: nothing in the agent path calls a REST port for mail, so the server is MCP-only.

## Quick start

### Option 1: Run locally (no Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Point at your data directory and start
DATA_DIR=/path/to/integrations/data python3 mcp_server.py
```

The server starts on `http://localhost:8014/mcp` (Streamable HTTP transport).

### Option 2: Docker

```bash
# From the mcp-servers-2 root
docker compose up mail-mcp-http
```

Published on `http://localhost:8015/mcp` (container port 8014).

## Authentication

None. Like the other MCP HTTP endpoints in this folder, the mail server takes no bearer token.

## MCP tools

### Read

| Tool | Description |
|------|-------------|
| `search_threads` | Find threads matching a Gmail query string; returns threads with per-message snippets, never full bodies |
| `get_thread` | Read one thread by ID, all messages in chronological order |
| `get_message` | Read one message by ID |
| `list_labels` | List every label with message and thread counts - call this to discover label IDs and display names |
| `list_drafts` | List drafts, filtered by the same query syntax. Drafts do not appear in `search_threads` |

### Write

| Tool | Description | Returns |
|------|-------------|---------|
| `create_draft` | Compose a draft; pass `reply_to_message_id` to thread it under an existing message | the created `Draft` |
| `label_thread` | Add labels to every message in a thread | `{}` |
| `unlabel_thread` | Remove labels from every message in a thread | `{}` |
| `label_message` | Add labels to one message | `{}` |
| `unlabel_message` | Remove labels from one message | `{}` |
| `apply_sensitive_thread_label` | Move a thread to Trash or mark it Spam | `{}` |
| `apply_sensitive_message_label` | Move a message to Trash or mark it Spam | `{}` |
| `create_label` | Create a user label; `/` nests it and missing parents are created | the created `Label` |

Two asymmetries carried over from the real toolset rather than smoothed out:

- **`label_*` rejects `TRASH` and `SPAM`; `unlabel_*` accepts them.** Adding `TRASH` is a move - it also has to remove `INBOX`, or the thread would answer both `in:inbox` and `in:trash` - so it gets its own tool. Taking `TRASH` off needs no companion change.
- **`list_drafts` is `readOnlyHint: true` but `idempotentHint: false`**, the only read tool with that combination.

`create_draft` and `create_label` are the two non-idempotent writes: calling either twice creates two records. The six label tools are idempotent - applying a label a message already has changes nothing.

### Writes do not touch the dataset

`/data` is mounted read-only, and the write tools still work. The store is an in-process SQLite database rebuilt from the JSON at every startup, so a mutation lands there and nowhere else:

- Effects last for the life of the container and are gone after a restart, which is what keeps benchmark trials isolated - every run starts from the same authored mailbox, with no reset step.
- **Verify a write through a read tool**, not by reading `$DATA_DIR`. Trash a thread and `search_threads` stops returning it; the JSON on disk is identical either way.

### `search_threads`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `query` | string | `""` | Gmail query syntax (see below). Empty returns every thread except spam, trash and drafts |
| `page_size` | int | `20` | Clamped to 1-50. Counts **threads**, not messages |
| `page_token` | string | `""` | Cursor from a previous response's `nextPageToken` |
| `view` | enum | `THREAD_VIEW_MINIMAL` | Or `THREAD_VIEW_METADATA_ONLY`, which drops `subject` and `snippet` |
| `include_trash` | bool | `false` | Whether `TRASH`-labeled messages are eligible at all |

```json
{
  "threads": [
    {
      "id": "THR-0101",
      "messages": [
        {
          "id": "EMAIL-0001",
          "sender": "amara.nwosu@veranotravel.com",
          "toRecipients": ["ellie.ashworth@maplesoftware.net"],
          "ccRecipients": [],
          "bccRecipients": [],
          "date": "2026-06-02T08:41:00Z",
          "labelIds": ["INBOX", "Label_1", "Label_5", "IMPORTANT", "STARRED"],
          "subject": "Settlement reconciliation fix + board review",
          "snippet": "Ellie, Our finance team can't close May. The multi-currency..."
        }
      ]
    }
  ],
  "resultCountEstimate": "5",
  "nextPageToken": "1"
}
```

**Address operators are substrings, over the name as well as the address.** `from:amara`, `from:"Amara Nwosu"` and `from:amara.nwosu@veranotravel.com` all find the same person, in any case, exactly as Gmail's own `from:` does — its spec defines the argument as "a specific person", not an address. Two things follow: the match is deliberately fuzzy (`from:uk` hits six people in this dataset, `from:a` hits seventeen), and `from:` also searches the `sender` column, so the address a search result advertises is always one you can query back.

**Matching is per message, but results are whole threads.** A thread comes back if *any* one of its messages matches, and then every message in it is returned. So `-is:starred` can hand you a thread that contains starred messages, and `after:`/`before:` can hand you messages outside the window. Filter at the message level yourself if you need to. (The real tool's own description carries the same warning.)

### `get_thread` / `get_message`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `thread_id` / `message_id` | string | required | e.g. `THR-0101`, `EMAIL-0004` |
| `message_format` | enum | `FULL_CONTENT` | Or `MINIMAL` (subject + snippet) or `METADATA_ONLY` |

`get_thread` returns a bare `Thread` - no `{"thread": ...}` wrapper, no pagination fields. `get_message` returns a bare `Message`. `FULL_CONTENT` adds `body` and `attachments`; `METADATA_ONLY` returns only IDs, addresses, date and labels.

`get_thread` takes **no filters**: every message in the thread comes back regardless of date, sender or content.

### `list_labels`

Takes `page_size` (0 = all, the default) and `page_token`. Returns 8 user labels and 8 system labels:

```json
{
  "labels": [
    {
      "labelId": "Label_1",
      "name": "Verano",
      "color": {"textColor": "#ffffff", "backgroundColor": "#1a73e8"},
      "messagesTotal": 15,
      "messagesUnread": 0,
      "threadsTotal": 4,
      "threadsUnread": 0
    }
  ]
}
```

### `list_drafts`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `query` | string | `""` | Same Gmail query syntax as `search_threads` |
| `page_size` | int | `20` | Clamped to 1-50 |
| `page_token` | string | `""` | Cursor from a previous response |
| `view` | enum | `DRAFT_VIEW_FULL` | Or `DRAFT_VIEW_METADATA_ONLY`, which drops `subject` and the body |

Returns `{"drafts": [...]}`. A `Draft` carries `threadId` - the field `Message` does not have - so a reply draft can be traced back to the conversation it belongs to.

### `create_draft`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `to` / `cc` / `bcc` | string[] | `[]` | Plain addresses only. `Name <email>` is **not** supported |
| `subject` | string | `""` | Inherited from the parent on a reply |
| `body` | string | `""` | Plain text. Wins over `html_body` when both are given |
| `html_body` | string | `""` | Used when there is no plain-text body |
| `reply_to_message_id` | string | `""` | Threads the draft under that message and appends to its body |
| `attachments` | object[] | `[]` | `{content (base64), filename, mimeType}`; 25MB combined |

No field is required - an empty draft is valid. Returns the whole `Draft`, including the assigned `id`, despite the official description saying it returns only the ID: its `outputSchema` is a full `Draft`.

Attachments are stored as metadata (filename, MIME type, decoded size) and become searchable by `filename:`. The bytes are weighed and dropped - there is no column for content, and no tool in this toolset downloads one.

```json
{
  "id": "DRAFT-0001",
  "threadId": "THR-0101",
  "subject": "Re: Settlement reconciliation fix + board review",
  "toRecipients": ["amara.nwosu@veranotravel.com"],
  "ccRecipients": [],
  "bccRecipients": [],
  "date": "2026-07-20T07:00:00Z",
  "plaintextBody": "..."
}
```

### `label_thread` / `unlabel_thread` / `label_message` / `unlabel_message`

| Parameter | Type | Notes |
|-----------|------|-------|
| `thread_id` / `message_id` | string | required |
| `label_ids` | string[] | required, non-empty. Label IDs; display names are accepted too |

Returns `{}`. One bad ID in the list applies **none** of them - everything is validated before the first write.

`UNREAD`, `IMPORTANT` and `STARRED` work here even though they are computed rather than stored: labeling with one updates the state it is derived from, so `is:unread` and `list_labels` stay in agreement. Removing `IMPORTANT` is lossy, because Gmail has two importance states and this dataset has three.

### `apply_sensitive_thread_label` / `apply_sensitive_message_label`

| Parameter | Type | Notes |
|-----------|------|-------|
| `thread_id` / `message_id` | string | required |
| `label_option` | enum | required: `TRASH` or `SPAM`. No default - the tool exists to say which |

Returns `{}`. Adds the label **and removes `INBOX`**, because this is a move. The target then drops out of the default search but stays reachable via `in:trash` / `in:spam`, or `include_trash`. Not reversible through `unlabel_*` alone - the same as real Gmail, where restoring from Trash also puts the message back in the inbox.

### `create_label`

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `display_name` | string | required | `/` nests: `Renewals/Q4` is a sub-label of `Renewals` |
| `color` | object | none | `{"textColor": "#ffffff", "backgroundColor": "#cc3a21"}` |
| `auto_create_parent_labels` | bool | `true` | When false, a missing parent is an error instead |

Returns the created `Label` with its assigned `labelId` - the only write tool with a populated body, because the caller needs that ID before it can label anything. A duplicate name is an error: label names have to stay unique or `label:` becomes ambiguous. Auto-created parents get no color; the one you passed belongs to the label you asked for.

### MCP client config

Over Streamable HTTP (how the benchmark agents connect):

```json
{
  "mcpServers": {
    "mail": {
      "type": "http",
      "url": "http://bench-mail-mcp:8014/mcp"
    }
  }
}
```

## Query syntax

Everything goes in the one `query` string, exactly as with real Gmail. Clauses are separated by spaces and combined with **AND**. Prefix any operator with `-` to negate it.

| Operator | Example | Meaning |
|----------|---------|---------|
| `from:` | `from:amara`, `from:amara.nwosu@veranotravel.com` | Sender address **or** display name, case-insensitive substring. Also matches `sender` |
| `to:` `cc:` `bcc:` | `cc:rohan`, `cc:rohan.mehta@maplesoftware.net` | Recipient lists, same address-or-name substring |
| `deliveredto:` | `deliveredto:ellie.ashworth@maplesoftware.net` | Treated as `to:` - the dataset has no delivery headers |
| `subject:` | `subject:"board review"` | Subject substring; quote to keep a phrase together |
| `label:` | `label:Verano`, `label:Label_1` | Accepts the display name **or** the label ID |
| `is:` | `is:unread` `is:read` `is:starred` `is:important` | Message state |
| `has:` | `has:attachment` `has:userlabels` `has:nouserlabels` | |
| `filename:` | `filename:pdf` | Attachment filename substring |
| `rfc822msgid:` | `rfc822msgid:<abc@mail>` | Exact Message-ID |
| `in:` | `in:inbox` `in:sent` `in:spam` `in:trash` `in:archive` `in:anywhere` | Folder |
| `after:` `before:` | `after:2026/07/01 before:2026/07/16` | `YYYY/MM/DD`. Half-open: `before:` **excludes** the named day |
| `newer_than:` `older_than:` | `newer_than:30d` `older_than:1y` | Relative duration (`d`, `m`, `y`) |
| `"exact phrase"` | `"rounding drift"` | Phrase search over subject, body and attachment text |
| bare word | `settlement` | Full-text search over subject, body and attachment text |

```
from:amara.nwosu@veranotravel.com has:attachment newer_than:90d
label:Escalations is:unread -in:sent
subject:renewal after:2026/07/01 before:2026/07/16
```

**Two things to know about dates.** Bare dates are calendar days in `Europe/London`, not UTC instants - during BST, `2026/07/01` begins at `2026-06-30T23:00:00Z`. And `newer_than:`/`older_than:` measure from the newest message in the dataset (`2026-07-20T07:00:00Z`), not the wall clock, so the same query returns the same threads on every run. Override with `MAIL_NOW`.

### Not supported

`OR`, `{}`, `()`, `AROUND`, `size:`/`larger:`/`smaller:`, `category:`, `list:`, `is:muted`, `has:drive`/`has:youtube`/`has:document`.

These are **reported, not silently dropped** - the response grows an `unsupportedOperators` map naming each one and why, and the rest of the query still applies as AND. A "no results" answer is therefore never ambiguous about whether a filter was ignored.

```json
{
  "threads": [ ... ],
  "resultCountEstimate": "3",
  "unsupportedOperators": {
    "larger": "messages carry no byte size",
    "OR": "only AND is supported; clauses are combined with AND"
  }
}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `/data` | Path to the data root (must contain an `email_json_data/` subdir) |
| `MCP_PORT` | `8014` | MCP HTTP listen port |
| `MAIL_NOW` | newest message date | RFC 3339 instant that `newer_than:`/`older_than:` measure from |

## Data format

Unlike its siblings, this server **does** transform its data at startup: the three JSON files are loaded into an in-process, in-memory SQLite database with an FTS5 index over subject, body and attachment text. Nothing is written back to disk, and the database is rebuilt from the JSON on every start.

```
$DATA_DIR/
└── email_json_data/
    ├── messages.json        (50 messages across 19 threads)
    ├── labels.json          (8 user labels; system labels are synthesized)
    └── attachments.json     (8 attachments, with extracted text)
```

The `messages` table is 1:1 with the source record - one column per field, same names, same order, arrays kept as JSON arrays. A startup assertion re-reads every row and fails the load if it does not reconstruct the source JSON exactly, so a schema drift is a startup error rather than a wrong answer at query time. Source-of-truth field definitions live in `docs/ontology/ontology.md` §A.5.1 (`Email`), which a pre-commit validator enforces.

## Things that look wrong and are not

- **`resultCountEstimate` is a JSON string** (`"19"`, not `19`) - proto3 serializes int64 that way.
- **`labelIds` holds IDs, not display names.** A message labeled `Verano` reports `Label_1`.
- **Label objects use `labelId`, not `id`.**
- **`color` is absent on system labels**, not present-and-null.
- **`sender` is not always the `From:` address.** One message in the dataset was transmitted by an ops address on someone else's behalf; `sender` says who actually sent it. `from:` searches both columns, so either address finds it.
- **`Message` has no `threadId`, but `Draft` does.** The official schema is built that way; `Draft` is a separate wire type.
- **`UNREAD`, `IMPORTANT` and `STARRED` are real labels here.** They are computed from message state rather than stored, but `list_labels` counts them, `label:IMPORTANT` works, and the label tools can add and remove them.
- **The write tools return `{}`**, not a status or the modified record. Only `create_draft` and `create_label` return anything.
- **`create_draft` returns a whole `Draft`** even though its description says "returns only the unique ID", and it accepts attachments even though the description calls them "not supported yet". Both times the `inputSchema`/`outputSchema` disagrees with the prose, and the schema wins.
- **A reply draft's body starts with the parent's body.** `create_draft` documents `body` as being *appended to the original message body*, which is what a reply quoting its parent looks like.
- **Search results are ordered by the thread's newest message, not its newest match.** A filter can exclude a conversation's latest reply without moving where it ranks, so a thread whose only match is old still sorts first if it is still active. That is what a mail client shows.
- **`create_draft` accepts an attachment's `inline` flag and ignores it.** The field is in the official input schema; there is nowhere to store it (the record has no such field) and no read tool that would report it, so an inline attachment comes back as an ordinary one. Same class of approximation as `deliveredto:` being treated as `to:`.
- **A write survives until the container restarts, and never reaches the JSON.** The store is `:memory:`; see "Writes do not touch the dataset" above.

## Troubleshooting

**Server says "0 messages loaded"**
- `DATA_DIR` must contain an `email_json_data/` subdirectory. Check that `$DATA_DIR/email_json_data/messages.json` exists.

**`ZoneInfoNotFoundError: Europe/London`**
- `python:3.12-slim` ships no system timezone database. `tzdata` is in `requirements.txt` for exactly this reason - reinstall dependencies.

**`no such module: fts5`**
- The SQLite build lacks FTS5. Rare on Debian-based images; check with `sqlite3.connect(":memory:").execute("PRAGMA compile_options")`.

**A query returns nothing and you expected results**
- Check `unsupportedOperators` in the response first - an `OR` or a `larger:` in the query is reported there.
- `label:` needs an exact label name or ID; call `list_labels` to see them. A bad label name is an error, not an empty result. Unlike `from:`/`to:`, it is **not** a substring match - `label:Board` does not find `Board Prep`.
- `before:2026/07/16` excludes the 16th. Use `before:2026/07/17` to include it.

**Relative dates return everything or nothing**
- `MAIL_NOW` is set to a wall-clock instant outside the dataset's range. Unset it.

**Server exits immediately / nothing on the port**
- This server speaks Streamable HTTP, not stdio. It listens on `$MCP_PORT`; there is no REST port and no `/health` endpoint to curl.
