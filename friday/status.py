"""What every task is doing right now, and what it has cost so far."""
import datetime
import math
import time

from . import card as cards
from . import i18n
from .i18n import N_, _, ngettext

ROLE_NAMES = {"digest": N_("rules digest"), "plan": N_("plan"), "plan-review": N_("plan review"), "develop": N_("development"),
              "review": N_("review"), "final-review": N_("final review"), "blind-code": N_("blind check"), "browser": N_("browser"),
              "gate": N_("gate"), "translate": N_("translation")}
STATE_NAMES = {"planning": N_("planning"), "awaiting_approval": N_("awaiting approval"), "approved": N_("approved"),
               "running": N_("running"), "stopped": N_("stopped"), "done": N_("done"), "failed": N_("failed")}


def role_name(role, lang=None):
    return i18n.tr(lang or i18n.language(), ROLE_NAMES[role]) if role in ROLE_NAMES else role


def state_name(value, lang=None):
    return i18n.tr(lang or i18n.language(), STATE_NAMES[value]) if value in STATE_NAMES else value


def card_state_name(card, lang=None):
    """A queued task, and a finished task that nothing actually checked, are not shown by their plain state."""
    if card.get("queued"):
        return i18n.tr(lang or i18n.language(), N_("queued"))
    if card.get("state") == "done" and card.get("unverified"):
        return i18n.tr(lang or i18n.language(), N_("done, not verified"))
    return state_name(card.get("state"), lang)


def run_seconds(event, started):
    """Prefer recorded execution time; legacy pairs include any wait for a vendor slot."""
    milliseconds = event.get("duration_ms")
    if (isinstance(milliseconds, (int, float)) and not isinstance(milliseconds, bool)
            and math.isfinite(milliseconds) and milliseconds >= 0):
        return milliseconds / 1000, False
    # Recovery may happen long after the agent died. Its timestamp is not an end time.
    if started is None or event.get("status") == "lost" or "execution_started" in event:
        return None, False
    try:
        begin = datetime.datetime.fromisoformat(started["at"])
        end = datetime.datetime.fromisoformat(event["at"])
        seconds = (end - begin).total_seconds()
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, False
    return (seconds, True) if seconds >= 0 else (None, False)


def totals(events):
    spent = {"runs": 0, "tokens": 0, "cost_usd": 0.0, "unpriced_runs": 0, "seconds": 0.0,
             "unmeasured_runs": 0, "duration_estimated_runs": 0, "by_role": {}}
    starts = {}
    for event in events:
        if event.get("type") == "run_started" and event.get("run"):
            starts[event["run"]] = event
        if event.get("type") != "run_finished":
            continue
        usage = event.get("usage") or {}
        tokens = sum(usage.get(name, 0) for name in ("input", "output", "cache_read", "cache_write"))
        role = spent["by_role"].setdefault(event.get("role", "?"), {
            "runs": 0, "tokens": 0, "cost_usd": 0.0, "unpriced_runs": 0,
            "seconds": 0.0, "unmeasured_runs": 0, "duration_estimated_runs": 0})
        spent["runs"] += 1
        role["runs"] += 1
        spent["tokens"] += tokens
        role["tokens"] += tokens
        if usage.get("cost_usd") is None:
            spent["unpriced_runs"] += 1
            role["unpriced_runs"] += 1
        else:
            spent["cost_usd"] += usage["cost_usd"]
            role["cost_usd"] += usage["cost_usd"]
        seconds, estimated = run_seconds(event, starts.get(event.get("run")))
        for total in (spent, role):
            if seconds is None:
                total["unmeasured_runs"] += 1
            else:
                total["seconds"] += seconds
                total["duration_estimated_runs"] += int(estimated)
    return spent


def task(root, key, now=None):
    now = now or time.time()
    card = cards.read(root, key)
    row = {"key": key, "state": card["state"], "state_name": card_state_name(card), "unverified": card.get("unverified", []), "now": [], "waiting": [], "spent": totals(cards.events(root, key))}
    for current in cards.running(card).values():
        status = cards.run_status(root, key, current["run"])
        if status.get("state") == "waiting":
            continue  # shown by the waiting entry instead
        usage = status.get("usage") or {}
        row["now"].append({"run": current["run"], "role": current["role"], "milestone": current.get("milestone"),
                           "phase": current.get("phase"), "model": current["model"],
                           "elapsed": now - current["started_at"],
                           "quiet": now - status.get("last_activity_at", current["started_at"]),
                           "last_event": status.get("last_event"), "lost": cards.lost(root, key, current),
                           "tokens": sum(usage.get(name, 0) for name in ("input", "output", "cache_read", "cache_write"))})
    for where, item in (card.get("waiting") or {}).items():
        row["waiting"].append({"milestone": None if where == "task" else where, "resource": item["resource"],
                               "elapsed": now - item["since"]})
    return row


def overview(root, now=None):
    return [task(root, key, now) for key in cards.keys(root)]


def duration(seconds, lang=None):
    lang = lang or i18n.language()
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return i18n.tr(lang, "{s}s", s=seconds)
    if seconds < 3600:
        return i18n.tr(lang, "{m}m", m=seconds // 60)
    return i18n.tr(lang, "{h}h{m:02d}m", h=seconds // 3600, m=seconds % 3600 // 60)


def money(spent, lang=None):
    cost = spent["cost_usd"]
    if cost == 0 and spent["unpriced_runs"]:
        return i18n.tr(lang or i18n.language(), "unknown ({count} unpriced)", count=spent["unpriced_runs"])
    text = "$%.4f" % cost if 0 < cost < 0.01 else "$%.2f" % cost
    if spent["unpriced_runs"]:
        text += " " + i18n.tr(lang or i18n.language(), "+{count} unpriced", count=spent["unpriced_runs"])
    return text


def work_time(spent, lang=None):
    lang = lang or i18n.language()
    text = duration(spent["seconds"], lang)
    if spent.get("duration_estimated_runs"):
        text += " " + i18n.tr(lang, "({count} from events)", count=spent["duration_estimated_runs"])
    if spent.get("unmeasured_runs"):
        text += " " + i18n.tr(lang, "+{count} without duration", count=spent["unmeasured_runs"])
    return text


def tokens(count):
    return "%.1fM" % (count / 1e6) if count >= 1e6 else "%dk" % (count // 1000) if count >= 1000 else str(count)


def describe(now):
    if now is None:
        return "—"
    parts = [now["milestone"]] if now["milestone"] else []
    parts.append(role_name(now["role"]))
    parts.append(now["model"])
    if now["lost"]:
        parts.append(_("LOST: the Friday process has exited"))
    elif now["last_event"]:
        parts.append(now["last_event"])
    return " · ".join(parts)


def describe_wait(wait):
    parts = [wait["milestone"]] if wait["milestone"] else []
    return " · ".join(parts + [_("waiting for a slot: {resource}", resource=wait["resource"])])


def server_line(root):
    from . import serve
    pid = serve.running_server(root)
    if not pid:
        return _("friday serve is not running: tasks run as separate background processes")
    return _("friday serve: running (process {pid}, tasks at once: {workers}), queued: {queue}", pid=pid,
             workers=serve.max_workers(root), queue=", ".join(serve.queue(root)) or _("none"))


def render(rows, width=110):
    if not rows:
        return _("No active tasks.")
    header = (_("TASK"), _("STATE"), _("ELAPSED"), _("QUIET"), _("TOKENS"), _("SPENT"), _("NOW"))
    table = [header]
    for row in rows:
        live = sum(now["tokens"] for now in row["now"])
        first = (row["key"], row.get("state_name") or state_name(row["state"]))
        totals_cells = (tokens(row["spent"]["tokens"] + live), money(row["spent"]))
        activity = [(duration(now["elapsed"]), duration(now["quiet"]), describe(now)) for now in row["now"]]
        activity += [(duration(wait["elapsed"]), "—", describe_wait(wait)) for wait in row["waiting"]]
        if not activity:
            activity = [("—", "—", "—")]
        for index, (elapsed, quiet, text) in enumerate(activity):
            lead = first if index == 0 else ("", "")
            money_cells = totals_cells if index == 0 else ("", "")
            table.append(lead + (elapsed, quiet) + money_cells + (text,))
    sizes = [max(len(line[index]) for line in table) for index in range(len(header) - 1)]
    lines = []
    for line in table:
        text = "  ".join(value.ljust(size) for value, size in zip(line, sizes)) + "  " + line[-1]
        lines.append(text if len(text) <= width else text[:width - 1] + "…")
    return "\n".join(lines)


def render_task(root, key, recent=10):
    row = task(root, key)
    card = cards.read(root, key)
    lines = ["%s — %s" % (key, card_state_name(card))]
    for item in card["milestones"]:
        if item.get("state") == "blocked" and item.get("blocked_reason"):
            lines.append(_("Blocked {milestone}: {reason}", milestone=item["id"], reason=item["blocked_reason"]))
        for reason in item.get("unverified") or []:
            lines.append(_("Not verified {milestone}: {reason}", milestone=item["id"], reason=reason))
    if card.get("owner_step"):
        lines.append(_("Owner step needed: {step}", step=card["owner_step"]))
    for question in card.get("questions") or []:
        lines.append(_("Question for the owner: {question} (answer: friday decide {key} <name> \"text\")", question=question, key=key))
    from . import steps
    for item in steps.attention_of(card):
        lines.append(_("Step {state}: {name} — {command}", state=_(steps.STATES[item["state"]]), name=item["name"],
                       command=item["command"]))
    if card.get("branch"):
        lines.append(_("Branch: {branch}  Worktree: {worktree}", branch=card["branch"], worktree=card.get("worktree") or "—"))
    if card["cast"]:
        lines.append(_("Models: {cast}", cast=", ".join("%s → %s" % (role_name(role), value.get("model"))
                                                       for role, value in card["cast"].items())))
    for now in row["now"]:
        lines.append(_("Now: {activity} (running {elapsed}, quiet {quiet})", activity=describe(now),
                       elapsed=duration(now["elapsed"]), quiet=duration(now["quiet"])))
    for wait in row["waiting"]:
        lines.append(_("Waiting: {activity} ({elapsed})", activity=describe_wait(wait), elapsed=duration(wait["elapsed"])))
    spent = row["spent"]
    lines.append(_("Spent: {money}, {tokens} tokens, {runs}, {seconds} of model work", money=money(spent), tokens=tokens(spent["tokens"]),
                   runs=ngettext("{n} run", "{n} runs", spent["runs"]), seconds=work_time(spent)))
    for role, value in sorted(spent["by_role"].items(), key=lambda item: -item[1]["tokens"]):
        lines.append("  " + _("{role}: {runs}, {tokens} tokens, {money}", role=role_name(role),
                              runs=ngettext("{n} run", "{n} runs", value["runs"]), tokens=tokens(value["tokens"]),
                              money=money(value)))
    history = cards.events(root, key)[-recent:]
    if history:
        lines.append(_("Recent events:"))
        for event in history:
            detail = " ".join(str(event[name]) for name in ("role", "status", "model") if event.get(name))
            lines.append("  %s %s %s" % (event["at"][11:19], event["type"], detail))
    return "\n".join(lines)
