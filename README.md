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
| `parse_iostat.py` | Parses raw iostat output into SQLite |
| `iostat_report.py` | Reads SQLite and generates health report |
| `*.db` | SQLite database (generated) |
| `*-badness.txt` | Final report output |

---

## 🚀 Usage

### Step 1 — Parse iostat output
```bash
python3 parse_iostat.py -i iostat_output.txt
```

This creates:
```
iostat_output-iostat.db
```

---

### Step 2 — Generate the report
```bash
python3 iostat_report.py -i iostat_output-iostat.db
```

Output:
```
iostat_output-iostat-badness.txt
```

---

## 📊 Example Output

```
time                 device  write_kB_s  write_await_ms  read_kB_s  read_await_ms  queue_size  badness_score  health     dominance
-------------------  ------  -----------  --------------  ---------  -------------  ----------  -------------  ---------  -----------
2025-02-12 17:25:30  sdb     2362.14      5.08            0.76       0.59           10.7        82             CRITICAL   queue-dominant
```

---

## 🧮 How the Badness Score Works (Simple Explanation)

### Two things matter most for disk health:

### 1️⃣ Latency (how long a request takes)
Measured by:
- `r_await`
- `w_await`

Think of this as:
> “How long does one disk request take?”

---

### 2️⃣ Queue Size (how many requests are waiting)
Measured by:
- `aqu-sz`

Think of this as:
> “How many people are waiting in line?”

---

## 🧠 Simple Analogy (9th-Grade Level)

Imagine a grocery store:

| Metric | Meaning |
|------|------|
| Latency | How fast the cashier scans items |
| Queue | How many people are waiting |
| Badness | How bad the checkout experience is |

### Examples:
- Fast cashier + short line → ✅ good
- Slow cashier + short line → ⚠️ slow
- Fast cashier + long line → 🚨 overloaded
- Slow cashier + long line → 🔥 disaster

---

## 📐 Badness Score Formula

### Latency penalty (L)

```
if latency ≤ 2 ms:           0
if 2–10 ms:                  latency - 2
if 10–40 ms:                 8 + 2 × (latency - 10)
if > 40 ms:                  8 + 60 + 4 × (latency - 40)
```

### Queue penalty (Q)

```
if queue ≤ 4:                0
if 4–8:                      3 × (queue - 4)
if 8–16:                     12 + 5 × (queue - 8)
if > 16:                     12 + 40 + 8 × (queue - 16)
```

### Final Score

```
Badness Score = L + Q
```

---

## 📊 Score Meaning

| Score | Meaning |
|------|--------|
| 0–9 | OK |
| 10–29 | WARN |
| 30–59 | DEGRADED |
| 60+ | CRITICAL |

---

## 🧭 Dominance Label

The tool also tells **what caused the problem**:

| Label | Meaning |
|------|--------|
| `latency-dominant` | Disk is slow |
| `queue-dominant` | Too many requests |
| `balanced` | Both contribute |

---

## 🚨 Important Behavior

✔ Ignores noise  
✔ Ignores sub-5ms latency  
✔ Highlights overload early  
✔ Works for SSD / NVMe / HDD  
✔ No dependencies  
✔ Safe for automation  

---

## 📌 Why This Matters

A disk can look “fine” while being overloaded.

This tool catches:
- Silent performance degradation
- Queue buildup before latency explodes
- Bottlenecks hidden by averages

---

## ✅ Summary

✔ Easy to run  
✔ Easy to understand  
✔ Explains problems clearly  
✔ Suitable for reports, tickets, or monitoring  
✔ Designed for real-world operations  

---

If you want next:
- CSV / JSON output
- Trend graphs
- Alert thresholds
- Per-host summaries
- Integration with Grafana

Just ask.
