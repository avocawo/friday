"""kind analysis (ADR 0017): the project's own session studies the ticket and writes its documents, the owner answers its
questions through friday decide, and the project's check closes the task. No milestones, no gate, no merge request."""
import subprocess

from . import card as cards
from . import models
from . import plan
from .errors import FridayError

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "questions"],
          "properties": {"summary": {"type": "string"}, "questions": {"type": "array", "items": {"type": "string"}}}}

RULES = ("You run under Friday with nobody at the keyboard. Never wait for an answer: when the work needs the owner's "
         "decisions, end the session and return them in questions, each one standalone with your default and what it "
         "blocks; Friday resumes this session with the answers. Return questions empty only when the work is finished. "
         "In summary, say what you produced and where.")
CHECK_TAIL = 4000
# The session works for the owner but must not change the owner's own memory of Claude Code; it still reads it.
MEMORY_WRITES = "Edit(~/.claude/projects/*/memory/**)"


def start(root, key, profile, brief=None):
    """The first session of an analysis task: the profile's analysis_command for this key, with the owner's brief when
    the task has no ticket."""
    layout, notes = models.cast(profile, models.availability(root))
    cards.update(root, key, lambda value: value.update(cast=layout, state="planning", kind="analysis",
                                                       **({"task_statement": brief} if brief else {})))
    for note in notes:
        cards.record(root, key, "cast_note", note=note)
    prompt = profile["project"]["analysis_command"].replace("{key}", key) + "\n\n" + RULES
    if brief:
        prompt += "\n\nThe task has no tracker ticket yet; this brief from the owner stands in for it:\n" + brief
    return settle(root, key, profile, session(root, key, profile, prompt))


def ensure_waiting(root, key):
    """The card waits on the owner's answer; checked before decide records the decision."""
    value = cards.read(root, key)
    if value["state"] != "stopped" or not value.get("questions") or not value.get("analysis_session"):
        raise FridayError("invalid_state", "Task {key} has no analysis question waiting for an answer.", key=key)
    return value


def hold(root, key, name, text):
    """An answer kept on the card without a session turn; the next answer that runs the session carries it."""
    ensure_waiting(root, key)
    cards.update(root, key, lambda card: card.setdefault("held_answers", []).append({"name": name, "text": text}))
    cards.record(root, key, "answer_held", name=name)


def answer(root, key, profile, text, name=None):
    """The owner answered: the same session continues with this answer and every held one, in one turn."""
    value = ensure_waiting(root, key)
    answers = (value.get("held_answers") or []) + [{"name": name, "text": text}]
    joined = "\n\n".join(("%s: %s" % (item["name"], item["text"])) if item["name"] else item["text"] for item in answers)
    cards.update(root, key, lambda card: card.update(state="planning"))
    try:
        result = session(root, key, profile, "The owner answered:\n" + joined + "\n\nContinue the work with these "
                         "answers; return in questions only what is still open.", resume=value["analysis_session"])
        cards.update(root, key, lambda card: card.pop("held_answers", None) and None)
        return settle(root, key, profile, result)
    except BaseException:
        # A failed check already failed the task on purpose; anything else leaves the question open for another answer.
        cards.update(root, key, lambda card: card.update(state="stopped") if card["state"] == "planning" else None)
        raise


def reopen(root, key, profile, text):
    """The owner continues finished work, or work whose check failed: the same session gets the new instruction, and
    the check runs again."""
    value = cards.read(root, key)
    before = value["state"]
    if before not in ("done", "failed") or not value.get("analysis_session"):
        raise FridayError("invalid_state", "Task {key} has no finished analysis to reopen.", key=key)
    cards.update(root, key, lambda card: card.update(state="planning"))
    cards.record(root, key, "analysis_reopened")
    try:
        result = session(root, key, profile, "The owner reopens your finished work:\n" + text + "\n\nDo this work; "
                         "return in questions only what needs the owner.", resume=value["analysis_session"])
    except BaseException:
        cards.update(root, key, lambda card: card.update(state=before) if card["state"] == "planning" else None)
        raise
    return settle(root, key, profile, result)


def session(root, key, profile, prompt, resume=None):
    return plan.project_session(root, key, profile, prompt, resume=resume, role="plan", schema=SCHEMA,
                                session_field="analysis_session", disallowed=[MEMORY_WRITES])


def settle(root, key, profile, result, retries=1):
    """Questions stop the task for the owner; a finished session must pass the project's check, which it gets one
    chance to fix."""
    for attempt in range(retries + 1):
        report = result.structured
        if report["questions"]:
            cards.update(root, key, lambda value: value.update(state="stopped", questions=report["questions"],
                                                               analysis_summary=report["summary"]))
            cards.record(root, key, "questions", questions=report["questions"])
            return report
        failure = check(root, key, profile)
        if failure is None:
            def finish(value):
                value.pop("questions", None)
                value.update(state="done", analysis_summary=report["summary"])
            cards.update(root, key, finish)
            cards.record(root, key, "analysis_done")
            return report
        cards.record(root, key, "analysis_check_failed", output=failure)
        if attempt == retries:
            cards.update(root, key, lambda value: value.update(state="failed", analysis_summary=report["summary"]))
            raise FridayError("check_failed", "The project's check fails after the analysis:\n{output}", output=failure)
        result = session(root, key, profile, "The project's check fails on your work. Fix every problem it reports and "
                         "return the summary again:\n" + failure, resume=result.session)


def check(root, key, profile):
    """The output tail of a failed analysis_check, or None when it passes or the profile has none."""
    command = profile["project"].get("analysis_check")
    if not command:
        return None
    worktree = cards.read(root, key)["worktree"]
    argv = [part.replace("{key}", key).replace("{worktree}", worktree) for part in command]
    try:
        done = subprocess.run(argv, cwd=worktree, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if done.returncode == 0:
        return None
    return ((done.stdout or "") + (done.stderr or "")).strip()[-CHECK_TAIL:] or "exit code %d" % done.returncode


def documents(value, profile):
    """Where the project's command writes the documents, when the profile says so."""
    folder = (profile.get("project") or {}).get("analysis_dir")
    return folder.replace("{key}", value["key"]).replace("{worktree}", value.get("worktree") or "") if folder else None


def is_analysis(value):
    return value.get("kind") == "analysis" or "analysis_session" in value


def render(root, key, profile):
    """What the session reported, in the owner's language, and the questions it waits on."""
    from . import i18n, status, translate
    from .i18n import _, ngettext
    value = cards.read(root, key)
    t = translate.into(root, key, profile, i18n.language())
    # A running turn still holds the questions it answers; they wait for the owner only on a stopped card.
    questions = (value.get("questions") or []) if value["state"] == "stopped" else []
    t.prefetch([value.get("analysis_summary") or ""] + questions)
    lines = ["%s — %s" % (key, status.card_state_name(value))]
    folder = documents(dict(value, key=key), profile)
    if folder:
        lines.append(_("Documents: {path}", path=folder))
    if questions:
        lines.append(ngettext("{n} question waits for the owner.", "{n} questions wait for the owner.", len(questions)))
    held = value.get("held_answers") or []
    if held:
        lines.append(ngettext("{n} answer held; the next answer without --hold sends it.",
                              "{n} answers held; the next answer without --hold sends them.", len(held)))
    lines += ["", t(value.get("analysis_summary") or "")]
    for question in questions:
        lines.append(_("Question for the owner: {question} (answer: friday decide {key} <name> \"text\")",
                       question=t(question), key=key))
    return "\n".join(lines)
