"""Rank CRM accounts by open P1 support tickets in the v2 dataset."""

import argparse
import csv
import json
import sqlite3
import sys
from contextlib import closing
from io import TextIOWrapper
from pathlib import Path
from zipfile import BadZipFile, ZipFile

DEFAULT_ARCHIVE = Path(__file__).resolve().parents[2] / "artifacts" / "data.zip"
READ_SIZE = 16 * 1024
JSON_WHITESPACE = " \t\r\n"
QUERY = """
SELECT accounts.account_id,
       accounts.account_name,
       accounts.arr,
       COUNT(*) AS open_p1_tickets
FROM support_tickets AS tickets
JOIN crm_accounts AS accounts ON accounts.account_id = tickets.account_id
WHERE tickets.priority = 'p1' AND tickets.status = 'open'
GROUP BY accounts.account_id, accounts.account_name, accounts.arr
ORDER BY open_p1_tickets DESC, accounts.account_id
LIMIT 10
"""


def skip_json_whitespace(source: TextIOWrapper, buffer: str = "") -> str:
    buffer = buffer.lstrip(JSON_WHITESPACE)
    while not buffer:
        chunk = source.read(READ_SIZE)
        if not chunk:
            return ""
        buffer = chunk.lstrip(JSON_WHITESPACE)
    return buffer


def fixture_rows(archive: ZipFile, member: str):
    """Read a fixture's JSON array one record at a time."""
    decoder = json.JSONDecoder()
    with TextIOWrapper(archive.open(member), encoding="utf-8") as source:
        buffer = skip_json_whitespace(source)
        if not buffer.startswith("["):
            raise ValueError(f"{member}: expected a JSON array")
        buffer = buffer[1:]
        separator = ""

        while True:
            buffer = skip_json_whitespace(source, buffer)
            if not buffer:
                raise ValueError(f"{member}: incomplete JSON array")
            if buffer.startswith("]"):
                if skip_json_whitespace(source, buffer[1:]):
                    raise ValueError(f"{member}: unexpected text after JSON array")
                return
            if not buffer.startswith(separator):
                raise ValueError(f"{member}: expected a comma between records")
            buffer = skip_json_whitespace(source, buffer[len(separator) :])

            while True:
                try:
                    record, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    chunk = source.read(READ_SIZE)
                    if not chunk:
                        raise ValueError(f"{member}: invalid JSON record") from None
                    buffer += chunk
                else:
                    break

            if not isinstance(record, dict):
                raise ValueError(f"{member}: expected a JSON object")
            yield record
            buffer = buffer[end:]
            separator = ","


def load_fixtures(db: sqlite3.Connection, archive: ZipFile) -> None:
    db.executescript("""
        CREATE TABLE crm_accounts (
            account_id TEXT PRIMARY KEY NOT NULL,
            account_name TEXT,
            arr REAL
        );
        CREATE TABLE support_tickets (
            account_id TEXT,
            priority TEXT,
            status TEXT
        );
    """)
    accounts = fixture_rows(archive, "crm_json_data/accounts.json")
    db.executemany(
        "INSERT INTO crm_accounts VALUES (?, ?, ?)",
        ((row["account_id"], row["account_name"], row["arr"]) for row in accounts),
    )
    tickets = fixture_rows(archive, "cs_json_data/tickets.json")
    db.executemany(
        "INSERT INTO support_tickets VALUES (?, ?, ?)",
        ((row["account_id"], row["priority"], row["status"]) for row in tickets),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE, help="path to v2 data.zip")
    args = parser.parse_args()

    try:
        with ZipFile(args.archive) as archive, closing(sqlite3.connect(":memory:")) as db:
            load_fixtures(db, archive)
            result = db.execute(QUERY)
            sys.stdout.reconfigure(newline="")
            writer = csv.writer(sys.stdout)
            writer.writerow(column[0] for column in result.description)
            writer.writerows(result)
    except (OSError, BadZipFile, KeyError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{parser.prog}: {args.archive}: {exc}\n")


if __name__ == "__main__":
    main()
