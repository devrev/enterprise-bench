"""
Automated tests for the Mail (Gmail) MCP server (MCP HTTP port 9014).

Covers all 13 tools exposed by ``gmail-server/mcp_server.py``.

Read:

search_threads  — search threads with Gmail query syntax, paginated
get_thread      — fetch one thread and all of its messages
get_message     — fetch one message by ID
list_labels     — list every label with message and thread counts
list_drafts     — list drafts, filtered by the same query syntax

Write:

create_draft                    — compose a draft, optionally replying to a message
label_thread / unlabel_thread   — add or remove labels across a whole thread
label_message / unlabel_message — add or remove labels on one message
apply_sensitive_thread_label    — move a thread to Trash or mark it Spam
apply_sensitive_message_label   — move a message to Trash or mark it Spam
create_label                    — create a user label, with ``/``-nested parents

How this server differs from its siblings
-----------------------------------------
Same two-module split - ``server.py`` for the data layer, ``mcp_server.py`` importing it
in-process - but ``server.py`` carries no FastAPI app, because nothing in the agent path
calls a REST port for mail.  So there is no REST half to test here, and the store is an
in-process SQLite database built by ``setup_db()`` instead of a dict built by
``_load_data()``.

Tool results are checked by shape and by cross-checking one tool against another
(e.g. an ID found by ``search_threads`` must resolve through ``get_message``) rather
than against hard-coded record IDs, so the tests survive edits to the fixture data.
Where a value is a documented contract rather than data — ``resultCountEstimate`` being
a JSON string, ``labelIds`` being IDs and not display names — it is asserted directly.

Testing the write half
----------------------
``mcp_test_client`` is class-scoped (see ``conftest.py``), so every test in this class
shares one database and a write is visible to every test that runs after it.  The write
tests are therefore built two ways, and never assert an absolute count:

* **Reversible writes restore what they changed.**  A test that labels a thread unlabels
  it before returning, so the mailbox other tests see is the one they were written
  against.
* **Creates assert deltas.**  There is no delete tool for a draft or a label - the real
  toolset has none either - so those tests measure before-and-after rather than
  before-and-absolute, and each generates a unique name so a re-run cannot collide with
  its own leftovers.

Nothing reaches the dataset in either case: the store is ``:memory:`` and the JSON is
mounted read-only, so a fresh process starts from the authored mailbox again.


Inherited tests (from BaseMCPServerTests)
-----------------------------------------
test_tool_discovery
test_all_tool_schemas_valid
test_unknown_tool_raises_error
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

from base.base_mcp_test import BaseMCPServerTests

_BAD_THREAD_ID = "THR-DOES-NOT-EXIST-9999"
_BAD_MESSAGE_ID = "EMAIL-DOES-NOT-EXIST-9999"

#: A query guaranteed to match nothing, for "empty is not an error" assertions.
_NO_MATCH_QUERY = "from:nobody@xyzzy-no-match-guaranteed-9999.invalid"

#: Fields every Message carries, at every message_format.
_MESSAGE_METADATA_FIELDS = (
    "id",
    "sender",
    "toRecipients",
    "ccRecipients",
    "bccRecipients",
    "date",
    "labelIds",
)


class TestMailServer(BaseMCPServerTests):
    """
    Test suite for the Mail MCP server.

    Verifies all 13 tools exposed by ``gmail-server/mcp_server.py`` across both unit
    (PatchedFastMCPHarness) and integration (Docker/Streamable HTTP) modes.

    The server loads ``integrations/data/email_json_data/`` (messages, labels,
    attachments) into an in-process SQLite database and exposes it as a replica of
    Google's official Gmail MCP server.
    """

    server_name = "mail-mcp"
    server_port = 9014
    expected_tools = [
        # Read
        "search_threads",
        "get_thread",
        "get_message",
        "list_labels",
        "list_drafts",
        # Write
        "create_draft",
        "label_thread",
        "unlabel_thread",
        "apply_sensitive_thread_label",
        "label_message",
        "unlabel_message",
        "apply_sensitive_message_label",
        "create_label",
    ]

    # ------------------------------------------------------------------
    # FastMCP app factory (unit mode)
    # ------------------------------------------------------------------

    @classmethod
    def _build_app(cls, data_dir: Path):
        """Build the Mail FastMCP app in-process for unit testing.

        Uses importlib to load server.py with a unique module name, then
        registers it as ``sys.modules["server"]`` so mcp_server.py's
        ``from server import ...`` resolves correctly.

        The only deviation from the siblings is the load call: ``setup_db()`` builds a
        SQLite database rather than filling a dict, and it must run *after* ``DATA_DIR``
        is assigned because the source path is resolved when the loader runs.
        """
        server_dir = Path(__file__).parent.parent / "gmail-server"

        # Load server.py under a unique module name to avoid collisions when
        # multiple servers are loaded in the same pytest session.
        spec = importlib.util.spec_from_file_location(
            "mail_server_server", server_dir / "server.py"
        )
        server_mod = importlib.util.module_from_spec(spec)
        sys.modules["server"] = server_mod
        spec.loader.exec_module(server_mod)

        # Point the server at the test fixture data directory and build the database.
        server_mod.DATA_DIR = data_dir
        server_mod.setup_db()

        # Load mcp_server.py — it will import from the ``server`` module we just set.
        mcp_spec = importlib.util.spec_from_file_location(
            "mail_server_mcp", server_dir / "mcp_server.py"
        )
        mcp_mod = importlib.util.module_from_spec(mcp_spec)
        mcp_spec.loader.exec_module(mcp_mod)
        return mcp_mod.mcp

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _first_thread(self) -> dict:
        """Return the first thread from an unfiltered search."""
        result = self._call_json("search_threads")
        assert result["threads"], "No threads found in the fixture data directory."
        return result["threads"][0]

    def _first_message_id(self) -> str:
        """Return the ID of the first message of the first thread."""
        thread = self._first_thread()
        assert thread["messages"], f"Thread {thread['id']} has no messages."
        return thread["messages"][0]["id"]

    def _all_threads(self) -> list[dict]:
        """Return every thread the default query yields, in one page."""
        return self._call_json("search_threads", page_size=50)["threads"]

    def _labels_of(self, message_id: str) -> list[str]:
        """The labelIds currently on one message, read back through get_message."""
        return self._call_json("get_message", message_id=message_id)["labelIds"]

    def _user_label_id(self) -> str:
        """
        The ID of some label that exists and carries a color, i.e. a user label.

        Picked by shape rather than hard-coded, so a fixture edit that renames or
        renumbers the authored labels does not break the write tests.  Colors are the
        discriminator because ``_label`` omits the field on system labels.
        """
        for label in self._call_json("list_labels", page_size=0)["labels"]:
            if "color" in label:
                return label["labelId"]
        pytest.fail("No user label (one carrying a color) in the fixture data.")

    def _unlabeled_thread(self, label_id: str) -> dict:
        """
        A thread that does not already carry ``label_id``, for an add-then-remove test.

        Starting from a thread that already has the label would make the add a no-op and
        the remove a real change, so the restore would not restore.
        """
        for thread in self._all_threads():
            if all(
                label_id not in self._labels_of(message["id"])
                for message in thread["messages"]
            ):
                return thread
        pytest.fail(f"Every thread already carries {label_id}.")

    def _unique_name(self, stem: str) -> str:
        """
        A label name no earlier call can have used.

        Derived from the current label count rather than a random value or a clock, both
        of which the rest of this server goes out of its way to avoid: ``MAIL_NOW`` is
        pinned and every ID is sequential, so a test helper that reintroduced
        nondeterminism would be the one unreproducible thing in the suite.
        """
        count = len(self._call_json("list_labels", page_size=0)["labels"])
        return f"{stem}-{count}"

    # ------------------------------------------------------------------
    # search_threads — envelope
    # ------------------------------------------------------------------

    def test_search_threads_returns_envelope(self) -> None:
        """search_threads returns a dict with 'threads' and 'resultCountEstimate'."""
        result = self._call_json("search_threads")
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
        assert "threads" in result, "Missing 'threads' key in response."
        assert "resultCountEstimate" in result, "Missing 'resultCountEstimate' key."

    def test_search_threads_non_empty(self) -> None:
        """search_threads returns at least one thread from fixture data."""
        result = self._call_json("search_threads")
        assert result["threads"], "Expected at least one thread."
        assert int(result["resultCountEstimate"]) > 0, "Expected a non-zero estimate."

    def test_search_threads_result_count_estimate_is_string(self) -> None:
        """resultCountEstimate is a JSON string, not a number (proto3 int64)."""
        result = self._call_json("search_threads")
        assert isinstance(result["resultCountEstimate"], str), (
            f"Expected str, got {type(result['resultCountEstimate']).__name__}: "
            f"{result['resultCountEstimate']!r}"
        )

    def test_search_threads_thread_shape(self) -> None:
        """Each thread has an 'id' and a non-empty 'messages' list."""
        for thread in self._all_threads():
            assert "id" in thread, f"Thread missing 'id': {thread}"
            assert thread.get("messages"), f"Thread {thread.get('id')} has no messages."

    def test_search_threads_message_metadata_fields(self) -> None:
        """Every message carries the metadata fields present at all formats."""
        for thread in self._all_threads():
            for message in thread["messages"]:
                missing = set(_MESSAGE_METADATA_FIELDS) - set(message)
                assert not missing, f"Message {message.get('id')} missing {missing}."

    def test_search_threads_messages_chronological(self) -> None:
        """Messages within a thread are ordered oldest to newest."""
        for thread in self._all_threads():
            dates = [message["date"] for message in thread["messages"]]
            assert dates == sorted(dates), (
                f"Thread {thread['id']} messages are not chronological: {dates}"
            )

    def test_search_threads_no_bodies(self) -> None:
        """search_threads never returns message bodies — that is get_thread's job."""
        for thread in self._all_threads():
            for message in thread["messages"]:
                assert "plaintextBody" not in message, (
                    f"Message {message['id']} leaked plaintextBody into a search result."
                )
                assert "htmlBody" not in message, (
                    f"Message {message['id']} leaked htmlBody into a search result."
                )

    # ------------------------------------------------------------------
    # search_threads — views
    # ------------------------------------------------------------------

    def test_search_threads_minimal_view_has_snippet(self) -> None:
        """THREAD_VIEW_MINIMAL (the default) includes subject and snippet."""
        result = self._call_json("search_threads", view="THREAD_VIEW_MINIMAL")
        message = result["threads"][0]["messages"][0]
        assert "subject" in message, "MINIMAL view should include 'subject'."
        assert "snippet" in message, "MINIMAL view should include 'snippet'."

    def test_search_threads_metadata_view_omits_snippet(self) -> None:
        """THREAD_VIEW_METADATA_ONLY excludes subject and snippet."""
        result = self._call_json("search_threads", view="THREAD_VIEW_METADATA_ONLY")
        for thread in result["threads"]:
            for message in thread["messages"]:
                assert "subject" not in message, "METADATA_ONLY leaked 'subject'."
                assert "snippet" not in message, "METADATA_ONLY leaked 'snippet'."

    def test_search_threads_unspecified_view_uses_default(self) -> None:
        """The _UNSPECIFIED enum value aliases to the default view, not an error."""
        result = self._call_json("search_threads", view="THREAD_VIEW_UNSPECIFIED")
        assert "snippet" in result["threads"][0]["messages"][0], (
            "_UNSPECIFIED should behave as THREAD_VIEW_MINIMAL."
        )

    def test_search_threads_unrecognized_view_uses_default(self) -> None:
        """An unrecognized view formats as the default rather than failing the call."""
        result = self._call_json("search_threads", view="NOT_A_REAL_VIEW")
        assert "threads" in result, f"Expected a normal response, got: {result}"
        assert "snippet" in result["threads"][0]["messages"][0]

    # ------------------------------------------------------------------
    # search_threads — query operators
    # ------------------------------------------------------------------

    def test_search_threads_empty_query_returns_all(self) -> None:
        """An empty query returns every eligible thread."""
        empty = self._call_json("search_threads", query="", page_size=50)
        default = self._call_json("search_threads", page_size=50)
        assert empty["resultCountEstimate"] == default["resultCountEstimate"]

    def test_search_threads_from_operator_filters(self) -> None:
        """from: narrows the result set to threads containing that sender."""
        sender = self._first_thread()["messages"][0]["sender"]
        result = self._call_json("search_threads", query=f"from:{sender}", page_size=50)
        assert result["threads"], f"Expected at least one thread from {sender}."
        for thread in result["threads"]:
            assert any(m["sender"] == sender for m in thread["messages"]), (
                f"Thread {thread['id']} has no message from {sender}."
            )

    def test_search_threads_from_matches_address_substring(self) -> None:
        """from: is a substring match, so a fragment of the address finds the sender."""
        sender = self._first_thread()["messages"][0]["sender"]
        fragment = sender.split("@")[0].split(".")[0]
        full = self._call_json("search_threads", query=f"from:{sender}", page_size=50)
        partial = self._call_json("search_threads", query=f"from:{fragment}", page_size=50)
        found = {thread["id"] for thread in partial["threads"]}
        assert found >= {thread["id"] for thread in full["threads"]}, (
            f"from:{fragment} lost threads that from:{sender} returned."
        )

    def test_search_threads_from_is_case_insensitive(self) -> None:
        """A correctly spelled address in the wrong case still matches."""
        sender = self._first_thread()["messages"][0]["sender"]
        lower = self._call_json("search_threads", query=f"from:{sender}", page_size=50)
        upper = self._call_json(
            "search_threads", query=f"from:{sender.upper()}", page_size=50
        )
        assert [t["id"] for t in upper["threads"]] == [
            t["id"] for t in lower["threads"]
        ], "Case changed the result set; from: must be case-insensitive."

    def test_search_threads_from_matches_display_name(self) -> None:
        """from: matches the display name, not only the address."""
        message = self._call_json(
            "get_message",
            message_id=self._first_thread()["messages"][0]["id"],
            message_format="FULL_CONTENT",
        )
        surname = message["sender"].split("@")[0].split(".")[-1]
        result = self._call_json("search_threads", query=f"from:{surname}", page_size=50)
        assert result["threads"], (
            f"from:{surname} found nothing; the display name is not being matched."
        )

    def test_search_threads_from_matches_the_sender_column(self) -> None:
        """
        from: reaches the `sender` column, not just `from`.

        `sender` is the address the wire reports, so it is the one an agent copies out of
        a search result and back into a query. The two differ on any message transmitted
        by a delegate or a shared mailbox on someone else's behalf, and matching only
        `from` would make such a message unreachable by the address it advertises.
        """
        senders = {
            message["sender"]
            for thread in self._call_json("search_threads", page_size=50)["threads"]
            for message in thread["messages"]
        }
        for sender in sorted(senders):
            result = self._call_json(
                "search_threads", query=f"from:{sender}", page_size=50
            )
            assert result["threads"], (
                f"search_threads reported sender {sender}, but from:{sender} "
                "returns nothing - the address it advertises is unqueryable."
            )

    def test_search_threads_recipient_operators_match_substrings(self) -> None:
        """to:/cc:/bcc: are substring matches over the same Person halves as from:."""
        for thread in self._call_json("search_threads", page_size=50)["threads"]:
            for message in thread["messages"]:
                if message["toRecipients"]:
                    recipient = message["toRecipients"][0]
                    break
            else:
                continue
            break
        else:
            pytest.skip("No message with a To recipient in the fixture data.")

        fragment = recipient.split("@")[0].split(".")[0]
        full = self._call_json("search_threads", query=f"to:{recipient}", page_size=50)
        partial = self._call_json("search_threads", query=f"to:{fragment}", page_size=50)
        assert {t["id"] for t in partial["threads"]} >= {
            t["id"] for t in full["threads"]
        }, f"to:{fragment} lost threads that to:{recipient} returned."

    def test_search_threads_from_escapes_like_wildcards(self) -> None:
        """
        An underscore in an address is a literal, not LIKE's single-character wildcard.

        Unescaped, `from:a_c` would match `abc@...`, so a real address containing an
        underscore would match addresses that merely resemble it.
        """
        result = self._call_json("search_threads", query="from:_", page_size=50)
        assert not result["threads"], (
            "from:_ matched threads; LIKE's wildcard is not being escaped."
        )

    def test_search_threads_is_unread_matches_unread_label(self) -> None:
        """is:unread returns threads containing a message labeled UNREAD."""
        result = self._call_json("search_threads", query="is:unread", page_size=50)
        if not result["threads"]:
            pytest.skip("No unread messages in the fixture data.")
        for thread in result["threads"]:
            assert any("UNREAD" in m["labelIds"] for m in thread["messages"]), (
                f"Thread {thread['id']} matched is:unread with no UNREAD message."
            )

    def test_search_threads_is_starred_matches_starred_label(self) -> None:
        """is:starred returns threads containing a message labeled STARRED."""
        result = self._call_json("search_threads", query="is:starred", page_size=50)
        if not result["threads"]:
            pytest.skip("No starred messages in the fixture data.")
        for thread in result["threads"]:
            assert any("STARRED" in m["labelIds"] for m in thread["messages"]), (
                f"Thread {thread['id']} matched is:starred with no STARRED message."
            )

    def test_search_threads_negation_narrows_results(self) -> None:
        """Negating an operator returns no more threads than the unfiltered query."""
        everything = self._call_json("search_threads", page_size=50)
        negated = self._call_json("search_threads", query="-is:starred", page_size=50)
        assert int(negated["resultCountEstimate"]) <= int(
            everything["resultCountEstimate"]
        ), "A negated filter cannot widen the result set."

    def test_search_threads_negation_is_per_message(self) -> None:
        """
        A negated query can still return threads containing a matching message.

        This is the documented behavior of the real tool, not a bug: matching happens
        per message, so a thread is returned when *any* message matches, which means
        -is:starred can return a thread that also contains a starred message.
        """
        result = self._call_json("search_threads", query="-is:starred", page_size=50)
        for thread in result["threads"]:
            assert any("STARRED" not in m["labelIds"] for m in thread["messages"]), (
                f"Thread {thread['id']} matched -is:starred but every message is starred."
            )

    def test_search_threads_and_is_implicit(self) -> None:
        """Two operators combine with AND, so the result is a subset of either alone."""
        one = self._call_json("search_threads", query="is:read", page_size=50)
        both = self._call_json(
            "search_threads", query="is:read has:attachment", page_size=50
        )
        assert int(both["resultCountEstimate"]) <= int(one["resultCountEstimate"])

    def test_search_threads_has_attachment(self) -> None:
        """has:attachment returns threads whose messages carry attachment IDs."""
        result = self._call_json("search_threads", query="has:attachment", page_size=50)
        if not result["threads"]:
            pytest.skip("No messages with attachments in the fixture data.")
        for thread in result["threads"]:
            ids = [m["id"] for m in thread["messages"]]
            full = [self._call_json("get_message", message_id=i) for i in ids]
            assert any(m.get("attachmentIds") for m in full), (
                f"Thread {thread['id']} matched has:attachment with no attachments."
            )

    def test_search_threads_date_range_covers_corpus(self) -> None:
        """
        before: and after: on the same date together match every message.

        Because before: is exclusive of the named day and after: inclusive of it, the
        two are complements at the *message* level, so between them they have to match
        everything.  An off-by-one in either direction drops or double-counts the
        boundary day, and this identity breaks.

        The assertion is over the union rather than checking the two are disjoint: they
        are not, and should not be.  Matching happens per message but results come back
        as whole threads, so a thread matched by before: still carries any later
        messages it contains.  What that does forbid is a *single-message* thread landing
        on both sides — that message would have to be simultaneously before and after
        one instant — which is asserted below without duplicating the timezone maths.
        """
        date = "2026/07/16"
        everything = self._call_json("search_threads", query="in:anywhere", page_size=50)
        before = self._call_json(
            "search_threads", query=f"in:anywhere before:{date}", page_size=50
        )
        after = self._call_json(
            "search_threads", query=f"in:anywhere after:{date}", page_size=50
        )

        def message_ids(response: dict) -> set[str]:
            return {m["id"] for t in response["threads"] for m in t["messages"]}

        assert message_ids(before) | message_ids(after) == message_ids(everything), (
            f"before:{date} and after:{date} together must cover every message."
        )

        def threads_by_id(response: dict) -> dict[str, dict]:
            return {thread["id"]: thread for thread in response["threads"]}

        before_threads, after_threads = threads_by_id(before), threads_by_id(after)
        assert set(before_threads) | set(after_threads) == set(
            threads_by_id(everything)
        ), f"before:{date} and after:{date} together must cover every thread."

        for thread_id in set(before_threads) & set(after_threads):
            assert len(before_threads[thread_id]["messages"]) > 1, (
                f"Thread {thread_id} has one message but matched both sides of {date}."
            )

    def test_search_threads_before_is_exclusive_of_named_day(self) -> None:
        """
        before: excludes the named day itself; after: includes it.

        Asserted at the message level via single-message threads, where a thread's match
        cannot be explained by a sibling message.  If before: were inclusive (a common
        off-by-one, and the reason a bare-date string comparison is wrong), a message
        sent during 2026-07-16 would show up on the before: side.
        """
        date, prefix = "2026/07/16", "2026-07-16"
        before = self._call_json(
            "search_threads", query=f"in:anywhere before:{date}", page_size=50
        )
        after = self._call_json(
            "search_threads", query=f"in:anywhere after:{date}", page_size=50
        )

        def solo_message_dates(response: dict) -> list[str]:
            return [
                thread["messages"][0]["date"]
                for thread in response["threads"]
                if len(thread["messages"]) == 1
            ]

        for value in solo_message_dates(before):
            assert not value.startswith(prefix), (
                f"before:{date} returned a message sent on {value} — "
                "before: must exclude the named day."
            )
        on_the_day = [d for d in solo_message_dates(after) if d.startswith(prefix)]
        if not on_the_day:
            pytest.skip(f"No single-message thread dated {prefix} in the fixture data.")

    def test_search_threads_relative_date_is_reproducible(self) -> None:
        """newer_than: resolves against the data's horizon, so it is stable."""
        first = self._call_json("search_threads", query="newer_than:7d", page_size=50)
        second = self._call_json("search_threads", query="newer_than:7d", page_size=50)
        assert first == second, "newer_than: must not depend on the wall clock."

    def test_search_threads_free_text_search(self) -> None:
        """A bare word runs a full-text search over subjects and bodies."""
        subject = self._first_thread()["messages"][0]["subject"]
        word = max(subject.split(), key=len).strip(".,:;!?\"'")
        result = self._call_json("search_threads", query=word, page_size=50)
        assert result["threads"], f"Full-text search for {word!r} found nothing."

    def test_search_threads_free_text_survives_punctuation(self) -> None:
        """
        A search term containing punctuation is a literal, not search-engine syntax.

        The full-text index has an expression language of its own in which a hyphen is
        negation and a colon is a column filter, so an unescaped term like "All-hands"
        or "a:b" is a syntax error against it rather than a search — which surfaces as a
        failed tool call on a perfectly ordinary user query.
        """
        for term in ("All-hands", "multi-currency", "a:b", "re:", "*", "(", '""'):
            result = self._call_json("search_threads", query=term, page_size=50)
            assert "error" not in result, (
                f"Searching for {term!r} returned an error: {result['error']}"
            )
            assert "threads" in result, f"Searching {term!r} returned no envelope."

    def test_search_threads_hyphenated_term_finds_its_subject(self) -> None:
        """A hyphenated word from a real subject line finds the message it came from."""
        subjects = {
            message["id"]: message["subject"]
            for thread in self._all_threads()
            for message in thread["messages"]
        }
        hyphenated = [
            (mid, word.strip(".,:;!?\"'"))
            for mid, subject in subjects.items()
            for word in subject.split()
            if "-" in word.strip(".,:;!?\"'")
        ]
        if not hyphenated:
            pytest.skip("No hyphenated words in any fixture subject line.")
        message_id, word = hyphenated[0]
        result = self._call_json("search_threads", query=word, page_size=50)
        found = {m["id"] for t in result["threads"] for m in t["messages"]}
        assert message_id in found, (
            f"Searching for {word!r} did not find {message_id}, whose subject contains it."
        )

    def test_search_threads_quoted_phrase_is_one_term(self) -> None:
        """A quoted phrase searches as a phrase, so it cannot beat its own words."""
        loose = self._call_json("search_threads", query="board review", page_size=50)
        phrase = self._call_json("search_threads", query='"board review"', page_size=50)
        assert int(phrase["resultCountEstimate"]) <= int(loose["resultCountEstimate"])

    def test_search_threads_label_accepts_name_and_id(self) -> None:
        """label: accepts a display name or a label ID, case-insensitively."""
        labels = self._call_json("list_labels")["labels"]
        user_labels = [label for label in labels if "color" in label]
        if not user_labels:
            pytest.skip("No user labels in the fixture data.")
        label = user_labels[0]

        by_name = self._call_json("search_threads", query=f"label:{label['name']}")
        by_id = self._call_json("search_threads", query=f"label:{label['labelId']}")
        by_lower = self._call_json(
            "search_threads", query=f"label:{label['name'].lower()}"
        )
        assert (
            by_name["resultCountEstimate"]
            == by_id["resultCountEstimate"]
            == by_lower["resultCountEstimate"]
        ), "Label name, label ID, and lowercased name must resolve identically."

    def test_search_threads_derived_label_agrees_with_is_operator(self) -> None:
        """
        label:IMPORTANT and is:important describe the same set, and so do the others.

        UNREAD, IMPORTANT and STARRED are exposed both as labels and as states, but they
        are computed from message columns rather than stored in the label list, so a
        naive label lookup finds nothing for them — a silent zero-result query on a
        label that list_labels advertises with a non-zero count.
        """
        for label, state in (
            ("UNREAD", "unread"),
            ("IMPORTANT", "important"),
            ("STARRED", "starred"),
        ):
            by_label = self._call_json(
                "search_threads", query=f"label:{label}", page_size=50
            )
            by_state = self._call_json(
                "search_threads", query=f"is:{state}", page_size=50
            )
            assert by_label["resultCountEstimate"] == by_state["resultCountEstimate"], (
                f"label:{label} found {by_label['resultCountEstimate']} threads but "
                f"is:{state} found {by_state['resultCountEstimate']}."
            )

    def test_search_threads_unknown_label_returns_error(self) -> None:
        """An unknown label is an error, not a silent zero-result query."""
        result = self._call_json("search_threads", query="label:no-such-label-9999")
        assert "error" in result, f"Expected 'error' for an unknown label, got: {result}"

    def test_search_threads_unsupported_operator_is_reported(self) -> None:
        """
        An operator this dataset cannot answer is reported, not silently dropped.

        Without this an ignored filter is indistinguishable from a filter that matched
        nothing, which would let an agent draw a false conclusion from the result.
        """
        result = self._call_json("search_threads", query="larger:10M")
        assert "unsupportedOperators" in result, (
            f"Expected 'unsupportedOperators' in the response, got: {sorted(result)}"
        )
        assert "larger" in result["unsupportedOperators"]

    def test_search_threads_or_is_reported_unsupported(self) -> None:
        """OR is not supported; it is reported and the clauses combine with AND."""
        result = self._call_json("search_threads", query="is:read OR is:starred")
        assert "OR" in result.get("unsupportedOperators", {}), (
            f"Expected OR reported as unsupported, got: {result.get('unsupportedOperators')}"
        )

    def test_search_threads_no_match_is_not_an_error(self) -> None:
        """A query matching nothing returns an empty list, not an error."""
        result = self._call_json("search_threads", query=_NO_MATCH_QUERY)
        assert result["threads"] == [], f"Expected no threads, got {result['threads']}"
        assert result["resultCountEstimate"] == "0"
        assert "error" not in result, f"Empty results must not be an error: {result}"

    def test_search_threads_malformed_date_returns_error(self) -> None:
        """A date operator given something that is not a date returns an error."""
        result = self._call_json("search_threads", query="after:not-a-date")
        assert "error" in result, f"Expected 'error' for a malformed date, got: {result}"

    def test_search_threads_malformed_duration_returns_error(self) -> None:
        """newer_than: given something that is not a duration returns an error."""
        result = self._call_json("search_threads", query="newer_than:not-a-duration")
        assert "error" in result, f"Expected 'error' for a bad duration, got: {result}"

    # ------------------------------------------------------------------
    # search_threads — default exclusions and include_trash
    # ------------------------------------------------------------------

    def test_search_threads_excludes_spam_by_default(self) -> None:
        """Spam is hidden from an unfiltered search."""
        for thread in self._all_threads():
            for message in thread["messages"]:
                assert "SPAM" not in message["labelIds"], (
                    f"Message {message['id']} is spam but appeared in a default search."
                )

    def test_search_threads_excludes_trash_by_default(self) -> None:
        """Trash is hidden from an unfiltered search."""
        for thread in self._all_threads():
            for message in thread["messages"]:
                assert "TRASH" not in message["labelIds"], (
                    f"Message {message['id']} is trashed but appeared by default."
                )

    def test_search_threads_in_anywhere_widens(self) -> None:
        """in:anywhere makes spam and trash eligible, so it cannot return fewer."""
        default = self._call_json("search_threads", page_size=50)
        anywhere = self._call_json("search_threads", query="in:anywhere", page_size=50)
        assert int(anywhere["resultCountEstimate"]) >= int(
            default["resultCountEstimate"]
        ), "in:anywhere must not return fewer threads than the default query."

    def test_search_threads_in_spam_reaches_excluded_mail(self) -> None:
        """in:spam overrides the default exclusion instead of guaranteeing zero rows."""
        result = self._call_json("search_threads", query="in:spam", page_size=50)
        for thread in result["threads"]:
            assert any("SPAM" in m["labelIds"] for m in thread["messages"]), (
                f"Thread {thread['id']} matched in:spam with no spam message."
            )

    def test_search_threads_include_trash_does_not_leak(self) -> None:
        """include_trash=True is accepted and never narrows the result set."""
        default = self._call_json("search_threads", page_size=50)
        with_trash = self._call_json(
            "search_threads", page_size=50, include_trash=True
        )
        assert int(with_trash["resultCountEstimate"]) >= int(
            default["resultCountEstimate"]
        ), "include_trash must not remove threads."

    def test_search_threads_default_exclusions_do_not_leak_between_calls(self) -> None:
        """An include_trash call does not change what a later default call returns."""
        before = self._call_json("search_threads", page_size=50)
        self._call_json("search_threads", page_size=50, include_trash=True)
        after = self._call_json("search_threads", page_size=50)
        assert before == after, (
            "A call with include_trash=True leaked into subsequent default calls."
        )

    # ------------------------------------------------------------------
    # search_threads — pagination
    # ------------------------------------------------------------------

    def test_search_threads_respects_page_size(self) -> None:
        """search_threads returns no more threads than page_size."""
        result = self._call_json("search_threads", page_size=2)
        assert len(result["threads"]) <= 2, (
            f"Expected at most 2 threads, got {len(result['threads'])}"
        )

    def test_search_threads_page_size_caps_threads_not_messages(self) -> None:
        """
        page_size counts threads; a returned thread keeps all of its messages.

        Compared against get_thread minus the drafts, not against get_thread outright:
        search_threads excludes DRAFT by default and get_thread takes no query, so a
        thread holding a draft legitimately has more messages in get_thread.  The
        fixture ships one such thread, and any reply draft creates another.
        """
        result = self._call_json("search_threads", page_size=1)
        assert len(result["threads"]) == 1
        thread_id = result["threads"][0]["id"]
        full = self._call_json("get_thread", thread_id=thread_id)
        drafts = {draft["id"] for draft in self._call_json("list_drafts", page_size=50)["drafts"]}
        expected = [m for m in full["messages"] if m["id"] not in drafts]
        assert len(result["threads"][0]["messages"]) == len(expected), (
            "Pagination truncated messages within a thread."
        )

    def test_search_threads_page_size_clamps_to_maximum(self) -> None:
        """An oversized page_size clamps to the documented maximum of 50."""
        result = self._call_json("search_threads", page_size=10_000)
        assert len(result["threads"]) <= 50, (
            f"Expected at most 50 threads, got {len(result['threads'])}"
        )

    def test_search_threads_estimate_counts_all_matches(self) -> None:
        """resultCountEstimate counts every match, not just the returned page."""
        page = self._call_json("search_threads", page_size=1)
        assert int(page["resultCountEstimate"]) >= len(page["threads"])

    def test_search_threads_pagination_covers_every_thread_once(self) -> None:
        """Walking nextPageToken yields every thread exactly once."""
        seen: list[str] = []
        token = ""
        for _ in range(100):                       # guard against a token loop
            page = self._call_json(
                "search_threads", page_size=2, page_token=token
            )
            seen.extend(thread["id"] for thread in page["threads"])
            token = page.get("nextPageToken", "")
            if not token:
                break
        else:
            pytest.fail("Pagination did not terminate after 100 pages.")

        assert len(seen) == len(set(seen)), (
            f"Duplicate threads across pages: {sorted(seen)}"
        )
        total = int(self._call_json("search_threads", page_size=1)["resultCountEstimate"])
        assert len(seen) == total, f"Paged through {len(seen)} threads, expected {total}."

    def test_search_threads_last_page_has_no_next_token(self) -> None:
        """The final page omits nextPageToken."""
        result = self._call_json("search_threads", page_size=50)
        if int(result["resultCountEstimate"]) > 50:
            pytest.skip("Fixture data exceeds one maximum-size page.")
        assert "nextPageToken" not in result, (
            "A page containing every result should not advertise another page."
        )

    def test_search_threads_bad_page_token_returns_first_page(self) -> None:
        """An unparseable page token degrades to the first page rather than erroring."""
        first = self._call_json("search_threads", page_size=2)
        bogus = self._call_json("search_threads", page_size=2, page_token="not-a-token")
        assert [t["id"] for t in bogus["threads"]] == [
            t["id"] for t in first["threads"]
        ], "A stale cursor should restart, not fail or skip."

    # ------------------------------------------------------------------
    # get_thread
    # ------------------------------------------------------------------

    def test_get_thread_returns_dict(self) -> None:
        """get_thread returns a dict for a valid thread ID."""
        thread_id = self._first_thread()["id"]
        result = self._call_json("get_thread", thread_id=thread_id)
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"

    def test_get_thread_correct_id(self) -> None:
        """get_thread echoes the requested thread ID."""
        thread_id = self._first_thread()["id"]
        result = self._call_json("get_thread", thread_id=thread_id)
        assert result.get("id") == thread_id, (
            f"Expected id={thread_id!r}, got {result.get('id')!r}"
        )

    def test_get_thread_returns_messages(self) -> None:
        """get_thread returns a non-empty list of messages."""
        thread_id = self._first_thread()["id"]
        result = self._call_json("get_thread", thread_id=thread_id)
        assert result.get("messages"), f"Thread {thread_id} returned no messages."

    def test_get_thread_messages_chronological(self) -> None:
        """get_thread orders messages oldest to newest."""
        thread_id = self._first_thread()["id"]
        result = self._call_json("get_thread", thread_id=thread_id)
        dates = [message["date"] for message in result["messages"]]
        assert dates == sorted(dates), f"Messages not chronological: {dates}"

    def test_get_thread_full_content_includes_body(self) -> None:
        """FULL_CONTENT (the default) includes exactly one body field per message."""
        thread_id = self._first_thread()["id"]
        result = self._call_json("get_thread", thread_id=thread_id)
        for message in result["messages"]:
            bodies = [k for k in ("plaintextBody", "htmlBody") if k in message]
            assert len(bodies) == 1, (
                f"Message {message['id']} has body fields {bodies}, expected exactly one."
            )

    def test_get_thread_minimal_omits_body(self) -> None:
        """MINIMAL returns subject and snippet but no body."""
        thread_id = self._first_thread()["id"]
        result = self._call_json(
            "get_thread", thread_id=thread_id, message_format="MINIMAL"
        )
        for message in result["messages"]:
            assert "subject" in message and "snippet" in message
            assert "plaintextBody" not in message and "htmlBody" not in message

    def test_get_thread_metadata_only_omits_subject(self) -> None:
        """METADATA_ONLY returns only the metadata fields."""
        thread_id = self._first_thread()["id"]
        result = self._call_json(
            "get_thread", thread_id=thread_id, message_format="METADATA_ONLY"
        )
        for message in result["messages"]:
            assert set(message) == set(_MESSAGE_METADATA_FIELDS), (
                f"METADATA_ONLY returned unexpected fields: {sorted(message)}"
            )

    def test_get_thread_takes_no_filters(self) -> None:
        """get_thread returns every message in the thread, whatever a search showed."""
        searched = max(self._all_threads(), key=lambda t: len(t["messages"]))
        full = self._call_json("get_thread", thread_id=searched["id"])
        assert len(full["messages"]) >= len(searched["messages"]), (
            "get_thread returned fewer messages than search_threads did."
        )

    def test_get_thread_bad_id_returns_error(self) -> None:
        """get_thread returns an error dict for an unknown thread ID."""
        result = self._call_json("get_thread", thread_id=_BAD_THREAD_ID)
        assert isinstance(result, dict)
        assert "error" in result, (
            f"Expected 'error' key for unknown thread ID, got: {result}"
        )

    def test_get_thread_empty_id_returns_error(self) -> None:
        """get_thread returns an error dict when thread_id is empty."""
        result = self._call_json("get_thread", thread_id="")
        assert "error" in result, f"Expected 'error' key for empty thread_id: {result}"

    # ------------------------------------------------------------------
    # get_message
    # ------------------------------------------------------------------

    def test_get_message_returns_dict(self) -> None:
        """get_message returns a dict for a valid message ID."""
        result = self._call_json("get_message", message_id=self._first_message_id())
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"

    def test_get_message_correct_id(self) -> None:
        """get_message echoes the requested message ID."""
        message_id = self._first_message_id()
        result = self._call_json("get_message", message_id=message_id)
        assert result.get("id") == message_id, (
            f"Expected id={message_id!r}, got {result.get('id')!r}"
        )

    def test_get_message_metadata_fields(self) -> None:
        """get_message returns every metadata field."""
        result = self._call_json("get_message", message_id=self._first_message_id())
        for field in _MESSAGE_METADATA_FIELDS:
            assert field in result, f"Message missing required field '{field}'"

    def test_get_message_recipients_are_strings(self) -> None:
        """Recipient lists are arrays of bare email strings, not objects."""
        result = self._call_json("get_message", message_id=self._first_message_id())
        for field in ("toRecipients", "ccRecipients", "bccRecipients"):
            assert isinstance(result[field], list), f"{field} is not a list."
            for address in result[field]:
                assert isinstance(address, str), (
                    f"{field} contains a {type(address).__name__}, expected str: {address!r}"
                )
                assert "@" in address, f"{field} entry is not an address: {address!r}"

    def test_get_message_sender_is_a_string(self) -> None:
        """sender is a bare email string."""
        result = self._call_json("get_message", message_id=self._first_message_id())
        assert isinstance(result["sender"], str), "sender should be a string."
        assert "@" in result["sender"], f"sender is not an address: {result['sender']!r}"

    def test_get_message_full_content_has_one_body(self) -> None:
        """FULL_CONTENT includes exactly one of plaintextBody / htmlBody."""
        result = self._call_json("get_message", message_id=self._first_message_id())
        bodies = [k for k in ("plaintextBody", "htmlBody") if k in result]
        assert len(bodies) == 1, f"Expected one body field, got {bodies}"

    def test_get_message_full_content_has_attachment_fields(self) -> None:
        """FULL_CONTENT always includes attachmentIds and attachments."""
        result = self._call_json("get_message", message_id=self._first_message_id())
        assert "attachmentIds" in result, "FULL_CONTENT missing 'attachmentIds'."
        assert "attachments" in result, "FULL_CONTENT missing 'attachments'."
        assert len(result["attachmentIds"]) == len(result["attachments"]), (
            "attachmentIds and attachments disagree on how many attachments there are."
        )

    def test_get_message_attachment_metadata_shape(self) -> None:
        """Each attachment carries id, filename and mimeType."""
        result = self._call_json("search_threads", query="has:attachment", page_size=50)
        if not result["threads"]:
            pytest.skip("No messages with attachments in the fixture data.")
        for thread in result["threads"]:
            for message in thread["messages"]:
                full = self._call_json("get_message", message_id=message["id"])
                for attachment in full.get("attachments", []):
                    for field in ("id", "filename", "mimeType"):
                        assert field in attachment, (
                            f"Attachment on {message['id']} missing '{field}': {attachment}"
                        )

    def test_get_message_formats_are_nested(self) -> None:
        """Each format is a superset of the next-smaller one, field for field."""
        message_id = self._first_message_id()
        meta = self._call_json(
            "get_message", message_id=message_id, message_format="METADATA_ONLY"
        )
        minimal = self._call_json(
            "get_message", message_id=message_id, message_format="MINIMAL"
        )
        full = self._call_json(
            "get_message", message_id=message_id, message_format="FULL_CONTENT"
        )
        for field, value in meta.items():
            assert minimal.get(field) == value, f"MINIMAL changed '{field}'."
        for field, value in minimal.items():
            assert full.get(field) == value, f"FULL_CONTENT changed '{field}'."

    def test_get_message_unspecified_format_uses_default(self) -> None:
        """MESSAGE_FORMAT_UNSPECIFIED aliases to FULL_CONTENT rather than erroring."""
        message_id = self._first_message_id()
        unspecified = self._call_json(
            "get_message",
            message_id=message_id,
            message_format="MESSAGE_FORMAT_UNSPECIFIED",
        )
        default = self._call_json("get_message", message_id=message_id)
        assert unspecified == default, "_UNSPECIFIED should behave as FULL_CONTENT."

    def test_get_message_snippet_matches_search_result(self) -> None:
        """The snippet is stored, so get_message and search_threads agree on it."""
        thread = self._first_thread()
        message = thread["messages"][0]
        full = self._call_json("get_message", message_id=message["id"])
        assert full["snippet"] == message["snippet"], (
            "search_threads and get_message disagree on the snippet."
        )

    def test_get_message_label_ids_are_ids_not_names(self) -> None:
        """
        labelIds contains label IDs, never display names.

        A user label named "Verano" must serialize as its ID (e.g. "Label_1"); emitting
        the display name would break any agent that round-trips the value back into a
        label: filter by ID.
        """
        known_ids = {label["labelId"] for label in self._call_json("list_labels")["labels"]}
        for thread in self._all_threads():
            for message in thread["messages"]:
                unknown = set(message["labelIds"]) - known_ids
                assert not unknown, (
                    f"Message {message['id']} carries labelIds absent from list_labels: "
                    f"{sorted(unknown)} — display names leaking instead of IDs?"
                )

    def test_get_message_bad_id_returns_error(self) -> None:
        """get_message returns an error dict for an unknown message ID."""
        result = self._call_json("get_message", message_id=_BAD_MESSAGE_ID)
        assert isinstance(result, dict)
        assert "error" in result, (
            f"Expected 'error' key for unknown message ID, got: {result}"
        )

    def test_get_message_empty_id_returns_error(self) -> None:
        """get_message returns an error dict when message_id is empty."""
        result = self._call_json("get_message", message_id="")
        assert "error" in result, f"Expected 'error' key for empty message_id: {result}"

    # ------------------------------------------------------------------
    # list_labels
    # ------------------------------------------------------------------

    def test_list_labels_returns_envelope(self) -> None:
        """list_labels returns a dict with a 'labels' key."""
        result = self._call_json("list_labels")
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
        assert "labels" in result, "Missing 'labels' key in response."

    def test_list_labels_non_empty(self) -> None:
        """list_labels returns at least one label."""
        result = self._call_json("list_labels")
        assert result["labels"], "Expected at least one label."

    def test_list_labels_label_shape(self) -> None:
        """Each label has labelId, name, and all four counts."""
        required = {
            "labelId",
            "name",
            "messagesTotal",
            "messagesUnread",
            "threadsTotal",
            "threadsUnread",
        }
        for label in self._call_json("list_labels")["labels"]:
            missing = required - set(label)
            assert not missing, f"Label {label.get('name')} missing {missing}."

    def test_list_labels_uses_label_id_not_id(self) -> None:
        """The identifier field is 'labelId', matching the official schema."""
        label = self._call_json("list_labels")["labels"][0]
        assert "labelId" in label, f"Expected 'labelId', got keys {sorted(label)}"
        assert "id" not in label, "Label should not carry a bare 'id' field."

    def test_list_labels_counts_are_integers(self) -> None:
        """Label counts are JSON numbers, unlike resultCountEstimate."""
        for label in self._call_json("list_labels")["labels"]:
            for field in (
                "messagesTotal",
                "messagesUnread",
                "threadsTotal",
                "threadsUnread",
            ):
                assert isinstance(label[field], int), (
                    f"{label['name']}.{field} is {type(label[field]).__name__}, expected int"
                )

    def test_list_labels_counts_are_coherent(self) -> None:
        """Unread counts never exceed totals, and threads never exceed messages."""
        for label in self._call_json("list_labels")["labels"]:
            assert label["messagesUnread"] <= label["messagesTotal"], (
                f"{label['name']}: more unread messages than messages."
            )
            assert label["threadsUnread"] <= label["threadsTotal"], (
                f"{label['name']}: more unread threads than threads."
            )
            assert label["threadsTotal"] <= label["messagesTotal"], (
                f"{label['name']}: more threads than messages."
            )

    def test_list_labels_color_omitted_never_null(self) -> None:
        """The optional color field is absent on system labels, not present-and-null."""
        for label in self._call_json("list_labels")["labels"]:
            if "color" in label:
                assert label["color"] is not None, (
                    f"{label['name']} has color=null; it should be omitted instead."
                )
                for field in ("textColor", "backgroundColor"):
                    assert field in label["color"], (
                        f"{label['name']} color missing '{field}': {label['color']}"
                    )

    def test_list_labels_includes_system_labels(self) -> None:
        """System labels are listed alongside user labels."""
        ids = {label["labelId"] for label in self._call_json("list_labels")["labels"]}
        for system in ("INBOX", "SENT", "UNREAD", "STARRED", "IMPORTANT"):
            assert system in ids, f"Missing system label {system}. Got: {sorted(ids)}"

    def test_list_labels_names_are_unique(self) -> None:
        """No two labels share a display name, so label: is never ambiguous."""
        names = [label["name"] for label in self._call_json("list_labels")["labels"]]
        assert len(names) == len(set(names)), f"Duplicate label names: {sorted(names)}"

    def test_list_labels_label_count_matches_search(self) -> None:
        """A label's threadsTotal agrees with what label: returns for it."""
        for label in self._call_json("list_labels")["labels"]:
            if label["threadsTotal"] == 0:
                continue
            found = self._call_json(
                "search_threads",
                query=f"label:{label['labelId']} in:anywhere",
                page_size=50,
            )
            assert int(found["resultCountEstimate"]) == label["threadsTotal"], (
                f"{label['name']}: threadsTotal={label['threadsTotal']} but "
                f"label: search found {found['resultCountEstimate']}."
            )

    def test_list_labels_respects_page_size(self) -> None:
        """list_labels returns no more labels than page_size."""
        result = self._call_json("list_labels", page_size=2)
        assert len(result["labels"]) <= 2, (
            f"Expected at most 2 labels, got {len(result['labels'])}"
        )

    def test_list_labels_page_size_zero_returns_all(self) -> None:
        """page_size=0 (the default) returns every label."""
        assert len(self._call_json("list_labels", page_size=0)["labels"]) == len(
            self._call_json("list_labels")["labels"]
        )

    def test_list_labels_pagination_covers_every_label_once(self) -> None:
        """Walking nextPageToken yields every label exactly once."""
        expected = [label["labelId"] for label in self._call_json("list_labels")["labels"]]
        seen: list[str] = []
        token = ""
        for _ in range(100):
            page = self._call_json("list_labels", page_size=3, page_token=token)
            seen.extend(label["labelId"] for label in page["labels"])
            token = page.get("nextPageToken", "")
            if not token:
                break
        else:
            pytest.fail("Pagination did not terminate after 100 pages.")
        assert seen == expected, f"Paged labels {seen} != full listing {expected}"

    # ------------------------------------------------------------------
    # list_drafts
    # ------------------------------------------------------------------

    def test_list_drafts_returns_envelope(self) -> None:
        """list_drafts returns a dict with a 'drafts' list."""
        result = self._call_json("list_drafts")
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
        assert isinstance(result.get("drafts"), list), (
            f"Expected 'drafts' to be a list, got: {result}"
        )

    def test_list_drafts_returns_only_drafts(self) -> None:
        """
        Every ID list_drafts returns is a draft, and no non-draft appears.

        The complement of the check below: search_threads hides drafts, so between them
        the two tools partition the mailbox rather than overlapping.
        """
        draft_ids = {draft["id"] for draft in self._call_json("list_drafts")["drafts"]}
        searchable = {
            message["id"]
            for thread in self._all_threads()
            for message in thread["messages"]
        }
        assert not (draft_ids & searchable), (
            f"IDs in both list_drafts and search_threads: {sorted(draft_ids & searchable)}"
        )

    def test_list_drafts_draft_shape(self) -> None:
        """A Draft carries threadId — the field Message does not have."""
        drafts = self._call_json("list_drafts")["drafts"]
        if not drafts:
            pytest.skip("No drafts in the fixture data.")
        draft = drafts[0]
        for field in ("id", "threadId", "toRecipients", "ccRecipients", "bccRecipients"):
            assert field in draft, f"Draft missing '{field}'. Got: {sorted(draft)}"

    def test_list_drafts_full_view_has_subject_and_body(self) -> None:
        """The default DRAFT_VIEW_FULL populates subject and one body field."""
        drafts = self._call_json("list_drafts", view="DRAFT_VIEW_FULL")["drafts"]
        if not drafts:
            pytest.skip("No drafts in the fixture data.")
        draft = drafts[0]
        assert "subject" in draft, f"Full view omitted subject: {sorted(draft)}"
        assert "plaintextBody" in draft or "htmlBody" in draft, (
            f"Full view has no body field: {sorted(draft)}"
        )

    def test_list_drafts_metadata_view_omits_content(self) -> None:
        """DRAFT_VIEW_METADATA_ONLY excludes subject and body, keeping recipients."""
        drafts = self._call_json("list_drafts", view="DRAFT_VIEW_METADATA_ONLY")["drafts"]
        if not drafts:
            pytest.skip("No drafts in the fixture data.")
        draft = drafts[0]
        for excluded in ("subject", "plaintextBody", "htmlBody"):
            assert excluded not in draft, (
                f"Metadata-only view leaked '{excluded}': {sorted(draft)}"
            )
        assert "toRecipients" in draft, "Metadata-only view dropped recipients."

    def test_list_drafts_unrecognized_view_uses_default(self) -> None:
        """An unspecified or unknown view falls back to DRAFT_VIEW_FULL."""
        expected = self._call_json("list_drafts", view="DRAFT_VIEW_FULL")
        for view in ("DRAFT_VIEW_UNSPECIFIED", "NOT_A_VIEW"):
            assert self._call_json("list_drafts", view=view) == expected, (
                f"view={view} did not fall back to DRAFT_VIEW_FULL."
            )

    def test_list_drafts_query_filters(self) -> None:
        """A query narrows the draft list rather than being ignored."""
        everything = self._call_json("list_drafts")["drafts"]
        if not everything:
            pytest.skip("No drafts in the fixture data.")
        filtered = self._call_json("list_drafts", query=_NO_MATCH_QUERY)["drafts"]
        assert filtered == [], f"A no-match query still returned drafts: {filtered}"

    def test_list_drafts_no_match_is_not_an_error(self) -> None:
        """An empty draft list is a result, not an error."""
        result = self._call_json("list_drafts", query=_NO_MATCH_QUERY)
        assert "error" not in result, f"Empty result reported as an error: {result}"

    def test_list_drafts_unsupported_operator_is_reported(self) -> None:
        """list_drafts reports unanswerable operators, exactly as search_threads does."""
        result = self._call_json("list_drafts", query="larger:5M")
        assert "unsupportedOperators" in result, (
            f"'larger:' was silently dropped rather than reported: {result}"
        )

    def test_list_drafts_respects_page_size(self) -> None:
        """list_drafts returns no more drafts than page_size."""
        assert len(self._call_json("list_drafts", page_size=1)["drafts"]) <= 1

    # ------------------------------------------------------------------
    # create_draft
    # ------------------------------------------------------------------

    def test_create_draft_returns_a_full_draft(self) -> None:
        """
        create_draft returns a populated Draft, not just an ID.

        The tool's own description says it "returns only the unique ID", but its
        outputSchema is a full Draft.  The schema wins - see gmail-mcp-toolset-spec.md
        section 4.1, which flags the contradiction explicitly.
        """
        draft = self._call_json(
            "create_draft",
            to=["someone@example.com"],
            subject="Schema over prose",
            body="Body text.",
        )
        assert "error" not in draft, f"create_draft failed: {draft}"
        for field in ("id", "threadId", "subject", "toRecipients", "date"):
            assert field in draft, f"Draft missing '{field}'. Got: {sorted(draft)}"
        assert draft["subject"] == "Schema over prose", draft["subject"]
        assert draft["plaintextBody"] == "Body text.", draft["plaintextBody"]

    def test_create_draft_is_findable_by_list_drafts(self) -> None:
        """A created draft shows up in list_drafts, which is how an agent retrieves it."""
        before = len(self._call_json("list_drafts", page_size=50)["drafts"])
        created = self._call_json("create_draft", subject="Findable", body="x")
        after = self._call_json("list_drafts", page_size=50)["drafts"]
        assert len(after) == before + 1, (
            f"Draft count went {before} -> {len(after)}, expected +1."
        )
        assert created["id"] in {draft["id"] for draft in after}, (
            f"{created['id']} not in list_drafts."
        )

    def test_create_draft_is_hidden_from_search_threads(self) -> None:
        """
        A new draft does not appear in search_threads.

        DRAFT is one of the default-excluded labels, so a draft is invisible to search
        even when it lands in a thread search_threads does return.  Gmail behaves the
        same way: drafts live in their own view.
        """
        created = self._call_json("create_draft", subject="Hidden", body="x")
        visible = {
            message["id"]
            for thread in self._all_threads()
            for message in thread["messages"]
        }
        assert created["id"] not in visible, (
            f"Draft {created['id']} leaked into search_threads."
        )

    def test_create_draft_is_unread_and_labeled_draft(self) -> None:
        """A new draft carries the DRAFT label and counts as unread."""
        created = self._call_json("create_draft", subject="Label check", body="x")
        labels = self._labels_of(created["id"])
        assert "DRAFT" in labels, f"Expected DRAFT in {labels}."
        assert "UNREAD" in labels, f"Expected UNREAD in {labels}."

    def test_create_draft_recipients_round_trip(self) -> None:
        """to/cc/bcc come back as the plain address strings they went in as."""
        draft = self._call_json(
            "create_draft",
            to=["to@example.com"],
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            subject="Recipients",
        )
        assert draft["toRecipients"] == ["to@example.com"], draft["toRecipients"]
        assert draft["ccRecipients"] == ["cc@example.com"], draft["ccRecipients"]
        assert draft["bccRecipients"] == ["bcc@example.com"], draft["bccRecipients"]

    def test_create_draft_html_only_body_is_returned_as_html(self) -> None:
        """With no plain-text body, the HTML body is stored and returned as htmlBody."""
        draft = self._call_json(
            "create_draft", subject="Rich", html_body="<p>Rich text.</p>"
        )
        assert draft.get("htmlBody") == "<p>Rich text.</p>", (
            f"Expected htmlBody, got: {sorted(draft)}"
        )
        assert "plaintextBody" not in draft, "Both body fields were populated."

    def test_create_draft_prefers_plaintext_when_both_given(self) -> None:
        """
        Given both bodies, the plain-text one is stored.

        The schema has one body with one content_type, so a draft carrying both has to
        pick, and create_draft documents ``body`` as the plain-text alternative - the
        part that always renders.
        """
        draft = self._call_json(
            "create_draft", subject="Both", body="Plain.", html_body="<p>Rich.</p>"
        )
        assert draft.get("plaintextBody") == "Plain.", f"Got: {sorted(draft)}"
        assert "htmlBody" not in draft, "HTML body won over plain text."

    def test_create_draft_date_is_the_reproducible_now(self) -> None:
        """
        A draft is dated from the pinned "now", not the wall clock.

        The same constant ``newer_than:`` resolves against.  A wall-clock date would put
        every draft years after the corpus and make relative date queries behave
        differently depending on when the benchmark ran.
        """
        draft = self._call_json("create_draft", subject="When", body="x")
        newest = max(
            message["date"]
            for thread in self._all_threads()
            for message in thread["messages"]
        )
        assert draft["date"] <= newest, (
            f"Draft dated {draft['date']}, after the newest message {newest} - "
            "the wall clock leaked in."
        )

    def test_create_draft_reply_joins_the_parent_thread(self) -> None:
        """A reply draft lands in its parent's thread and inherits the subject."""
        thread = self._first_thread()
        parent_id = thread["messages"][-1]["id"]
        parent = self._call_json("get_message", message_id=parent_id)
        draft = self._call_json(
            "create_draft", to=["a@example.com"], body="Reply.", reply_to_message_id=parent_id
        )
        assert draft["threadId"] == thread["id"], (
            f"Reply went to thread {draft['threadId']}, expected {thread['id']}."
        )
        assert draft["subject"] == parent["subject"], (
            f"Reply subject {draft['subject']!r} != parent {parent['subject']!r}."
        )

    def test_create_draft_reply_appends_to_the_parent_body(self) -> None:
        """
        A reply's body is the parent's body with the new text appended.

        Unusual, but it is what create_draft documents, and it is how a reply that quotes
        what it is replying to behaves.
        """
        thread = self._first_thread()
        parent_id = thread["messages"][-1]["id"]
        parent = self._call_json("get_message", message_id=parent_id)
        parent_body = parent.get("plaintextBody") or parent.get("htmlBody") or ""
        draft = self._call_json(
            "create_draft", body="Appended text.", reply_to_message_id=parent_id
        )
        body = draft.get("plaintextBody") or draft.get("htmlBody") or ""
        assert body.startswith(parent_body), "Reply body does not open with the parent's."
        assert body.endswith("Appended text."), "Reply body does not end with the new text."

    def test_create_draft_reply_is_visible_in_the_thread(self) -> None:
        """
        get_thread shows a reply draft in context with what it replies to.

        get_thread takes no query, so the default label exclusions that hide the draft
        from search_threads do not apply here - which is the point: an agent that drafted
        a reply can read the conversation back and see it in place.
        """
        thread = self._first_thread()
        draft = self._call_json(
            "create_draft", body="In context.", reply_to_message_id=thread["messages"][0]["id"]
        )
        fetched = self._call_json("get_thread", thread_id=thread["id"])
        assert draft["id"] in {message["id"] for message in fetched["messages"]}, (
            f"Draft {draft['id']} missing from thread {thread['id']}."
        )

    def test_create_draft_unknown_reply_target_returns_error(self) -> None:
        """Replying to a message that does not exist is an error, not a new thread."""
        result = self._call_json("create_draft", reply_to_message_id=_BAD_MESSAGE_ID)
        assert "error" in result, f"Expected an error for a bad parent, got: {result}"

    def test_create_draft_unknown_reply_target_creates_nothing(self) -> None:
        """The failed reply above leaves the draft count untouched."""
        before = len(self._call_json("list_drafts", page_size=50)["drafts"])
        self._call_json("create_draft", reply_to_message_id=_BAD_MESSAGE_ID)
        after = len(self._call_json("list_drafts", page_size=50)["drafts"])
        assert after == before, f"A rejected create still added a draft ({before} -> {after})."

    def test_create_draft_no_arguments_is_legal(self) -> None:
        """create_draft has no required fields, so an empty draft is valid."""
        draft = self._call_json("create_draft")
        assert "error" not in draft, f"An empty draft was rejected: {draft}"
        assert draft["id"], "An empty draft got no ID."

    def test_create_draft_attachment_metadata_is_retained(self) -> None:
        """
        An attachment's filename and mimeType survive, and its size is the decoded size.

        The description calls attachments "not supported yet" while inputSchema fully
        defines them; the schema wins.  The bytes themselves are weighed and dropped -
        there is no column for content, and no tool in this toolset downloads one.
        """
        import base64

        payload = base64.b64encode(b"z" * 321).decode()
        draft = self._call_json(
            "create_draft",
            subject="With attachment",
            body="See attached.",
            attachments=[
                {
                    "content": payload,
                    "filename": "notes.pdf",
                    "mimeType": "application/pdf",
                }
            ],
        )
        assert "error" not in draft, f"create_draft with an attachment failed: {draft}"
        message = self._call_json("get_message", message_id=draft["id"])
        names = [attachment["filename"] for attachment in message["attachments"]]
        assert names == ["notes.pdf"], f"Expected ['notes.pdf'], got {names}"
        types = [attachment["mimeType"] for attachment in message["attachments"]]
        assert types == ["application/pdf"], f"Expected the given mimeType, got {types}"
        # attachmentIds and attachments describe the same attachments, in the same order:
        # the schema carries both, and the loader asserts the two agree on the dataset's
        # own records.  A created draft has to satisfy the same invariant.
        assert message["attachmentIds"] == [
            attachment["id"] for attachment in message["attachments"]
        ], f"attachmentIds disagrees with attachments: {message['attachmentIds']}"

    def test_create_draft_attachment_is_findable_by_filename(self) -> None:
        """filename: finds a draft by its attachment, so the index was updated too."""
        import base64

        payload = base64.b64encode(b"q" * 64).decode()
        draft = self._call_json(
            "create_draft",
            subject="Indexed attachment",
            attachments=[
                {"content": payload, "filename": "unique-invoice-9931.pdf"},
            ],
        )
        found = self._call_json("list_drafts", query="filename:unique-invoice-9931.pdf")
        assert draft["id"] in {d["id"] for d in found["drafts"]}, (
            f"filename: did not find {draft['id']}: {found}"
        )

    def test_create_draft_rejects_invalid_base64(self) -> None:
        """An attachment whose content is not base64 is an error, not a zero-byte file."""
        result = self._call_json(
            "create_draft",
            attachments=[{"content": "not!valid!base64!", "filename": "x.bin"}],
        )
        assert "error" in result, f"Invalid base64 was accepted: {result}"

    def test_create_draft_rejects_oversized_attachments(self) -> None:
        """Combined attachment size above 25MB is rejected, per inputSchema."""
        import base64

        payload = base64.b64encode(b"y" * (26 * 1024 * 1024)).decode()
        result = self._call_json(
            "create_draft", attachments=[{"content": payload, "filename": "big.bin"}]
        )
        assert "error" in result, f"A 26MB attachment was accepted: {result}"

    def test_create_draft_rejected_attachment_creates_nothing(self) -> None:
        """
        A rejected attachment leaves no draft behind.

        Attachments are validated before the insert, so there is no half-created draft
        with a message row and no attachment rows - which is exactly the state the load
        assertions reject.
        """
        before = len(self._call_json("list_drafts", page_size=50)["drafts"])
        self._call_json(
            "create_draft", attachments=[{"content": "!!!", "filename": "x.bin"}]
        )
        after = len(self._call_json("list_drafts", page_size=50)["drafts"])
        assert after == before, f"A rejected create still added a draft ({before} -> {after})."

    def test_create_draft_ids_are_unique(self) -> None:
        """Two creates get two IDs, and neither collides with a dataset message."""
        first = self._call_json("create_draft", subject="One")
        second = self._call_json("create_draft", subject="Two")
        assert first["id"] != second["id"], f"Both creates returned {first['id']}."
        for draft_id in (first["id"], second["id"]):
            fetched = self._call_json("get_message", message_id=draft_id)
            assert fetched["id"] == draft_id, f"{draft_id} does not resolve."

    # ------------------------------------------------------------------
    # label_thread / unlabel_thread
    # ------------------------------------------------------------------

    def test_label_thread_returns_empty_object(self) -> None:
        """The label tools return {} — no fields, per the response schema."""
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        try:
            result = self._call_json("label_thread", thread_id=thread["id"], label_ids=[label_id])
            assert result == {}, f"Expected {{}}, got: {result}"
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=[label_id])

    def test_label_thread_labels_every_message(self) -> None:
        """
        Labeling a thread affects all of its messages, not just the first.

        "Affects all messages currently in the thread" is the documented behavior, and it
        is why the tool exists alongside label_message.
        """
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        try:
            self._call_json("label_thread", thread_id=thread["id"], label_ids=[label_id])
            for message in thread["messages"]:
                labels = self._labels_of(message["id"])
                assert label_id in labels, (
                    f"{message['id']} in the labeled thread has {labels}."
                )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=[label_id])

    def test_label_thread_is_findable_by_label_query(self) -> None:
        """A newly labeled thread is returned by label: — the index agrees with the write."""
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        try:
            self._call_json("label_thread", thread_id=thread["id"], label_ids=[label_id])
            found = self._call_json(
                "search_threads", query=f"label:{label_id}", page_size=50
            )
            assert thread["id"] in {t["id"] for t in found["threads"]}, (
                f"label:{label_id} did not return the thread just labeled with it."
            )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=[label_id])

    def test_label_thread_is_idempotent(self) -> None:
        """
        Labeling twice leaves one label, not two.

        idempotentHint is true, and messages.labels is a JSON array with no uniqueness
        constraint of its own, so the guard has to be in the write.
        """
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        message_id = thread["messages"][0]["id"]
        try:
            self._call_json("label_thread", thread_id=thread["id"], label_ids=[label_id])
            once = self._labels_of(message_id)
            self._call_json("label_thread", thread_id=thread["id"], label_ids=[label_id])
            assert self._labels_of(message_id) == once, (
                f"A repeated label_thread changed the labels: {once} -> "
                f"{self._labels_of(message_id)}"
            )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=[label_id])

    def test_unlabel_thread_removes_from_every_message(self) -> None:
        """unlabel_thread is the inverse of label_thread across the whole thread."""
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        self._call_json("label_thread", thread_id=thread["id"], label_ids=[label_id])
        self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=[label_id])
        for message in thread["messages"]:
            labels = self._labels_of(message["id"])
            assert label_id not in labels, f"{message['id']} still carries {label_id}: {labels}"

    def test_unlabel_thread_preserves_other_labels(self) -> None:
        """
        Removing one label leaves the rest untouched, in their original order.

        Order matters because ``labels`` is an authored JSON array, not a set: removal
        rebuilds it, and a rebuild that scrambled the order would silently rewrite the
        fixture's own data.
        """
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        message_id = thread["messages"][0]["id"]
        before = self._labels_of(message_id)
        self._call_json("label_thread", thread_id=thread["id"], label_ids=[label_id])
        self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=[label_id])
        assert self._labels_of(message_id) == before, (
            f"Round trip changed the labels: {before} -> {self._labels_of(message_id)}"
        )

    def test_unlabel_thread_is_idempotent(self) -> None:
        """Removing a label the thread does not have is a no-op, not an error."""
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        message_id = thread["messages"][0]["id"]
        before = self._labels_of(message_id)
        result = self._call_json(
            "unlabel_thread", thread_id=thread["id"], label_ids=[label_id]
        )
        assert result == {}, f"Expected {{}}, got: {result}"
        assert self._labels_of(message_id) == before, "A no-op unlabel changed the labels."

    def test_label_thread_rejects_sensitive_labels(self) -> None:
        """
        TRASH and SPAM are rejected here and redirected to apply_sensitive_thread_label.

        The asymmetry is the real toolset's: label_thread refuses them, unlabel_thread
        accepts them.  Adding TRASH without also removing INBOX would leave a thread that
        is both trashed and in the inbox, which is why there is a separate tool.
        """
        thread = self._first_thread()
        for sensitive in ("TRASH", "SPAM"):
            result = self._call_json(
                "label_thread", thread_id=thread["id"], label_ids=[sensitive]
            )
            assert "error" in result, f"label_thread accepted {sensitive}: {result}"

    def test_label_thread_unknown_label_returns_error(self) -> None:
        """An unknown label ID is an error, so a typo cannot silently do nothing."""
        thread = self._first_thread()
        result = self._call_json(
            "label_thread", thread_id=thread["id"], label_ids=["Label_no_such_9999"]
        )
        assert "error" in result, f"Expected an error for an unknown label: {result}"

    def test_label_thread_accepts_label_names_too(self) -> None:
        """
        A display name works where an ID is documented.

        The real tool says "accepts label_ids and not label names".  Accepting both is a
        deliberate superset: an agent that passes the name it read from list_labels gets
        the obvious behavior instead of a puzzling error, and nothing that passes an ID
        breaks.
        """
        label_id = self._user_label_id()
        name = next(
            label["name"]
            for label in self._call_json("list_labels", page_size=0)["labels"]
            if label["labelId"] == label_id
        )
        thread = self._unlabeled_thread(label_id)
        try:
            result = self._call_json("label_thread", thread_id=thread["id"], label_ids=[name])
            assert result == {}, f"A label name was rejected: {result}"
            assert label_id in self._labels_of(thread["messages"][0]["id"]), (
                f"Labeling by name {name!r} did not apply {label_id}."
            )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=[label_id])

    def test_label_thread_unknown_thread_returns_error(self) -> None:
        """Labeling a thread that does not exist is an error, not a silent no-op."""
        result = self._call_json(
            "label_thread", thread_id=_BAD_THREAD_ID, label_ids=[self._user_label_id()]
        )
        assert "error" in result, f"Expected an error for an unknown thread: {result}"

    def test_label_thread_empty_label_list_returns_error(self) -> None:
        """labelIds is required, so an empty list is a mistake worth reporting."""
        result = self._call_json(
            "label_thread", thread_id=self._first_thread()["id"], label_ids=[]
        )
        assert "error" in result, f"An empty labelIds list was accepted: {result}"

    def test_label_thread_rejects_all_or_nothing(self) -> None:
        """
        One bad label ID in a list applies none of them.

        Validation happens before the first write, so a partially-applied batch cannot
        leave the agent guessing which half landed.
        """
        label_id = self._user_label_id()
        thread = self._unlabeled_thread(label_id)
        message_id = thread["messages"][0]["id"]
        before = self._labels_of(message_id)
        result = self._call_json(
            "label_thread",
            thread_id=thread["id"],
            label_ids=[label_id, "Label_no_such_9999"],
        )
        assert "error" in result, f"A batch with a bad ID was accepted: {result}"
        assert self._labels_of(message_id) == before, (
            f"A rejected batch still applied a label: {before} -> {self._labels_of(message_id)}"
        )

    # ------------------------------------------------------------------
    # label_message / unlabel_message
    # ------------------------------------------------------------------

    def test_label_message_labels_only_that_message(self) -> None:
        """
        label_message changes one message; its thread siblings are untouched.

        The distinction from label_thread, and the reason both exist.
        """
        label_id = self._user_label_id()
        thread = next(
            (t for t in self._all_threads() if len(t["messages"]) > 1),
            None,
        )
        if thread is None:
            pytest.skip("No multi-message thread in the fixture data.")
        target, *siblings = thread["messages"]
        if label_id in self._labels_of(target["id"]):
            pytest.skip(f"{target['id']} already carries {label_id}.")
        try:
            self._call_json("label_message", message_id=target["id"], label_ids=[label_id])
            assert label_id in self._labels_of(target["id"]), "The target was not labeled."
            for sibling in siblings:
                assert label_id not in self._labels_of(sibling["id"]), (
                    f"label_message also labeled {sibling['id']}."
                )
        finally:
            self._call_json("unlabel_message", message_id=target["id"], label_ids=[label_id])

    def test_label_message_returns_empty_object(self) -> None:
        """label_message returns {}, like every write tool but create_label."""
        label_id = self._user_label_id()
        message_id = self._first_message_id()
        try:
            result = self._call_json(
                "label_message", message_id=message_id, label_ids=[label_id]
            )
            assert result == {}, f"Expected {{}}, got: {result}"
        finally:
            self._call_json("unlabel_message", message_id=message_id, label_ids=[label_id])

    def test_label_message_derived_label_updates_the_column(self) -> None:
        """
        UNREAD is a projection of is_read, so labeling with it flips that column.

        Derived labels are never stored in ``labels``; writing one has to change the
        column it is computed from, or list_labels and is:unread would disagree with it.
        """
        message_id = next(
            (
                message["id"]
                for thread in self._all_threads()
                for message in thread["messages"]
                if "UNREAD" not in self._labels_of(message["id"])
            ),
            None,
        )
        if message_id is None:
            pytest.skip("Every message is already unread.")
        try:
            self._call_json("label_message", message_id=message_id, label_ids=["UNREAD"])
            assert "UNREAD" in self._labels_of(message_id), "UNREAD was not applied."
            found = self._call_json("search_threads", query="is:unread", page_size=50)
            assert message_id in {
                m["id"] for t in found["threads"] for m in t["messages"]
            }, "is:unread does not agree with the UNREAD label just applied."
        finally:
            self._call_json("unlabel_message", message_id=message_id, label_ids=["UNREAD"])

    def test_label_message_starred_round_trips(self) -> None:
        """STARRED is derived from flag.status and survives an add/remove round trip."""
        message_id = next(
            (
                message["id"]
                for thread in self._all_threads()
                for message in thread["messages"]
                if "STARRED" not in self._labels_of(message["id"])
            ),
            None,
        )
        if message_id is None:
            pytest.skip("Every message is already starred.")
        before = self._labels_of(message_id)
        self._call_json("label_message", message_id=message_id, label_ids=["STARRED"])
        assert "STARRED" in self._labels_of(message_id), "STARRED was not applied."
        self._call_json("unlabel_message", message_id=message_id, label_ids=["STARRED"])
        assert self._labels_of(message_id) == before, (
            f"Round trip changed the labels: {before} -> {self._labels_of(message_id)}"
        )

    def test_unlabel_message_removes_the_label(self) -> None:
        """unlabel_message is the inverse of label_message on one message."""
        label_id = self._user_label_id()
        message_id = self._first_message_id()
        before = self._labels_of(message_id)
        self._call_json("label_message", message_id=message_id, label_ids=[label_id])
        self._call_json("unlabel_message", message_id=message_id, label_ids=[label_id])
        assert self._labels_of(message_id) == before, (
            f"Round trip changed the labels: {before} -> {self._labels_of(message_id)}"
        )

    def test_label_message_rejects_sensitive_labels(self) -> None:
        """TRASH and SPAM route to apply_sensitive_message_label, as on the thread tool."""
        message_id = self._first_message_id()
        for sensitive in ("TRASH", "SPAM"):
            result = self._call_json(
                "label_message", message_id=message_id, label_ids=[sensitive]
            )
            assert "error" in result, f"label_message accepted {sensitive}: {result}"

    def test_label_message_unknown_message_returns_error(self) -> None:
        """Labeling a message that does not exist is an error."""
        result = self._call_json(
            "label_message", message_id=_BAD_MESSAGE_ID, label_ids=[self._user_label_id()]
        )
        assert "error" in result, f"Expected an error for an unknown message: {result}"

    def test_unlabel_message_accepts_sensitive_labels(self) -> None:
        """
        unlabel_message accepts TRASH and SPAM, unlike label_message.

        Not a leniency bug: the real toolset's unlabel tools list them among the system
        labels they accept, because taking a message *out* of Trash needs no companion
        change the way putting one in does.
        """
        message_id = self._first_message_id()
        before = self._labels_of(message_id)
        result = self._call_json(
            "unlabel_message", message_id=message_id, label_ids=["TRASH"]
        )
        assert result == {}, f"unlabel_message rejected TRASH: {result}"
        assert self._labels_of(message_id) == before, (
            "Removing an absent TRASH label changed something."
        )

    # ------------------------------------------------------------------
    # apply_sensitive_thread_label / apply_sensitive_message_label
    # ------------------------------------------------------------------

    def test_apply_sensitive_thread_label_trashes_the_thread(self) -> None:
        """
        TRASH removes the thread from the default search and adds the label.

        This is the tool's whole purpose, and it is why it exists separately from
        label_thread: the move is two changes, not one.
        """
        thread = self._unlabeled_thread("TRASH")
        try:
            result = self._call_json(
                "apply_sensitive_thread_label", thread_id=thread["id"], label_option="TRASH"
            )
            assert result == {}, f"Expected {{}}, got: {result}"
            visible = {t["id"] for t in self._all_threads()}
            assert thread["id"] not in visible, (
                f"Trashed thread {thread['id']} is still in the default search."
            )
            assert "TRASH" in self._labels_of(thread["messages"][0]["id"]), (
                "TRASH was not applied to the thread's messages."
            )
        finally:
            self._call_json(
                "unlabel_thread", thread_id=thread["id"], label_ids=["TRASH"]
            )
            self._call_json("label_thread", thread_id=thread["id"], label_ids=["INBOX"])

    def test_apply_sensitive_thread_label_removes_inbox(self) -> None:
        """
        Trashing takes the thread out of the inbox as well as into the trash.

        Without the removal the thread would answer both ``in:inbox`` and ``in:trash``,
        which is not a state Gmail can be in.
        """
        thread = self._unlabeled_thread("TRASH")
        message_id = thread["messages"][0]["id"]
        if "INBOX" not in self._labels_of(message_id):
            pytest.skip(f"{message_id} is not in the inbox to begin with.")
        try:
            self._call_json(
                "apply_sensitive_thread_label", thread_id=thread["id"], label_option="TRASH"
            )
            assert "INBOX" not in self._labels_of(message_id), (
                f"{message_id} is both trashed and in the inbox: {self._labels_of(message_id)}"
            )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=["TRASH"])
            self._call_json("label_thread", thread_id=thread["id"], label_ids=["INBOX"])

    def test_apply_sensitive_thread_label_is_reachable_by_in_trash(self) -> None:
        """A trashed thread is still findable — hidden by default, not deleted."""
        thread = self._unlabeled_thread("TRASH")
        try:
            self._call_json(
                "apply_sensitive_thread_label", thread_id=thread["id"], label_option="TRASH"
            )
            found = self._call_json("search_threads", query="in:trash", page_size=50)
            assert thread["id"] in {t["id"] for t in found["threads"]}, (
                f"in:trash did not return the thread just trashed: {found}"
            )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=["TRASH"])
            self._call_json("label_thread", thread_id=thread["id"], label_ids=["INBOX"])

    def test_apply_sensitive_thread_label_spam_is_excluded_by_default(self) -> None:
        """SPAM hides the thread from the default search, the same as TRASH does."""
        thread = self._unlabeled_thread("SPAM")
        try:
            self._call_json(
                "apply_sensitive_thread_label", thread_id=thread["id"], label_option="SPAM"
            )
            assert thread["id"] not in {t["id"] for t in self._all_threads()}, (
                f"Spammed thread {thread['id']} is still in the default search."
            )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=["SPAM"])
            self._call_json("label_thread", thread_id=thread["id"], label_ids=["INBOX"])

    def test_apply_sensitive_thread_label_is_idempotent(self) -> None:
        """Trashing an already-trashed thread changes nothing further."""
        thread = self._unlabeled_thread("TRASH")
        message_id = thread["messages"][0]["id"]
        try:
            self._call_json(
                "apply_sensitive_thread_label", thread_id=thread["id"], label_option="TRASH"
            )
            once = self._labels_of(message_id)
            self._call_json(
                "apply_sensitive_thread_label", thread_id=thread["id"], label_option="TRASH"
            )
            assert self._labels_of(message_id) == once, (
                f"A repeated trash changed the labels: {once} -> {self._labels_of(message_id)}"
            )
        finally:
            self._call_json("unlabel_thread", thread_id=thread["id"], label_ids=["TRASH"])
            self._call_json("label_thread", thread_id=thread["id"], label_ids=["INBOX"])

    def test_apply_sensitive_thread_label_rejects_other_labels(self) -> None:
        """
        Only TRASH and SPAM are valid; there is no default to coerce to.

        Unlike the view enums, LabelOption's whole job is to say which of the two, so an
        unrecognized value is an error rather than something to fall back from.
        """
        thread = self._first_thread()
        for bad in ("ARCHIVE", "INBOX", ""):
            result = self._call_json(
                "apply_sensitive_thread_label", thread_id=thread["id"], label_option=bad
            )
            assert "error" in result, f"labelOption={bad!r} was accepted: {result}"

    def test_apply_sensitive_thread_label_unknown_thread_returns_error(self) -> None:
        """Trashing a thread that does not exist is an error."""
        result = self._call_json(
            "apply_sensitive_thread_label", thread_id=_BAD_THREAD_ID, label_option="TRASH"
        )
        assert "error" in result, f"Expected an error for an unknown thread: {result}"

    def test_apply_sensitive_message_label_trashes_one_message(self) -> None:
        """
        The message-scoped version trashes one message and leaves its siblings alone.

        Its thread stays visible, because the thread still holds live messages - which is
        the two-phase search working as documented.
        """
        thread = next((t for t in self._all_threads() if len(t["messages"]) > 1), None)
        if thread is None:
            pytest.skip("No multi-message thread in the fixture data.")
        target, sibling = thread["messages"][0], thread["messages"][1]
        if "TRASH" in self._labels_of(target["id"]):
            pytest.skip(f"{target['id']} is already trashed.")
        try:
            self._call_json(
                "apply_sensitive_message_label",
                message_id=target["id"],
                label_option="TRASH",
            )
            assert "TRASH" in self._labels_of(target["id"]), "TRASH was not applied."
            assert "TRASH" not in self._labels_of(sibling["id"]), (
                f"Trashing {target['id']} also trashed {sibling['id']}."
            )
        finally:
            self._call_json(
                "unlabel_message", message_id=target["id"], label_ids=["TRASH"]
            )
            self._call_json("label_message", message_id=target["id"], label_ids=["INBOX"])

    def test_apply_sensitive_message_label_hides_it_from_search(self) -> None:
        """
        A trashed message drops out of its thread's search results.

        Label exclusions are eligibility *and* filtering: without re-applying them in
        phase two, a trashed message sharing a thread with a live one would still be
        visible by default.
        """
        thread = next((t for t in self._all_threads() if len(t["messages"]) > 1), None)
        if thread is None:
            pytest.skip("No multi-message thread in the fixture data.")
        target = thread["messages"][0]
        if "TRASH" in self._labels_of(target["id"]):
            pytest.skip(f"{target['id']} is already trashed.")
        try:
            self._call_json(
                "apply_sensitive_message_label",
                message_id=target["id"],
                label_option="TRASH",
            )
            visible = {
                message["id"]
                for found in self._all_threads()
                if found["id"] == thread["id"]
                for message in found["messages"]
            }
            assert target["id"] not in visible, (
                f"Trashed message {target['id']} is still in its thread's results."
            )
        finally:
            self._call_json(
                "unlabel_message", message_id=target["id"], label_ids=["TRASH"]
            )
            self._call_json("label_message", message_id=target["id"], label_ids=["INBOX"])

    def test_apply_sensitive_message_label_rejects_other_labels(self) -> None:
        """Only TRASH and SPAM, same as the thread-scoped tool."""
        result = self._call_json(
            "apply_sensitive_message_label",
            message_id=self._first_message_id(),
            label_option="ARCHIVE",
        )
        assert "error" in result, f"labelOption='ARCHIVE' was accepted: {result}"

    def test_apply_sensitive_message_label_unknown_message_returns_error(self) -> None:
        """Trashing a message that does not exist is an error."""
        result = self._call_json(
            "apply_sensitive_message_label",
            message_id=_BAD_MESSAGE_ID,
            label_option="TRASH",
        )
        assert "error" in result, f"Expected an error for an unknown message: {result}"

    # ------------------------------------------------------------------
    # create_label
    # ------------------------------------------------------------------

    def test_create_label_returns_a_populated_label(self) -> None:
        """
        create_label is the one write tool that returns a body rather than {}.

        It has to: the caller needs the allocated ID before it can label anything.
        """
        name = self._unique_name("Test-Label")
        label = self._call_json("create_label", display_name=name)
        assert "error" not in label, f"create_label failed: {label}"
        assert label["name"] == name, f"Expected {name!r}, got {label['name']!r}"
        assert label["labelId"], "create_label returned no labelId."
        for count in ("messagesTotal", "messagesUnread", "threadsTotal", "threadsUnread"):
            assert label[count] == 0, f"A new label reports {count}={label[count]}."

    def test_create_label_appears_in_list_labels(self) -> None:
        """The new label is listed, and the listing grew by exactly one."""
        before = self._call_json("list_labels", page_size=0)["labels"]
        created = self._call_json("create_label", display_name=self._unique_name("Listed"))
        after = self._call_json("list_labels", page_size=0)["labels"]
        assert len(after) == len(before) + 1, (
            f"Label count went {len(before)} -> {len(after)}, expected +1."
        )
        assert created["labelId"] in {label["labelId"] for label in after}, (
            f"{created['labelId']} is missing from list_labels."
        )

    def test_create_label_is_usable_immediately(self) -> None:
        """
        A brand-new label can be applied and then searched for.

        The end-to-end reason create_label exists, and the check that nothing caches the
        name-to-ID mapping from before the label existed.
        """
        created = self._call_json("create_label", display_name=self._unique_name("Usable"))
        thread = self._first_thread()
        self._call_json(
            "label_thread", thread_id=thread["id"], label_ids=[created["labelId"]]
        )
        try:
            found = self._call_json(
                "search_threads", query=f"label:{created['name']}", page_size=50
            )
            assert thread["id"] in {t["id"] for t in found["threads"]}, (
                f"label:{created['name']} did not find the thread just labeled: {found}"
            )
        finally:
            self._call_json(
                "unlabel_thread", thread_id=thread["id"], label_ids=[created["labelId"]]
            )

    def test_create_label_stores_the_color(self) -> None:
        """A color comes back in the wire shape it went in as."""
        label = self._call_json(
            "create_label",
            display_name=self._unique_name("Colored"),
            color={"textColor": "#ffffff", "backgroundColor": "#cc3a21"},
        )
        assert label.get("color") == {
            "textColor": "#ffffff",
            "backgroundColor": "#cc3a21",
        }, f"Color did not round-trip: {label.get('color')}"

    def test_create_label_without_color_omits_the_field(self) -> None:
        """No color means the field is absent, never present-and-null."""
        label = self._call_json("create_label", display_name=self._unique_name("Plain"))
        assert "color" not in label, f"Expected no color key, got: {label.get('color')!r}"

    def test_create_label_creates_missing_parents(self) -> None:
        """
        A nested name creates each ancestor level, shallowest first.

        Nesting is a naming convention rather than a parent column - Gmail stores the
        whole path as the display name - so the ancestors are separate labels that happen
        to be prefixes.
        """
        stem = self._unique_name("Tree")
        leaf = f"{stem}/Middle/Leaf"
        created = self._call_json("create_label", display_name=leaf)
        assert "error" not in created, f"create_label failed: {created}"
        names = {label["name"] for label in self._call_json("list_labels", page_size=0)["labels"]}
        for expected in (stem, f"{stem}/Middle", leaf):
            assert expected in names, f"Ancestor {expected!r} was not created."

    def test_create_label_parents_get_no_color(self) -> None:
        """The requested color belongs to the requested label, not to its ancestors."""
        stem = self._unique_name("Colored-Tree")
        self._call_json(
            "create_label",
            display_name=f"{stem}/Child",
            color={"textColor": "#000000", "backgroundColor": "#fad165"},
        )
        by_name = {
            label["name"]: label
            for label in self._call_json("list_labels", page_size=0)["labels"]
        }
        assert "color" not in by_name[stem], (
            f"Auto-created parent {stem!r} inherited a color: {by_name[stem].get('color')}"
        )
        assert "color" in by_name[f"{stem}/Child"], "The requested label lost its color."

    def test_create_label_without_auto_create_rejects_missing_parents(self) -> None:
        """auto_create_parent_labels=False turns a missing ancestor into an error."""
        result = self._call_json(
            "create_label",
            display_name=f"{self._unique_name('No-Auto')}/Child",
            auto_create_parent_labels=False,
        )
        assert "error" in result, f"A missing parent was auto-created anyway: {result}"

    def test_create_label_without_auto_create_accepts_existing_parents(self) -> None:
        """With the parent already there, auto_create_parent_labels=False is fine."""
        parent = self._unique_name("Has-Parent")
        self._call_json("create_label", display_name=parent)
        result = self._call_json(
            "create_label",
            display_name=f"{parent}/Child",
            auto_create_parent_labels=False,
        )
        assert "error" not in result, f"An existing parent was rejected: {result}"

    def test_create_label_rejects_duplicates(self) -> None:
        """
        A duplicate name is an error, not a second label with the same name.

        list_labels' names have to stay unique or ``label:`` becomes ambiguous - which is
        why labels.name is UNIQUE in the schema as well.
        """
        name = self._unique_name("Duplicate")
        first = self._call_json("create_label", display_name=name)
        assert "error" not in first, f"The first create failed: {first}"
        second = self._call_json("create_label", display_name=name)
        assert "error" in second, f"A duplicate name was accepted: {second}"

    def test_create_label_rejects_existing_system_label(self) -> None:
        """Shadowing a system label is the same collision as any other duplicate."""
        result = self._call_json("create_label", display_name="INBOX")
        assert "error" in result, f"'INBOX' was accepted as a new label: {result}"

    def test_create_label_rejects_malformed_names(self) -> None:
        """'/' separates levels, so an empty level is not a name."""
        for bad in ("", "   ", "/Leading", "Trailing/", "Empty//Level"):
            result = self._call_json("create_label", display_name=bad)
            assert "error" in result, f"display_name={bad!r} was accepted: {result}"

    def test_create_label_rejected_name_creates_nothing(self) -> None:
        """A rejected create leaves the label count alone."""
        before = len(self._call_json("list_labels", page_size=0)["labels"])
        self._call_json("create_label", display_name="Bad//Name")
        after = len(self._call_json("list_labels", page_size=0)["labels"])
        assert after == before, f"A rejected create still added a label ({before} -> {after})."

    # ------------------------------------------------------------------
    # Cross-tool consistency
    # ------------------------------------------------------------------

    def test_search_ids_resolve_through_get_thread_and_get_message(self) -> None:
        """Every ID search_threads returns is fetchable by the other two tools."""
        for thread in self._all_threads():
            fetched = self._call_json("get_thread", thread_id=thread["id"])
            assert fetched["id"] == thread["id"], (
                f"get_thread({thread['id']}) returned {fetched['id']}."
            )
            for message in thread["messages"]:
                one = self._call_json("get_message", message_id=message["id"])
                assert one["id"] == message["id"], (
                    f"get_message({message['id']}) returned {one['id']}."
                )

    def test_message_ids_are_unique_across_threads(self) -> None:
        """A message belongs to exactly one thread."""
        seen: dict[str, str] = {}
        for thread in self._all_threads():
            for message in thread["messages"]:
                previous = seen.setdefault(message["id"], thread["id"])
                assert previous == thread["id"], (
                    f"Message {message['id']} appears in both {previous} and {thread['id']}."
                )

    def test_repeated_calls_are_deterministic(self) -> None:
        """The same call twice returns the same result — the store is read-only."""
        first = self._call_json("search_threads", query="has:attachment", page_size=50)
        second = self._call_json("search_threads", query="has:attachment", page_size=50)
        assert first == second, "Identical queries returned different results."
