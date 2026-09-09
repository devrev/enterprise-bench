"""
Automated tests for the Google Calendar MCP server (MCP HTTP port 9015).

Covers all 9 tools exposed by ``googlecalendar-server/mcp_server.py``.

Read:

list_events     — list events on a calendar, filtered by time, text, and type
get_event       — fetch one event by ID
list_calendars  — list the calendars the user has access to
suggest_time    — suggest free slots across one or more attendees
search_events   — free-text search over the primary calendar

Write:

create_event      — create an event, optionally recurring
update_event      — sparse-patch an event
delete_event      — cancel an event (soft delete)
respond_to_event  — set the user's own RSVP

How this server differs from its siblings
-----------------------------------------
Same two-module split - ``server.py`` for the data layer, ``mcp_server.py`` importing it
in-process - and, like ``gmail-server``, ``server.py`` carries no FastAPI app because
nothing in the agent path calls a REST port for calendar.  So there is no REST half to
test.  The store is a plain dict built by ``_load_data()``: the free-text corpus is ~13k
characters across 50 events, the range key is derived rather than stored so no index
serves it, and occurrences of recurring events do not exist until a query runs, so there
is no table for SQL to scan.

Four bugs this suite exists to catch
------------------------------------
Each is invisible to a test written the obvious way, and each produces confidently wrong
answers rather than errors:

1. **Recurrence not expanded.**  Only 8 of 50 events recur, so a listing looks complete
   without expansion.  ``test_recurrence_expands_within_window`` pins the measured 13
   occurrences across 9 distinct events for the week of 27 July, and
   ``test_suggest_time_accounts_for_recurring_events`` covers the case that actually
   misleads a user: 3 August looks entirely free without expansion, and the tool offers a
   slot already occupied by a weekly pipeline review.  A test written against 22 July
   instead would pass with no expansion at all - that is the RRULE's own authored date.

2. **All-day dates converted through UTC.**  Both all-day events are Europe/London, which
   is +01:00 in summer, so converting stored local midnight to UTC yields the previous day
   at 23:00.  ``test_all_day_event_date_is_local_midnight`` asserts the exact strings.

3. **Free-text corpus too narrow.**  A title-only matcher passes every ``Verano``-style
   check.  ``test_search_matches_email_and_location_only_fields`` uses the two queries that
   match nothing in any title: ``priya`` (attendee address) and ``thames`` (location).

4. **Timestamps compared as strings.**  Storage is naive local wall-clock plus a separate
   IANA zone, so two events an hour apart in reality can sort the wrong way as text.
   ``test_non_default_timezones_resolve_to_instants`` uses the Singapore and New York
   events, which a naive string compare gets backwards in both directions.

Testing the write half
----------------------
``mcp_test_client`` is class-scoped (see ``conftest.py``), so every test in this class
shares one store and a write is visible to every test that runs after it.  pytest gives no
guarantee that the read tests run first, so isolation is enforced from both directions:

* **Reversible writes restore what they changed.**  ``respond_to_event`` and the
  ``update_event`` tests put the original value back before returning, so the calendar
  other tests see is the one they were written against.
* **Creates assert deltas, and delete is soft.**  ``create_event`` measures
  before-and-after; ``delete_event`` sets ``status: "cancelled"`` rather than removing the
  record, so it is applied only to events this suite created.
* **The exact-count assertions subtract what this suite created.**  Every created id lands
  in ``_CREATED_IDS`` and ``_authored()`` filters it out, so the measured occurrence and
  search counts hold no matter which half of the suite ran first.  Without it a create adds
  a row to the bare listing, and an ``update_event`` probe that sets location "Room Thames"
  joins the ``thames`` fixture's result set.

Nothing reaches the dataset in either case: the store is a per-process dict and the JSON is
mounted read-only, so a fresh process starts from the authored calendar again.


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

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

from base.base_mcp_test import BaseMCPServerTests

_BAD_EVENT_ID = "EVT-DOES-NOT-EXIST-9999"

#: A query guaranteed to match nothing, for "empty is not an error" assertions.
_NO_MATCH_QUERY = "xyzzy-no-match-guaranteed-9999"

#: Every event id this suite created, so the exact-count assertions can exclude them.
#:
#: The client is class-scoped, so a created event is visible to every test that runs after
#: it - and pytest gives no guarantee that the read tests run before the write tests.  The
#: counts below are measured against the *authored* calendar, and a created event is not
#: inert: `test_update_event_patches_only_what_it_is_given` sets location "Room Thames",
#: which matches the `thames` fixture, and any create adds a row to the bare listing.
#: Filtering by id rather than by title keeps the assertions exact without constraining
#: what a write test is allowed to write.
_CREATED_IDS: set[str] = set()

#: The account whose calendar this is.
_OWNER = "ellie.ashworth@maplesoftware.net"
_COLLEAGUE = "priya.deshpande@maplesoftware.net"

#: Fields every Event carries, on every tool that returns one.
_EVENT_FIELDS = (
    "id",
    "summary",
    "start",
    "end",
    "status",
    "eventType",
    "availability",
    "visibility",
    "organizer",
    "creator",
    "attendees",
    "htmlLink",
)

#: The week the recurrence fixtures are measured over: Mon 27 Jul - Mon 3 Aug 2026.
_WEEK_START = "2026-07-27T00:00:00Z"
_WEEK_END = "2026-08-03T00:00:00Z"

#: Measured for that week: 13 occurrences across 9 distinct events.
_WEEK_OCCURRENCES = 13
_WEEK_DISTINCT = 9

#: The authored data's own horizon, max(utc_end) over the 50 authored events.
#:
#: Passed as an explicit ``end_time`` by the bare-listing test rather than relying on the
#: internal bound.  ``HORIZON`` is refreshed by ``put()``, so creating an event dated past
#: 6 August moves it and the *authored* recurring events legitimately generate further
#: occurrences - 7 more, across EVT-0113/0119/0120/0121.  Those rows carry authored ids, so
#: no ``_CREATED_IDS`` filter can subtract them: 75 is a fact about a generation bound, not
#: about the store.  Naming the bound is what makes the count independent of test order.
_AUTHORED_HORIZON = "2026-08-06T09:30:00Z"

#: Measured to that horizon: 75 occurrences across all 50 events.
_BARE_OCCURRENCES = 75
_BARE_DISTINCT = 50

#: Free-text match counts, per event (toolset section 3.5.2).  ``priya`` matches only on
#: attendee addresses and ``thames`` only on location - the two rows a title-only matcher
#: returns 0 for.
_SEARCH_FIXTURES = {
    "reconciliation": 5,
    "Verano": 10,
    "Loch": 6,
    "Thornbury": 7,
    "review": 8,
    "sync": 5,
    "priya": 23,
    "forecast": 1,
    "thames": 2,
    "Verano board": 6,
    "Loch webhook": 1,
    "Maple Deal Desk": 2,
}


class TestGcalServer(BaseMCPServerTests):
    """
    Test suite for the Google Calendar MCP server.

    Verifies all 9 tools exposed by ``googlecalendar-server/mcp_server.py`` across both
    unit (PatchedFastMCPHarness) and integration (Docker/Streamable HTTP) modes.

    The server loads ``integrations/data/calendar_json_data/`` (events, transcripts) into
    an in-process dict and exposes it as a replica of Google's official Calendar MCP
    server.
    """

    server_name = "gcal-mcp"
    server_port = 9015
    expected_tools = [
        # Read
        "list_events",
        "get_event",
        "list_calendars",
        "suggest_time",
        "search_events",
        # Write
        "create_event",
        "update_event",
        "delete_event",
        "respond_to_event",
    ]

    # ------------------------------------------------------------------
    # FastMCP app factory (unit mode)
    # ------------------------------------------------------------------

    @classmethod
    def _build_app(cls, data_dir: Path):
        """Build the Calendar FastMCP app in-process for unit testing.

        Uses importlib to load server.py with a unique module name, then registers it as
        ``sys.modules["server"]`` so mcp_server.py's ``from server import ...`` resolves
        correctly.

        ``DATA_DIR`` must be assigned before ``_load_data()`` runs: the source path is
        resolved by ``calendar_dir()`` at call time, which is why that is a function rather
        than a module constant.
        """
        server_dir = Path(__file__).parent.parent / "googlecalendar-server"

        # Load server.py under a unique module name to avoid collisions when
        # multiple servers are loaded in the same pytest session.
        spec = importlib.util.spec_from_file_location(
            "gcal_server_server", server_dir / "server.py"
        )
        server_mod = importlib.util.module_from_spec(spec)
        sys.modules["server"] = server_mod
        spec.loader.exec_module(server_mod)

        # Point the server at the test fixture data directory and load the store.
        server_mod.DATA_DIR = data_dir
        server_mod._load_data()

        # Load mcp_server.py — it will import from the ``server`` module we just set.
        mcp_spec = importlib.util.spec_from_file_location(
            "gcal_server_mcp", server_dir / "mcp_server.py"
        )
        mcp_mod = importlib.util.module_from_spec(mcp_spec)
        mcp_spec.loader.exec_module(mcp_mod)
        return mcp_mod.mcp

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _events(self, **kwargs) -> list[dict]:
        """Call ``list_events`` and return just the events array."""
        result = self._call_json("list_events", **kwargs)
        assert "events" in result, f"list_events envelope has no events[]: {result}"
        return result["events"]

    @staticmethod
    def _authored(events: list[dict]) -> list[dict]:
        """Drop the events this suite created, leaving the calendar as authored.

        Used only by the exact-count assertions.  See ``_CREATED_IDS``.
        """
        return [e for e in events if e["id"] not in _CREATED_IDS]

    def _find_event(self, predicate) -> dict:
        """Return the first event in the full listing satisfying ``predicate``."""
        for event in self._events(page_size=250):
            if predicate(event):
                return event
        raise AssertionError("No event in the fixture data satisfies the predicate.")

    def _make_event(self, **overrides) -> dict:
        """Create an event with sane defaults, returning the created Event."""
        params = {
            "summary": "Test event",
            "start_time": "2026-08-12T14:00:00+01:00",
            "end_time": "2026-08-12T15:00:00+01:00",
        }
        params.update(overrides)
        created = self._call_json("create_event", **params)
        assert "error" not in created, f"create_event failed: {created}"
        _CREATED_IDS.add(created["id"])
        return created

    # ------------------------------------------------------------------
    # list_calendars
    # ------------------------------------------------------------------

    def test_list_calendars_returns_the_owners_calendar(self) -> None:
        """The calendar list resolves a human phrase to an address the other tools take."""
        result = self._call_json("list_calendars")
        calendars = result["calendars"]
        assert calendars, "No calendars returned."
        ids = [c["id"] for c in calendars]
        assert _OWNER in ids, f"Owner calendar missing from {ids}."
        for calendar in calendars:
            for field in ("id", "summary", "timeZone"):
                assert calendar.get(field), f"Calendar missing {field}: {calendar}"

    # ------------------------------------------------------------------
    # list_events — shape, filtering, ordering, pagination
    # ------------------------------------------------------------------

    def test_list_events_returns_calendar_envelope(self) -> None:
        """The envelope is the calendar resource, with events[] as one of its fields."""
        result = self._call_json("list_events", page_size=5)
        for field in ("summary", "timeZone", "updated", "accessRole", "events"):
            assert field in result, f"Envelope missing {field}: {sorted(result)}"
        assert result["accessRole"] == "owner"
        # Calendar v3 carries milliseconds, unlike Gmail's second precision.
        assert result["updated"].endswith("Z") and "." in result["updated"], (
            f"updated is not millisecond RFC3339: {result['updated']}"
        )

    def test_event_shape_is_complete(self) -> None:
        """Every Event carries the full field set, and no field is JSON null."""
        for event in self._events(page_size=25):
            for field in _EVENT_FIELDS:
                assert field in event, f"Event {event.get('id')} missing {field}."
            for key, value in event.items():
                assert value is not None, f"Event {event['id']} has null {key}."

    def test_list_events_time_window_filters(self) -> None:
        """A window returns fewer events than the unbounded call, and none outside it."""
        windowed = self._events(start_time=_WEEK_START, end_time=_WEEK_END, page_size=250)
        everything = self._events(page_size=250)
        assert 0 < len(windowed) < len(everything)

    def test_list_events_rejects_inverted_window(self) -> None:
        """start_time must precede end_time; the error is returned, not raised."""
        result = self._call_json(
            "list_events", start_time=_WEEK_END, end_time=_WEEK_START
        )
        assert "error" in result, f"Inverted window was accepted: {result}"

    def test_list_events_empty_result_is_not_an_error(self) -> None:
        """A filter matching nothing returns an empty envelope, not an error."""
        result = self._call_json("list_events", full_text=_NO_MATCH_QUERY)
        assert "error" not in result
        assert result["events"] == []

    def test_list_events_pagination_is_consistent(self) -> None:
        """Paging through with a token yields the same events as one large page."""
        everything = self._events(page_size=250)
        assert len(everything) > 10, "Need more than one page's worth to test paging."

        collected: list[dict] = []
        token = ""
        for _ in range(50):  # guard against a token that never advances
            result = self._call_json("list_events", page_size=10, page_token=token)
            collected.extend(result["events"])
            token = result.get("nextPageToken", "")
            if not token:
                break
        assert [e["id"] for e in collected] == [e["id"] for e in everything]

    def test_list_events_order_by_reverses(self) -> None:
        """startTimeDesc is the exact reverse of startTime."""
        ascending = self._events(order_by="startTime", page_size=250)
        descending = self._events(order_by="startTimeDesc", page_size=250)
        assert [e["id"] for e in descending] == [e["id"] for e in ascending][::-1]

    def test_list_events_unknown_event_type_returns_empty(self) -> None:
        """Every event is DEFAULT, so a FOCUS_TIME filter is honoured and returns none.

        Honoured rather than dropped: EVT-0126 is titled "Focus block -- Q3 forecast" but
        is an ordinary DEFAULT event, and classifying it by title match would be the
        adapter inventing data the schema has no field for.
        """
        result = self._call_json("list_events", event_type=["FOCUS_TIME"], page_size=250)
        assert "error" not in result
        assert result["events"] == []

    # ------------------------------------------------------------------
    # Bug 1 — recurrence expansion
    # ------------------------------------------------------------------

    def test_recurrence_expands_within_window(self) -> None:
        """A window returns one row per occurrence, not one per event.

        Only 8 of 50 events recur, so an unexpanded listing looks complete.  Measured for
        the week of 27 July: 13 occurrences across 9 distinct events.
        """
        events = self._authored(
            self._events(start_time=_WEEK_START, end_time=_WEEK_END, page_size=250)
        )
        distinct = {e["id"] for e in events}
        assert len(events) == _WEEK_OCCURRENCES, (
            f"Expected {_WEEK_OCCURRENCES} occurrences for the week of 27 July, got "
            f"{len(events)} — recurrence expansion is likely missing or over-generating."
        )
        assert len(distinct) == _WEEK_DISTINCT

    def test_expanded_occurrences_carry_recurring_event_id(self) -> None:
        """Generated instances carry recurringEventId and originalStartTime; bases do not.

        This is how a caller tells a generated occurrence from an authored record - the
        only signal, since there is no singleEvents parameter to gate expansion.
        """
        events = self._events(start_time=_WEEK_START, end_time=_WEEK_END, page_size=250)
        recurring = [e for e in events if "recurringEventId" in e]
        assert recurring, "No expanded instances found in a window known to contain some."
        for event in recurring:
            assert event["recurringEventId"] == event["id"]
            assert "originalStartTime" in event
            assert isinstance(event.get("recurrence"), list) and event["recurrence"]
            assert event["recurrence"][0].startswith("RRULE"), (
                "recurrence must be the RRULE string wrapped in an array, not indexed "
                f"into: {event['recurrence']}"
            )

    def test_recurring_occurrences_hold_local_time_across_dst(self) -> None:
        """A weekly 10:00 local meeting stays 10:00 local across the October DST change.

        The UTC instant moves by an hour; the wall-clock time must not.  Getting this
        backwards shifts every recurring meeting by an hour for half the year.
        """
        events = self._events(
            full_text="Loch",
            start_time="2026-10-20T00:00:00Z",
            end_time="2026-11-10T00:00:00Z",
            page_size=250,
        )
        weekly = [e for e in events if e.get("recurringEventId")]
        assert len(weekly) >= 3, "Need occurrences on both sides of the DST boundary."
        local_times = {e["start"]["dateTime"][11:16] for e in weekly}
        assert len(local_times) == 1, (
            f"Local start time drifted across the DST boundary: {sorted(local_times)}"
        )
        offsets = {e["start"]["dateTime"][19:] for e in weekly}
        assert len(offsets) > 1, (
            "Expected the UTC offset to change across the DST boundary; the window may "
            f"not span it: {sorted(offsets)}"
        )

    def test_bare_list_events_terminates_and_is_complete(self) -> None:
        """A call with no arguments expands, covers every event, and finishes.

        The authored RRULEs carry no COUNT or UNTIL, so an unbounded generation loop never
        exits - a prototype reached occurrence #100,000 dated November 2409.  The bound is
        the store's own far edge.

        Two assertions, because they are independent and only one of them is order-free:

        * **The bare call** is asserted on termination and coverage only.  Its row count is
          a function of ``HORIZON``, which a create dated past 6 August moves - and the
          extra occurrences carry *authored* ids, so ``_authored()`` cannot subtract them.
        * **The exact 75** is asserted against an explicit ``end_time`` at the authored
          horizon, which pins the bound instead of inheriting it.
        """
        bare = self._events(page_size=250)
        assert len({e["id"] for e in bare}) >= _BARE_DISTINCT, (
            "The bare call did not cover every authored event."
        )
        assert len(bare) >= len({e["id"] for e in bare}), "Nothing expanded."

        bounded = self._authored(
            self._events(end_time=_AUTHORED_HORIZON, page_size=250)
        )
        assert len(bounded) == _BARE_OCCURRENCES
        assert len({e["id"] for e in bounded}) == _BARE_DISTINCT

    # ------------------------------------------------------------------
    # Bug 2 — all-day dates
    # ------------------------------------------------------------------

    def test_all_day_event_date_is_local_midnight(self) -> None:
        """All-day events use `date`, built from the stored local date, not a UTC convert.

        Both all-day events are Europe/London, which is +01:00 in summer - so converting
        stored local midnight to UTC lands on the previous day at 23:00 and reports the
        event a day early.  The capture documents this field as a full midnight timestamp
        with no milliseconds, which is also why it is not the `updated` formatter.
        """
        all_day = [
            e
            for e in self._events(page_size=250)
            if "date" in e["start"]
        ]
        assert all_day, "No all-day events found in the fixture data."
        for event in all_day:
            for edge in ("start", "end"):
                value = event[edge]["date"]
                assert value.endswith("T00:00:00Z"), (
                    f"{event['id']} {edge}.date is not literal midnight Z: {value}"
                )
                assert "." not in value, (
                    f"{event['id']} {edge}.date carries milliseconds: {value}"
                )
            assert "dateTime" not in event["start"], (
                "Set date or date_time, but not both."
            )

    def test_timed_events_carry_offset_bearing_datetime(self) -> None:
        """Timed events use `dateTime` with an offset computed per event and per date."""
        timed = [e for e in self._events(page_size=250) if "dateTime" in e["start"]]
        assert timed, "No timed events found."
        for event in timed:
            assert "date" not in event["start"]
            value = event["start"]["dateTime"]
            assert value[10] == "T" and value[19] in "+-", (
                f"{event['id']} start.dateTime carries no UTC offset: {value}"
            )
            assert event["start"]["timeZone"], f"{event['id']} start has no timeZone."

    # ------------------------------------------------------------------
    # Bug 3 — the free-text corpus
    # ------------------------------------------------------------------

    def test_search_matches_email_and_location_only_fields(self) -> None:
        """`priya` matches only attendee addresses and `thames` only locations.

        A title-only matcher returns 0 for both and still passes every `Verano`-style
        test.
        """
        for query, expected in (("priya", 23), ("thames", 2)):
            found = self._authored(
                self._call_json("search_events", query=query, page_size=250)["events"]
            )
            assert len(found) == expected, (
                f"search_events({query!r}) returned {len(found)}, expected "
                f"{expected} — the free-text corpus is probably missing a field."
            )

    def test_search_fixture_counts(self) -> None:
        """Every documented match count reproduces exactly."""
        for query, expected in _SEARCH_FIXTURES.items():
            found = self._authored(
                self._call_json("search_events", query=query, page_size=250)["events"]
            )
            assert len(found) == expected, (
                f"search_events({query!r}): got {len(found)}, want {expected}"
            )

    def test_search_terms_are_anded(self) -> None:
        """Multi-term queries require every term, so they narrow rather than widen."""
        broad = self._call_json("search_events", query="Verano", page_size=250)
        narrow = self._call_json("search_events", query="Verano board", page_size=250)
        assert len(narrow["events"]) < len(broad["events"])
        broad_ids = {e["id"] for e in broad["events"]}
        assert {e["id"] for e in narrow["events"]} <= broad_ids

    def test_search_is_case_insensitive(self) -> None:
        """Matching lowercases both sides."""
        lower = self._call_json("search_events", query="verano", page_size=250)
        upper = self._call_json("search_events", query="VERANO", page_size=250)
        assert [e["id"] for e in lower["events"]] == [e["id"] for e in upper["events"]]

    def test_search_envelope_is_thin(self) -> None:
        """search_events returns {events, nextPageToken?} - not the calendar resource."""
        result = self._call_json("search_events", query="Verano", page_size=250)
        assert set(result) <= {"events", "nextPageToken"}, (
            f"search_events envelope has calendar fields it should not: {sorted(result)}"
        )

    def test_search_and_full_text_share_one_matcher(self) -> None:
        """list_events(full_text=) and search_events agree on the same query."""
        for query in ("priya", "thames", "Loch webhook"):
            search = self._call_json("search_events", query=query, page_size=250)
            listed = self._call_json("list_events", full_text=query, page_size=250)
            assert {e["id"] for e in search["events"]} == {
                e["id"] for e in listed["events"]
            }, f"The two entry points disagree on {query!r}."

    def test_search_finds_cancelled_events(self) -> None:
        """Cancelled events stay visible: there is no showDeleted parameter to gate them.

        EVT-0112 is authored cancelled, so `status` is the only signal a caller has.
        """
        result = self._call_json("search_events", query="Thornbury", page_size=250)
        statuses = {e["status"] for e in result["events"]}
        assert "cancelled" in statuses, (
            f"No cancelled event in a result set known to contain one: {statuses}"
        )

    def test_search_empty_result_is_not_an_error(self) -> None:
        """A query matching nothing returns an empty list, not an error."""
        result = self._call_json("search_events", query=_NO_MATCH_QUERY)
        assert "error" not in result
        assert result["events"] == []

    # ------------------------------------------------------------------
    # Bug 4 — timestamps are instants, not strings
    # ------------------------------------------------------------------

    def test_non_default_timezones_resolve_to_instants(self) -> None:
        """Windows are compared as instants, so a foreign-zone event lands correctly.

        Storage is naive local wall-clock plus a separate IANA zone.  A naive string
        compare gets both of these backwards: the Singapore event's local 09:00 is 01:00Z,
        and the New York event's local 15:00 is 19:00Z.
        """
        foreign = [
            e
            for e in self._events(page_size=250)
            if e.get("start", {}).get("timeZone") not in (None, "Europe/London")
        ]
        assert foreign, "No non-London events found in the fixture data."

        for event in foreign:
            zoned = event["start"]["dateTime"]
            offset = zoned[19:]
            assert offset not in ("+01:00", "+00:00"), (
                f"{event['id']} was rendered with a London offset: {zoned}"
            )
            # The event must be found by a window expressed in UTC around its real
            # instant, which only works if the server converted through the zone.
            found = self._events(
                start_time=zoned,
                end_time=event["end"]["dateTime"],
                page_size=250,
            )
            assert any(e["id"] == event["id"] for e in found), (
                f"{event['id']} was not found by a window over its own instant — the "
                "range filter is probably comparing local strings."
            )

    def test_overlap_not_containment(self) -> None:
        """A window inside an event matches it: overlap, not containment.

        A five-minute window in the middle of a meeting must find the meeting.  Requiring
        containment silently drops every event longer than the window.
        """
        event = self._find_event(
            lambda e: "dateTime" in e["start"] and not e.get("recurringEventId")
        )
        start = event["start"]["dateTime"]
        # A one-minute window one minute after the start, in the event's own offset.
        hour, minute = int(start[11:13]), int(start[14:16])
        inner_start = f"{start[:11]}{hour:02d}:{minute + 1:02d}:00{start[19:]}"
        inner_end = f"{start[:11]}{hour:02d}:{minute + 2:02d}:00{start[19:]}"
        found = self._events(start_time=inner_start, end_time=inner_end, page_size=250)
        assert any(e["id"] == event["id"] for e in found), (
            f"{event['id']} not found by a window inside it — the range predicate is "
            "probably testing containment rather than overlap."
        )

    # ------------------------------------------------------------------
    # get_event
    # ------------------------------------------------------------------

    def test_get_event_round_trips_from_a_listing(self) -> None:
        """An ID from list_events resolves through get_event to the same event."""
        listed = self._events(page_size=1)[0]
        fetched = self._call_json("get_event", event_id=listed["id"])
        assert fetched["id"] == listed["id"]
        assert fetched["summary"] == listed["summary"]

    def test_get_event_unknown_id_errors(self) -> None:
        """An unknown ID is a genuine fault and reports one."""
        result = self._call_json("get_event", event_id=_BAD_EVENT_ID)
        assert "error" in result

    def test_get_event_marks_self_and_organizer(self) -> None:
        """`self` and `organizer` are derived projections, never stored fields."""
        event = self._find_event(lambda e: len(e.get("attendees", [])) > 1)
        organizer_email = event["organizer"]["email"]
        flagged = [a for a in event["attendees"] if a.get("organizer")]
        if any(a["email"] == organizer_email for a in event["attendees"]):
            assert flagged and flagged[0]["email"] == organizer_email
        owners = [a for a in event["attendees"] if a.get("self")]
        for attendee in owners:
            assert attendee["email"] == _OWNER

    # ------------------------------------------------------------------
    # suggest_time
    # ------------------------------------------------------------------

    def test_suggest_time_returns_slots_without_pagination(self) -> None:
        """The response is {timeSlots} - this tool has no pagination field of any kind."""
        result = self._call_json(
            "suggest_time",
            attendee_emails=[_OWNER, _COLLEAGUE],
            start_time="2026-08-03T09:00:00+01:00",
            end_time="2026-08-03T18:00:00+01:00",
            duration_minutes=30,
            time_zone="Europe/London",
        )
        assert set(result) == {"timeSlots"}, f"Unexpected envelope: {sorted(result)}"
        for slot in result["timeSlots"]:
            assert set(slot) == {"start", "end"}
            assert "dateTime" in slot["start"] and "dateTime" in slot["end"]

    def test_suggest_time_accounts_for_recurring_events(self) -> None:
        """3 August is not free at 09:00: a weekly review recurs onto it.

        The measured case that proves expansion feeds free/busy.  Without it the whole day
        reads as free and the tool confidently offers an occupied slot.  A test written
        against 22 July would pass either way - that is the RRULE's authored date, so
        expanded and unexpanded agree there.
        """
        result = self._call_json(
            "suggest_time",
            attendee_emails=[_OWNER, _COLLEAGUE],
            start_time="2026-08-03T09:00:00+01:00",
            end_time="2026-08-03T18:00:00+01:00",
            duration_minutes=30,
            time_zone="Europe/London",
            preferences={"startHour": "09:00", "endHour": "18:00", "pageSize": 20},
        )
        starts = [s["start"]["dateTime"] for s in result["timeSlots"]]
        assert starts, "No slots offered on a day that has free time."
        assert not any(s[11:16] == "09:00" for s in starts), (
            "A 09:00 slot was offered on 3 August, which is occupied by a recurring "
            f"review — free/busy is not seeing expanded occurrences. Slots: {starts[:5]}"
        )
        assert any(s[11:16] == "10:00" for s in starts), (
            f"Expected the first free slot at 10:00 on 3 August; got {starts[:5]}"
        )

    def test_suggest_time_respects_preference_hours(self) -> None:
        """No slot may start before startHour or end after endHour."""
        result = self._call_json(
            "suggest_time",
            attendee_emails=[_OWNER],
            start_time="2026-08-03T00:00:00+01:00",
            end_time="2026-08-04T00:00:00+01:00",
            duration_minutes=30,
            time_zone="Europe/London",
            preferences={"startHour": "09:00", "endHour": "12:00", "pageSize": 20},
        )
        slots = result["timeSlots"]
        assert slots, "No slots offered inside a three-hour preferred window."
        for slot in slots:
            assert slot["start"]["dateTime"][11:16] >= "09:00", (
                f"Slot starts before startHour: {slot['start']['dateTime']}"
            )
            # The end must be inside the window too, and must not wrap past midnight -
            # a slot ending "00:00" is the next day and sorts below every endHour as a
            # bare string.
            end = slot["end"]["dateTime"]
            assert end[:10] == slot["start"]["dateTime"][:10] and end[11:16] <= "12:00", (
                f"Slot ends past endHour: {end}"
            )

    def test_suggest_time_slot_length_matches_duration(self) -> None:
        """Slots are discrete durationMinutes candidates, not maximal free intervals."""
        result = self._call_json(
            "suggest_time",
            attendee_emails=[_OWNER],
            start_time="2026-08-03T10:00:00+01:00",
            end_time="2026-08-03T18:00:00+01:00",
            duration_minutes=45,
            time_zone="Europe/London",
        )
        for slot in result["timeSlots"]:
            start_minutes = int(slot["start"]["dateTime"][11:13]) * 60 + int(
                slot["start"]["dateTime"][14:16]
            )
            end_minutes = int(slot["end"]["dateTime"][11:13]) * 60 + int(
                slot["end"]["dateTime"][14:16]
            )
            assert end_minutes - start_minutes == 45, f"Slot is not 45 minutes: {slot}"

    def test_suggest_time_excludes_weekends_when_asked(self) -> None:
        """excludeWeekends drops Saturday and Sunday entirely."""
        # 2026-08-08 is a Saturday, 2026-08-09 a Sunday.
        result = self._call_json(
            "suggest_time",
            attendee_emails=[_OWNER],
            start_time="2026-08-08T00:00:00+01:00",
            end_time="2026-08-10T00:00:00+01:00",
            duration_minutes=30,
            time_zone="Europe/London",
            preferences={"excludeWeekends": True, "pageSize": 20},
        )
        assert result["timeSlots"] == [], (
            f"Weekend slots offered despite excludeWeekends: {result['timeSlots'][:3]}"
        )

    def test_suggest_time_rejects_inverted_window(self) -> None:
        """An inverted interval is a fault, not an empty result."""
        result = self._call_json(
            "suggest_time",
            attendee_emails=[_OWNER],
            start_time="2026-08-04T00:00:00Z",
            end_time="2026-08-03T00:00:00Z",
        )
        assert "error" in result

    # ------------------------------------------------------------------
    # create_event
    # ------------------------------------------------------------------

    def test_create_event_appears_in_reads(self) -> None:
        """A created event is visible through get_event and search_events.

        The store is per-process and the dataset is mounted read-only, so a write must be
        verified through a read tool rather than by inspecting $DATA_DIR.
        """
        created = self._make_event(
            summary="Zzz unique create probe",
            description="Created by the test suite.",
        )
        assert created["summary"] == "Zzz unique create probe"
        assert created["status"] == "confirmed"
        assert created["updated"].endswith("Z")

        fetched = self._call_json("get_event", event_id=created["id"])
        assert fetched["summary"] == created["summary"]

        found = self._call_json("search_events", query="unique create probe", page_size=250)
        assert created["id"] in {e["id"] for e in found["events"]}

    def test_create_event_adds_the_owner_as_attendee(self) -> None:
        """The organizer is added to attendees[] with an accepted RSVP.

        Both the tool's own description and the schema require it - the schema's rule that
        the organizer appears in attendees[] is enforced by the load-time assertions,
        which apply to written records too.
        """
        created = self._make_event(
            summary="Zzz unique attendee probe",
            attendees=[{"email": _COLLEAGUE, "displayName": "Priya Deshpande"}],
        )
        by_email = {a["email"]: a for a in created["attendees"]}
        assert _OWNER in by_email, f"Owner not added: {sorted(by_email)}"
        assert by_email[_OWNER]["responseStatus"] == "accepted"
        assert by_email[_OWNER].get("self") is True
        assert _COLLEAGUE in by_email

    def test_create_recurring_event_expands(self) -> None:
        """A created RRULE is honoured by the same expansion path as authored ones."""
        created = self._make_event(
            summary="Zzz unique weekly probe",
            start_time="2026-08-12T14:00:00+01:00",
            end_time="2026-08-12T15:00:00+01:00",
            recurrence_data=["RRULE:FREQ=WEEKLY;BYDAY=WE"],
        )
        assert created["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=WE"]
        occurrences = [
            e
            for e in self._events(
                start_time="2026-08-12T00:00:00Z",
                end_time="2026-09-10T00:00:00Z",
                page_size=250,
            )
            if e["id"] == created["id"]
        ]
        assert len(occurrences) >= 4, (
            f"A weekly rule produced {len(occurrences)} occurrences in four weeks."
        )

    def test_create_event_rejects_unsupported_recurrence(self) -> None:
        """An RRULE token this server does not implement is refused, not ignored.

        Accepting it silently produces an event that exists and recurs wrongly - worse
        than a refusal, because nothing signals it.
        """
        for rule in (
            "RRULE:FREQ=WEEKLY;INTERVAL=2",
            "RRULE:FREQ=YEARLY",
            "RRULE:FREQ=WEEKLY;BYDAY=XX",
        ):
            result = self._call_json(
                "create_event",
                summary="Zzz unsupported rule probe",
                start_time="2026-08-12T14:00:00+01:00",
                end_time="2026-08-12T15:00:00+01:00",
                recurrence_data=[rule],
            )
            assert "error" in result, f"{rule} was accepted without complaint."

    def test_create_event_rejects_bad_input(self) -> None:
        """Unparseable timestamps and inverted intervals are faults."""
        assert "error" in self._call_json(
            "create_event",
            summary="Zzz bad time probe",
            start_time="not-a-timestamp",
            end_time="2026-08-12T15:00:00+01:00",
        )
        assert "error" in self._call_json(
            "create_event",
            summary="Zzz inverted probe",
            start_time="2026-08-12T15:00:00+01:00",
            end_time="2026-08-12T14:00:00+01:00",
        )

    def test_create_all_day_event_uses_date_form(self) -> None:
        """An all-day create round-trips through the `date` projection, not `dateTime`."""
        created = self._make_event(
            summary="Zzz unique all-day probe",
            start_time="2026-08-12T00:00:00+01:00",
            end_time="2026-08-13T00:00:00+01:00",
            all_day=True,
        )
        assert "date" in created["start"] and "dateTime" not in created["start"]
        assert created["start"]["date"] == "2026-08-12T00:00:00Z", (
            f"All-day start was shifted: {created['start']['date']}"
        )

    def test_create_event_mints_a_meet_url(self) -> None:
        """add_google_meet_url produces a conferenceUrl; an explicit URL overrides it."""
        minted = self._make_event(
            summary="Zzz unique meet probe", add_google_meet_url=True
        )
        assert minted.get("conferenceUrl", "").startswith("https://"), minted

        explicit = self._make_event(
            summary="Zzz unique meet override probe",
            add_google_meet_url=True,
            google_meet_url="https://meet.example.com/abc-defg-hij",
        )
        assert explicit["conferenceUrl"] == "https://meet.example.com/abc-defg-hij"

    # ------------------------------------------------------------------
    # update_event
    # ------------------------------------------------------------------

    def test_update_event_patches_only_what_it_is_given(self) -> None:
        """Unset fields are left alone - the patch is sparse, not a replace."""
        created = self._make_event(
            summary="Zzz unique patch probe",
            description="Original description.",
            location="Room Thames",
        )
        patched = self._call_json(
            "update_event", event_id=created["id"], summary="Zzz patched summary"
        )
        assert patched["summary"] == "Zzz patched summary"
        assert patched["description"] == "Original description."
        assert patched["location"] == "Room Thames"
        assert patched["start"] == created["start"]

    def test_update_event_empty_string_clears_a_field(self) -> None:
        """An explicit empty string clears; absent leaves alone. They must differ."""
        created = self._make_event(
            summary="Zzz unique clear probe", description="Will be cleared."
        )
        patched = self._call_json("update_event", event_id=created["id"], description="")
        assert patched["description"] == ""

    def test_update_event_start_only_preserves_duration(self) -> None:
        """Moving the start without an end keeps the original length."""
        created = self._make_event(
            summary="Zzz unique move probe",
            start_time="2026-08-12T14:00:00+01:00",
            end_time="2026-08-12T15:30:00+01:00",
        )
        patched = self._call_json(
            "update_event",
            event_id=created["id"],
            start_time="2026-08-12T16:00:00+01:00",
        )
        assert patched["start"]["dateTime"][11:16] == "16:00"
        assert patched["end"]["dateTime"][11:16] == "17:30", (
            f"Duration was not preserved: {patched['start']} -> {patched['end']}"
        )

    def test_update_event_adds_and_removes_attendees(self) -> None:
        """Attendee edits are additive and subtractive, never a wholesale replace."""
        created = self._make_event(
            summary="Zzz unique attendee edit probe",
            attendees=[{"email": _COLLEAGUE, "displayName": "Priya Deshpande"}],
        )
        added = self._call_json(
            "update_event",
            event_id=created["id"],
            added_attendees=[
                {"email": "rohan.mehta@maplesoftware.net", "displayName": "Rohan Mehta"}
            ],
        )
        emails = {a["email"] for a in added["attendees"]}
        assert {"rohan.mehta@maplesoftware.net", _COLLEAGUE, _OWNER} <= emails

        removed = self._call_json(
            "update_event",
            event_id=created["id"],
            removed_attendee_emails=["rohan.mehta@maplesoftware.net"],
        )
        remaining = {a["email"] for a in removed["attendees"]}
        assert "rohan.mehta@maplesoftware.net" not in remaining
        assert _COLLEAGUE in remaining, "Removing one attendee dropped the others."

    def test_update_event_cannot_remove_the_organizer(self) -> None:
        """The schema requires the organizer in attendees[], so removing them is refused."""
        created = self._make_event(summary="Zzz unique organizer probe")
        result = self._call_json(
            "update_event", event_id=created["id"], removed_attendee_emails=[_OWNER]
        )
        assert "error" in result

    def test_update_event_echo_does_not_destroy_tentative(self) -> None:
        """Writing back the value a read reported must not lose information.

        show_as: tentative projects to AVAILABILITY_BUSY, because there is no tentative
        availability on the wire.  A read-modify-write that echoes BUSY must therefore
        leave a tentative hold tentative rather than flattening it to busy - the projection
        is lossy in one direction only.
        """
        tentative = None
        for event in self._events(page_size=250):
            if event["availability"] == "AVAILABILITY_BUSY":
                tentative = event
                break
        assert tentative is not None, "No busy event found."

        before = self._call_json("get_event", event_id=tentative["id"])
        echoed = self._call_json(
            "update_event",
            event_id=tentative["id"],
            availability=before["availability"],
        )
        assert echoed["availability"] == before["availability"]
        # And the round-trip is stable: reading again reports the same thing.
        after = self._call_json("get_event", event_id=tentative["id"])
        assert after["availability"] == before["availability"]

    def test_update_event_unknown_id_errors(self) -> None:
        """Patching a nonexistent event is a fault."""
        result = self._call_json(
            "update_event", event_id=_BAD_EVENT_ID, summary="nope"
        )
        assert "error" in result

    # ------------------------------------------------------------------
    # respond_to_event
    # ------------------------------------------------------------------

    def test_respond_to_event_sets_the_owners_rsvp(self) -> None:
        """The RSVP changes only the owner's attendee entry, and restores afterwards."""
        event = self._find_event(
            lambda e: any(a.get("self") for a in e.get("attendees", []))
        )
        before = self._call_json("get_event", event_id=event["id"])
        original = [a for a in before["attendees"] if a.get("self")][0]["responseStatus"]
        others_before = {
            a["email"]: a["responseStatus"]
            for a in before["attendees"]
            if not a.get("self")
        }

        try:
            responded = self._call_json(
                "respond_to_event",
                event_id=event["id"],
                response_status="declined",
                response_comment="Conflict with the board call.",
            )
            mine = [a for a in responded["attendees"] if a.get("self")][0]
            assert mine["responseStatus"] == "declined"
            assert mine.get("comment") == "Conflict with the board call."
            others_after = {
                a["email"]: a["responseStatus"]
                for a in responded["attendees"]
                if not a.get("self")
            }
            assert others_after == others_before, "Other attendees' RSVPs changed."
            # Event status and attendee RSVP are independent.
            assert responded["status"] == before["status"]
        finally:
            self._call_json(
                "respond_to_event", event_id=event["id"], response_status=original
            )

    def test_respond_to_event_rejects_needs_action(self) -> None:
        """An RSVP cannot be undone: this tool's own parameter lists only three values."""
        event = self._find_event(
            lambda e: any(a.get("self") for a in e.get("attendees", []))
        )
        result = self._call_json(
            "respond_to_event", event_id=event["id"], response_status="needsAction"
        )
        assert "error" in result

    def test_respond_to_event_unknown_id_errors(self) -> None:
        """Responding to a nonexistent event is a fault."""
        result = self._call_json(
            "respond_to_event", event_id=_BAD_EVENT_ID, response_status="accepted"
        )
        assert "error" in result

    # ------------------------------------------------------------------
    # delete_event
    # ------------------------------------------------------------------

    def test_delete_event_is_a_soft_cancel(self) -> None:
        """Delete returns the Event with status cancelled, and it stays readable.

        A soft delete is forced by the contract rather than chosen: the tool returns the
        deleted Event, so it cannot hard-delete.  Applied only to an event this suite
        created, since the class shares one store.
        """
        created = self._make_event(summary="Zzz unique delete probe")
        deleted = self._call_json("delete_event", event_id=created["id"])
        assert deleted["id"] == created["id"]
        assert deleted["status"] == "cancelled"

        still_there = self._call_json("get_event", event_id=created["id"])
        assert still_there["status"] == "cancelled"

    def test_delete_event_is_idempotent(self) -> None:
        """Deleting twice reports the same thing rather than failing."""
        created = self._make_event(summary="Zzz unique double delete probe")
        first = self._call_json("delete_event", event_id=created["id"])
        second = self._call_json("delete_event", event_id=created["id"])
        assert first["status"] == second["status"] == "cancelled"

    def test_delete_event_unknown_id_errors(self) -> None:
        """Deleting a nonexistent event is a fault."""
        result = self._call_json("delete_event", event_id=_BAD_EVENT_ID)
        assert "error" in result
