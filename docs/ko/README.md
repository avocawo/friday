<!-- i18n: source=README.md sha256=b45badbf640b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — 네온 로고" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>금요일을 한 걸음 더 가까이 당겨 주는 개인용 개발 파이프라인.</strong><br>
작업에서 초안 MR까지, 계획과 리뷰는 강력한 모델이 맡고 코드는 저렴한 모델이 작성합니다.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · 초기 버전</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · **한국어** · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · [Deutsch](../de/README.md) · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

_이 문서는 영어판 문서를 번역한 것입니다. 내용이 다를 경우 영어판이 우선합니다._

---

```text
작업 → 계획 (강력한 모델) → 계획 리뷰 (다른 벤더) → 오너의 승인
     → 마일스톤: 샌드박스 안의 실행자 → 게이트 → 마일스톤 리뷰 → UI의 브라우저 인수 확인
     → 최종 리뷰 + 블라인드 검사 → 초안 MR
```

오너는 계획을 승인하고 초안 MR을 인수합니다. 파이프라인은 설정한 예산 안에서 계획을 실행하며, 예산이 소진되거나 환경에 문제가 생기면 오너에게 돌아옵니다. 리뷰에서 이견이 남은 지적은 MR과 함께 오너에게 전달됩니다.

## 핵심 특징

- **모델은 이름이 아니라 등급으로 고릅니다.** 프로필에는 모델마다 벤더와 등급(`frontier`, `strong`, `efficient`)을 적은 모델 카탈로그가 있습니다. 코드를 리뷰하는 모델은 `frontier` 등급뿐이며, 리뷰어는 작성자보다 약하지 않고, 가능하면 작성자와 다른 벤더의 모델이 맡습니다. 비싼 모델에는 작업당 한 번만 실행되도록 표시할 수 있습니다.
- **저렴한 실행자와 빠른 피드백.** 실행자는 네트워크가 차단된 샌드박스에서 린터, 타입 검사, 테스트를 직접 실행하며, 게이트는 실패한 검사만 실행자에게 돌려줍니다.
- **진행 상황이 보입니다.** `friday status`는 각 작업의 단계, 사용 중인 모델, 에이전트가 응답 없이 보낸 시간, 지금까지 쓴 비용을 보여 줍니다.
- **여러 작업을 동시에 처리합니다.** 작업마다 자체 브랜치와 워크트리가 있습니다. 동시에 실행할 수 있는 게이트, 브라우저, 같은 벤더의 실행 수는 머신 설정으로 정합니다.
- **프로젝트 이름을 딴 브랜치.** 기본적으로 `start`는 `<repository>/<task key>` 브랜치를 만듭니다. 예를 들면 `octis/31`입니다. 이름은 Git의 `origin`에서 가져오며, 프로필에서 자체 템플릿을 지정할 수 있습니다.
- **제한된 비용.** 프로필에서 `executor_tiers`로 최상위(frontier) 등급 실행자를 배제하고, `budget`으로 계획 전체의 크기와 모델 실행 횟수에 상한을 둘 수 있습니다. `resume`은 카운터를 초기화하지 않습니다. 최종 리뷰는 역할별로 저장되므로, 입력이 바뀌지 않은 상태에서 이어서 실행하면 다시 실행되지 않습니다.
- **병렬 마일스톤.** 준비된 독립 마일스톤은 각자의 워크트리에서 실행되며, 기본값은 동시에 두 개입니다. 마일스톤들이 함께 바꾼 파일은 통합을 막습니다.

## 빠른 시작

Python 3.11+, Git, 그리고 Claude Code가 필요합니다(Codex CLI는 선택 사항). 게이트와 브라우저 인수 확인에는 Docker도 필요합니다.

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

Friday는 10개 언어를 지원합니다. `friday --lang ru …`, `FRIDAY_LANG=ru`, 또는 `friday config language ru`로 바꿀 수 있습니다. 에이전트는 항상 영어로 작업하며, 사용자가 읽는 내용은 번역됩니다. Node와 Python 프로젝트용 예시 프로필은 [examples/profiles](../../examples/profiles)에 있습니다.

## 명령어

| Command | What it does |
| --- | --- |
| `start KEY --profile P [--brief F] [--no-design]` | 워크트리와 계획을 만들고, 다른 모델이 계획을 리뷰하게 합니다. 프로필에 `design`이 있으면 티켓의 Figma 프레임과 대조해 계획을 검사합니다 |
| `plan KEY [--diff]` | 해결되지 않은 지적과 함께 계획을 보여 줍니다. `--diff`는 최신 수정본에서 바뀐 내용을 보여 줍니다 |
| `approve KEY [--no-run]` | 계획을 승인하고 파이프라인을 백그라운드에서 시작합니다 |
| `status [KEY] [--json]` | 각 작업이 무엇을 하고 있는지, 비용이 얼마나 들었는지 보여 줍니다 |
| `decide KEY name "text" [--reopen M2]` | 오너의 결정을 기록합니다. 계획이 승인을 기다리는 동안에는 플래너가 그 결정에 따라 지정한 마일스톤을 수정하고(`--reopen`이 없으면 직접 고릅니다), 바뀐 마일스톤만 다시 리뷰합니다([ADR 0016](../adr/0016-decision-revises-the-plan.md)). 승인 후에는 지정한 마일스톤만 다시 엽니다. `--file PATH`는 텍스트를 파일에서 읽습니다. 분석 작업에서 `--hold`는 세션 턴 없이 답변을 보관하고, 다음 답변이 보관된 답변을 한 턴에 모두 보냅니다 |
| `reopen KEY "text"` | 완료된 분석 작업을 같은 세션에서 새 지시로 이어갑니다. 검사는 다시 실행됩니다(`--file PATH`는 텍스트를 파일에서 읽습니다) |
| `run KEY` / `resume KEY [--brief F]` / `stop KEY` | 이 터미널에서 실행, 이어서 실행, 중지. `--brief`는 계획 지문(fingerprint)으로 오래된 작업의 원래 설명을 복원합니다 |
| `serve` / `service print\|install\|uninstall\|check` | 머신의 작업 큐: 작업을 차례로 시작하고, 실패한 작업을 다시 큐에 넣고, 알림을 보냅니다. launchd 또는 systemd 서비스로 실행됩니다([가이드](guide.md#작업-큐-friday-serve)) |
| `step [NAME] [KEY] [--profile P] [--dry-run]` | 프로필 단계(`steps`)를 실행합니다. 작업 단계는 작업 키와 함께, 프로필 단계는 `--profile`과 함께 실행하며, 이름이 없으면 단계와 상태의 목록을 보여 줍니다. 단계가 하는 일(이미지나 번들 빌드 등)은 프로필에 기술합니다([ADR 0011](../adr/0011-profile-steps.md)) |
| `archive KEY [--force]` | 워크트리와 카드를 삭제합니다. 브랜치는 남습니다 |
| `journal KEY` | 작업 저널(Obsidian, jsonl)을 갱신합니다 |
| `doctor --profile P` | 머신과 프로필이 준비되었는지 확인합니다 |
| `config language L` | 이 머신에서 Friday가 사용할 언어를 설정합니다 |

프로필, 모델, 경계에 대한 자세한 내용은 [가이드](guide.md)를 참고하세요. 파이프라인을 이렇게 설계한 이유는 [파일럿 분석](../process-redesign.md)(러시아어)과 [ADR](../adr/)(0001–0006은 러시아어)에 있습니다.

## 개발

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

테스트는 임시 데이터와 가짜 에이전트를 사용하며, 네트워크도 Docker도 필요 없습니다. 코드가 어떻게 구성되어 있는지는 [docs/architecture.md](../architecture.md), 변경을 제안하는 방법은 [CONTRIBUTING.md](../../CONTRIBUTING.md)에 있습니다.

[가이드](guide.md) · [용어집](GLOSSARY.md) · [기여하기](../../CONTRIBUTING.md) · [라이선스](../../LICENSE)
