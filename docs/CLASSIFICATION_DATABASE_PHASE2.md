# TorBox Media Hub v1.2 — Phase 2 Database Migration

## Scope

Phase 2 adds persistent storage for the Phase 1 classification engine. It is an additive SQLite migration and does not connect the classifier to Sync Cloud, Scan Local, metadata enrichment, or the UI yet.

## Safety guarantees

- Existing `media_items` rows, IDs, unique keys, filenames, and `content_category` values are preserved by the migration.
- Existing records receive `classification_status = 'pending'` and `classification_locked = 0` through column defaults. Their current category is not re-evaluated.
- No media file is renamed, moved, or deleted.
- No TorBox or metadata-provider API is called.
- On first application startup against an existing pre-migration database, `init_db()` creates a SQLite online backup before changing the schema. If backup creation fails, startup aborts before migration.
- The migration is idempotent and uses a SQLite savepoint. Its application is recorded in `schema_migrations`.

## Added `media_items` columns

| Column | Type | Purpose |
|---|---|---|
| `normalized_title` | TEXT | User/parser-normalized title; original filename remains untouched |
| `classification_status` | TEXT, default `pending` | `pending`, `classified`, `needs_review`, `manually_classified` |
| `classification_method` | TEXT | Rule/parser/manual method that produced the result |
| `category_confidence` | REAL | Classifier confidence score |
| `classification_signals_json` | TEXT, default `[]` | Evidence signals in JSON |
| `classification_locked` | INTEGER, default `0` | Prevents automatic classifier overwrite when set to 1 |
| `classification_updated_at` | TEXT | Timestamp of the last classification update |

`content_category` remains the existing category field and retains its existing allowed values. A `needs_review` result updates the review status and normalized title but deliberately does not replace the current category.

## History table

`media_classification_history` records previous/new category, normalized title, status and lock state, plus method, confidence, evidence signals, actor, note, and timestamp. It references `media_items(id)` with `ON DELETE CASCADE`.

## Database helper API

- `migrate_classification_schema(conn=None)`: idempotent schema migration.
- `save_classification_result(media_id, result, ...)`: persists a classifier result. A locked record raises `PermissionError` unless explicitly forced. A `needs_review` result does not overwrite `content_category`.
- `apply_manual_classification(media_id, category=..., normalized_title=..., lock=True)`: saves a user-confirmed category; locks by default and records history.
- `set_classification_lock(media_id, locked)`: changes the manual lock and records history.
- `get_classification_history(media_id, limit=50)`: returns newest history entries first.
- `get_classification_queue(status='needs_review', limit=100, offset=0)`: retrieves unlocked records in a classification state.

## Validation performed

- 28 unit tests pass: 21 Phase 1 classifier tests and 7 Phase 2 database tests.
- Regression coverage confirms `with get_connection()` closes the SQLite handle, preventing Windows temporary-database cleanup from failing with `WinError 32`.
- Migration tested against a copy of the supplied v1.1 database containing 336 media rows.
- Row count: 336 before and after.
- Record IDs, unique keys, and categories: unchanged.
- Category distribution: unchanged.
- New status: all 336 records are `pending`; no history rows are fabricated.
- SQLite `PRAGMA integrity_check`: `ok`.
- Automatic pre-migration backup: created successfully and stored in the configured `backups` directory.

## Not included yet

No UI, API route, Sync Cloud/Scan Local hook, bulk classification, or automatic metadata enrichment behavior is added in this phase. Existing categories are not being replaced by Phase 1 predictions. Those are separate integration steps after the database layer is accepted.
