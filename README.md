# Torbox Playlist Builder v1

**Torbox Playlist Builder v1** adalah aplikasi web lokal berbasis Python (Flask) dan SQLite untuk mengelola, mengkategorikan, memperkaya metadata/poster, mencari subtitle secara otomatis, serta menyusun dan memutar playlist `.m3u` di **VLC Media Player** dari sumber **TorBox Cloud** maupun **Disk Lokal**.

---

## 1. Fitur Utama

### A. Hybrid Media Ingestion (Cloud & Lokal)
* **TorBox Cloud Sync**: Mengambil daftar berkas video dari endpoint `torrents/mylist` dan `usenet/mylist` secara instan dalam satu transaksi SQLite tanpa memicu *rate-limit* `requestdl` pada saat sinkronisasi.
* **Local Disk Scanner**: Memindai direktori lokal secara rekursif untuk berbagai ekstensi video (`.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.webm`, `.m4v`, `.ts`, `.m2ts`) sekaligus mendeteksi berkas subtitle *sidecar* di folder yang sama.
* **Proteksi Metadata Tersimpan**: Sinkronisasi ulang tidak menimpa metadata atau kategori yang sudah berhasil diambil atau diubah secara manual.

### B. Multi-Source Metadata & Cover Engine (`metadata_engine.py`)
Sistem mengklasifikasikan setiap berkas media secara otomatis berdasarkan urutan prioritas ketat:
1. **JAV (`jav`)**:
   * Deteksi kode produksi resmi (termasuk `FC2-PPV`, `HEYZO`, `CARIB`, dan ratusan prefix studio) dengan filter anti-false-positive terhadap tag teknis codec/audio/bahasa (`DDP5.1`, `ITA`, `x264`, `AMZN`, dll.).
   * Mengambil poster, judul, aktris, genre, studio, tanggal rilis, dan durasi dari **JAVDatabase**.
2. **English Adult (`english_adult`)**:
   * Deteksi berbasis nama studio dewasa dan kata kunci eksplisit (terisolasi sepenuhnya dari TMDb/AniList agar tidak terjadi salah pencocokan).
   * Terhubung ke **ThePornDB (TPDB) API** (`/scenes` dan `/movies`) untuk mengambil poster/background, performer, studio, deskripsi, dan tag.
3. **Anime & Donghua (`anime` / `donghua`)**:
   * Identifikasi berbasis grup fansub resmi (seperti `[SubsPlease]`, `[Erai-raws]`, dll.), aksara Hanzi/Kana, kata kunci spesifik Donghua, serta negara asal (`JP` vs `CN`/`TW`) melalui **AniList GraphQL API** dan **TMDb**.
4. **General Movie & TV Show (`general_movie` / `tv_show`)**:
   * Pencocokan judul, season/episode, dan tahun rilis melalui **TMDb API** (dilengkapi fallback pemotongan subjudul untuk judul bertitik dua serta **TVMaze API** untuk serial TV).
5. **Poster Caching & Cache-Busting**:
   * Poster diunduh dan disimpan secara lokal di `cache/posters/` menggunakan hash SHA-256 dari `unique_key`, dilengkapi parameter `?v=updated_at` pada antarmuka web agar pembaruan gambar langsung tampil tanpa perlu *hard refresh*.

### C. Multi-Tier Subtitle Engine (`subtitle_engine.py`)
Pencarian subtitle berjalan secara bertingkat sesuai sumber dan kategori konten:
1. **Local Sidecar**: Mencari berkas `.srt`, `.ass`, `.ssa`, `.vtt`, atau `.sub` di folder yang sama dengan video lokal.
2. **TorBox Cloud Sidecar**: Membangun indeks berkas subtitle yang ikut terunduh di dalam torrent/usenet TorBox Cloud dan mengunduhnya ke `cache/subtitles/`.
3. **Embedded Subtitle Inspection (`ffprobe`)**: Memeriksa header kontainer `.mkv`/`.mp4` untuk mendeteksi track subtitle bawaan baik pada **file lokal** maupun **stream TorBox Cloud** (berlaku untuk semua kategori termasuk `english_adult`).
4. **Public Subtitle Providers**:
   * **JAV**: Mencari subtitle bahasa Inggris/Indonesia melalui **SubtitleCat**, dengan fallback ke **OpenSubtitles REST API**.
   * **Movie / TV Show / Anime / Donghua**: Mencari subtitle melalui **OpenSubtitles REST API** dengan dukungan parameter `season` dan `episode`.
   * *(Provider publik dilewati otomatis untuk kategori `english_adult`.)*

### D. Manajemen Playlist M3U & Integrasi VLC
* **Pemisahan Ruang Kerja Playlist**: Tab khusus untuk memisahkan media **Belum Masuk Playlist**, **Sudah Masuk Playlist**, dan **Semua Media**.
* **Dynamic M3U Generation**: Menghasilkan berkas `.m3u` di folder `playlists/` lengkap dengan durasi (`#EXTINF`), injeksi subtitle eksternal (`#EXTVLCOPT:sub-file=...`), serta pembuatan tautan stream segar (`requestdl`) tepat saat playlist dibuat atau diputar.
* **Direct VLC Playback**: Memutar satu item media maupun seluruh playlist langsung ke aplikasi **VLC Media Player** lokal.

### E. Kontrol Tugas Latar Belakang & Keamanan
* **Task Concurrency Lock**: Pemrosesan paralel menggunakan `ThreadPoolExecutor(max_workers=4)` dengan indikator progres persentase (`..% completed`) dan penguncian tombol aksi di UI hingga tugas selesai `100%`.
* **Write-Only Secret Masking**: API Key (TorBox, TMDb, TPDB) tidak pernah dikirim dalam bentuk teks terang ke browser; server hanya mengirimkan nilai tersamar (`••••••••xxxx`) dan mempertahankan key lama saat konfigurasi lain disimpan.
* **Database Maintenance**: Tersedia fitur Backup `.db`, Restore, Maintenance (`VACUUM` & pembersihan berkas lokal yang hilang), Reset Metadata & Poster, serta Erase Semua Data Media.

---

## 2. Arsitektur File & Struktur Direktori

Lokasi direktori proyek di Windows: `%USERPROFILE%\Projects\TorBox Playlist Builder` (atau `$HOME\Projects\TorBox Playlist Builder` di PowerShell).

```text
TorBox Playlist Builder/
├── app.py                  # Server Flask (127.0.0.1:5050), REST API, Task Manager, & M3U Generator
├── database_manager.py     # Manajemen SQLite WAL, schema, secret masking, backup & maintenance
├── metadata_engine.py      # Klasifikasi konten, parser judul/kode, API client, & downloader poster
├── subtitle_engine.py      # Pencarian subtitle multi-tier (Sidecar, TorBox, ffprobe, SubtitleCat, OpenSubtitles)
├── templates/
│   └── index.html          # Antarmuka Single-Page Application (Tailwind CSS + Vanilla JS)
├── database/               # File database SQLite (torbox_media_hub.db) [diabaikan di Git]
├── cache/                  # Cache poster (cache/posters/) & subtitle (cache/subtitles/) [diabaikan di Git]
├── playlists/              # Output file playlist .m3u [diabaikan di Git]
└── backups/                # Arsip backup database .db [diabaikan di Git]
```

---

## 3. Alur Kerja Sistem (End-to-End Workflow)

1. **Inisialisasi & Konfigurasi**:
   * Saat `app.py` dijalankan, `db.init_db()` menginisialisasi skema database SQLite dalam mode `WAL` serta membuat folder `database/`, `cache/`, `playlists/`, dan `backups/`.
   * Pengguna membuka tab **Settings & DB** untuk mengisi **TorBox API Key**, **TMDb API Key**, **ThePornDB API Key** (opsional), **Path VLC Media Player**, serta daftar **Folder Scan Lokal**.
2. **Ingesti Media (`Sync Cloud` / `Scan Lokal`)**:
   * Sistem mendaftarkan seluruh berkas video ke tabel `media_items` dengan kunci unik (`torbox:<kind>:<item_id>:<file_id>` atau `local:<normalized_path>`).
3. **Enrichment Metadata (`Batch Enrich` / `Auto Enrich Ulang`)**:
   * Worker memproses item yang belum memiliki metadata lengkap secara paralel (4 worker) melalui jalur:
     `JAV -> English Adult -> Anime/Donghua -> General Movie/TV Show`.
   * Metadata dan poster lokal disimpan ke database serta `cache/posters/`.
4. **Pencarian Subtitle (`Cari Subtitle`)**:
   * Sistem memverifikasi ketersediaan subtitle yang ada, lalu memindai item tanpa subtitle melalui sidecar lokal, TorBox Cloud, `ffprobe` (embedded track), hingga provider eksternal.
5. **Kurasi & Pemutaran**:
   * Pengguna memfilter media di tab **Belum Masuk Playlist**, mencentang item pilihan untuk membuat playlist `.m3u` baru atau menambahkannya ke playlist yang sudah ada, lalu memutarnya langsung di **VLC Media Player**.

---

## 4. Instalasi & Menjalankan Aplikasi (Windows)

### Prasyarat
* **Python 3.10+**
* **VLC Media Player** (untuk pemutaran langsung video & playlist `.m3u`)
* **FFmpeg / `ffprobe`** tersedia di `PATH` sistem (opsional, direkomendasikan untuk deteksi *embedded subtitle* pada berkas lokal maupun TorBox Cloud)

### Langkah Instalasi
1. Buka terminal dan masuk ke direktori proyek:
   * **PowerShell**:
     ```powershell
     cd "$HOME\Projects\TorBox Playlist Builder"
     ```
   * **Command Prompt (`cmd`)**:
     ```cmd
     cd /d "%USERPROFILE%\Projects\TorBox Playlist Builder"
     ```
2. Install dependensi Python yang diperlukan:
   ```bash
   pip install flask requests beautifulsoup4
   ```
3. Jalankan server aplikasi:
   ```bash
   python app.py
   ```
4. Buka browser dan akses:
   ```text
   http://127.0.0.1:5050
   ```

---

## 5. Konfigurasi `.gitignore` yang Direkomendasikan

Untuk menjaga agar API Key, database pribadi, dan berkas cache tidak ikut terunggah ke repositori Git, pastikan file `.gitignore` berisi aturan berikut:

```gitignore
__pycache__/
*.pyc
.env
database/
backups/
cache/
playlists/
```