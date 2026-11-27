TorBox Media Hub v1.3D3 - Direct-to-Library increment

Files to replace:
- database_manager.py
- roxxy_download_engine.py
- templates/roxxy.html

What changed:
1. Added a dedicated serialized D3 registration worker for D2 tasks with status 'identified'.
2. Registers valid, classified Roxxy downloads into media_items as source_type='local'.
3. Uses a stable local:<normalized absolute path> unique_key, making registration idempotent.
4. Maps Roxxy metadata directly (title, description, poster, release year, runtime, genres) and sets metadata_source='roxxy'.
5. Keeps the D2 JAV duplicate gate: duplicate tasks are not registered.
6. Skips subtitle processing for Indo.
7. For other categories, invokes the existing V1.2 subtitle engine as fallback; subtitle errors do not undo Library registration.
8. Recovers interrupted D3 'registering' tasks to 'identified' on application restart.
9. Exposes D3 registration/subtitle status through /api/roxxy/library-queue and the local download job status.
10. Displays the Library status under the existing Roxxy local download queue item.

Install:
- Stop TorBox Media Hub.
- Back up the three existing files.
- Replace them with the files in this patch, preserving folder structure.
- Start the app normally and test with one newly completed Roxxy download.

Notes:
- Existing D1/D2 classification logic and download engines were not intentionally changed.
- No original URL / TorBox WebDL UI was added.
- The uploaded baseline did not contain Roxxy-native subtitle URL data in its sample contract; this increment uses the existing V1.2 subtitle engine fallback. Indo remains skipped.
- Validate on a fresh test download before relying on bulk registration.
