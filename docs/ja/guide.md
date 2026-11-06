<!-- i18n: source=docs/guide.md sha256=9f95975c05a4 -->
# Friday ガイド

[English](../guide.md) · [Русский](../ru/guide.md) · [简体中文](../zh-cn/guide.md) · **日本語** · [한국어](../ko/guide.md) · [Português (Brasil)](../pt-br/guide.md) · [Español](../es/guide.md) · [Deutsch](../de/guide.md) · [Français](../fr/guide.md) · [Bahasa Indonesia](../id/guide.md)

_この文書は英語版ドキュメントの翻訳です。内容が異なる場合は、英語版が優先されます。_

## インストール

1. Friday をクローンして `bin` を `PATH` に追加するか、`FRIDAY_BIN` を設定します。
2. エージェントが必要とする CLI をインストールします。Anthropic と Z.ai のモデルには Claude Code (`claude`)、OpenAI のモデルには Codex CLI (`codex`) を使います。どちらも通常どおりサインインしてください。
3. ゲートとブラウザ受け入れ確認のために、Docker をインストールします。ブラウザ受け入れ確認には、デバッグポート 9333 を開いた Chrome と、テスト環境にサインイン済みのセッションも必要です。
4. 状態は `~/.local/share/friday` に保存されます (`FRIDAY_HOME` で上書きできます)。プロファイルとシークレットはリポジトリには入りません。`profiles/` フォルダーは `.gitignore` に含まれています。

### Codex と Claude Code 用のプラグイン

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

このプラグインは、エージェントに `status` と `plan` の読み方を教えます。タスクの開始、承認、決定の記録は、あなたの手に残されます。

## タスクの流れ

1. **`start`** は、最新の `origin/<base_branch>` からブランチ `friday/KEY` とワークツリーを作成し、ロール割り当て (role assignment) を確定して、計画を書きます。計画の内容は、2〜5 ステージのマイルストーン、ファイル、規模の見積もり、コードとブラウザの基準、そしてエグゼキューター向けのプロジェクトルールの要約です。計画はまずモデルを使わずに検査されます (規模、ファイル、順序、基準)。その後、別のベンダーのモデルがレビューし、プランナーは指摘に一度だけ回答します。各指摘を受け入れるか、理由を添えて却下します。
2. **`plan`** は、計画、質問、却下された指摘、ほかのアクティブなタスクとのファイルの重なりを表示します。
3. **`approve`** は計画を承認し、バックグラウンドでパイプラインを開始します (`--no-run` は承認のみを行います。あとで `run` で実行してください)。
4. **マイルストーン。** エグゼキューターは、マイルストーンごとに 1 つのセッションで作業し、ステージごとにコミットします。ゲートはそのコミットに対して実行されます。ゲートが失敗した場合、エグゼキューターが受け取るのは、ログのうち失敗したセクションだけです。マイルストーンレビューは、このマイルストーンの差分とゲートの結果を合わせて確認します。エグゼキューターは、ブロッキングの指摘を修正するか、異議を唱えます。異議を唱えた指摘 (争点となった指摘) はあなたに送られます。インターフェースを伴うマイルストーンは、隔離されたプレビューでブラウザ受け入れ確認を通ります。2 回連続で失敗すると、より上位のティアのモデルが、新しいセッションでそのマイルストーンを引き継ぎます。
5. **最終。** 最終レビューと、差分全体に対するブラインドチェックが並行して実行されます。ブロッキングの指摘には、修正の機会が 1 回与えられます。
6. **ドラフト MR** は、プロファイルに `delivery` が設定されていれば作成されます。設定されていなければ、その本文はタスクフォルダーの `merge-request.md` に保存されます。

パイプラインが止まるのは、ゲートが一度も通らなかったとき、エグゼキューターが何もしなかったとき、環境が利用できないとき、ルールで許可されたモデルを使い切ったときだけです。レビューでの意見の相違では止まりません。

`decide KEY name "text" --reopen M2` は、あなたの決定を記録します。再開されるのは、指定したマイルストーンと、それに依存するマイルストーンだけです。`--reopen` を付けない場合は、以降のマイルストーンがその決定を考慮に入れます。`resume` は、停止したタスクや失敗したタスクを続行します。`stop` は実行中のタスクを停止します。エージェントは子プロセスとともに終了します。

## プロファイル

プロファイルは JSON ファイルです。`extends` キーで別のプロファイル (たとえば共有のモデルカタログ) を取り込めます。パスはプロファイルファイルからの相対パスです。

| キー | 用途 |
| --- | --- |
| `name`, `repository` | プロジェクト名と、そのリポジトリのパス |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | タスクのブランチをどこから作成し、どこに置くか。テンプレートの既定値は `friday/{key}`、レイアウトは `key` または `branch` です |
| `models`, `roles` | モデルカタログと、ロールの優先設定 (後述) |
| `engineering_policy` | プランナーとレビューに適用するルール |
| `rules_docs` | プロジェクトのルールを記したドキュメント。Friday はこれらからプランナーと計画レビュアー向けの要約を 1 つ作ります (既定は `AGENTS.md`、`CLAUDE.md`、`docs/*.md`)。要約は内容のハッシュで保存され、ドキュメントが変わらない限りタスク間で再利用されます。プランナーはドキュメントを全文では読みません |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | プランナーと計画レビュアーのプロンプトに入れるリポジトリマップです。ツール呼び出しで構造を調べずに済むようにします。`none` (既定): マップなし。`builtin`: 追跡対象のファイルと行数。`graphify`: さらに、各ファイルのシンボルと、そのファイルを import しているファイルの数を、[graphify](https://github.com/Graphify-Labs/graphify) のグラフから取得します (`uv tool install 'graphifyy[mcp]==0.9.100'`。コードを解析するだけで、モデルは使いません。グラフはリポジトリではなく `FRIDAY_HOME/maps` に保存されます)。CS-2 での計測では、マップによって計画のコストは下がりませんでした (マップなし $6.19、`builtin` $7.08、`graphify` $7.97、いずれも 1 回ずつの実行)。そのため、既定ではオフです。マップはコミット単位でキャッシュされます。`repo_map_budget` の文字数 (60000) を超える場合は、まずシンボルを省き、次にフォルダーをまとめて短縮します。`repo_map_mcp: true` (`graphify` と併用する場合のみ) にすると、両方のロールがグラフに問い合わせられるようになります (`query_graph`、`get_node`、`get_neighbors`、`shortest_path`)。Codex での実機検証は未実施です。計画レビュアーは、計画に名前の挙がっているファイルの現在のテキスト (最大 40000 文字) を受け取ります |
| `approval` | `owner` (既定): 計画は `approve` を待ちます。`auto`: レビューのあとに計画が自動で承認され、すぐにパイプラインが始まります。計画が自動で承認されたことはジャーナルに記録されます |
| `limits` | `stage_files` と `stage_lines`: ステージの上限 (10 と 500) |
| `worktree_setup` | 計画の前に、新しいワークツリーで Friday が実行するコマンド (依存関係のインストールなど)。`{repository}` と `{worktree}` を置換できる引数の配列で、繰り返し実行しても問題のないものにしてください。macOS で npm を使う例: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | エグゼキューターが自分で実行してよいコマンド。正確な形で書きます。`python` ではなく `"python3 -m pytest"` のようにします |
| `executor_deny_read` | エグゼキューターのシェルが読めない追加のパス |
| `milestone_rounds` | 1 つのマイルストーンに与えられるエグゼキューターのラウンド数 (4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | Docker で実行するチェックコマンド。`git-diff` はタスクのベースと比較します |
| `environment_errors` | コードの失敗ではなく、環境の失敗を意味するログ行のパターン |
| `browser`, `preview`, `behavior_test_policy` | テスト環境、プレビュー、ブラウザ受け入れ確認のルール |
| `tracker` | Yandex Tracker (API または MCP)。タスクの説明を読むために使います |
| `journal` | タスクのジャーナルの書き込み先: `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`。`journal_dir` は、Markdown ジャーナルを 1 つだけ使う場合の短縮形です |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}` と、キー `github.repository`、または `gitlab.base_url` + `gitlab.project` |

### モデルとロール

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`: モデルを動かすもの。`claude` または `codex` です。Z.ai は Claude Code を通して動かします。トークンは、オーナーだけがアクセスできるファイルから読み込まれます。
- `tier`: `frontier`、`strong`、`efficient` のいずれかです。`strength` は、同じティア内でモデルの順序を決めます。
- `effort`: 推論の深さ (`low` … `max`)。1 つのモデルを、異なる effort で複数回記述できます。
- `scarce`: 高価なモデルを表します。ルール上、そのモデルなしでタスクを進められる場合は、1 つのタスクで 1 回だけ使われます。
- `price`: 種類別 (`input`、`output`、`cache_read`、`cache_write`) の 100 万トークンあたりの価格です。ベンダーが価格を報告しない場合に、コストの見積もりに使われます。

ロールは `plan`、`plan-review`、`develop`、`review`、`final-review`、`blind-code`、`browser` です。`roles` のリストは優先設定です。リストに適したモデルがなければ、Friday はカタログから別のモデルを選びます。厳格なルールは次のとおりです。コードをレビューするのは `frontier` のモデルだけ、レビュアーはその作業の作成者より弱くない、ブラインドチェックはコードの作成者が行わない。緩やかなルールは、優先順に次のとおりです。作成者と同じモデルでない、高価なモデルは 1 回だけ、別のベンダー、ブラインドチェックは計画の作成者が行わない。緩やかなルールを破れるのは、ほかに方法がない場合だけで、違反はタスクの履歴に記録されます。

ロール割り当ての例は [examples/models.json](../../examples/models.json) にあります。

## ジャーナル

タスクのカードが唯一の信頼できる情報源です。ジャーナルはカードの内容を表示するもので、重要なイベントの後に自動で更新されます。対象は、計画の完成と承認、マイルストーンの完了と失敗、オーナーの決定、最終レビュー、MR、停止です。手動で更新するには、`friday journal KEY` を実行します。

- `markdown`: ページ `KEY.md` と、すべてのタスクを一覧にした共通の概要ページ。Obsidian では、Vault のフォルダーを指定します。ページにはフロントマター (キー、状態、コスト、タグ `friday`) があり、タスク同士は wiki リンクで結ばれます。タスクのページには、前提条件と未解決の指摘を含む計画、エグゼキューターの要約とコミットを含むマイルストーン、争点となった指摘、決定、ロール別のコスト、タイムラインが含まれます。
- `jsonl`: タスクのすべてのイベント。スプレッドシートやダッシュボードでの分析に使えます。

テンプレートは Markdown で、置換には `{{key}}`、`{{state}}`、`{{status}}`、`{{plan}}`、`{{milestones}}`、`{{disputes}}`、`{{decisions}}`、`{{costs}}`、`{{timeline}}`、`{{notes}}` を使えます。Friday は、対象の `template` フィールド、プロジェクトリポジトリの `.friday/task.md`、組み込みのテンプレート ([friday/templates](../../friday/templates)) の順にテンプレートを探します。`<!-- friday:notes -->` と `<!-- /friday:notes -->` の間のテキストはあなたのものです。更新のたびに保持されます。Friday は、自分が書いていないページを上書きしません。

## マシンの設定

`FRIDAY_HOME/machine.json` は、すべてのタスクが共有するリソースを設定します。

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

既定では、ゲートは 1 つ、ブラウザは 2 つが同時に動き、ベンダーごとの制限はありません。ベンダーのクォータは自動で追跡されます。上限に達した実行はクォータが戻る時刻を記録し、それまでの間、モデルの選択ではそのベンダーが飛ばされます。

## 境界

- **エグゼキューターのサンドボックス。** エグゼキューターのシェル (Claude Code) はサンドボックス内で動きます。ネットワークなし、`~/.ssh`、`~/.aws`、`~/.config` などの読み取りなし、書き込みはワークツリーと一時フォルダーのみ、サンドボックス外での実行なし、です。`executor_commands` になく、git コマンドでもないコマンドは拒否されます。Codex は、同じ境界を持つ独自のサンドボックスで動きます。
- **レビュアー** は読み取り専用です。ブラインドチェックは、空のフォルダーで実行されます。
- **ゲート** は、ネットワークのないコンテナーで、読み取り専用でマウントしたコミットのスナップショットに対して実行されます。
- **ブラウザ** が受け取るのは、1 つのプレビュー用の Friday のツールだけです。認可の回避や、認可済みリクエストのヘッダーの読み取りは禁止されています。
- **公開。** Friday がプッシュするのは自分のタスクのブランチだけで、作成するのはドラフトの MR/PR だけです。他人のブランチの上書き、マージ、デプロイは行いません。
- **実機では未検証:** Codex でのロール、ブラウザ受け入れ確認、GitLab への公開は、偽のエージェントを使ったテストでのみ検証されています。
