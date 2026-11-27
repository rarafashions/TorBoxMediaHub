# TorBox Media Hub v1.2 — Classification Engine (Phase 1)

## Scope

This checkpoint introduces a deterministic, local-only classification engine and a golden test suite.

Included:
- `classification_engine.py`
- A small robustness fix in `metadata_engine.py` for bracketed/punctuated English Adult studio tags, plus the `Brazzers Exxtra` aliases observed in the supplied database.
- `tests/test_classification_engine.py`

Not included in this phase:
- SQLite schema migration
- Needs Review UI or REST endpoints
- Changes to Sync Cloud / Scan Local
- Automatic enrichment or metadata-provider requests
- Reclassification or writes to the supplied database

The engine is not wired into the running application yet. That is deliberate: this phase validates rules before connecting them to the database and UI.

## Output contract

`classify_media(filename, parent_name="", normalized_title=None)` returns:

- `original_filename`
- `parent_name`
- `normalized_title`
- `detected_category` (`jav`, `english_adult`, `anime`, `donghua`, `tv_show`, `general_movie`, or `None`)
- `category_confidence` (deterministic score, not a statistical probability)
- `classification_method`
- `classification_status` (`classified` or `needs_review`)
- `detected_code`
- `season_num`, `episode_num` (reserved for subsequent integration)
- `signals`
- `warnings`

When status is `needs_review`, `detected_category` is `None`. A candidate is not silently written to a content category.

## Rules

1. Validated JAV maker prefixes / special formats have the highest priority.
2. A generic code such as `ABC-123` is not automatically treated as JAV. It is sent to Needs Review unless its prefix is validated.
3. Explicit English Adult studio tags are recognized even when bracketed, e.g. `[Blacked]`.
4. Explicit Donghua title/tag signals are recognized. Hanzi alone is insufficient to claim Donghua.
5. Explicit Anime signals include Kana, Anime tags, and known fansub groups. Kana alone is not allowed to override an unverified product-like code in a JAV release parent name.
6. An episodic filename is a strong TV-show signal, except where Japanese/Korean or dual-audio hints make Anime-vs-TV ambiguous; those cases go to Needs Review.
7. General Movie requires a corroborating signal such as a release year. A multiword or single-word name alone is not enough.
8. The normalizer never renames the original media file. It only returns a search-friendly title.

Confidence scores are deterministic rule scores. They must not be presented as calibrated probabilities.

## Use

```python
from classification_engine import classify_media

result = classify_media(
    filename="[Group] IPX-123 1080p HEVC.mkv",
    parent_name="",
)

if result["classification_status"] == "classified":
    category = result["detected_category"]
else:
    # Queue for the Needs Review workflow; do not guess a category.
    pass
```

For a user correction:

```python
result = classify_media(
    filename="[Complex.Release.Group] original_file_name.mkv",
    parent_name="",
    normalized_title="IPX-123",
)
```

The caller must still ask the user to confirm the preview before persisting a manual classification. This module does not write to SQLite.

## Tests

From the project root:

```powershell
python -m unittest discover -s tests -v
```

The current golden suite contains 21 tests covering:
- Known JAV codes and FC2
- Unknown/generic code review
- Bracketed and parenthesized English Adult studio names
- Brazzers Exxtra and false `CLUB-480p` code detection
- Anime fansub/Kana signals
- Donghua title/tag and Hanzi-only ambiguity
- TV episode structure and ambiguous Japanese/Korean dual-audio episodes
- General Movie with year
- Performer-like names and uninformative filenames
- Normalized title cleanup and preservation of meaningful bracketed titles

## Dry-run against the supplied v1.1 database

A read-only audit was run against the 336 `media_items` rows in the supplied ZIP. The database was not modified and no provider/API calls were made.

| Result | Count |
|---|---:|
| Classified | 275 |
| Needs Review | 61 |
| Total | 336 |

Classified categories:

| Category | Count |
|---|---:|
| TV Show | 109 |
| English Adult | 72 |
| JAV | 57 |
| General Movie | 34 |
| Anime | 1 |
| Donghua | 2 |
| Needs Review | 61 |

Needs Review reasons:

| Reason | Count |
|---|---:|
| Unverified product-like code | 36 |
| Episodic + Japanese/Korean/dual-audio ambiguity | 21 |
| Hanzi without confirmed Donghua signal | 4 |

Interpretation:
- The 36 unverified codes may include legitimate JAV prefixes not yet present in the validated prefix list. They are intentionally not guessed.
- The episodic group needs an Anime-vs-TV decision. This prevents anime episodes from being silently classified as ordinary TV Shows based only on `SxxExx`.
- The Hanzi-only items may be Chinese-language media, but that alone does not prove Donghua.
- The engine also re-identifies 12 records currently labeled General Movie as English Adult based on explicit studio evidence. This is a dry-run finding only; no database update was performed.

## Integration safety

Before wiring this engine into Sync/Scan or Enrich Meta:
1. Add the schema migration and history table in a separate checkpoint.
2. Preserve existing v1.1 category values as legacy data; do not mass-reclassify them automatically.
3. Use this engine for new inventory and explicit user-triggered reclassification.
4. Keep `file_name` immutable; store user edits only in `normalized_title`.
5. Require explicit confirmation for manual category overrides.
6. Add the Needs Review API/UI only after database migration tests pass.

## Revision d2-multicat-5 (2026-11-27)

The engine order did not change: Indo, JAV, English Adult, then the episodic and movie
categories. The revision fixes Indo recall on the TorBox auto-add path.

- `classify_media` returns `classifier_version`. Store it; the media row carries the
  same value in `media_items.classifier_version` (migration
  `tbh_v1_3d5_multicat5_indo`).
- `classify_indo` accepts `filename` and `parent_name` as evidence. The cloud import
  path used to drop both when a provider title existed.
- Dictionary phrases accept common suffixes (`-nya`, `-ku`, `-mu`, `-lah`, `-kah`,
  `-pun`), so `Tetangganya` matches `tetangga`.
- The supporting dictionary now covers kinship, work roles, housing settings,
  regions/cities, Indonesian function words, and address terms. None of them are
  English adult descriptors.
- Two independent supporting clues are enough (`score >= 2`); one weight-1 clue is
  still rejected, and a lone country adjective remains weak evidence.
- Reclassification reads stored `studio`, `genres`, `plot`, and `year` so the verdict
  is not thinner than the original import. `app._guard_adult_reclassification` keeps
  an Indo item from being demoted and an adult item from moving into a non-adult
  category; those rows become `needs_review` instead.
- Rows outside `uncategorized`, `indo`, and `english_adult` are only rewritten when
  the new verdict is `indo`; otherwise only `classifier_version` is stamped.
