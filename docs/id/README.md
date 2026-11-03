<!-- i18n: source=README.md sha256=5262ab4aca2b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — logo neon" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Pipeline pengembangan pribadi yang membuat Jumat terasa lebih dekat.</strong><br>
Dari tugas hingga draft MR: model yang kuat merencanakan dan mereview, model yang hemat biaya menulis kodenya.</p>

<p align="center">Python 3.9+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · versi awal</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · [Deutsch](../de/README.md) · [Français](../fr/README.md) · **Bahasa Indonesia**

_Ini adalah terjemahan dokumentasi berbahasa Inggris; jika ada perbedaan, versi bahasa Inggris yang berlaku._

---

```text
tugas → rencana (model kuat) → review rencana (vendor lain) → persetujuan Anda
      → milestone: eksekutor di sandbox → gate → review milestone → akseptasi UI di browser
      → review akhir + pemeriksaan buta → draft MR
```

Anda terlibat dua kali: menyetujui rencana dan menerima draft MR. Di antara kedua titik ini pipeline tidak berhenti: temuan review yang disengketakan dikirim kepada Anda bersama MR.

## Poin utama

- **Model berdasarkan tingkat, bukan nama.** Profil memuat katalog model dengan vendor dan tingkat (`frontier`, `strong`, `efficient`) untuk setiap model. Hanya model `frontier` yang mereview kode, reviewer tidak pernah lebih lemah daripada penulisnya, dan sebisa mungkin reviewer berasal dari vendor yang berbeda. Model yang mahal dapat ditandai agar hanya berjalan sekali per tugas.
- **Eksekutor murah dengan umpan balik cepat.** Eksekutor menjalankan linter, pemeriksaan tipe, dan tes sendiri di sandbox tanpa akses jaringan, dan gate hanya memberinya pemeriksaan yang gagal.
- **Anda bisa melihat apa yang terjadi.** `friday status` menampilkan fase, model, sudah berapa lama agen diam, dan berapa banyak yang telah dikeluarkan untuk setiap tugas.
- **Beberapa tugas sekaligus.** Setiap tugas memiliki branch dan worktree sendiri. Pengaturan mesin menentukan berapa banyak gate, browser, dan proses dari satu vendor yang boleh berjalan bersamaan.

## Memulai dengan cepat

Anda memerlukan Python 3.9+, Git, serta Claude Code dan/atau Codex CLI. Gate dan akseptasi di browser juga memerlukan Docker.

```sh
git clone git@github.com:avocawo/friday.git && cd friday
mkdir -p profiles
cp examples/models.json examples/project-profile.json profiles/
# di profiles/project-profile.json, isi path repositori, perintah pemeriksaan, dan gate
./bin/friday doctor --profile profiles/project-profile.json
./bin/friday start TASK-1 --profile profiles/project-profile.json --brief task.md
./bin/friday approve TASK-1
./bin/friday status
```

## Perintah

| Perintah | Fungsinya |
| --- | --- |
| `start KEY --profile P [--brief F]` | Membuat worktree dan rencana, lalu meminta model lain mereview rencana |
| `plan KEY` | Menampilkan rencana beserta temuan yang belum terselesaikan |
| `approve KEY [--no-run]` | Menyetujui rencana dan menjalankan pipeline di latar belakang |
| `status [KEY] [--json]` | Menampilkan apa yang sedang dikerjakan tugas dan berapa pengeluarannya |
| `decide KEY name "text" [--reopen M2]` | Mencatat keputusan pemilik; hanya membuka kembali milestone yang disebutkan |
| `run KEY` / `resume KEY` / `stop KEY` | Menjalankan di terminal ini, melanjutkan, menghentikan |
| `archive KEY [--force]` | Menghapus worktree dan kartu; branch tetap ada |
| `journal KEY` | Memperbarui jurnal tugas (Obsidian, jsonl) |
| `doctor --profile P` | Memeriksa apakah mesin dan profil sudah siap |

Untuk detail tentang profil, model, dan batasan, lihat [panduan](guide.md). Untuk alasan pipeline dibangun seperti ini, lihat [analisis pilot](../process-redesign.md) (dalam bahasa Rusia) dan [ADR](../adr/) (dalam bahasa Rusia).

## Pengembangan

```sh
docker build -f Dockerfile.test -t friday-test:local .
./bin/test-offline
```

Tes menggunakan data sementara dan agen palsu di dalam kontainer tanpa akses jaringan.

[Panduan](guide.md) · [Glosarium](GLOSSARY.md) · [Berkontribusi](../../CONTRIBUTING.md) · [Lisensi](../../LICENSE)
