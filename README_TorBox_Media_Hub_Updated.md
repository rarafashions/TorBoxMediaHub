# TorBox Media Hub

### Personal Multimedia Management, Discovery, Roxxy Integration & TorBox Download Hub

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%2011%20%7C%20Linux%20%7C%20macOS-informational)
![Database](https://img.shields.io/badge/Database-SQLite-003B57)
![Integration](https://img.shields.io/badge/Integration-TorBox%20%7C%20Stremio%20%7C%20Roxxy-00AEEF)

**TorBox Media Hub** adalah aplikasi multimedia self-hosted berbasis
Python untuk mengelola koleksi video lokal dan cloud TorBox, melakukan
discovery melalui berbagai provider, menganalisis stream Roxxy,
mengunduh media ke komputer, serta mengotomatisasi klasifikasi dan
registrasi ke Media Library.

> **Status dokumentasi:** README ini merangkum kemampuan proyek sampai
> baseline **v1.3D2** dan rancangan/patch awal **v1.3D3 (Phase D)**. D3
> masih memerlukan pengujian end-to-end pada instalasi pengguna sebelum
> dinyatakan selesai.

## Daftar Isi

-   [Gambaran Umum](#gambaran-umum)
-   [Arsitektur dan Modul](#arsitektur-dan-modul)
-   [Discovery & Search V1.2](#discovery--search-v12)
-   [Roxxy Hub](#roxxy-hub)
-   [Integrasi TorBox](#integrasi-torbox)
-   [Download Engine](#download-engine)
-   [Media Library dan Database](#media-library-dan-database)
-   [Metadata dan Klasifikasi D2](#metadata-dan-klasifikasi-d2)
-   [Subtitle Engine](#subtitle-engine)
-   [Duplicate Detection](#duplicate-detection)
-   [Pipeline D1--D3](#pipeline-d1d3)
-   [Status Fitur](#status-fitur)
-   [Instalasi Windows](#instalasi-windows)
-   [Konfigurasi dan Penggunaan](#konfigurasi-dan-penggunaan)
-   [Keamanan](#keamanan)
-   [Batasan yang Diketahui](#batasan-yang-diketahui)
-   [Roadmap](#roadmap)

## Gambaran Umum

TorBox Media Hub menyatukan beberapa fungsi yang biasanya tersebar di
berbagai aplikasi:

1.  Discovery dan pencarian media melalui provider yang diintegrasikan.
2.  Roxxy Hub untuk katalog, metadata, analisis stream, dan download.
3.  Integrasi TorBox untuk cache check, submit magnet, dan pemantauan
    download cloud.
4.  Download lokal menggunakan yt-dlp dan aria2c sesuai jenis sumber.
5.  Media Library berbasis SQLite untuk mengelola file dan metadata.
6.  Metadata dan classification engine untuk mengidentifikasi kategori
    media.
7.  Subtitle engine untuk pemeriksaan dan pencarian subtitle.
8.  Persistent task queue untuk menjaga pekerjaan background tetap
    tercatat.
9.  JAV duplicate detection untuk mencegah registrasi kode produksi yang
    sama.

Aplikasi berjalan pada komputer pengguna dan diakses melalui browser
lokal. Backend Python mengelola API, database, worker, serta komunikasi
dengan layanan eksternal.

## Arsitektur dan Modul

  -----------------------------------------------------------------------
  Modul                               Tanggung jawab
  ----------------------------------- -----------------------------------
  `app.py`                            Flask server, API, lifecycle
                                      aplikasi, dan integrasi antarmodul

  `database_manager.py`               SQLite, schema, task queue, dan
                                      operasi database

  `metadata_engine.py`                Metadata/enrichment Library V1.2

  `subtitle_engine.py`                Pemeriksaan dan pencarian subtitle

  `classification_engine.py`          Klasifikasi media untuk pipeline
                                      Roxxy D2

  `roxxy_module.py`                   Integrasi katalog, metadata, dan
                                      stream Roxxy

  `roxxy_download_engine.py`          Download Roxxy, queue, validasi,
                                      identifikasi, dan pipeline lanjutan

  `templates/`                        Antarmuka web

  `database/`                         Database runtime SQLite

  `cache/`                            Cache poster dan subtitle

  `downloads/`                        Lokasi file hasil download, sesuai
                                      konfigurasi build
  -----------------------------------------------------------------------

Struktur aktual dapat berbeda antar-build. Database, cache, dan folder
download merupakan data runtime dan sebaiknya tidak ditimpa saat
memperbarui source code.

## Discovery & Search V1.2

Discovery & Search adalah subsistem yang dikembangkan sebelum integrasi
Roxxy. Provider-provider di sini merupakan integrasi tersendiri, bukan
bagian otomatis dari Roxxy.

Kategori dan sumber yang pernah diintegrasikan meliputi:

-   **General Movies / TV Shows:** discovery dan metadata melalui sumber
    metadata seperti TMDb.
-   **JAV:** sumber discovery dan identifikasi seperti MissAV,
    JAVDatabase, TPDB, serta indeks terkait.
-   **English Adult:** provider ThePornDB (TPDB) dan Eporner pada
    implementasi sebelumnya.
-   **Anime / Donghua:** sumber katalog seperti AniList pada
    implementasi sebelumnya.

Kemampuan provider berbeda-beda. Ada yang menyediakan katalog, metadata,
pencarian, atau stream. Keberadaan satu provider di V1.2 tidak
membuktikan bahwa fungsi yang sama tersedia pada Roxxy.

### Perbedaan V1.2 dan Roxxy

-   ThePornDB dan Eporner merupakan provider English Adult pada
    subsistem Discovery & Search V1.2.
-   Roxxy merupakan modul/provider aggregator terpisah yang menyediakan
    katalog, metadata, dan stream melalui addon Roxxy.
-   Kode, endpoint, atau kemampuan provider V1.2 tidak boleh diasumsikan
    berlaku pada Roxxy tanpa audit dan pengujian tersendiri.

## Roxxy Hub

Roxxy Hub memanfaatkan addon Roxxy untuk mengambil katalog, metadata,
dan pilihan stream dari provider yang tersedia melalui Roxxy.

### Discovery dan katalog

-   Menampilkan katalog Roxxy.
-   Memuat item secara bertahap.
-   Mendukung pencarian pada katalog yang menyediakan endpoint search.
-   Membuka detail metadata dan poster untuk item yang dipilih.
-   Memisahkan area Discovery dari Download Center.

### Metadata

Metadata yang dapat tersedia, bergantung provider:

-   Judul/nama.
-   Provider dan item ID.
-   Poster dan background.
-   Runtime dan release information.
-   Description.
-   Genre atau tags.
-   JAV code jika tersedia.
-   Informasi `in_library` atau `library_match` jika disediakan
    provider.

Tidak semua provider mengirim field yang sama. Aplikasi harus
memperlakukan field yang tidak tersedia sebagai data yang memang tidak
diberikan, bukan membuat nilainya sendiri.

### Stream analysis

Pilihan stream dapat memuat nama provider, kualitas/resolusi, URL, serta
route hint. Aplikasi menganalisis sumber untuk menentukan jalur download
yang sesuai, misalnya kandidat yt-dlp atau direct HTTP/aria2c. Route
hint adalah petunjuk, bukan jaminan keberhasilan.

### Download Center

Download Center menyatukan pemantauan local download dan antrean TorBox.
Fitur yang telah dikembangkan mencakup:

-   Local download queue.
-   TorBox queue.
-   Status dan progres pekerjaan.
-   Analisis stream sebelum download.
-   Pemrosesan beberapa pekerjaan dengan batas concurrency.
-   Penanganan kegagalan dan resume sesuai dukungan engine/sumber.

### Original website URL --- tidak termasuk scope saat ini

Original page URL seperti halaman video di website sumber tidak sama
dengan URL stream Roxxy atau URL CDN. Sampel metadata Roxxy yang telah
diuji belum membuktikan bahwa original page URL tersedia secara
konsisten untuk semua provider. Karena itu, ekstraksi original URL dan
tombol pengiriman original URL ke TorBox **di-skip untuk D3 saat ini**.

Fitur ThePornDB/Eporner pada Discovery & Search V1.2 adalah subsistem
terpisah dan tidak menjadi bukti bahwa Roxxy menyediakan original URL.

## Integrasi TorBox

TorBox Media Hub menggunakan TorBox API untuk workflow cloud yang telah
diintegrasikan, termasuk:

-   Konfigurasi TorBox API key.
-   Pemeriksaan cache untuk sumber yang didukung.
-   Pengiriman magnet ke TorBox.
-   Pemantauan antrean dan status download cloud.
-   Pemanfaatan informasi media cloud dalam workflow Library.

Hasil cache check bukan jaminan bahwa setiap sumber akan berhasil
diproses. Dukungan bergantung pada tipe sumber, host, akun, dan kondisi
layanan saat permintaan dilakukan.

## Download Engine

### yt-dlp

Digunakan untuk stream yang memerlukan extractor atau pemrosesan
manifest, seperti kandidat HLS/DASH dan sumber yang kompatibel dengan
yt-dlp.

### aria2c

Digunakan untuk direct HTTP download yang kompatibel. Resume bergantung
pada dukungan server terhadap HTTP range dan karakteristik sumber.

### Pemilihan engine

Stream classifier dan route hint membantu memilih engine. URL tetap
perlu diperiksa dan kegagalan harus ditangani oleh worker. Tidak semua
stream Roxxy dapat diproses dengan cara yang sama.

## Media Library dan Database

Media Library menggunakan SQLite untuk menyimpan identitas media,
metadata, dan task aplikasi.

Field media yang digunakan pada skema V1.2 antara lain:

-   `unique_key`
-   `source_type`
-   `file_name`
-   `file_path`
-   `file_size`
-   `code_id`
-   `title`
-   `content_category`
-   `has_metadata`
-   `metadata_source`
-   `poster_url`
-   `local_poster_path`

Skema aktual mengikuti migrasi database versi yang dipasang.

### Local scan

Local scan mengindeks folder yang dipilih pengguna. Identitas file lokal
menggunakan `unique_key` berbasis path yang dinormalisasi agar file yang
sama dapat dikenali secara konsisten.

### Cloud media

Workflow cloud mengelola metadata dan status media TorBox. Sinkronisasi
metadata cloud tidak sama dengan mengunduh file video ke komputer.

## Metadata dan Klasifikasi D2

Terdapat dua konteks yang harus dibedakan:

1.  **Metadata Engine V1.2:** enrichment dan identifikasi media pada
    Library.
2.  **Roxxy D2 Classification Engine:** klasifikasi hasil download Roxxy
    sebelum proses registrasi Library.

### Urutan kategori D2

  ------------------------------------------------------------------------
                     Prioritas Kategori              Tujuan
  ---------------------------- --------------------- ---------------------
                             1 `indo`                Konten Indonesia
                                                     berdasarkan sinyal
                                                     lokal/Indonesia

                             2 `jav`                 Japanese Adult Video
                                                     yang dikenali melalui
                                                     kode produksi atau
                                                     sinyal JAV

                             3 `english_adult`       Konten Adult non-Indo
                                                     dan non-JAV

                             4 `tv_show`             Serial televisi

                             5 `anime`               Anime

                             6 `donghua`             Donghua

                             7 `general_movie`       Film umum dengan
                                                     bukti identifikasi
                                                     memadai

                             8 `uncategorized`       Fallback jika bukti
                                                     kategori tidak
                                                     mencukupi
  ------------------------------------------------------------------------

Nama kategori `english_adult` dipertahankan sesuai keputusan proyek.
Kategori ini dapat menampung konten Adult non-Indo/non-JAV meskipun
judul atau metadata menggunakan bahasa Prancis, Spanyol, atau bahasa
lain. Tidak ada kategori baru `international_adult`.

### Prinsip klasifikasi

-   Engine dievaluasi secara berurutan; engine pertama yang lolos
    menghentikan evaluasi kategori berikutnya.
-   Keyword seksual generik dicatat sebagai attributes dan tidak
    otomatis menentukan kategori.
-   Kategori Indo menggunakan sinyal lokal/Indonesia; kata generik saja
    tidak cukup.
-   JAV code digunakan sebagai identitas produksi untuk duplicate
    detection.
-   English Adult mengutamakan metadata provider, studio, atau kombinasi
    sinyal yang relevan.
-   General Movie bukan kategori penampung untuk semua judul yang tidak
    dikenali.
-   Jika bukti tidak cukup, hasil yang diharapkan adalah
    `uncategorized`, bukan klasifikasi paksa.

### Attributes

Kategori berbeda dari atribut. Atribut dapat berupa tag seperti `adult`,
`amateur`, `verified`, `swinger`, `asian`, dan istilah konten lain.
Attributes tidak membuat kategori baru.

### Status pengujian D2

Hasil pengujian yang telah dikonfirmasi dalam pengembangan:

-   **Indo:** beberapa sampel masuk `indo`; subtitle policy `skip`.
-   **JAV:** kode produksi pada sampel berhasil dikenali.
-   **JAV duplicate lookup:** fungsi pencarian menemukan kode yang telah
    ada pada Library dalam pengujian sintetis read-only.
-   **English Adult:** sampel studio dan keyword berhasil
    diklasifikasikan, termasuk metadata berbahasa non-Inggris.
-   **General Movie:** dua sampel diterima pengguna sebagai hasil yang
    sesuai.
-   **Uncategorized:** belum ditemukan sampel positif; diterima untuk
    milestone saat ini tanpa pengujian positif langsung.
-   **TV Show / Anime / Donghua:** ditunda karena memerlukan desain
    navigasi show → season → episode.

Hasil ini membuktikan perilaku pada sampel yang diuji, bukan jaminan
klasifikasi sempurna untuk seluruh katalog.

## Subtitle Engine

Subtitle Engine V1.2 memiliki mekanisme pemeriksaan dan pencarian
berikut, sesuai integrasi yang tersedia pada build:

1.  **Local sidecar detection:** mendeteksi subtitle lokal yang
    berpasangan dengan file video.
2.  **TorBox archive indexing:** memeriksa file subtitle yang tersedia
    dalam struktur file torrent/Usenet cloud.
3.  **Embedded track inspection:** memeriksa track subtitle menggunakan
    `ffprobe`.
4.  **External subtitle providers:** pencarian melalui provider yang
    telah diintegrasikan, termasuk OpenSubtitles dan SubtitleCat pada
    implementasi sebelumnya.

### Kebijakan subtitle Roxxy D2

-   `indo`: `subtitle_policy = skip`.
-   Kategori lain: `eligible_if_needed`.
-   `eligible_if_needed` berarti task memenuhi syarat untuk pencarian
    jika diperlukan; ini bukan bukti subtitle telah ditemukan atau
    diunduh.
-   Roxxy-native subtitle belum dianggap terintegrasi sebelum
    mekanismenya dibuktikan.
-   Fallback menggunakan Subtitle Engine V1.2 sesuai implementasi
    pipeline yang dipasang.

## Duplicate Detection

Duplicate detection D2 difokuskan pada JAV:

1.  Kategori akhir harus `jav`.
2.  Kode diambil dari metadata Roxxy atau hasil deteksi.
3.  Jika kategori bukan JAV, lookup kode JAV tidak dilakukan.
4.  Jika kode tersedia, database mencari `code_id` yang sama pada
    `media_items`.
5.  Jika ditemukan, task ditandai duplicate dan registrasi entri kedua
    harus dicegah.

Normalisasi kode menangani perbedaan huruf besar/kecil serta pemisah
yang relevan.

Pengujian positif duplicate yang telah dikonfirmasi adalah pemanggilan
fungsi identifikasi terhadap data sintetis dan database existing secara
read-only. Ini membuktikan jalur lookup, tetapi bukan pengujian
end-to-end seluruh proses download baru.

## Pipeline D1--D3

### D1 --- Queue & File Validation

D1 telah diimplementasikan dan diuji pengguna.

-   Membuat task Library setelah download Roxxy selesai.
-   Menyimpan snapshot metadata dan stream yang diperlukan.
-   Menyimpan task pada tabel persisten `roxxy_library_tasks`.
-   Memvalidasi file ada, ukuran tidak nol, ekstensi didukung, dan
    ukuran stabil.
-   Memulihkan task validasi yang terputus setelah restart.
-   Menyediakan endpoint queue untuk pemantauan.
-   D1 tidak melakukan insert ke `media_items`.

### D2 --- Ordered Multi-Category Classification

D2 v4 menambahkan klasifikasi kategori berurutan, attributes, ekstraksi
JAV code, duplicate lookup, dan subtitle policy. D2 telah diuji pada
sejumlah sampel nyata. TV Show, Anime, dan Donghua ditunda sampai desain
hierarki season/episode tersedia.

### D3 --- Direct-to-Library Pipeline

D3 menghubungkan task yang telah diidentifikasi ke tabel `media_items`.

Rancangan yang disepakati:

-   Menggunakan metadata Roxxy jika sudah memadai.
-   Tidak menjalankan metadata enrichment V1.2 secara default untuk
    metadata Roxxy yang lengkap.
-   Menggunakan poster Roxxy jika tersedia.
-   Mencegah registrasi JAV duplicate.
-   Menerapkan subtitle policy berdasarkan kategori.
-   Menyimpan status registrasi.
-   Memulihkan task yang terputus dan menjaga proses idempotent.
-   Menyinkronkan status Library/In Library.

Patch awal D3 telah disiapkan. Namun D3 **belum boleh dinyatakan
production-ready atau PASS end-to-end** sebelum diuji pada instalasi
pengguna, termasuk registrasi nyata ke Library, subtitle, recovery, dan
duplicate handling.

## Status Fitur

  -----------------------------------------------------------------------
  Modul                               Status
  ----------------------------------- -----------------------------------
  Discovery & Search V1.2             Baseline sebelumnya; dipertahankan

  ThePornDB / Eporner V1.2            Provider terpisah dari Roxxy

  Roxxy catalog, metadata, stream     Telah digunakan dan diuji

  Stream analysis                     Telah digunakan dan diuji

  TorBox cache/queue workflow         Telah diuji dalam workflow proyek

  Download via yt-dlp / aria2c        Telah diuji dalam workflow proyek

  D1 persistent queue & validation    Diuji pengguna

  D2 ordered classification v4        Diuji pada sejumlah sampel

  D2 JAV duplicate lookup             Fungsi lookup diuji read-only

  D3 Library registration             Patch awal tersedia; perlu validasi
                                      end-to-end

  TV Show / Anime / Donghua hierarchy Ditunda

  Roxxy original page URL → TorBox    Tidak dilanjutkan saat ini
  -----------------------------------------------------------------------

## Instalasi Windows

### Persyaratan

-   Windows 10/11 (lingkungan pengembangan utama: Windows 11).
-   Python 3.10+.
-   TorBox account dan API key untuk fitur TorBox.
-   Koneksi internet.
-   yt-dlp untuk stream yang memerlukannya.
-   aria2c untuk direct download yang kompatibel.
-   FFmpeg/ffprobe direkomendasikan untuk pemeriksaan media/subtitle.
-   Browser modern.

### Setup

Buka PowerShell pada folder project:

``` powershell
python --version
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Jika PowerShell membatasi aktivasi script, gunakan kebijakan eksekusi
yang sesuai untuk akun pengguna atau panggil interpreter langsung dari
`venv\Scripts\python.exe`.

Jalankan aplikasi:

``` powershell
python app.py
```

Buka alamat lokal yang ditampilkan di terminal. Pada build yang
menggunakan port default sebelumnya, alamatnya:

``` text
http://127.0.0.1:5050
```

Port aktual mengikuti konfigurasi build.

## Konfigurasi dan Penggunaan

1.  Masukkan TorBox API key pada konfigurasi aplikasi.
2.  Masukkan API key metadata yang dibutuhkan modul terkait, misalnya
    TMDb atau ThePornDB pada V1.2.
3.  Pastikan yt-dlp dan aria2c tersedia melalui PATH atau path
    konfigurasi.
4.  Pastikan FFmpeg/ffprobe tersedia jika digunakan.
5.  Periksa database dan folder download.
6.  Lakukan satu pengujian kecil sebelum menjalankan banyak pekerjaan.

### Workflow Discovery V1.2

1.  Buka Discovery & Search.
2.  Pilih provider/kategori yang tersedia.
3.  Cari judul atau kode.
4.  Periksa metadata dan fungsi provider yang tersedia.

### Workflow Roxxy

1.  Buka Roxxy Hub.
2.  Pilih katalog atau lakukan pencarian.
3.  Buka detail metadata.
4.  Analisis pilihan stream.
5.  Pilih stream yang sesuai.
6.  Jalankan download lokal atau workflow TorBox yang tersedia.
7.  Pantau status di Download Center.

### Workflow D1/D2

Setelah download Roxxy selesai, D1 membuat task persisten dan
memvalidasi file. D2 kemudian mengklasifikasikan media, mengekstrak JAV
code bila relevan, memeriksa duplicate, dan menentukan subtitle policy.

### Workflow D3

Setelah patch D3 dipasang dan tervalidasi, task `identified` akan
diproses untuk registrasi Library. Pastikan pengujian end-to-end
berhasil sebelum mengandalkan workflow ini untuk batch besar.

## Keamanan

-   Simpan TorBox API key secara privat.
-   Jangan memasukkan API key ke README, screenshot publik, issue
    tracker, atau ZIP source yang dibagikan.
-   Backup database sebelum memasang patch atau migrasi schema.
-   Jangan mengekspos Flask development server langsung ke internet
    tanpa autentikasi dan pengamanan yang sesuai.
-   Batasi akses LAN melalui jaringan tepercaya dan firewall.
-   Periksa URL sumber sebelum mengirimnya ke downloader.
-   Gunakan layanan dan konten sesuai hak akses serta ketentuan yang
    berlaku.

## Batasan yang Diketahui

1.  Kelengkapan metadata berbeda antar-provider Roxxy.
2.  Tidak semua stream dapat diproses dengan engine yang sama.
3.  Dukungan TorBox bergantung pada jenis sumber dan host.
4.  Klasifikasi berbasis metadata/keyword dapat menghasilkan false
    positive atau false negative.
5.  Jalur Uncategorized belum memiliki sampel positif langsung pada
    validasi D2 saat ini.
6.  TV Show, Anime, dan Donghua membutuhkan desain show/season/episode
    dan ditunda.
7.  Original page URL tidak dijamin tersedia dari metadata Roxxy; fitur
    ini di-skip.
8.  D3 awal memerlukan validasi end-to-end pada instalasi pengguna.
9.  Samsung Tizen tidak menjalankan aplikasi Python desktop ini secara
    native. Akses melalui browser LAN atau perangkat/player lain harus
    diuji sesuai kompatibilitas browser dan format stream.

## Roadmap

-   [x] Discovery & Search multi-provider pada subsistem V1.2.
-   [x] Provider English Adult ThePornDB dan Eporner pada V1.2.
-   [x] Roxxy catalog, metadata, dan stream integration.
-   [x] Stream analysis dan pemilihan download engine.
-   [x] TorBox cache/queue workflow yang telah diuji.
-   [x] D1 persistent task queue dan file validation.
-   [x] D2 ordered multi-category classification v4.
-   [x] D2 JAV code extraction dan duplicate lookup validation.
-   [ ] D3 end-to-end Library registration validation.
-   [ ] D3 subtitle processing dan recovery validation.
-   [ ] D3 In Library synchronization dan UI status validation.
-   [ ] Desain hierarki TV Show / Anime / Donghua sampai Season dan
    Episode.

------------------------------------------------------------------------

**TorBox Media Hub --- satu pusat kendali untuk Discovery, Roxxy,
TorBox, Download, dan Personal Media Library.**
