<!-- i18n: source=README.md sha256=a008f1f30688 -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — logo neon" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Pipeline pengembangan pribadi yang membuat Jumat terasa lebih dekat.</strong><br>
Dari tugas hingga draft MR: model yang kuat merencanakan dan mereview, model yang hemat biaya menulis kodenya.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · versi awal</p>

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

Anda memerlukan Python 3.11+, Git, dan Claude Code (Codex CLI bersifat opsional). Gate dan akseptasi di browser juga memerlukan Docker.

```sh
uv tool install git+https://github.com/avocawo/friday   # or: pipx install git+https://github.com/avocawo/friday
mkdir -p ~/.config/friday/profiles && cd ~/.config/friday/profiles
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/models.json
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/project-profile.json
# di project-profile.json, isi repository, worktree_root, dan executor_commands proyek Anda
export FRIDAY_PROFILE=~/.config/friday/profiles/project-profile.json
friday doctor
friday start TASK-1 --brief task.md
friday approve TASK-1
friday status
```

Friday berbicara dalam sepuluh bahasa: `friday --lang ru …`, `FRIDAY_LANG=ru`, atau `friday config language ru`. Agen selalu bekerja dalam bahasa Inggris; yang Anda baca diterjemahkan. Contoh profil untuk proyek Node dan Python ada di [examples/profiles](../../examples/profiles).

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
| `config language L` | Mengatur bahasa yang digunakan Friday di mesin ini |

Untuk detail tentang profil, model, dan batasan, lihat [panduan](guide.md). Untuk alasan pipeline dibangun seperti ini, lihat [analisis pilot](../process-redesign.md) (dalam bahasa Rusia) dan [ADR](../adr/) (0001–0006 dalam bahasa Rusia).

## Pengembangan

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

Tes menggunakan data sementara dan agen palsu, tanpa jaringan maupun Docker. Cara kode disusun dijelaskan di [docs/architecture.md](../architecture.md); cara mengusulkan perubahan ada di [CONTRIBUTING.md](../../CONTRIBUTING.md).

[Panduan](guide.md) · [Glosarium](GLOSSARY.md) · [Berkontribusi](../../CONTRIBUTING.md) · [Lisensi](../../LICENSE)
