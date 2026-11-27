# TorBox Media Hub v1.2 — Phase 4: Poster & Metadata Reliability

## Objective

Make missing or broken covers diagnosable and recoverable without rerunning all metadata enrichment. Preserve existing media records, original filenames, confirmed classifications, and manually selected posters.

## Changes

### 1. Additive database migration

Migration ID: `tbh_v1_2_phase4_poster_reliability`

New `media_items` fields:

- `poster_status` — `not_checked`, `found`, `missing`, `invalid_url`, `invalid_image`, `unreachable`, `load_error`, or `manual`.
- `poster_checked_at` — timestamp of the last poster operation.
- `poster_error` — concise diagnostic detail.
- `poster_source` — metadata provider, manual selection, browser, or retry source.
- `poster_is_manual` — protects a user-selected poster from automatic metadata updates.

The migration is additive and idempotent. Existing categories, metadata, poster paths, and media rows are not rewritten. Startup creates a database backup before applying an unapplied schema migration.

### 2. Poster download and cache validation

`metadata_engine.download_poster_image_result()` returns a structured result containing the local path, status, and error.

- Only HTTP(S) public destinations are accepted. Local/private addresses and unsafe URL schemes are rejected.
- Redirect destinations are validated again to reduce SSRF risk.
- Redirects are followed manually with a bounded redirect count.
- Response bodies are streamed with a 15 MB limit.
- Only JPEG, PNG, and WebP signatures are accepted; HTML/error pages are not saved as posters.
- Files are written to a temporary file and atomically renamed after successful validation.
- Cache keys include both the media unique key and poster URL hash, so a changed URL cannot silently reuse a stale poster.
- A valid cached poster is reused without a network request.
- Poster cache now follows `TBH_DATA_DIR`, matching the database/cache location used by the application.

The legacy `download_poster_image()` function remains as a compatibility wrapper.

### 3. Metadata enrichment integration

When available, the confirmed `normalized_title` and `content_category` now drive metadata provider selection/search. The original `file_name` remains unchanged and continues to be used for source-code parsing. This prevents the metadata engine from ignoring a user's classification correction.

Manual posters are not downloaded again or overwritten by normal metadata enrichment. Batch enrichment also avoids repeatedly processing a media item solely because its manually selected poster file is unavailable; the user can retry that poster explicitly or release the manual lock.

### 4. Poster recovery endpoints

- `POST /api/media/<id>/poster/retry` — retries the saved poster URL; if it is missing or stale, refreshes provider metadata and persists poster fields only.
- `POST /api/media/<id>/poster/manual` — downloads and validates a user-supplied direct image URL, then locks it as manual.
- `POST /api/media/<id>/poster/automatic` — releases the manual lock and tries the provider poster again.
- `POST /api/media/<id>/poster/load-error` — records a browser rendering failure.
- `GET /api/media/<id>/poster` — serves only a cached file whose signature is a supported image type.

A failed retry does not delete the currently cached poster. Full metadata enrichment no longer deletes the old poster before a replacement has been validated.

### 5. UI

The media detail modal now shows poster status and diagnostic text, provides a poster-only retry action, accepts a direct poster image URL for manual selection, and offers a way to return to automatic provider posters. Main library poster elements report browser load failures and show a fallback without removing card overlays.

### 6. Discovery poster proxy hardening

`GET /api/discover/poster-proxy` fetches arbitrary user-supplied image URLs, so it is treated as an outbound-fetch boundary rather than a plain image cache.

- **SSRF guard on every hop.** The initial URL and each redirect target are checked with `metadata_engine._poster_url_is_public()`, which resolves the host and rejects loopback, private, link-local, CGNAT, multicast and reserved addresses, plus `localhost`/`*.local` names and non-HTTP schemes. The same validator is reused from the poster downloader instead of a second copy of the logic.
- **Manual redirects.** `allow_redirects=False` with a 5 hop ceiling; the `Location` header is resolved with `urljoin` and revalidated before the next request, so a public host cannot redirect into `169.254.169.254` or the LAN.
- **Size cap.** `Content-Length` is rejected above 5 MB, and the body is read through `_read_poster_response(max_bytes=5 MB)` so a lying or chunked response is also stopped.
- **Honest status codes.** 404 (missing), 415 (not an image) and 413 (too large) are returned as themselves; only transport failures map to 502. The previous `except Exception` flattened all of them into 502.
- **Socket hygiene.** The proxy uses a short-lived `requests.Session` closed in `finally` rather than `metadata_engine.HTTP_SESSION`, and always closes the streaming response. Cover hosts vary per item, so pooling them in the global session only accumulated idle `CloseWait` sockets. The shared session pool was also reduced to `pool_connections=8, pool_maxsize=8`.
- **Optional host allowlist.** `TBH_POSTER_HOST_ALLOWLIST` accepts comma-separated domains or suffixes (e.g. `missav.ws,fourhoi.com`). It is empty by default because provider CDNs change without notice and a hard allowlist would silently break covers.

## Safety and compatibility

- No automatic mass metadata refresh is started by this patch.
- Sync Cloud and Scan Local are not coupled to poster downloads.
- Existing categories and original filenames are preserved.
- Manual poster choices are protected from automatic overwrite.
- No new paid service, VPS, NAS, or additional dependency is introduced.

## Tests

The Phase 4 test suite covers poster schema migration/idempotency, startup backup, manual poster protection, stale URL clearing, cache reuse, URL/redirect safety, response size and image-signature validation, atomic writes, normalized-title/category metadata routing, and API endpoints.

Proxy hardening is additionally covered by `tests/test_poster_proxy_ssrf.py` (private/link-local/metadata address rejection, per-hop redirect validation, 5 MB cap with and without `Content-Length`, 415/413/502 mapping, optional host allowlist, and session/response cleanup).

In the isolated build environment, 48 tests pass and 3 Flask API tests are skipped because Flask is not installed in that environment. The project requirements already include Flask; those three tests are designed to run on the Windows installation where the app dependencies are installed. Python compilation and inline JavaScript syntax checks pass.
