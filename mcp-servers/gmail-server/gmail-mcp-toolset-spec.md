# Gmail MCP Server — Toolset & Wire Contract

Companion to [gmail-mcp-server.md](gmail-mcp-server.md) (architecture: process model, transport,
SQLite-in-process, directory layout, registration). **This** doc is the wire contract: for every
tool — its description, the exact request the LLM will emit, and the exact response we must return.

Source: untruncated `tools/list` captures against `gmailmcp.googleapis.com/mcp/v1`. **13 tools — 5
read, 8 write.** The raw captures are not in the repo; every tool description reproduced below is
quoted from them verbatim, and `mcp_server.py` copies those descriptions from here.

Examples use real records from `integrations/data/email_json_data/`. Field names on the storage side
are the ones in the ontology's Communication domain,
[ontology.md](../../../docs/ontology/ontology.md) §A.5.1 `Email`.

---

## 1. Shared response types

The real server reuses these across tools via `$ref`. **One builder function per type, reused
everywhere** — never shape a response inline in a tool body.

### `Message` — "Message within a thread"
Used by `get_message` (whole response body), `get_thread` + `search_threads` (inside `Thread.messages`).

| Field | Type | Populated when |
|---|---|---|
| `id` | string | always |
| `sender` | string | always — **bare email string, not an object** |
| `toRecipients` | string[] | always |
| `ccRecipients` | string[] | always |
| `bccRecipients` | string[] | always |
| `date` | string | always |
| `labelIds` | string[] | always — user label IDs + system labels, limited to `INBOX`, `SPAM`, `TRASH`, `UNREAD`, `STARRED`, `IMPORTANT`, `SENT`, `DRAFT`, `CHAT` |
| `subject` | string | `MINIMAL`, `FULL_CONTENT` |
| `snippet` | string | `MINIMAL`, `FULL_CONTENT` |
| `plaintextBody` | string | `FULL_CONTENT` only |
| `htmlBody` | string | `FULL_CONTENT` only |
| `attachmentIds` | string[] | `FULL_CONTENT` only (`readOnly`) |
| `attachments` | `AttachmentMetadata[]` | `FULL_CONTENT` only (`readOnly`) |

**No `threadId` on `Message`** — even though `get_message`'s prose claims `METADATA_ONLY` returns
"thread ID … and size estimate". The schema is authoritative.

### `Thread` — "Thread containing a list of messages"
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `messages` | `Message[]` | **"ordered chronologically"** — sort ascending by date |

No subject, participant list, or message count at thread level. Callers derive those from `messages[]`.

### `AttachmentMetadata`
| Field | Type |
|---|---|
| `id` | string (`readOnly`) |
| `filename` | string |
| `mimeType` | string |

### `Label`
| Field | Type | Notes |
|---|---|---|
| `labelId` | string | **`labelId`, not `id`** |
| `name` | string | Display name |
| `color` | `LabelColor` | Optional |
| `messagesTotal` | int32 | |
| `messagesUnread` | int32 | |
| `threadsTotal` | int32 | |
| `threadsUnread` | int32 | |

### `LabelColor`
`backgroundColor` (string), `textColor` (string) — both constrained by the real API to a fixed
palette of ~100 predefined hex values, not free-form hex.

### `Draft`
| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `threadId` | string | **`Draft` has `threadId`; `Message` does not** |
| `subject` | string | omitted under `DRAFT_VIEW_METADATA_ONLY` |
| `toRecipients` / `ccRecipients` / `bccRecipients` | string[] | |
| `date` | string | |
| `plaintextBody` | string | omitted under `DRAFT_VIEW_METADATA_ONLY` |
| `htmlBody` | string | omitted under `DRAFT_VIEW_METADATA_ONLY` |

### `Attachment` (input-only, `create_draft` only)
| Field | Type | Required | Notes |
|---|---|---|---|
| `content` | string (`format: byte`) | **yes** | base64 |
| `filename` | string | no | for inline attachments, drives Content-ID generation |
| `mimeType` | string | no | IANA MIME type; defaults `application/octet-stream` |
| `inline` | boolean | no | default false — true renders inside HTML body vs. listed as download |
| `id` | string | no | `readOnly` — ID of an external attachment retrievable via a separate `GetMessageAttachment` request (**not an exposed MCP tool**) |

---

## 2. Enums

Every enum has an `_UNSPECIFIED` zero value aliasing to the default. Accept it, don't error.

**`MessageFormat`** — `get_thread`, `get_message`. Strictly nested: `METADATA_ONLY ⊂ MINIMAL ⊂ FULL_CONTENT`.
| Value | Returns |
|---|---|
| `MESSAGE_FORMAT_UNSPECIFIED` | → `FULL_CONTENT` |
| `METADATA_ONLY` | `id`, `sender`, `to/cc/bccRecipients`, `date`, `labelIds` |
| `MINIMAL` | above **+** `subject`, `snippet` |
| `FULL_CONTENT` | above **+** `plaintextBody`, `htmlBody`, `attachmentIds`, `attachments` (**default**) |

**`ThreadView`** — `search_threads` only.
| Value | Returns per nested message |
|---|---|
| `THREAD_VIEW_UNSPECIFIED` | → `THREAD_VIEW_MINIMAL` |
| `THREAD_VIEW_METADATA_ONLY` | `id`, `from`, `to`, `cc`, `bcc`, `date`, `labelIds` |
| `THREAD_VIEW_MINIMAL` | above **+** `snippet`, `subject` (**default**) |

`search_threads` has **no `messageFormat` and no `FULL_CONTENT` equivalent** — so it never returns
`plaintextBody`, `htmlBody`, `attachmentIds`, or `attachments`. Its description says so outright:
"use the 'get_thread' tool with a thread ID to fetch the full message body if needed." That's the
deliberate search→retrieve two-step.

**`DraftView`** — `list_drafts`.
`DRAFT_VIEW_UNSPECIFIED` → `DRAFT_VIEW_FULL` | `DRAFT_VIEW_METADATA_ONLY` (excludes `subject`,
`plaintextBody`, `htmlBody`) | `DRAFT_VIEW_FULL` (**default**)

**`LabelOption`** — `apply_sensitive_*_label`.
`LABEL_OPTION_UNSPECIFIED` | `TRASH` | `SPAM`

---

## 3. READ tools — implement these

### 3.1 `search_threads`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`

**Description** (this is prompt text the model reads — copy close to verbatim):
- Lists email threads from the authenticated user's Gmail account.
- Filters threads based on a query string; supports pagination.
- Returns thread IDs plus related messages — each carrying a snippet, subject, sender, recipients, etc.
- `view` controls which fields are populated on the related messages: default `THREAD_VIEW_MINIMAL`
  includes subject and snippet; `THREAD_VIEW_METADATA_ONLY` excludes them.
- **Full message bodies are not returned by this tool** — use `get_thread` with a thread ID for those.
- **"Threads with excluded criteria may still appear in the results. This occurs because Gmail
  identifies matching messages first. For example, if you search for `-is:starred`, Gmail will find
  an entire thread if it contains at least one unstarred message, even if other emails in that same
  conversation are starred."**
- Natural-language queries must be pre-converted into Gmail syntax before calling.

That last-but-one bullet defines the query semantics — see §3.1.1.

**Request:**
| Field | Type | Required | Default | Role |
|---|---|---|---|---|
| `query` | string | no | `""` = all threads (excl. spam/trash) | **The entire filter surface** — Gmail operator syntax, §5 |
| `pageSize` | int32 | no | 20 | max **50** |
| `pageToken` | string | no | `""` | opaque cursor from a prior call |
| `view` | `ThreadView` | no | `THREAD_VIEW_MINIMAL` | formatting, not filtering |
| `includeTrash` | boolean | no | false | whether `TRASH`-labeled threads are eligible at all |

```json
{
  "name": "search_threads",
  "arguments": {
    "query": "from:amara.nwosu@veranotravel.com newer_than:90d",
    "pageSize": 10,
    "view": "THREAD_VIEW_MINIMAL"
  }
}
```

**Response:** `{ threads: Thread[], nextPageToken?: string, resultCountEstimate: string }`
- `nextPageToken` — present **only** if there are more results.
- `resultCountEstimate` — declared `int64`, **serialized as a JSON string** (proto3 convention).
  Emit `"19"`, not `19`. "Treated as a lower bound, so … if it is 500, the count can be reported
  to the user as `500+`."

The query above matches `EMAIL-0001` and `EMAIL-0005` only — yet the **entire 6-message thread**
comes back, including the four messages Ellie and Charles sent that don't match `from:amara…` at
all. Abridged to 2 of 6:

```json
{
  "threads": [
    {
      "id": "THR-0101",
      "messages": [
        {
          "id": "EMAIL-0001",
          "subject": "Settlement reconciliation fix + board review",
          "snippet": "Ellie, Our finance team can't close May. The multi-currency settlement batches are reconciling with a rounding drift on the FX leg -- small per transaction, but across ~180k payouts it comes to about GBP 4,100 unexplained for the month. We can't sign off ",
          "sender": "amara.nwosu@veranotravel.com",
          "toRecipients": ["ellie.ashworth@maplesoftware.net"],
          "ccRecipients": [],
          "bccRecipients": [],
          "date": "2026-06-02T08:41:00Z",
          "labelIds": ["INBOX", "Label_1", "Label_5", "IMPORTANT", "STARRED"]
        },
        {
          "id": "EMAIL-0002",
          "subject": "Re: Settlement reconciliation fix + board review",
          "snippet": "Amara, ...",
          "sender": "ellie.ashworth@maplesoftware.net",
          "toRecipients": ["amara.nwosu@veranotravel.com"],
          "ccRecipients": [],
          "bccRecipients": [],
          "date": "2026-06-03T07:15:00Z",
          "labelIds": ["SENT", "Label_1"]
        }
      ]
    }
  ],
  "resultCountEstimate": "1"
}
```

No `plaintextBody`/`htmlBody`/`attachments` anywhere — this tool can't emit them.

#### 3.1.1 Query semantics — filters match *messages*, results are whole *threads*

1. **Match messages.** Apply the parsed query to `messages`. Collect `DISTINCT thread_id`.
2. **Assemble threads.** For each matched `thread_id`, re-query **all** its messages *unfiltered*,
   `ORDER BY date ASC`. Shape each per `view`.
3. **Paginate at thread level** — `pageSize` caps threads, not messages.

```sql
-- 1: threads with at least one matching message
SELECT DISTINCT m.thread_id
FROM messages m
LEFT JOIN message_recipients r ON r.message_id = m.id
WHERE <parsed query predicates>
ORDER BY (SELECT MAX(date) FROM messages WHERE thread_id = m.thread_id) DESC
LIMIT :page_size OFFSET :offset;

-- 2: per thread_id from step 1, everything in it
SELECT * FROM messages WHERE thread_id = ? ORDER BY date ASC;
```

Defaults the implementation must honour, per the description: spam and trash excluded unless
`includeTrash` or `in:anywhere`/`in:trash`; **drafts explicitly excluded by default**; archived and
sent messages **included** by default (exclude via `-in:archive` / `-in:sent`).

---

### 3.2 `get_thread`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`

**Description:**
- Retrieves a specific email thread by ID, including a list of its messages.
- `messageFormat` controls the format of the messages returned; defaults to `FULL_CONTENT`.
- `MINIMAL` → only subject and snippet (excluding body). `METADATA_ONLY` → only basic metadata.

**Request:**
| Field | Type | Required | Default |
|---|---|---|---|
| `threadId` | string | **yes** | — |
| `messageFormat` | `MessageFormat` | no | `FULL_CONTENT` |

**No filters** — pure ID lookup. Every message in the thread comes back regardless of date, sender,
or content; all narrowing happens upstream in `search_threads`.

```json
{ "name": "get_thread", "arguments": { "threadId": "THR-0101", "messageFormat": "FULL_CONTENT" } }
```

**Response:** a bare `Thread` — no `{"thread": …}` wrapper, no pagination fields. A 7-message thread
returns all 7. Showing the one message in `THR-0101` that has an attachment:

```json
{
  "id": "THR-0101",
  "messages": [
    {
      "id": "EMAIL-0004",
      "subject": "Re: Settlement reconciliation fix + board review",
      "snippet": "Charles, Amara, That sequencing is fair and it's the one we'd argue for too. Engineering has reproduced it and confirmed root cause: the reconciliation event stream applies FX conversion per-batch rather than per-transaction, so sub-unit remainders are tr",
      "sender": "ellie.ashworth@maplesoftware.net",
      "toRecipients": ["charles.pemberton@veranotravel.com", "amara.nwosu@veranotravel.com"],
      "ccRecipients": ["rohan.mehta@maplesoftware.net"],
      "bccRecipients": [],
      "date": "2026-06-08T10:08:00Z",
      "labelIds": ["SENT", "Label_1", "Label_5"],
      "plaintextBody": "Charles, Amara,\n\nThat sequencing is fair and it's the one we'd argue for too.\n\nEngineering has reproduced it and confirmed root cause: the reconciliation event stream applies FX conversion per-batch rather than per-transaction, so sub-unit remainders are truncated instead of carried. It is a real defect on our side, not a configuration issue at yours. It is now tracked as ISS-201.\n\n...",
      "attachmentIds": ["ATT-0001"],
      "attachments": [
        {
          "id": "ATT-0001",
          "filename": "verano-fx-rounding-root-cause.pdf",
          "mimeType": "application/pdf"
        }
      ]
    }
  ]
}
```

`htmlBody` is absent here because this message's `body.content_type` is `"text"`; for `EMAIL-0048`
(the one HTML message in the dataset) it's the reverse.

---

### 3.3 `get_message`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`

**Description** — note how much of it is disambiguation from `get_thread`:
- Retrieves a specific email message by its unique message ID.
- Use when you already know the message ID and want to inspect a single individual email — read it
  in detail, check exact wording, or examine attachment metadata for one email.
- **Not suitable for retrieving entire conversations or back-and-forth threads — use `get_thread`.**
- Key indicators: the user asks for the full content of a specific message ID returned by a previous
  search, or asks to inspect one individual email rather than a whole thread.
- Example prompts: *"Get the full text of message ID 18f123456789abcd."* · *"Read the latest message
  in that thread from Alice."* · *"What are the attachment names in the email I just received from HR?"*
- `messageFormat` controls the returned format; defaults to `FULL_CONTENT`.

**Request:**
| Field | Type | Required | Default |
|---|---|---|---|
| `messageId` | string | **yes** | — |
| `messageFormat` | `MessageFormat` | no | `FULL_CONTENT` |

**No filters** — pure ID lookup.

```json
{ "name": "get_message", "arguments": { "messageId": "EMAIL-0004", "messageFormat": "MINIMAL" } }
```

**Response:** a bare `Message` at the top level — not wrapped, not in an array. Same builder as §1.

```json
{
  "id": "EMAIL-0004",
  "subject": "Re: Settlement reconciliation fix + board review",
  "snippet": "Charles, Amara, That sequencing is fair and it's the one we'd argue for too. Engineering has reproduced it and confirmed root cause: the reconciliation event stream applies FX conversion per-batch rather than per-transaction, so sub-unit remainders are tr",
  "sender": "ellie.ashworth@maplesoftware.net",
  "toRecipients": ["charles.pemberton@veranotravel.com", "amara.nwosu@veranotravel.com"],
  "ccRecipients": ["rohan.mehta@maplesoftware.net"],
  "bccRecipients": [],
  "date": "2026-06-08T10:08:00Z",
  "labelIds": ["SENT", "Label_1", "Label_5"]
}
```

(`MINIMAL`, so no `plaintextBody`/`htmlBody`/`attachmentIds`/`attachments`.)

---

### 3.4 `list_labels`

`readOnlyHint: true` · `idempotentHint: true` · `destructiveHint: false` · `openWorldHint: false`

**Description:**
- Lists all labels available in the authenticated user's Gmail account.
- Use this to **discover a label's `id` before calling** `label_thread`, `unlabel_thread`,
  `label_message`, or `unlabel_message`.
- The system labels `DRAFT` and `SENT` cannot be set on messages and are read-only.

**Request:** `pageSize` (int32 — **no stated default or max**, unlike the other paginated tools),
`pageToken` (string). **No `query` parameter — no filtering at all.**

```json
{ "name": "list_labels", "arguments": {} }
```

**Response:** `{ labels: Label[], nextPageToken?: string }`

```json
{
  "labels": [
    {
      "labelId": "Label_1",
      "name": "Verano",
      "color": { "textColor": "#ffffff", "backgroundColor": "#1a73e8" },
      "messagesTotal": 15,
      "messagesUnread": 0,
      "threadsTotal": 4,
      "threadsUnread": 0
    },
    {
      "labelId": "Label_5",
      "name": "Escalations",
      "color": { "textColor": "#ffffff", "backgroundColor": "#cc3a21" },
      "messagesTotal": 6,
      "messagesUnread": 0,
      "threadsTotal": 3,
      "threadsUnread": 0
    }
  ]
}
```

The four counts are computed, not stored:

```sql
SELECT l.id, l.name, l.text_color, l.background_color,
       COUNT(ml.message_id)                                         AS messages_total,
       SUM(CASE WHEN m.is_read = 0 THEN 1 ELSE 0 END)               AS messages_unread,
       COUNT(DISTINCT m.thread_id)                                  AS threads_total,
       COUNT(DISTINCT CASE WHEN m.is_read = 0 THEN m.thread_id END) AS threads_unread
FROM labels l
LEFT JOIN message_labels ml ON ml.label_id = l.id
LEFT JOIN messages m       ON m.id = ml.message_id
GROUP BY l.id ORDER BY l.id;
```

---

### 3.5 `list_drafts`

`readOnlyHint: true` · **`idempotentHint: false`** (read-only yet declared non-idempotent, unlike
the other read tools) · `destructiveHint: false` · `openWorldHint: false`

**Description:**
- Lists draft emails from the account; filters on a query string and supports pagination.
- Returns drafts including their IDs and subjects, unless `view` is `DRAFT_VIEW_METADATA_ONLY`.
- `pageToken` from a previous response fetches the next page.
- `view` controls populated fields: default `DRAFT_VIEW_FULL` returns full content;
  `DRAFT_VIEW_METADATA_ONLY` excludes sensitive content like subject and body.

**Request:**
| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `query` | string | no | `""` | same Gmail operator family — its own description cites `subject:`, `from:`, `to: AND newer_than:7d`, `has:attachment`, `is:unread` |
| `pageSize` | int32 | no | 20 | max **50** |
| `pageToken` | string | no | `""` | |
| `view` | `DraftView` | no | `DRAFT_VIEW_FULL` | |

Its `query` description carries one tokenizing detail found nowhere else in the capture: *"A space
or a dash (`-`) will separate a number while a dot (`.`) will be a decimal. For example,
`01.2047-100` is considered two numbers: `01.2047` and `100`."*

```json
{ "name": "list_drafts", "arguments": { "query": "subject:forecast", "view": "DRAFT_VIEW_FULL" } }
```

**Response:** `{ drafts: Draft[], nextPageToken?: string }`

```json
{
  "drafts": [
    {
      "id": "EMAIL-0044",
      "threadId": "THR-0142",
      "subject": "Q3 forecast -- UK & Ireland",
      "toRecipients": ["priya.deshpande@maplesoftware.net"],
      "ccRecipients": [],
      "bccRecipients": [],
      "date": "2026-07-17T18:40:00Z",
      "plaintextBody": "Priya -- draft forecast notes, not final.\n\nMoving Verano from 80% to 50% pending the board outcome on the 24th. ..."
    }
  ]
}
```

---

## 4. WRITE tools

Consistent with every other mock in this repo (`:ro` bind mount, no CRUD — `file-server`'s
`create_file` returns a "not supported" stub rather than faking a write). Documented in full so the
real surface is visible rather than silently dropped.

### 4.1 `create_draft`

`readOnlyHint: false` · `destructiveHint: false` · **`idempotentHint: false`** · `openWorldHint: false`

**Description:**
- Creates a new draft email in the authenticated user's Gmail account.
- Takes recipient addresses, a subject, and body content as inputs.
- If the draft is a reply to an existing message, pass the original message's ID in `replyToMessageId`.
- **Returns only the unique ID (`id`) of the draft message.**
- Limitation: creating drafts with attachments is not supported yet.

**Request** (no required fields):
| Field | Type | Notes |
|---|---|---|
| `to` / `cc` / `bcc` | string[] | **"Each string MUST be a valid plain email address. The `Name <email>` format is NOT supported by this tool."** |
| `subject` | string | defaults to empty |
| `body` | string | if `htmlBody` is also given, this is the plain-text alternative |
| `htmlBody` | string | rich-text version |
| `replyToMessageId` | string | threads the draft; `body`/`htmlBody` get **appended to the original message body** |
| `attachments` | `Attachment[]` | combined size ≤ 25MB; larger → upload to Drive and link in the body |

```json
{
  "name": "create_draft",
  "arguments": {
    "to": ["amara.nwosu@veranotravel.com"],
    "cc": ["rohan.mehta@maplesoftware.net"],
    "subject": "Re: Settlement reconciliation fix + board review",
    "body": "Amara -- confirming the walkthrough for Thursday.",
    "replyToMessageId": "EMAIL-0005"
  }
}
```

**Response:** the description says "returns only the unique ID", but `outputSchema` is a **full
`Draft`**. Two more contradictions in Google's own spec: attachments are called "not supported yet"
while `inputSchema` fully defines them with a 25MB cap. **Follow the schema, not the prose.**

```json
{
  "id": "DRAFT-0001",
  "threadId": "THR-0101",
  "subject": "Re: Settlement reconciliation fix + board review",
  "toRecipients": ["amara.nwosu@veranotravel.com"],
  "ccRecipients": ["rohan.mehta@maplesoftware.net"],
  "bccRecipients": [],
  "date": "2026-08-05",
  "plaintextBody": "Amara -- confirming the walkthrough for Thursday."
}
```

### 4.2 `label_thread`

`readOnlyHint: false` · `destructiveHint: false` · `idempotentHint: true` · `openWorldHint: false`

**Description:**
- Adds labels to an entire thread. **Affects all messages currently in the thread and any future
  messages added to it.**
- If unsure of the thread ID, use `search_threads` first.
- If unsure of a user label's ID, use `list_labels` first to discover available labels and their IDs.
- **To add a Trash or Spam label, or move a thread to Trash, use `apply_sensitive_thread_label` instead.**

**Request:** `threadId` (**required**), `labelIds[]` (**required** — system IDs like `INBOX`,
`STARRED`, `UNREAD`, `IMPORTANT`, or user-defined IDs). **"The tool accepts `label_ids` and not
label names."**

```json
{ "name": "label_thread", "arguments": { "threadId": "THR-0101", "labelIds": ["Label_6", "IMPORTANT"] } }
```

**Response:** `{}` — empty object, no fields.

### 4.3 `unlabel_thread`

`readOnlyHint: false` · **`destructiveHint: true`** · `idempotentHint: true` · `openWorldHint: false`

**Description:**
- Removes labels from an entire thread.
- If unsure of the thread ID, use `search_threads` first. If unsure of a user label's ID, use
  `list_labels` first.

**Request:** `threadId` (**required**), `labelIds[]` (**required** — system list here also includes
`TRASH`/`SPAM`; IDs not names).

```json
{ "name": "unlabel_thread", "arguments": { "threadId": "THR-0101", "labelIds": ["Label_5"] } }
```

**Response:** `{}`

### 4.4 `apply_sensitive_thread_label`

`readOnlyHint: false` · **`destructiveHint: true`** · `idempotentHint: true` · `openWorldHint: false`

**Description:**
- Adds a sensitive label (Trash or Spam) to an entire thread. **Affects all messages currently in
  the thread and any future messages added to it.**
- Use to trash a thread, mark a thread as spam, or move the specified thread to Trash.
- To find the thread ID, use `search_threads` first.

**Request:** `threadId` (**required**), `labelOption` (**required** — `TRASH` | `SPAM`).

```json
{ "name": "apply_sensitive_thread_label", "arguments": { "threadId": "THR-0148", "labelOption": "SPAM" } }
```

**Response:** `{}`

### 4.5 `label_message`

`readOnlyHint: false` · `destructiveHint: false` · `idempotentHint: true` · `openWorldHint: false`

**Description:**
- Adds one or more labels to a **specific message** rather than its whole thread.
- To find the message ID, use `search_threads` or `get_thread`. If unsure of a user label's ID, use
  `list_labels` first.
- **To add a Trash or Spam label, or move a message to Trash, use `apply_sensitive_message_label` instead.**

**Request:** `messageId` (**required**), `labelIds[]` (**required** — IDs not names).

```json
{ "name": "label_message", "arguments": { "messageId": "EMAIL-0004", "labelIds": ["Label_6"] } }
```

**Response:** `{}`

### 4.6 `unlabel_message`

`readOnlyHint: false` · **`destructiveHint: true`** · `idempotentHint: true` · `openWorldHint: false`

**Description:**
- Removes one or more labels from a specific message.
- To find the message ID, use `search_threads` or `get_thread`. If unsure of a user label's ID, use
  `list_labels` first.

**Request:** `messageId` (**required**), `labelIds[]` (**required**).

```json
{ "name": "unlabel_message", "arguments": { "messageId": "EMAIL-0001", "labelIds": ["UNREAD"] } }
```

**Response:** `{}`

### 4.7 `apply_sensitive_message_label`

`readOnlyHint: false` · **`destructiveHint: true`** · `idempotentHint: true` · `openWorldHint: false`

**Description:**
- Adds a sensitive label (Trash or Spam) to a specific message.
- Use to trash a message, mark a message as spam, or move the specified message to Trash.
- To find the message ID, use `search_threads` or `get_thread`. **To find a draft message ID, use
  `list_drafts`.**

**Request:** `messageId` (**required**), `labelOption` (**required** — `TRASH` | `SPAM`).

```json
{ "name": "apply_sensitive_message_label", "arguments": { "messageId": "EMAIL-0048", "labelOption": "TRASH" } }
```

**Response:** `{}`

### 4.8 `create_label`

`readOnlyHint: false` · `destructiveHint: false` · **`idempotentHint: false`** · `openWorldHint: false`

**Description:**
- Creates a new label in the authenticated user's Gmail account.
- Supports **nested labels (sub-labels) using a forward slash** — e.g. `Projects/Alpha/Sprint-1`.
- By default, parent labels are automatically created if they do not exist.

**Request:**
| Field | Type | Required | Notes |
|---|---|---|---|
| `displayName` | string | **yes** | `/`-separated for nesting |
| `color` | `LabelColor` | no | palette-constrained per §1 |
| `autoCreateParentLabels` | boolean | no | default **true** |

```json
{
  "name": "create_label",
  "arguments": {
    "displayName": "Renewals/Q4",
    "color": { "textColor": "#ffffff", "backgroundColor": "#cc3a21" },
    "autoCreateParentLabels": true
  }
}
```

**Response:** a full `Label` — the **only write tool that returns a populated body** rather than `{}`.

```json
{
  "labelId": "Label_9",
  "name": "Renewals/Q4",
  "color": { "textColor": "#ffffff", "backgroundColor": "#cc3a21" },
  "messagesTotal": 0,
  "messagesUnread": 0,
  "threadsTotal": 0,
  "threadsUnread": 0
}
```

---

## 5. Query operator catalog

The complete operator list from `search_threads.query`. This is the build spec for
`_parse_gmail_query()`.

**Sender & recipient**
| Operator | Meaning |
|---|---|
| `from:` | sent from a specific person |
| `to:` | sent to a specific person |
| `cc:` | specific people in Cc |
| `bcc:` | specific people in Bcc |
| `deliveredto:` | delivered to a specific address |
| `list:` | from a specific mailing list |

**Time & date** — note the format is `YYYY/MM/DD` (**slashes**). Since RFC 3339 sorts
lexicographically, `date >= '2026-06-01'` works as a plain string comparison in SQLite.
| Operator | Meaning |
|---|---|
| `after:YYYY/MM/DD` / `newer:YYYY/MM/DD` | received after a date |
| `before:YYYY/MM/DD` / `older:YYYY/MM/DD` | received before a date |
| `newer_than:<n><unit>` | newer than a duration relative to **now** (`7d`, `1y`) |
| `older_than:<n><unit>` | older than a duration relative to **now** |

**Content**
| Operator | Meaning |
|---|---|
| `subject:` | words in the subject line |
| `"exact phrase"` | exact word or phrase |
| bare term | free-text search |
| `+word` | match a word exactly (no stemming) |
| `AROUND n` | words within n of each other — maps to FTS5 `NEAR(a b, n)` |
| `has:attachment` | has an attachment |
| `has:drive` / `has:youtube` / `has:document` | embedded content types |
| `filename:` | attachment with a specific name or type |
| `rfc822msgid:` | specific `Message-ID` header |

**Labels & categories**
| Operator | Meaning |
|---|---|
| `label:` | under a specific label — **"accepts label IDs, not display names. Use the `list_labels` tool to get the ID."** |
| `in:inbox` / `in:sent` / `in:trash` / `in:draft` / `in:archive` / `in:snoozed` | search in specific folders |
| `in:anywhere` | search all folders, including spam and trash |
| `category:` | primary, social, promotions, updates, forums, reservations, purchases |
| `has:userlabels` | has any user label |
| `has:nouserlabels` | has no user labels |
| `has:*-star` | specific star colors, e.g. `has:yellow-star` |

Defaults spelled out by the description: *"Archived and sent messages are included by default; use
`-in:archive` and `-in:sent` to exclude them. Drafts are explicitly excluded by default by the
tool. Use `in:inbox` to restrict search to the inbox only."*

**Status** — `is:important`, `is:starred`, `is:unread`, `is:read`, `is:muted`

**Size** — `size:<bytes>`, `larger:<size>`, `smaller:<size>` (e.g. `10M` for 10 MB)

**Logic & grouping**
| Operator | Meaning |
|---|---|
| `AND` / juxtaposition | match all criteria (default behavior) |
| `OR` or `{ }` | match one or more — `from:amy OR from:david`, `{from:amy from:david}` |
| `-` (minus) | exclude criteria — `-movie`, `-is:starred` |
| `( )` | group terms — `subject:(dinner film)` means subject contains dinner **or** film |

**v1 is AND-only, flat clause list**, same shape as `pm`'s `_parse_jql` and `file-server`'s
`_parse_query`. `OR`/`( )` need a recursive-descent boolean parser producing a predicate tree — a
materially bigger lift. Negation is the cheapest of the three and the most likely needed first
(the tool description itself uses `-is:starred` and `is:unread -in:draft` as examples): a flat
clause list can carry a `negated: bool` per clause without any tree.

**Parser test cases, verbatim from the tool descriptions:**
```
subject:OneMCP Update
from:user@example.com
to:user2@example.com AND newer_than:7d
project proposal has:attachment
is:unread -in:draft
from:amy OR from:david
{from:amy from:david}
subject:(dinner film)
holiday AROUND 10 vacation
-movie
```
