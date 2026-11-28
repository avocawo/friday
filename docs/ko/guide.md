<!-- i18n: source=docs/guide.md sha256=21bda6e09dc0 -->
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

1. **`start`**는 최신 `origin/<base_branch>`에서 `<repository>/KEY` 브랜치(예: `octis/31`)와 워크트리를 만들고, 역할 배정(role assignment)을 확정한 뒤 계획을 작성합니다. 계획에는 2~5개 단계로 이루어진 마일스톤, 파일, 규모 추정, 코드 기준과 브라우저 기준, 그리고 실행자를 위한 프로젝트 규칙 요약이 담깁니다. 계획은 먼저 모델 없이 검사합니다(규모, 파일, 순서, 기준). 그다음 다른 벤더의 모델이 계획을 리뷰하고, 플래너는 지적에 한 번 답합니다. 각 지적을 수용하거나, 이유를 들어 반박합니다.
   `"toolset": {"plan": "project"}`를 지정하면 프로젝트 자체의 프로세스가 계획을 작성합니다([ADR 0015](../adr/0015-project-toolset.md)). Friday는 워크트리에서 프로젝트의 설정, 훅, 스킬, 에이전트, MCP를 갖춘 일반 Claude Code 세션을 시작하고, 작업 설명과 함께 `project.plan_command`를 전달합니다. 세션은 프로젝트의 계획 파일(`project.plan_file`)을 쓰고, 같은 계획의 요약을 Friday의 스키마로 돌려줍니다. Friday는 요약을 모델 없이 검사하며, 프로젝트의 프로세스에 자체 리뷰가 있으므로 다른 벤더의 리뷰는 실행하지 않습니다. 세션은 `auto` 권한 모드로 실행됩니다. 프로젝트의 프로세스가 백그라운드 리뷰를 기다릴 수 있으므로, 세션의 결과는 프로세스가 종료되기 전의 마지막 응답입니다.
2. **`plan`**은 계획, 질문, 반박된 지적, 그리고 진행 중인 다른 작업과 겹치는 파일을 보여 줍니다.
3. **`approve`**는 계획을 승인하고 파이프라인을 백그라운드에서 시작합니다(`--no-run`은 승인만 하며, 나중에 `run`으로 실행합니다).
   `"toolset": {"execute": "project"}`를 지정하면 Friday는 마일스톤 대신 프로젝트 자체의 프로세스를 실행합니다([ADR 0015](../adr/0015-project-toolset.md)). `develop` 역할의 모델로 `project.execute_command`를 실행하는 Claude Code 세션 하나입니다. 세션은 계획 요약에 있는 단계 id를 제목 맨 앞에 붙여(`M1.1: …`) 단계마다 커밋하며, 아무것도 게시하지 않습니다. 질문이 나오면 세션이 끝납니다. 작업은 중지되고, 질문은 `status`에 표시되며, `decide`로 준 답은 같은 세션을 백그라운드에서 이어서 실행합니다. `resume`은 답 없이 같은 일을 합니다. 세션은 자신을 멈춘 원인을 전달받습니다. 세션이 질문 없이 끝났는데 환경이 작업을 멈춘 경우(게이트 이미지, 테스트 환경 로그인)에는 `resume`이 세션을 깨우지 않고 세션 이후의 검사만 다시 실행합니다. 세션이 끝나면 Friday는 트리가 깨끗한지, 모든 단계에 `limits` 안의 커밋이 있는지(해당 커밋의 `git diff --numstat`) 확인하고, 게이트와 인터페이스가 있는 마일스톤의 브라우저 인수 확인을 실행한 뒤 평소처럼 최종 검사와 MR로 넘어갑니다. 이 모드의 최종 리뷰는 먼저 저장소 루트의 `AGENTS.md`(또는 `CLAUDE.md`)를 읽고, 계획의 규칙뿐 아니라 프로젝트 문서의 항목에 비춰 diff를 검사합니다. 어떤 불일치든 오너에게 가는 질문이 됩니다.
4. **마일스톤.** 실행자는 마일스톤마다 하나의 세션에서 작업하고 단계마다 커밋합니다. 게이트(gate)는 커밋에 대해 실행됩니다. 게이트가 실패하면 실행자는 로그에서 실패한 부분만 받습니다. 부분이 한도보다 길면 제목과 오류 줄(앞뒤 문맥 포함)을 남기며, TAP 형식의 실패한 테스트(`not ok …`와 진단 내용)가 먼저 옵니다. 마일스톤 리뷰는 이 마일스톤의 diff를 게이트 결과와 함께 검토합니다. 실행자는 차단 지적을 수정하거나 이의를 제기하며, 이견이 있는 지적(disputed finding)은 오너에게 전달됩니다. 인터페이스가 있는 마일스톤은 격리된 프리뷰에서 브라우저 인수 확인(browser acceptance)을 거칩니다. 연속으로 두 번 실패하면, 프로필이 `executor_tiers` 제한을 설정한 경우 그 범위 안에서 Friday가 실행자의 등급을 올리며, 교체된 실행자는 새 세션을 시작합니다.
5. **최종.** `final_review_roles`의 역할이 전체 diff를 검사합니다. 기본적으로 최종 리뷰와 블라인드 검사(blind check)가 병렬로 실행되며, 프로필에서 최종 리뷰만 남길 수도 있습니다. 차단 지적은 수정 라운드를 한 번 받습니다.
6. 프로필에 `delivery`가 설정되어 있으면 **초안 MR**을 엽니다. 그렇지 않으면 MR 본문을 작업 폴더의 `merge-request.md`에 저장합니다.

파이프라인은 게이트를 한 번도 통과하지 못했을 때, 실행자가 아무것도 하지 않았을 때, 환경을 사용할 수 없을 때, 규칙이 허용하는 모델을 모두 소진했을 때, 설정한 예산이 소진되었을 때 멈춥니다. 병렬 변경의 겹침, 통합되지 않은 채 남은 브랜치, 최종 리뷰의 입력이 바뀌는 경우에도 작업을 계속하기 전에 확인이 필요합니다. 리뷰에서 의견이 갈려도 멈추지 않습니다. 아무것도 검사하지 않은 마일스톤(게이트 없음, 실행자의 검사가 실행되지 않음, 브라우저 인수 확인 없음)은 이유와 함께 "완료, 미검증"으로 닫히며, 상태, 저널, MR에 표시됩니다([ADR 0009](../adr/0009-unverified-is-not-done.md)). 실행자의 검사를 실행할 수 없고 게이트도 없으면 마일스톤은 차단됩니다.

`decide KEY name "text" --reopen M2`는 오너의 결정을 기록합니다. Friday의 계획이 승인을 기다리는 동안에는 `decide`가 계획도 수정합니다([ADR 0016](../adr/0016-decision-revises-the-plan.md)). 플래너가 `--reopen`에 지정된 마일스톤을 새 세션에서 다시 쓰고 나머지는 그대로 가져오며, `--reopen`이 없으면 영향받는 마일스톤을 직접 고릅니다. 다른 모델은 바뀐 마일스톤만 리뷰합니다. `decide`는 계획에서 수정된 마일스톤, 다시 실행하도록 다시 열린 마일스톤, 결정이 컨텍스트에만 추가된 마일스톤을 각각 출력하며, `friday plan KEY --diff`는 이전 수정본과의 차이를 보여 줍니다. 수정이 실패하면 계획은 그대로 승인을 기다리고 결정은 기록되므로, 같은 `decide`를 다시 실행합니다. 프로젝트의 프로세스가 만든 계획이 승인을 기다리는 동안에는 `decide`가 오너의 답으로 그 세션을 이어서 실행하고 계획의 새 수정본을 보여 줍니다. 이런 `decide`는 매번 세션을 따로 이어서 실행하므로, 계획의 모든 질문에 한 번에 답하세요. 같은 방식으로 `decide`는 프로젝트의 프로세스에서 중지된 실행 세션의 질문에도 답합니다. 지정한 마일스톤과 그에 의존하는 마일스톤만 다시 열립니다. `--reopen`이 없으면 이후의 마일스톤이 그 결정을 반영합니다. `resume`은 중지되었거나 실패한 작업을 이어서 진행합니다. `stop`은 실행 중인 작업을 중지하며, 에이전트는 자식 프로세스와 함께 종료됩니다.

원래의 작업 설명은 전체가 보관되어 실행자와 리뷰에 전달되므로, 계획이 사실을 줄이더라도 정확한 해시와 URL은 남습니다. 저장된 설명이 없는 오래된 카드에는 `resume KEY --brief task.md`를 사용합니다. 설명은 텍스트가 원래 계획의 지문(fingerprint)과 일치할 때만 복원되며, 다른 텍스트는 새 계획이 필요합니다. 승인, 단계, 예산 카운터는 초기화되지 않습니다.

실패한 마일스톤이 이어질 때 이미 만든 커밋은 원래의 리뷰 경계를 유지하며, 빈 추가 커밋은 필요 없습니다. 실행자는 이전 게이트 실패를 받으며, Python 트레이스백은 진단 출력의 한도 안에서 전체 본문이 함께 전달됩니다.

## 프로필

프로필은 JSON 파일입니다. `extends` 키로 다른 프로필(예: 공용 모델 카탈로그)을 포함할 수 있으며, 경로는 프로필 파일을 기준으로 합니다.

| Key | Purpose |
| --- | --- |
| `name`, `repository` | 프로젝트 이름과 저장소 경로 |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | 작업 브랜치를 어디서 만들고 어디에 둘지. 기본값은 `<repository>/{key}`이고, 명시적인 `branch_template`에는 `{key}`를 하나 넣습니다. 레이아웃은 `key` 또는 `branch`입니다 |
| `models`, `roles` | 모델 카탈로그와 역할별 선호(아래 참고) |
| `engineering_policy` | 플래너, 실행자, 마일스톤 리뷰, 최종 리뷰, 수정을 위한 규칙 |
| `project_lessons` | 리뷰에서 계속 발견되는 프로젝트의 함정: 짧은 점검 항목의 목록입니다. 실행자, 마일스톤 리뷰, 최종 리뷰, 수정이 변경을 이 목록에 비춰 확인합니다 |
| `rules_docs` | 프로젝트 규칙이 담긴 문서. Friday는 이 문서들로 플래너와 계획 리뷰어를 위한 요약을 하나 만듭니다(기본값 `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). 요약은 내용 해시로 저장되며, 문서가 바뀌지 않는 동안 여러 작업이 재사용합니다. 플래너는 문서를 통째로 읽지 않습니다. 패턴은 저장소 루트에서 확장되며, `**`는 하위 폴더까지 내려갑니다(`docs/**/*.md`, `src/**/README.md`). 찾은 파일만 지문에 들어갑니다. 실행자, 마일스톤 리뷰, 최종 리뷰, 수정은 계획이 고른 규칙, 요약의 문서 맵, 선례를 찾을 작업의 기준 리비전을 받습니다. 실행자는 `bin/friday-precedent <base> <pattern> [<path>…]`로 검색합니다(옵션을 통과시키지 않는 `git grep` 래퍼입니다) |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | 플래너와 계획 리뷰어의 프롬프트에 넣는 저장소 맵으로, 두 역할이 도구 호출로 구조를 탐색하지 않아도 되게 합니다. `none`(기본값): 맵 없음. `builtin`: 추적 중인 파일과 줄 수. `graphify`: 여기에 각 파일의 심볼과 그 파일을 import하는 파일 수를 더한 것으로, [graphify](https://github.com/Graphify-Labs/graphify) 그래프에서 가져옵니다(`uv tool install 'graphifyy[mcp]==0.9.100'`. 코드를 파싱할 뿐 모델은 쓰지 않으며, 그래프는 저장소가 아니라 `FRIDAY_HOME/maps`에 저장됩니다). CS-2에서 측정했을 때 맵은 계획 비용을 줄이지 못했으므로(맵 없음 $6.19, `builtin` $7.08, `graphify` $7.97, 각 1회 실행) 기본값은 꺼짐입니다. 맵은 커밋 단위로 캐시됩니다. `repo_map_budget`자(60000)보다 길면 먼저 심볼을 빼서 줄이고, 그다음 폴더를 접어서 줄입니다. `repo_map_mcp: true`(`graphify`와 함께만 사용)는 두 역할에 그래프 쿼리 `query_graph`, `get_node`, `get_neighbors`, `shortest_path`를 제공하며, Codex에서는 실제 환경에서 검증하지 않았습니다. 계획 리뷰어는 계획에 언급된 파일의 현재 내용을 받습니다(최대 40000자) |
| `approval` | `owner`(기본값): 계획이 `approve`를 기다립니다. `auto`: 리뷰 후 계획이 자동으로 승인되고 파이프라인이 즉시 시작됩니다. 계획이 자동 승인되었다는 사실은 저널에 기록됩니다 |
| `limits` | `stage_files`와 `stage_lines`: 단계의 한도(10과 500). 선택 항목인 `task_milestones`와 `task_stages`는 실행 전에 계획 전체를 제한합니다 |
| `toolset`, `project` | 각 국면을 누가 실행하는지: `{"plan": "project", "execute": "friday"}`는 프로젝트의 프로세스가 계획을 작성하고 Friday가 그 계획을 실행한다는 뜻입니다(두 국면 모두 기본값은 `friday`). `project`: `{statement}`가 들어가는 `plan_command`는 프로젝트의 계획 명령으로, 예를 들어 `"/plan-stages {statement}"`입니다. `plan_file`은 워크트리의 계획 파일이고, `{stages}`가 들어가는 `execute_command`와 `stage_argument`는 실행 국면용입니다. `session_env`는 세션이 평소 환경 변수에 더해 받는 환경 변수의 이름이며(예: `git fetch`용 `SSH_AUTH_SOCK`), `disallowed_tools`는 세션에 닫아 둔 도구입니다(예: 오너의 브라우저. 인수 확인은 Friday의 `browser` 역할이 합니다). `timeouts`는 초 단위의 `idle`, `tool`, `total`입니다([ADR 0015](../adr/0015-project-toolset.md)) |
| `worktree_setup` | 계획을 세우기 전에 Friday가 새 워크트리에서 실행하는 명령(의존성 설치 등). `{repository}`와 `{worktree}` 치환을 쓸 수 있는 인자 배열이며, 반복 실행해도 안전해야 합니다. macOS에서 npm을 쓰는 예: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | 실행자가 직접 실행해도 되는 명령으로, 정확한 형태로 적습니다. `"python3 -m pytest"`처럼 쓰고 `python`은 안 됩니다 |
| `executor_deny_read` | 실행자의 셸이 읽을 수 없는 추가 경로 |
| `executor_tiers` | 모든 쓰기 에이전트에 허용되는 등급으로, 예를 들어 `["strong", "efficient"]`입니다. 이 필드가 없으면 모든 등급이 허용됩니다. 복잡도, 에스컬레이션, 할당량, 최종 수정은 이 제한을 따릅니다 |
| `milestone_rounds` | 마일스톤 하나에 실행자가 가질 수 있는 라운드 수(4) |
| `max_parallel_milestones` | `parallel=true`인 독립적이고 준비된 마일스톤이 동시에 실행되는 수(2). `1`이면 하나씩 차례로 실행합니다 |
| `budget` | 선택 항목인 양의 정수 `max_model_runs`와 `max_milestone_runs`: 작업당, 마일스톤당 모든 역할이 허용받은 실행 횟수의 한도입니다. 예약은 원자적이며, 실패한 시도와 끝나지 않은 시도도 횟수에 들어가고, `resume`과 다시 열기는 카운터를 초기화하지 않습니다. 소진되면 작업은 `budget_exhausted`로 멈추며, 예산은 프로필에서 명시적으로 바꿉니다. 비용이 아니라 실행 횟수를 제한합니다 |
| `final_review_roles` | 기본값은 `["final-review", "blind-code"]`이고, `["final-review"]`는 최종 검사를 하나만 둡니다. 끝난 결과는 따로 저장되며, 소스, 계획, 결정, 설정, 역할의 입력이 바뀌지 않았을 때만 재사용됩니다 |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | Docker에서 실행하는 검사 명령. `git-diff`는 작업의 기준 지점과 비교합니다. 프로필은 게이트 이미지를 빌드하는 방법을 단계(`steps`)로 기술하며, `gate_image_step`이 그 단계의 이름입니다. 이미지의 `gate_lockfile`이 작업의 것과 다르면 작업이 멈춥니다. `status`는 단계의 명령과 함께 "오너 단계 필요"를 표시하고, 단계를 마친 뒤 `resume`은 검사를 다시 실행하며 프로젝트의 프로세스의 실행 세션을 깨우지 않습니다 |
| `environment_errors` | 코드 실패가 아니라 환경 실패를 뜻하는 로그 줄의 패턴 |
| `browser`, `preview`, `behavior_test_policy` | 테스트 환경, 프리뷰, 브라우저 인수 확인의 규칙. 프리뷰 백엔드에는 `storybook`이 있습니다(`mount`에 키트의 스냅숏을 둔 `stories`의 쇼케이스, [ADR 0008](../adr/0008-ui-kit-preview-and-design-check.md)). `storybook`에서는 프로젝트별 내용이 모두 프로필에 있습니다. `command`(쇼케이스를 시작하는 인자 배열이며 `{mount}`는 키트의 경로), `port`, `env`, `tmpfs`(쓰기가 허용되는 컨테이너 절대 경로), `links`(스냅숏에서 이미지로 가는 링크, 예: `{"node_modules": "/app/node_modules"}`)이며, 이미지는 `stories.image`입니다 |
| `stories` | 작업의 두 번째 저장소로, 키트의 워크트리를 담는 컴포넌트 쇼케이스(Storybook)입니다. `repository`, `base_branch`, `worktree_root`, `mount`(필수: 쇼케이스 안의 키트 경로), `setup`(새 워크트리에서 실행하는 명령. `{repository}`, `{worktree}`, `{kit}`), `commands`(실행자의 검사), `image`(프리뷰 이미지), `themes`(각 테마에서 브라우저 인수 확인이 스토리를 열고 컴포넌트의 상태를 비교하는 테마들), `theme_query`(`themes`와 함께 필수: `{theme}`가 들어가는 테마의 URL 매개변수)가 있습니다. 쇼케이스 파일은 계획에 `stories:src/stories/X.stories.ts`로 적으며, 결과는 두 저장소 모두의 작업 브랜치입니다 |
| `steps` | Friday가 스스로는 알지 못하는 프로젝트 단계로, 오너만 실행합니다(`friday step NAME [KEY]`, 이름이 없으면 목록과 상태)([ADR 0011](../adr/0011-profile-steps.md)): `{"<name>": {"run": [...], "scope": "task" \| "profile", ...}}`. `run`은 호스트에서 실행되는 인자 배열이며, 작업 단계는 `{key}`, `{worktree}`, `{branch}`, `{base}`, `{head}`, `{showcase}`를, 모든 단계는 `{profile_dir}`, `{repository}`, `{base_branch}`, `{stories_repository}`, `{secret.<name>}`를 받습니다. `description`, `env`(시크릿 값 불가), `cwd`도 있습니다. `lock`은 단계가 실행되는 동안의 공유 자원입니다. 경로는 저장소의 락을 공유하고, 이름은 이름 있는 락입니다. `commit: {"paths": [...], "message": "{key}: ..."}`는 Friday가 이 경로들을 커밋하게 하며, 커밋 후 브랜치가 이 경로 밖에서 바뀌면 단계는 오래된 것이 됩니다. `required`(작업 단계): 단계가 끝나지 않았거나 오래된 동안 `status`, 저널, MR 본문이 그 명령과 함께 알려 줍니다. `check`(프로필 단계)는 `doctor`용 준비 확인 명령입니다. 0은 준비됨, 2는 오래됨(!), 그 밖의 값은 없음(✗)입니다. `secrets: {"<name>": "<path>"}`는 명령이 경로로 받는 파일이며, Friday는 읽지 않습니다. 복잡한 절차는 프로필 옆의 스크립트에 두세요 |
| `design` | UI 작업용 디자인 도구([ADR 0007](../adr/0007-design-tools.md)). `figma: {"url": "http://127.0.0.1:3845/mcp"}`는 Figma 데스크톱의 로컬 Dev Mode MCP 서버입니다. `tools`는 읽기 도구(`get_metadata`, `get_design_context`, `get_screenshot`, `get_variable_defs`)를 좁히며, 쓰기 도구는 허용되지 않습니다. 티켓에 `figma.com/design/…?node-id=…` 링크가 있으면 `start`는 그 노드가 활성 Figma 탭에 열려 있는지 확인하고, 플래너와 계획 리뷰어는 프레임을 읽어 측정 가능한 명세를 계획에 씁니다. 실행자와 리뷰는 Figma에 접근하지 않고 프롬프트로 명세를 받습니다. Friday는 프레임의 스크린샷을 작업 폴더에 저장하며, 브라우저 인수 확인은 프리뷰를 그것과 비교하고 스타일을 측정합니다(`computed_style`). 명세의 수치와 다르면 차단되고, 눈으로만 보이는 차이는 MR의 사소한 지적으로 갑니다. Figma가 응답하지 않거나 노드가 없으면 `start`는 멈추고 무엇을 열어야 하는지 알려 주며, `start --no-design`은 목업 없이 계획합니다. `impeccable: {"skill_dir": ".../skills/impeccable", "references": {...}}`는 Friday가 UI 마일스톤의 프롬프트에 넣는, /impeccable 스킬의 `reference/`에 있는 파일을 가리킵니다. `references`는 필수이며 역할 `develop`, `review`, `final-review`를 파일 목록에 대응시킵니다(예: `{"develop": ["polish.md"]}`) |
| `tracker` | 작업 설명을 읽는 데 쓰는 Yandex Tracker(API 또는 MCP) |
| `journal` | 작업 저널을 쓸 위치: `[{"kind": "markdown", "dir": "...", "page": "{key}/Friday.md"}, {"kind": "jsonl", "dir": "..."}]`. `page`는 `dir` 안의 작업 페이지 경로(기본값은 `{key}.md`)이며, 예를 들어 Obsidian의 작업 폴더로 지정할 수 있습니다. `journal_dir`과 `journal_page`는 markdown 저널 하나를 위한 짧은 형태이며, 프로필에서 설정한(또는 상속된) `journal`이 이들보다 우선합니다. Friday는 자신이 쓰지 않은 페이지(front matter에 `friday_generated`가 없는 페이지)를 건드리지 않으며, 그 페이지를 건너뛰었다고 출력하고 나머지를 씁니다 |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}`, 그리고 `github.repository` 키 또는 `gitlab.base_url` + `gitlab.project` |

저장소 이름은 `origin`의 URL(HTTPS, SSH 또는 로컬 경로)에서 `.git`을 뺀 것을 쓰며, `origin`이 없으면 디스크에 있는 메인 저장소의 이름을 쓰는데, 연결된 워크트리에서 작업할 때도 마찬가지입니다. `A–Z`, `a–z`, `0–9`, `_`, `-` 밖의 문자는 `-`로 바뀌고, 앞뒤의 `-`는 제거됩니다. 이름이 비어 있으면 명시적인 `branch_template`이 필요합니다. 작업 키는 전체가 유지됩니다. `31`은 `octis/31`이 되고, `OCTIS-031`은 `octis/OCTIS-031`이 됩니다. 작업 카드에 이미 기록된 브랜치는 작업이 이어질 때 유지되며, 새 설정은 새 작업에 적용됩니다. 연결된 쇼케이스는 작업의 메인 저장소 이름을 씁니다.

### 모델과 역할

```json
"models": {
  "fable": {"model": "claude-fable-5-2", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
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

`executor_tiers`를 지정하면 복잡한 마일스톤은 허용된 가장 높은 등급을 쓰고, 할당량 때문에 교체할 때도 제한 안에서 필요한 등급이 유지됩니다. 허용되지 않은 실행자가 있는 오래된 역할 배정은 허용된 실행자로 교체되며, 허용되지 않았거나 카탈로그와 맞지 않는 모델의 쓰기는 거부됩니다. 등급과 이름은 프로필의 카탈로그에서 가져오며, Friday는 모델 이름으로 추측하지 않습니다.

병렬 마일스톤은 각자의 워크트리를 받고 계획 순서대로 통합됩니다. Friday는 시작 전에 선언된 경로의 겹침을, 통합 전에 실제 변경의 겹침을 확인합니다. 통합하지 못한 작업은 자기 브랜치와 워크트리에 남으며, 작업을 계속하기 전에 `parallel_recovery_required`가 그 목록을 알려 줍니다. 남은 작업을 검토한 뒤 컨트롤러는 옮기기를 마무리하거나 필요한 커밋을 따로 보관하고, 검토를 마친 레인의 워크트리와 브랜치를 일반 Git 명령으로 제거합니다. Friday는 다시 시도하려고 그런 결과를 자동으로 삭제하지 않습니다.

저장된 최종 리뷰는 깨끗한 워크트리, 두 저장소의 커밋, 계획, 결정, 모델 설정, 역할 입력의 정확한 텍스트에 묶입니다. 검사 중에 입력이 바뀌면 `stale_evidence`를 반환합니다. 두 번째 역할이 실패해도 끝난 첫 번째 결과는 유지되며, 다음 `resume`은 같은 입력에 대해 빠진 검사만 실행합니다. `budget`을 올리거나 `max_parallel_milestones`를 바꿔도 끝난 리뷰는 유지됩니다. 이 설정들은 검사의 내용이 아니라 허용과 스케줄링을 제어하기 때문입니다.

## 저널

작업 카드가 유일한 기준 정보입니다. 저널은 카드를 보여 주며, 계획이 준비되고 승인되었을 때, 마일스톤이 완료되거나 실패했을 때, 오너가 결정을 내렸을 때, 최종 리뷰, MR, 중지와 같은 주요 이벤트 후에 스스로 갱신됩니다. 직접 갱신하려면 `friday journal KEY`를 실행합니다.

시간은 병렬 실행을 포함한 각 실행 시간의 합이며, 달력상의 시간이 아닙니다. 제공자가 실행 시간을 주지 않는 새 실행은 러너가 직접 측정합니다. 러너가 시작되기 전의 중단은 실행 시간 0으로 기록되고, 측정하지 못한 새 실행은 시간을 알 수 없는 상태로 남습니다. 연결된 오래된 이벤트는 슬롯을 기다린 시간이 포함될 수 있는, 명시적으로 표시된 추정치를 줍니다. 측정 없이 유실된 실행의 시간은 알 수 없습니다. 알 수 없는 가격도 역할별로 표시되며, 비용 0이 되지 않습니다.

- `markdown`: `KEY.md` 페이지와 모든 작업을 나열하는 공용 개요 페이지입니다. Obsidian에서는 볼트 폴더를 지정합니다. 페이지에는 front matter(키, 상태, 비용, 태그 `friday`)가 있고 작업끼리 위키 링크로 연결됩니다. 작업 페이지에는 가정과 해결되지 않은 지적이 포함된 계획, 실행자 요약과 커밋이 포함된 마일스톤, 이견이 있는 지적, 결정, 역할별 비용, 타임라인이 담깁니다.
- `jsonl`: 스프레드시트나 대시보드에서 분석할 수 있도록 작업의 모든 이벤트를 담습니다.

템플릿은 `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}` 치환을 쓰는 Markdown입니다. Friday는 대상의 `template` 필드에 지정된 템플릿을 먼저 쓰고, 없으면 프로젝트 저장소의 `.friday/task.md`, 그다음 내장 템플릿([friday/templates](../../friday/templates))을 씁니다. `<!-- friday:notes -->`와 `<!-- /friday:notes -->` 사이의 텍스트는 오너의 몫이며 갱신할 때마다 그대로 유지됩니다. Friday는 자신이 쓰지 않은 페이지를 덮어쓰지 않습니다.

## 머신 설정

`FRIDAY_HOME/machine.json`은 모든 작업이 공유하는 자원을 정합니다.

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

기본적으로 게이트는 하나, 브라우저는 두 개가 동시에 실행되며 벤더에는 제한이 없습니다. 벤더 할당량은 자동으로 추적됩니다. 한도에 걸린 실행은 할당량이 돌아오는 시각을 기록하고, 그때까지는 모델을 고를 때 해당 벤더를 건너뜁니다.

### 작업 큐: `friday serve`

`friday serve`는 머신당 하나, 승인된 작업의 큐를 가진 장기 실행 프로세스입니다([ADR 0014](../adr/0014-work-pool.md)). 이 프로세스가 실행 중이면 `approve`, `resume`, 실행 세션에 대해 `decide`로 준 답은 작업을 직접 시작하지 않고 큐에 넣습니다. 이 프로세스가 없으면 작업은 예전처럼 별도의 백그라운드 프로세스로 실행되고, `resume`은 터미널에서 실행됩니다.

- 동시에 실행되는 작업은 최대 `max_workers`개입니다(`machine.json`, 기본값 2). 큐 밖에서 시작된 작업도 포함됩니다.
- 큐는 계획이 승인된 시각 순서를 따르며, `decide` 후에 이어지는 작업이 먼저 갑니다. `friday status`는 큐와 서버의 상태를 보여 주고, `friday stop`은 작업을 큐에서 제거합니다.
- "실행 중"인데 프로세스가 죽은 작업(절전, 재시작)은 서버가 `resume`으로 큐에 다시 넣습니다.
- 작업이 질문이나 오너 단계에서 멈추거나, 실패하거나, 끝나면 시스템 알림이 옵니다(macOS에서는 `osascript`, Linux에서는 `notify-send`).

서버에게 작업은 하나의 잡입니다. 마일스톤은 잡으로 나뉘지 않으며, 작업별 한도는 아직 없습니다.

`friday service print|install|uninstall`은 서버를 사용자 서비스로 설치합니다. launchd 에이전트(`~/Library/LaunchAgents/local.friday.serve.plist`) 또는 systemd 유닛입니다. 서비스는 빈 환경으로 시작하므로, 서비스 파일이 필요한 것을 모두 직접 지정합니다. `claude`, `codex`, `docker`, `git`(있으면 `glab`, `gh`, `uvx`, `node`, `npx`)의 디렉터리로 만든 `PATH`, `FRIDAY_HOME`, `HOME`입니다. `friday service check --profile P`는 정확히 그 환경에서 `friday doctor`를 실행하므로, 설치하기 전에 서비스가 찾지 못할 것을 알 수 있습니다. 서비스가 Claude 구독 로그인을 찾는지는 그 아래에서 처음 실행되는 작업만 보여 줍니다. 서비스는 `friday config language`로 저장한 언어를 사용합니다.

## 경계

- **실행자 샌드박스.** 실행자의 셸(Claude Code)은 샌드박스에서 실행됩니다. 네트워크가 없고, `~/.ssh`, `~/.aws`, `~/.config` 등은 읽을 수 없으며, 워크트리와 임시 폴더에만 쓸 수 있고, 샌드박스 밖에서는 실행할 수 없습니다. `executor_commands`에 없는 명령, git 명령(`add`, `commit`, `status`, `diff`, `log`, `restore`), `bin/friday-precedent`가 아닌 명령은 거부됩니다. Codex는 같은 경계를 가진 자체 샌드박스에서 실행됩니다.
- **Codex의 Git.** Friday는 설치된 Git과 Git 메타데이터의 별도 사본을 작업의 런타임에 넣습니다. 전역 Git 설정과 사용자 셸 프로필은 로드되지 않으며, 원본 저장소의 서비스 데이터는 에이전트에 노출되지 않습니다. 실행자는 일반 Git 명령으로 단계를 저장합니다. 프로세스가 끝나면 Friday는 검증된 번들로 커밋을 가져오고, HEAD와 인덱스가 바뀌지 않았다면 원본 브랜치로 되돌립니다. 상태가 충돌하면 번들은 `git-recovery.bundle`로 실행 로그에 남으며, 자동으로 덮어쓰는 것은 없습니다. 리뷰어는 Git 메타데이터를 읽기 전용으로 받습니다. `doctor`는 모델을 호출하지 않고 연결된 워크트리에서 커밋 세 개를 만드는 실제 시나리오와, 시크릿 읽기 금지와 리뷰어의 쓰기 금지가 지켜지는지 확인합니다.
- **리뷰어**는 읽기만 합니다. 블라인드 검사는 빈 폴더에서 실행됩니다.
- **게이트**는 네트워크가 없는 컨테이너에서, 읽기 전용으로 마운트한 커밋 스냅숏에 대해 실행됩니다.
- **브라우저**는 프리뷰 하나에 대한 Friday의 도구만 받으며(`hover`와, `evaluate_script` 없이 고정된 스크립트로 스타일을 읽는 `computed_style`, 같은 방식으로 `aria-*`, `data-*`와 몇 가지 마크업 속성을 읽는 `element_attributes` 포함, 로컬 프리뷰에서는 프리뷰 탭의 콘솔 `list_console_messages`도 포함), 인증 우회와 인증된 요청의 헤더 읽기는 금지됩니다.
- **Figma**는 플래너와 계획 리뷰어만 읽으며, 로컬 Figma 데스크톱 서버의 읽기 도구로만 읽습니다. /impeccable 스킬은 역할 안에서 실행되지 않으며, Friday가 그 텍스트를 프롬프트에 넣습니다.
- **게시.** Friday는 자기 작업의 브랜치만 푸시하고 초안 MR/PR만 엽니다. 다른 사람의 브랜치를 덮어쓰거나, 머지하거나, 배포하지 않습니다.
- **아직 실제 환경에서 검증하지 않음:** 브라우저 인수 확인과 GitLab 게시는 가짜(fake)를 쓴 테스트로 검증했습니다. Codex 환경의 Git도 모델을 호출하지 않는 실제 로컬 실험으로 확인했으며, 모델이 역할을 실행하는 것은 작업마다 따로 확인합니다.
