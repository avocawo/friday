"""Choose models for roles by tier and vendor (ADR 0002), and remember which vendors are out of quota."""
import datetime
import json
import os
import re
import time
from pathlib import Path

from . import credentials
from .errors import FridayError
from .locks import locked

TIERS = ("efficient", "strong", "frontier")
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
DRIVERS = ("claude", "codex")
VENDOR_ENDPOINTS = {"zai": "https://api.z.ai/api/anthropic"}
ROLES = ("plan", "plan-review", "develop", "review", "final-review", "blind-code", "browser")
# Which role's work each reviewing role checks.
REVIEWS = {"plan-review": "plan", "review": "develop", "final-review": "develop", "blind-code": "develop", "browser": "develop"}
# Code is reviewed only by frontier models; the browser check only needs to match its author.
CODE_REVIEWS = ("review", "final-review", "blind-code")
# Tier preference when the profile gives no order for a role.
DEFAULT_TIERS = {"develop": ("strong", "efficient", "frontier")}
QUOTA_FALLBACK = 3600


def rank(tier):
    return TIERS.index(tier)


def level(entry):
    """Comparable strength: tier first, then the optional strength within the tier (Fable above Opus)."""
    return rank(entry["tier"]), entry.get("strength", 0)


def catalog(profile):
    models = profile.get("models")
    if not isinstance(models, dict) or not models:
        raise FridayError("invalid_profile", "Profile requires a models catalog.")
    result = {}
    for name, entry in models.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("model"), str) or not entry["model"]:
            raise FridayError("invalid_profile", "Model %s requires a model id." % name)
        if entry.get("tier") not in TIERS:
            raise FridayError("invalid_profile", "Model %s requires a tier: %s." % (name, ", ".join(TIERS)))
        if entry.get("driver") not in DRIVERS or not isinstance(entry.get("vendor"), str) or not entry["vendor"]:
            raise FridayError("invalid_profile", "Model %s requires a driver (%s) and a vendor." % (name, ", ".join(DRIVERS)))
        if entry.get("effort") is not None and entry["effort"] not in EFFORTS:
            raise FridayError("invalid_profile", "Model %s effort must be one of: %s." % (name, ", ".join(EFFORTS)))
        if not isinstance(entry.get("scarce", False), bool):
            raise FridayError("invalid_profile", "Model %s scarce must be true or false." % name)
        strength = entry.get("strength", 0)
        if not isinstance(strength, int) or isinstance(strength, bool):
            raise FridayError("invalid_profile", "Model %s strength must be an integer." % name)
        price = entry.get("price")
        if price is not None and (not isinstance(price, dict) or any(not isinstance(value, (int, float)) for value in price.values())):
            raise FridayError("invalid_profile", "Model %s price must map token kinds to USD per million." % name)
        result[name] = dict(entry, name=name)
    preferences = profile.get("roles", {})
    if not isinstance(preferences, dict) or any(role not in ROLES for role in preferences):
        raise FridayError("invalid_profile", "roles may only name: " + ", ".join(ROLES))
    for role, names in preferences.items():
        if not isinstance(names, list) or any(name not in result for name in names):
            raise FridayError("invalid_profile", "roles.%s must list models from the catalog." % role)
    return result


def candidates(profile, role, available):
    """Models listed for the role come first, in their order; the rest of the catalog follows by tier preference."""
    models = catalog(profile)
    listed = [models[name] for name in profile.get("roles", {}).get(role, [])]
    tiers = DEFAULT_TIERS.get(role, ("frontier", "strong", "efficient"))
    rest = sorted((entry for entry in models.values() if entry not in listed), key=lambda entry: tiers.index(entry["tier"]))
    return [entry for entry in listed + rest if available(entry["vendor"])]


def allowed(role, entry, author):
    """Hard rules: never weaker than the author; code reviews are frontier; the blind check is not the code author."""
    if role in CODE_REVIEWS and entry["tier"] != "frontier":
        return False
    if role == "blind-code" and author is not None and entry["model"] == author["model"]:
        return False
    return author is None or level(entry) >= level(author)


def closeness(role, entry, author, planner, used=()):
    """Soft rules as a sort key, best first: avoid self-review, then reusing a scarce model the task already uses,
    then a same-vendor reviewer (or the plan's author on the blind check). (0, 0, 0) means every soft rule holds."""
    own = author is not None and entry["model"] == author["model"]
    reused = bool(entry.get("scarce")) and entry["model"] in used
    near = (author is not None and entry["vendor"] == author["vendor"]) or (
        role == "blind-code" and planner is not None and entry["model"] == planner["model"])
    return int(own), int(reused), int(near)


def note_for(role, entry, author, planner, value):
    own, reused, near = value
    if own:
        return "%s: %s проверяет собственную работу, другой модели не ниже автора нет" % (role, entry["model"])
    if reused:
        return "%s: %s используется в задаче повторно, других подходящих моделей нет" % (role, entry["model"])
    if near and role == "blind-code" and planner is not None and entry["model"] == planner["model"]:
        return "blind-code: кроме автора плана %s подходящих моделей нет" % entry["model"]
    if near:
        return "%s: другой вендор недоступен, %s проверяет работу %s того же вендора %s" % (
            role, entry["model"], author["model"], entry["vendor"])
    return None


def choose(profile, role, author=None, available=lambda vendor: True, planner=None, used=()):
    """Pick a model for a role. Hard rules raise when unmet; soft rules return a note instead."""
    options = [entry for entry in candidates(profile, role, available) if allowed(role, entry, author)]
    if not options:
        raise FridayError("cast_impossible", "No available model satisfies the rules for " + role + ".")
    entry = min(options, key=lambda item: closeness(role, item, author, planner, used))
    return entry, note_for(role, entry, author, planner, closeness(role, entry, author, planner, used))


def cast(profile, available=lambda vendor: True):
    """The role → model layout fixed on the card before the plan is approved.

    The planner is the first candidate of the top tier whose plan gets the best-placed reviewer: when no other vendor
    can review the strongest planner, a slightly weaker planner reviewed by the stronger model beats self-review."""
    best = None
    planners = candidates(profile, "plan", available)
    for planner in planners:
        if planner["tier"] != planners[0]["tier"]:
            continue  # trade a stronger planner only for another model of the same tier
        try:
            review, _ = choose(profile, "plan-review", planner, available)
        except FridayError:
            continue
        value = closeness("plan-review", review, planner, None)
        if best is None or value < best[0]:
            best = (value, planner)
        if value == (0, 0, 0):
            break
    if best is None:
        raise FridayError("cast_impossible", "No available model satisfies the rules for plan-review.")
    roles, notes = {"plan": best[1]}, []
    roles["develop"], _ = choose(profile, "develop", available=available)
    for role, author in REVIEWS.items():
        used = {entry["model"] for entry in roles.values()}
        roles[role], note = choose(profile, role, roles[author], available, planner=roles["plan"], used=used)
        if note:
            notes.append(note)
    return {role: public(entry) for role, entry in roles.items()}, notes


def public(entry):
    return {key: entry[key] for key in ("name", "model", "vendor", "tier", "strength", "scarce", "driver", "effort") if key in entry}


def executor(profile, layout, complexity="clear", failures=0, previous=None, available=lambda vendor: True):
    """The executor of a milestone: the cast one for clear work, frontier for complex work, a tier up after two failures."""
    models = catalog(profile)
    current = models[(previous or layout["develop"])["name"]]
    floor = rank("frontier") if complexity == "complex" else rank(current["tier"])
    if failures >= 2:
        floor = max(floor, min(rank(current["tier"]) + 1, rank("frontier")))
    if rank(current["tier"]) >= floor and available(current["vendor"]):
        return public(current), None
    options = [entry for entry in candidates(profile, "develop", available) if rank(entry["tier"]) >= floor]
    if not options:
        raise FridayError("cast_impossible", "No available executor of tier %s or higher." % TIERS[floor])
    chosen = min(options, key=lambda entry: rank(entry["tier"]))
    if rank(current["tier"]) >= floor:
        reason = "квота вендора"
    elif failures >= 2:
        reason = "два провала подряд"
    else:
        reason = "сложная веха"
    return public(chosen), "исполнитель %s → %s (%s), новая сессия" % (current["model"], chosen["model"], reason)


def reviewer(profile, layout, role, author, available=lambda vendor: True, planner=None):
    """The reviewer for actual work: the cast one unless the real author outranks it, shares its vendor or is it."""
    planned = catalog(profile)[layout[role]["name"]]
    planner = planner or layout.get("plan")
    used = {entry["model"] for name, entry in layout.items() if name != role}
    if (allowed(role, planned, author) and available(planned["vendor"])
            and closeness(role, planned, author, planner, used) == (0, 0, 0)):
        return public(planned), None
    chosen, note = choose(profile, role, author, available, planner, used)
    if chosen["name"] == planned["name"]:
        return public(chosen), note
    return public(chosen), note or "%s: %s заменён на %s для работы %s" % (role, planned["model"], chosen["model"], author["model"])


def environment(entry, profile):
    """Environment for the agent process: vendor endpoint and its token, never other credentials."""
    definition = catalog(profile)[entry["name"]]
    env = {}
    if definition["vendor"] in VENDOR_ENDPOINTS:
        token = credentials.token({"token_env": definition.get("token_env", "FRIDAY_" + definition["vendor"].upper() + "_TOKEN"),
                                   "token_file": definition.get("token_file"), "token_key": definition.get("token_key")},
                                  "FRIDAY_" + definition["vendor"].upper() + "_TOKEN")
        env = {"ANTHROPIC_BASE_URL": VENDOR_ENDPOINTS[definition["vendor"]], "ANTHROPIC_AUTH_TOKEN": token}
    return env


def estimate(entry, profile, usage):
    """USD for a run from the catalog price when the vendor reports none; None without a price."""
    price = catalog(profile)[entry["name"]].get("price")
    if not price:
        return None
    return sum(usage.get(kind, 0) * price.get(kind, 0) for kind in ("input", "output", "cache_read", "cache_write")) / 1e6


def quota_path(root):
    return Path(root) / "quota.json"


def quotas(root):
    try:
        value = json.loads(quota_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def reset_time(error, limits, now):
    if isinstance(limits, dict) and isinstance(limits.get("resetsAt"), (int, float)):
        return float(limits["resetsAt"])
    match = re.search(r"(?:at|until)\s+([A-Z][a-z]{2})[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)", error or "")
    if match:
        month, day, year, hour, minute, half = match.groups()
        moment = datetime.datetime.strptime("%s %s %s %s:%s %s" % (month, day, year, hour, minute, half), "%b %d %Y %I:%M %p")
        return moment.timestamp()
    return now + QUOTA_FALLBACK


def exhausted(root, vendor, error="", limits=None, now=None):
    """Record that a vendor's quota ran out, with the time it comes back."""
    now = now or time.time()
    until = reset_time(error, limits, now)
    with locked(root, "quota"):
        value = quotas(root)
        value[vendor] = {"until": until, "reason": (error or "")[:300], "at": now}
        quota_path(root).parent.mkdir(parents=True, exist_ok=True)
        temporary = quota_path(root).with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        os.replace(temporary, quota_path(root))
    return until


def availability(root, now=None):
    """A predicate for choose/cast: False for vendors whose quota has not come back yet."""
    now = now or time.time()
    blocked = {vendor for vendor, item in quotas(root).items() if isinstance(item, dict) and item.get("until", 0) > now}
    return lambda vendor: vendor not in blocked
