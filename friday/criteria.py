"""Assign acceptance criteria to the checks that can observe them."""
from .errors import FridayError

KINDS = ('code', 'behavior')


def normalize(items):
    if not isinstance(items, list) or not items:
        raise FridayError('invalid_plan', 'A stage needs acceptance criteria.')
    texts, checks = [], []
    for item in items:
        if isinstance(item, str):
            text, roles = item, list(KINDS)
        elif isinstance(item, dict) and set(item) == {'text', 'checks'}:
            text, roles = item['text'], item['checks']
        else:
            raise FridayError('invalid_plan', 'A criterion must have text and checks.')
        if not isinstance(text, str) or not text.strip():
            raise FridayError('invalid_plan', 'Criteria must be non-empty.')
        if not isinstance(roles, list) or not roles or any(not isinstance(role, str) or role not in KINDS for role in roles) or len(set(roles)) != len(roles):
            raise FridayError('invalid_plan', 'Criterion checks must contain code and/or behavior.')
        texts.append(text)
        checks.append(roles)
    return texts, checks


def for_role(stage, role):
    texts = stage['criteria']
    kinds = stage.get('criterion_checks', [list(KINDS) for _ in texts])
    if not isinstance(kinds, list) or len(kinds) != len(texts) or any(
        not isinstance(roles, list) or not roles or any(not isinstance(kind, str) or kind not in KINDS for kind in roles)
        or len(set(roles)) != len(roles) for roles in kinds
    ):
        raise FridayError('invalid_plan', 'Criterion assignments do not match the plan.')
    kind = {'blind-code': 'code', 'blind-behavior': 'behavior'}.get(role)
    return [text for text, roles in zip(texts, kinds) if kind is None or kind in roles]


def review_roles(stage):
    return tuple(role for role in ('review', 'blind-code', 'blind-behavior') if for_role(stage, role))
