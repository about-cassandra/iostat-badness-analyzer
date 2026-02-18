# iostat Badness Analyzer

Analyzes Linux `iostat` output to produce human-readable disk health reports based on latency and queue depth. Answers the question: *"Is this disk slow, overloaded, or healthy?"*

Converts raw metrics into severity scores, classifies issues as latency-dominant or queue-dominant, and filters out sub-5ms noise. Pure Python, no dependencies.

---

## Usage

### Quick Start
```bash
iostat -x 2 3 > my-iostat.txt
python3 iostat-badness-analyzer.py all --input my-iostat.txt
```

### Separate Steps
```bash
# Parse iostat output into a SQLite database
python3 iostat-badness-analyzer.py parse --input iostat-output.txt

# Generate report from database
python3 iostat-badness-analyzer.py report --database iostat-output.db

# Custom output paths
python3 iostat-badness-analyzer.py report \
  --database my-disk-data.db \
  --report-output /reports/disk-health.txt
```

---

## Example Output

```
time                 device   write_kB_s  write_await_ms  read_kB_s  read_await_ms  queue_size  badness_score  health    dominance
-------------------  -------  ----------  --------------  ---------  -------------  ----------  -------------  --------  ----------------
2026-01-28 12:36:52  nvme0n1  4           24              0          0              0.01        36             DEGRADED  latency-dominant
2026-01-28 12:37:00  dm-1     95.27       12.27           27.09      0.83           0.07        13             WARN      latency-dominant
```

Health levels: `OK` (< 10) · `WARN` (10–29) · `DEGRADED` (30–59) · `CRITICAL` (≥ 60)

---

## Automation

### Cron
```bash
0 2 * * * iostat -x 2 3 > /var/log/iostat-daily.txt && \
  python3 /opt/iostat-badness-analyzer.py all --input /var/log/iostat-daily.txt
```

### Compare Time Periods
```bash
python3 iostat-badness-analyzer.py report --database before.db --report-output before.txt
python3 iostat-badness-analyzer.py report --database after.db --report-output after.txt
vimdiff before.txt after.txt
```
