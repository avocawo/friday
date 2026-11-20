<!-- i18n: source=README.md sha256=b45badbf640b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — Neon-Logo" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Eine persönliche Entwicklungs-Pipeline, die den Freitag näher rücken lässt.</strong><br>
Von der Aufgabe bis zum Draft-MR: Starke Modelle planen und prüfen, günstige Modelle schreiben den Code.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · frühe Version</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · **Deutsch** · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

_Dies ist eine Übersetzung der englischen Dokumentation; bei Abweichungen gilt die englische Fassung._

---

```text
Aufgabe → Plan (starkes Modell) → Plan-Review (anderer Anbieter) → deine Freigabe
        → Meilensteine: Executor in der Sandbox → Gate → Meilenstein-Review → Browser-Abnahme der UI
        → Abschluss-Review + Blindprüfung → Draft-MR
```

Du gibst den Plan frei und nimmst den Draft-MR ab. Die Pipeline führt den Plan im Rahmen des konfigurierten Budgets aus; ein erschöpftes Budget oder eine defekte Umgebung geht an den Owner zurück. Strittige Review-Befunde gehen zusammen mit dem MR an dich.

## Das Wichtigste

- **Modelle nach Stufe, nicht nach Name.** Das Profil enthält einen Modellkatalog mit Anbieter und Stufe (`frontier`, `strong`, `efficient`) für jedes Modell. Nur `frontier`-Modelle prüfen Code, der Reviewer ist nie schwächer als der Autor, und wenn möglich stammt der Reviewer von einem anderen Anbieter. Ein teures Modell lässt sich so markieren, dass es nur einmal pro Aufgabe läuft.
- **Ein günstiger Executor mit schnellem Feedback.** Der Executor führt Linter, Typprüfungen und Tests selbst in einer Sandbox ohne Netzwerkzugriff aus, und das Gate gibt ihm nur die Prüfungen zurück, die fehlgeschlagen sind.
- **Du siehst, was passiert.** `friday status` zeigt die Phase, das Modell, wie lange der Agent schon schweigt und wie viel für jede Aufgabe ausgegeben wurde.
- **Mehrere Aufgaben gleichzeitig.** Jede Aufgabe hat einen eigenen Branch und einen eigenen Worktree. Die Maschineneinstellungen legen fest, wie viele Gates, Browser und Läufe eines Anbieters gleichzeitig laufen dürfen.
- **Branches nach dem Projekt benannt.** Standardmäßig legt `start` `<repository>/<task key>` an, zum Beispiel `octis/31`. Der Name stammt vom Git-`origin`; das Profil kann eine eigene Vorlage festlegen.
- **Begrenzte Ausgaben.** Ein Profil kann Frontier-Executoren mit `executor_tiers` ausschließen und mit `budget` die Größe des gesamten Plans sowie die Anzahl der Modellläufe begrenzen; `resume` setzt die Zähler nicht zurück. Das Abschluss-Review wird pro Rolle gespeichert, sodass ein Resume bei unveränderten Eingaben es nicht erneut ausführt.
- **Parallele Meilensteine.** Unabhängige, bereite Meilensteine laufen in eigenen Worktrees, standardmäßig zwei gleichzeitig; geänderte Dateien, die sie gemeinsam haben, blockieren die Integration.

## Schnellstart

Du brauchst Python 3.11+, Git und Claude Code (die Codex CLI ist optional). Für das Gate und die Browser-Abnahme wird außerdem Docker benötigt.

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

Friday spricht zehn Sprachen: `friday --lang ru …`, `FRIDAY_LANG=ru` oder `friday config language ru`. Agenten arbeiten immer auf Englisch; was du liest, wird übersetzt. Beispielprofile für Node- und Python-Projekte findest du in [examples/profiles](../../examples/profiles).

## Befehle

| Befehl | Was er tut |
| --- | --- |
| `start KEY --profile P [--brief F] [--no-design]` | Legt den Worktree und den Plan an und lässt den Plan von einem anderen Modell prüfen; mit `design` im Profil wird der Plan gegen den Figma-Frame aus dem Ticket geprüft |
| `plan KEY [--diff]` | Zeigt den Plan mit den offenen Befunden; `--diff` zeigt, was die letzte Revision geändert hat |
| `approve KEY [--no-run]` | Gibt den Plan frei und startet die Pipeline im Hintergrund |
| `status [KEY] [--json]` | Zeigt, was die Aufgaben gerade tun und wie viel ausgegeben wurde |
| `decide KEY name "text" [--reopen M2]` | Hält eine Owner-Entscheidung fest. Solange der Plan auf Freigabe wartet, überarbeitet der Planer die genannten Meilensteine danach (ohne `--reopen` wählt er sie selbst), und nur die geänderten werden erneut geprüft ([ADR 0016](../adr/0016-decision-revises-the-plan.md)); nach der Freigabe öffnet es nur die genannten Meilensteine erneut |
| `run KEY` / `resume KEY [--brief F]` / `stop KEY` | Läuft in diesem Terminal, setzt fort, hält an; `--brief` stellt die ursprüngliche Aufgabenstellung einer alten Aufgabe anhand ihres Planungs-Fingerabdrucks wieder her |
| `serve` / `service print\|install\|uninstall\|check` | Die Aufgabenwarteschlange der Maschine: startet Aufgaben nacheinander, stellt fehlgeschlagene wieder ein, benachrichtigt; läuft als launchd- oder systemd-Dienst ([Guide](guide.md#aufgabenwarteschlange-friday-serve)) |
| `step [NAME] [KEY] [--profile P] [--dry-run]` | Führt einen Profilschritt aus (`steps`): einen Aufgabenschritt mit dem Aufgaben-Key, einen Profilschritt mit `--profile`; ohne Namen listet es die Schritte und ihren Zustand auf. Das Profil beschreibt, was ein Schritt tut (ein Image, ein Bundle bauen und so weiter) ([ADR 0011](../adr/0011-profile-steps.md)) |
| `archive KEY [--force]` | Entfernt den Worktree und die Karte; der Branch bleibt erhalten |
| `journal KEY` | Aktualisiert die Aufgaben-Journale (Obsidian, jsonl) |
| `doctor --profile P` | Prüft, ob der Rechner und das Profil bereit sind |
| `config language L` | Legt die Sprache fest, in der Friday auf diesem Rechner spricht |

Details zu Profil, Modellen und Grenzen stehen im [Guide](guide.md). Warum die Pipeline so aufgebaut ist, erklären die [Pilot-Analyse](../process-redesign.md) (auf Russisch) und die [ADRs](../adr/) (0001–0006 auf Russisch).

## Entwicklung

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

Die Tests verwenden temporäre Daten und Fake-Agenten, ohne Netzwerk und ohne Docker. Wie der Code aufgebaut ist, steht in [docs/architecture.md](../architecture.md); wie du eine Änderung vorschlägst, steht in [CONTRIBUTING.md](../../CONTRIBUTING.md).

[Guide](guide.md) · [Glossar](GLOSSARY.md) · [Mitwirken](../../CONTRIBUTING.md) · [Lizenz](../../LICENSE)
