<!-- i18n: source=docs/guide.md sha256=9f95975c05a4 -->
# Friday-Guide

[English](../guide.md) · [Русский](../ru/guide.md) · [简体中文](../zh-cn/guide.md) · [日本語](../ja/guide.md) · [한국어](../ko/guide.md) · [Português (Brasil)](../pt-br/guide.md) · [Español](../es/guide.md) · **Deutsch** · [Français](../fr/guide.md) · [Bahasa Indonesia](../id/guide.md)

_Dies ist eine Übersetzung der englischen Dokumentation; bei Abweichungen gilt die englische Fassung._

## Installation

1. Klone Friday und nimm `bin` in den `PATH` auf, oder setze `FRIDAY_BIN`.
2. Installiere die CLIs, die die Agenten brauchen: Claude Code (`claude`) für Modelle von Anthropic und Z.ai, die Codex CLI (`codex`) für OpenAI-Modelle. Melde dich dort wie gewohnt an.
3. Für das Gate und die Browser-Abnahme installierst du Docker. Die Browser-Abnahme braucht außerdem Chrome mit dem Debugging-Port 9333 und eine angemeldete Sitzung in der Testumgebung.
4. Der Zustand liegt in `~/.local/share/friday` (überschreibbar mit `FRIDAY_HOME`). Profile und Secrets landen nicht im Repository: Der Ordner `profiles/` steht in `.gitignore`.

### Plugin für Codex und Claude Code

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

Das Plugin bringt dem Agenten bei, `status` und `plan` zu lesen. Eine Aufgabe zu starten, sie freizugeben und Entscheidungen zu treffen, bleibt dir überlassen.

## Wie eine Aufgabe abläuft

1. **`start`** legt den Branch `friday/KEY` auf Basis eines frischen `origin/<base_branch>` sowie einen Worktree an, legt die Rollenzuweisung fest und schreibt den Plan: Meilensteine mit 2–5 Etappen, Dateien, eine Größenschätzung, Code- und Browser-Kriterien sowie eine Zusammenfassung der Projektregeln für den Executor. Zuerst wird der Plan ohne Modell geprüft (Größe, Dateien, Reihenfolge, Kriterien). Danach prüft ihn ein Modell eines anderen Anbieters, und der Planer antwortet einmal auf die Befunde: Er übernimmt jeden Befund oder lehnt ihn mit einer Begründung ab.
2. **`plan`** zeigt den Plan, die Fragen, die abgelehnten Befunde und die Dateiüberschneidungen mit anderen aktiven Aufgaben.
3. **`approve`** gibt den Plan frei und startet die Pipeline im Hintergrund (`--no-run` gibt nur frei; starte später mit `run`).
4. **Meilenstein.** Der Executor arbeitet pro Meilenstein in einer Sitzung und committet jede Etappe. Das Gate läuft auf dem Commit. Schlägt es fehl, erhält der Executor nur die fehlgeschlagenen Abschnitte des Logs. Das Meilenstein-Review betrachtet den Diff dieses Meilensteins zusammen mit dem Gate-Ergebnis. Der Executor behebt die blockierenden Befunde oder bestreitet sie; strittige Befunde gehen an dich. Meilensteine mit einer Oberfläche durchlaufen die Browser-Abnahme in einer isolierten Preview. Nach zwei Fehlschlägen in Folge übernimmt ein Modell einer höheren Stufe den Meilenstein in einer neuen Sitzung.
5. **Abschluss.** Das Abschluss-Review und die Blindprüfung des gesamten Diffs laufen parallel. Blockierende Befunde bekommen eine Korrekturrunde.
6. **Draft-MR** wird geöffnet, wenn das Profil `delivery` setzt. Andernfalls wird sein Text in `merge-request.md` im Aufgabenordner gespeichert.

Die Pipeline hält nur an, wenn das Gate nie bestanden wurde, der Executor nichts getan hat, die Umgebung nicht verfügbar ist oder die nach den Regeln erlaubten Modelle aufgebraucht sind. Meinungsverschiedenheiten im Review halten sie nicht auf.

`decide KEY name "text" --reopen M2` hält deine Entscheidung fest. Nur die genannten Meilensteine und die Meilensteine, die von ihnen abhängen, werden erneut geöffnet. Ohne `--reopen` berücksichtigen die folgenden Meilensteine die Entscheidung. `resume` setzt eine angehaltene oder fehlgeschlagene Aufgabe fort. `stop` hält eine laufende Aufgabe an: Die Agenten werden samt ihren Kindprozessen beendet.

## Profil

Ein Profil ist eine JSON-Datei. Der Schlüssel `extends` bindet ein anderes Profil ein (zum Beispiel einen gemeinsamen Modellkatalog); Pfade sind relativ zur Profildatei.

| Schlüssel | Zweck |
| --- | --- |
| `name`, `repository` | Der Name des Projekts und der Pfad zu seinem Repository |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | Wovon der Aufgaben-Branch abgezweigt wird und wo er liegt; die Standardvorlage ist `friday/{key}`, das Layout ist `key` oder `branch` |
| `models`, `roles` | Der Modellkatalog und die Rollenpräferenzen (siehe unten) |
| `engineering_policy` | Regeln für den Planer und die Reviews |
| `rules_docs` | Dokumente mit den Projektregeln. Friday erstellt daraus eine Zusammenfassung für den Planer und den Plan-Reviewer (Standard `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). Die Zusammenfassung wird per Inhalts-Hash gespeichert und von Aufgaben wiederverwendet, solange sich die Dokumente nicht ändern; der Planer liest die Dokumente nicht vollständig |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | Eine Repository-Karte im Prompt des Planers und des Plan-Reviewers, damit sie die Struktur nicht mit Tool-Aufrufen erkunden müssen. `none` (Standard): keine Karte. `builtin`: die versionierten Dateien mit ihren Zeilenzahlen. `graphify`: zusätzlich die Symbole jeder Datei und wie viele Dateien sie importieren, entnommen einem [graphify](https://github.com/Graphify-Labs/graphify)-Graphen (`uv tool install 'graphifyy[mcp]==0.9.100'`; er parst nur Code, es wird kein Modell verwendet; der Graph liegt in `FRIDAY_HOME/maps`, nicht im Repository). In einer Messung an CS-2 machte die Karte die Planung nicht günstiger (ohne Karte 6,19 $, `builtin` 7,08 $, `graphify` 7,97 $, je ein Lauf), deshalb ist sie standardmäßig aus. Die Karte wird pro Commit zwischengespeichert. Ist sie länger als `repo_map_budget` Zeichen (60000), wird sie zuerst durch Weglassen der Symbole gekürzt, dann durch Zusammenfalten von Ordnern. `repo_map_mcp: true` (nur mit `graphify`) gibt beiden Rollen Graph-Abfragen: `query_graph`, `get_node`, `get_neighbors`, `shortest_path`; mit Codex nicht live geprüft. Der Plan-Reviewer erhält den aktuellen Text der im Plan genannten Dateien (bis zu 40000 Zeichen) |
| `approval` | `owner` (Standard): Der Plan wartet auf `approve`. `auto`: Der Plan wird nach dem Review automatisch freigegeben und die Pipeline startet sofort; das Journal hält fest, dass der Plan automatisch freigegeben wurde |
| `limits` | `stage_files` und `stage_lines`: die Grenzen einer Etappe (10 und 500) |
| `worktree_setup` | Befehle, die Friday vor der Planung in einem neuen Worktree ausführt (Abhängigkeiten und Ähnliches): Argument-Arrays mit den Ersetzungen `{repository}` und `{worktree}`; sie müssen wiederholbar sein. Beispiel für npm unter macOS: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | Befehle, die der Executor selbst ausführen darf, in genauer Form: `"python3 -m pytest"`, nicht `python` |
| `executor_deny_read` | Zusätzliche Pfade, die die Shell des Executors nicht lesen kann |
| `milestone_rounds` | Wie viele Executor-Runden ein Meilenstein bekommt (4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | Der Prüfbefehl in Docker; `git-diff` vergleicht mit der Basis der Aufgabe |
| `environment_errors` | Muster von Logzeilen, die einen Umgebungsfehler bedeuten, nicht einen Codefehler |
| `browser`, `preview`, `behavior_test_policy` | Die Testumgebung, die Preview und die Regeln der Browser-Abnahme |
| `tracker` | Yandex Tracker (API oder MCP), wird zum Lesen der Aufgabenbeschreibung verwendet |
| `journal` | Wohin das Aufgaben-Journal geschrieben wird: `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`; `journal_dir` ist eine Kurzform für ein einzelnes Markdown-Journal |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}` und der Schlüssel `github.repository` oder `gitlab.base_url` + `gitlab.project` |

### Modelle und Rollen

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`: was das Modell ausführt, `claude` oder `codex`. Z.ai läuft über Claude Code; das Token wird aus einer Datei gelesen, auf die nur der Owner zugreifen kann.
- `tier`: `frontier`, `strong` oder `efficient`; `strength` ordnet die Modelle innerhalb einer Stufe.
- `effort`: die Tiefe des Denkens (`low` … `max`); ein Modell kann mehrfach mit unterschiedlichem Aufwand beschrieben werden.
- `scarce`: ein teures Modell; es wird einmal pro Aufgabe verwendet, wenn die Regeln es erlauben, die Aufgabe ohne es zu erledigen.
- `price`: der Preis pro Million Tokens nach Art (`input`, `output`, `cache_read`, `cache_write`), mit dem die Ausgaben geschätzt werden, wenn der Anbieter keinen Preis meldet.

Rollen: `plan`, `plan-review`, `develop`, `review`, `final-review`, `blind-code`, `browser`. Die Listen in `roles` sind Präferenzen: Enthält die Liste kein geeignetes Modell, nimmt Friday ein anderes aus dem Katalog. Harte Regeln: Nur `frontier`-Modelle prüfen Code, der Reviewer ist nicht schwächer als der Autor der Arbeit, und die Blindprüfung führt nicht der Autor des Codes durch. Weiche Regeln, in dieser Reihenfolge: nicht dasselbe Modell wie der Autor; ein teures Modell nur einmal; ein anderer Anbieter; die Blindprüfung nicht durch den Autor des Plans. Eine weiche Regel darf nur gebrochen werden, wenn es keinen anderen Weg gibt, und der Verstoß wird im Verlauf der Aufgabe festgehalten.

Eine Beispiel-Rollenzuweisung steht in [examples/models.json](../../examples/models.json).

## Journal

Die Aufgabenkarte ist die einzige Quelle der Wahrheit. Die Journale spiegeln sie und aktualisieren sich nach wichtigen Ereignissen: Der Plan ist fertig und freigegeben, ein Meilenstein ist abgeschlossen oder fehlgeschlagen, eine Owner-Entscheidung, das Abschluss-Review, der MR, ein Stopp. Um sie von Hand zu aktualisieren, führe `friday journal KEY` aus.

- `markdown`: eine Seite `KEY.md` und eine gemeinsame Übersichtsseite, die alle Aufgaben auflistet. Für Obsidian legst du den Vault-Ordner fest: Die Seiten haben Front Matter (Key, Status, Ausgaben, den Tag `friday`), und Aufgaben sind per Wiki-Link verknüpft. Eine Aufgabenseite enthält den Plan mit seinen Annahmen und offenen Befunden, die Meilensteine mit der Zusammenfassung des Executors und den Commits, die strittigen Befunde, die Entscheidungen, die Ausgaben nach Rolle und die Zeitleiste.
- `jsonl`: alle Ereignisse der Aufgabe, zur Auswertung in Tabellenkalkulationen oder Dashboards.

Vorlagen sind Markdown mit den Platzhaltern `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}`. Friday nimmt die Vorlage aus dem Feld `template` des Ziels, dann aus `.friday/task.md` im Projekt-Repository, dann die eingebaute ([friday/templates](../../friday/templates)). Der Text zwischen `<!-- friday:notes -->` und `<!-- /friday:notes -->` gehört dir: Er bleibt bei jeder Aktualisierung erhalten. Friday überschreibt keine Seite, die es nicht selbst geschrieben hat.

## Maschineneinstellungen

`FRIDAY_HOME/machine.json` legt die gemeinsamen Ressourcen für alle Aufgaben fest:

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

Standardmäßig laufen ein Gate und zwei Browser gleichzeitig, und Anbieter sind nicht begrenzt. Anbieter-Kontingente werden automatisch erfasst: Ein Lauf, der an ein Limit stößt, hält fest, wann das Kontingent zurückkehrt, und bis dahin wird der Anbieter bei der Modellwahl übersprungen.

## Grenzen

- **Sandbox des Executors.** Die Shell des Executors (Claude Code) läuft in einer Sandbox: kein Netzwerk, kein Lesen von `~/.ssh`, `~/.aws`, `~/.config` und Ähnlichem, Schreiben nur in den Worktree und einen temporären Ordner, und kein Ausführen außerhalb der Sandbox. Befehle, die nicht in `executor_commands` stehen und keine Git-Befehle sind, werden abgelehnt. Codex läuft in seiner eigenen Sandbox mit denselben Grenzen.
- **Reviewer** lesen nur. Die Blindprüfung läuft in einem leeren Ordner.
- **Das Gate** läuft in einem Container ohne Netzwerkzugriff, auf einem schreibgeschützt eingebundenen Snapshot des Commits.
- **Der Browser** erhält nur Fridays Tools für eine Preview; das Umgehen der Autorisierung und das Lesen der Header autorisierter Anfragen sind verboten.
- **Veröffentlichung.** Friday pusht nur den Branch der eigenen Aufgabe und öffnet nur einen Draft-MR/-PR. Es überschreibt keinen fremden Branch, mergt nicht und deployt nicht.
- **Noch nicht live geprüft:** Rollen auf Codex, die Browser-Abnahme und das Veröffentlichen auf GitLab wurden nur mit Tests mit Fake-Agenten geprüft.
