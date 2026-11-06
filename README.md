<p align="center">
  <img src="assets/friday-logo.png" alt="Friday — неоновый логотип" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Личный конвейер разработки, который приближает пятницу.</strong><br>
От задачи до чернового MR: сильные модели планируют и проверяют, недорогие пишут код.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="LICENSE">MIT</a> · ранняя версия</p>

---

```text
задача → план (сильная модель) → проверка плана (другой вендор) → ваше утверждение
       → вехи: исполнитель в песочнице → гейт → ревью вехи → браузерная приёмка UI
       → финальное ревью + слепая проверка → черновой MR
```

Вы участвуете дважды: утверждаете план и принимаете черновой MR. Между этими точками конвейер не останавливается: спорные замечания ревью уходят вам вместе с MR.

## Что важно

- **Модели по уровням, а не по именам.** В профиле — каталог моделей с вендором и уровнем (`frontier`, `strong`, `efficient`). Код смотрят только `frontier`-модели, ревьюер не бывает слабее автора, по возможности он другого вендора. Дорогую модель можно пометить, чтобы она шла один раз на задачу.
- **Дешёвый исполнитель с быстрой обратной связью.** Исполнитель сам запускает линтер, типы и тесты в песочнице без сети, а гейт отдаёт ему только упавшие проверки.
- **Видно, что происходит.** `friday status` показывает фазу, модель, сколько агент молчит и сколько потрачено на каждую задачу.
- **Несколько задач сразу.** Каждая задача — своя ветка и рабочая копия. Сколько гейтов, браузеров и запусков одного вендора идёт одновременно, задаётся настройками машины.

## Быстрый старт

Нужны Python 3.11+, Git и Claude Code (Codex CLI — по желанию). Для гейта и браузерной приёмки нужен Docker.

```sh
uv tool install git+https://github.com/avocawo/friday   # или: pipx install git+https://github.com/avocawo/friday
mkdir -p ~/.config/friday/profiles && cd ~/.config/friday/profiles
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/models.json
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/project-profile.json
# в project-profile.json укажите repository, worktree_root и executor_commands своего проекта
export FRIDAY_PROFILE=~/.config/friday/profiles/project-profile.json
friday doctor
friday start TASK-1 --brief task.md
friday approve TASK-1
friday status
```

Язык вывода: `friday --lang ru …`, `FRIDAY_LANG=ru` или `friday config language ru`. Примеры профилей для Node и Python — в [examples/profiles](examples/profiles).

## Команды

| Команда | Что делает |
| --- | --- |
| `start KEY --profile P [--brief F]` | Рабочая копия, план, проверка плана другой моделью |
| `plan KEY` | Показать план с несогласованными замечаниями |
| `approve KEY [--no-run]` | Утвердить план и запустить конвейер в фоне |
| `status [KEY] [--json]` | Что делают задачи и сколько потрачено |
| `decide KEY name "текст" [--reopen M2]` | Решение владельца; переоткрывает только названные вехи |
| `run KEY` / `resume KEY` / `stop KEY` | Исполнить в этом терминале, продолжить, остановить |
| `archive KEY [--force]` | Убрать рабочую копию и карточку; ветка остаётся |
| `journal KEY` | Обновить журналы задачи (Obsidian, jsonl) |
| `doctor --profile P` | Готовность машины и профиля |
| `config language L` | Язык вывода на этой машине |

Подробно о профиле, моделях и границах — в [руководстве](docs/guide.md). Почему конвейер устроен так — в [разборе пилота](docs/process-redesign.md) и [ADR](docs/adr/).

## Разработка

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

Тесты работают с временными данными и фейковыми агентами, без сети и Docker. Как устроен код — в [архитектуре](docs/architecture.md), как предлагать изменения — в [CONTRIBUTING.md](CONTRIBUTING.md).

[Руководство](docs/guide.md) · [Глоссарий](GLOSSARY.md) · [Участие в разработке](CONTRIBUTING.md) · [Лицензия](LICENSE)
