# Phase 5 — Automatic Title Normalization Engine

## Scope

This incremental patch aligns classification-time title normalization with the metadata engine's release-noise cleaner. It preserves meaningful title text after a year token (for example `HQ` in `Apocalypse Z New World (2026) HQ ...`) instead of truncating the entire remainder of the filename.

## Behavior

- Original filenames are not modified.
- The release year is removed from the normalized search title as a standalone token; year extraction remains handled separately by the existing release parser.
- Known release/fansub/technical bracket labels and multilingual audio-language lists are removed.
- Meaningful bracketed title text such as `[REC]` is retained as `REC`.
- Existing metadata-engine release-token cleanup is reused to reduce divergence between classification and provider search.
- Existing manual normalized-title overrides remain governed by the existing database/manual-title logic; this patch does not alter that behavior.

## Example

Input:
`Apocalypse Z New World (2026) HQ HDRip - 1080p - x264 - [Tel + Tam + Hin + Eng] - (DD+5.1 - 640Kbps) - 3.3GB - ESub.mkv`

Normalized title:
`Apocalypse Z New World HQ`

Year:
`2026` (extracted independently by the release parser)

## Files

- `classification_engine.py`
- `tests/test_classification_engine.py`

No database schema migration is required. This is an incremental patch for the existing v1.2 build with Phase 1–4 and Provider Search Reliability Hotfix applied.

## Validation

- Python compilation passed for app, database manager, metadata engine, and classification engine.
- Unit tests: 65 total, 62 passed, 3 skipped because Flask is not installed in the isolated development environment (the skipped tests are Flask route tests).
- No live metadata-provider calls were performed.
