<!-- i18n: source=README.md sha256=5262ab4aca2b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — Neon-Logo" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Eine persönliche Entwicklungs-Pipeline, die den Freitag näher rücken lässt.</strong><br>
Von der Aufgabe bis zum Draft-MR: Starke Modelle planen und prüfen, günstige Modelle schreiben den Code.</p>

<p align="center">Python 3.9+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · frühe Version</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · **Deutsch** · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

_Dies ist eine Übersetzung der englischen Dokumentation; bei Abweichungen gilt die englische Fassung._

---

```text
Aufgabe → Plan (starkes Modell) → Plan-Review (anderer Anbieter) → deine Freigabe
        → Meilensteine: Executor in der Sandbox → Gate → Meilenstein-Review → Browser-Abnahme der UI
        → Abschluss-Review + Blindprüfung → Draft-MR
```

Du bist zweimal beteiligt: Du gibst den Plan frei und nimmst den Draft-MR ab. Zwischen diesen beiden Punkten hält die Pipeline nicht an: Strittige Review-Befunde gehen zusammen mit dem MR an dich.

## Das Wichtigste

- **Modelle nach Stufe, nicht nach Name.** Das Profil enthält einen Modellkatalog mit Anbieter und Stufe (`frontier`, `strong`, `efficient`) für jedes Modell. Nur `frontier`-Modelle prüfen Code, der Reviewer ist nie schwächer als der Autor, und wenn möglich stammt der Reviewer von einem anderen Anbieter. Ein teures Modell lässt sich so markieren, dass es nur einmal pro Aufgabe läuft.
- **Ein günstiger Executor mit schnellem Feedback.** Der Executor führt Linter, Typprüfungen und Tests selbst in einer Sandbox ohne Netzwerkzugriff aus, und das Gate gibt ihm nur die Prüfungen zurück, die fehlgeschlagen sind.
- **Du siehst, was passiert.** `friday status` zeigt die Phase, das Modell, wie lange der Agent schon schweigt und wie viel für jede Aufgabe ausgegeben wurde.
- **Mehrere Aufgaben gleichzeitig.** Jede Aufgabe hat einen eigenen Branch und einen eigenen Worktree. Die Maschineneinstellungen legen fest, wie viele Gates, Browser und Läufe eines Anbieters gleichzeitig laufen dürfen.

## Schnellstart

Du brauchst Python 3.9+, Git sowie Claude Code und/oder die Codex CLI. Für das Gate und die Browser-Abnahme wird außerdem Docker benötigt.

```sh
git clone git@github.com:avocawo/friday.git && cd friday
mkdir -p profiles
cp examples/models.json examples/project-profile.json profiles/
# in profiles/project-profile.json, set the repository path, the check commands and the gate
./bin/friday doctor --profile profiles/project-profile.json
./bin/friday start TASK-1 --profile profiles/project-profile.json --brief task.md
./bin/friday approve TASK-1
./bin/friday status
```

## Befehle

| Befehl | Was er tut |
| --- | --- |
| `start KEY --profile P [--brief F]` | Legt den Worktree und den Plan an und lässt den Plan von einem anderen Modell prüfen |
| `plan KEY` | Zeigt den Plan mit den offenen Befunden |
| `approve KEY [--no-run]` | Gibt den Plan frei und startet die Pipeline im Hintergrund |
| `status [KEY] [--json]` | Zeigt, was die Aufgaben gerade tun und wie viel ausgegeben wurde |
| `decide KEY name "text" [--reopen M2]` | Hält eine Owner-Entscheidung fest; öffnet nur die genannten Meilensteine erneut |
| `run KEY` / `resume KEY` / `stop KEY` | Läuft in diesem Terminal, setzt fort, hält an |
| `archive KEY [--force]` | Entfernt den Worktree und die Karte; der Branch bleibt erhalten |
| `journal KEY` | Aktualisiert die Aufgaben-Journale (Obsidian, jsonl) |
| `doctor --profile P` | Prüft, ob der Rechner und das Profil bereit sind |

Details zu Profil, Modellen und Grenzen stehen im [Guide](guide.md). Warum die Pipeline so aufgebaut ist, erklären die [Pilot-Analyse](../process-redesign.md) (auf Russisch) und die [ADRs](../adr/) (auf Russisch).

## Entwicklung

```sh
docker build -f Dockerfile.test -t friday-test:local .
./bin/test-offline
```

Die Tests verwenden temporäre Daten und Fake-Agenten in einem Container ohne Netzwerkzugriff.

[Guide](guide.md) · [Glossar](GLOSSARY.md) · [Mitwirken](../../CONTRIBUTING.md) · [Lizenz](../../LICENSE)
