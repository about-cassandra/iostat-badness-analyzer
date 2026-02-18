#!/usr/bin/env python3
"""
Unified iostat badness analyzer

Combines parsing iostat output to SQLite with generating a badness report.

This script provides two main functions:
1. Parse iostat device tables into a SQLite database (with timestamps)
2. Generate a badness report from the SQLite database

Supports:
- Extended (-x) and basic (-d) device reports
- Column order changes and header variants
- Timestamp parsing and ISO 8601 formatted timestamps
- Badness scoring based on latency and queue depth
- Dominance classification (latency vs queue)

Changes from original scripts:
- Excludes rows where max await < 5 ms
- Uses refactored latency + queue model
- Includes dominance classification
- Outputs aligned text table

Usage:
  python3 iostat-badness-analyzer.py [parse|report|all] [--input INPUT] [--output OUTPUT] [--report-output REPORT]

Commands:
  parse       Parse iostat files to SQLite database
  report      Generate report from SQLite database
  all         Parse and generate report (default)

Parse options:
  --input INPUT        Path to iostat output text file (for parse)
  --output OUTPUT      Output SQLite database file (for parse)

Report options:
  --database DATABASE  SQLite database to read (for report)
  --report-output REPORT  Output text file for report

Examples:
  # Parse iostat file to database
  python3 iostat-badness-analyzer.py parse --input iostat-output.txt

  # Generate report from database
  python3 iostat-badness-analyzer.py report --database output.db

  # Do both (parse then generate report)
  python3 iostat-badness-analyzer.py all --input iostat-output.txt

  # Parse with custom output name
  python3 iostat-badness-analyzer.py parse --input input.txt --output mydb.db

  # Generate report to custom file
  python3 iostat-badness-analyzer.py report --database mydb.db --report-output my-report.txt
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Dict, List, Optional, Tuple, Sequence, Any
import argparse
import json
import re
import sqlite3
import sys
import logging
import os
from pathlib import Path
from datetime import datetime


# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Cache for header normalization
_header_normalize_cache: Dict[str, Tuple[str, str]] = {}

# Cache for fingerprint generation
_fingerprint_cache: Dict[Tuple[str, ...], str] = {}

# Cache for header mapping computation
_header_mapping_cache: Dict[Tuple[str, ...], Tuple[List[str], List[str]]] = {}

CANONICAL_ORDER = [
    "device",
    "tps",
    "kB_read/s",
    "kB_wrtn/s",
    "kB_dscd/s",
    "kB_w+d/s",
    "kB_read",
    "kB_wrtn",
    "kB_dscd",
    "kB_w+d",
    "r/s",
    "w/s",
    "d/s",
    "f/s",
    "rkB/s",
    "wkB/s",
    "dkB/s",
    "rrqm/s",
    "wrqm/s",
    "drqm/s",
    "%rrqm",
    "%wrqm",
    "%drqm",
    "areq-sz",
    "rareq-sz",
    "wareq-sz",
    "dareq-sz",
    "await",
    "r_await",
    "w_await",
    "d_await",
    "f_await",
    "aqu-sz",
    "svctm",
    "%util",
]

ALIASES: Dict[str, str] = {
    "Device": "device",
    "Device:": "device",
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
    "r/s": "r/s",
    "w/s": "w/s",
    "d/s": "d/s",
    "f/s": "f/s",
    "rkB/s": "rkB/s",
    "wkB/s": "wkB/s",
    "dkB/s": "dkB/s",
    "rMB/s": "rkB/s",
    "wMB/s": "wkB/s",
    "dMB/s": "dkB/s",
    "rsec/s": "rkB/s",
    "wsec/s": "wkB/s",
    "dsec/s": "dkB/s",
    "rrqm/s": "rrqm/s",
    "wrqm/s": "wrqm/s",
    "drqm/s": "drqm/s",
    "%rrqm": "%rrqm",
    "%wrqm": "%wrqm",
    "%drqm": "%drqm",
    "areq-sz": "areq-sz",
    "avgrq-sz": "areq-sz",
    "rareq-sz": "rareq-sz",
    "wareq-sz": "wareq-sz",
    "dareq-sz": "dareq-sz",
    "await": "await",
    "r_await": "r_await",
    "w_await": "w_await",
    "d_await": "d_await",
    "f_await": "f_await",
    "aqu-sz": "aqu-sz",
    "avgqu-sz": "aqu-sz",
    "svctm": "svctm",
    "%util": "%util",
}

THROUGHPUT_MB_HEADERS = {"rMB/s", "wMB/s", "dMB/s"}
SECTOR_RATE_HEADERS = {"rsec/s", "wsec/s", "dsec/s"}
BASIC_MB_HEADERS = {
    "MB_read/s",
    "MB_wrtn/s",
    "MB_dscd/s",
    "MB_w+d/s",
    "MB_read",
    "MB_wrtn",
    "MB_dscd",
    "MB_w+d",
}

SECTOR_SIZE_BYTES = 512.0
KIB_BYTES = 1024.0


# === Report module: badness report from SQLite =============================

REPORT_QUERY = """
WITH scored AS (
  SELECT
    source_file,
    sample_id,
    sample_ts,
    device,

    CAST(json_extract(metrics_json, '$."wkB/s"') AS REAL)    AS write_kB_s,
    CAST(json_extract(metrics_json, '$."w_await"') AS REAL)  AS w_await_ms,

    CAST(json_extract(metrics_json, '$."rkB/s"') AS REAL)    AS read_kB_s,
    CAST(json_extract(metrics_json, '$."r_await"') AS REAL)  AS r_await_ms,

    CAST(json_extract(metrics_json, '$."aqu-sz"') AS REAL)   AS queue_size,

    MAX(
      COALESCE(json_extract(metrics_json, '$."r_await"'), 0),
      COALESCE(json_extract(metrics_json, '$."w_await"'), 0)
    ) AS worst_await_ms
  FROM iostat_device_rows
),

parts AS (
  SELECT
    source_file,
    sample_id,
    sample_ts,
    device,
    write_kB_s,
    w_await_ms,
    read_kB_s,
    r_await_ms,
    queue_size,

    CASE
      WHEN worst_await_ms <= 2  THEN 0
      WHEN worst_await_ms <= 10 THEN (worst_await_ms - 2)
      WHEN worst_await_ms <= 40 THEN 8 + 2 * (worst_await_ms - 10)
      ELSE                           8 + 60 + 4 * (worst_await_ms - 40)
    END AS latency_penalty,

    CASE
      WHEN queue_size <= 4  THEN 0
      WHEN queue_size <= 8  THEN 3 * (queue_size - 4)
      WHEN queue_size <= 16 THEN 12 + 5 * (queue_size - 8)
      ELSE                       12 + 40 + 8 * (queue_size - 16)
    END AS queue_penalty
  FROM scored
  WHERE worst_await_ms >= 5
),

badness AS (
  SELECT
    source_file,
    sample_id,
    sample_ts,
    device,
    write_kB_s,
    w_await_ms,
    read_kB_s,
    r_await_ms,
    queue_size,

    CAST(
      ROUND(
        MIN(100, latency_penalty + queue_penalty)
      ) AS INTEGER
    ) AS badness_score,

    CASE
      WHEN latency_penalty > queue_penalty THEN 'latency-dominant'
      WHEN queue_penalty > latency_penalty THEN 'queue-dominant'
      ELSE 'balanced'
    END AS dominance
  FROM parts
)

SELECT
  REPLACE(sample_ts, 'T', ' ') AS time,
  device,
  write_kB_s,
  w_await_ms  AS write_await_ms,
  read_kB_s,
  r_await_ms  AS read_await_ms,
  queue_size,
  badness_score,
  CASE
    WHEN badness_score < 10 THEN 'OK'
    WHEN badness_score < 30 THEN 'WARN'
    WHEN badness_score < 60 THEN 'DEGRADED'
    ELSE 'CRITICAL'
  END AS health,
  dominance
FROM badness
ORDER BY sample_ts, sample_id, device;
"""


def _stringify(v: Any) -> str:
    """
    Convert a value to string, with special handling for floats.

    Args:
        v: Value to convert

    Returns:
        String representation of the value
    """
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """
    Format rows into a aligned text table.

    Args:
        headers: List of column headers
        rows: List of row data

    Returns:
        Formatted table as string
    """
    str_rows = [[_stringify(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]

    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    sep = "-" * (sum(widths) + (2 * (len(widths) - 1)))

    out = [fmt.format(*headers), sep]
    for row in str_rows:
        out.append(fmt.format(*row))

    return "\n".join(out) + "\n"


# === Parsing helpers =======================================================


def _to_float(s: str) -> Optional[float]:
    """
    Convert string to float, handling special cases.

    Args:
        s: String to convert

    Returns:
        Float value or None if conversion fails
    """
    s = s.strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _convert_value(
    original_header: str, canonical_header: str, value: Optional[float]
) -> Optional[float]:
    if value is None:
        return None

    if original_header in BASIC_MB_HEADERS and canonical_header.startswith("kB_"):
        return value * 1024.0

    if original_header in THROUGHPUT_MB_HEADERS and canonical_header in {
        "rkB/s",
        "wkB/s",
        "dkB/s",
    }:
        return value * 1024.0

    if original_header in SECTOR_RATE_HEADERS and canonical_header in {
        "rkB/s",
        "wkB/s",
        "dkB/s",
    }:
        return value * SECTOR_SIZE_BYTES / KIB_BYTES

    if original_header == "avgrq-sz" and canonical_header == "areq-sz":
        return value * SECTOR_SIZE_BYTES / KIB_BYTES

    return value


# Timestamp parsing

TS_RE = re.compile(r"^\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+[AP]M)\s*$")


def parse_timestamp_to_iso8601(line: str) -> Optional[str]:
    if "/" not in line:
        return None
    m = TS_RE.match(line)
    if not m:
        return None
    ts_str = m.group(1)
    dt = datetime.strptime(ts_str, "%m/%d/%Y %I:%M:%S %p")
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# Parsing logic

HEADER_RE = re.compile(r"^\s*Device(?:\:)?\s+.+\S\s*$")
CPU_HEADER_RE = re.compile(r"^\s*avg-cpu:\s*")


@dataclass
class IostatRecord:
    sample_id: int
    sample_ts: Optional[str]
    device: str
    metrics: Dict[str, Optional[float]]
    header_fingerprint: str


def _normalize_header_tokens(header_tokens: List[str]) -> List[Tuple[str, str]]:
    """
    Normalize header tokens with caching to avoid repeated computation.

    Args:
        header_tokens: List of raw header tokens

    Returns:
        List of (original_token, normalized_token) tuples
    """
    # Optimize: precompute the normalized tokens in a single pass
    # Also implement caching to avoid reprocessing same header tokens
    normalized = []
    for tok in header_tokens:
        if tok in _header_normalize_cache:
            normalized.append(_header_normalize_cache[tok])
        else:
            norm = (tok, ALIASES.get(tok, tok))
            _header_normalize_cache[tok] = norm
            normalized.append(norm)
    return normalized


def _split_device_sections(
    lines: Iterable[str],
) -> List[Tuple[int, Optional[str], List[str], List[List[str]], str]]:
    """Returns list of: (sample_id, sample_ts_iso8601, header_tokens, row_tokens_list, fingerprint)"""
    sections: List[Tuple[int, Optional[str], List[str], List[List[str]], str]] = []
    header: Optional[List[str]] = None
    rows: List[List[str]] = []
    sample_id = -1
    current_ts: Optional[str] = None
    section_ts: Optional[str] = None

    def flush():
        nonlocal header, rows, section_ts
        if header is not None and rows:
            # Create fingerprint once and cache it
            header_tuple = tuple(header)
            if header_tuple in _fingerprint_cache:
                fingerprint = _fingerprint_cache[header_tuple]
            else:
                fingerprint = "|".join(header)
                _fingerprint_cache[header_tuple] = fingerprint
            sections.append((sample_id, section_ts, header, rows, fingerprint))
        header = None
        rows = []
        section_ts = None

    for line in lines:
        line = line.rstrip("\n")

        iso = parse_timestamp_to_iso8601(line)
        if iso is not None:
            current_ts = iso
            continue

        if HEADER_RE.match(line):
            flush()
            sample_id += 1
            header = line.split()
            rows = []
            section_ts = current_ts
            continue

        if header is None:
            continue

        if not line.strip() or CPU_HEADER_RE.match(line):
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


def parse_iostat_lines(lines: Iterable[str]) -> Iterable[IostatRecord]:
    sections = _split_device_sections(lines)

    for sample_id, sample_ts, header_tokens, rows, fingerprint in sections:
        # Optimize: precompute header mappings to avoid repeated computation
        header_tuple = tuple(header_tokens)
        if header_tuple in _header_mapping_cache:
            orig_by_idx, canon_by_idx = _header_mapping_cache[header_tuple]
        else:
            header_map = _normalize_header_tokens(header_tokens)
            orig_by_idx = [o for (o, _c) in header_map]
            canon_by_idx = [c for (_o, c) in header_map]
            _header_mapping_cache[header_tuple] = (orig_by_idx, canon_by_idx)

        for row in rows:
            device = row[0]
            metrics: Dict[str, Optional[float]] = {}

            seen: Dict[str, int] = {}

            for i, raw_val in enumerate(row):
                orig = orig_by_idx[i]
                canon = canon_by_idx[i]

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

            yield IostatRecord(
                sample_id=sample_id,
                sample_ts=sample_ts,
                device=device,
                metrics=metrics,
                header_fingerprint=fingerprint,
            )


def parse_iostat_text(text: str) -> List[IostatRecord]:
    return list(parse_iostat_lines(text.splitlines()))


# SQLite output

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS iostat_device_rows (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file        TEXT NOT NULL,
  sample_id          INTEGER NOT NULL,
  sample_ts          TEXT,
  header_fingerprint TEXT NOT NULL,
  device             TEXT NOT NULL,
  metrics_json       TEXT NOT NULL
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
    # Security: Sanitize filename to prevent path traversal
    safe_stem = "".join(c for c in stem if c.isalnum() or c in ("-", "_", "."))
    if "iostat" in name.lower():
        out_name = f"{safe_stem}.db"
    else:
        out_name = f"{safe_stem}-iostat.db"
    return input_path.with_name(out_name)


def write_records_to_sqlite(
    db_path: Path,
    source_file: str,
    records: Iterable[IostatRecord],
    batch_size: int = 1000,
) -> int:
    """Write records in batches; returns rows written."""
    # Security: Validate output directory path
    if not str(db_path).startswith("/") and ".." in str(db_path):
        raise ValueError("Invalid output path - path traversal attempt detected")

    # Security: Ensure the database path is within a safe directory
    try:
        safe_dir = db_path.resolve().parent
        current_dir = Path.cwd().resolve()
        if safe_dir != current_dir:
            safe_dir.relative_to(current_dir)
    except ValueError:
        raise ValueError(
            f"Database path {db_path} is outside the working directory - security restriction"
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)

    insert_sql = """
        INSERT INTO iostat_device_rows
          (source_file, sample_id, sample_ts, header_fingerprint, device, metrics_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """

    def record_values(
        batch: Iterable[IostatRecord],
    ) -> Iterable[Tuple[str, int, Optional[str], str, str, str]]:
        for r in batch:
            yield (
                source_file,
                r.sample_id,
                r.sample_ts,
                r.header_fingerprint,
                r.device,
                json.dumps(r.metrics, separators=(",", ":")),
            )

    total = 0
    current_batch_size = max(100, min(5000, batch_size))  # Clamp between 100-5000
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.cursor()
        batch: List[IostatRecord] = []
        for record in records:
            batch.append(record)
            if len(batch) >= current_batch_size:
                cur.executemany(insert_sql, record_values(batch))
                total += len(batch)
                batch.clear()
        if batch:
            cur.executemany(insert_sql, record_values(batch))
            total += len(batch)
        conn.commit()

    return total


# === CLI ===================================================================


def parse_iostat_file(in_path: Path, out_db: Path, batch_size: int = 1000) -> int:
    """Parse iostat file and create SQLite database."""
    logger.info(f"Starting to parse {in_path}...")
    with in_path.open("r", encoding="utf-8", errors="replace") as handle:
        records = parse_iostat_lines(handle)
        rows_written = write_records_to_sqlite(
            out_db, source_file=str(in_path), records=records, batch_size=batch_size
        )
    logger.info(f"Completed parsing {in_path}. Rows written: {rows_written}")
    return rows_written


def generate_report(db_path: Path, out_path: Path) -> int:
    # Security: Validate paths before processing
    if ".." in str(db_path):
        raise ValueError(f"Invalid database path: {db_path}")
    if ".." in str(out_path):
        raise ValueError(f"Invalid output path: {out_path}")

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        cur.execute(REPORT_QUERY)
        rows = cur.fetchall()
        headers = [d[0] for d in cur.description]

    report = format_table(headers, rows)
    out_path.write_text(report, encoding="utf-8")
    return len(rows)


def parse_command(args: argparse.Namespace) -> int:
    """Parse iostat files and create SQLite database."""
    in_path = Path(args.input).expanduser().resolve()
    if not in_path.exists() or not in_path.is_file():
        raise SystemExit(f"Input file not found: {in_path}")

    # Security: Validate input path is not a directory traversal
    if ".." in str(in_path):
        raise SystemExit(f"Invalid input path: {in_path}")

    if args.output:
        out_db = Path(args.output).expanduser().resolve()
        # Security: Ensure output path is not in parent directories
        if ".." in str(out_db):
            raise SystemExit(f"Invalid output path: {out_db}")
    else:
        out_db = derive_output_db_path(in_path)

    # Make batch size configurable
    batch_size = getattr(args, "batch_size", 1000)

    rows_written = parse_iostat_file(in_path, out_db, batch_size)

    print(
        json.dumps(
            {
                "action": "parse",
                "input": str(in_path),
                "output_db": str(out_db),
                "rows_written": rows_written,
            }
        )
    )
    return 0


def report_command(args: argparse.Namespace) -> int:
    """Generate badness report from SQLite database."""
    db_path = Path(args.database).expanduser().resolve()
    # Security: Validate database path
    if ".." in str(db_path):
        raise SystemExit(f"Invalid database path: {db_path}")
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    if args.report_output:
        out_path = Path(args.report_output).expanduser().resolve()
        # Security: Validate output path
        if ".." in str(out_path):
            raise SystemExit(f"Invalid output path: {out_path}")
    else:
        out_path = db_path.with_name(f"{db_path.stem}-badness.txt")

    rows = generate_report(db_path, out_path)

    print(f"Report generated: {out_path}")
    print(f"Total rows: {rows}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified iostat badness analyzer - parse and report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Parse iostat file to database
  python3 iostat-badness-analyzer.py parse --input iostat-output.txt
  
  # Generate report from database
  python3 iostat-badness-analyzer.py report --database output.db
  
  # Do both (parse then generate report)
  python3 iostat-badness-analyzer.py all --input iostat-output.txt
  
  # Parse with custom output name
  python3 iostat-badness-analyzer.py parse --input input.txt --output mydb.db
  
  # Generate report to custom file
  python3 iostat-badness-analyzer.py report --database mydb.db --report-output my-report.txt
  
  # Parse with custom batch size
  python3 iostat-badness-analyzer.py parse --input input.txt --batch-size 2000
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Parse command
    parse_parser = subparsers.add_parser(
        "parse", help="Parse iostat files to SQLite database"
    )
    parse_parser.add_argument(
        "--input", required=True, help="Path to iostat output text file"
    )
    parse_parser.add_argument("--output", help="Output SQLite database file")
    parse_parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("IOSTAT_BATCH_SIZE", "1000")),
        help="Batch size for database inserts (default: 1000)",
    )

    # Report command
    report_parser = subparsers.add_parser(
        "report", help="Generate badness report from SQLite database"
    )
    report_parser.add_argument(
        "--database", required=True, help="SQLite database to read"
    )
    report_parser.add_argument("--report-output", help="Output text file for report")

    # All command
    all_parser = subparsers.add_parser("all", help="Parse iostat and generate report")
    all_parser.add_argument(
        "--input", required=True, help="Path to iostat output text file"
    )
    all_parser.add_argument("--db-output", help="Output SQLite database file")
    all_parser.add_argument("--report-output", help="Output text file for report")
    all_parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("IOSTAT_BATCH_SIZE", "1000")),
        help="Batch size for database inserts (default: 1000)",
    )

    args = parser.parse_args()

    # Default to 'all' if no command specified
    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "parse":
            return parse_command(args)
        elif args.command == "report":
            return report_command(args)
        elif args.command == "all":
            db_path = (
                Path(args.db_output).expanduser().resolve()
                if args.db_output
                else derive_output_db_path(Path(args.input).expanduser().resolve())
            )
            # Make batch size configurable for all command
            batch_size = getattr(
                args, "batch_size", int(os.getenv("IOSTAT_BATCH_SIZE", "1000"))
            )
            parse_iostat_file(
                Path(args.input).expanduser().resolve(), db_path, batch_size
            )
            report_path = (
                Path(args.report_output).expanduser().resolve()
                if args.report_output
                else db_path.with_name(f"{db_path.stem}-badness.txt")
            )
            generate_report(db_path, report_path)
            print(f"Report generated: {report_path}")
            return 0

    except SystemExit as e:
        return e.code
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
