# Phase 3 — Classification Integration

## Scope

Phase 3 connects the Phase 1 filename classifier and Phase 2 persistence layer to the Flask application and Library UI. It introduces a separate classification task and an explicit user review workflow.

## Processing model

1. Sync Cloud and Scan Local continue to discover and save media inventory.
2. Neither inventory task invokes `classification_engine`.
3. The user starts **Classify New** separately.
4. The worker selects only records matching all conditions:
   - `classification_status = 'pending'`
   - `classification_locked = 0`
   - `content_category = 'uncategorized'`
5. The classifier analyzes filename and parent/folder name locally.
6. `classified` results update category and classification fields.
7. `needs_review` results save normalized title, evidence, confidence, and status, but retain the existing category.

The batch query has a 5,000-item safety cap per run. Re-run the task to process a larger pending backlog.

## User review workflow

The media detail modal now includes Classification Review:

- Normalized Title is editable and stored separately from the original filename.
- Preview calls the classifier but performs no database writes.
- The preview displays recommendation, confidence, method, signals, and warnings.
- The user selects/confirms a final category explicitly.
- Manual confirmation sets `classification_status = 'manually_classified'` and optionally locks the record.
- History is available from the modal.

## API additions

- `POST /api/tasks/classify-pending`
- `POST /api/media/<id>/classification/preview`
- `POST /api/media/<id>/classification/apply`
- `GET /api/media/<id>/classification/history`

Library list endpoints accept `classification_status` as an optional filter.

## Safety and compatibility

- No physical file rename or move.
- No automatic reclassification of records with an existing category.
- No change to Sync Cloud or Scan Local behavior.
- No TorBox/provider API calls from classification.
- Existing metadata enrichment and subtitle operations remain separate.
- Manual locks prevent automatic classifier updates.

## Validation performed

- 32 unit/integration tests pass.
- Python compilation passes for application and engine modules.
- Inline JavaScript syntax check passes.
- A read-only dry run against the 336-record Phase 1 source database confirmed all 336 legacy category values remain in place and the new batch candidate query selects zero existing records (because all already have categories).

The Flask route layer is syntax-checked in this build environment; route execution should be confirmed in the user's Windows environment where the project's Flask dependencies are installed.
