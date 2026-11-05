"""Steps a profile describes: how its project builds an image, a bundle or anything else Friday itself knows nothing about.

Friday only runs a step's command with placeholders filled in, on the host or in a named container, commits the files
the step names, checks its readiness for doctor and reminds the owner about required steps that have not run. Which
commands, paths, images and secrets a project needs lives in its profile (and in scripts next to it), never here."""
import os
import re
import subprocess
from pathlib import Path

from . import card as cards
from . import stories
from .errors import FridayError

FIELDS = {"run", "description", "scope", "when", "required", "container", "env", "cwd", "commit", "check", "secrets"}
SCOPES = ("task", "profile")
WHEN = ("owner", "before_delivery")
PROFILE_PLACES = {"profile_dir", "repository", "base_branch", "stories_repository"}
TASK_PLACES = PROFILE_PLACES | {"key", "worktree", "branch", "base", "head", "showcase"}
PLACEHOLDER = re.compile(r"\{([a-z_]+(?:\.[A-Za-z0-9_-]+)?)\}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


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
        if step.get("when") not in WHEN:
            raise FridayError("invalid_profile", "%s.when is required: owner or before_delivery." % where)
        if not isinstance(step.get("required", False), bool):
            raise FridayError("invalid_profile", "%s.required is a boolean." % where)
        if step.get("required") and step["scope"] != "task":
            raise FridayError("invalid_profile", "%s.required applies to task steps; a profile step reports through check." % where)
        if "container" in step and (not isinstance(step["container"], str) or not step["container"] or step["container"].startswith("-")):
            raise FridayError("invalid_profile", "%s.container must be a container name." % where)
        env = step.get("env", {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                            or not isinstance(v, str) for k, v in env.items()):
            raise FridayError("invalid_profile", "%s.env maps variable names to strings." % where)
        secrets = step.get("secrets", {})
        if not isinstance(secrets, dict) or any(not NAME.fullmatch(k) or not isinstance(v, str) or not v for k, v in secrets.items()):
            raise FridayError("invalid_profile", "%s.secrets maps names to file paths." % where)
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
        texts = step["run"] + step.get("check", []) + list(env.values()) + [step.get("cwd", "")] + [(commit or {}).get("message", "")]
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


def command(step, values, argv=None):
    """The argv to run: placeholders filled in, wrapped in docker exec when the step runs in a container."""
    argv = [fill(part, values) for part in (argv or step["run"])]
    env = {name: fill(text, values) for name, text in (step.get("env") or {}).items()}
    if step.get("container"):
        return ["docker", "exec"] + [part for name in sorted(env) for part in ("-e", "%s=%s" % (name, env[name]))] + [step["container"]] + argv, {}
    return argv, env


def hint(profile, name, key=None):
    step = get(profile, name)
    return "friday step %s %s" % (name, key or "KEY") if step["scope"] == "task" else "friday step %s --profile %s" % (name, profile["path"])


def secrets_present(step):
    # A stat only: secrets go to the command by path and Friday never opens them.
    for name, path in (step.get("secrets") or {}).items():
        if not os.path.isfile(path):
            raise FridayError("missing", "Secret %s is not a file: %s" % (name, path))


def run(root, profile, name, key=None, dry_run=False, echo=print):
    """Run one step; for a task step, commit the files it names and mark it done on the card. Returns a short report."""
    step = get(profile, name)
    card = cards.read(root, key) if step["scope"] == "task" else None
    if step["scope"] == "task" and key is None:
        raise FridayError("invalid_state", "Step %s belongs to a task: friday step %s KEY." % (name, name))
    values = places(profile, step, card)
    argv, env = command(step, values)
    cwd = fill(step.get("cwd") or (card["worktree"] if card else values["profile_dir"]), values)
    if dry_run:
        return {"name": name, "dry_run": True, "argv": argv, "env": env, "cwd": cwd}
    secrets_present(step)
    from . import workspace
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
    if card:
        def done(value):
            value.setdefault("steps", {})[name] = {"state": "done", "commit": report["commit"], "head": values["head"]}
            value["pending_steps"] = [item for item in value.get("pending_steps", []) if item["name"] != name]
        cards.update(root, key, done)
        cards.record(root, key, "step_done", name=name, commit=report["commit"])
    return report


def pending(profile, card):
    """Required owner task steps not run yet on this task."""
    done = card.get("steps") or {}
    return [name for name, step in sorted((profile.get("steps") or {}).items())
            if step["scope"] == "task" and step.get("required") and step["when"] == "owner" and name not in done]


def before_delivery(root, key, profile, echo=print):
    """The steps the pipeline itself runs right before delivery."""
    for name, step in sorted((profile.get("steps") or {}).items()):
        if step["when"] == "before_delivery":
            run(root, profile, name, key if step["scope"] == "task" else None, echo=echo)


def check(profile, name):
    """doctor: the step's own readiness command. Exit 0 ready, 2 out of date (warning), anything else missing."""
    step = get(profile, name)
    secrets_present(step)
    values = places(profile, step)
    argv, env = command(step, values, step["check"])
    try:
        result = subprocess.run(argv, cwd=values["profile_dir"], env=dict(os.environ, **env), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "missing", "check cannot run: %s" % exc
    lines = (result.stdout.strip() or result.stderr.strip()).splitlines()
    reason = lines[-1] if lines else ""
    if result.returncode == 0:
        return "ok", reason
    state = "stale" if result.returncode == 2 else "missing"
    return state, "%s. Run: %s" % (reason or "not ready", hint(profile, name))


def text(report):
    if report.get("dry_run"):
        lines = ["Шаг %s (пробный прогон, ничего не запускается):" % report["name"], "  cwd: " + report["cwd"]]
        lines += ["  env: %s=%s" % item for item in sorted(report["env"].items())]
        return "\n".join(lines + ["  $ " + " ".join(report["argv"])])
    lines = ["Шаг %s выполнен%s." % (report["name"], ", коммит " + report["commit"][:12] if report.get("commit") else "")]
    if report.get("stat"):
        lines += ["", report["stat"]]
    return "\n".join(lines)
