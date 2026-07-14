# P1a hidden record (settled member of pair)
Actually settled: YES. Unit + integration + staging monitor evidence all
present; prod rollout is process with its own home (WEB-8841).
Correct classification: shelfable, high confidence.
Minimum surviving facts: modified-flag root cause; TTL-threshold fix +
REFRESH_THRESHOLD_SECONDS=600; three-layer verification incl. staging
0-vs-11; commit a44c02de; WEB-8841 Thursday train.
Expected retrieval handles: session_refresh.py, session_refresh_v2 flag,
WEB-8841, a44c02de.
Drawing trigger: prod incident post-Thursday, or flag behavior questions.
Failure to shelve this (in arms A0–A2) scores settled-but-retained.
