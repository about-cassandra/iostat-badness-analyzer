#!/usr/bin/env python3
"""
Generate an iostat "badness" report from the SQLite DB produced by the parser.

Changes:
- Excludes rows where max await < 5 ms
- Uses refactored latency + queue model
- Includes dominance classification
- Outputs aligned text table

Usage:
  python3 iostat_report.py -i host-iostat.db -o report.txt
"""

import argparse
import sqlite3
from pathlib import Path
from typing import List, Sequence, Any


QUERY = """
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
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate iostat badness report.")
    parser.add_argument("-i", "--input", required=True, help="SQLite DB file")
    parser.add_argument("-o", "--output", help="Output text file")
    args = parser.parse_args()

    db_path = Path(args.input).expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    out_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else db_path.with_name(f"{db_path.stem}-badness.txt")
    )

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(QUERY)
        rows = cur.fetchall()
        headers = [d[0] for d in cur.description]

        report = format_table(headers, rows)
        out_path.write_text(report, encoding="utf-8")

        print(f"Wrote report to: {out_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

