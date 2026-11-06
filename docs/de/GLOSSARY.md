<!-- i18n: source=GLOSSARY.md sha256=fd78b7d8bdb2 -->
# Friday

[English](../../GLOSSARY.md) · [Русский](../ru/GLOSSARY.md) · [简体中文](../zh-cn/GLOSSARY.md) · [日本語](../ja/GLOSSARY.md) · [한국어](../ko/GLOSSARY.md) · [Português (Brasil)](../pt-br/GLOSSARY.md) · [Español](../es/GLOSSARY.md) · **Deutsch** · [Français](../fr/GLOSSARY.md) · [Bahasa Indonesia](../id/GLOSSARY.md)

_Dies ist eine Übersetzung der englischen Dokumentation; bei Abweichungen gilt die englische Fassung._

Eine persönliche Entwicklungs-Pipeline: Sie führt eine Aufgabe vom Plan bis zum Draft-MR, nutzt dafür Modelle verschiedener Anbieter und prüft deren Arbeit.

## Arbeit

**Aufgabe**:
Eine Arbeitseinheit für den Owner mit eigener Karte, eigenem Branch und eigenem Worktree; entspricht in der Regel einem Tracker-Ticket.
_Avoid_: Auftrag, Job

**Plan**:
Die Liste der Meilensteine und Etappen einer Aufgabe mit Abnahmekriterien, vom Owner freigegeben.

**Meilenstein**:
Eine Gruppe von 2–5 aufeinanderfolgenden Etappen, die das Gate und das Review als Ganzes durchläuft.
_Avoid_: Phase, Abschnitt

**Etappe**:
Der kleinste Teil eines Plans; sie endet mit genau einem Commit.
_Avoid_: Schritt

**Kriterium**:
Eine überprüfbare Abnahmebedingung für eine Etappe; entweder ein Code-Kriterium (gegen den Diff geprüft) oder ein Verhaltenskriterium (im Browser beobachtet).

**Profil**:
Die JSON-Datei, die Friday für ein Projekt konfiguriert (Repository, Modelle, Rollen, Gate, Browser, Tracker, Journal, Auslieferung); sie kann über den Schlüssel `extends` ein anderes Profil erweitern.

## Rollen

**Owner**:
Die Person, die den Plan freigibt, den Draft-MR abnimmt und strittige Befunde entscheidet.
_Avoid_: Betreiber, Nutzer

**Planer**:
Das Modell, das den Plan schreibt.

**Executor**:
Das Modell, das den Code eines Meilensteins schreibt.
_Avoid_: Entwickler

**Reviewer**:
Das Modell, das den Plan, einen Meilenstein oder den abschließenden Diff prüft und ein Urteil zu den Kriterien abgibt.

**Modellstufe**:
Die Leistungsklasse eines Modells: `frontier`, `strong` oder `efficient`.

**Rollenzuweisung**:
Die Zuordnung konkreter Modelle zu den Rollen einer Aufgabe, festgelegt vor der Freigabe des Plans.

## Prüfungen

**Gate**:
Deterministische Prüfungen ohne Modelle, ausgeführt in einem isolierten Container auf einem bestimmten Commit.
_Avoid_: CI, Validierung

**Meilenstein-Review**:
Eine Prüfung des Meilenstein-Diffs samt Gate-Log durch einen Reviewer eines anderen Anbieters.

**Abschluss-Review**:
Eine Prüfung des gesamten Diffs der Aufgabe vor dem MR, einschließlich der Blindprüfung des Codes.

**Browser-Abnahme**:
Beobachtung der Verhaltenskriterien eines Meilensteins im Browser auf einer lokalen Testumgebung.
_Avoid_: blind-behavior

**Strittiger Befund**:
Ein Befund eines Reviewers, dem der Executor widersprochen hat und der über das Journal an den Owner ging.

## Aufzeichnungen

**Karte**:
Fridays maschineller Zustand einer Aufgabe: Ereignisse, Meilensteine, Urteile, Ausgaben.

**Journal**:
Der lesbare Verlauf einer Aufgabe in Obsidian, aus der Karte mit Vorlagen erzeugt.
