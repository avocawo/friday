"""Durable decisions remain in force until the owner changes them."""
from . import state
from .errors import FridayError


def set_decision(root, key, name, value):
    state.validate_key(name)
    if not value.strip():
        raise FridayError("invalid_decision", "Decision cannot be empty.")
    def change(card):
        decisions = card.setdefault("decisions", {})
        if decisions.get(name, {}).get("value") == value:
            return decisions[name]
        revision = card.get("decision_revision", 0) + 1
        card["decision_revision"] = revision
        decision = {"value": value, "revision": revision, "at": state.now()}
        decisions[name] = decision
        reopened = []
        for stage_name, stage in card["stages"].items():
            stage.pop("automation", None)
            if stage.get("status") == "closed":
                stage["status"] = "open"
                reopened.append(stage_name)
        card["events"].append({"type": "decision", "name": name, "revision": revision,
                               "reopened": reopened, "at": state.now()})
        return decision
    return state.update(root, key, change)
