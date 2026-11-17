# Benchmark RocksDB LogStore and SQLite — 2026-10-27

## Environment and workload

- Windows 11 build 26200, Python 3.12.14, AMD64.
- CPU: Intel64 Family 6 Model 186 Stepping 2; 12 logical CPUs.
- RAM reported by Windows: 16,674,521,088 bytes.
- 200,000 generated events (`seed=1`), batch size 1,000, plus 1,000 single writes. Queries therefore ran against 201,000 events. Keyword search returned up to 100 matches and may stop once it reaches that limit; this is not a guaranteed full-dataset worst-case measurement. There were 100 query observations and three independent database builds for repeated batch-write throughput.
- SQLite used WAL and `synchronous=NORMAL`; RocksDB used its default asynchronous write setting. The raw JSON records all parameters and observed values.
- Benchmark databases were temporary and removed after the run. No demo database or backup was read or changed.

Run command:

```powershell
$result = Join-Path $env:TEMP 'logstore-realism-200k-measured.json'
python bench.py --n 200000 --single 1000 --repeat 100 --write-repeat 3 --json $result
Copy-Item $result docs/benchmark-results/2026-10-27-local-operations.json
```

## Results

| Operation | RocksDB | SQLite |
|---|---:|---:|
| Batch insert, first run | 75,974 events/s | 97,557 events/s |
| Batch insert, three independent runs (median; min–max) | 75,081 (70,714–81,445) events/s | 98,443 (92,574–101,484) events/s |
| Single-event writes | 22,143 events/s | 7,501 events/s |
| 10-minute range query p50 / p95 / p99 | 0.50 / 0.79 / 0.89 ms | 0.27 / 0.42 / 0.53 ms |
| `level >= ERROR` query p50 / p95 / p99 | 0.60 / 1.19 / 1.39 ms | 0.78 / 1.13 / 1.30 ms |
| Source query p50 / p95 / p99 | 0.75 / 1.29 / 2.14 ms | 0.35 / 0.59 / 0.70 ms |
| Message substring scan for `timeout`, up to 100 rows, p50 / p95 / p99 | 100.58 / 171.15 / 205.38 ms | 17.33 / 29.14 / 38.87 ms |
| Point read | 8.79 µs | 20.84 µs |
| Database size after flush/compaction | 19.73 MB | 35.21 MB |
| Purge older half of time range | 1,334.02 ms | 820.45 ms |

The query summaries use 100 observations and nearest-rank percentiles. Repeated write throughput varies substantially between database builds, especially for RocksDB, so the median and spread are more useful than a single first-run number. The JSON file contains unrounded values.

## Interpretation and decision

RocksDB performed better for single-event writes and point reads in this run, and used less disk after compaction. SQLite was faster for batch ingestion, range/source queries, and deleting the older half. Neither engine wins every operation.

Substring lookup is the clearest RocksDB weakness in this implementation: the current `contains` query scans event messages and decodes records in Python. At 201,000 loaded rows, the `timeout` query returned 100 matches in both stores. This limited query measured p99 at about 205 ms for RocksDB and 39 ms for SQLite. It can stop after finding 100 matches, so it is not a worst-case scan bound. Rare or absent terms can require scanning much more of the dataset.

This iteration does not add an inverted index. The measured limited-query p99 is about 205 ms for this workload, and the benchmark has not measured rare-term/worst-case latency or the write-amplification and storage cost of a trigram/posting index. If expected data grows well beyond this size or the search response target is below 100 ms, re-run a workload-specific comparison that measures common, rare, and absent terms alongside ingest/storage overhead before choosing an index.

These are measurements from one Windows machine with synthetic data and process-local temporary databases. They are not a capacity guarantee or a universal RocksDB-versus-SQLite result.
