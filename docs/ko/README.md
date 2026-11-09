<!-- i18n: source=README.md sha256=9ddeae110640 -->
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

오너가 개입하는 것은 두 번뿐입니다. 계획을 승인할 때와 초안 MR을 인수할 때입니다. 이 두 시점 사이에서 파이프라인은 멈추지 않습니다. 리뷰에서 이견이 남은 지적은 MR과 함께 오너에게 전달됩니다.

## 핵심 특징

- **모델은 이름이 아니라 등급으로 고릅니다.** 프로필에는 모델마다 벤더와 등급(`frontier`, `strong`, `efficient`)을 적은 모델 카탈로그가 있습니다. 코드를 리뷰하는 모델은 `frontier` 등급뿐이며, 리뷰어는 작성자보다 약하지 않고, 가능하면 작성자와 다른 벤더의 모델이 맡습니다. 비싼 모델에는 작업당 한 번만 실행되도록 표시할 수 있습니다.
- **저렴한 실행자와 빠른 피드백.** 실행자는 네트워크가 차단된 샌드박스에서 린터, 타입 검사, 테스트를 직접 실행하며, 게이트는 실패한 검사만 실행자에게 돌려줍니다.
- **진행 상황이 보입니다.** `friday status`는 각 작업의 단계, 사용 중인 모델, 에이전트가 응답 없이 보낸 시간, 지금까지 쓴 비용을 보여 줍니다.
- **여러 작업을 동시에 처리합니다.** 작업마다 자체 브랜치와 워크트리가 있습니다. 동시에 실행할 수 있는 게이트, 브라우저, 같은 벤더의 실행 수는 머신 설정으로 정합니다.

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
| `start KEY --profile P [--brief F]` | 워크트리와 계획을 만들고, 다른 모델이 계획을 리뷰하게 합니다 |
| `plan KEY` | 해결되지 않은 지적과 함께 계획을 보여 줍니다 |
| `approve KEY [--no-run]` | 계획을 승인하고 파이프라인을 백그라운드에서 시작합니다 |
| `status [KEY] [--json]` | 각 작업이 무엇을 하고 있는지, 비용이 얼마나 들었는지 보여 줍니다 |
| `decide KEY name "text" [--reopen M2]` | 오너의 결정을 기록합니다. 지정한 마일스톤만 다시 엽니다 |
| `replan KEY [--brief F]` | 승인을 기다리는 계획을 새 결정과 함께 플래너에게 돌려보냅니다 |
| `run KEY` / `resume KEY` / `stop KEY` | 이 터미널에서 실행, 이어서 실행, 중지 |
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
