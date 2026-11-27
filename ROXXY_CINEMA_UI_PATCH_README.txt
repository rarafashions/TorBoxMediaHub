TORBOX MEDIA HUB V1.3C — ROXXY CINEMA UI PATCH
================================================

Baseline
--------
Built from the uploaded "TorBox Media Hub v1.3C.zip".
This patch preserves the existing V1.3C downloader engines, TorBox queue
monitoring, Roxxy catalog/meta/stream routes, and V1.2 shared engines.

Changes
-------
1. Roxxy page redesigned as a streaming-style Cinema UI.
   - Header: Roxxy branding and Settings button on one line.
   - Settings moved into a right-side drawer.
   - Catalog, genre, and search controls moved to a sticky navbar.
   - Discovery / Download Center split uses a 60:40 grid on wide screens.
   - Download Center is visible by default and can be collapsed.
   - Local and TorBox queues are separated into tabs.
   - Discovery uses a poster-first responsive grid.
   - Detail view remains a right-side drawer.

2. TorBox cache pre-check for Magnet streams.
   - New endpoint: POST /api/roxxy/torbox-cache-check
   - Extracts BTIH hash from the selected Magnet URI.
   - Calls TorBox torrents/checkcached with the configured TorBox API key.
   - Reports Cached / Not Cached / status unavailable.
   - Cache check does not submit the torrent.
   - User can still submit when not cached or when cache status is unavailable.

3. One-click download workflow.
   - Removed confirmation dialogs for TorBox, yt-dlp, and aria2c.
   - On successful enqueue/submission, the detail drawer closes.
   - UI returns to catalog and selects the matching Download Center tab.
   - Queue continues to show status/progress.
   - Failed requests remain visible as an inline status message.

Files in patch
--------------
- templates/roxxy.html
- roxxy_module.py

Installation
------------
1. Stop Flask.
2. Back up the entire current V1.3C application folder and database.
3. Extract this patch into the application root, preserving directory structure.
4. Overwrite the two files.
5. Start Flask and open /roxxy.
6. Open Settings and verify the existing manifest and download path remain
   configured (the manifest token is not exposed in the UI).
7. Load Catalogs, open a Magnet stream, and verify the cache status appears
   before the Download to TorBox button becomes available.

Validation performed during patch preparation
----------------------------------------------
- Python syntax compilation: app.py, roxxy_module.py, roxxy_download_engine.py.
- JavaScript syntax check: templates/roxxy.html.
- Static checks: required UI IDs present once; no confirm()/alert() calls remain.
- No live TorBox cache request or Windows download was executed in this build
  environment. Validate the cache response and complete workflow on the user's
  Windows installation.

Security
--------
- The Roxxy manifest URL and Magnet are not written into the UI queue.
- TorBox API key is read from existing V1.2 settings and is not returned.
- Download only media you are authorized to access. No DRM, CAPTCHA, paywall,
  or access-control bypass is implemented.
