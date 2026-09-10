#!/usr/bin/env python3
"""Handle-fidelity check across arm-A0 cells (handles per hidden records)."""
RUNS = "/tmp/shelving-eval-run/A0-qwen38"

HANDLES = {
    "P1a": ["web/middleware/session_refresh.py", "session_refresh_v2",
            "WEB-8841", "a44c02de", "REFRESH_THRESHOLD_SECONDS",
            "SessionRefreshMiddleware.process_request()"],
    "P1b": ["web/middleware/session_refresh.py", "session_refresh_v2",
            "WEB-8841", "a44c02de", "REFRESH_THRESHOLD_SECONDS",
            "SessionRefreshMiddleware.process_request()"],
    "P2a": ["ParquetSink", "EXPORT_FORMAT", "jobs/nightly_export/writer.py",
            "7c3d91e0", "DATA-2210", "CsvSink"],
    "P2b": ["ParquetSink", "EXPORT_FORMAT", "jobs/nightly_export/writer.py",
            "7c3d91e0", "DATA-2210", "legacy_tags"],
    "T5":  ["media/thumbnails/generate.py", "make_thumbnail()",
            "exif_transpose", "10.4.0", "requirements/media.txt", "b2e88f04"],
    "T6":  ["PRICE-990", "release-2214", "/cart/apply-coupon",
            "product_details"],
    "T1":  ["deploy/appserver/uwsgi.ini"],
    "T2":  ["55ab1c02", "c9d0447a", "e77b3f21", "test_ledger_rounding_edge",
            "UserSerializer", "AccountSerializer", "ubuntu-24.04"],
}

def load(ep):
    with open(f"{RUNS}/{ep}/output.txt") as f:
        raw = f.read()
    return "\n".join(l for l in raw.splitlines()
                     if not l.startswith("⚠") and not l.startswith("[claude-code:"))

total_hit = total_all = 0
for ep, hs in HANDLES.items():
    txt = load(ep)
    miss = [h for h in hs if h not in txt]
    hit = len(hs) - len(miss)
    total_hit += hit; total_all += len(hs)
    flag = "OK " if not miss else "MISS"
    print(f"[{flag}] {ep:<4} {hit}/{len(hs)}" + (f"   missing: {miss}" if miss else ""))
print(f"\nTOTAL handles retained: {total_hit}/{total_all}")
