<!-- i18n: source=README.md sha256=a008f1f30688 -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — ネオンロゴ" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>金曜日をぐっと近づける、個人用の開発パイプライン。</strong><br>
タスクからドラフト MR まで、計画とレビューは高性能なモデルが、コードの実装は低コストなモデルが担当します。</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · 初期バージョン</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · **日本語** · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · [Deutsch](../de/README.md) · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

_この文書は英語版ドキュメントの翻訳です。内容が異なる場合は、英語版が優先されます。_

---

```text
タスク → 計画 (高性能モデル) → 計画レビュー (別のベンダー) → あなたの承認
     → マイルストーン: サンドボックス内のエグゼキューター → ゲート → マイルストーンレビュー → UI のブラウザ受け入れ確認
     → 最終レビュー + ブラインドチェック → ドラフト MR
```

あなたが関わるのは 2 回だけです。計画を承認するときと、ドラフト MR を受け入れるときです。この 2 点の間、パイプラインは止まりません。レビューで争点となった指摘は、MR と一緒にあなたへ届けられます。

## 主な特長

- **モデルは名前ではなくティアで選びます。** プロファイルには、モデルごとにベンダーとティア (`frontier`、`strong`、`efficient`) を記したモデルカタログがあります。コードをレビューするのは `frontier` のモデルだけで、レビュアーが作成者より弱くなることはなく、可能な限り作成者とは別のベンダーのモデルが選ばれます。高価なモデルには、1 つのタスクで 1 回だけ使うよう印を付けられます。
- **低コストなエグゼキューターと素早いフィードバック。** エグゼキューターは、ネットワークのないサンドボックス内でリンター、型チェック、テストを自分で実行します。ゲートが返すのは、失敗したチェックだけです。
- **何が起きているかが見えます。** `friday status` を実行すると、フェーズ、モデル、エージェントが沈黙している時間、各タスクにかかったコストを確認できます。
- **複数のタスクを同時に処理できます。** タスクごとに専用のブランチとワークツリーが用意されます。同時に実行できるゲート、ブラウザ、同一ベンダーの実行数は、マシンの設定で決まります。

## クイックスタート

必要なものは、Python 3.11+、Git、そして Claude Code (Codex CLI は任意) です。ゲートとブラウザ受け入れ確認には Docker も必要です。

```sh
uv tool install git+https://github.com/avocawo/friday   # or: pipx install git+https://github.com/avocawo/friday
mkdir -p ~/.config/friday/profiles && cd ~/.config/friday/profiles
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/models.json
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/project-profile.json
# in project-profile.json, set your project's repository, worktree_root and executor_commands
export FRIDAY_PROFILE=~/.config/friday/profiles/project-profile.json
friday doctor
friday start TASK-1 --brief task.md
friday approve TASK-1
friday status
```

Friday は 10 言語に対応しています。`friday --lang ru …`、`FRIDAY_LANG=ru`、または `friday config language ru` で切り替えられます。エージェントは常に英語で作業し、あなたが読む内容は翻訳されます。Node と Python のプロジェクト向けのプロファイル例は [examples/profiles](../../examples/profiles) にあります。

## コマンド

| コマンド | 内容 |
| --- | --- |
| `start KEY --profile P [--brief F]` | ワークツリーと計画を作成し、別のモデルに計画をレビューさせます |
| `plan KEY` | 計画を、未解決の指摘とともに表示します |
| `approve KEY [--no-run]` | 計画を承認し、バックグラウンドでパイプラインを開始します |
| `status [KEY] [--json]` | タスクの進行状況とコストを表示します |
| `decide KEY name "text" [--reopen M2]` | オーナーの決定を記録します。指定したマイルストーンだけを再開します |
| `run KEY` / `resume KEY` / `stop KEY` | このターミナルで実行、続行、停止します |
| `archive KEY [--force]` | ワークツリーとカードを削除します。ブランチは残ります |
| `journal KEY` | タスクのジャーナル (Obsidian、jsonl) を更新します |
| `doctor --profile P` | マシンとプロファイルの準備ができているかを確認します |
| `config language L` | このマシンで Friday が使う言語を設定します |

プロファイル、モデル、動作上の境界の詳細は[ガイド](guide.md)を参照してください。パイプラインがこのように作られている理由は、[パイロット分析](../process-redesign.md) (ロシア語) と [ADR](../adr/) (0001–0006 はロシア語) にあります。

## 開発

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

テストは一時的なデータと偽のエージェントを使い、ネットワークも Docker も必要としません。コードの構成は [docs/architecture.md](../architecture.md)、変更を提案する方法は [CONTRIBUTING.md](../../CONTRIBUTING.md) にあります。

[ガイド](guide.md) · [用語集](GLOSSARY.md) · [コントリビュート](../../CONTRIBUTING.md) · [ライセンス](../../LICENSE)
