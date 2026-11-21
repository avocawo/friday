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


def start(root, key, profile, brief=None):
    """The first session of an analysis task: the profile's analysis_command for this key, with the owner's brief when
    the task has no ticket."""
    layout, notes = models.cast(profile, models.availability(root))
    cards.update(root, key, lambda value: value.update(cast=layout, state="planning", **({"task_statement": brief} if brief else {})))
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


def answer(root, key, profile, text):
    """The owner answered: the same session continues with the answer."""
    value = ensure_waiting(root, key)
    cards.update(root, key, lambda card: card.update(state="planning"))
    try:
        result = session(root, key, profile, "The owner answered:\n" + text + "\n\nContinue the work with this answer; "
                         "return in questions only what is still open.", resume=value["analysis_session"])
        return settle(root, key, profile, result)
    except BaseException:
        # A failed check already failed the task on purpose; anything else leaves the question open for another answer.
        cards.update(root, key, lambda card: card.update(state="stopped") if card["state"] == "planning" else None)
        raise


def session(root, key, profile, prompt, resume=None):
    return plan.project_session(root, key, profile, prompt, resume=resume, role="plan", schema=SCHEMA,
                                session_field="analysis_session")


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


def render(root, key, profile):
    """What the session reported, in the owner's language, and the questions it waits on."""
    from . import i18n, status, translate
    from .i18n import _
    value = cards.read(root, key)
    t = translate.into(root, key, profile, i18n.language())
    questions = value.get("questions") or []
    t.prefetch([value.get("analysis_summary") or ""] + questions)
    lines = ["%s — %s" % (key, status.card_state_name(value)), "", t(value.get("analysis_summary") or "")]
    for question in questions:
        lines.append(_("Question for the owner: {question} (answer: friday decide {key} <name> \"text\")",
                       question=t(question), key=key))
    return "\n".join(lines)
