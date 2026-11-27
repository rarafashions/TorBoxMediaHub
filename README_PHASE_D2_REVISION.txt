TorBox Media Hub - Phase D2 Revision v4
Roxxy Ordered Multi-Category Classification

Purpose
- Implements the user-approved, independent Roxxy category engines in strict order.
- Removes English sexual/adult descriptors from the Indo keyword dictionary.
- Keeps content attributes (e.g. adult, anal, amateur, MILF) separate from media category.
- Preserves the D1 persistent queue, file validation, and JAV-only duplicate lookup.
- Does not register media into media_items; D3 remains separate.

Exact category order
1. Indo
2. JAV
3. English Adult
4. TV Show
5. Anime
6. Donghua
7. General Movie
8. Uncategorized

Engine behavior
- Each engine records passed, not_matched, or skipped.
- The first passing engine wins; subsequent engines are skipped.
- Uncategorized is the terminal fallback when no category engine matches. It remains
  in the D2 review queue (task status needs_review) rather than being silently accepted.
- Indo is evaluated using Indonesian identity/title/genre evidence only. English adult
  descriptors such as adult, anal, amateur, hardcore, MILF, creampie, boobs, tits,
  pussy, etc. are not Indo keywords.
- Generic country hints such as Indonesia/Indonesian are weak supporting evidence;
  a single such hint is not enough to classify a title as Indo.
- Adult descriptors are retained in the independent `attributes` array and cannot
  override a category decision.
- TV Show is evaluated before Anime and Donghua, as explicitly requested.
- Anime is evaluated before Donghua. Donghua-only markers such as `donghua`,
  `guoman`, and `chinese anime` do not by themselves trigger the Anime engine.
- JAV duplicate lookup runs only if the winning category is `jav` and a validated
  JAV production code is available.
- Indo subtitle_policy remains `skip`; other categories remain `eligible_if_needed`.

Audit data
Each D2 identification stores:
- classifier_version: d2-multicat-4
- category, classification_status, classification_method, confidence
- engine_results: ordered engine decision history
- attributes: independent content attributes
- signals, warnings, code_id, duplicate, subtitle_policy

Reclassification
- Existing tasks with a classifier version other than d2-multicat-4 are promoted to
  the queue once by the existing startup migration helper.
- Reclassification is idempotent for the same classifier version.

Files in this patch
- database_manager.py
- roxxy_download_engine.py
- classification_engine.py
- indo_keyword_engine.py
- metadata_engine.py (retains the verified SKMJ JAV maker prefix from v3)

Install
1. Stop TorBox Media Hub.
2. Back up database/torbox_media_hub.db and the five Python files listed above.
3. Extract this ZIP into the application folder and overwrite the five files.
4. Do not replace app.py.
5. Start the application and wait for the D2 reclassification queue to finish.
6. Inspect GET http://127.0.0.1:5000/api/roxxy/library-queue.

Validation performed in the patch build environment
- Python syntax compilation passed for all five files.
- Deterministic tests passed for the three exact Indonesian sample titles from the
  user's metadata JSON, including English adult tags in the same metadata.
- SKMJ-808 -> jav; English Adult -> english_adult; TV Show -> tv_show;
  Anime -> anime; Donghua -> donghua; General Movie -> general_movie;
  no-match -> uncategorized / needs_review.
- Verified `anal` and `amateur` are absent from Indo keyword dictionaries.
- Verified engine_results follows the specified order and marks later engines skipped.
- These are build-environment tests, not a claim of runtime validation on the user's
  Windows installation. User-side queue output is still required.
