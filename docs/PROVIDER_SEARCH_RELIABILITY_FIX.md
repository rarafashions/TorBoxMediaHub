# Provider Search Reliability Fix — v1.2 hotfix

## Why this hotfix exists

A successful `POST /api/media/<id>/enrich` only means the Flask request completed. Earlier code could return HTTP 200 with a local filename-parser fallback even when no provider match or poster had been found. The UI then displayed a generic success message.

The uploaded library snapshot also contained this movie filename:

`Spider-Man 9 Zbrusu nový den (2026) UHDRDV cz sk en.mkv`

The legacy title cleaner retained `UHDRDV`, `cz`, `sk`, and `en`, and the old TMDb search used one query only. That made a legitimate movie title difficult to match.

## Changes

### Movie / TV provider search

- Cleans common UHD/HDR/Dolby Vision, codec, resolution, and language release tags before provider lookup.
- Uses a small, conservative set of TMDb query candidates instead of a single raw title.
- Recognizes the specific `Spider-Man 9` / `Zbrusu nový den` filename pattern as a search alias for the canonical title `Spider-Man: Brand New Day` (2026).
- Uses the release year to rank and constrain franchise fallback results, reducing the risk of matching an older Spider-Man film.
- Uses the parsed filename title for legacy rows when the old `series_title` only contains release noise. Explicit `normalized_title` and manual title locks remain authoritative.

### JAV provider fallback

- Keeps JavDatabase as the first provider.
- If JavDatabase does not return a page/poster, optionally tries ThePornDB's dedicated `/jav` search endpoint using its documented `parse` query.
- If JavDatabase returns a poster URL but the image download fails validation or is unreachable, makes one alternate ThePornDB poster attempt before reporting failure.
- For Caribbeancom date-code releases such as `081326-001`, builds a site/date/title parse candidate when those fields can be extracted safely.
- This fallback requires a ThePornDB API key in Settings. The setting label now states that the key is used for English Adult and JAV fallback.

### Accurate lookup status

- Filename-parser-only fallback results now set `has_metadata=0`; they are no longer falsely reported as fully enriched.
- The single-item Re-Enrich API returns a `lookup` diagnostic object with provider, status, matched title/query, poster status, and recent provider attempts.
- The UI distinguishes `not_found`, `metadata_found_poster_missing`, and poster-download failures.
- Batch Enrich task logs now record provider/status/query for items without a clean metadata+poster result.

## Provider routing

| Category | Primary provider | Fallback |
|---|---|---|
| General Movie | TMDb | — |
| TV Show | TMDb | TVmaze |
| Anime / Donghua | AniList | TMDb |
| JAV | JavDatabase | ThePornDB `/jav` (requires TPDB key) |
| English Adult | ThePornDB scenes/movies (requires TPDB key) | Local parser fallback |

## Safety

- No database schema migration is required.
- No media filename or TorBox object is renamed or modified.
- Manual title and manual poster locks remain respected.
- No automatic bulk reclassification is performed.
- Existing poster cache and validation rules remain in place.

## Tests

The added tests cover release-tag cleaning, Spider-Man alias generation, TMDb title/year selection, TPDB JAV fallback parsing, and legacy-title lookup. Existing classification, database, manual-title, and poster tests remain in the suite.

Three Flask route tests may be skipped in environments where Flask is not installed; they should run in the user's normal application environment.
