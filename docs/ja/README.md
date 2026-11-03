<!-- i18n: source=README.md sha256=5262ab4aca2b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — ネオンロゴ" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>金曜日をぐっと近づける、個人用の開発パイプライン。</strong><br>
タスクからドラフト MR まで、計画とレビューは高性能なモデルが、コードの実装は低コストなモデルが担当します。</p>

<p align="center">Python 3.9+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · 初期バージョン</p>

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

必要なものは、Python 3.9+、Git、そして Claude Code または Codex CLI (あるいはその両方) です。ゲートとブラウザ受け入れ確認には Docker も必要です。

```sh
git clone git@github.com:avocawo/friday.git && cd friday
mkdir -p profiles
cp examples/models.json examples/project-profile.json profiles/
# profiles/project-profile.json に、リポジトリのパス、チェックのコマンド、ゲートを設定します
./bin/friday doctor --profile profiles/project-profile.json
./bin/friday start TASK-1 --profile profiles/project-profile.json --brief task.md
./bin/friday approve TASK-1
./bin/friday status
```

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

プロファイル、モデル、動作上の境界の詳細は[ガイド](guide.md)を参照してください。パイプラインがこのように作られている理由は、[パイロット分析](../process-redesign.md) (ロシア語) と [ADR](../adr/) (ロシア語) にあります。

## 開発

```sh
docker build -f Dockerfile.test -t friday-test:local .
./bin/test-offline
```

テストは、ネットワークのないコンテナー内で、一時的なデータと偽のエージェントを使って実行されます。

[ガイド](guide.md) · [用語集](GLOSSARY.md) · [コントリビュート](../../CONTRIBUTING.md) · [ライセンス](../../LICENSE)
