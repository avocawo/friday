<!-- i18n: source=README.md sha256=9ddeae110640 -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — неоновый логотип" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Личный конвейер разработки, который приближает пятницу.</strong><br>
От задачи до чернового MR: сильные модели планируют и проверяют, недорогие пишут код.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · ранняя версия</p>

[English](../../README.md) · **Русский** · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · [Deutsch](../de/README.md) · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

_Это перевод английской документации; при расхождениях действует английская версия._

---

```text
задача → план (сильная модель) → проверка плана (другой вендор) → ваше утверждение
       → вехи: исполнитель в песочнице → гейт → ревью вехи → браузерная приёмка UI
       → финальное ревью + слепая проверка → черновой MR
```

Вы участвуете дважды: утверждаете план и принимаете черновой MR. Между этими точками конвейер не останавливается: спорные замечания ревью уходят вам вместе с MR.

## Что важно

- **Модели по уровням, а не по именам.** В профиле — каталог моделей с вендором и уровнем (`frontier`, `strong`, `efficient`) для каждой модели. Код смотрят только `frontier`-модели, ревьюер не бывает слабее автора, по возможности он другого вендора. Дорогую модель можно пометить, чтобы она шла один раз на задачу.
- **Дешёвый исполнитель с быстрой обратной связью.** Исполнитель сам запускает линтер, типы и тесты в песочнице без сети, а гейт отдаёт ему только упавшие проверки.
- **Видно, что происходит.** `friday status` показывает фазу, модель, сколько агент молчит и сколько потрачено на каждую задачу.
- **Несколько задач сразу.** Каждая задача — своя ветка и рабочая копия. Сколько гейтов, браузеров и запусков одного вендора идёт одновременно, задаётся настройками машины.

## Быстрый старт

Нужны Python 3.11+, Git и Claude Code (Codex CLI — по желанию). Для гейта и браузерной приёмки нужен Docker.

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

Friday говорит на десяти языках: `friday --lang ru …`, `FRIDAY_LANG=ru` или `friday config language ru`. Агенты всегда работают по-английски; переводится то, что читаете вы. Примеры профилей для проектов на Node и Python лежат в [examples/profiles](../../examples/profiles).

## Команды

| Команда | Что делает |
| --- | --- |
| `start KEY --profile P [--brief F]` | Создаёт рабочую копию и план, проверку плана поручает другой модели |
| `plan KEY` | Показывает план с несогласованными замечаниями |
| `approve KEY [--no-run]` | Утверждает план и запускает конвейер в фоне |
| `status [KEY] [--json]` | Показывает, что делают задачи и сколько потрачено |
| `decide KEY name "text" [--reopen M2]` | Записывает решение владельца; переоткрывает только названные вехи |
| `replan KEY [--brief F]` | Возвращает планировщику план, ждущий одобрения, с вашими новыми решениями |
| `run KEY` / `resume KEY` / `stop KEY` | Исполняет в этом терминале, продолжает, останавливает |
| `archive KEY [--force]` | Убирает рабочую копию и карточку; ветка остаётся |
| `journal KEY` | Обновляет журналы задачи (Obsidian, jsonl) |
| `doctor --profile P` | Проверяет, что машина и профиль готовы |
| `config language L` | Задаёт язык, на котором Friday говорит на этой машине |

Подробно о профиле, моделях и границах — в [руководстве](guide.md). Почему конвейер устроен так — в [разборе пилота](../process-redesign.md) и [ADR](../adr/) (0001–0006 на русском).

## Разработка

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

Тесты работают с временными данными и фейковыми агентами, без сети и Docker. Как устроен код — в [docs/architecture.md](../architecture.md); как предложить изменение — в [CONTRIBUTING.md](../../CONTRIBUTING.md).

[Руководство](guide.md) · [Глоссарий](GLOSSARY.md) · [Участие в разработке](../../CONTRIBUTING.md) · [Лицензия](../../LICENSE)
