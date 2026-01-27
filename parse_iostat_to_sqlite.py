#!/usr/bin/env python3
"""
iostat Device-table parser -> SQLite with per-sample timestamps

Supports:
- Extended (-x) and basic (-d) device reports
- Column order changes (header-driven mapping)
- Headings renamed in "previous versions":
  - avgrq-sz (sectors) -> areq-sz (KiB)
  - avgqu-sz -> aqu-sz
- Throughput variants:
  - rsec/s,wsec/s,dsec/s -> rkB/s,wkB/s,dkB/s (assume 512-byte sectors)
  - rMB/s,wMB/s,dMB/s -> rkB/s,wkB/s,dkB/s (MiB->KiB)
- Basic report variants:
  - Blk_read/s (kB_read/s, MB_read/s), etc.

Adds:
- Parses timestamp lines like: "02/12/2025 05:25:30 PM"
- Stores sample_ts in ISO 8601: "2025-02-12T17:25:30"

CLI:
  python3 parse_iostat.py -i <inputfile>

SQLite output naming:
- If "iostat" appears in input filename (case-insensitive), output "<stem>.db"
- Else output "<stem>-iostat.db"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Dict, List, Optional, Tuple
import argparse
import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime


# -----------------------------
# Canonical schema (edit as needed)
# -----------------------------

CANONICAL_ORDER = [
    "device",

    # basic report (-d) fields
    "tps",
    "kB_read/s", "kB_wrtn/s", "kB_dscd/s", "kB_w+d/s",
    "kB_read", "kB_wrtn", "kB_dscd", "kB_w+d",

    # extended report (-x) fields
    "r/s", "w/s", "d/s", "f/s",
    "rkB/s", "wkB/s", "dkB/s",
    "rrqm/s", "wrqm/s", "drqm/s",
    "%rrqm", "%wrqm", "%drqm",
    "areq-sz", "rareq-sz", "wareq-sz", "dareq-sz",
    "await", "r_await", "w_await", "d_await", "f_await",
    "aqu-sz",
    "svctm",
    "%util",
]


# -----------------------------
# Aliases: raw heading -> canonical heading
# -----------------------------

ALIASES: Dict[str, str] = {
    "Device": "device",
    "Device:": "device",

    # basic (-d) report headings
    "tps": "tps",

    "Blk_read/s": "kB_read/s",
    "kB_read/s": "kB_read/s",
    "MB_read/s": "kB_read/s",

    "Blk_wrtn/s": "kB_wrtn/s",
    "kB_wrtn/s": "kB_wrtn/s",
    "MB_wrtn/s": "kB_wrtn/s",

    "Blk_dscd/s": "kB_dscd/s",
    "kB_dscd/s": "kB_dscd/s",
    "MB_dscd/s": "kB_dscd/s",

    "Blk_w+d/s": "kB_w+d/s",
    "kB_w+d/s": "kB_w+d/s",
    "MB_w+d/s": "kB_w+d/s",

    "Blk_read": "kB_read",
    "kB_read": "kB_read",
    "MB_read": "kB_read",

    "Blk_wrtn": "kB_wrtn",
    "kB_wrtn": "kB_wrtn",
    "MB_wrtn": "kB_wrtn",

    "Blk_dscd": "kB_dscd",
    "kB_dscd": "kB_dscd",
    "MB_dscd": "kB_dscd",

    "Blk_w+d": "kB_w+d",
    "kB_w+d": "kB_w+d",
    "MB_w+d": "kB_w+d",

    # extended (-x) core headings
    "r/s": "r/s",
    "w/s": "w/s",
    "d/s": "d/s",
    "f/s": "f/s",

    # throughput variants (sectors/kB/MB)
    "rkB/s": "rkB/s",
    "wkB/s": "wkB/s",
    "dkB/s": "dkB/s",
    "rMB/s": "rkB/s",
    "wMB/s": "wkB/s",
    "dMB/s": "dkB/s",
    "rsec/s": "rkB/s",
    "wsec/s": "wkB/s",
    "dsec/s": "dkB/s",

    # merges
    "rrqm/s": "rrqm/s",
    "wrqm/s": "wrqm/s",
    "drqm/s": "drqm/s",
    "%rrqm": "%rrqm",
    "%wrqm": "%wrqm",
    "%drqm": "%drqm",

    # request sizes
    "areq-sz": "areq-sz",
    "avgrq-sz": "areq-sz",   # previous versions: sectors -> KiB conversion applied
    "rareq-sz": "rareq-sz",
    "wareq-sz": "wareq-sz",
    "dareq-sz": "dareq-sz",

    # latencies
    "await": "await",
    "r_await": "r_await",
    "w_await": "w_await",
    "d_await": "d_await",
    "f_await": "f_await",

    # queue depth (previous versions)
    "aqu-sz": "aqu-sz",
    "avgqu-sz": "aqu-sz",

    # deprecated / optional
    "svctm": "svctm",
    "%util": "%util",
}


# -----------------------------
# Unit conversion configuration
# -----------------------------

THROUGHPUT_MB_HEADERS = {"rMB/s", "wMB/s", "dMB/s"}
SECTOR_RATE_HEADERS = {"rsec/s", "wsec/s", "dsec/s"}
BASIC_MB_HEADERS = {
    "MB_read/s", "MB_wrtn/s", "MB_dscd/s", "MB_w+d/s",
    "MB_read", "MB_wrtn", "MB_dscd", "MB_w+d",
}

SECTOR_SIZE_BYTES = 512.0
KIB_BYTES = 1024.0


def _to_float(s: str) -> Optional[float]:
    s = s.strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _convert_value(original_header: str, canonical_header: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None

    # Basic report MB_* -> kB_*
    if original_header in BASIC_MB_HEADERS and canonical_header.startswith("kB_"):
        return value * 1024.0

    # Extended report rMB/s -> rkB/s
    if original_header in THROUGHPUT_MB_HEADERS and canonical_header in {"rkB/s", "wkB/s", "dkB/s"}:
        return value * 1024.0

    # Sector-rate -> kB/s
    if original_header in SECTOR_RATE_HEADERS and canonical_header in {"rkB/s", "wkB/s", "dkB/s"}:
        return value * SECTOR_SIZE_BYTES / KIB_BYTES

    # Previous versions: avgrq-sz (sectors) -> areq-sz (KiB)
    if original_header == "avgrq-sz" and canonical_header == "areq-sz":
        return value * SECTOR_SIZE_BYTES / KIB_BYTES

    return value


# -----------------------------
# Timestamp parsing
# -----------------------------

# Matches: 02/12/2025 05:25:30 PM
TS_RE = re.compile(r"^\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+[AP]M)\s*$")


def parse_timestamp_to_iso8601(line: str) -> Optional[str]:
    m = TS_RE.match(line)
    if not m:
        return None
    ts_str = m.group(1)
    # input is 12-hour clock with AM/PM
    dt = datetime.strptime(ts_str, "%m/%d/%Y %I:%M:%S %p")
    # ISO 8601, 24-hour clock, to seconds
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# -----------------------------
# Parsing logic
# -----------------------------

HEADER_RE = re.compile(r"^\s*Device(?:\:)?\s+.+\S\s*$")
CPU_HEADER_RE = re.compile(r"^\s*avg-cpu:\s*")
BLANK_RE = re.compile(r"^\s*$")


@dataclass
class IostatRecord:
    sample_id: int
    sample_ts: Optional[str]  # ISO 8601 "YYYY-MM-DDTHH:MM:SS"
    device: str
    metrics: Dict[str, Optional[float]]
    raw_columns: Dict[str, str]
    header_fingerprint: str


def _normalize_header_tokens(header_tokens: List[str]) -> List[Tuple[str, str]]:
    return [(tok, ALIASES.get(tok, tok)) for tok in header_tokens]


def _split_device_sections(lines: Iterable[str]) -> List[Tuple[int, Optional[str], List[str], List[List[str]]]]:
    """
    Returns list of:
      (sample_id, sample_ts_iso8601, header_tokens, row_tokens_list)

    sample_ts is derived from the nearest preceding timestamp line like:
      MM/DD/YYYY HH:MM:SS AM|PM
    """
    sections: List[Tuple[int, Optional[str], List[str], List[List[str]]]] = []
    header: Optional[List[str]] = None
    rows: List[List[str]] = []
    sample_id = -1
    current_ts: Optional[str] = None
    section_ts: Optional[str] = None

    def flush():
        nonlocal header, rows, section_ts
        if header is not None and rows:
            sections.append((sample_id, section_ts, header, rows))
        header = None
        rows = []
        section_ts = None

    for line in lines:
        line = line.rstrip("\n")

        # Capture timestamp lines that apply to the next block
        iso = parse_timestamp_to_iso8601(line)
        if iso is not None:
            current_ts = iso
            continue

        if HEADER_RE.match(line):
            flush()
            sample_id += 1
            header = line.split()
            rows = []
            section_ts = current_ts  # attach most recently seen timestamp
            continue

        if header is None:
            continue

        if BLANK_RE.match(line) or CPU_HEADER_RE.match(line):
            flush()
            continue

        tokens = line.split()
        if not tokens:
            continue

        if len(tokens) < len(header):
            tokens = tokens + [""] * (len(header) - len(tokens))
        elif len(tokens) > len(header):
            tokens = tokens[: len(header)]

        rows.append(tokens)

    flush()
    return sections


def parse_iostat_text(text: str) -> List[IostatRecord]:
    records: List[IostatRecord] = []
    sections = _split_device_sections(text.splitlines())

    for sample_id, sample_ts, header_tokens, rows in sections:
        header_map = _normalize_header_tokens(header_tokens)
        orig_by_idx = [o for (o, _c) in header_map]
        canon_by_idx = [c for (_o, c) in header_map]
        fingerprint = "|".join(header_tokens)

        for row in rows:
            device = row[0]
            raw_cols: Dict[str, str] = {}
            metrics: Dict[str, Optional[float]] = {}

            seen: Dict[str, int] = {}

            for i, raw_val in enumerate(row):
                orig = orig_by_idx[i]
                canon = canon_by_idx[i]
                raw_cols[orig] = raw_val

                if canon == "device":
                    continue

                v = _to_float(raw_val)
                v = _convert_value(orig, canon, v)

                if canon in seen:
                    seen[canon] += 1
                    canon_key = f"{canon}__dup{seen[canon]}"
                else:
                    seen[canon] = 1
                    canon_key = canon

                metrics[canon_key] = v

            records.append(
                IostatRecord(
                    sample_id=sample_id,
                    sample_ts=sample_ts,
                    device=device,
                    metrics=metrics,
                    raw_columns=raw_cols,
                    header_fingerprint=fingerprint,
                )
            )

    return records


# -----------------------------
# SQLite output
# -----------------------------

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS iostat_device_rows (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file        TEXT NOT NULL,
  sample_id          INTEGER NOT NULL,
  sample_ts          TEXT,
  header_fingerprint TEXT NOT NULL,
  device             TEXT NOT NULL,
  metrics_json       TEXT NOT NULL,
  raw_columns_json   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_iostat_device_rows_source_sample_device
  ON iostat_device_rows (source_file, sample_id, device);

CREATE INDEX IF NOT EXISTS idx_iostat_device_rows_sample_ts
  ON iostat_device_rows (sample_ts);

CREATE INDEX IF NOT EXISTS idx_iostat_device_rows_device
  ON iostat_device_rows (device);
"""


def derive_output_db_path(input_path: Path) -> Path:
    name = input_path.name
    stem = input_path.stem
    if "iostat" in name.lower():
        out_name = f"{stem}.db"
    else:
        out_name = f"{stem}-iostat.db"
    return input_path.with_name(out_name)


def write_records_to_sqlite(db_path: Path, source_file: str, records: List[IostatRecord]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA_SQL)

        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO iostat_device_rows
              (source_file, sample_id, sample_ts, header_fingerprint, device, metrics_json, raw_columns_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    source_file,
                    r.sample_id,
                    r.sample_ts,
                    r.header_fingerprint,
                    r.device,
                    json.dumps(r.metrics, sort_keys=True),
                    json.dumps(r.raw_columns, sort_keys=True),
                )
                for r in records
            ],
        )
        conn.commit()
    finally:
        conn.close()


# -----------------------------
# CLI
# -----------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Parse iostat 'Device' tables and write rows to SQLite (with timestamps).")
    p.add_argument("-i", "--input", required=True, help="Path to iostat output text file.")
    args = p.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.exists() or not in_path.is_file():
        raise SystemExit(f"Input file not found: {in_path}")

    out_db = derive_output_db_path(in_path)

    text = in_path.read_text(errors="replace")
    records = parse_iostat_text(text)

    write_records_to_sqlite(out_db, source_file=str(in_path), records=records)

    # minimal stdout status
    print(json.dumps({"input": str(in_path), "output_db": str(out_db), "rows_written": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
