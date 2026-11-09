<!-- i18n: source=docs/guide.md sha256=9198a3cbcbff -->
# Friday 가이드

[English](../guide.md) · [Русский](../ru/guide.md) · [简体中文](../zh-cn/guide.md) · [日本語](../ja/guide.md) · **한국어** · [Português (Brasil)](../pt-br/guide.md) · [Español](../es/guide.md) · [Deutsch](../de/guide.md) · [Français](../fr/guide.md) · [Bahasa Indonesia](../id/guide.md)

_이 문서는 영어판 문서를 번역한 것입니다. 내용이 다를 경우 영어판이 우선합니다._

## 설치

1. Friday를 클론하고 `bin`을 `PATH`에 추가하거나 `FRIDAY_BIN`을 설정합니다.
2. 에이전트에 필요한 CLI를 설치합니다. Anthropic과 Z.ai 모델에는 Claude Code(`claude`)가, OpenAI 모델에는 Codex CLI(`codex`)가 필요합니다. 설치한 CLI에는 평소처럼 로그인해 둡니다.
3. 게이트와 브라우저 인수 확인을 위해 Docker를 설치합니다. 브라우저 인수 확인에는 디버깅 포트 9333이 열린 Chrome과, 테스트 환경에 로그인된 세션도 필요합니다.
4. 상태는 `~/.local/share/friday`에 저장됩니다(`FRIDAY_HOME`으로 변경할 수 있습니다). 프로필과 시크릿은 저장소에 들어가지 않습니다. `profiles/` 폴더가 `.gitignore`에 있기 때문입니다.

### Codex와 Claude Code용 플러그인

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

이 플러그인은 에이전트가 `status`와 `plan`을 읽도록 가르칩니다. 작업을 시작하고, 승인하고, 결정을 내리는 일은 오너가 직접 합니다.

## 작업이 실행되는 방식

1. **`start`**는 최신 `origin/<base_branch>`에서 `friday/KEY` 브랜치와 워크트리를 만들고, 역할 배정(role assignment)을 확정한 뒤 계획을 작성합니다. 계획에는 2~5개 단계로 이루어진 마일스톤, 파일, 규모 추정, 코드 기준과 브라우저 기준, 그리고 실행자를 위한 프로젝트 규칙 요약이 담깁니다. 계획은 먼저 모델 없이 검사합니다(규모, 파일, 순서, 기준). 그다음 다른 벤더의 모델이 계획을 리뷰하고, 플래너는 지적에 한 번 답합니다. 각 지적을 수용하거나, 이유를 들어 반박합니다.
2. **`plan`**은 계획, 질문, 반박된 지적, 그리고 진행 중인 다른 작업과 겹치는 파일을 보여 줍니다.
3. **`approve`**는 계획을 승인하고 파이프라인을 백그라운드에서 시작합니다(`--no-run`은 승인만 하며, 나중에 `run`으로 실행합니다).
4. **마일스톤.** 실행자는 마일스톤마다 하나의 세션에서 작업하고 단계마다 커밋합니다. 게이트(gate)는 커밋에 대해 실행됩니다. 게이트가 실패하면 실행자는 로그에서 실패한 부분만 받습니다. 마일스톤 리뷰는 이 마일스톤의 diff를 게이트 결과와 함께 검토합니다. 실행자는 차단 지적을 수정하거나 이의를 제기하며, 이견이 있는 지적(disputed finding)은 오너에게 전달됩니다. 인터페이스가 있는 마일스톤은 격리된 프리뷰에서 브라우저 인수 확인(browser acceptance)을 거칩니다. 연속으로 두 번 실패하면, 더 높은 등급의 모델이 새 세션에서 그 마일스톤을 넘겨받습니다.
5. **최종.** 전체 diff에 대한 최종 리뷰와 블라인드 검사(blind check)가 병렬로 실행됩니다. 차단 지적은 수정 라운드를 한 번 받습니다.
6. 프로필에 `delivery`가 설정되어 있으면 **초안 MR**을 엽니다. 그렇지 않으면 MR 본문을 작업 폴더의 `merge-request.md`에 저장합니다.

파이프라인이 멈추는 경우는 게이트를 한 번도 통과하지 못했을 때, 실행자가 아무것도 하지 않았을 때, 환경을 사용할 수 없을 때, 규칙이 허용하는 모델을 모두 소진했을 때뿐입니다. 리뷰에서 의견이 갈려도 멈추지 않습니다.

`decide KEY name "text" --reopen M2`는 오너의 결정을 기록합니다. 지정한 마일스톤과 그에 의존하는 마일스톤만 다시 열립니다. `--reopen`이 없으면 이후의 마일스톤이 그 결정을 반영합니다. `resume`은 중지되었거나 실패한 작업을 이어서 진행합니다. `stop`은 실행 중인 작업을 중지하며, 에이전트는 자식 프로세스와 함께 종료됩니다.

승인 전에 계획을 돌려보내려면 바꿀 내용을 `decide`로 기록하고 `replan KEY`를 실행합니다. 플래너는 대기 중인 계획을 새 세션에서 고치며, 그 계획 이후에 기록된 결정을 수정 이유로 삼습니다. 수정된 계획은 검사와 다른 벤더 모델의 리뷰를 다시 거친 뒤, `approval: auto`여도 여러분의 승인을 기다립니다. 작업의 카드, 이력, 비용은 그대로 남습니다. `decide` 자체는 모델을 실행하지 않으므로 여러 결정을 기록한 뒤 한 번만 다시 계획할 수 있습니다. 재계획이 실패해도 이전 계획이 계속 승인을 기다립니다. Friday가 작업 설명을 저장하기 전에 시작한 작업이라면 `--brief`로 작업 설명을 다시 전달합니다.

## 프로필

프로필은 JSON 파일입니다. `extends` 키로 다른 프로필(예: 공용 모델 카탈로그)을 포함할 수 있으며, 경로는 프로필 파일을 기준으로 합니다.

| Key | Purpose |
| --- | --- |
| `name`, `repository` | 프로젝트 이름과 저장소 경로 |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | 작업 브랜치를 어디서 만들고 어디에 둘지. 기본 템플릿은 `friday/{key}`이고, 레이아웃은 `key` 또는 `branch`입니다 |
| `models`, `roles` | 모델 카탈로그와 역할별 선호(아래 참고) |
| `engineering_policy` | 플래너와 리뷰를 위한 규칙 |
| `rules_docs` | 프로젝트 규칙이 담긴 문서. Friday는 이 문서들로 플래너와 계획 리뷰어를 위한 요약을 하나 만듭니다(기본값 `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). 요약은 내용 해시로 저장되며, 문서가 바뀌지 않는 동안 여러 작업이 재사용합니다. 플래너는 문서를 통째로 읽지 않습니다 |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | 플래너와 계획 리뷰어의 프롬프트에 넣는 저장소 맵으로, 두 역할이 도구 호출로 구조를 탐색하지 않아도 되게 합니다. `none`(기본값): 맵 없음. `builtin`: 추적 중인 파일과 줄 수. `graphify`: 여기에 각 파일의 심볼과 그 파일을 import하는 파일 수를 더한 것으로, [graphify](https://github.com/Graphify-Labs/graphify) 그래프에서 가져옵니다(`uv tool install 'graphifyy[mcp]==0.9.100'`. 코드를 파싱할 뿐 모델은 쓰지 않으며, 그래프는 저장소가 아니라 `FRIDAY_HOME/maps`에 저장됩니다). CS-2에서 측정했을 때 맵은 계획 비용을 줄이지 못했으므로(맵 없음 $6.19, `builtin` $7.08, `graphify` $7.97, 각 1회 실행) 기본값은 꺼짐입니다. 맵은 커밋 단위로 캐시됩니다. `repo_map_budget`자(60000)보다 길면 먼저 심볼을 빼서 줄이고, 그다음 폴더를 접어서 줄입니다. `repo_map_mcp: true`(`graphify`와 함께만 사용)는 두 역할에 그래프 쿼리 `query_graph`, `get_node`, `get_neighbors`, `shortest_path`를 제공하며, Codex에서는 실제 환경에서 검증하지 않았습니다. 계획 리뷰어는 계획에 언급된 파일의 현재 내용을 받습니다(최대 40000자) |
| `approval` | `owner`(기본값): 계획이 `approve`를 기다립니다. `auto`: 리뷰 후 계획이 자동으로 승인되고 파이프라인이 즉시 시작됩니다. 계획이 자동 승인되었다는 사실은 저널에 기록됩니다 |
| `limits` | `stage_files`와 `stage_lines`: 단계의 한도(10과 500) |
| `worktree_setup` | 계획을 세우기 전에 Friday가 새 워크트리에서 실행하는 명령(의존성 설치 등). `{repository}`와 `{worktree}` 치환을 쓸 수 있는 인자 배열이며, 반복 실행해도 안전해야 합니다. macOS에서 npm을 쓰는 예: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | 실행자가 직접 실행해도 되는 명령으로, 정확한 형태로 적습니다. `"python3 -m pytest"`처럼 쓰고 `python`은 안 됩니다 |
| `executor_deny_read` | 실행자의 셸이 읽을 수 없는 추가 경로 |
| `milestone_rounds` | 마일스톤 하나에 실행자가 가질 수 있는 라운드 수(4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | Docker에서 실행하는 검사 명령. `git-diff`는 작업의 기준 지점과 비교합니다 |
| `environment_errors` | 코드 실패가 아니라 환경 실패를 뜻하는 로그 줄의 패턴 |
| `browser`, `preview`, `behavior_test_policy` | 테스트 환경, 프리뷰, 브라우저 인수 확인의 규칙 |
| `tracker` | 작업 설명을 읽는 데 쓰는 Yandex Tracker(API 또는 MCP) |
| `journal` | 작업 저널을 쓸 위치: `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`. `journal_dir`은 markdown 저널 하나를 위한 짧은 형태입니다 |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}`, 그리고 `github.repository` 키 또는 `gitlab.base_url` + `gitlab.project` |

### 모델과 역할

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`: 모델을 실행하는 주체로, `claude` 또는 `codex`입니다. Z.ai는 Claude Code를 통해 실행되며, 토큰은 소유자만 접근할 수 있는 파일에서 읽습니다.
- `tier`: `frontier`, `strong`, `efficient` 중 하나입니다. `strength`는 같은 등급 안에서 모델의 순서를 정합니다.
- `effort`: 추론의 깊이(`low` … `max`)입니다. 하나의 모델을 서로 다른 effort로 여러 번 기술할 수 있습니다.
- `scarce`: 비싼 모델입니다. 규칙상 이 모델 없이 작업을 진행할 수 있다면 작업당 한 번만 사용합니다.
- `price`: 종류별(`input`, `output`, `cache_read`, `cache_write`) 100만 토큰당 가격으로, 벤더가 가격을 알려 주지 않을 때 비용을 추정하는 데 씁니다.

역할은 `plan`, `plan-review`, `develop`, `review`, `final-review`, `blind-code`, `browser`입니다. `roles`의 목록은 선호일 뿐이며, 목록에 적합한 모델이 없으면 Friday가 카탈로그에서 다른 모델을 고릅니다. 하드 규칙은 다음과 같습니다. 코드는 `frontier` 모델만 리뷰하고, 리뷰어는 작업 작성자보다 약하지 않으며, 블라인드 검사는 코드 작성자가 하지 않습니다. 소프트 규칙은 우선순위 순으로 다음과 같습니다. 작성자와 같은 모델이 아닐 것, 비싼 모델은 한 번만 쓸 것, 다른 벤더일 것, 블라인드 검사를 계획 작성자가 하지 않을 것. 소프트 규칙은 다른 방법이 전혀 없을 때만 어길 수 있으며, 위반은 작업 이력에 기록됩니다.

역할 배정 예시는 [examples/models.json](../../examples/models.json)에 있습니다.

## 저널

작업 카드가 유일한 기준 정보입니다. 저널은 카드를 보여 주며, 계획이 준비되고 승인되었을 때, 마일스톤이 완료되거나 실패했을 때, 오너가 결정을 내렸을 때, 최종 리뷰, MR, 중지와 같은 주요 이벤트 후에 스스로 갱신됩니다. 직접 갱신하려면 `friday journal KEY`를 실행합니다.

- `markdown`: `KEY.md` 페이지와 모든 작업을 나열하는 공용 개요 페이지입니다. Obsidian에서는 볼트 폴더를 지정합니다. 페이지에는 front matter(키, 상태, 비용, 태그 `friday`)가 있고 작업끼리 위키 링크로 연결됩니다. 작업 페이지에는 가정과 해결되지 않은 지적이 포함된 계획, 실행자 요약과 커밋이 포함된 마일스톤, 이견이 있는 지적, 결정, 역할별 비용, 타임라인이 담깁니다.
- `jsonl`: 스프레드시트나 대시보드에서 분석할 수 있도록 작업의 모든 이벤트를 담습니다.

템플릿은 `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}` 치환을 쓰는 Markdown입니다. Friday는 대상의 `template` 필드에 지정된 템플릿을 먼저 쓰고, 없으면 프로젝트 저장소의 `.friday/task.md`, 그다음 내장 템플릿([friday/templates](../../friday/templates))을 씁니다. `<!-- friday:notes -->`와 `<!-- /friday:notes -->` 사이의 텍스트는 오너의 몫이며 갱신할 때마다 그대로 유지됩니다. Friday는 자신이 쓰지 않은 페이지를 덮어쓰지 않습니다.

## 머신 설정

`FRIDAY_HOME/machine.json`은 모든 작업이 공유하는 자원을 정합니다.

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

기본적으로 게이트는 하나, 브라우저는 두 개가 동시에 실행되며 벤더에는 제한이 없습니다. 벤더 할당량은 자동으로 추적됩니다. 한도에 걸린 실행은 할당량이 돌아오는 시각을 기록하고, 그때까지는 모델을 고를 때 해당 벤더를 건너뜁니다.

## 경계

- **실행자 샌드박스.** 실행자의 셸(Claude Code)은 샌드박스에서 실행됩니다. 네트워크가 없고, `~/.ssh`, `~/.aws`, `~/.config` 등은 읽을 수 없으며, 워크트리와 임시 폴더에만 쓸 수 있고, 샌드박스 밖에서는 실행할 수 없습니다. `executor_commands`에 없는 명령과 git 명령이 아닌 명령은 거부됩니다. Codex는 같은 경계를 가진 자체 샌드박스에서 실행됩니다.
- **리뷰어**는 읽기만 합니다. 블라인드 검사는 빈 폴더에서 실행됩니다.
- **게이트**는 네트워크가 없는 컨테이너에서, 읽기 전용으로 마운트한 커밋 스냅숏에 대해 실행됩니다.
- **브라우저**는 프리뷰 하나에 대한 Friday의 도구만 받으며, 인증 우회와 인증된 요청의 헤더 읽기는 금지됩니다.
- **게시.** Friday는 자기 작업의 브랜치만 푸시하고 초안 MR/PR만 엽니다. 다른 사람의 브랜치를 덮어쓰거나, 머지하거나, 배포하지 않습니다.
- **아직 실제 환경에서 검증하지 않음:** Codex의 역할, 브라우저 인수 확인, GitLab 게시는 가짜(fake)를 쓴 테스트로만 검증했습니다.
