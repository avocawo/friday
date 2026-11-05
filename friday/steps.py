"""Steps a profile describes: how its project builds an image, a bundle or anything else Friday itself knows nothing about.

The owner runs a step with `friday step`; the pipeline never does. Friday fills in the placeholders, runs the command on
the host, commits the files the step names, checks a profile step's readiness for doctor, and tells whether a task
step is done, out of date or still to run. Which commands, paths, images and secrets a project needs lives in its
profile (and in scripts next to it), never here."""
import os
import re
import subprocess
from pathlib import Path

from . import card as cards
from . import stories
from .errors import FridayError
from .locks import locked

FIELDS = {"run", "description", "scope", "required", "env", "cwd", "commit", "check", "secrets", "lock"}
SCOPES = ("task", "profile")
PROFILE_PLACES = {"profile_dir", "repository", "base_branch", "stories_repository"}
TASK_PLACES = PROFILE_PLACES | {"key", "worktree", "branch", "base", "head", "showcase"}
PLACEHOLDER = re.compile(r"\{([a-z_]+(?:\.[A-Za-z0-9_-]+)?)\}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
STATES = {"done": "выполнен", "stale": "устарел", "pending": "не выполнен", "failed": "последний запуск упал"}


def argv_list(value):
    return isinstance(value, list) and value and all(isinstance(part, str) for part in value)


def validate(value):
    if value is None:
        return
    if not isinstance(value, dict):
        raise FridayError("invalid_profile", "steps maps step names to step descriptions.")
    for name, step in value.items():
        where = "steps.%s" % name
        if not NAME.fullmatch(name):
            raise FridayError("invalid_profile", "%s: step names use a-z, 0-9 and -." % where)
        if not isinstance(step, dict) or set(step) - FIELDS:
            raise FridayError("invalid_profile", "%s takes %s." % (where, ", ".join(sorted(FIELDS))))
        if not argv_list(step.get("run")):
            raise FridayError("invalid_profile", "%s.run is required: an argument array." % where)
        if step.get("scope") not in SCOPES:
            raise FridayError("invalid_profile", "%s.scope is required: task or profile." % where)
        if not isinstance(step.get("required", False), bool):
            raise FridayError("invalid_profile", "%s.required is a boolean." % where)
        if step.get("required") and step["scope"] != "task":
            raise FridayError("invalid_profile", "%s.required applies to task steps; a profile step reports through check." % where)
        env = step.get("env", {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                            or not isinstance(v, str) for k, v in env.items()):
            raise FridayError("invalid_profile", "%s.env maps variable names to strings." % where)
        secrets = step.get("secrets", {})
        if not isinstance(secrets, dict) or any(not NAME.fullmatch(k) or not isinstance(v, str) or not v for k, v in secrets.items()):
            raise FridayError("invalid_profile", "%s.secrets maps names to file paths." % where)
        if "lock" in step and (not isinstance(step["lock"], str) or not step["lock"]):
            raise FridayError("invalid_profile", "%s.lock names a shared resource (a path or a name)." % where)
        commit = step.get("commit")
        if commit is not None:
            if step["scope"] != "task":
                raise FridayError("invalid_profile", "%s.commit needs a task step." % where)
            if (not isinstance(commit, dict) or set(commit) - {"paths", "message"} or not isinstance(commit.get("message"), str)
                    or not commit["message"] or not isinstance(commit.get("paths"), list) or not commit["paths"]
                    or any(not stories.safe_relative(path) for path in commit["paths"])):
                raise FridayError("invalid_profile", "%s.commit takes paths inside the worktree and a message." % where)
        if "check" in step:
            if step["scope"] != "profile":
                raise FridayError("invalid_profile", "%s.check needs a profile step: doctor has no task." % where)
            if not argv_list(step["check"]):
                raise FridayError("invalid_profile", "%s.check must be an argument array." % where)
        allowed = TASK_PLACES if step["scope"] == "task" else PROFILE_PLACES
        texts = (step["run"] + step.get("check", []) + list(env.values()) + [step.get("cwd", ""), step.get("lock", "")] +
                 [(commit or {}).get("message", "")])
        for text in texts:
            for found in PLACEHOLDER.findall(text):
                if found.startswith("secret."):
                    if found[len("secret."):] not in secrets:
                        raise FridayError("invalid_profile", "%s uses {%s}, which is not in its secrets." % (where, found))
                elif found not in allowed:
                    raise FridayError("invalid_profile", "%s uses {%s}, which a %s step does not have." % (where, found, step["scope"]))


def resolve_paths(layer, directory):
    """Secret paths and cwd, as written in a profile layer: relative to that profile file, ~ expanded."""
    value = layer.get("steps")
    if not isinstance(value, dict):
        return
    for step in value.values():
        if not isinstance(step, dict):
            continue
        for name, path in (step.get("secrets") or {}).items():
            if isinstance(path, str) and path:
                step["secrets"][name] = str((directory / Path(path).expanduser()).resolve())
        if isinstance(step.get("cwd"), str) and step["cwd"] and "{" not in step["cwd"]:
            step["cwd"] = str((directory / Path(step["cwd"]).expanduser()).resolve())


def get(profile, name):
    step = (profile.get("steps") or {}).get(name)
    if not step:
        raise FridayError("not_found", "The profile has no step %s. Steps: %s." % (name, ", ".join(sorted(profile.get("steps") or {})) or "none"))
    return step


def places(profile, step, card=None):
    from . import workspace
    value = {"profile_dir": str(Path(profile["path"]).parent), "repository": profile["repository"],
             "base_branch": profile.get("base_branch", ""), "stories_repository": (stories.settings(profile) or {}).get("repository", "")}
    if step["scope"] == "task":
        if card is None:
            raise FridayError("invalid_state", "This step belongs to a task: pass its key.")
        value.update(key=card["key"], worktree=card["worktree"], branch=card.get("branch") or "", base=card.get("base") or "",
                     head=workspace.git(card["worktree"], "rev-parse", "HEAD").strip(), showcase=stories.worktree(card) or "")
    for name, path in (step.get("secrets") or {}).items():
        value["secret." + name] = path
    return value


def fill(text, values):
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def hint(profile, name, key=None):
    step = get(profile, name)
    return "friday step %s %s" % (name, key or "KEY") if step["scope"] == "task" else "friday step %s --profile %s" % (name, profile["path"])


def secrets_present(step):
    # A stat only: secrets go to the command by path and Friday never opens them.
    for name, path in (step.get("secrets") or {}).items():
        if not os.path.isfile(path):
            raise FridayError("missing", "Secret %s is not a file: %s" % (name, path))


def lock_name(step, values):
    """A step's shared resource: a path shares the repository lock Friday itself takes there; anything else is a name."""
    value = fill(step["lock"], values)
    return "repo:" + str(Path(value).resolve()) if os.path.isabs(value) else "step:" + value


def recorded(card, name):
    """The card's record of a step; a dist built before steps existed counts as the step of that name."""
    value = (card.get("steps") or {}).get(name)
    old = card.get("dist") or {}
    if value is None and name == "dist" and old.get("state") == "built":  # cards from before steps (ADR 0011)
        value = {"state": "done", "commit": old.get("commit"), "head": old.get("source")}
    return value


def state(profile, card, name):
    """done, stale (the branch changed outside the step's committed paths since it ran), pending or failed."""
    step = get(profile, name)
    value = recorded(card, name)
    if not value:
        return "pending"
    if value.get("state") == "failed":
        return "failed"
    since = value.get("commit") or value.get("head")
    if step.get("commit") and since and card.get("worktree"):
        excluded = [":(exclude)" + path for path in step["commit"]["paths"]]
        changed = subprocess.run(["git", "-C", card["worktree"], "diff", "--quiet", since, "HEAD", "--", ".", *excluded],
                                 capture_output=True)
        if changed.returncode == 1:
            return "stale"
    return "done"


def overview(profile, card):
    """Every task step with its state, for status, the journal and the MR text."""
    return [{"name": name, "state": state(profile, card, name), "required": bool(step.get("required")),
             "command": hint(profile, name, card["key"])}
            for name, step in sorted((profile.get("steps") or {}).items()) if step["scope"] == "task"]


def attention(profile, card):
    """Required steps that are not done and fresh, and any step whose last run failed."""
    return [item for item in overview(profile, card) if item["state"] != "done" and (item["required"] or item["state"] == "failed")]


def attention_of(card):
    """attention() for a card, with the profile it was started with; nothing when that profile cannot be read."""
    from . import profile as profiles
    try:
        return attention(profiles.load(card["profile"]), card)
    except (FridayError, OSError, KeyError):
        return []


def sections(card):
    """MR text sections: steps that need the owner, and steps whose result is on the branch."""
    from . import profile as profiles
    try:
        selected = profiles.load(card["profile"])
    except (FridayError, OSError, KeyError):
        return []
    items = overview(selected, card)
    result = []
    waiting = [item for item in items if item["state"] != "done" and (item["required"] or item["state"] == "failed")]
    if waiting:
        result.append(("## Шаги профиля не выполнены", ["- %s — %s: `%s`" % (item["name"], STATES[item["state"]], item["command"])
                                                        for item in waiting]))
    done = [(item["name"], recorded(card, item["name"])) for item in items if item["state"] == "done"]
    done = [(name, value) for name, value in done if value and value.get("commit")]
    if done:
        result.append(("## Шаги профиля", ["- %s: коммит `%s`" % (name, value["commit"][:10]) for name, value in done]))
    return result


def pending(profile, card):
    return [item["name"] for item in attention(profile, card)]


def run(root, profile, name, key=None, dry_run=False, echo=print):
    """Run one step; for a task step, commit the files it names and record it on the card. Returns a short report."""
    step = get(profile, name)
    if step["scope"] == "task" and key is None:
        raise FridayError("invalid_state", "Step %s belongs to a task: friday step %s KEY." % (name, name))
    card = cards.read(root, key) if step["scope"] == "task" else None
    values = places(profile, step, card)
    argv = [fill(part, values) for part in step["run"]]
    env = {variable: fill(text, values) for variable, text in (step.get("env") or {}).items()}
    cwd = fill(step.get("cwd") or (card["worktree"] if card else values["profile_dir"]), values)
    if dry_run:
        return {"name": name, "dry_run": True, "argv": argv, "env": env, "cwd": cwd,
                "lock": lock_name(step, values) if step.get("lock") else None}
    secrets_present(step)
    if card and card["state"] == "running" and isinstance(card.get("runner_pid"), int) and cards.alive(card["runner_pid"]):
        raise FridayError("invalid_state", "Task %s is running; run the step when it stops." % key)
    import contextlib
    with contextlib.ExitStack() as stack:
        if card:
            stack.enter_context(locked(root, "step-task:" + key, timeout=5))
        if step.get("lock"):
            stack.enter_context(locked(root, lock_name(step, values), timeout=5))
        return execute(root, profile, name, step, card, values, argv, env, cwd, echo)


def execute(root, profile, name, step, card, values, argv, env, cwd, echo):
    from . import workspace
    key = card["key"] if card else None
    commit = step.get("commit")
    if commit and workspace.git(card["worktree"], "status", "--porcelain"):
        raise FridayError("invalid_state", "The task worktree has uncommitted changes; step %s commits its result there." % name)
    echo("$ " + " ".join(argv))
    try:
        result = subprocess.run(argv, cwd=cwd, env=dict(os.environ, **env))  # streamed to the owner's terminal
    except FileNotFoundError as exc:
        raise FridayError("step_failed", "Step %s cannot start: %s" % (name, exc))
    if result.returncode:
        if card:
            cards.update(root, key, lambda value: value.setdefault("steps", {}).update({name: {"state": "failed", "code": result.returncode}}))
            cards.record(root, key, "step_failed", name=name, code=result.returncode)
        raise FridayError("step_failed", "Step %s exited with %d." % (name, result.returncode))
    report = {"name": name, "commit": None}
    if commit:
        from .milestone import identity
        paths = list(commit["paths"])
        if workspace.git(card["worktree"], "status", "--porcelain", "--", *paths):
            names = identity(card["worktree"])
            workspace.git(card["worktree"], "add", "-A", "--", *paths)
            workspace.git(card["worktree"], "-c", "user.name=" + names["GIT_AUTHOR_NAME"], "-c", "user.email=" + names["GIT_AUTHOR_EMAIL"],
                          "commit", "-qm", fill(commit["message"], values))
            report["commit"] = workspace.git(card["worktree"], "rev-parse", "HEAD").strip()
            report["stat"] = workspace.git(card["worktree"], "show", "--stat", "--format=", "HEAD").strip()
        left = workspace.git(card["worktree"], "status", "--porcelain")
        if left:
            report["left"] = left.strip()
    if card:
        cards.update(root, key, lambda value: value.setdefault("steps", {}).update(
            {name: {"state": "done", "commit": report["commit"], "head": values["head"]}}))
        cards.record(root, key, "step_done", name=name, commit=report["commit"])
        report.update(after_task_step(root, key, report))
    return report


def after_task_step(root, key, report):
    """Keep the saved MR text current, and say when the step's commit still has to reach a delivered branch."""
    from . import final
    notes = {}
    text = cards.directory(root, key) / "merge-request.md"
    if text.exists():
        text.write_text(final.description(root, key) + "\n", encoding="utf-8")
        notes["mr_text"] = str(text)
    delivery = cards.read(root, key).get("delivery") or {}
    if report.get("commit") and delivery.get("head") and delivery["head"] != report["commit"]:
        notes["push"] = "Ветка %s уже доставлена; коммит шага ещё не запушен, а текст MR на сервере не обновлён." % delivery.get("branch", "")
    return notes


def check(profile, name):
    """doctor: the step's own readiness command. Exit 0 ready, 2 out of date (warning), anything else missing."""
    step = get(profile, name)
    secrets_present(step)
    values = places(profile, step)
    argv = [fill(part, values) for part in step["check"]]
    env = {variable: fill(text, values) for variable, text in (step.get("env") or {}).items()}
    try:
        result = subprocess.run(argv, cwd=values["profile_dir"], env=dict(os.environ, **env), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "missing", "check cannot run: %s" % exc
    lines = (result.stdout.strip() or result.stderr.strip()).splitlines()
    reason = lines[-1] if lines else ""
    if result.returncode == 0:
        return "ok", reason
    return ("stale" if result.returncode == 2 else "missing"), "%s. Run: %s" % (reason or "not ready", hint(profile, name))


def listing(profile, card=None):
    """`friday step` without a name: the profile's steps, their description and, for a task, their state."""
    lines = []
    for name, step in sorted((profile.get("steps") or {}).items()):
        shown = ""
        if step["scope"] == "task" and card:
            shown = " — " + STATES[state(profile, card, name)]
        lines.append("%s (%s%s)%s" % (name, "задача" if step["scope"] == "task" else "профиль",
                                      ", обязательный" if step.get("required") else "", shown))
        if step.get("description"):
            lines.append("  " + step["description"])
    return "\n".join(lines) or "В профиле нет шагов (steps)."


def text(report):
    if report.get("dry_run"):
        lines = ["Шаг %s (пробный прогон, ничего не запускается):" % report["name"], "  cwd: " + report["cwd"]]
        lines += ["  env: %s=%s" % item for item in sorted(report["env"].items())]
        if report.get("lock"):
            lines.append("  блокировка: " + report["lock"])
        return "\n".join(lines + ["  $ " + " ".join(report["argv"])])
    lines = ["Шаг %s выполнен%s." % (report["name"], ", коммит " + report["commit"][:12] if report.get("commit") else "")]
    if report.get("stat"):
        lines += ["", report["stat"]]
    if report.get("left"):
        lines += ["", "Шаг оставил в рабочей копии изменения вне своих путей — разберитесь до следующего шага:", report["left"]]
    if report.get("mr_text"):
        lines.append("Текст MR обновлён: " + report["mr_text"])
    if report.get("push"):
        lines.append(report["push"])
    return "\n".join(lines)
