# Manual Title Override and Re-Enrich Fix

## Problem

The detail modal's **Save Changes** endpoint updated `title` and `series_title`, but did not update `normalized_title`. The metadata engine prioritizes `normalized_title`, so a previously generated filename-derived value (for example `081326-001`) could override the title the user had just saved. Forced metadata upserts could then replace the visible title with the provider's title.

## Changes

- Added `title_is_manual` to `media_items` through an additive, idempotent schema migration.
- The migration creates a pre-migration backup through the existing startup backup gate.
- Existing rows with `metadata_source='manual'` are repaired once: `normalized_title` is synchronized from `title` (or `series_title` for TV Show, Anime, and Donghua), and `title_is_manual` is set.
- Save Changes now synchronizes `normalized_title` and marks the title as manually controlled.
- Forced metadata refreshes may update provider metadata (plot, poster, runtime, IDs, etc.) but cannot overwrite a manually controlled `title` or `series_title`.
- Metadata enrichment keeps a manually confirmed title as the canonical series title. Automatically normalized filenames do not override a better provider title.
- Single-item Re-Enrich now returns a useful JSON error and the UI displays it instead of silently doing nothing.

## Safety

- Original `file_name` is unchanged.
- No media files are renamed or moved.
- No TorBox files are modified.
- No batch enrichment is started automatically.
- Backfill is restricted to records previously saved with `metadata_source='manual'`.
- Migration is additive and idempotent.

## Installation

This is an incremental patch for a project already running Phase 4.

1. Stop TorBox Media Hub.
2. Make a manual copy of `database/torbox_media_hub.db` as an extra precaution.
3. Extract this ZIP into the project root and allow the listed files to be replaced.
4. Start the application once. The startup migration makes its own backup before changing the database.
5. Run `python -m unittest discover -s tests -v`.
6. Open the affected media item and run **Re-Enrich Metadata**.

The migration repairs prior Save Changes records automatically when their `metadata_source` is `manual`; there is no need to retype the title first.

## Validation

- 58 tests passed; 3 Flask API tests skipped because Flask is not installed in the isolated development environment.
- Python compilation passed.
- Inline JavaScript syntax check passed with Node.js.
