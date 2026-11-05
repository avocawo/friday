"""The UI kit's built dist, committed on the task branch as its own commit, built the way the host project builds it.

The kit's dist is not built in the task worktree: the host project (nfront) builds it in its running dev container, in
the kit checkout it mounts (deps/vue-uikit), against the host's own Vue. `friday dist KEY` borrows that checkout for
the build: it detaches it at the task's commit, runs the install and build in the container, removes the nested
packages that would give the dev stand a second Vue, copies dist into the task worktree and commits it, then puts the
checkout back where it was. The owner runs it; no role does, because it changes what the owner's dev stand shows."""
import os
import shutil
import subprocess
from pathlib import Path

from . import card as cards
from . import stories, workspace
from .errors import FridayError
from .locks import locked

DEFAULTS = {"container_path": "/app/deps/vue-uikit", "env": {"NODE_ENV": "development"},
            "install": ["npm", "ci", "--prefix", "{path}"], "build": ["npm", "--prefix", "{path}", "run", "build"],
            "cleanup": ["node_modules/vue", "node_modules/@vue"], "output": "dist", "message": "{key}: Собран dist",
            "volatile": "__file"}
FIELDS = set(DEFAULTS) | {"checkout", "container"}


def validate(value):
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise FridayError("invalid_profile", "dist takes " + ", ".join(sorted(FIELDS)) + ".")
    for name in ("checkout", "container"):
        if not isinstance(value.get(name), str) or not value[name] or value[name].startswith("-"):
            raise FridayError("invalid_profile", "dist.%s is required." % name)
    for name in ("install", "build"):
        if name in value and (not isinstance(value[name], list) or not value[name] or
                              any(not isinstance(part, str) for part in value[name])):
            raise FridayError("invalid_profile", "dist.%s must be an argument array." % name)
    paths = value.get("cleanup", []) + [value.get("output", "dist")]
    if not isinstance(value.get("cleanup", []), list) or any(not stories.safe_relative(path) for path in paths):
        raise FridayError("invalid_profile", "dist.cleanup and dist.output must be paths inside the kit.")
    env = value.get("env", {})
    if not isinstance(env, dict) or any(not isinstance(k, str) or not k or "=" in k or not isinstance(v, str) for k, v in env.items()):
        raise FridayError("invalid_profile", "dist.env must map names to strings.")
    for name in ("container_path", "message", "volatile"):
        if name in value and not isinstance(value[name], str):
            raise FridayError("invalid_profile", "dist.%s must be a string." % name)


def settings(profile):
    value = profile.get("dist")
    return dict(DEFAULTS, **value) if isinstance(value, dict) else None


def git(repo, *args):
    return workspace.git(repo, *args)


def run(argv, echo):
    echo("$ " + " ".join(argv))
    try:
        result = subprocess.run(argv)  # streamed: the owner watches npm in the terminal
    except FileNotFoundError:
        raise FridayError("environment_unavailable", "Docker is not available.")
    if result.returncode:
        raise FridayError("dist_failed", "%s exited with %d." % (" ".join(argv[:6]), result.returncode))


def container_running(name):
    try:
        result = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise FridayError("environment_unavailable", "Docker is not available.")
    return result.returncode == 0 and result.stdout.strip() == "true"


def commands(config):
    """docker exec argv for the install, the build and the cleanup, in order."""
    prefix = ["docker", "exec"] + [part for name, value in sorted(config["env"].items()) for part in ("-e", "%s=%s" % (name, value))]
    path = config["container_path"]
    steps = [prefix + [config["container"]] + [part.replace("{path}", path) for part in config[name]] for name in ("install", "build")]
    if config["cleanup"]:
        steps.append(["docker", "exec", config["container"], "rm", "-rf"] + ["%s/%s" % (path, item) for item in config["cleanup"]])
    return steps


def previous_builds(worktree, ref, message, count=3):
    pattern = message.replace("{key}: ", "").replace("{key}", "")
    return git(worktree, "log", "--format=%h %s", "-%d" % count, "--grep", pattern, ref).splitlines()


def volatile_diff(checkout, output, volatile):
    """Lines of the rebuilt output that differ from the commit, without lines that only differ by the volatile marker."""
    text = git(checkout, "diff", "-U0", "--no-color", "--", output)
    changed = [line for line in text.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    return [line for line in changed if volatile not in line] if volatile else changed


def build(root, key, profile, dry_run=False, check_base=False, echo=print):
    """Build the kit's dist in the host project's container and commit it on the task branch (or, with check_base, only
    check that the base branch's committed dist is reproduced here). Returns a short report."""
    config = settings(profile)
    if not config:
        raise FridayError("invalid_profile", "The profile has no dist block.")
    card = cards.read(root, key)
    worktree, checkout, output = card["worktree"], config["checkout"], config["output"]
    base_ref = "refs/remotes/origin/" + profile["base_branch"]
    target = git(worktree, "rev-parse", (base_ref if check_base else "HEAD") + "^{commit}").strip()
    if subprocess.run(["git", "-C", checkout, "cat-file", "-e", target + "^{commit}"], capture_output=True).returncode:
        raise FridayError("dist_failed", "%s does not have the task's commit; it must be a checkout of the kit repository." % checkout)
    if git(checkout, "status", "--porcelain"):
        raise FridayError("dist_failed", "%s has uncommitted changes; Friday does not build over someone's work." % checkout)
    if not check_base and git(worktree, "status", "--porcelain"):
        raise FridayError("dist_failed", "The task worktree has uncommitted changes: " + worktree)
    saved = git(checkout, "rev-parse", "HEAD").strip()
    branch = subprocess.run(["git", "-C", checkout, "symbolic-ref", "-q", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    steps = commands(config)
    plan = ["git -C %s checkout --detach %s" % (checkout, target)] + [" ".join(step) for step in steps]
    if dry_run:
        return {"dry_run": True, "target": target, "restore": branch or saved, "steps": plan}
    if not container_running(config["container"]):
        raise FridayError("environment_unavailable", "Container %s is not running; start the host project's dev stand." % config["container"])
    echo("Пока идёт сборка, %s стоит на %s: dev-стенд видит кит из этого коммита (HMR)." % (checkout, target[:12]))
    report = {"target": target}
    with locked(root, "repo:" + str(Path(checkout).resolve()), timeout=300):
        try:
            git(checkout, "checkout", "-q", "--detach", target)
            for step in steps:
                run(step, echo)
            if check_base:
                differing = volatile_diff(checkout, output, config["volatile"])
                report["reproduced"] = not differing
                report["differing_lines"] = differing[:50]
                return report
            destination = Path(worktree) / output
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(Path(checkout) / output, destination, symlinks=True)
        finally:
            restore(checkout, output, branch, saved, echo)
    if not git(worktree, "status", "--porcelain", "--", output):
        report["commit"] = None
        echo("dist в ветке уже совпадает со сборкой — коммит не нужен.")
    else:
        from .milestone import identity
        names = identity(worktree)
        git(worktree, "add", "-A", "--", output)
        git(worktree, "-c", "user.name=" + names["GIT_AUTHOR_NAME"], "-c", "user.email=" + names["GIT_AUTHOR_EMAIL"],
            "commit", "-qm", config["message"].replace("{key}", key))
        report["commit"] = git(worktree, "rev-parse", "HEAD").strip()
        report["stat"] = git(worktree, "show", "--stat", "--format=", "HEAD").strip()
    report["previous"] = previous_builds(worktree, base_ref, config["message"])
    cards.update(root, key, lambda value: value.update(dist={"state": "built", "commit": report["commit"], "source": target}))
    cards.record(root, key, "dist_built", commit=report["commit"], source=target)
    return report


def restore(checkout, output, branch, saved, echo):
    """Put the borrowed checkout back: the rebuilt output first (it blocks switching), then the original ref."""
    try:
        git(checkout, "checkout", "-q", "--", output)
        git(checkout, "checkout", "-q", *((branch,) if branch else ("--detach", saved)))
    except FridayError as exc:
        echo("Не удалось вернуть %s на прежнее место (%s). Верните вручную:\n  git -C %s checkout -- %s\n  git -C %s checkout %s"
             % (checkout, exc, checkout, output, checkout, branch or "--detach " + saved))
        raise


def required(profile):
    """A task of a profile with dist is not finished until its dist is built."""
    return {"state": "pending"} if settings(profile) else None


def text(report):
    if report.get("dry_run"):
        return "\n".join(["Сборка dist (пробный прогон, ничего не меняется): коммит %s, затем вернуть %s." % (
            report["target"][:12], report["restore"])] + ["  " + step for step in report["steps"]])
    if "reproduced" in report:
        if report["reproduced"]:
            return "База воспроизводится: dist из %s совпадает с закоммиченным (без строк с путями)." % report["target"][:12]
        return "База НЕ воспроизводится, отличаются строки:\n" + "\n".join(report["differing_lines"])
    lines = ["dist собран из %s%s." % (report["target"][:12], ", коммит " + report["commit"][:12] if report.get("commit") else "")]
    if report.get("stat"):
        lines += ["", report["stat"]]
    if report.get("previous"):
        lines += ["", "Прошлые сборки dist для сравнения (большой дифф vue-uikit.es.js при новом компоненте или импорте — "
                  "это переименования минификатора, норма):"] + ["  " + line for line in report["previous"]]
    return "\n".join(lines)
