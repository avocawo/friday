"""Friday's command line: task → plan → milestones → final review → draft merge request."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, analysis, card, doctor, i18n, journal, milestone, plan, profile, serve, service, state, status, steps, tasks, tracker, workspace
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
    start.add_argument("--no-design", action="store_true", help=_("plan without the profile's Figma mockup"))
    for name, text in (("plan", _("show the task's plan")), ("run", _("run the approved plan in this terminal")),
                       ("resume", _("resume a stopped or failed task")), ("stop", _("stop a running task"))):
        command = commands.add_parser(name, help=text)
        command.add_argument("key")
        if name == "plan":
            command.add_argument("--diff", action="store_true", help=_("what the latest revision of the plan changed"))
        if name == "resume":
            command.add_argument("--brief", help=_("restore the original statement of an old task by its planning fingerprint"))
    approve = commands.add_parser("approve", help=_("approve the plan and run the pipeline in the background"))
    approve.add_argument("key")
    approve.add_argument("--no-run", action="store_true", help=_("only approve, do not run"))
    show = commands.add_parser("status", help=_("what tasks are doing now and what they have cost"))
    show.add_argument("key", nargs="?")
    show.add_argument("--json", action="store_true", help=_("machine-readable output"))
    decide = commands.add_parser("decide", help=_("record an owner decision; a plan awaiting approval is revised by it"))
    decide.add_argument("key")
    decide.add_argument("name")
    decide.add_argument("text")
    decide.add_argument("--reopen", default="", help=_("milestones the decision changes, comma-separated; before approval "
                                                         "without them the planner chooses the milestones"))
    archive = commands.add_parser("archive", help=_("remove the worktree and the card to start the task over"))
    archive.add_argument("key")
    archive.add_argument("--force", action="store_true", help=_("also remove uncommitted changes"))
    step = commands.add_parser("step", help=_("run a profile step (steps): for a task with its key, for the profile with --profile"))
    step.add_argument("name", nargs="?", help=_("without a name, the profile's steps"))
    step.add_argument("key", nargs="?")
    step.add_argument("--list", action="store_true", help=_("the profile's steps and their state for the task"))
    step.add_argument("--profile", default=profile_default, help=_("the project profile (or FRIDAY_PROFILE)"))
    step.add_argument("--dry-run", action="store_true", help=_("show the command without running anything"))
    commands.add_parser("journal", help=_("update the task's journals from its card")).add_argument("key")
    commands.add_parser("serve", help=_("the machine's queue of approved tasks: starts them, puts back failed ones, notifies"))
    unit = commands.add_parser("service", help=_("friday serve as a user service: launchd (macOS) or systemd (Linux)"))
    unit.add_argument("action", choices=("print", "install", "uninstall", "check"))
    unit.add_argument("--profile", default=profile_default, help=_("for check: the profile for friday doctor"))
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


def launch(root, key, command="run", first=False):
    """Run the pipeline of an approved task in the background — through friday serve's queue when it runs."""
    if serve.running_server(root):
        serve.enqueue(root, key, command, first)
        return _("the task is queued in friday serve (position {position}). Follow it: friday status {key}",
                 position=serve.queue(root).index(key) + 1, key=key)
    log = card.directory(root, key) / "run.log"
    with open(log, "a") as output:
        subprocess.Popen([sys.executable, "-m", "friday", command, key], stdout=output, stderr=output,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent),
                         env=dict(os.environ, FRIDAY_HOME=str(root), FRIDAY_LANG=i18n.language()))
    return _("the pipeline is running in the background (friday serve is not running). Follow it: friday status {key}, "
             "log: {log}", key=key, log=log)


def task_profile(root, key):
    return profile.load(card.read(root, key)["profile"])


def decision_report(value, reopened, revised=None):
    """What a decision did to the milestones: revised in the plan, reopened to run again, or only told about it."""
    changed, removed = revised or ([], [])
    told = [item["id"] for item in value["milestones"]
            if item["state"] not in ("done", "running") and item["id"] not in reopened and item["id"] not in changed]
    lines = []
    if changed:
        lines.append(_("The plan is revised (milestones: {milestones}).", milestones=", ".join(changed)))
    if removed:
        lines.append(_("Removed from the plan (milestones: {milestones}).", milestones=", ".join(removed)))
    if revised is not None and not changed and not removed:
        lines.append(_("The planner changed no milestone."))
    if reopened:
        lines.append(_("Reopened to run again (milestones: {milestones}).", milestones=", ".join(reopened)))
    if told:
        lines.append(_("Only added to the context of milestones ({milestones}): their plan steps are unchanged.",
                       milestones=", ".join(told)))
    return "\n".join(lines) or _("No milestone will get the decision: all are done. To redo them, name them in --reopen.")


def dispatch(root, args):
    if args.command == "start":
        return start_task(root, args)
    if args.command == "plan":
        print(plan.diff(root, args.key) if args.diff else plan.render(root, args.key))
    elif args.command == "approve":
        plan.approve(root, args.key)
        if args.no_run:
            print(_("Plan {key} approved.", key=args.key))
        else:
            print(_("Plan {key} approved, {launched}", key=args.key, launched=launch(root, args.key)))
    elif args.command in ("run", "resume"):
        selected = task_profile(root, args.key)
        if args.command == "resume" and args.brief:
            plan.restore_statement(root, args.key, selected, Path(args.brief).read_text(encoding="utf-8"))
        if args.command == "resume" and serve.running_server(root) and not os.environ.get(serve.CHILD):
            print(_("Task {key}: {launched}", key=args.key, launched=launch(root, args.key, "resume")))
            return 0
        result = (milestone.run_task if args.command == "run" else milestone.resume)(root, args.key, selected)
        delivery = result.get("delivery") or {}
        print(_("Milestones done: {count}. Left: {left}", count=len(result["completed"]), left=", ".join(result["left"]) or _("none")))
        if delivery.get("url"):
            print(_("Draft MR: {url}", url=delivery["url"]))
        elif delivery.get("description"):
            print(_("MR text: {text}", text=delivery["description"]))
    elif args.command == "stop":
        if serve.dequeue(root, args.key) and not serve.active(card.read(root, args.key)):
            print(_("Task {key} is removed from the friday serve queue.", key=args.key))
        else:
            print(_("Stop signal sent to task {key} (process {pid}).", key=args.key, pid=tasks.stop(root, args.key)))
    elif args.command == "serve":
        serve.serve(root)
    elif args.command == "service":
        return manage_service(root, args)
    elif args.command == "decide":
        return record_decision(root, args)
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
    elif args.command == "step":
        if args.list and args.name and not args.key:
            args.name, args.key = None, args.name  # friday step --list KEY
        if not args.key and not args.profile:
            raise FridayError("invalid_state", "Pass the task key or --profile.")
        selected = task_profile(root, args.key) if args.key else profile.load(args.profile)
        if args.list or not args.name:
            print(steps.listing(selected, card.read(root, args.key) if args.key else None))
        else:
            print(steps.text(steps.run(root, selected, args.name, args.key, dry_run=args.dry_run)))
    elif args.command == "status":
        if args.json:
            print(json.dumps(status.task(root, args.key) if args.key else status.overview(root), ensure_ascii=False, indent=2))
        else:
            print(status.render_task(root, args.key) if args.key else
                  status.render(status.overview(root)) + "\n\n" + status.server_line(root))
    elif args.command == "config":
        return configure(root, args)
    return 0


def manage_service(root, args):
    if args.action == "print":
        path, text = service.render(root)
        print("# %s\n%s" % (path, text))
    elif args.action == "install":
        print(_("The service is installed: {path}. Log: {log}", path=service.install(root), log=root / "serve.log"))
    elif args.action == "uninstall":
        print(_("The service is removed: {path}", path=service.uninstall()))
    else:
        if not args.profile:
            raise FridayError("invalid_state", "Pass --profile for friday doctor.")
        code, output = service.check(root, args.profile)
        print(_("friday doctor in the service's environment:") + "\n" + output)
        return code
    return 0


def record_decision(root, args):
    named = [x for x in args.reopen.split(",") if x]
    selected = task_profile(root, args.key)
    if selected.get("kind") == "analysis":
        analysis.ensure_waiting(root, args.key)
    reopened = milestone.decide(root, args.key, args.name, args.text, named, profile=selected)
    print(_("Decision recorded."))
    if selected.get("kind") == "analysis":
        analysis.answer(root, args.key, selected, args.text)  # ADR 0017
        print(analysis.render(root, args.key, selected))
        return 0
    value = card.read(root, args.key)
    if value["state"] == "awaiting_approval":
        if profile.toolset(selected, "plan") == "project":
            before = milestone.load_plan(root, args.key)
            # The project's planning session continues with the answer.
            revised = plan.changes(before, plan.answer(root, args.key, selected, args.text))
        else:
            revised = plan.amend(root, args.key, selected, args.text, named)  # ADR 0016
        print(plan.render(root, args.key) + "\n\n" + decision_report(card.read(root, args.key), reopened, revised) +
              ("\n" + _("What changed: friday plan {key} --diff", key=args.key) if any(revised) else "") +
              "\n" + _("To approve and run: friday approve {key}", key=args.key))
        return 0
    print(decision_report(value, reopened))
    if value["state"] == "stopped" and value.get("questions") and profile.toolset(selected, "execute") == "project":
        # The project's execution session continues with the answer in the background, as approve starts it.
        card.update(root, args.key, lambda current: current.update(pending_answer=args.text))
        print(_("The answer is passed to the execution session, {launched}", launched=launch(root, args.key, first=True)))
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
        if selected.get("kind") == "analysis":
            # The project's session reads the ticket itself; a brief stands in for a ticket that does not exist yet (ADR 0017).
            analysis.start(root, args.key, selected, Path(args.brief).read_text(encoding="utf-8") if args.brief else None)
            print(analysis.render(root, args.key, selected))
            return 0
        if args.brief:
            ticket = Path(args.brief).read_text(encoding="utf-8")
        else:
            ticket = tracker.ticket(selected, args.key)
            if ticket is None:
                raise FridayError("ticket_missing", "The profile has no tracker; pass --brief with the task statement.")
        plan.make(root, args.key, selected, ticket, use_design=not args.no_design)
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
