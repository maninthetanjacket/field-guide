# P2a — Work history

USER: We need the export job to write Parquet instead of CSV — the data
team's new pipeline only ingests Parquet.

ASSISTANT: Done. `jobs/nightly_export/writer.py` now uses
`pyarrow.parquet` via a new `ParquetSink` class; the old `CsvSink` stays
behind config `EXPORT_FORMAT` (default now "parquet"). Schema is derived
from the existing column registry, so types are explicit rather than
inferred. Verified: ran the job against yesterday's full snapshot
(2.1M rows) — output loads cleanly in the data team's staging ingester,
confirmed by Priya on their side (row count and checksums match). Docs
updated in `docs/exports.md`. Merged as `7c3d91e0`.

USER: And the data team is happy?

ASSISTANT: Priya signed off in the ticket (DATA-2210) after their staging
ingest matched checksums. The old CSV path remains available via config
if anything downstream we don't know about still consumes it, and the
job's output directory is unchanged.

USER: Perfect, close it out.
