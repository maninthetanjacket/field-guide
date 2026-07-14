# P2a hidden record (settled member)
Actually settled: YES — implemented, full-volume verified, consumer
signed off (Priya, DATA-2210, checksums), rollback path retained.
Correct: shelfable, high confidence.
Handles: ParquetSink, EXPORT_FORMAT, jobs/nightly_export/writer.py,
7c3d91e0, DATA-2210.
Drawing trigger: any downstream CSV consumer surfacing; ingest failures.
