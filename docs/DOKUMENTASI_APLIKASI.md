# Dokumentasi Lengkap — TorBox Media Hub

**Versi aplikasi:** `v1.3DO`  
**Status dokumen:** 28 November 2026  
**Baseline tes:** `python -m pytest tests -q --no-header` → **535 passed, 196 subtests passed**

Dokumen ini menjelaskan aplikasi secara menyeluruh: fitur, cara menjalankan untuk pengguna baru, konfigurasi, mesin internal, keamanan, batasan, dan pemecahan masalah. Untuk catatan rilis dan keputusan pengembangan, lihat [`CATATAN_VERSI_PENGEMBANGAN.md`](CATATAN_VERSI_PENGEMBANGAN.md). Untuk draf README GitHub, lihat [`README_GITHUB_DRAFT.md`](README_GITHUB_DRAFT.md).

---

## Daftar Isi

1. [Ringkasan aplikasi](#1-ringkasan-aplikasi)
2. [Persyaratan sistem](#2-persyaratan-sistem)
3. [Mulai cepat untuk pengguna baru](#3-mulai-cepat-untuk-pengguna-baru)
4. [Konfigurasi pertama](#4-konfigurasi-pertama)
5. [Dua antarmuka: UI lama dan UI v2](#5-dua-antarmuka-ui-lama-dan-ui-v2)
6. [Fitur per tampilan](#6-fitur-per-tampilan)
7. [Video Feeds (Roxxy)](#7-video-feeds-roxxy)
8. [Mesin internal dan pipeline](#8-mesin-internal-dan-pipeline)
9. [Data, backup, dan pembaruan](#9-data-backup-dan-pembaruan)
10. [Ringkasan API](#10-ringkasan-api)
11. [Variabel lingkungan dan pengaturan](#11-variabel-lingkungan-dan-pengaturan)
12. [Keamanan dan privasi](#12-keamanan-dan-privasi)
13. [Pemecahan masalah](#13-pemecahan-masalah)
14. [Batasan yang diketahui](#14-batasan-yang-diketahui)
15. [Checklist uji manual](#15-checklist-uji-manual)
16. [FAQ](#16-faq)

---

## 1. Ringkasan aplikasi

**TorBox Media Hub** adalah aplikasi web self-hosted berbasis Python + Flask untuk mengelola koleksi video **TorBox Cloud** (torrent dan Usenet) dan **file lokal** dalam satu katalog visual bergaya Netflix.

Aplikasi ini tidak mengunduh video cloud ke disk. Video cloud tetap berada di TorBox dan diputar lewat tautan `requestdl` yang dibuat saat tombol Putar ditekan. Untuk media cloud, yang disimpan lokal hanya poster, subtitle, playlist `.m3u`, dan database SQLite. Unduhan lokal (Video Feeds) adalah tindakan eksplisit pengguna dan memang menulis file video ke folder yang dipilih.

Kemampuan utama:

| Kelompok | Kemampuan |
|---|---|
| Katalog | Library visual, pencarian, filter kategori/sumber/subtitle/cover/status tonton/status klasifikasi, rating bintang, tandai ditonton, mode pilih massal |
| Serial | Pengelompokan serial TV/anime/donghua per judul, panel season, kelengkapan episode resmi, pencarian rilis episode hilang, progres unduhan, auto-add ke Library, putar season |
| Discovery | Feed Trending/Popular/Top Rated/Search untuk Movies, TV Shows, Anime, Donghua; cek cache TorBox lintas indexer; kirim magnet/NZB ke TorBox; quality upgrade |
| Video Feeds (Roxxy) | Lima manifest addon (JAV, English Adult, OnlyFans, Indonesia Movies, Donghua), katalog, pencarian, metadata, episode, analisis stream, unduhan lokal, WebDL, badge IN LIBRARY |
| TorBox | Sinkronisasi cloud, cek cache, tambah torrent/NZB/WebDL, monitor unduhan, impor otomatis, enrichment, poster, subtitle, hapus remote aman |
| Lokal | Scan folder rekursif, `ffprobe` durasi, sidecar subtitle, streaming HTTP Range untuk LAN, unduhan via yt-dlp/aria2c |
| Metadata | TMDb, TVMaze, AniList, JAVDatabase, TPDB; klasifikasi deterministik dengan status `classified`/`needs_review`/manual lock |
| Subtitle | Empat lapis: sidecar lokal, arsip TorBox, track tertanam (`ffprobe`), provider publik (OpenSubtitles/SubtitleCat); dua bahasa (EN/ID); audit kualitas |
| Playlist | Buat playlist, tambah/hapus item, unduh `.m3u`, putar berurutan, M3U remote untuk perangkat LAN |
| Pemutar | Deteksi otomatis VLC/MPV/PotPlayer/MPC-HC/MPC-BE/KMPlayer di Windows, VLC/MPV/IINA/Celluloid/SMPlayer/Haruna di Linux/macOS, intent Android |
| Database | SQLite WAL, migrasi berversi, backup/restore, maintenance, reset metadata, hapus semua data media |

---

## 2. Persyaratan sistem

### Wajib

| Komponen | Keterangan |
|---|---|
| Python | **3.10+** (disarankan 3.12) |
| Git | Untuk clone repo; boleh diganti unduhan ZIP |
| `flask`, `requests`, `beautifulsoup4` | Terdaftar di `requirements.txt` |
| Browser modern | Chrome, Edge, Firefox, Safari, Brave |

### Sangat disarankan

| Komponen | Fungsi bila tidak ada |
|---|---|
| **FFmpeg + ffprobe** | Durasi video, deteksi subtitle tertanam, dan beberapa validasi tidak berjalan |
| **VLC / MPV / IINA** | Pemutaran tidak bisa dibuka dari server |
| **yt-dlp** | Unduhan lokal HLS/DASH dan fallback stream tidak tersedia |
| **aria2c** | Unduhan file langsung kehilangan mode multi-koneksi (masih bisa lewat yt-dlp) |

### Akun dan API key

| Key | Wajib? | Fungsi |
|---|---|---|
| **TorBox API key** | Untuk fitur cloud | Sinkronisasi cloud, cek cache, tambah torrent/NZB/WebDL, streaming |
| **TMDb API key (v3)** | Sangat disarankan | Metadata film/serial, poster, struktur season resmi |
| **TPDB API key** | Opsional | Metadata English Adult dari ThePornDB |
| **NZBGeek API key** | Opsional/premium | Pencarian NZB di panel Serial; NZBIndex gratis tetap dipakai |

Usenet TorBox umumnya memerlukan paket **Pro**. WebDL dan batas ukuran/plan mengikuti akun TorBox.

---

## 3. Mulai cepat untuk pengguna baru

### 3.1 Windows 10/11

Buka **PowerShell** (bukan Command Prompt bila memakai `Activate.ps1`).

```powershell
winget install Python.Python.3.12 Git.Git Gyan.FFmpeg VideoLAN.VLC yt-dlp.yt-dlp aria2.aria2
```

Tutup dan buka ulang PowerShell agar `python`, `git`, `ffmpeg`, `ffprobe`, `yt-dlp`, dan `aria2c` masuk `PATH`.

```powershell
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Bila aktivasi venv ditolak kebijakan eksekusi:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

Buka `http://127.0.0.1:5050`.

> Alternatif tanpa venv: `py -3 -m pip install -r requirements.txt` lalu `py -3 app.py`. Venv lebih disarankan agar paket sistem tidak berubah.

### 3.2 Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git ffmpeg vlc mpv aria2
python3 -m pip install --user yt-dlp   # atau: sudo apt install -y yt-dlp
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Fedora:

```bash
sudo dnf install -y python3 python3-pip python3-tkinter git ffmpeg vlc mpv aria2
```

Arch:

```bash
sudo pacman -S --needed python tk git ffmpeg vlc mpv aria2 yt-dlp
```

`python3-tkinter`/`tk` hanya diperlukan untuk dialog file/folder native. Di server headless, aplikasi otomatis memakai browser filesystem bawaan.

### 3.3 macOS (Intel dan Apple Silicon)

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

Buka `http://127.0.0.1:5050` di Safari/Chrome. Bila `python3 -m venv` gagal karena Python dari Homebrew, pastikan `brew install python` selesai dan `python3 --version` ≥ 3.10.

### 3.4 Android (Termux)

Aplikasi berjalan langsung di perangkat Android. Pemutaran memakai intent ke VLC/mpv Android.

1. Pasang **Termux** dari [F-Droid](https://f-droid.org/en/packages/com.termux/) (versi Play Store sering kedaluwarsa), lalu pasang **VLC for Android** dan/atau **mpv-android**.
2. Di Termux:

```bash
pkg update && pkg upgrade -y
pkg install -y python git ffmpeg aria2 yt-dlp
termux-setup-storage
git clone https://github.com/rarafashions/TorBoxMediaHub.git
cd TorBoxMediaHub
pip install -r requirements.txt
python app.py
```

3. Buka `http://127.0.0.1:5050` di browser Android.
4. Buka **Pengaturan → Media player → Auto-Detect**, pilih **VLC for Android (Intent)**, **mpv-android (Intent)**, atau **Android System Chooser**.
5. Agar proses tidak dibunuh sistem saat layar mati:

```bash
termux-wake-lock
```

Untuk folder unduhan, atur di `/roxxy` ke lokasi yang dapat ditulis, misalnya `~/storage/shared/Movies/TorBox`. Jangan menunjuk ke root penyimpanan, `Android/data`, atau folder sistem.

> Catatan: `pip install` di Termux mengompilasi beberapa paket. Bila gagal, jalankan `pkg install -y build-essential python-dev` lalu ulangi. Pengalaman terbaik tetap memakai PC/NAS sebagai host dan Android sebagai klien LAN.

### 3.5 Menjalankan ulang dan menghentikan

```bash
python app.py
```

Hentikan dengan **Ctrl+C** di terminal. Di Windows, jendela konsol harus tetap terlihat agar traceback tidak hilang. Log juga ditulis ke `logs/app.log` (rotasi 5 MB, 4 berkas).

Port default **5050**. Bila bentrok:

```powershell
$env:TBH_PORT="8080"; python app.py
```

```bash
TBH_PORT=8080 python app.py
```

### 3.6 Verifikasi instalasi (opsional, untuk pengembang)

```bash
python -m pytest tests -q --no-header
```

Baseline yang diharapkan: **425 passed, 63 subtests passed**. Tes berjalan offline dan memakai database sementara, bukan database aplikasi.

---

## 4. Konfigurasi pertama

1. Buka **Pengaturan** (UI v2) atau **Settings & DB** (UI lama).
2. Isi **TorBox API Key** dari `https://torbox.app/settings`. Key disimpan di database dan hanya ditampilkan tersamarkan.
3. Isi **TMDb API Key (v3)**. Ini diperlukan untuk metadata film/serial dan struktur season.
4. Opsional: **TPDB API Key** untuk English Adult, **NZBGeek API Key** untuk pencarian NZB premium.
5. Klik **Auto-Detect** pemutar, atau isi path manual. Di Windows path biasanya `C:\Program Files\VideoLAN\VLC\vlc.exe`; di macOS `/Applications/VLC.app/Contents/MacOS/VLC`; di Linux `/usr/bin/vlc`.
6. Tambahkan **folder scan lokal** bila punya koleksi di disk (satu path per baris).
7. Pilih bahasa subtitle: **English selalu aktif**, **Indonesian** dapat diaktifkan.
8. Simpan.

Konfigurasi Video Feeds terpisah: buka `/roxxy` → **Pengaturan** → tempel manifest URL per sumber → simpan folder unduhan.

### Alur pemakaian yang disarankan

1. **Sync Cloud** untuk memasukkan seluruh isi TorBox ke katalog.
2. **Scan folder lokal** bila ada koleksi lokal.
3. **Enrich Meta** untuk metadata, poster, klasifikasi, dan durasi.
4. **Find Subtitles** untuk melengkapi subtitle.
5. Buka **Serial** → pilih judul → cek kelengkapan season → cari episode hilang → **+ TorBox**.
6. Pantau di panel **Unduhan episode** atau view **Unduhan**; setelah selesai, episode otomatis masuk Library dan berubah menjadi **Putar**.
7. Buat **Playlist** atau langsung **Putar** / **Putar season**.

---

## 5. Dua antarmuka: UI lama dan UI v2

| | UI lama `/` | UI v2 `/v2` |
|---|---|---|
| Gaya | Tailwind single-page, padat | Netflix/Prime-style, `static/ui/*` tanpa framework |
| Navigasi | Library, Discovery, Settings & DB | Beranda, Discovery, Library, Serial, Playlist, Unduhan, Statistik, Pengaturan |
| Discovery | Modal penuh 4 kategori | View Discovery: 4 kategori + tab English Adult 18+ |
| Detail | Modal lengkap: edit manual, preview/apply klasifikasi, riwayat, hapus fisik, poster manual, audit subtitle | Panel detail: putar, tonton, rating, subtitle EN/ID, enrich, poster retry, playlist, **kurasi (edit metadata, klasifikasi, poster, hapus fisik)** |
| Serial | Kartu/grup + flat episode | Kartu serial + panel season dengan pencarian episode dan progres unduhan |
| Status | Fitur terlengkap untuk kurasi data | Fitur harian dan pemantauan |

Keduanya memakai API, database, dan mesin yang sama. Route lama tidak diubah; v2 hanya menambah tampilan.

---

## 6. Fitur per tampilan

### 6.1 Beranda (v2)

- **Billboard** untuk satu item pilihan dengan poster latar dan aksi **Putar**.
- **Strip statistik**: total item, cloud/lokal, sudah ditonton, punya subtitle, jumlah playlist. Klik kartu membuka filter terkait.
- **Pintasan**: Library lengkap, Serial & episode, Playlist, Pusat unduhan, Discovery (v2), Video Feeds.
- **Rail**: Baru Ditambahkan, Belum Ditonton, Rating Tertinggi, lalu satu rail per kategori yang memiliki isi.

### 6.2 Library

Filter yang tersedia:

- **Chip kategori**: Semua, General Movie, TV Show, Anime, Donghua, JAV, English Adult, Indonesia Movies, Indo, Uncategorized (sesuai isi DB).
- **Sumber**: semua, TorBox Cloud, lokal.
- **Status tonton**: semua, belum, sudah.
- **Subtitle**: semua, ada, tanpa.
- **Cover**: semua, ada, tanpa.
- **Status klasifikasi**: semua, **Needs Review**, terklasifikasi, manual.
- **Urutkan**: terbaru, terlama, judul, rating, tahun, ukuran; untuk serial ada episode dan progres.
- **Mode grup**: serial digabung atau episode terpisah.
- **Reset filter**.

Aksi:

- **Pilih** → mode pilih massal; item terpilih bisa ditandai ditonton/belum dan diputar berurutan.
- **Segarkan** memuat ulang bootstrap tanpa kehilangan posisi overlay.
- Klik kartu membuka detail.
- Di UI lama tersedia **Select All/Clear Selection**, scope playlist (Unassigned/In Playlists/All), dan bulk add ke playlist.

Detail media (v2) menampilkan poster, judul, tahun, durasi, rating bintang, status tonton, studio, sutradara, sumber, ukuran, kode JAV, seri/season/episode, status klasifikasi, kategori, playlist, tag, dan nama file.

Aksi detail v2:

- **Putar** (cloud atau lokal).
- **Tandai ditonton/belum**.
- **Rating 1–5 bintang**.
- **Cari subtitle ID / EN** (per bahasa, tersimpan di `media_subtitles`).
- **Enrich ulang** metadata.
- **Coba poster** bila cover gagal.
- **Tambah/hapus dari playlist**.

Aksi detail UI lama (lebih lengkap untuk kurasi):

- **Preview Classification** dan **Apply** (dengan history).
- **Edit manual** judul, kategori, tahun, studio, plot, season/episode, kode, lock klasifikasi.
- **Poster manual**: tempel URL, retry, atau kembali ke poster otomatis.
- **Find Subtitle** dan **Run Audit** kualitas subtitle.
- **Delete Physical**: hapus file lokal dan/atau item TorBox dengan proteksi paket multi-file.
- **Play in VLC/MPV/custom**.

### 6.3 Serial

Kartu serial menggabungkan episode berdasarkan `series_title` dan kategori (`tv_show`, `anime`, `donghua`). Setiap kartu menampilkan jumlah episode, jumlah season, dan progres.

Panel serial:

- **Tab season** dengan hitungan `<ada>/<jumlah resmi>` (mis. `Season 02 · 1/3`).
- Daftar episode `E01…N`. Episode yang ada punya tombol **Putar** dan toggle tonton; episode hilang tampil bergaris putus-putus dengan tombol **Cari episode**.
- **Cari semua episode hilang**.
- **Putar season** dan **Tandai season ditonton**.
- **Putar Episode Berikutnya** pada detail episode/serial.
- Status: `x episode tersedia`, daftar `Belum ada: E..`, atau pesan resmi dari metadata.
- Sumber struktur resmi: TMDb untuk TV Show (fallback TVMaze), AniList untuk anime/donghua (fallback Jikan, tidak memakai TMDb); cache season disimpan di `series_season_cache`.

Pencarian episode:

- Sumber: TorBox cache, TPB/APIBay, EZTV (via IMDb dari TMDb), Torrentio, Nyaa (anime/donghua), NZBIndex (gratis), NZBGeek (bila key premium), dan Roxxy (anime/donghua).
- Judul TV memakai nama kanonik **TVMaze** (cache 6 jam) agar pencarian tidak memakai judul library yang berantakan.
- Hasil menampilkan **CACHED/UNCACHED/CACHE ?**, sumber, seed/leech/peer, ukuran, kualitas, dan tombol **+ TorBox**.
- **CACHE ?** berarti status NZB tidak diketahui: TorBox meng-cache Usenet berdasarkan hash file NZB, bukan judul, sehingga aplikasi tidak mengklaim cached.
- Maksimal **24 episode** per pencarian; UI menandai bila dipotong.
- Rilis di-dedupe per `(kind, hash, episode)` dan diurutkan cached → seeders → kualitas → ukuran.

Progres unduhan dan auto-add:

- Setelah **+ TorBox**, panel menampilkan tray **Unduhan episode**: progress bar, persen, state TorBox, pill `IMPORT` saat registrasi Library.
- Polling UI tiap **5 detik**; backend TorBox dipoll tiap **10 detik**.
- Setelah impor selesai, muncul toast `E03 selesai diimpor ke Library`, katalog dan status season dimuat ulang diam-diam, rilis episode itu dihapus dari hasil pencarian, dan baris berubah menjadi **Putar**.
- Hint `series/season/episode` disimpan di server. Untuk unduhan **satu file video**, nomor episode dipaku ke permintaan; **paket season** tetap diparse per file.

Duplikat episode:

- Duplikat **tidak disembunyikan**. Untuk setiap episode, salinan terbaik dipilih berdasarkan resolusi lalu ukuran file.
- Duplikat masih ikut terhitung di total Library/statistik dan di **Putar season**; ini batasan yang diketahui (lihat bagian 14).

### 6.4 Playlist

- Buat playlist baru.
- Tambah media dari detail atau picker; hapus item.
- **Unduh M3U** berisi `#EXTINF` durasi, opsi subtitle eksternal `#EXTVLCOPT:sub-file=...`, dan tautan streaming baru.
- **Putar playlist** berurutan.
- **M3U remote** memakai URL HTTP server (mis. `http://192.168.1.10:5050/api/media/<id>/stream`) agar bisa dibuka pemutar di perangkat LAN.
- File playlist disimpan di `playlists/`.

### 6.5 Unduhan (v2)

Empat panel:

1. **Tugas latar**: tipe task, pesan, progress `current/total`, 8 baris log terakhir.
2. **TorBox Cloud**: item aktif, item yang sedang disinkronkan/impor, dan riwayat impor terbaru.
3. **Unduhan lokal**: antrean job, status, progres, engine, pesan; tombol **Coba ulang** dan **Bersihkan**.
4. **Validasi & registrasi Library**: antrean D1–D3 dengan status `queued`, `validating`, `identified`, `registering`.

Aksi: **Sync Cloud**, **Scan Local**, **Classify New**, **Enrich Meta**, **Find Subtitles**, buka Video Feeds. Halaman diperbarui otomatis tiap **5 detik**; poll berhenti saat view ditinggalkan.

### 6.6 Statistik

- Total item, cloud, lokal, sudah ditonton, persentase subtitle pada item non-adult.
- Bar distribusi kategori.
- Kelengkapan metadata: cover, subtitle, klasifikasi.
- Status klasifikasi: classified, needs review, manual, pending.
- Lingkungan: OS, Python, headless/docker, ffmpeg/ffprobe, URL LAN, data dir.
- Daftar backup database.

### 6.7 Pengaturan (v2)

Enam panel:

1. **API keys** — TorBox, TMDb, TPDB, NZBGeek. Nilai tersimpan hanya tampil sebagai badge "Tersimpan"; input selalu kosong; menyimpan input kosong tidak menghapus key lama.
2. **Media player** — VLC/MPV/custom, path, argumen custom, **Auto-Detect**, **Browse**, reset path default OS.
3. **Folder lokal** — textarea satu path per baris, Browse folder, tombol scan.
4. **Subtitle** — English selalu aktif, toggle Indonesian, statistik Complete/Ready/Partial/Not Ready.
5. **Lingkungan** — platform, Python, mode headless/docker, ffmpeg/ffprobe, URL LAN, data dir.
6. **Database & cache** — backup, maintenance/VACUUM, reset metadata & poster, hapus semua media (dengan konfirmasi), daftar backup dan restore.

### 6.8 Discovery (v2)

Route `#/discover`, tombol nav **Discovery**. Dua tab:

- **Film & Serial** — empat kategori: **Film**, **Serial TV**, **Anime**, **Donghua** (kategori adult/Indo tetap ditolak `400`). Urutan **Trending/Populer/Rating** memakai `GET /api/discover`; kotak pencarian memakai `feed=search`.
- Kartu membuka sheet detail (poster, sinopsis, tahun/rating, jumlah episode). Tombol **Cari rilis** memanggil `POST /api/discover/search-cache` dengan `query`, `category`, `tmdb_id`, `year`, dan `season`/`episode` untuk serial; hasil menampilkan badge **CACHED/UNCACHED**, sumber, dan tombol **Tambahkan** (`POST /api/torbox/add-cached` + `delete_remote_on_replace`). Pencarian tidak dijalankan otomatis karena satu query indexer butuh puluhan detik.
- **Toolbar tunggal**: tab (Film & Serial / English Adult 18+), chip kategori, chip urutan, dan status (jumlah judul / memuat / galat) berada dalam satu baris; status rata kanan dan chip membungkus sendiri di layar sempit, sehingga grid kartu langsung berada di bawah kolom pencarian.
- **English Adult 18+** — gate sekali per browser (`tbh_adult_v2_gate`) sebelum katalog tampil.
- **Sumber unduhan (English Adult)** — detail item menampilkan stream dari `/api/adult/streams`. Setiap stream dianalisis `/api/roxxy/analyze` dan menawarkan **Cached TorBox**, **Torrent ke TorBox**, **NZB ke TorBox** (`/api/torbox/add-cached`), atau **yt-dlp/aria2c** lokal (`/api/roxxy/local/start`). Sumber dan status diambil dari `GET /api/adult/providers`; katalog dari `GET /api/adult/feed`/`GET /api/adult/search` (kata kunci minimal 2 karakter). Detail membaca cover, sinopsis, dan durasi dari halaman provider (`GET /api/adult/detail`, `POST /api/adult/streams`), dengan TPDB sebagai pelengkap. Tombol **Simpan ke TorBox Cloud (WebDL)** memakai `POST /api/adult/webdl` (batas 100 GB per tautan) dan hasilnya masuk Library lewat auto-add.


Teks sekunder dan tersier memakai kontras minimum 4,5:1 terhadap kanvas gelap (`--text-2` ≈8,3:1, `--text-3` ≈5,8:1); batas ini dijaga tes otomatis.

### 6.9 Discovery Hub (UI lama)

- Empat kategori: **Movies**, **TV Shows**, **Anime**, **Donghua**. Kategori adult/Indo ditolak `400` dan ditangani di Video Feeds.
- Mode: Trending, Popular, Top Rated, Search.
- Sumber metadata: TMDb untuk film/serial, AniList untuk anime/donghua.
- Pencarian cache TorBox: Torrentio, YTS, TPB/APIBay, Nyaa, EZTV (serial), NZBIndex (bila TorBox Pro). Status cached/uncached per kandidat.
- **+ TorBox** untuk magnet/NZB, dengan opsi **Upgrade / Replace Existing File** (hapus file lama setelah upgrade selesai).
- Poster di-hotlink dari TMDb/AniList; proxy poster tersedia dengan guard SSRF.

---

## 7. Video Feeds (Roxxy)

Route `/roxxy`. Nama tampilan "Video Feeds", tetapi route dan nama modul tetap `roxxy` demi kompatibilitas.

### 7.1 Sumber manifest

Lima slot terpisah, masing-masing punya token manifest sendiri:

| Sumber | Kategori unduhan | Catatan |
|---|---|---|
| JAV | `jav` bila kode produksi terdeteksi; jika tidak, verdict adult/uncategorized | Katalog JAV juga memuat konten tanpa kode |
| English Adult | `english_adult` | Termasuk konten adult non-Inggris |
| OnlyFans | `english_adult` | Dikunci oleh manifest |
| Indonesia Movies | `indo_movie` (non-adult) | Cover/subtitle memakai Roxxy; bila kosong diisi manual, tidak di-enrich otomatis |
| Donghua | `donghua` | Serial episode |

Manifest URL harus HTTPS dari host Roxxy dan berakhiran `/manifest.json`. Token tidak pernah dikembalikan ke browser; nilai yang ditampilkan disamarkan.

### 7.2 Katalog dan pencarian

- Dropdown **Sumber** dan **Katalog**; katalog hanya dari sumber aktif.
- **Cari semua katalog** hanya menghubungi katalog yang mendeklarasikan extra `search`; katalog yang tidak mendukung tidak dipanggil.
- Hasil muncul bertahap per katalog (`Promise.allSettled`), dedupe `type:id`.
- **Filter katalog** (`cat:<source>:<type>:<id>`) hanya menyembunyikan kartu di klien; **Genre** memuat ulang katalog dari addon.
- **Muat hasil berikutnya** mengambil halaman lanjutan tiap katalog yang belum habis.

### 7.3 Metadata dan episode

- Judul, poster/background, deskripsi, runtime, genre, studio, kode JAV, dan `in_library`/`library_match`.
- Badge **IN LIBRARY** mencocokkan kode JAV lebih dulu, lalu judul/nama file ternormalisasi. Baris episodic tidak dicocokkan hanya lewat judul serial.
- Panel **Episode** untuk serial: dropdown season (bila >1), chip episode dengan tanda ✓, episode terbaru dipilih otomatis.
- Metadata diklasifikasikan lokal (tanpa network) sebelum registrasi D3.

### 7.4 Analisis stream

Setiap stream dianalisis otomatis (3 probe paralel):

- **Magnet** → opsi cached TorBox / torrent ke TorBox.
- **NZB / forwarder** → NZB ke TorBox Usenet.
- **HTTP langsung** → yt-dlp dan/atau aria2c lokal.
- Stream yang gagal di-probe header menampilkan tombol nonaktif dengan alasan "perlu probe header", bukan error 400.

Pemeriksaan cache TorBox dilakukan batch (satu permintaan `checkcached` untuk banyak hash, maksimal 100 per panggilan).

### 7.5 Unduhan lokal

- Engine: **aria2c** untuk file langsung (4 koneksi, split 4, `min-split-size=1M`, 3 percobaan), **yt-dlp** untuk HLS/DASH (`--concurrent-fragments 4`, retry fragmen 10) dan fallback aria2c.
- Concurrency worker: 3 job.
- Retry maksimal 3× per job; tautan stream di-refresh bila terdeteksi kedaluwarsa/transien.
- Output dipindai untuk menemukan file hasil sebenarnya, termasuk ekstensi pilihan yt-dlp.
- Riwayat job dibatasi 50; job aktif dan job registrasi tidak dibuang oleh **Bersihkan**.
- Path unduhan divalidasi: menolak OS tree, `Program Files*`, `ProgramData`, folder Startup, direktori aplikasi, dan prefix `\\?\`.

### 7.6 WebDL TorBox

- TorBox menerima **URL halaman publik**, bukan URL media langsung.
- Provider yang dipetakan: Pornhub, Eporner, XHamster, RedGifs, XVideos, YouJizz, TNAFlix, EMPFlix.
- Hoster yang didokumentasikan tidak didukung (Keep2Share, FileJoker, Spotify, Netflix, dll.) ditolak dengan pesan jelas sebelum request.
- Dedupe 15 menit dan batas 60 submit/jam.
- Setelah WebDL dibuat, monitor TorBox mengimpor dan meng-enrich seperti torrent.

### 7.7 Auto-add ke Library (D3)

- Unduhan lokal: worker registrasi memvalidasi file (ada, ukuran > 0, ekstensi didukung, ukuran stabil), mengidentifikasi metadata, mengklasifikasi, mencegah duplikat JAV, lalu menulis ke `media_items` dengan `source_type='local'` dan `unique_key=local:<path>`.
- Unduhan TorBox: `_sync_torbox_cloud_worker` mengimpor file, menerapkan hint metadata/subtitle, cover, dan klasifikasi.
- Subtitle Roxxy yang disediakan provider diunduh lebih dulu (EN/ID, guard SSRF, batas 5 MB), lalu fallback ke subtitle engine.
- `indo` → subtitle policy `skip`; kategori lain `eligible_if_needed`.
- Status registrasi terlihat di panel **Validasi & registrasi Library**.

---

## 8. Mesin internal dan pipeline

### 8.1 Metadata engine (`metadata_engine.py`)

Urutan prioritas enrichment:

1. **JAV** — kode produksi tervalidasi (FC2-PPV, HEYZO, CARIB, prefix studio), anti-false-positive terhadap tag codec/audio; poster/aktris/studio/runtime dari JAVDatabase.
2. **English Adult** — studio/scene dari TPDB, terisolasi dari API mainstream.
3. **Anime/Donghua** — fansub group, karakter CJK, negara asal (JP vs CN/TW) via AniList dan TMDb.
4. **Movie/TV** — TMDb, fallback TVMaze; season/episode `SxxExx`.
5. **Runtime** — `ffprobe` pada file lokal atau stream TorBox.
6. **Poster** — cache lokal, sumber otomatis, retry, manual override; status poster dicatat (`poster_status`, `poster_error`, `poster_source`, `poster_is_manual`).

Judul dinormalisasi: sufiks rilis, grup fansub, tag teknis, dan nama folder generik dibersihkan agar pencarian TMDb/AniList tidak gagal.

### 8.2 Classification engine (`classification_engine.py`)

Klasifikasi deterministik, tanpa network. Urutan kategori:

| Prioritas | Kategori | Bukti utama |
|---|---|---|
| 1 | `indo` | Kamus dan sinyal lokal Indonesia (judul, tag, deskripsi, nama file/folder); minimal 2 sinyal pendukung |
| 2 | `jav` | Kode produksi tervalidasi; kode saja tanpa bukti lain bisa `needs_review` |
| 3 | `english_adult` | Judul/tag/studio/network adult; deskripsi saja tidak cukup |
| 4 | `tv_show` | Struktur episode + sinyal serial |
| 5 | `anime` | Fansub/CJK/negara asal |
| 6 | `donghua` | Sinyal CN/TW |
| 7 | `general_movie` | Film dengan bukti identifikasi memadai |
| 8 | `uncategorized` | Bukti tidak cukup |

Status:

- `classified` — verdict melewati ambang (70).
- `needs_review` — bukti ambigu; tidak dipaksa masuk kategori.
- `manual` — dikunci pengguna; reklasifikasi otomatis tidak menurunkan kategori adult/Indo.
- `pending` — belum diproses.

`CLASSIFIER_VERSION = "d2-multicat-5"`. Setiap perubahan aturan menaikkan versi agar baris lama bisa diklasifikasi ulang.

Atribut adult (tag seperti `amateur`, `verified`, `swinger`) dicatat terpisah dan tidak membuat kategori baru.

### 8.3 Subtitle engine (`subtitle_engine.py`)

Empat lapis:

1. **Sidecar lokal** — `.srt`, `.ass`, `.ssa`, `.vtt`, `.sub` di samping video; tag `SxxExx` dicek agar tidak salah episode.
2. **Arsip TorBox** — indeks file subtitle di dalam torrent/Usenet; hanya teks kecil yang di-cache.
3. **Track tertanam** — `ffprobe` pada file lokal atau stream TorBox.
4. **Provider publik** — SubtitleCat (JAV) dan OpenSubtitles REST, dengan pencocokan season/episode.

Fitur tambahan:

- Dua bahasa (EN/ID) dengan track terverifikasi di `media_subtitles`.
- Subtitle yang disediakan provider Roxxy diunduh lebih dulu.
- **Audit kualitas subtitle** (`subtitle_quality_audit.py`): cek encoding, timing, bahasa, dan kecocokan; hasil batch disimpan di memori untuk view hasil.
- Pencarian subtitle **tetap dijalankan untuk `english_adult`** (keputusan pengguna: audio non-Inggris tetap butuh subtitle).
- `indo_movie` tidak menjalankan pencarian subtitle otomatis; memakai payload Roxxy atau manual.

### 8.4 Integrasi TorBox

- **Sync Cloud**: `torrents/mylist`, `usenet/mylist`, `webdl/mylist` → satu transaksi SQLite; tidak mengunduh video.
- **Cek cache**: hash torrent/NZB dikirim batch ke `/checkcached`.
- **Tambah**: magnet (`/torrents/createtorrent`), NZB (`/usenet/createusenetdownload`), WebDL (`/webdl/createwebdownload`).
- **Monitor** (`TorboxDownloadMonitor`): in-memory, poll 10 detik selama ada item; mendeteksi selesai, memindahkan ke `syncing`, menjalankan impor + enrichment + subtitle + poster; menyimpan `recently_imported` dengan TTL 15 menit untuk dibaca UI tanpa saling merebut event.
- **Impor**: file video dari parent yang selesai; hint `torbox_import_hints` (kategori, poster, metadata, subtitle, series hint) bertahan setelah restart.
- **Quality upgrade**: `replace_media_id` + `delete_remote_on_replace`; file lama dihapus setelah yang baru selesai.
- **Hapus remote aman**: proteksi paket multi-file agar season pack tidak ikut terhapus.

Monitor bersifat in-memory. Setelah restart, unduhan yang belum selesai tidak dipantau ulang otomatis; hint tetap tersimpan untuk impor berikutnya bila item dilacak lagi.

### 8.5 Pipeline lokal D1–D3

| Tahap | Isi |
|---|---|
| **D1** | Job selesai → task `roxxy_library_tasks`; validasi file ada, ukuran > 0, ekstensi didukung, ukuran stabil; recovery task terputus |
| **D2** | Klasifikasi berurutan, attributes, kode JAV, duplicate lookup, subtitle policy |
| **D3** | Registrasi ke `media_items` (`source_type='local'`, `unique_key=local:<path>`), poster Roxxy, subtitle, idempotent, recovery `registering` → `identified` saat restart |

Duplicate JAV: hanya kategori `jav` dengan `code_id` yang dicek; task duplikat tidak diregistrasi.

### 8.6 Pencarian rilis episode

Sumber dan batas:

| Sumber | Cakupan | Catatan |
|---|---|---|
| TorBox cache | Semua hash torrent yang ditemukan | Batch 25 |
| TPB/APIBay | Umum | Seed/leech |
| EZTV | TV via IMDb dari TMDb | Semua episode sekaligus |
| Torrentio | IMDb/TMDb | Seed/size dari metadata judul |
| Nyaa | Anime/Donghua | RSS, seed/leech |
| NZBIndex | Umum | Gratis, cache `null` |
| NZBGeek | Umum | Butuh key premium |
| Roxxy | Anime/Donghua | Katalog ber-extra `search`, maks 4 katalog/3 meta/8 stream, cache 10 menit |

Filter judul mencegah "Dark" mengambil "Dark Matter". Error indexer disanitasi agar API key tidak bocor.

---

## 9. Data, backup, dan pembaruan

### 9.1 Lokasi data

Default di root proyek, dapat dipindah dengan `TBH_DATA_DIR`:

```text
TorBoxMediaHub/
├── database/torbox_media_hub.db     # SQLite utama (WAL)
├── backups/                          # backup .db
├── cache/                            # poster + subtitle
├── playlists/                        # file .m3u
├── logs/app.log                      # log rotasi
└── static/, templates/               # aset aplikasi
```

Folder `database/`, `cache/`, `playlists/`, `backups/`, `logs/`, `*.db`, `*.m3u`, dan `*.zip` diabaikan Git. Jangan menimpa folder tersebut saat memperbarui source.

### 9.2 Tabel utama

| Tabel | Isi |
|---|---|
| `media_items` | Katalog media: identitas, path, metadata, kategori, klasifikasi, poster, subtitle, rating, watched |
| `media_subtitles` | Track subtitle per bahasa + status verifikasi |
| `playlists`, `playlist_items` | Playlist dan urutan item |
| `settings` | API key, path pemutar, folder scan, manifest Roxxy, folder unduhan |
| `subtitle_preferences` | Preferensi bahasa |
| `series_season_cache` | Cache struktur season resmi |
| `roxxy_library_tasks` | Antrean D1–D3 |
| `torbox_import_hints` | Hint impor TorBox (kategori, metadata, subtitle, series hint) |
| `media_classification_history` | Riwayat perubahan klasifikasi |
| `schema_migrations` | Versi migrasi |

### 9.3 Backup dan restore

- **Backup Database** membuat salinan di `backups/`.
- **Restore** memilih berkas backup; aplikasi sebaiknya dijalankan tanpa task aktif.
- Sebelum migrasi/percobaan berisiko, buat backup manual.
- **Maintenance & Cleanup** menjalankan VACUUM dan pembersihan.
- **Reset Metadata & Posters** menghapus metadata/poster, bukan API key.
- **Erase All Media Data** menghapus media, playlist, dan cache; API key tetap.

### 9.4 Pembaruan aplikasi

1. Hentikan server (Ctrl+C).
2. Backup database.
3. `git pull` (atau timpa source baru).
4. `pip install -r requirements.txt` bila dependensi berubah.
5. Jalankan `python app.py`; migrasi DB berjalan otomatis.
6. Buka `/v2`, cek versi di header.

Jangan menyalin folder `database/`, `cache/`, `playlists/`, atau `backups/` dari instalasi lain kecuali memang ingin memindahkan data.

---

## 10. Ringkasan API

API tidak memakai autentikasi; lindungi port dengan loopback atau reverse proxy.

### Halaman

| Route | Isi |
|---|---|
| `GET /` | UI lama (Discovery, Library, Settings & DB) |
| `GET /v2` | UI v2 |
| `GET /roxxy` | Video Feeds |

### Katalog & Discovery

| Route | Fungsi |
|---|---|
| `GET /api/bootstrap` | Item, playlist, stats, task, env, versi |
| `GET /api/media` | Query media dengan filter |
| `GET /api/discover` | Feed Discovery (4 kategori) |
| `POST /api/discover/search-cache` | Cari kandidat + cek cache TorBox |
| `GET /api/discover/poster-proxy` | Proxy poster dengan guard SSRF |

### Serial

| Route | Fungsi |
|---|---|
| `GET /api/series/episodes-status` | Struktur season + kelengkapan |
| `POST /api/series/episode-search` | Cari rilis episode hilang |
| `GET /api/series/download-status` | Progres unduhan per serial/season (peek) |
| `POST /api/series/check-torbox-cache` | Cek cache kandidat (endpoint lama) |

### TorBox

| Route | Fungsi |
|---|---|
| `POST /api/torbox/add-cached` | Tambah magnet/NZB ke TorBox |
| `GET /api/torbox/watch-status` | Status monitor (consume event) |
| `GET /api/torbox/webdl/info` | Daftar hoster + provider WebDL |
| `POST /api/torbox/webdl/add` | Kirim WebDL |

### Media

| Route | Fungsi |
|---|---|
| `GET /api/media/<id>/poster` | Poster (cache lokal/proxy) |
| `POST /api/media/<id>/poster/retry\|manual\|automatic` | Kelola poster |
| `GET /api/media/<id>/stream` | Stream lokal HTTP Range |
| `GET /api/media/<id>/subtitle-file[/<lang>]` | Subtitle untuk pemutar LAN |
| `GET /api/media/<id>/remote-m3u` | M3U satu item untuk LAN |
| `POST /api/media/<id>/enrich` | Enrich metadata |
| `POST /api/media/<id>/manual` | Edit manual |
| `POST /api/media/<id>/classification/preview\|apply` | Preview/terapkan klasifikasi |
| `GET /api/media/<id>/classification/history` | Riwayat klasifikasi |
| `POST /api/media/<id>/subtitle[/<lang>]` | Cari subtitle |
| `POST /api/media/<id>/watched\|rating` | Tonton/rating |
| `POST /api/media/<id>/delete-physical` | Hapus file/remote |
| `POST /api/media/<id>/play`, `/api/media/play-batch` | Putar |

### Task, playlist, settings, DB

| Route | Fungsi |
|---|---|
| `POST /api/tasks/sync-torbox\|scan-local\|classify-pending\|batch-enrich\|batch-subtitle` | Task latar |
| `GET /api/task/status` | Status task |
| `GET/POST /api/playlists` | Daftar/buat playlist |
| `POST/DELETE /api/playlists/<id>/items...` | Kelola item |
| `GET /api/playlists/<id>/download` | Unduh M3U |
| `POST /api/playlists/<id>/play` | Putar playlist |
| `GET/POST /api/settings` | Baca/simpan pengaturan |
| `POST /api/db/backup\|restore\|maintenance\|reset-metadata\|erase-all` | Operasi DB |
| `GET /api/players/detect`, `/api/system/*` | Deteksi pemutar, dialog/browser file |

### Video Feeds

| Route | Fungsi |
|---|---|
| `GET/POST /api/roxxy/config` | Konfigurasi manifest + folder |
| `GET /api/roxxy/manifest` | Gabungan katalog lima sumber |
| `GET /api/roxxy/catalog/...`, `/meta/...`, `/streams/...` | Katalog, metadata, stream |
| `POST /api/roxxy/analyze` | Analisis stream |
| `POST /api/roxxy/torbox-cache-check` | Cek cache batch |
| `POST /api/roxxy/local/inspect\|start` | Inspeksi/mulai unduhan lokal |
| `GET /api/roxxy/local/queue`, `/api/roxxy/library-queue` | Antrean job + registrasi |
| `POST /api/roxxy/local/retry/<id>`, `/clear` | Retry/bersihkan |

---

## 11. Variabel lingkungan dan pengaturan

### Variabel lingkungan

| Variabel | Default | Fungsi |
|---|---|---|
| `TBH_HOST` | `127.0.0.1` | Bind host. `0.0.0.0` untuk LAN (tanpa autentikasi) |
| `TBH_PORT` | `5050` | Port server |
| `TBH_DATA_DIR` | root proyek | Lokasi database/cache/playlist/backup |
| `TBH_CONTAINER` | — | `1` menandai mode container/headless |
| `TBH_POSTER_HOST_ALLOWLIST` | kosong | Allowlist domain poster (opsional) |
| `TORBOX_API_KEY` | — | Fallback key bila DB kosong |
| `TMDB_API_KEY` | — | Fallback key |
| `TPDB_API_KEY` | — | Fallback key |
| `NZBGEEK_API_KEY` | — | Fallback key |
| `ROXXY_JAV_MANIFEST_URL` | — | Manifest JAV |
| `ROXXY_ENGLISH_ADULT_MANIFEST_URL` | — | Manifest English Adult |
| `ROXXY_ONLYFANS_MANIFEST_URL` | — | Manifest OnlyFans |
| `ROXXY_INDO_MOVIE_MANIFEST_URL` | — | Manifest Indonesia Movies |
| `ROXXY_DONGHUA_MANIFEST_URL` | — | Manifest Donghua |
| `ROXXY_MANIFEST_URL`, `ROXXY_EXTRA_MANIFEST_URL` | — | Alias lama (English Adult/Donghua) |
| `ROXXY_FORWARD_HOSTS` | `roxxy-tech.com` | Host forwarder yang di-decode |

### Pengaturan di database

| Key | Isi |
|---|---|
| `torbox_api_key`, `tmdb_api_key`, `tpdb_api_key`, `nzbgeek_api_key` | Secret, disamarkan saat dibaca |
| `media_player` | `vlc`, `mpv`, `custom` |
| `vlc_path`, `mpv_path`, `custom_player_path`, `custom_player_args` | Path/argumen pemutar |
| `local_scan_paths` | JSON array folder scan |
| `roxxy_manifest_url_{jav,english_adult,onlyfans,indo_movie,donghua}` | Manifest per sumber |
| `roxxy_download_path` | Folder unduhan lokal Roxxy |
| `subtitle_preferences.indonesian_enabled` | Toggle subtitle Indonesia |

### Contoh LAN

Linux/macOS:

```bash
TBH_HOST=0.0.0.0 TBH_PORT=5050 python app.py
```

Windows PowerShell:

```powershell
$env:TBH_HOST="0.0.0.0"; $env:TBH_PORT="5050"; python app.py
```

Akses dari perangkat lain: `http://<IP-host>:5050`. Buka firewall untuk port tersebut. Aplikasi akan mencetak peringatan bahwa tidak ada autentikasi.

---

## 12. Keamanan dan privasi

- **Tanpa login.** Default bind `127.0.0.1`. Siapa pun yang bisa menjangkau port dapat mengubah pengaturan, memulai unduhan, dan menghapus file. Untuk LAN, gunakan reverse proxy berautentikasi atau jaringan tepercaya.
- **Guard tulis lintas situs.** `POST/PUT/PATCH/DELETE` dengan `Origin`/`Referer` beda host atau `Sec-Fetch-Site: cross-site` ditolak `403`. Request tanpa header (curl/skrip/launcher) sengaja diizinkan.
- **Secret tidak pernah dikirim mentah.** API key dimask (`••••••••xxxx`), manifest Roxxy disamarkan, field input selalu kosong setelah simpan.
- **Signed stream URL tidak ditampilkan di UI.** Tautan `requestdl` dibuat di server saat Play atau saat membangun M3U; browser tidak menampilkannya, tetapi file M3U yang diunduh memang berisi tautan yang diperlukan pemutar.
- **Guard SSRF poster & subtitle.** Resolusi semua IP, tolak IP privat/localhost/`*.local`, redirect manual maksimal 5 hop, batas ukuran 5 MB.
- **Guard path unduhan.** Tolak OS tree, `Program Files*`, `ProgramData`, Startup, direktori aplikasi, prefix `\\?\`.
- **WebDL hanya URL halaman publik.** URL media langsung dan hoster terlarang ditolak.
- **Sanitasi error.** Pesan indexer/engine tidak memuat API key atau URL bertanda tangan.
- **Data lokal.** Hanya poster, subtitle, M3U, dan SQLite yang disimpan. Video cloud tidak diunduh.

---

## 13. Pemecahan masalah

| Gejala | Penyebab umum | Solusi |
|---|---|---|
| `python` tidak dikenali | PATH belum diperbarui | Tutup/buka terminal; instal ulang Python dengan "Add to PATH" |
| `Activate.ps1 cannot be loaded` | Execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Port 5050 dipakai | Proses lain/aplikasi lama | Hentikan proses atau set `TBH_PORT` |
| Halaman kosong / data tidak muncul | API key/TorBox belum disetel | Isi key di Pengaturan, klik Sync Cloud |
| Poster tidak muncul | CDN memblokir/hotlink gagal | Klik **Coba poster**, isi poster manual di UI lama, cek log |
| Durasi kosong | `ffprobe` tidak ada | Pasang FFmpeg dan pastikan `ffprobe` di PATH |
| Pemutar tidak terdeteksi | Path tidak standar | **Browse** path manual atau isi argumen custom |
| Putar gagal di Android | Intent belum dipilih | Auto-Detect → pilih VLC/mpv/System Chooser |
| Subtitle tidak ditemukan | Tidak ada sidecar/embedded/provider | Coba bahasa lain, cek koneksi, audit kualitas |
| `CACHE ?` pada NZB | Hash NZB tidak diketahui | Wajar; TorBox memakai hash file, bukan judul |
| NZBGeek `unconfigured` | Key premium belum diisi | Isi di Pengaturan atau env `NZBGEEK_API_KEY` |
| Unduhan TorBox tidak bergerak | Monitor in-memory terhenti setelah restart | Tambahkan ulang atau tunggu sync cloud berikutnya |
| Unduhan lokal gagal `exit N` | Tautan kedaluwarsa/403/timeout | Klik **Coba ulang**; engine me-refresh tautan sekali |
| `database is locked` | Dua proses menulis DB | Pastikan hanya satu instance `app.py` berjalan |
| Data hilang setelah update | Folder runtime tertimpa | Pulihkan dari `backups/`; jangan timpa folder runtime |
| Log tidak ada | Dijalankan tanpa konsol | Jalankan `python app.py` di terminal; cek `logs/app.log` |

Pemeriksaan cepat:

```bash
python -m py_compile app.py roxxy_module.py database_manager.py
python -m pytest tests -q --no-header
```

---

## 14. Batasan yang diketahui

1. **Tidak ada autentikasi.** Hanya aman di loopback atau jaringan/reverse proxy tepercaya.
2. **Tidak ada Dockerfile/Docker Compose** resmi. Roadmap.
3. **Tidak ada endpoint redirect M3U permanen.** File M3U berisi tautan yang dibuat saat generate; regenerate untuk tautan baru.
4. **Monitor TorBox in-memory.** Unduhan yang belum selesai tidak dilacak ulang otomatis setelah restart.
5. **Duplikat episode** masih dihitung di total Library/statistik dan di **Putar season**; yang sudah diperbaiki adalah urutan pemilihan salinan terbaik per episode.
6. **Baris Needs Review** masih ada untuk sebagian kecil adult/JAV hasil parse nama file; ini disengaja daripada memaksa kategori.
7. **Pencarian episode dibatasi 24 episode** per panggilan; Roxxy dibatasi 4 katalog/3 meta/8 stream per pencarian.
8. **Status cache NZB tidak diketahui** (`CACHE ?`), karena TorBox memakai hash file NZB.
9. **WebDL bergantung pada dukungan TorBox** dan extractor yt-dlp; provider/hoster tertentu ditolak.
10. **Usenet butuh TorBox Pro**; fitur NZBIndex/NZBGeek dan cache Usenet tidak berfungsi penuh di paket gratis.
11. **`indo_movie` tidak di-enrich otomatis** dan tidak masuk pencarian subtitle otomatis.
12. **Audit subtitle dan riwayat klasifikasi** masih di UI lama. Kurasi inti (edit metadata, klasifikasi, poster, hapus fisik) sudah tersedia di panel detail v2.
13. **Manifest Roxxy bergantung pihak ketiga.** Bila addon berubah/mati, katalog dan stream ikut terpengaruh.
14. **Kualitas klasifikasi bergantung nama file/folder.** Penamaan yang buruk dapat menghasilkan `needs_review`.

---

## 15. Checklist uji manual

Ringkasan uji yang disarankan setelah instalasi/restart (antrean unduhan harus kosong):

- Buka `/v2`: delapan tab tampil; pada layar sempit daftar tab digeser di dalam nav (tanpa scrollbar halaman), navbar tidak menutupi judul.
- Library: chip kategori, enam filter, sort, mode pilih, bulk tonton/putar.
- Detail film dan serial: rating, subtitle ID/EN, tambah/hapus playlist, Escape, tombol Back, deep link `#media-<id>`.
- Serial: tab season, episode hilang, **Cari episode**, hasil dengan seed/leech/ukuran, **+ TorBox**, tray progres, toast impor, baris berubah **Putar**, completeness naik.
- Paket season: season/episode tidak dipaku ke satu nomor.
- `/roxxy`: lima sumber (bila dikonfigurasi), cari semua katalog, filter katalog vs genre, analisis stream otomatis, badge IN LIBRARY.
- Unduhan lokal: satu job selesai, satu gagal lalu **Coba ulang**, **Bersihkan** tidak membuang job aktif/registrasi.
- WebDL: satu provider didukung berhasil; Spotify/Keep2Share ditolak dengan pesan jelas.
- Discovery (v2, `#/discover`): 50 kartu per feed, chip kategori/urutan berpindah, sheet detail menampilkan sinopsis, **Cari rilis** menampilkan CACHED/UNCACHED dan **Tambahkan**. Tab **English Adult 18+** meminta konfirmasi sekali, lalu memuat feed; sheet adult menampilkan cover/sinopsis/durasi dan tombol **Simpan ke TorBox Cloud (WebDL)**.
- Kurasi v2: dari panel detail, **Edit metadata** menyimpan perubahan, **Klasifikasi** menampilkan pratinjau lalu menyimpan, **Poster** menerima URL manual, dan **Hapus fisik** meminta konfirmasi sebelum menghapus.
- Discovery lama: hanya empat tab; `category=jav/english_adult/indo` → `400`.
- Pengaturan: simpan tanpa perubahan, ganti folder, ganti manifest; tidak ada token di pesan.
- Backup DB, restore, maintenance, reset metadata, hapus semua media (di lingkungan uji).
- Restart saat idle: `/v2` 200, log tidak memuat traceback, `download-status` 400 tanpa judul dan 200 dengan judul.

---

## 16. FAQ

**Apakah video cloud diunduh ke disk?**  
Tidak. Video tetap di TorBox dan diputar lewat tautan yang dibuat saat Play. Hanya poster/subtitle/M3U/DB yang lokal.

**Apakah butuh akun TorBox berbayar?**  
Untuk streaming dan cache yang andal, ya. Usenet/WebDL memerlukan paket yang sesuai (umumnya Pro). Akun gratis bisa dipakai untuk uji terbatas.

**Bisakah dipakai di Android tanpa PC?**  
Bisa, lewat Termux. Namun host PC/NAS lebih stabil untuk unduhan besar; Android cukup menjadi klien LAN.

**Apakah API key aman?**  
Key disimpan di SQLite lokal dan hanya ditampilkan tersamarkan. Risiko utama adalah port yang terbuka tanpa autentikasi.

**Bagaimana menambah episode yang hilang?**  
Buka Serial → season → **Cari episode** → pilih rilis → **+ TorBox**. Setelah selesai, episode otomatis masuk Library.

**Kenapa unduhan TorBox lambat tampil?**  
Backend memoll TorBox tiap 10 detik; UI tiap 5 detik. Angka bisa naik bertahap.

**Kenapa dua episode terlihat dobel?**  
Duplikat sengaja tidak disembunyikan. Salinan terbaik dipilih per episode; total statistik masih menghitung semuanya.

**Bagaimana mengganti folder unduhan Roxxy?**  
`/roxxy` → Pengaturan → folder unduhan. Di Windows jangan arahkan ke folder sistem.

**Apakah playlist bisa dipakai di TV/Ponsel?**  
Ya, lewat M3U remote/HTTP Range selama perangkat berada di LAN yang sama dan port terbuka.

**Bagaimana melaporkan bug?**  
Sertakan versi (`v1.3DH`), OS, langkah reproduksi, dan potongan `logs/app.log` yang sudah disanitasi (jangan sertakan API key).