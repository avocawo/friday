"""Record evidence-backed objections without changing the approved plan."""
import json
from pathlib import Path

from . import gate, profile, state
from .errors import FridayError
from .locks import locked


def validate(worktree, item):
    fields = {'finding', 'rule_path', 'quote', 'precedent', 'reason'}
    if not isinstance(item, dict) or set(item) != fields or any(not isinstance(item[k], str) or not item[k].strip() or len(item[k]) > 4000 for k in fields):
        raise FridayError('invalid_dispute', 'A dispute needs a finding, rule, exact quote, precedent and reason.')
    root = Path(worktree).resolve()
    for field in ('rule_path', 'precedent'):
        relative = Path(item[field])
        target = (root / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(root) or not target.is_file():
            raise FridayError('invalid_dispute', 'A dispute path must be a file inside the worktree.')
        gate.git(worktree, 'ls-files', '--error-unmatch', '--', str(relative))
    if not item['rule_path'].endswith('.md'):
        raise FridayError('invalid_dispute', 'A rule must be a tracked Markdown document.')
    if item['quote'] not in (root / item['rule_path']).read_text(encoding='utf-8'):
        raise FridayError('invalid_dispute', 'The rule quote was not found verbatim.')
    return dict(item)


def from_developer(worktree, output, feedback):
    try:
        result = json.loads(output)
    except ValueError:
        return []
    if not isinstance(result, dict) or 'disputes' not in result:
        return []
    items = result['disputes']
    if not isinstance(items, list) or len(items) > 10:
        raise FridayError('invalid_dispute', 'Disputes must be a bounded list.')
    try:
        previous = json.loads(feedback or '')
    except ValueError:
        previous = {}
    findings = previous.get('findings', []) if isinstance(previous, dict) else []
    checked = [validate(worktree, item) for item in items]
    if any(item['finding'] not in findings for item in checked):
        raise FridayError('invalid_dispute', 'Disputes must refer to an actual review finding.')
    return checked


def record(root, key, name, item):
    initial = state.read(root, key)
    selected = profile.load(initial['profile'])
    target = initial.get('worktree', selected['repository'])
    with locked(root, 'resource:branch:' + target, timeout=30):
        checked = validate(target, item)
        head = gate.git(target, 'rev-parse', 'HEAD').decode().strip()
        def update(card):
            if card.get('worktree', selected['repository']) != target:
                raise FridayError('stale_evidence', 'The worktree changed before recording the dispute.')
            stage = card['stages'].get(name)
            if not stage or stage.get('status') != 'open':
                raise FridayError('invalid_stage', 'A dispute requires an open stage.')
            stage.setdefault('review_disputes', []).append(dict(checked, head=head))
            card['events'].append({'type': 'review_disputed', 'stage': name, 'at': state.now()})
        state.update(root, key, update)
    return {'recorded': True, 'head': head}
