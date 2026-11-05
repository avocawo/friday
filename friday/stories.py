"""A task's second repository: the component showcase (Storybook) that holds the UI kit's worktree inside it.

The kit stays the task's repository: its worktree, gate, base and reviews work as for any task. The showcase gets its own
worktree and branch, the kit's worktree is created at <showcase>/<mount> (Storybook's own layout for linked sources),
and plan paths that start with "stories:" name files of the showcase."""
import re
import subprocess
from pathlib import Path, PurePosixPath

from . import card as cards
from . import gate, state
from .errors import FridayError
from .locks import locked

PREFIX = "stories:"
MOUNT = "deps/vue-uikit"
THEME_QUERY = "globals=theme:{theme}"
GIT = ("add", "commit", "status", "diff", "log", "restore")


def settings(profile):
    value = profile.get("stories")
    return value if isinstance(value, dict) else None


def safe_relative(value):
    return (isinstance(value, str) and value and not value.startswith("/") and "\\" not in value
            and ".." not in PurePosixPath(value).parts)


def validate(value):
    if value is None:
        return
    allowed = {"repository", "base_branch", "worktree_root", "mount", "setup", "commands", "image", "build", "themes",
               "theme_query"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise FridayError("invalid_profile", "stories takes " + ", ".join(sorted(allowed)) + ".")
    for name in ("repository", "base_branch", "worktree_root"):
        if not isinstance(value.get(name), str) or not value[name]:
            raise FridayError("invalid_profile", "stories.%s is required." % name)
    if value["base_branch"].startswith("-"):
        raise FridayError("invalid_profile", "stories.base_branch is not a branch name.")
    if not safe_relative(value.get("mount", MOUNT)):
        raise FridayError("invalid_profile", "stories.mount must be a path inside the showcase repository.")
    for command in value.get("setup", []):
        if not isinstance(command, list) or not command or any(not isinstance(part, str) for part in command):
            raise FridayError("invalid_profile", "stories.setup must be a list of argument arrays.")
    commands = value.get("commands", [])
    if not isinstance(commands, list) or any(not isinstance(item, str) or not item.strip() for item in commands):
        raise FridayError("invalid_profile", "stories.commands must be an array of commands.")
    if value.get("image") is not None and (not isinstance(value["image"], str) or not value["image"]):
        raise FridayError("invalid_profile", "stories.image must be a local image name.")
    themes = value.get("themes", [])
    if not isinstance(themes, list) or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9-]+", item) for item in themes):
        raise FridayError("invalid_profile", "stories.themes must be an array of theme names (a-z, 0-9, -).")
    query = value.get("theme_query", THEME_QUERY)
    if not isinstance(query, str) or "{theme}" not in query or not re.fullmatch(r"[A-Za-z0-9_.:={}-]+", query):
        raise FridayError("invalid_profile", "stories.theme_query must be a URL parameter with {theme}, like globals=theme:{theme}.")
    from . import images
    images.validate(value.get("build"), "stories.build")


def mount(profile):
    return settings(profile).get("mount", MOUNT)


def up(profile):
    """The way from the kit's worktree to the showcase's root, e.g. ../.. for deps/vue-uikit."""
    return "/".join([".."] * len(PurePosixPath(mount(profile)).parts))


def worktree(card):
    return (card.get("stories") or {}).get("worktree")


def path(card, name):
    """A plan path as a file: stories:… in the showcase, anything else in the kit."""
    if name.startswith(PREFIX):
        return Path(worktree(card) or "/nonexistent") / name[len(PREFIX):]
    return Path(card["worktree"]) / name


def create(root, key, profile):
    """The showcase worktree on the task branch, once; returns its path. The kit's worktree goes inside it."""
    from . import workspace
    config = settings(profile)
    repo = config["repository"]
    base = config["base_branch"]
    branch = state.task_branch(profile.get("branch_template", "friday/{key}"), key)
    target = Path(config["worktree_root"]).resolve() / key
    card = cards.read(root, key)
    if worktree(card):
        if worktree(card) != str(target) or not workspace.registered(repo, target, branch):
            raise FridayError("worktree_conflict", "The recorded showcase worktree is unavailable or conflicts with the profile.")
        return str(target)
    with locked(root, "repo:" + str(Path(repo).resolve()), timeout=300):
        if target.exists():
            if not workspace.registered(repo, target, branch):
                raise FridayError("worktree_conflict", "Showcase worktree path already exists: " + str(target))
            base_commit = workspace.git(target, "merge-base", "HEAD", "origin/" + base)
        else:
            workspace.git(repo, "check-ref-format", "--branch", branch)
            workspace.git(repo, "fetch", "--no-tags", "origin", base)
            base_commit = workspace.git(repo, "rev-parse", "FETCH_HEAD^{commit}")
            target.parent.mkdir(parents=True, exist_ok=True)
            workspace.git(repo, "worktree", "add", "-b", branch, str(target), base_commit)
    # The kit's worktree lives inside the showcase: the showcase must ignore it, or its commits would swallow the kit.
    ignored = subprocess.run(["git", "-C", str(target), "check-ignore", "-q", mount(profile) + "/package.json"],
                             capture_output=True)
    if ignored.returncode:
        raise FridayError("invalid_profile", "The showcase does not ignore %s, where the kit's worktree goes." % mount(profile))
    cards.update(root, key, lambda value: value.update(stories={"worktree": str(target), "branch": branch, "base": base_commit,
                                                                "repository": repo}))
    cards.record(root, key, "stories_worktree_created", branch=branch, worktree=str(target), base=base_commit)
    return str(target)


def prepare(profile, card):
    """The showcase's setup commands (dependencies, the link to the kit), after the kit's worktree exists."""
    config = settings(profile)
    for command in config.get("setup", []):
        argv = [part.replace("{repository}", config["repository"])
                .replace("{worktree}", worktree(card)).replace("{kit}", card["worktree"]) for part in command]
        try:
            result = subprocess.run(argv, cwd=worktree(card), capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FridayError("worktree_setup_failed", str(exc))
        if result.returncode:
            raise FridayError("worktree_setup_failed", " ".join(argv) + ": " + (result.stderr.strip() or result.stdout.strip())[-2000:])


def git(card, *args):
    return gate.git(worktree(card), *args).decode("utf-8", "replace")


def head(card):
    return git(card, "rev-parse", "HEAD").strip() if worktree(card) else None


def diff(card, start):
    """The showcase's changes since start (a commit), or an empty string."""
    if not worktree(card) or not start:
        return ""
    return git(card, "diff", start + "..HEAD")


def executor_commands(profile):
    """Shell commands the executor may run for the showcase from the kit's worktree."""
    if not settings(profile):
        return []
    return ["git -C %s %s" % (up(profile), command) for command in GIT] + list(settings(profile).get("commands", []))


def plan_text(profile):
    return ("\nThis task has a second repository, the component showcase (Storybook), at %s from the working directory; "
            "the kit's own sources are linked into it. Name its files with the prefix %s (for example "
            "%ssrc/stories/Button.stories.ts) in files and new_files, and read its agent instructions and docs before "
            "planning stories. A UI milestone adds or updates the component's story with every state of the design, and "
            "its behavior criteria name the story id (as in iframe.html?id=<story-id>) the browser check opens."
            % (up(profile), PREFIX, PREFIX))


def executor_text(profile, card):
    if not worktree(card):
        return ""
    return ("\n\nThis task has a second repository, the component showcase (Storybook), at %s from your working "
            "directory. Plan paths that start with %s are files of the showcase: edit them there. Commit the "
            "showcase's changes there with git -C %s add <files> and git -C %s commit -m \"<same message format>\"; "
            "commit the kit's changes here as usual." % (up(profile), PREFIX, up(profile), up(profile)))


def themes(profile):
    config = settings(profile) or {}
    return list(config.get("themes", [])), config.get("theme_query", THEME_QUERY)


def browser_text(profile):
    """The browser check in every theme of the showcase, comparing the component's states with each other in each one."""
    names, query = themes(profile)
    if not names:
        return ""
    urls = ", ".join("%s: append &%s" % (name, query.replace("{theme}", name)) for name in names)
    return ("\n\nThemes. The showcase has the themes %s. Check every story once in each theme by opening "
            "iframe.html?id=<story-id>&viewMode=story with the theme parameter (%s). Before judging a theme, confirm it "
            "took effect: computed_style of the page or the component's surface (background-color, color) must differ "
            "from the other theme. In each theme compare the component's states with each other, not only that styles "
            "exist: measure rest, then hover (hover the element), focus, active or drag-over and disabled with "
            "computed_style. Every interactive state must differ visibly from rest in that theme and must make the element "
            "more prominent, never less: a hover background that comes out darker or lower in contrast than rest against "
            "the page (the area dims instead of reacting) is a blocking defect, even when the same state is right in the "
            "other theme. Report each criterion once per theme, starting its criterion text with [<theme>], for example "
            "[%s] %s; name the theme in every finding it concerns."
            % (", ".join(names), urls, names[-1], "hover отличается от покоя"))


def theme_results(criteria_results, profile):
    """pass, fail or unverifiable per theme, from criteria the browser check prefixed with [<theme>]."""
    names, _ = themes(profile)
    result = {}
    for name in names:
        statuses = [item["status"] for item in criteria_results if item.get("criterion", "").startswith("[%s]" % name)]
        result[name] = ("unverifiable" if not statuses or all(s == "unverifiable" for s in statuses)
                        else "fail" if "fail" in statuses else "pass")
    return result


def commit_leftovers(card, key, milestone_id, names):
    if worktree(card) and git(card, "status", "--porcelain"):
        git(card, "add", "-A")
        git(card, "-c", "user.name=" + names["GIT_AUTHOR_NAME"], "-c", "user.email=" + names["GIT_AUTHOR_EMAIL"],
            "commit", "-qm", "%s %s: changes left uncommitted by the executor" % (key, milestone_id))
        return True
    return False
