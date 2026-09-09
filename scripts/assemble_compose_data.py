#!/usr/bin/env python3
"""Materialise real-file email/calendar mount dirs for the MCP compose stack.

The scale roots under data/scaled/_roots/ are symlinked, which is fine for the
in-process test harness but not for Docker: a bind mount does not follow a
symlink that points outside the mounted tree. This writes real copies instead.

Each output dir is a drop-in replacement for one server's dataset directory:

    <out>/email_json_data/     messages.json, labels.json, attachments.json
    <out>/calendar_json_data/  events.json, transcripts.json

Usage:
    python3 scripts/assemble_compose_data.py --email mid --calendar base
    python3 scripts/assemble_compose_data.py --email max --calendar beyond --name my_run
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO / "data/scaled/_compose"

EMAIL_SOURCES = {
    "base": REPO / "data/no_noise_email_calendar/messages.json",
    "mid": REPO / "data/scaled/email_mid/messages.json",
    "max": REPO / "data/email_json_data/messages.json",
    "beyond": REPO / "data/scaled/email_beyond/messages.json",
}
CALENDAR_SOURCES = {
    "base": REPO / "data/no_noise_email_calendar/events.json",
    "mid": REPO / "data/scaled/calendar_mid/events.json",
    "max": REPO / "data/calendar_json_data/events.json",
    "beyond": REPO / "data/scaled/calendar_beyond/events.json",
}

# Authored once, shared by every scale.
EMAIL_AUX = REPO / "data/email_json_data"
CALENDAR_AUX = REPO / "data/calendar_json_data"


def copy(src: Path, dst: Path) -> None:
    if not src.exists():
        raise SystemExit(f"missing source: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", choices=sorted(EMAIL_SOURCES), required=True)
    parser.add_argument("--calendar", choices=sorted(CALENDAR_SOURCES), required=True)
    parser.add_argument("--name", help="output dir name (default: email_<e>__calendar_<c>)")
    args = parser.parse_args()

    name = args.name or f"email_{args.email}__calendar_{args.calendar}"
    out = OUT_ROOT / name

    copy(EMAIL_SOURCES[args.email], out / "email_json_data/messages.json")
    copy(EMAIL_AUX / "labels.json", out / "email_json_data/labels.json")
    copy(EMAIL_AUX / "attachments.json", out / "email_json_data/attachments.json")

    copy(CALENDAR_SOURCES[args.calendar], out / "calendar_json_data/events.json")
    copy(CALENDAR_AUX / "transcripts.json", out / "calendar_json_data/transcripts.json")

    import json

    n_msg = len(json.loads((out / "email_json_data/messages.json").read_text()))
    n_evt = len(json.loads((out / "calendar_json_data/events.json").read_text()))

    print(f"wrote {out.relative_to(REPO)}")
    print(f"  email_json_data/messages.json  {n_msg:>6} messages  (level: {args.email})")
    print(f"  calendar_json_data/events.json {n_evt:>6} events    (level: {args.calendar})")
    print()
    print("export these for compose:")
    print(f"  EMAIL_DATA_PATH={out / 'email_json_data'}")
    print(f"  CALENDAR_DATA_PATH={out / 'calendar_json_data'}")


if __name__ == "__main__":
    main()
