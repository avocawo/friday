<!-- i18n: source=docs/guide.md sha256=9f95975c05a4 -->
# Panduan Friday

[English](../guide.md) · [Русский](../ru/guide.md) · [简体中文](../zh-cn/guide.md) · [日本語](../ja/guide.md) · [한국어](../ko/guide.md) · [Português (Brasil)](../pt-br/guide.md) · [Español](../es/guide.md) · [Deutsch](../de/guide.md) · [Français](../fr/guide.md) · **Bahasa Indonesia**

_Ini adalah terjemahan dokumentasi berbahasa Inggris; jika ada perbedaan, versi bahasa Inggris yang berlaku._

## Instalasi

1. Clone Friday dan tambahkan `bin` ke `PATH`, atau atur `FRIDAY_BIN`.
2. Pasang CLI yang dibutuhkan agen: Claude Code (`claude`) untuk model Anthropic dan Z.ai, Codex CLI (`codex`) untuk model OpenAI. Login ke keduanya seperti biasa.
3. Untuk gate dan akseptasi di browser, pasang Docker. Akseptasi di browser juga memerlukan Chrome dengan port debugging 9333 dan sesi yang sudah login di lingkungan uji.
4. State disimpan di `~/.local/share/friday` (ubah dengan `FRIDAY_HOME`). Profil dan rahasia tidak masuk ke repositori: folder `profiles/` ada di `.gitignore`.

### Plugin untuk Codex dan Claude Code

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

Plugin ini mengajari agen membaca `status` dan `plan`. Memulai tugas, menyetujuinya, dan mengambil keputusan tetap menjadi urusan Anda.

## Cara tugas berjalan

1. **`start`** membuat branch `friday/KEY` dari `origin/<base_branch>` yang segar beserta worktree, mengunci penugasan peran, lalu menulis rencana: milestone yang terdiri dari 2–5 tahap, berkas, perkiraan ukuran, kriteria kode dan browser, serta ringkasan aturan proyek untuk eksekutor. Rencana lebih dulu diperiksa tanpa model (ukuran, berkas, urutan, kriteria). Setelah itu model dari vendor lain mereviewnya, dan perencana menjawab temuan satu kali: menerima tiap temuan atau menolaknya disertai alasan.
2. **`plan`** menampilkan rencana, pertanyaan, temuan yang ditolak, dan tumpang tindih berkas dengan tugas aktif lainnya.
3. **`approve`** menyetujui rencana dan menjalankan pipeline di latar belakang (`--no-run` hanya menyetujui; jalankan nanti dengan `run`).
4. **Milestone.** Eksekutor bekerja dalam satu sesi per milestone dan melakukan commit pada setiap tahap. Gate berjalan pada commit tersebut. Jika gagal, eksekutor hanya menerima bagian log yang gagal. Review milestone memeriksa diff milestone ini bersama hasil gate. Eksekutor memperbaiki atau membantah temuan yang memblokir; temuan yang dibantah dikirim kepada Anda. Milestone yang memiliki antarmuka melewati akseptasi di browser dalam preview yang terisolasi. Setelah dua kegagalan berturut-turut, model dengan tingkat lebih tinggi mengambil alih milestone dalam sesi baru.
5. **Akhir.** Review akhir dan pemeriksaan buta atas seluruh diff berjalan paralel. Temuan yang memblokir mendapat satu putaran perbaikan.
6. **Draft MR** dibuka jika profil menetapkan `delivery`. Jika tidak, teksnya disimpan di `merge-request.md` dalam folder tugas.

Pipeline hanya berhenti jika gate tidak pernah lulus, eksekutor tidak melakukan apa pun, lingkungan tidak tersedia, atau model yang diizinkan oleh aturan sudah habis. Perbedaan pendapat dalam review tidak menghentikannya.

`decide KEY name "text" --reopen M2` mencatat keputusan Anda. Hanya milestone yang disebutkan dan milestone yang bergantung padanya yang dibuka kembali. Tanpa `--reopen`, milestone berikutnya memperhitungkan keputusan tersebut. `resume` melanjutkan tugas yang berhenti atau gagal. `stop` menghentikan tugas yang sedang berjalan: agen berakhir bersama proses anaknya.

## Profil

Profil adalah berkas JSON. Kunci `extends` menyertakan profil lain (misalnya katalog model bersama); path bersifat relatif terhadap berkas profil.

| Kunci | Fungsi |
| --- | --- |
| `name`, `repository` | Nama proyek dan path ke repositorinya |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | Dari mana branch tugas dibuat dan di mana diletakkan; templat bawaannya adalah `friday/{key}`, layout-nya `key` atau `branch` |
| `models`, `roles` | Katalog model dan preferensi peran (lihat di bawah) |
| `engineering_policy` | Aturan untuk perencana dan review |
| `rules_docs` | Dokumen berisi aturan proyek. Friday membuat satu ringkasan darinya untuk perencana dan reviewer rencana (bawaan `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). Ringkasan disimpan berdasarkan hash isi dan dipakai ulang oleh tugas selama dokumen tidak berubah; perencana tidak membaca dokumen secara penuh |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | Peta repositori dalam prompt perencana dan reviewer rencana, agar mereka tidak menjelajahi struktur dengan pemanggilan tool. `none` (bawaan): tanpa peta. `builtin`: berkas yang dilacak beserta jumlah barisnya. `graphify`: ditambah simbol tiap berkas dan berapa banyak berkas yang mengimpornya, diambil dari graf [graphify](https://github.com/Graphify-Labs/graphify) (`uv tool install 'graphifyy[mcp]==0.9.100'`; ia hanya mem-parse kode, tidak memakai model; graf disimpan di `FRIDAY_HOME/maps`, bukan di repositori). Dalam pengukuran pada CS-2, peta tidak membuat perencanaan lebih murah (tanpa peta $6.19, `builtin` $7.08, `graphify` $7.97, masing-masing satu kali jalan), sehingga secara bawaan dimatikan. Peta di-cache per commit. Jika lebih panjang dari `repo_map_budget` karakter (60000), peta dipangkas dulu dengan membuang simbol, lalu dengan melipat folder. `repo_map_mcp: true` (hanya bersama `graphify`) memberi kedua peran kueri graf: `query_graph`, `get_node`, `get_neighbors`, `shortest_path`; belum diverifikasi langsung di Codex. Reviewer rencana menerima teks terkini dari berkas yang disebut dalam rencana (hingga 40000 karakter) |
| `approval` | `owner` (bawaan): rencana menunggu `approve`. `auto`: rencana disetujui otomatis setelah review dan pipeline langsung dimulai; jurnal mencatat bahwa rencana disetujui secara otomatis |
| `limits` | `stage_files` dan `stage_lines`: batas sebuah tahap (10 dan 500) |
| `worktree_setup` | Perintah yang dijalankan Friday di worktree baru sebelum perencanaan (dependensi dan sejenisnya): larik argumen dengan substitusi `{repository}` dan `{worktree}`; harus dapat diulang. Contoh untuk npm di macOS: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | Perintah yang boleh dijalankan sendiri oleh eksekutor, dalam bentuk persis: `"python3 -m pytest"`, bukan `python` |
| `executor_deny_read` | Path tambahan yang tidak dapat dibaca oleh shell eksekutor |
| `milestone_rounds` | Berapa putaran eksekutor yang didapat sebuah milestone (4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | Perintah pemeriksaan di Docker; `git-diff` membandingkan dengan basis tugas |
| `environment_errors` | Pola baris log yang berarti kegagalan lingkungan, bukan kegagalan kode |
| `browser`, `preview`, `behavior_test_policy` | Lingkungan uji, preview, dan aturan akseptasi di browser |
| `tracker` | Yandex Tracker (API atau MCP), dipakai untuk membaca deskripsi tugas |
| `journal` | Tempat menulis jurnal tugas: `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`; `journal_dir` adalah bentuk singkat untuk satu jurnal markdown |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}` dan kunci `github.repository` atau `gitlab.base_url` + `gitlab.project` |

### Model dan peran

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`: apa yang menjalankan model, `claude` atau `codex`. Z.ai berjalan melalui Claude Code; token dibaca dari berkas yang hanya dapat diakses pemiliknya.
- `tier`: `frontier`, `strong`, atau `efficient`; `strength` mengurutkan model dalam satu tingkat.
- `effort`: kedalaman penalaran (`low` … `max`); satu model dapat dideskripsikan beberapa kali dengan effort yang berbeda.
- `scarce`: model yang mahal; dipakai sekali per tugas jika aturan mengizinkan tugas berjalan tanpanya.
- `price`: harga per juta token menurut jenisnya (`input`, `output`, `cache_read`, `cache_write`), dipakai untuk memperkirakan pengeluaran ketika vendor tidak melaporkan harga.

Peran: `plan`, `plan-review`, `develop`, `review`, `final-review`, `blind-code`, `browser`. Daftar di `roles` adalah preferensi: jika daftar tidak memuat model yang cocok, Friday mengambil model lain dari katalog. Aturan keras: hanya model `frontier` yang mereview kode, reviewer tidak lebih lemah daripada penulis pekerjaan, dan pemeriksaan buta tidak dilakukan oleh penulis kode. Aturan lunak, berurutan: bukan model yang sama dengan penulis; model mahal sekali saja; vendor yang berbeda; pemeriksaan buta tidak oleh penulis rencana. Aturan lunak hanya boleh dilanggar jika tidak ada jalan lain, dan pelanggarannya dicatat dalam riwayat tugas.

Contoh penugasan peran ada di [examples/models.json](../../examples/models.json).

## Jurnal

Kartu tugas adalah satu-satunya sumber kebenaran. Jurnal menampilkannya dan memperbarui diri setelah peristiwa penting: rencana siap dan disetujui, milestone selesai atau gagal, keputusan pemilik, review akhir, MR, penghentian. Untuk memperbaruinya secara manual, jalankan `friday journal KEY`.

- `markdown`: halaman `KEY.md` dan satu halaman ikhtisar bersama yang mendaftar semua tugas. Untuk Obsidian, atur folder vault: halaman memiliki front matter (key, state, pengeluaran, tag `friday`), dan tugas dihubungkan dengan wiki link. Halaman tugas berisi rencana beserta asumsi dan temuan yang belum terselesaikan, milestone beserta ringkasan eksekutor dan commit-nya, temuan yang disengketakan, keputusan, pengeluaran per peran, dan lini masa.
- `jsonl`: semua peristiwa tugas, untuk dianalisis di spreadsheet atau dasbor.

Templat berupa Markdown dengan substitusi `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}`. Friday mengambil templat dari field `template` pada target, lalu `.friday/task.md` di repositori proyek, lalu templat bawaan ([friday/templates](../../friday/templates)). Teks di antara `<!-- friday:notes -->` dan `<!-- /friday:notes -->` adalah milik Anda: teks itu dipertahankan pada setiap pembaruan. Friday tidak menimpa halaman yang tidak ia tulis.

## Pengaturan mesin

`FRIDAY_HOME/machine.json` menetapkan sumber daya bersama untuk semua tugas:

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

Secara bawaan, satu gate dan dua browser berjalan bersamaan, dan vendor tidak dibatasi. Kuota vendor dilacak secara otomatis: proses yang menabrak batas mencatat kapan kuota kembali, dan sampai saat itu vendor dilewati saat memilih model.

## Batasan

- **Sandbox eksekutor.** Shell eksekutor (Claude Code) berjalan di dalam sandbox: tanpa jaringan, tanpa pembacaan `~/.ssh`, `~/.aws`, `~/.config` dan sejenisnya, hanya menulis ke worktree dan folder sementara, serta tidak boleh berjalan di luar sandbox. Perintah yang tidak ada di `executor_commands` dan bukan perintah git akan ditolak. Codex berjalan di sandbox-nya sendiri dengan batasan yang sama.
- **Reviewer** hanya membaca. Pemeriksaan buta berjalan di folder kosong.
- **Gate** berjalan di kontainer tanpa akses jaringan, pada snapshot commit yang di-mount hanya-baca.
- **Browser** hanya menerima tool Friday untuk satu preview; melewati otorisasi dan membaca header permintaan yang terotorisasi dilarang.
- **Publikasi.** Friday hanya mem-push branch tugasnya sendiri dan hanya membuka draft MR/PR. Ia tidak menimpa branch orang lain, tidak melakukan merge, dan tidak men-deploy.
- **Belum diverifikasi langsung:** peran di Codex, akseptasi di browser, dan publikasi ke GitLab baru diverifikasi lewat tes dengan agen palsu.
