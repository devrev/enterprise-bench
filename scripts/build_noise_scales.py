#!/usr/bin/env python3
"""Build noise-scaled email/calendar datasets for the MCP noise-scaling experiment.

Implements the recipe in noise_plan.md (scale-free noise design) and
scaling-experiment-plan.md (the levels to build).

The canonical storyline records (98 messages / 58 events) are hard-pinned verbatim
into every output. Only the noise count changes between levels.

Noise is derived from the existing max-max noise pool rather than templated from
scratch, per scaling-experiment-plan.md 2.1 ("match this exactly - do not invent a
new noise style"):

  * target <= pool  -> deterministic subsample, taking WHOLE email threads only
  * target >  pool  -> the full pool plus clones with freshly minted identifiers
                       and shifted dates, preserving every other field

Usage:
    python3 scripts/build_noise_scales.py [--seed 20260909] [--canonical nonoise|detelled]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import string
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CANON_MESSAGES = REPO / "data/no_noise_email_calendar/messages.json"
CANON_EVENTS = REPO / "data/no_noise_email_calendar/events.json"
MAX_MESSAGES = REPO / "data/email_json_data/messages.json"
MAX_EVENTS = REPO / "data/calendar_json_data/events.json"
OUT_ROOT = REPO / "data/scaled"

# Target totals (canonical + noise). scaling-experiment-plan.md 2.4 + the beyond levels.
LEVELS = {
    "email_mid": 1000,
    "email_beyond": 24500,
    "calendar_mid": 580,
    "calendar_beyond": 5800,
}

B62 = string.ascii_letters + string.digits


def load(path: Path):
    with path.open() as fh:
        return json.load(fh)


def dump(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def id_num(record_id: str) -> int:
    return int(re.search(r"(\d+)$", record_id).group(1))


def token(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(B62) for _ in range(n))


def parse_dt(value: str) -> tuple[datetime, bool]:
    """Parse an ISO timestamp, remembering whether it carried a trailing Z."""
    zulu = value.endswith("Z")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None), zulu


def fmt_dt(moment: datetime, zulu: bool) -> str:
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return stamp + "Z" if zulu else stamp


# --------------------------------------------------------------------------- email


def build_messages(canonical, pool, target, rng):
    """Return canonical + enough noise messages to reach `target`, whole threads only."""
    noise_target = target - len(canonical)
    if noise_target < 0:
        raise SystemExit(f"target {target} is below the canonical count {len(canonical)}")

    threads: dict[str, list] = {}
    for record in pool:
        threads.setdefault(record["thread_id"], []).append(record)
    # Keep each thread in send order so in_reply_to chains stay coherent.
    for members in threads.values():
        members.sort(key=lambda r: (r["date"], id_num(r["id"])))

    thread_ids = sorted(threads)
    rng.shuffle(thread_ids)

    selected: list = []
    for tid in thread_ids:
        if len(selected) >= noise_target:
            break
        members = threads[tid]
        # Never split a thread: only take it if it fits whole.
        if len(selected) + len(members) <= noise_target:
            selected.extend(members)

    # Top up with clones when the pool cannot cover the target.
    if len(selected) < noise_target:
        selected.extend(
            clone_messages(threads, thread_ids, noise_target - len(selected), pool, rng)
        )

    return canonical + selected


def clone_messages(threads, thread_ids, needed, pool, rng):
    next_id = max(id_num(r["id"]) for r in pool) + 1
    next_thread = max(id_num(r["thread_id"]) for r in pool) + 1

    lo = min(parse_dt(r["date"])[0] for r in pool)
    hi = max(parse_dt(r["date"])[0] for r in pool)
    span = int((hi - lo).total_seconds())

    clones: list = []
    while len(clones) < needed:
        source = threads[thread_ids[rng.randrange(len(thread_ids))]]
        if len(clones) + len(source) > needed:
            # Only ever emit whole threads; fall back to a single-message thread.
            source = [
                next(
                    m
                    for tid in thread_ids
                    if len(threads[tid]) == 1
                    for m in threads[tid]
                )
            ]

        thread_id = f"THR-{next_thread:04d}"
        next_thread += 1

        # Shift the whole thread by one delta so intra-thread ordering is preserved.
        base_dt, _ = parse_dt(source[0]["date"])
        shift = timedelta(seconds=rng.randrange(span) - int((base_dt - lo).total_seconds()))

        minted: list[str] = []
        for position, original in enumerate(source):
            record = dict(original)  # dict() preserves the canonical key order
            record["id"] = f"EMAIL-{next_id:04d}"
            next_id += 1

            domain = original["from"]["email"].split("@")[1]
            message_id = f"<{token(rng, 22)}@{domain}>"
            record["message_id"] = message_id
            record["thread_id"] = thread_id

            if position and original["in_reply_to"]:
                record["in_reply_to"] = minted[-1]
                record["references"] = list(minted)
            else:
                record["in_reply_to"] = None
                record["references"] = []
            minted.append(message_id)

            for field in ("date", "received_date"):
                moment, zulu = parse_dt(original[field])
                record[field] = fmt_dt(moment + shift, zulu)

            clones.append(record)
        if len(clones) >= needed:
            break

    return clones[:needed]


# ------------------------------------------------------------------------ calendar


def build_events(canonical, pool, target, rng):
    noise_target = target - len(canonical)
    if noise_target < 0:
        raise SystemExit(f"target {target} is below the canonical count {len(canonical)}")

    order = list(range(len(pool)))
    rng.shuffle(order)
    selected = [pool[i] for i in order[:noise_target]]

    if len(selected) < noise_target:
        selected.extend(clone_events(pool, noise_target - len(selected), rng))

    return canonical + selected


def clone_events(pool, needed, rng):
    next_id = max(id_num(r["id"]) for r in pool) + 1

    lo = min(parse_dt(r["start"]["date_time"])[0] for r in pool)
    hi = max(parse_dt(r["start"]["date_time"])[0] for r in pool)

    clones = []
    for _ in range(needed):
        original = pool[rng.randrange(len(pool))]
        record = dict(original)

        event_id = f"EVT-{next_id:04d}"
        next_id += 1
        record["id"] = event_id

        domain = original["organizer"]["email"].split("@")[1]
        record["iCal_uid"] = f"{token(rng, 26)}@{domain}"

        # Shift start/end together so the duration is untouched. Whole weeks only:
        # the pool puts every event on :00 seconds and on business weekdays, and a
        # 7-day multiple preserves seconds, time-of-day and weekday alike.
        start_dt, start_z = parse_dt(original["start"]["date_time"])
        end_dt, end_z = parse_dt(original["end"]["date_time"])
        weeks_before = int((start_dt - lo).days // 7)
        weeks_after = int((hi - start_dt).days // 7)
        shift = timedelta(weeks=rng.randint(-weeks_before, weeks_after))
        record["start"] = dict(original["start"])
        record["end"] = dict(original["end"])
        record["start"]["date_time"] = fmt_dt(start_dt + shift, start_z)
        record["end"]["date_time"] = fmt_dt(end_dt + shift, end_z)

        if original["online_meeting"]:
            vendor = domain.split(".")[0]
            slug = f"{vendor}-evt-{id_num(event_id):06d}"
            record["online_meeting"] = {
                "conference_id": slug,
                "join_url": f"https://meet.{domain}/{slug}",
            }

        clones.append(record)
    return clones


# ----------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument(
        "--canonical",
        choices=("nonoise", "detelled"),
        default="nonoise",
        help="which copy of the 98/58 storyline records to pin into each output",
    )
    args = parser.parse_args()

    canon_messages = load(CANON_MESSAGES)
    canon_events = load(CANON_EVENTS)
    max_messages = load(MAX_MESSAGES)
    max_events = load(MAX_EVENTS)

    message_ids = {r["id"] for r in canon_messages}
    event_ids = {r["id"] for r in canon_events}

    if args.canonical == "detelled":
        by_id = {r["id"]: r for r in max_messages}
        canon_messages = [by_id[r["id"]] for r in canon_messages]
        by_id = {r["id"]: r for r in max_events}
        canon_events = [by_id[r["id"]] for r in canon_events]

    message_pool = [r for r in max_messages if r["id"] not in message_ids]
    event_pool = [r for r in max_events if r["id"] not in event_ids]

    print(f"canonical copy : {args.canonical}")
    print(f"seed           : {args.seed}")
    print(f"message pool   : {len(message_pool)}   event pool: {len(event_pool)}\n")

    for level, target in LEVELS.items():
        rng = random.Random(f"{args.seed}:{level}")
        if level.startswith("email"):
            records = build_messages(canon_messages, message_pool, target, rng)
            out = OUT_ROOT / level / "messages.json"
        else:
            records = build_events(canon_events, event_pool, target, rng)
            out = OUT_ROOT / level / "events.json"
        dump(out, records)
        noise = len(records) - (
            len(canon_messages) if level.startswith("email") else len(canon_events)
        )
        print(
            f"{level:17} total={len(records):6}  canonical={len(records)-noise:4}"
            f"  noise={noise:6}  -> {out.relative_to(REPO)}"
        )


if __name__ == "__main__":
    main()
