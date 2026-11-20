<!-- i18n: source=GLOSSARY.md sha256=fd78b7d8bdb2 -->
# Friday

[English](../../GLOSSARY.md) · [Русский](../ru/GLOSSARY.md) · [简体中文](../zh-cn/GLOSSARY.md) · [日本語](../ja/GLOSSARY.md) · [한국어](../ko/GLOSSARY.md) · [Português (Brasil)](../pt-br/GLOSSARY.md) · [Español](../es/GLOSSARY.md) · [Deutsch](../de/GLOSSARY.md) · [Français](../fr/GLOSSARY.md) · **Bahasa Indonesia**

_Ini adalah terjemahan dokumentasi berbahasa Inggris; jika ada perbedaan, versi bahasa Inggris yang berlaku._

Pipeline pengembangan pribadi: membawa tugas dari rencana hingga draft MR dengan model dari berbagai vendor dan memeriksa hasil kerja mereka.

## Pekerjaan

**Tugas**:
Satuan pekerjaan bagi pemilik, dengan kartu, branch, dan worktree sendiri; biasanya sesuai dengan satu tiket di tracker.
_Avoid_: job, misi

**Rencana**:
Daftar milestone dan tahap untuk sebuah tugas, beserta kriteria akseptasi, yang disetujui oleh pemilik.

**Milestone**:
Kelompok 2–5 tahap berurutan yang melewati gate dan review sebagai satu kesatuan.
_Avoid_: fase, tonggak

**Tahap**:
Bagian terkecil dari rencana; diakhiri dengan satu commit.
_Avoid_: langkah, step

**Kriteria**:
Syarat akseptasi suatu tahap yang dapat diverifikasi; berupa kriteria kode (diperiksa terhadap diff) atau kriteria perilaku (diamati di browser).

**Profil**:
Berkas JSON yang mengonfigurasi Friday untuk satu proyek (repositori, model, peran, gate, browser, tracker, jurnal, delivery); dapat memperluas profil lain melalui kunci `extends`.

## Peran

**Pemilik**:
Orang yang menyetujui rencana, menerima draft MR, dan menyelesaikan temuan yang disengketakan.
_Avoid_: operator, pengguna

**Perencana**:
Model yang menulis rencana.

**Eksekutor**:
Model yang menulis kode sebuah milestone.
_Avoid_: pengembang, developer

**Reviewer**:
Model yang mereview rencana, milestone, atau diff akhir dan memberikan putusan atas kriteria.

**Tingkat model**:
Kelas kemampuan sebuah model: `frontier`, `strong`, atau `efficient`.

**Penugasan peran**:
Penetapan model tertentu pada peran-peran sebuah tugas, dikunci sebelum rencana disetujui.

## Pemeriksaan

**Gate**:
Pemeriksaan deterministik tanpa model, dijalankan dalam kontainer terisolasi pada commit tertentu.
_Avoid_: CI, validasi

**Review milestone**:
Review atas diff milestone, beserta log gate, oleh reviewer dari vendor yang berbeda.

**Review akhir**:
Review atas seluruh diff tugas sebelum MR, termasuk pemeriksaan buta atas kode.

**Akseptasi di browser**:
Pengamatan kriteria perilaku sebuah milestone di browser pada lingkungan uji lokal.
_Avoid_: blind-behavior

**Temuan yang disengketakan**:
Temuan reviewer yang dibantah oleh eksekutor dan dikirim kepada pemilik melalui jurnal.

## Catatan

**Kartu**:
State mesin Friday untuk sebuah tugas: peristiwa, milestone, putusan, pengeluaran.

**Jurnal**:
Riwayat tugas yang mudah dibaca di Obsidian, dibangun dari kartu dengan templat.
