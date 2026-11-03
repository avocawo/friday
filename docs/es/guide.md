<!-- i18n: source=docs/guide.md sha256=9f95975c05a4 -->
# Guía de Friday

[English](../guide.md) · [Русский](../ru/guide.md) · [简体中文](../zh-cn/guide.md) · [日本語](../ja/guide.md) · [한국어](../ko/guide.md) · [Português (Brasil)](../pt-br/guide.md) · **Español** · [Deutsch](../de/guide.md) · [Français](../fr/guide.md) · [Bahasa Indonesia](../id/guide.md)

_Esta es una traducción de la documentación en inglés; si hay diferencias, prevalece la versión en inglés._

## Instalación

1. Clona Friday y añade `bin` al `PATH`, o define `FRIDAY_BIN`.
2. Instala las CLI que necesitan los agentes: Claude Code (`claude`) para los modelos de Anthropic y Z.ai, Codex CLI (`codex`) para los modelos de OpenAI. Inicia sesión en ellas como de costumbre.
3. Para el gate y la aceptación en el navegador, instala Docker. La aceptación en el navegador además necesita Chrome con el puerto de depuración 9333 y una sesión iniciada en el entorno de pruebas.
4. El estado se guarda en `~/.local/share/friday` (se cambia con `FRIDAY_HOME`). Los perfiles y los secretos no entran en el repositorio: la carpeta `profiles/` está en `.gitignore`.

### Plugin para Codex y Claude Code

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

El plugin enseña al agente a leer `status` y `plan`. Iniciar una tarea, aprobarla y tomar decisiones queda en tus manos.

## Cómo se ejecuta una tarea

1. **`start`** crea la rama `friday/KEY` a partir de un `origin/<base_branch>` recién actualizado y un worktree, fija la asignación de roles y escribe el plan: hitos de 2–5 etapas, archivos, una estimación de tamaño, criterios de código y de navegador, y un resumen de las reglas del proyecto para el ejecutor. Primero el plan se comprueba sin modelo (tamaño, archivos, orden, criterios). Después lo revisa un modelo de otro proveedor, y el planificador responde a los hallazgos una sola vez: acepta cada uno o lo rechaza con un motivo.
2. **`plan`** muestra el plan, las preguntas, los hallazgos rechazados y las coincidencias de archivos con otras tareas activas.
3. **`approve`** aprueba el plan e inicia el pipeline en segundo plano (`--no-run` solo aprueba; ejecútalo más tarde con `run`).
4. **Hito.** El ejecutor trabaja en una sesión por hito y hace un commit por cada etapa. El gate se ejecuta sobre el commit. Si falla, el ejecutor recibe solo las secciones fallidas del log. La revisión del hito examina el diff de este hito junto con el resultado del gate. El ejecutor corrige o discute los hallazgos bloqueantes; los hallazgos en disputa se te envían a ti. Los hitos con interfaz pasan por la aceptación en el navegador en un preview aislado. Tras dos fallos seguidos, un modelo de nivel superior toma el hito en una sesión nueva.
5. **Final.** La revisión final y la comprobación a ciegas de todo el diff se ejecutan en paralelo. Los hallazgos bloqueantes tienen una ronda de correcciones.
6. **El MR en borrador** se abre si el perfil define `delivery`. En caso contrario, su texto se guarda en `merge-request.md` en la carpeta de la tarea.

El pipeline se detiene solo cuando el gate nunca pasó, el ejecutor no hizo nada, el entorno no está disponible o se agotaron los modelos que permiten las reglas. Los desacuerdos en la revisión no lo detienen.

`decide KEY name "text" --reopen M2` registra tu decisión. Solo se reabren los hitos indicados y los que dependen de ellos. Sin `--reopen`, los hitos siguientes tienen en cuenta la decisión. `resume` continúa una tarea detenida o fallida. `stop` detiene una tarea en ejecución: los agentes terminan junto con sus procesos hijos.

## Perfil

Un perfil es un archivo JSON. La clave `extends` incluye otro perfil (por ejemplo, un catálogo de modelos compartido); las rutas son relativas al archivo del perfil.

| Clave | Para qué sirve |
| --- | --- |
| `name`, `repository` | El nombre del proyecto y la ruta a su repositorio |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | De dónde crear la rama de la tarea y dónde colocarla; la plantilla por defecto es `friday/{key}`, el layout es `key` o `branch` |
| `models`, `roles` | El catálogo de modelos y las preferencias de roles (véase abajo) |
| `engineering_policy` | Reglas para el planificador y las revisiones |
| `rules_docs` | Documentos con las reglas del proyecto. Friday elabora con ellos un único resumen para el planificador y el revisor del plan (por defecto `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). El resumen se guarda por hash del contenido y lo reutilizan las tareas mientras los documentos no cambien; el planificador no lee los documentos completos |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | Un mapa del repositorio en el prompt del planificador y del revisor del plan, para que no exploren la estructura con llamadas a herramientas. `none` (por defecto): sin mapa. `builtin`: los archivos rastreados con su número de líneas. `graphify`: además los símbolos de cada archivo y cuántos archivos lo importan, tomados de un grafo de [graphify](https://github.com/Graphify-Labs/graphify) (`uv tool install 'graphifyy[mcp]==0.9.100'`; solo analiza código, no usa ningún modelo; el grafo se guarda en `FRIDAY_HOME/maps`, no en el repositorio). En una medición en CS-2 el mapa no abarató la planificación (sin mapa $6.19, `builtin` $7.08, `graphify` $7.97, una ejecución cada uno), por eso está desactivado por defecto. El mapa se guarda en caché por commit. Si supera los `repo_map_budget` caracteres (60000), primero se acorta quitando los símbolos y después colapsando carpetas. `repo_map_mcp: true` (solo con `graphify`) da a ambos roles consultas al grafo: `query_graph`, `get_node`, `get_neighbors`, `shortest_path`; no verificado en vivo con Codex. El revisor del plan recibe el texto actual de los archivos mencionados en el plan (hasta 40000 caracteres) |
| `approval` | `owner` (por defecto): el plan espera a `approve`. `auto`: el plan se aprueba automáticamente tras la revisión y el pipeline se inicia de inmediato; el diario registra que el plan se aprobó automáticamente |
| `limits` | `stage_files` y `stage_lines`: los límites de una etapa (10 y 500) |
| `worktree_setup` | Comandos que Friday ejecuta en un worktree nuevo antes de planificar (dependencias y similares): arrays de argumentos con las sustituciones `{repository}` y `{worktree}`; deben poder repetirse. Ejemplo para npm en macOS: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | Comandos que el ejecutor puede ejecutar por sí mismo, en la forma exacta: `"python3 -m pytest"`, no `python` |
| `executor_deny_read` | Rutas adicionales que el shell del ejecutor no puede leer |
| `milestone_rounds` | Cuántas rondas del ejecutor recibe un hito (4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | El comando de comprobación en Docker; `git-diff` compara con la base de la tarea |
| `environment_errors` | Patrones de líneas del log que indican un fallo del entorno, no del código |
| `browser`, `preview`, `behavior_test_policy` | El entorno de pruebas, el preview y las reglas de la aceptación en el navegador |
| `tracker` | Yandex Tracker (API o MCP), se usa para leer la descripción de la tarea |
| `journal` | Dónde escribir el diario de la tarea: `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`; `journal_dir` es una forma corta para un único diario markdown |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}` y la clave `github.repository` o `gitlab.base_url` + `gitlab.project` |

### Modelos y roles

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`: qué ejecuta el modelo, `claude` o `codex`. Z.ai pasa por Claude Code; el token se lee de un archivo al que solo puede acceder el propietario.
- `tier`: `frontier`, `strong` o `efficient`; `strength` ordena los modelos dentro de un nivel.
- `effort`: la profundidad del razonamiento (`low` … `max`); un mismo modelo se puede describir varias veces con distinto esfuerzo.
- `scarce`: un modelo caro; se usa una vez por tarea si las reglas permiten que la tarea siga sin él.
- `price`: el precio por millón de tokens según el tipo (`input`, `output`, `cache_read`, `cache_write`), que se usa para estimar el gasto cuando el proveedor no informa de un precio.

Roles: `plan`, `plan-review`, `develop`, `review`, `final-review`, `blind-code`, `browser`. Las listas de `roles` son preferencias: si la lista no tiene un modelo adecuado, Friday toma otro del catálogo. Reglas estrictas: solo los modelos `frontier` revisan código, el revisor no es más débil que el autor del trabajo y la comprobación a ciegas no la hace el autor del código. Reglas flexibles, por orden: no el mismo modelo que el autor; un modelo caro una vez; un proveedor distinto; la comprobación a ciegas no por el autor del plan. Una regla flexible solo se puede incumplir cuando no hay otra salida, y la infracción queda registrada en el historial de la tarea.

Hay un ejemplo de asignación de roles en [examples/models.json](../../examples/models.json).

## Diario

La ficha de la tarea es la única fuente de verdad. Los diarios la reflejan y se actualizan solos tras los eventos importantes: el plan está listo y aprobado, un hito se completa o falla, una decisión del propietario, la revisión final, el MR, una detención. Para actualizarlos a mano, ejecuta `friday journal KEY`.

- `markdown`: una página `KEY.md` y una página de resumen compartida que lista todas las tareas. Para Obsidian, indica la carpeta del vault: las páginas tienen front matter (clave, estado, gasto, la etiqueta `friday`) y las tareas se enlazan con wiki links. Una página de tarea contiene el plan con sus supuestos y hallazgos sin resolver, los hitos con el resumen del ejecutor y los commits, los hallazgos en disputa, las decisiones, el gasto por rol y la línea de tiempo.
- `jsonl`: todos los eventos de la tarea, para analizarlos en hojas de cálculo o paneles.

Las plantillas son Markdown con las sustituciones `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}`. Friday toma la plantilla del campo `template` del destino, luego `.friday/task.md` en el repositorio del proyecto y, por último, la integrada ([friday/templates](../../friday/templates)). El texto entre `<!-- friday:notes -->` y `<!-- /friday:notes -->` es tuyo: se conserva en cada actualización. Friday no sobrescribe una página que no escribió él.

## Configuración de la máquina

`FRIDAY_HOME/machine.json` define los recursos compartidos por todas las tareas:

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

Por defecto se ejecutan a la vez un gate y dos navegadores, y los proveedores no tienen límite. Las cuotas de los proveedores se siguen automáticamente: una ejecución que alcanza un límite registra cuándo se renueva la cuota y, hasta entonces, el proveedor se omite al elegir modelos.

## Límites

- **Sandbox del ejecutor.** El shell del ejecutor (Claude Code) se ejecuta en un sandbox: sin red, sin lectura de `~/.ssh`, `~/.aws`, `~/.config` y similares, con escritura solo en el worktree y en una carpeta temporal, y sin ejecución fuera del sandbox. Los comandos que no están en `executor_commands` y no son comandos de git se rechazan. Codex se ejecuta en su propio sandbox con los mismos límites.
- **Los revisores** solo leen. La comprobación a ciegas se ejecuta en una carpeta vacía.
- **El gate** se ejecuta en un contenedor sin acceso a la red, sobre una instantánea del commit montada en solo lectura.
- **El navegador** recibe solo las herramientas de Friday para un preview; está prohibido eludir la autorización y leer las cabeceras de las solicitudes autorizadas.
- **Publicación.** Friday solo sube la rama de su propia tarea y solo abre un MR/PR en borrador. No sobrescribe la rama de otra persona, no hace merge ni despliega.
- **Aún no verificado en vivo:** los roles en Codex, la aceptación en el navegador y la publicación en GitLab solo se han verificado con pruebas con simulaciones.
