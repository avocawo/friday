"""The readable history of a task, built from its card and events (ADR 0006).

The card is the only source of truth; journals are renderings of it. A profile lists where they go:
"journal": [{"kind": "markdown", "dir": "...", "template": "..."}, {"kind": "jsonl", "dir": "..."}]
("journal_dir": "..." is short for one markdown journal). Obsidian is a markdown journal in a vault folder."""
import datetime
import json
import os
import re
import sys
from pathlib import Path

from . import card as cards
from . import profile as profiles
from . import state, status
from .errors import FridayError

TEMPLATES = Path(__file__).resolve().parent / "templates"
MARKER = "<!-- friday:generated %s -->"
FIELD = "friday_generated: %s"
NOTES = re.compile(r"<!-- friday:notes -->\n(.*?)<!-- /friday:notes -->", re.S)
# Events that change what the journal shows; anything else waits for the next of these.
SIGNIFICANT = {"plan_ready", "plan_approved", "milestone_done", "milestone_failed", "decision", "final_reviewed",
               "draft_opened", "delivery_skipped", "run_task_finished", "stopped", "start_failed", "resumed"}
STATES = {"planning": "Планирование", "awaiting_approval": "Ждёт утверждения плана", "approved": "План утверждён",
          "running": "В работе", "stopped": "Остановлена", "done": "Готова", "failed": "Сбой"}
MILESTONE_STATES = {"pending": "впереди", "running": "в работе", "done": "готова", "failed": "сбой", "reopened": "переоткрыта",
                    "blocked": "среда недоступна"}


def sinks(profile):
    value = profile.get("journal")
    if value is None and profile.get("journal_dir"):
        value = [{"kind": "markdown", "dir": profile["journal_dir"]}]
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) or item.get("kind") not in ("markdown", "jsonl")
                                          or not isinstance(item.get("dir"), str) for item in value):
        raise FridayError("invalid_profile", "journal must list sinks with kind markdown or jsonl and a dir.")
    base = Path(profile.get("path", ".")).parent
    return [dict(item, dir=str((base / Path(item["dir"]).expanduser()).resolve())) for item in value]


def template(sink, profile, name):
    """The sink's own template, then the project's .friday/<name>, then Friday's built-in one."""
    candidates = []
    if sink.get(name.split(".")[0] + "_template") or (name == "task.md" and sink.get("template")):
        own = sink.get(name.split(".")[0] + "_template") or sink.get("template")
        candidates.append((Path(profile.get("path", ".")).parent / Path(own).expanduser()).resolve())
    candidates += [Path(profile["repository"]) / ".friday" / name, TEMPLATES / name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FridayError("not_found", "No journal template " + name)


def fill(text, values):
    return re.sub(r"\{\{(\w+)\}\}", lambda match: str(values.get(match.group(1), "")), text)


def owned(key, text):
    """Mark a page as Friday's: a front matter field (Obsidian needs front matter on the first line), else a comment."""
    text = text.lstrip("\n")
    if text.startswith("---\n"):
        return "---\n" + FIELD % key + "\n" + text[4:]
    return MARKER % key + "\n" + text


def is_owned(key, text):
    head = text.split("\n", 12)[:12]
    return (MARKER % key) in head or (FIELD % key) in head


def or_dash(lines):
    return "\n".join(lines) if lines else "—"


def local_time(stamp):
    try:
        moment = datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return str(stamp)
    return moment.astimezone().strftime("%d.%m %H:%M")


def describe(event):
    """One line of history in plain words, or None for events that only matter to Friday."""
    kind, milestone = event["type"], event.get("milestone") or ""
    texts = {
        "created": lambda: "Задача заведена.",
        "worktree_created": lambda: "Ветка `%s` от `%s`." % (event.get("branch", ""), (event.get("base") or "")[:10]),
        "plan_invalid": lambda: "План не прошёл проверку выполнимости и ушёл на доработку (%d проблем)." % len(event.get("errors", [])),
        "plan_reviewed": lambda: "План проверен: %s, замечаний %s, блокирующих %s." % (event.get("verdict"), event.get("findings"), event.get("blocking")),
        "plan_ready": lambda: "План готов: вех %s, несогласованных замечаний %s." % (event.get("milestones"), event.get("open_notes")),
        "plan_approved": lambda: "План утверждён.",
        "cast_note": lambda: "Модели: %s" % event.get("note", ""),
        "milestone_started": lambda: "%s: начата." % milestone,
        "gate": lambda: "%s: гейт %s." % (milestone, {"passed": "прошёл", "failed": "не прошёл"}.get(event.get("status"), event.get("status"))),
        "milestone_reviewed": lambda: "%s: ревью %s — %s, блокирующих %s." % (milestone, event.get("model"), event.get("verdict"), event.get("blocking")),
        "browser_checked": lambda: "%s: браузерная приёмка %s — %s." % (milestone, event.get("model"), event.get("verdict")),
        "browser_unverifiable": lambda: "%s: в браузере проверить не удалось (%s)." % (milestone, event.get("reason", "")),
        "browser_skipped": lambda: "%s: браузерной приёмки нет (%s)." % (milestone, event.get("reason", "")),
        "milestone_done": lambda: "%s: готова за %s круг(а), коммитов %s, спорных %s." % (milestone, event.get("rounds"), event.get("commits"), event.get("disputes")),
        "milestone_failed": lambda: "%s: сбой (%s)." % (milestone, event.get("reason") or "гейт так и не прошёл"),
        "parallel_started": lambda: "Параллельно: %s." % ", ".join(map(str, event.get("milestones") or [])),
        "decision": lambda: "Решение владельца «%s», переоткрыты: %s." % (event.get("name"), ", ".join(event.get("reopened", [])) or "ничего"),
        "final_reviewed": lambda: "Финал, %s (%s): %s, блокирующих %s." % (
            {"final-review": "финальное ревью", "blind-code": "слепая проверка"}.get(event.get("role"), event.get("role")),
            event.get("model"), event.get("verdict"), event.get("blocking")),
        "draft_opened": lambda: "Черновой MR: %s" % event.get("url", ""),
        "delivery_skipped": lambda: "MR не публикуется: в профиле нет транспорта, текст сохранён.",
        "stopped": lambda: "Остановлена.",
        "resumed": lambda: "Продолжена.",
        "start_failed": lambda: "Подготовка не удалась: %s" % event.get("error", ""),
    }
    if kind == "run_finished" and event.get("status") not in ("ok", None):
        return "%s %s (%s): %s %s" % (milestone, event.get("role"), event.get("model"), event.get("status"), (event.get("error") or "")[:160])
    text = texts.get(kind)
    return text() if text else None


def render_task(root, key, profile, sink, previous=""):
    card = cards.read(root, key)
    plan_path = cards.directory(root, key) / "plan.json"
    document = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
    events = cards.events(root, key)
    spent = status.totals(events)
    titles = {item["id"]: item for item in document.get("milestones", [])}
    plan = []
    if document:
        plan.append(document.get("summary", ""))
        plan += [""] + ["- Вопрос: " + item for item in card["plan"].get("questions", [])]
        for note in card["plan"].get("review_notes", []):
            plan.append("- Несогласованное замечание (%s/%s): %s Ответ планировщика: %s" % (
                note["milestone"], note["stage"], note["problem"], note["answer"]))
        plan += ["- Пересечение: " + item for item in card["plan"].get("overlaps", [])]
        plan += ["- Допущение: " + item for item in document.get("assumptions", [])]
    milestones = []
    for item in card["milestones"]:
        planned = titles.get(item["id"], {})
        milestones.append("### %s. %s — %s" % (item["id"], planned.get("title", item.get("title", "")),
                                               MILESTONE_STATES.get(item["state"], item["state"])))
        if planned.get("goal"):
            milestones.append(planned["goal"])
        if item.get("summary"):
            milestones.append("\n> Исполнитель (%s): %s" % (item.get("executor", ""), item["summary"].replace("\n", "\n> ")))
        for stage in item.get("stages", []):
            if stage.get("commit"):
                milestones.append("- %s `%s`" % (stage["id"], stage["commit"][:10]))
        for finding in item.get("minor_findings", []):
            milestones.append("- Мелкое: `%s` %s" % (finding.get("file", ""), finding["problem"]))
        if item.get("outside_files"):
            milestones.append("- Вне плана: " + ", ".join("`%s`" % path for path in item["outside_files"]))
        milestones.append("")
    disputes = ["- **%s** `%s`: %s\n  - Ревьюер %s; исполнитель: %s" % (
        d.get("milestone", ""), d.get("file", ""), d["problem"], d.get("reviewer", ""), d.get("executor_reason", "")) for d in card["disputes"]]
    decisions = ["- **%s**: %s%s" % (d["name"], d["text"], " (переоткрыты: %s)" % ", ".join(d["reopen"]) if d.get("reopen") else "")
                 for d in card["decisions"]]
    costs = ["%s, %s токенов, %d запусков, %s работы моделей." % (
        status.money(spent), status.tokens(spent["tokens"]), spent["runs"], status.duration(spent["seconds"])), ""]
    costs += ["| Роль | Запусков | Токенов | Расход |", "|---|---|---|---|"]
    costs += ["| %s | %d | %s | %s |" % (status.ROLE_NAMES.get(role, role), value["runs"], status.tokens(value["tokens"]),
                                       status.money({"cost_usd": value["cost_usd"], "unpriced_runs": 0}))
              for role, value in sorted(spent["by_role"].items(), key=lambda item: -item[1]["tokens"])]
    timeline = ["- %s %s" % (local_time(event["at"]), line) for event in events for line in [describe(event)] if line]
    found = NOTES.search(previous)
    notes = "<!-- friday:notes -->\n%s<!-- /friday:notes -->" % (found.group(1) if found else "")
    delivery = card.get("delivery") or {}
    status_line = STATES.get(card["state"], card["state"])
    if delivery.get("url"):
        status_line += " · [черновой MR](%s)" % delivery["url"]
    if card.get("branch"):
        status_line += " · ветка `%s`" % card["branch"]
    title = document.get("summary", "").split(". ")[0].rstrip(".")[:120] if document else ""
    values = {"key": key, "state": card["state"], "updated": card["updated_at"], "cost": "%.4f" % spent["cost_usd"],
              "title": title or key, "status": status_line, "plan": or_dash(plan), "milestones": or_dash(milestones).rstrip(),
              "disputes": or_dash(disputes), "decisions": or_dash(decisions), "costs": "\n".join(costs),
              "timeline": or_dash(timeline), "notes": notes}
    return owned(key, fill(template(sink, profile, "task.md"), values))


def render_now(root, sink, profile):
    rows = ["| Задача | Состояние | Сейчас | Расход |", "|---|---|---|---|"]
    for row in status.overview(root):
        doing = "; ".join(status.describe(now) for now in row["now"]) or "; ".join(status.describe_wait(w) for w in row["waiting"]) or "—"
        rows.append("| [[%s]] | %s | %s | %s |" % (row["key"], STATES.get(row["state"], row["state"]), doing.replace("|", "/"),
                                                  status.money(row["spent"])))
    return owned("now", fill(template(sink, profile, "now.md"), {"updated": state.now(), "tasks": "\n".join(rows)}))


def write_page(path, key, text):
    """Replace a page Friday wrote before; never overwrite a page somebody else wrote."""
    if path.exists() and not is_owned(key, path.read_text(encoding="utf-8")):
        raise FridayError("journal_conflict", "%s exists and was not written by Friday; it is left untouched." % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def sync(root, key, profile=None):
    """Render the task into every journal its profile names; returns the written paths."""
    profile = profile or profiles.load(cards.read(root, key)["profile"])
    written = []
    for sink in sinks(profile):
        directory = Path(sink["dir"])
        if sink["kind"] == "markdown":
            page = directory / (key + ".md")
            previous = page.read_text(encoding="utf-8") if page.exists() else ""
            write_page(page, key, render_task(root, key, profile, sink, previous))
            write_page(directory / "Friday — сейчас.md", "now", render_now(root, sink, profile))
            written.append(str(page))
        else:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (key + ".jsonl")
            temporary = target.with_name("." + target.name + ".tmp")
            temporary.write_text("".join(json.dumps(dict(event, task=key), ensure_ascii=False) + "\n" for event in cards.events(root, key)),
                                 encoding="utf-8")
            os.replace(temporary, target)
            written.append(str(target))
    return written


def on_event(root, key, event):
    """Called after each card event: keep journals current without ever breaking the pipeline."""
    if event["type"] not in SIGNIFICANT:
        return
    try:
        source = cards.read(root, key)["profile"]
        if not Path(source).is_file():
            return
        sync(root, key, profiles.load(source))
    except Exception as exc:  # a journal must never stop the pipeline
        print("Friday: журнал %s не обновлён: %s" % (key, exc), file=sys.stderr)
