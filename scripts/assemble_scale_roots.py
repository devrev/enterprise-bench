#!/usr/bin/env python3
"""Assemble per-pairing DATA_DIR roots for the MCP servers.

Each server resolves its dataset as ``DATA_DIR/<subdir>``:

    calendar_json_data/  events.json, transcripts.json
    email_json_data/     messages.json, labels.json, attachments.json

Only events.json / messages.json change between scale levels; the auxiliary files
(transcripts, labels, attachments) are shared. Everything is symlinked so the
24,500-message level does not cost another 128 MB on disk.

Usage:
    python3 scripts/assemble_scale_roots.py
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOTS = REPO / "data/scaled/_roots"

# Auxiliary files that are identical at every scale.
SHARED = {
    "calendar_json_data": ["transcripts.json"],
    "email_json_data": ["labels.json", "attachments.json"],
}
SHARED_SRC = {
    "calendar_json_data": REPO / "data/calendar_json_data",
    "email_json_data": REPO / "data/email_json_data",
}

# pairing -> (messages.json source, events.json source)
PAIRINGS = {
    "base_base": (
        REPO / "data/no_noise_email_calendar/messages.json",
        REPO / "data/no_noise_email_calendar/events.json",
    ),
    "mid_mid": (
        REPO / "data/scaled/email_mid/messages.json",
        REPO / "data/scaled/calendar_mid/events.json",
    ),
    "max_max": (
        REPO / "data/email_json_data/messages.json",
        REPO / "data/calendar_json_data/events.json",
    ),
    "beyond_beyond": (
        REPO / "data/scaled/email_beyond/messages.json",
        REPO / "data/scaled/calendar_beyond/events.json",
    ),
}


def link(src: Path, dst: Path) -> None:
    if not src.exists():
        raise SystemExit(f"missing source: {src}")
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src)


def main() -> None:
    for pairing, (messages, events) in PAIRINGS.items():
        root = ROOTS / pairing
        for subdir, names in SHARED.items():
            target = root / subdir
            target.mkdir(parents=True, exist_ok=True)
            for name in names:
                link(SHARED_SRC[subdir] / name, target / name)

        link(events, root / "calendar_json_data/events.json")
        link(messages, root / "email_json_data/messages.json")
        print(f"{pairing:15} -> {root.relative_to(REPO)}")

    print(f"\nroots assembled under {ROOTS.relative_to(REPO)}")


if __name__ == "__main__":
    main()
