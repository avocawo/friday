"""JSON contracts shared by model adapters."""
import json
from pathlib import Path


def environment_failure(evidence):
    try:
        value = json.loads(Path(evidence).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return (isinstance(value, dict) and value.get("verdict") == "fail" and
            isinstance(value.get("findings"), list) and any(isinstance(item, str) and item.startswith("ENVIRONMENT-UNAVAILABLE:") for item in value["findings"]))


def validate_review(value, strict=False, observed=False):
    if not isinstance(value, dict) or value.get("verdict") not in ("pass", "fail"):
        raise ValueError("Review must return a pass or fail verdict.")
    verdict = value["verdict"]
    findings = value.get("findings", None if strict else [])
    if not isinstance(findings, list) or (verdict == "pass" and findings) or (verdict == "fail" and not findings):
        raise ValueError("Review findings contradict the verdict.")
    evidence = value.get("evidence")
    if strict and not isinstance(evidence, list):
        raise ValueError("Review must return an evidence list.")
    if observed and verdict == "pass" and (not isinstance(evidence, list) or not evidence):
        raise ValueError("A behavioral pass requires observed evidence.")
    return verdict


def review():
    return {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]},
            "findings": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}}},
            "required": ["verdict", "findings", "evidence"], "additionalProperties": False}


def plan():
    assignment = {"type": "object", "properties": {"id": {"type": "string"}, "agent": {"type": "string"},
                  "role": {"type": "string", "enum": ["research", "develop", "review", "blind-code", "blind-behavior"]},
                  "brief": {"type": "string"}, "skills": {"type": "array", "items": {"type": "string"}},
                  "depends_on": {"type": "array", "items": {"type": "string"}}},
                  "required": ["id", "agent", "role", "brief", "skills", "depends_on"], "additionalProperties": False}
    stage = {"type": "object", "properties": {"id": {"type": "string"}, "brief": {"type": "string"},
             "criteria": {"type": "array", "items": {"type": "string"}},
             "depends_on": {"type": "array", "items": {"type": "string"}},
             "files": {"type": "array", "items": {"type": "string"}}, "max_added_lines": {"type": "integer"},
             "crew": {"type": "array", "items": assignment}},
             "required": ["id", "brief", "criteria", "depends_on", "files", "max_added_lines", "crew"], "additionalProperties": False}
    return {"type": "object", "properties": {"stages": {"type": "array", "items": stage},
            "questions": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "suggested_issues": {"type": "array", "items": {"type": "string"}}},
            "required": ["stages", "questions", "assumptions", "suggested_issues"], "additionalProperties": False}
