# iostat Badness Analyzer

This project analyzes **Linux `iostat` output** and produces a **human-readable disk health report** based on latency and queue depth.

It is designed to answer one question clearly:

> **“Is this disk slow, overloaded, or healthy?”**

---

## 📦 What This Project Does

This project consists of **one combined workflow**:

1. **Parse iostat output**
   - Reads raw `iostat` text output
   - Extracts per-device metrics
   - Stores them in a SQLite database

2. **Analyze disk health**
   - Reads the SQLite database
   - Calculates a **Badness Score**
   - Classifies issues as:
     - `OK`
     - `WARN`
     - `DEGRADED`
     - `CRITICAL`
   - Labels whether problems are:
     - **latency-dominant**
     - **queue-dominant**

3. **Outputs a clean text report**
   - Easy to read
   - Fixed-width columns
   - Ready for email, tickets, or review

---

## 🧠 What Problem This Solves

`iostat` output is hard to interpret because:

- Low latency does **not** always mean healthy
- High queue depth often predicts future problems
- Raw numbers don’t explain *severity*
- Humans need interpretation, not metrics 

This tool:
- Converts raw stats into a **single severity score**
- Explains *why* performance is bad
- Filters out noise
- Highlights real bottlenecks

---

## 📁 Files

| File | Purpose |
|----|----|
| `iostat-badness-analyzer.py` | Combined parser and reporter |
| `*.db` | SQLite database (generated) |
| `*-badness.txt` | Final report output |

---

## 🚀 Usage

### Basic example - Parse and analyze immediately

```bash
# Generate iostat output
iostat -x 2 3 > my-iostat.txt

# Parse and generate report (combined command)
python3 iostat-badness-analyzer.py all --input my-iostat.txt

# Result files appear automatically:
#   my-iostat.db (SQLite database)
#   my-iostat-badness.txt (report)
```

### Separate steps - Parse first, then report later

```bash
# Step 1: Parse iostat to database
python3 iostat-badness-analyzer.py parse --input iostat-output.txt

# Step 2: Generate report from database
python3 iostat-badness-analyzer.py report --database iostat-output.db

# The report outputs to: iostat-output-badness.txt
```

### Generate report to custom file

```bash
python3 iostat-badness-analyzer.py report \
  --database my-disk-data.db \
  --report-output /reports/disk-health.txt
```

---

## 📊 Example Output

```
time                 device  write_kB_s  write_await_ms  read_kB_s  read_await_ms  queue_size  badness_score  health     dominance
-------------------  ------  -----------  --------------  ---------  -------------  ----------  -------------  ---------  -----------
2026-01-28 12:36:52  nvme0n1  4           24              0          0              0.01        36             DEGRADED  latency-dominant
2026-01-28 12:37:00  dm-1     95.27       12.27           27.09      0.83           0.07        13             WARN      latency-dominant
```

---

## 🚨 Important Behavior

✔  Ignores noise  
✔  Ignores sub-5ms latency  
✔  Highlights overload early  
✔  Works for SSD / NVMe / HDD  
✔  No dependencies  
✔  Safe for automation  

---

## 📌 Why This Matters

A disk can look “fine” while being overloaded.

This tool catches:
- Silent performance degradation
- Queue buildup before latency explodes
- Bottlenecks hidden by averages

---

## ✅ Summary

✔  Easy to run  
✔  Easy to understand  
✔  Explains problems clearly  
✔  Suitable for reports, tickets, or monitoring  
✔  Designed for real-world operations  

---

## 💡 Example Command Sequence

```bash
# Step 1: Get fresh iostat data
iostat -x -c -d -t 1 5 > disk-check.txt

# Step 2: Parse and analyze (everything in one command)
python3 iostat-badness-analyzer.py all --input disk-check.txt

# Step 3: Check the report
less disk-check-badness.txt

# Step 4: Send to monitoring or save for review
./disk-check-badness.txt >> monthly_disk_reports/$(date +%Y-%m).txt
```

---

## 🧭 Tips and Tricks

### Multiple disks?
Run once per disk or group:
```bash
iostat -x -d nvme0n1 2 3 > nvme-stats.txt
```

### Automate with cron
```bash
0 2 * * * iostat -x 2 3 > /var/log/iostat-daily.txt && \
  python3 /opt/iostat-badness-analyzer.py all --input /var/log/iostat-daily.txt
```

### Compare two time periods
```bash
# Morning
python3 iostat-badness-analyzer.py report --database before.db --report-output before.txt

# Afternoon
python3 iostat-badness-analyzer.py report --database after.db --report-output after.txt

# Compare
vimdiff before.txt after.txt
```


