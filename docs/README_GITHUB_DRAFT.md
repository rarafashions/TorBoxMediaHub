# 🎬 TorBox Media Hub `v1.3DH`

### Self-hosted media catalog, TorBox cache finder, and direct-stream hub for TorBox Cloud and local drives

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![TorBox](https://img.shields.io/badge/TorBox-API%20Ready-00d4ff)](https://torbox.app/subscription?referral=d8962fcc-d39e-43f6-9a8a-f565bab5f809)
[![Players](https://img.shields.io/badge/Players-VLC%20%7C%20MPV%20%7C%20IINA%20%7C%20Android-FF8800?logo=vlcmediaplayer&logoColor=white)](https://www.videolan.org/)
[![Platforms](https://img.shields.io/badge/Platforms-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20Android%20%7C%20LAN-6366f1)](#installation)
[![Tests](https://img.shields.io/badge/Tests-425%20passed%20%2B%2063%20subtests-brightgreen)](#development)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#license)

**TorBox Media Hub** turns your TorBox Cloud and local video folders into a fast, poster-rich media library that streams directly to VLC, MPV, IINA, or Android — **without downloading cloud video files to your device**.

It combines four things that usually require separate apps:

1. A Netflix-style catalog for cloud and local media.
2. Multi-indexer TorBox cache checks and one-click magnet/NZB/WebDL submission.
3. Series/season tracking with missing-episode search and automatic Library import.
4. A Video Feeds (Roxxy) hub with five addon sources, stream analysis, and local downloads.

Only posters, subtitles, `.m3u` playlists, and a SQLite database are stored locally for cloud media. Cloud videos stay on TorBox and are streamed through freshly generated `requestdl` links when you press Play. Local downloads from Video Feeds are explicit actions and do write video files to the folder you choose.

> 🎁 New to TorBox? Use the **[official referral link](https://torbox.app/subscription?referral=d8962fcc-d39e-43f6-9a8a-f565bab5f809)** for **7 bonus days** on Essential, Standard, or Pro plans. It also supports this project.

---

## 📸 Screenshots

| Catalog / Library | Subtitles | Media details |
| :---: | :---: | :---: |
| ![Catalog](docs/assets/TMH-General-Movies.png) | ![Subtitles](docs/assets/TMH-Subtitle.png) | ![Details](docs/assets/TMH-Details-Media-Card.png) |

| TV Shows / Series | JAV / Video Feeds |
| :---: | :---: |
| ![TV Shows](docs/assets/TMH-TV_Show.png) | ![JAV](docs/assets/TMH-JAV.png) |

---

## ✨ What's new in `v1.3DH`

- **Live episode download progress in the Series panel.** Every **+ TorBox** action shows a progress tray in the same panel: progress bar, percentage, TorBox state, and an `IMPORT` pill while the file is registered into the Library.
- **Automatic Library refresh after import.** When the download finishes, the episode row changes from **Find episode** to **Play**, the season completeness counter increases, the release disappears from search results, and **Play season** includes the new episode.
- **Correct episode pinning.** The requested season/episode is stored server-side and applied only to single-video downloads, so ambiguous release names do not land on the wrong episode. Season packs still parse each file individually.
- **Non-destructive progress endpoint.** `GET /api/series/download-status` reads the TorBox monitor without consuming completion events, so the Series panel and the Downloads view can observe the same import.
- **Stratified episode search.** TVMaze canonical titles, TPB, EZTV, Torrentio, Nyaa, NZBIndex, optional NZBGeek, and Roxxy — with seed/leech, size, quality, and honest `CACHE ?` for NZBs.
- **Full v2 interface.** Home, Library, Series, Playlists, Downloads, Stats, and Settings, built with plain CSS/JS (no framework, bundler, npm, or Tailwind).

---

## 🚀 Core features

### Library and catalog

- Poster-rich grid for TorBox torrents, Usenet, WebDL, and local files.
- Search and filters: category, source, watched state, subtitle, cover, classification status.
- Sort by newest, oldest, title, rating, year, size, episode count, or completeness.
- Star rating, watched toggle, bulk select, bulk play, bulk watched.
- Series grouping per title, with optional flat episode view.
- Deep links (`#media-<id>`) and browser Back support.
- Manual metadata edit, classification preview/apply/history, poster override, physical delete (legacy UI).

### Discovery

- Four categories: **Movies**, **TV Shows**, **Anime**, **Donghua**.
- Trending, Popular, Top Rated, and Search feeds (TMDb / AniList).
- Cache checks across Torrentio, YTS, The Pirate Bay/APIBay, Nyaa, EZTV, and NZBIndex (TorBox Pro).
- One-click magnet/NZB submission to TorBox.
- **Quality Upgrade / Replace**: download a better release and delete the old file after completion.
- Poster proxy with SSRF guards.

### Series and episodes

- Official season structure from TMDb (TVMaze fallback) and AniList (Jikan fallback), cached per series.
- Season tabs with completeness such as `Season 02 · 1/3`.
- Missing episodes rendered as dashed rows with **Find episode**.
- **Find all missing episodes** in one request (up to 24 episodes per call).
- Episode results show cached status, source, seed/leech/peer, size, quality, and **+ TorBox**.
- Live download progress + automatic Library import (new in `v1.3DH`).
- **Play season**, **Play next episode**, and mark-season-watched.
- NZB status is intentionally shown as `CACHE ?` when it cannot be known.

### Video Feeds (Roxxy at `/roxxy`)

- Five independent addon manifests: **JAV**, **English Adult**, **OnlyFans**, **Indonesia Movies**, **Donghua**.
- Per-source catalogs, search, genre reload, and all-catalog search limited to catalogues that support it.
- Metadata, posters, JAV codes, episode panels, and **IN LIBRARY** badges.
- Automatic stream analysis: cached/uncached TorBox torrent, NZB to TorBox Usenet, or local yt-dlp/aria2c.
- Local download queue with 3 concurrent workers, aria2c multi-connection for direct files, yt-dlp for HLS/DASH, retries, and expired-link refresh.
- WebDL support for public page URLs (Pornhub, Eporner, XHamster, RedGifs, XVideos, YouJizz, TNAFlix, EMPFlix).
- Auto-add to Library through the D1–D3 pipeline, including metadata, poster, subtitles, and JAV duplicate protection.

### Metadata and classification

- TMDb, TVMaze, AniList, JAVDatabase, and TPDB.
- Deterministic, offline classification with categories:
  `indo` → `jav` → `english_adult` → `tv_show` → `anime` → `donghua` → `general_movie` → `uncategorized`.
- `classified`, `needs_review`, `manual`, and `pending` states; manual locks are respected.
- Classification history and versioned classifier (`d2-multicat-5`).
- Clean title parsing for release suffixes, fansub groups, and generic parent folders.
- `ffprobe` runtime detection.

### Subtitles

- Four tiers: local sidecars, TorBox archive files, embedded tracks via `ffprobe`, and public providers (OpenSubtitles, SubtitleCat).
- English and Indonesian tracks stored per media item.
- Provider-supplied Roxxy subtitles are downloaded first.
- Subtitle quality audit with batch results.
- English Adult subtitle search remains enabled by design.

### Playlists and playback

- Create playlists, add/remove items, play sequentially, download `.m3u`.
- M3U includes duration, external subtitle options, and fresh stream URLs.
- Remote M3U and HTTP Range streaming for LAN devices.
- Auto-detection of VLC, MPV, PotPlayer, MPC-HC/BE, KMPlayer, IINA, Celluloid, SMPlayer, Haruna.
- Android intents for VLC, mpv-android, or system chooser.

### TorBox integration

- Cloud sync for torrents, Usenet, and WebDL — no video downloads.
- Batch cache checks.
- Add magnet/NZB/WebDL, background monitor, automatic import/enrichment/poster/subtitle.
- Persistent import hints across restarts.
- Safe remote deletion with season-pack protection.

### Database and operations

- SQLite WAL with versioned migrations.
- Backup, restore, maintenance/VACUUM, metadata reset, and full media erase.
- Rotating log at `logs/app.log`.
- Cross-site write guard, masked secrets, SSRF guards, and download-path guards.

---

## 📦 Requirements

| Component | Required | Notes |
|---|---|---|
| Python **3.10+** | Yes | 3.12 recommended |
| `flask`, `requests`, `beautifulsoup4` | Yes | `pip install -r requirements.txt` |
| FFmpeg / `ffprobe` | Strongly recommended | Runtime, embedded subtitles, validation |
| VLC / MPV / IINA | Strongly recommended | Playback |
| yt-dlp | Recommended | HLS/DASH and fallback downloads |
| aria2c | Recommended | Multi-connection direct downloads |
| TorBox API key | For cloud features | `torbox.app/settings` |
| TMDb API key (v3) | Strongly recommended | Movie/TV metadata and seasons |
| TPDB API key | Optional | English Adult metadata |
| NZBGeek API key | Optional | Premium NZB search; NZBIndex is free |

> Usenet and WebDL features depend on your TorBox plan. Some limits (storage, WebDL, Usenet) are enforced by TorBox, not by this app.

---

## 🛠️ Installation

### 🪟 Windows 10 / 11

Open **PowerShell**:

```powershell
winget install Python.Python.3.12 Git.Git Gyan.FFmpeg VideoLAN.VLC yt-dlp.yt-dlp aria2.aria2
```

Close and reopen PowerShell so the new tools are on `PATH`.

```powershell
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

Open **http://127.0.0.1:5050**.

> Keep the console window visible. It is the primary log sink; `logs/app.log` mirrors it.

### 🐧 Linux

**Ubuntu / Debian**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git ffmpeg vlc mpv aria2
python3 -m pip install --user yt-dlp
```

**Fedora**

```bash
sudo dnf install -y python3 python3-pip python3-tkinter git ffmpeg vlc mpv aria2
python3 -m pip install --user yt-dlp
```

**Arch**

```bash
sudo pacman -S --needed python tk git ffmpeg vlc mpv aria2 yt-dlp
```

Then, on any distribution:

```bash
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5050**.

`python3-tk`/`tk` is only needed for native file/folder dialogs. Headless servers automatically use the built-in web filesystem browser.

### 🍎 macOS

```bash
brew install python git ffmpeg aria2 yt-dlp
brew install --cask vlc iina
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5050** in Safari or Chrome.

### 📱 Android (Termux)

Run the hub directly on your phone or tablet.

1. Install **Termux** from [F-Droid](https://f-droid.org/en/packages/com.termux/) (the Play Store build is outdated), plus **VLC for Android** and/or **mpv-android**.
2. In Termux:

```bash
pkg update && pkg upgrade -y
pkg install -y python git ffmpeg aria2 yt-dlp
termux-setup-storage
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
pip install -r requirements.txt
python app.py
```

3. Open **http://127.0.0.1:5050** in your mobile browser.
4. Go to **Settings → Media player → Auto-Detect** and choose **VLC for Android (Intent)**, **mpv-android (Intent)**, or **Android System Chooser**.
5. Keep the process alive while downloading:

```bash
termux-wake-lock
```

Set the Video Feeds download folder in `/roxxy` to a writable location such as `~/storage/shared/Movies/TorBox`. Avoid the storage root, `Android/data`, and system folders.

> For large downloads, a PC or NAS host with Android as a LAN client is usually more reliable.

### 🌐 LAN access

Bind to all interfaces:

```bash
TBH_HOST=0.0.0.0 TBH_PORT=5050 python app.py
```

Windows PowerShell:

```powershell
$env:TBH_HOST="0.0.0.0"; $env:TBH_PORT="5050"; python app.py
```

Then open `http://<host-ip>:5050` from another device and allow the port through the firewall.

> ⚠️ There is **no authentication**. Only expose the port on a trusted LAN, or put it behind a reverse proxy that authenticates.

---

## ⚙️ First run

1. Open **Settings**.
2. Enter your **TorBox API key** (`https://torbox.app/settings`).
3. Enter a free **TMDb API key (v3)** for movie/TV metadata and season data.
4. Optional: **TPDB** for English Adult metadata, **NZBGeek** for premium NZB search.
5. Click **Auto-Detect** for your player, or paste the executable path.
6. Add local scan folders (one path per line).
7. Choose subtitle languages: English is always on; Indonesian is optional.
8. For Video Feeds, open `/roxxy` → Settings → paste the manifest URL for each source you use.

### Suggested workflow

1. **Sync Cloud** to index TorBox torrents, Usenet, and WebDL.
2. **Scan Local** for existing folders (optional).
3. **Enrich Meta** for metadata, posters, classification, and runtimes.
4. **Find Subtitles** to fill subtitle gaps.
5. Open **Series** → choose a show → check season completeness → find missing episodes → **+ TorBox**.
6. Watch progress in the Series panel or **Downloads**; the episode imports into the Library automatically.
7. **Play**, **Play season**, or build a **Playlist**.

The modern interface is at **`/v2`**. The legacy interface at **`/`** keeps the deepest curation tools (manual edit, classification history, physical delete, poster override, subtitle audit). Video Feeds is at **`/roxxy`**.

---

## 🔧 Configuration

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `TBH_HOST` | `127.0.0.1` | Bind address; `0.0.0.0` for LAN |
| `TBH_PORT` | `5050` | HTTP port |
| `TBH_DATA_DIR` | project root | Location of `database/`, `cache/`, `playlists/`, `backups/` |
| `TBH_CONTAINER` | — | Set `1` in container/headless environments |
| `TBH_POSTER_HOST_ALLOWLIST` | empty | Optional poster host allowlist |
| `TORBOX_API_KEY` | — | Fallback if not stored in the DB |
| `TMDB_API_KEY` | — | Fallback |
| `TPDB_API_KEY` | — | Fallback |
| `NZBGEEK_API_KEY` | — | Fallback |
| `ROXXY_JAV_MANIFEST_URL` | — | JAV manifest |
| `ROXXY_ENGLISH_ADULT_MANIFEST_URL` | — | English Adult manifest |
| `ROXXY_ONLYFANS_MANIFEST_URL` | — | OnlyFans manifest |
| `ROXXY_INDO_MOVIE_MANIFEST_URL` | — | Indonesia Movies manifest |
| `ROXXY_DONGHUA_MANIFEST_URL` | — | Donghua manifest |
| `ROXXY_MANIFEST_URL`, `ROXXY_EXTRA_MANIFEST_URL` | — | Legacy aliases (English Adult / Donghua) |
| `ROXXY_FORWARD_HOSTS` | `roxxy-tech.com` | Forwarder hosts to decode |

### Runtime data

```text
TorBoxMediaHub/
├── database/torbox_media_hub.db
├── backups/
├── cache/            # posters + subtitles only
├── playlists/        # generated .m3u
├── logs/app.log
├── static/
└── templates/
```

`database/`, `cache/`, `playlists/`, `backups/`, `logs/`, `*.db`, `*.m3u`, and `*.zip` are git-ignored. Do not overwrite runtime folders when updating the source.

---

## 🧱 Project structure

```text
TorBoxMediaHub/
├── app.py                     # Flask app, API, Discovery, cache checks, monitor, tasks
├── database_manager.py        # SQLite WAL, schema, migrations, backups, secret masking
├── metadata_engine.py         # TMDb/TVMaze/AniList/JAVDatabase/TPDB, posters, runtime
├── classification_engine.py   # Deterministic offline classification
├── indo_keyword_engine.py     # Indonesian signal dictionary
├── subtitle_engine.py         # Four-tier subtitle retrieval
├── dual_subtitle_engine.py    # Dual-language track resolution
├── subtitle_quality_audit.py  # Subtitle quality audit
├── subtitle_match_validator.py
├── roxxy_module.py            # Five manifests, catalog/meta/stream, analysis
├── roxxy_download_engine.py   # Local downloads, queue, D1-D3 registration
├── webdl_resolver.py          # WebDL page URL mapping and validation
├── platform_support.py        # OS/executable/data directory detection
├── static/ui/                 # v2 CSS/JS (no framework)
├── templates/index.html       # Legacy UI
├── templates/home_v2.html     # v2 shell
├── templates/roxxy.html       # Video Feeds
├── tests/                     # Offline pytest suite
└── docs/                      # Guides, release notes, screenshots
```

---

## 🔒 Security and privacy

- **No login.** The default bind is loopback. Anyone who can reach the port can control downloads, delete files, and read settings.
- **Cross-site write guard.** Browser-initiated writes with a foreign `Origin`/`Referer` or `Sec-Fetch-Site: cross-site` are rejected.
- **Secrets are masked.** API keys are returned only as masked values; manifest tokens are never echoed back.
- **Signed stream URLs are not shown in the UI.** Playback and M3U links are generated server-side; the browser never displays them, although a downloaded M3U necessarily contains the links your player needs.
- **SSRF guards** on poster and subtitle fetching: private IPs, localhost, and `*.local` are rejected; redirects are manual and capped; size is capped.
- **Download-path guard** rejects OS trees, `Program Files*`, `ProgramData`, Startup folders, the app directory, and `\\?\` paths.
- **WebDL accepts page URLs only**, not direct media CDN links.
- **Local data only.** No telemetry, no analytics, no cloud database.

---

## 🧪 Development

```bash
python -m py_compile app.py roxxy_module.py database_manager.py
node --check static/ui/home.js
python -m pytest tests -q --no-header
```

Expected baseline: **425 passed, 63 subtests passed**. The suite is offline and uses temporary databases.

Version history and design decisions are documented in [`docs/CATATAN_VERSI_PENGEMBANGAN.md`](docs/CATATAN_VERSI_PENGEMBANGAN.md). The complete user guide is [`docs/DOKUMENTASI_APLIKASI.md`](docs/DOKUMENTASI_APLIKASI.md).

---

## 🩺 Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Reopen the terminal after installing Python; enable "Add to PATH" |
| `Activate.ps1` blocked | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Port 5050 in use | Stop the other process or set `TBH_PORT` |
| Empty catalog | Set the TorBox key, then run **Sync Cloud** |
| Missing posters | **Retry poster**; set a manual poster in the legacy UI; check the log |
| No duration | Install FFmpeg and ensure `ffprobe` is on `PATH` |
| Player not detected | Use **Browse** to select the executable manually |
| Android playback fails | Auto-Detect and choose a VLC/mpv/System Chooser intent |
| `CACHE ?` for NZB | Expected: TorBox caches Usenet by NZB file hash, not title |
| NZBGeek `unconfigured` | Add the premium key in Settings |
| Local download fails | Press **Retry**; the engine refreshes expired stream links once |
| `database is locked` | Run only one `app.py` instance against the database |
| Lost data after update | Restore from `backups/`; never overwrite runtime folders |

Logs: `logs/app.log`. When reporting a bug, include the version, OS, reproduction steps, and sanitized log lines — never API keys.

---

## ⚠️ Known limitations

- No authentication (loopback/trusted LAN by design for now).
- No official Dockerfile yet.
- No permanent M3U redirect endpoint; playlists contain links generated at creation time.
- The TorBox monitor is in-memory, so unfinished downloads are not re-tracked after a restart.
- Duplicate episodes remain counted in totals and **Play season**; ordering picks the best copy per episode.
- Some adult/JAV rows remain `needs_review` by design.
- Episode search is capped at 24 episodes per request; Roxxy search is capped at 4 catalogs / 3 meta / 8 stream calls.
- NZB cache status is unknown (`CACHE ?`).
- WebDL depends on TorBox support and yt-dlp extractors.
- Usenet features require an appropriate TorBox plan.
- `indo_movie` is not auto-enriched and does not use automatic subtitle search.
- The v2 UI does not yet include manual metadata editing, classification tools, or physical delete; use the legacy UI for those.
- Third-party addons and indexers can change or go offline.

---

## ❓ FAQ

**Does it download cloud videos to my disk?**  
No. Cloud videos stay on TorBox and stream through generated links. Only posters, subtitles, playlists, and the database are local.

**Do I need a paid TorBox plan?**  
For reliable streaming, cache additions, Usenet, and WebDL, yes. Plan limits are enforced by TorBox.

**Can I run it on Android without a PC?**  
Yes, through Termux. For large libraries and downloads, a PC/NAS host with Android as a LAN client is more reliable.

**Are my API keys safe?**  
They are stored locally and masked in the UI. The main risk is exposing the unauthenticated port.

**How do I fill a missing episode?**  
Series → season → **Find episode** → choose a release → **+ TorBox**. The episode appears automatically after import.

**Why do two copies of an episode exist?**  
Duplicates are intentionally not hidden; the best copy is preferred per episode by resolution and size.

**Can a TV or phone use the playlists?**  
Yes, over the same LAN using HTTP Range and remote M3U, if the port is reachable.

---

## 🗺️ Roadmap

- [x] Zero-download catalog and direct TorBox streaming (`v1.0`)
- [x] Multi-platform players, LAN streaming, playlists (`v1.1`)
- [x] Discovery, cache checker, background import, series tracking (`v1.1`)
- [x] D1–D3 local auto-registration (`v1.3D3`)
- [x] WebDL, Roxxy auto-add, download hardening (`v1.3D4`)
- [x] Five Video Feeds manifests + Indonesia Movies (`v1.3D9`)
- [x] Full v2 interface (`v1.3DF`)
- [x] Stratified missing-episode search (`v1.3DG`)
- [x] Live episode progress + auto-refresh (`v1.3DH`)
- [ ] Docker / Docker Compose
- [ ] Permanent `/stream/<unique_key>` redirect endpoint
- [ ] Optional authentication for LAN deployments

---

## 🤝 Contributing and support

- **Issues / PRs:** [github.com/rarafashions/TorBoxMediaHub](https://github.com/rarafashions/TorBoxMediaHub)
- **Ko-fi:** [ko-fi.com/rarafashions](https://ko-fi.com/rarafashions) — supporters can request custom features.
- **TorBox referral:** [7 bonus days](https://torbox.app/subscription?referral=d8962fcc-d39e-43f6-9a8a-f565bab5f809)

Before opening a PR, run the test suite and keep the existing expectations intact. Changes that alter classification behavior should include new tests and a classifier version bump.

---

## 📄 License

MIT License. See the repository license file.

This project does not host, bundle, or distribute media. You are responsible for the content you access and for complying with the laws and terms of service that apply to you. Third-party services (TorBox, TMDb, AniList, indexers, addons, subtitle providers) have their own terms and availability.