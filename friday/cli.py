"""Friday's command line: task → plan → milestones → final review → draft merge request."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, card, doctor, i18n, journal, milestone, plan, profile, state, status, tasks, tracker, workspace
from .errors import FridayError
from .i18n import _

# argparse's own words, so that help and usage errors read in one language.
ARGPARSE = {i18n.N_("usage: "), i18n.N_("positional arguments"), i18n.N_("options"), i18n.N_("show this help message and exit"),
            i18n.N_("show program's version number and exit"), i18n.N_("the following arguments are required: %s"),
            i18n.N_("%(prog)s: error: %(message)s\n"), i18n.N_("invalid choice: %(value)r (choose from %(choices)s)"),
            i18n.N_("unrecognized arguments: %s"), i18n.N_("expected one argument")}


def localize_argparse():
    argparse._ = lambda text: i18n.tr(i18n.language(), text) if text in ARGPARSE else text
    argparse.ngettext = lambda singular, plural, count: i18n.tr(i18n.language(), singular if count == 1 else plural)


def language_from(argv):
    """--lang is read before the parser is built, so that the help itself is in that language."""
    for index, value in enumerate(argv):
        if value == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("--lang="):
            return value.split("=", 1)[1]
    return None


def parser():
    profile_default = os.environ.get("FRIDAY_PROFILE")
    top = argparse.ArgumentParser(prog="friday", description=_("A development pipeline: from a task to a draft merge request."))
    top.add_argument("--version", action="version", version="friday " + __version__)
    top.add_argument("--lang", metavar="LANG", help=_("language of the output: {languages}", languages=", ".join(i18n.LANGUAGES)))
    commands = top.add_subparsers(dest="command", required=True, metavar="COMMAND")
    start = commands.add_parser("start", help=_("create a worktree, a plan and its review by another model"))
    start.add_argument("key")
    start.add_argument("--profile", default=profile_default, required=not profile_default, help=_("the project profile (or FRIDAY_PROFILE)"))
    start.add_argument("--brief", help=_("a file with the task statement when the task is not in a tracker"))
    start.add_argument("--no-run", action="store_true", help=_("with approval: auto, only approve without running"))
    for name, text in (("plan", _("show the task's plan")), ("run", _("run the approved plan in this terminal")),
                       ("resume", _("resume a stopped or failed task")), ("stop", _("stop a running task"))):
        commands.add_parser(name, help=text).add_argument("key")
    approve = commands.add_parser("approve", help=_("approve the plan and run the pipeline in the background"))
    approve.add_argument("key")
    approve.add_argument("--no-run", action="store_true", help=_("only approve, do not run"))
    show = commands.add_parser("status", help=_("what tasks are doing now and what they have cost"))
    show.add_argument("key", nargs="?")
    show.add_argument("--json", action="store_true", help=_("machine-readable output"))
    decide = commands.add_parser("decide", help=_("record an owner decision"))
    decide.add_argument("key")
    decide.add_argument("name")
    decide.add_argument("text")
    decide.add_argument("--reopen", default="", help=_("milestones the decision changes, comma-separated"))
    archive = commands.add_parser("archive", help=_("remove the worktree and the card to start the task over"))
    archive.add_argument("key")
    archive.add_argument("--force", action="store_true", help=_("also remove uncommitted changes"))
    commands.add_parser("journal", help=_("update the task's journals from its card")).add_argument("key")
    check = commands.add_parser("doctor", help=_("is this machine and profile ready"))
    check.add_argument("--profile", default=profile_default, required=not profile_default, help=_("the project profile (or FRIDAY_PROFILE)"))
    check.add_argument("--json", action="store_true", help=_("machine-readable output"))
    config = commands.add_parser("config", help=_("show or change Friday's settings on this machine"))
    config.add_argument("setting", nargs="?", choices=["language"], help=_("the setting to change"))
    config.add_argument("value", nargs="?", help=_("the new value; 'auto' follows the system language"))
    return top


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = state.home()
    explicit = language_from(argv)
    i18n.select(i18n.resolve(explicit, root))
    if explicit and i18n.normalize(explicit) is None:
        print(_("Unknown language {language}; using English. Available: {languages}", language=explicit,
                languages=", ".join(i18n.LANGUAGES)), file=sys.stderr)
    localize_argparse()
    args = parser().parse_args(argv)
    try:
        return dispatch(root, args)
    except (FridayError, OSError) as exc:
        print(_("Error: {message}", message=i18n.error(exc) if isinstance(exc, FridayError) else str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(_("Stopped."), file=sys.stderr)
        return 130


def launch(root, key):
    """Run the pipeline of an approved task in the background; returns where to follow it."""
    log = card.directory(root, key) / "run.log"
    with open(log, "a") as output:
        subprocess.Popen([sys.executable, "-m", "friday", "run", key], stdout=output, stderr=output,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent),
                         env=dict(os.environ, FRIDAY_HOME=str(root), FRIDAY_LANG=i18n.language()))
    return _("the pipeline is running in the background. Follow it: friday status {key}, log: {log}", key=key, log=log)


def task_profile(root, key):
    return profile.load(card.read(root, key)["profile"])


def dispatch(root, args):
    if args.command == "start":
        return start_task(root, args)
    if args.command == "plan":
        print(plan.render(root, args.key))
    elif args.command == "approve":
        plan.approve(root, args.key)
        if args.no_run:
            print(_("Plan {key} approved.", key=args.key))
        else:
            print(_("Plan {key} approved, {launched}", key=args.key, launched=launch(root, args.key)))
    elif args.command in ("run", "resume"):
        selected = task_profile(root, args.key)
        result = (milestone.run_task if args.command == "run" else milestone.resume)(root, args.key, selected)
        delivery = result.get("delivery") or {}
        print(_("Milestones done: {count}. Left: {left}", count=len(result["completed"]), left=", ".join(result["left"]) or _("none")))
        if delivery.get("url"):
            print(_("Draft MR: {url}", url=delivery["url"]))
        elif delivery.get("description"):
            print(_("MR text: {text}", text=delivery["description"]))
    elif args.command == "stop":
        print(_("Stop signal sent to task {key} (process {pid}).", key=args.key, pid=tasks.stop(root, args.key)))
    elif args.command == "decide":
        reopened = milestone.decide(root, args.key, args.name, args.text, [x for x in args.reopen.split(",") if x],
                                    profile=task_profile(root, args.key))
        print(_("Decision recorded. Reopened: {milestones}", milestones=", ".join(reopened) or _("nothing")))
    elif args.command == "archive":
        result = tasks.archive(root, args.key, task_profile(root, args.key), force=args.force)
        print(_("Task archived: {archive}. Branch {branch} is kept.", archive=result["archive"], branch=result["branch"]))
    elif args.command == "journal":
        written = journal.sync(root, args.key)
        print("\n".join(written) if written else _("The profile configures no journals (journal or journal_dir)."))
    elif args.command == "doctor":
        report = doctor.inspect(args.profile)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else doctor.render(report))
        return 0 if report["ready"] else 1
    elif args.command == "status":
        if args.json:
            print(json.dumps(status.task(root, args.key) if args.key else status.overview(root), ensure_ascii=False, indent=2))
        else:
            print(status.render_task(root, args.key) if args.key else status.render(status.overview(root)))
    elif args.command == "config":
        return configure(root, args)
    return 0


def configure(root, args):
    if args.setting is None or args.value is None:
        chosen = i18n.saved(root)
        print(_("language: {language} ({source})", language=i18n.language(),
                source=_("saved") if chosen else _("from FRIDAY_LANG or the system")))
        print(_("Available: {languages}", languages=", ".join("%s (%s)" % (code, name) for code, name in i18n.NAMES.items())))
        return 0
    if args.value == "auto":
        i18n.save(None, root)
        print(_("Language follows FRIDAY_LANG or the system now."))
        return 0
    chosen = i18n.normalize(args.value)
    if chosen is None:
        raise FridayError("invalid_value", "Unknown language {language}. Available: {languages}", language=args.value,
                          languages=", ".join(i18n.LANGUAGES))
    i18n.save(chosen, root)
    i18n.select(chosen)
    print(_("Language: {name}.", name=i18n.NAMES[chosen]))
    return 0


def start_task(root, args):
    selected = profile.load(args.profile)
    try:
        existing = card.read(root, args.key)
    except FridayError as exc:
        if exc.code != "not_found":
            raise
        existing = card.create(root, args.key, selected["path"])
    if existing["state"] not in ("planning", "failed"):
        raise FridayError("invalid_state", "Task {key} is already {state}.", key=args.key, state=existing["state"])
    try:
        workspace.create(root, args.key, selected)
        if args.brief:
            ticket = Path(args.brief).read_text(encoding="utf-8")
        else:
            ticket = tracker.ticket(selected, args.key)
            if ticket is None:
                raise FridayError("ticket_missing", "The profile has no tracker; pass --brief with the task statement.")
        plan.make(root, args.key, selected, ticket)
    except (FridayError, OSError) as exc:
        card.update(root, args.key, lambda value: value.update(state="failed"))
        card.record(root, args.key, "start_failed", error=str(exc))
        raise
    print(plan.render(root, args.key, selected))
    if selected.get("approval") == "auto":
        plan.approve(root, args.key, by="auto")
        print("\n" + _("Plan approved automatically (approval: auto), {next}",
                       next=launch(root, args.key) if not args.no_run else _("to run it: friday run {key}", key=args.key)))
    else:
        print("\n" + _("To approve and run: friday approve {key}", key=args.key))
    return 0
