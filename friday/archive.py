"""Archive a detached task card without deleting its local evidence."""
import contextlib
import json
import os
import shutil
import uuid
from pathlib import Path

from . import journal, state
from .errors import FridayError
from .locks import locked


def relocate(value, old, new):
    if isinstance(value, str):
        if value.startswith(old + os.sep):
            return new + value[len(old):]
        if os.path.isabs(value):
            try:
                relative = Path(value).resolve().relative_to(Path(old).resolve())
                return str(Path(new) / relative)
            except (OSError, ValueError):
                pass
        return value
    if isinstance(value, list):
        return [relocate(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: relocate(item, old, new) for key, item in value.items()}
    return value


def archive(root, key):
    key = state.validate_key(key)
    with contextlib.ExitStack() as stack:
        for resource in ('mission-run:' + key, 'mission:' + key, 'project:' + key, 'watch:' + key, 'watch-local:' + key):
            stack.enter_context(locked(root, resource, timeout=1))
        initial = state.read(root, key)
        for stage in sorted(initial['stages']):
            for role in ('autopilot', 'dispatch', 'worker'):
                stack.enter_context(locked(root, role + ':' + key + ':' + stage, timeout=1))
        stack.enter_context(locked(root, 'task:' + key, timeout=1))
        card = state.read(root, key)
        if set(card['stages']) != set(initial['stages']):
            raise FridayError('stale_evidence', 'The task plan changed before archiving.')
        if card.get('worktree') or card.get('worktree_intent') or card.get('cleanup_intent'):
            raise FridayError('worktree_required', 'Clean up or resolve the task worktree before archiving.')
        delivery = card.get('autonomous_delivery', {})
        if delivery.get('push') or delivery.get('create_started') or delivery.get('pending_review'):
            raise FridayError('delivery_pending', 'Resolve the pending delivery before archiving.')
        if any(item.get('status') == 'running' for item in card.get('deliveries', {}).values() if isinstance(item, dict)):
            raise FridayError('delivery_pending', 'A delivery is still running.')
        source = state.task_path(root, key).parent
        if source.is_symlink() or any(path.is_symlink() for path in source.rglob('*')):
            raise FridayError('unsafe_archive', 'Task state contains a symbolic link.')
        parent = Path(root) / 'archived-tasks'
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / (key + '-' + uuid.uuid4().hex)
        temporary = parent / ('.' + destination.name)
        try:
            shutil.copytree(source, temporary)
            archived = relocate(card, str(source), str(destination))
            archived['events'].append({'type': 'archived', 'at': state.now()})
            (temporary / 'state.json').write_text(json.dumps(archived, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            (temporary / 'journal.md').write_text(journal.render(archived), encoding='utf-8')
            os.rename(temporary, destination)
            os.rename(source, destination / 'original')
        except OSError as exc:
            raise FridayError('archive_failed', 'Could not archive task state: ' + str(exc))
        return {'archive': str(destination)}
