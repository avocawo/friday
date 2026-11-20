"""Project guidance for the roles below the plan: what the plan's rules cannot carry on their own."""
from pathlib import Path

PRECEDENT_COMMAND = str(Path(__file__).resolve().parent.parent / "bin" / "friday-precedent")


def text(profile, card, commands=False):
    """Engineering policy, project lessons, the base revision as precedent and the map of the project's documents."""
    parts = []
    if profile.get("engineering_policy"):
        parts.append("Engineering policy of the project:\n" + "\n".join("- " + item for item in profile["engineering_policy"]))
    if profile.get("project_lessons"):
        parts.append("Project lessons (defects this project's reviewers keep finding; check the change against each):\n" +
                     "\n".join("- " + item for item in profile["project_lessons"]))
    if card.get("base"):
        search = (" Search it with %s %s <pattern> [<path>...]." % (PRECEDENT_COMMAND, card["base"])) if commands else ""
        parts.append("Base revision %s: its code is the project's existing practice; code added on this task's branch is "
                     "not a precedent.%s" % (card["base"], search))
    if card.get("doc_map"):
        parts.append("Project documents (open the section a rule or a decision needs; do not read whole documents):\n" +
                     "\n".join("- %s: %s" % (item["path"], item["covers"]) for item in card["doc_map"]))
    return "".join("\n\n" + part for part in parts)
