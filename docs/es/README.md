<!-- i18n: source=README.md sha256=a008f1f30688 -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — logotipo de neón" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Un pipeline de desarrollo personal que acerca el viernes.</strong><br>
De una tarea a un MR en borrador: los modelos potentes planifican y revisan, los modelos económicos escriben el código.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · versión temprana</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · **Español** · [Deutsch](../de/README.md) · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

_Esta es una traducción de la documentación en inglés; si hay diferencias, prevalece la versión en inglés._

---

```text
tarea → plan (modelo potente) → revisión del plan (otro proveedor) → tu aprobación
      → hitos: ejecutor en un sandbox → gate → revisión del hito → aceptación de la UI en el navegador
      → revisión final + comprobación a ciegas → MR en borrador
```

Participas dos veces: apruebas el plan y aceptas el MR en borrador. Entre estos dos puntos el pipeline no se detiene: los hallazgos en disputa de las revisiones se te envían junto con el MR.

## Puntos clave

- **Modelos por nivel, no por nombre.** El perfil contiene un catálogo de modelos con un proveedor y un nivel (`frontier`, `strong`, `efficient`) para cada uno. Solo los modelos `frontier` revisan código, el revisor nunca es más débil que el autor y, siempre que es posible, el revisor es de otro proveedor. Un modelo caro se puede marcar para que se ejecute una sola vez por tarea.
- **Un ejecutor económico con retroalimentación rápida.** El ejecutor ejecuta por sí mismo el linter, las comprobaciones de tipos y las pruebas en un sandbox sin acceso a la red, y el gate solo le entrega las comprobaciones que fallaron.
- **Ves lo que ocurre.** `friday status` muestra la fase, el modelo, cuánto tiempo lleva en silencio el agente y cuánto se ha gastado en cada tarea.
- **Varias tareas a la vez.** Cada tarea tiene su propia rama y su propio worktree. La configuración de la máquina define cuántos gates, navegadores y ejecuciones de un mismo proveedor pueden correr al mismo tiempo.

## Inicio rápido

Necesitas Python 3.11+, Git y Claude Code (Codex CLI es opcional). El gate y la aceptación en el navegador además necesitan Docker.

```sh
uv tool install git+https://github.com/avocawo/friday   # or: pipx install git+https://github.com/avocawo/friday
mkdir -p ~/.config/friday/profiles && cd ~/.config/friday/profiles
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/models.json
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/project-profile.json
# en project-profile.json, indica el repository, el worktree_root y los executor_commands de tu proyecto
export FRIDAY_PROFILE=~/.config/friday/profiles/project-profile.json
friday doctor
friday start TASK-1 --brief task.md
friday approve TASK-1
friday status
```

Friday habla diez idiomas: `friday --lang ru …`, `FRIDAY_LANG=ru` o `friday config language ru`. Los agentes siempre trabajan en inglés; lo que lees se traduce. Los perfiles de ejemplo para proyectos de Node y Python están en [examples/profiles](../../examples/profiles).

## Comandos

| Comando | Qué hace |
| --- | --- |
| `start KEY --profile P [--brief F]` | Crea el worktree y el plan, y hace que otro modelo revise el plan |
| `plan KEY` | Muestra el plan con los hallazgos sin resolver |
| `approve KEY [--no-run]` | Aprueba el plan e inicia el pipeline en segundo plano |
| `status [KEY] [--json]` | Muestra qué hacen las tareas y cuánto se ha gastado |
| `decide KEY name "text" [--reopen M2]` | Registra una decisión del propietario; reabre solo los hitos indicados |
| `run KEY` / `resume KEY` / `stop KEY` | Ejecuta en esta terminal, continúa, detiene |
| `archive KEY [--force]` | Elimina el worktree y la ficha; la rama se conserva |
| `journal KEY` | Actualiza los diarios de las tareas (Obsidian, jsonl) |
| `doctor --profile P` | Comprueba que la máquina y el perfil están listos |
| `config language L` | Establece el idioma en el que habla Friday en esta máquina |

Para los detalles sobre el perfil, los modelos y los límites, consulta la [guía](guide.md). Para saber por qué el pipeline está construido así, consulta el [análisis del piloto](../process-redesign.md) (en ruso) y los [ADR](../adr/) (0001–0006 en ruso).

## Desarrollo

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

Las pruebas usan datos temporales y agentes simulados, sin red ni Docker. Cómo está organizado el código se explica en [docs/architecture.md](../architecture.md); cómo proponer un cambio, en [CONTRIBUTING.md](../../CONTRIBUTING.md).

[Guía](guide.md) · [Glosario](GLOSSARY.md) · [Contribuir](../../CONTRIBUTING.md) · [Licencia](../../LICENSE)
