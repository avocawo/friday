"""JSON contracts shared by model adapters."""
from collections import Counter
import json
from pathlib import Path


def cosmetic_review(evidence):
    try:
        value = json.loads(Path(evidence).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        return False
    findings = value.get('findings') if isinstance(value, dict) else None
    return isinstance(findings, list) and bool(findings) and all(isinstance(item, str) and item.startswith('COSMETIC:') for item in findings)


def validate_review(value, strict=False, observed=False, criteria=None):
    if not isinstance(value, dict) or value.get("verdict") not in ("pass", "fail", "unverifiable"):
        raise ValueError("Review must return pass, fail or unverifiable.")
    verdict = value["verdict"]
    findings = value.get("findings", None if strict else [])
    if not isinstance(findings, list) or any(not isinstance(item, str) or not item.strip() for item in findings) or (verdict == "pass" and findings) or (verdict == "fail" and not findings):
        raise ValueError("Review findings contradict the verdict.")
    evidence = value.get("evidence")
    if (strict and not isinstance(evidence, list)) or (evidence is not None and (not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence))):
        raise ValueError("Review must return an evidence list.")
    if observed and verdict == "pass" and (not isinstance(evidence, list) or not evidence):
        raise ValueError("A behavioral pass requires observed evidence.")
    results = value.get('criteria_results')
    if results is not None:
        if not isinstance(results, list) or any(not isinstance(item, dict) or set(item) != {'criterion', 'status', 'evidence'} or
            not isinstance(item['criterion'], str) or item['status'] not in ('pass', 'fail', 'unverifiable') or
            not isinstance(item['evidence'], list) or any(not isinstance(e, str) or not e.strip() for e in item['evidence']) or (item['status'] == 'pass' and not item['evidence']) for item in results):
            raise ValueError('Review criterion results are invalid.')
        if criteria is not None and Counter(item['criterion'] for item in results) != Counter(criteria):
            raise ValueError('Review must cover exactly the assigned criteria.')
        expected = 'fail' if any(item['status'] == 'fail' for item in results) else 'unverifiable' if any(item['status'] == 'unverifiable' for item in results) else 'pass'
        if results and verdict != expected:
            raise ValueError('Review verdict contradicts criterion results.')
    return verdict


def review():
    return {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["pass", "fail", "unverifiable"]},
            "findings": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "criteria_results": {"type": "array", "items": {"type": "object", "properties": {
                "criterion": {"type": "string"}, "status": {"type": "string", "enum": ["pass", "fail", "unverifiable"]},
                "evidence": {"type": "array", "items": {"type": "string"}}},
                "required": ["criterion", "status", "evidence"], "additionalProperties": False}}},
            "required": ["verdict", "findings", "evidence", "criteria_results"], "additionalProperties": False}


def plan():
    assignment = {"type": "object", "properties": {"id": {"type": "string"}, "agent": {"type": "string"},
                  "role": {"type": "string", "enum": ["research", "develop", "review", "blind-code", "blind-behavior"]},
                  "brief": {"type": "string"}, "skills": {"type": "array", "items": {"type": "string"}},
                  "depends_on": {"type": "array", "items": {"type": "string"}}},
                  "required": ["id", "agent", "role", "brief", "skills", "depends_on"], "additionalProperties": False}
    stage = {"type": "object", "properties": {"id": {"type": "string"}, "brief": {"type": "string"},
             "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
             "criteria": {"type": "array", "items": {"type": "object", "properties": {
                 "text": {"type": "string"}, "checks": {"type": "array", "items": {"type": "string", "enum": ["code", "behavior"]}}},
                 "required": ["text", "checks"], "additionalProperties": False}},
             "depends_on": {"type": "array", "items": {"type": "string"}},
             "files": {"type": "array", "items": {"type": "string"}}, "max_added_lines": {"type": "integer"},
             "crew": {"type": "array", "items": assignment}},
             "required": ["id", "brief", "steps", "criteria", "depends_on", "files", "max_added_lines", "crew"], "additionalProperties": False}
    return {"type": "object", "properties": {"stages": {"type": "array", "items": stage},
            "questions": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "suggested_issues": {"type": "array", "items": {"type": "string"}}},
            "required": ["stages", "questions", "assumptions", "suggested_issues"], "additionalProperties": False}
