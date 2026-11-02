"""Validate a staged plan and dispatch bounded briefs to configured model adapters."""
import contextlib
import hashlib
import json
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import criteria as criterion_rules, crew, disputes, gate, isolated_preview, isolation, profile, runners, schemas, state, worktree as worktrees
from .errors import FridayError
from .locks import locked


def load_plan(root, key, document, replace=False, expected_decision_revision=None, planning=None):
    try:
        plan = json.loads(Path(document).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FridayError("invalid_plan", str(exc))
    items = plan.get("stages") if isinstance(plan, dict) else None
    if not isinstance(items, list) or not items:
        raise FridayError("invalid_plan", "Plan requires a non-empty stages array.")
    stages = {}
    proposed_crews = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise FridayError("invalid_plan", "Every stage requires an id.")
        name = state.validate_key(item["id"])
        if name in stages:
            raise FridayError("invalid_plan", "Duplicate stage: " + name)
        if not isinstance(item.get("brief"), str) or not item["brief"].strip():
            raise FridayError("invalid_plan", "Stage requires a brief: " + name)
        criteria, dependencies = item.get("criteria"), item.get("depends_on", [])
        structured = isinstance(criteria, list) and any(isinstance(entry, dict) for entry in criteria)
        criteria, criterion_checks = criterion_rules.normalize(criteria)
        if not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies):
            raise FridayError("invalid_plan", "depends_on must be a list of stage ids.")
        files = item.get("files", [])
        if not isinstance(files, list) or len(files) > 10 or any(not isinstance(x, str) or not x or x.startswith("/") or ".." in Path(x).parts for x in files):
            raise FridayError("invalid_plan", "Stage files must be at most 10 relative paths.")
        added = item.get("max_added_lines", 500)
        if not isinstance(added, int) or isinstance(added, bool) or not 0 <= added <= 500:
            raise FridayError("invalid_plan", "Stage added-line estimate must be between 0 and 500.")
        steps = item.get('steps', [])
        if not isinstance(steps, list) or len(steps) > 10 or any(not isinstance(step, str) or not step.strip() for step in steps):
            raise FridayError('invalid_plan', 'Stage steps must be at most 10 non-empty strings.')
        stages[name] = {"status": "open", "brief": item["brief"], "criteria": criteria, "depends_on": dependencies}
        stages[name]["criterion_checks"] = criterion_checks
        if steps:
            stages[name]['steps'] = steps
        if structured:
            stages[name]['structured_criteria'] = True
        if files:
            stages[name]["files"] = files
        if "crew" in item:
            proposed_crews[name] = item["crew"]
    visited, visiting = set(), set()
    def visit(name):
        if name in visiting:
            raise FridayError("invalid_plan", "Cyclic stage dependency: " + name)
        if name not in stages:
            raise FridayError("invalid_plan", "Unknown stage dependency: " + name)
        if name in visited:
            return
        visiting.add(name)
        for dep in stages[name]["depends_on"]:
            visit(dep)
        visiting.remove(name)
        visited.add(name)
    for name in stages:
        visit(name)
    def change(card):
        if expected_decision_revision is not None and card.get("decision_revision", 0) != expected_decision_revision:
            raise FridayError("stale_evidence", "A decision changed while planning.")
        if card["stages"] and not replace:
            raise FridayError("plan_exists", "The task already has stages; replacing them would lose evidence.")
        selected = profile.load(card["profile"])
        for name, proposed in proposed_crews.items():
            stages[name]["crew"] = crew.validate(proposed, selected)
            stages[name]["crew_binding"] = crew.catalog_fingerprint(stages[name]["crew"], selected)
        worktree = card.get("worktree", selected["repository"])
        base = card.get("plan_base") or gate.git(worktree, "rev-parse", "HEAD").decode().strip()
        gate.git(worktree, "merge-base", "--is-ancestor", base, "HEAD")
        if card["stages"]:
            card["events"].append({"type": "plan_replaced", "previous_stages": card["stages"], "at": state.now()})
        card["plan_revision"] = card.get("plan_revision", 0) + 1
        card["plan_policy"] = profile.policy_fingerprint(selected)
        card["plan_base"] = base
        card["plan_approved"] = card.get("mode") == "auto"
        card["stages"] = stages
        if planning is not None:
            card["planning_questions"] = planning["questions"]
            card["planning_assumptions"] = planning.get("assumptions", [])
            card["suggested_issues"] = planning["suggested_issues"]
            card["events"].append({"type": "planning", "model": planning["model"],
                                   "evidence": planning["evidence"], "at": state.now()})
        elif replace:
            card["planning_questions"] = []
            card["planning_assumptions"] = []
            card["suggested_issues"] = []
        card["events"].append({"type": "plan_loaded", "at": state.now()})
    return state.update(root, key, change)


def unanswered(card):
    prefix = "plan-" + str(card.get("plan_revision", 0)) + "-question-"
    return [{"name": prefix + str(index + 1), "question": question}
            for index, question in enumerate(card.get("planning_questions", []))
            if prefix + str(index + 1) not in card.get("decisions", {})]


def approve_plan(root, key):
    def change(card):
        if not card.get("plan_base") or not card["stages"]:
            raise FridayError("invalid_plan", "Load a plan before approving it.")
        card["plan_approved"] = True
        card["events"].append({"type": "plan_approved", "at": state.now()})
    return state.update(root, key, change)


def ready(card):
    return [name for name, stage in card["stages"].items() if stage.get("status") == "open" and
            all(card["stages"].get(dep, {}).get("status") == "closed" for dep in stage.get("depends_on", []))]


def review_diff(worktree, card, stage):
    base = stage.get("review_base", card.get("plan_base"))
    if not isinstance(base, str) or not re.fullmatch(r"[a-f0-9]{40,64}", base):
        raise FridayError("invalid_plan", "Review requires a valid base commit.")
    gate.git(worktree, "merge-base", "--is-ancestor", base, "HEAD")
    return gate.git(worktree, "diff", "--binary", base, "HEAD").decode("utf-8", "replace")


def redact_browser_text(text):
    # Browser observations can include URLs and authorization data. Do not forward
    # or persist recognizable credentials from the browser review.
    text = re.sub(r'(?im)\b(authorization|cookie|set-cookie)\s*:\s*[^\r\n]+', r'\1: [REDACTED]', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s,;"\']+', 'Bearer [REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[REDACTED]', text)
    text = re.sub(r'(?i)\b([\w-]*(?:token|authorization|password|secret|cookie|api[_-]?key))(["\']?\s*[:=]\s*["\']?)[^\s,;&"\']+', r'\1\2[REDACTED]', text)
    return text


def behavior_evidence(root, key, card, name, stage, head):
    required = criterion_rules.for_role(stage, 'blind-behavior')
    if not required:
        return None
    event = next((item for item in reversed(card['events']) if item.get('type') == 'model_run' and
                  item.get('stage') == name and item.get('role') == 'blind-behavior'), None)
    if not event or event.get('verdict') != 'pass' or any(
        field not in event or event[field] != value for field, value in
        (('head', head), ('decision_revision', card.get('decision_revision', 0)),
         ('plan_revision', card.get('plan_revision', 0)))
    ):
        return None
    try:
        directory = (state.task_path(root, key).parent / 'evidence').resolve()
        path = Path(event['evidence']).resolve()
        if path.parent != directory:
            return None
        content = path.read_bytes()
        if not isinstance(event.get('evidence_sha256'), str) or hashlib.sha256(content).hexdigest() != event['evidence_sha256']:
            return None
        answer = json.loads(content)
        if 'criteria_results' not in answer or schemas.validate_review(answer, observed=True, criteria=required) != 'pass':
            return None
    except (OSError, ValueError, TypeError, KeyError):
        return None
    # Never forward an observation that contained a recognizable credential:
    # a replacement marker cannot serve as independent behavioral evidence.
    def contains_redaction(value):
        if isinstance(value, str):
            return '[REDACTED]' in value or redact_browser_text(value) != value
        if isinstance(value, list):
            return any(contains_redaction(item) for item in value)
        if isinstance(value, dict):
            return any(contains_redaction(item) for item in value.values())
        return False
    if contains_redaction(answer) or any(redact_browser_text(item['criterion']) != item['criterion'] or
           any('[REDACTED]' in text or redact_browser_text(text) != text for text in item['evidence'])
           for item in answer['criteria_results']):
        return None
    results = [{'criterion': item['criterion'], 'status': item['status'],
             'evidence': [redact_browser_text(text) for text in item['evidence']]}
            for item in answer['criteria_results']]
    return results, {'evidence': str(path), 'sha256': hashlib.sha256(content).hexdigest()}


def current_gate_evidence(card, stage, selected, worktree, head):
    prior = stage.get('gate')
    command = selected.get('gate')
    # Only expose a constrained npm script name, which the reviewer can inspect
    # in package.json. Arbitrary command arguments may contain credentials.
    if not isinstance(prior, dict) or prior.get('status') != 'passed' or prior.get('exit_code') != 0 or prior.get('unchanged') is not True:
        return None
    event = next((event for event in reversed(card.get('events', [])) if event.get('type') == 'gate' and
                  card.get('stages', {}).get(event.get('stage')) is stage), None)
    if not event or event.get('status') != 'passed' or event.get('record_sha256') != gate.record_digest(prior):
        return None
    if not (isinstance(command, list) and len(command) == 3 and command[:2] == ['npm', 'run'] and
            isinstance(command[2], str) and re.fullmatch(r'[A-Za-z0-9:_-]{1,64}', command[2]) and
            not re.search(r'(?i)token|secret|password|credential|api.?key', command[2])):
        return None
    if any(field not in prior or prior[field] != value for field, value in
           (('head', head), ('decision_revision', card.get('decision_revision', 0)),
            ('plan_revision', card.get('plan_revision', 0)))):
        return None
    selected = dict(selected, repository=worktree)
    try:
        selected = gate.with_baseline(selected, card)
        actual_head, snapshot, _ = gate.snapshot(selected)
        image = gate.image_id(selected.get('gate_image', ''))
        definition = gate.definition(selected, image)
    except FridayError:
        return None
    if (actual_head, snapshot, definition) != (head, prior.get('snapshot'), prior.get('definition')):
        return None
    binding = gate.record_digest(prior)
    return {'status': 'passed', 'exit_code': 0, 'command': command, 'head': head,
            'snapshot': snapshot, 'definition': definition}, binding


def browser_test_context(value):
    if not isinstance(value, dict) or set(value) != {'mode', 'route', 'query_key', 'scenarios'} or value['mode'] != 'local_mock':
        raise FridayError('invalid_browser_context', 'Invalid browser test context.')
    route, key = value['route'], value['query_key']
    if not isinstance(route, str) or not re.fullmatch(r'/[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*){0,3}', route) or len(route) > 100 or not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9]{0,39}', key):
        raise FridayError('invalid_browser_context', 'Invalid local route or query key.')
    scenarios = value['scenarios']
    if not isinstance(scenarios, list) or len(scenarios) > 3 or any(not isinstance(item, str) or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+){0,3}', item) or len(item) > 40 for item in scenarios) or len(set(scenarios)) != len(scenarios):
        raise FridayError('invalid_browser_context', 'Invalid browser test scenarios.')
    if any(re.search(r'(?i)token|secret|password|auth|cookie|credential|apikey', item) for item in [route, key, *scenarios]):
        raise FridayError('invalid_browser_context', 'Credential-like context is not permitted.')
    return {'mode': 'local_mock', 'route': route, 'query_key': key, 'scenarios': list(scenarios)}


def invoke(root, key, name, role, feedback=None, require_owned=False, criteria_override=None, fallback_for=None, browser_context=None):
    if role not in ("develop", "review", "blind-code", "blind-behavior"):
        raise FridayError("invalid_role", "Unknown model role: " + role)
    if browser_context is not None:
        if role != 'blind-behavior':
            raise FridayError('invalid_browser_context', 'Browser test context is only for blind-behavior.')
        browser_context = browser_test_context(browser_context)
    initial = state.read(root, key)
    initial_profile = profile.load(initial["profile"])
    initial_worktree = initial.get("worktree", initial_profile["repository"])
    isolation.require_runner(initial_profile, initial_worktree)
    if role == "blind-behavior":
        isolation.require_browser(initial_profile)
    browser_lock = locked(root, "resource:browser:global", timeout=1800) if role == "blind-behavior" else contextlib.nullcontext()
    with locked(root, "worker:" + state.validate_key(key) + ":" + state.validate_key(name)), \
         locked(root, "resource:branch:" + initial_worktree, timeout=1800), browser_lock:
        card = state.read(root, key)
        if name not in ready(card):
            raise FridayError("stage_not_ready", "Stage is closed or its dependencies are not closed.")
        if role == "develop" and not card.get("plan_approved"):
            raise FridayError("plan_approval_required", "Approve the plan before development.")
        selected = profile.load(card["profile"])
        profile.verify_policy(selected, card)
        if card.get("worktree", selected["repository"]) != initial_worktree:
            raise FridayError("worktree_conflict", "The worktree changed before the runner started.")
        definitions = selected.get("runners", {})
        runner = definitions.get(role) if isinstance(definitions, dict) else None
        if not isinstance(runner, dict) or not isinstance(runner.get("model"), str):
            raise FridayError("invalid_profile", "Profile requires a model runner for " + role)
        command = runners.command(runner)
        developer = definitions.get("develop", {})
        if role != "develop":
            if not isinstance(developer, dict) or not isinstance(developer.get("model"), str) or not developer["model"]:
                raise FridayError("invalid_profile", "The developer model must be configured before review.")
            if runner["model"] == developer["model"]:
                raise FridayError("reviewer_not_independent", "Reviewer must use a different configured model.")
            previous = next((event for event in reversed(card["events"]) if event.get("type") == "model_run" and
                             event.get("stage") == name and event.get("role") == "develop"), None)
            if previous and runner["model"] == previous.get("model"):
                raise FridayError("reviewer_not_independent", "Reviewer must differ from the model that developed this stage.")
        stage = card["stages"][name]
        if browser_context is None and role == "blind-behavior" and stage.get("browser_context") is not None:
            browser_context = browser_test_context(stage.get("browser_context"))
        worktree = card.get("worktree", selected["repository"])
        if require_owned:
            if not card.get("worktree") or not card.get("branch"):
                raise FridayError("worktree_required", "The runner requires a Friday worktree.")
            worktrees.verify(selected["repository"], worktree, card["branch"])
        if role != "develop" and gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
            raise FridayError("dirty_tree", "Commit changes before running a review.")
        head = gate.git(worktree, "rev-parse", "HEAD").decode().strip()
        selected_criteria = criterion_rules.for_role(stage, role) if criteria_override is None else criteria_override
        if (criteria_override is not None and not fallback_for) or (fallback_for and (role != 'review' or fallback_for != 'blind-code' or selected_criteria != criterion_rules.for_role(stage, fallback_for))):
            raise FridayError('invalid_role', 'Invalid review fallback criteria.')
        request = {"role": role, "criteria": selected_criteria, "model": runner["model"]}
        if browser_context is not None:
            request['browser_test_context'] = {'mode': 'local_mock',
                'instruction': 'These are test conditions, not evidence. Local mock mutation does not create a network request; reopen the same item to check persistence. Report actual observations or unverifiable.',
                'scenario_paths': [browser_context['route'] + '?' + browser_context['query_key'] + '=' + value for value in browser_context['scenarios']]}
        if fallback_for:
            request['fallback_for'] = fallback_for
        if runner.get("vendor"):
            request["vendor"] = runner["vendor"]
        if role == "blind-behavior" and "behavior_test_policy" in selected:
            if not isinstance(selected["behavior_test_policy"], str):
                raise FridayError("invalid_profile", "behavior_test_policy must be a string.")
            request["behavior_test_policy"] = selected["behavior_test_policy"]
        if role == "blind-behavior" and card.get("preview_url"):
            request["preview_url"] = card["preview_url"]
        behavior_binding = None
        gate_binding = None
        if role in ("develop", "review"):
            request.update({"task": key, "stage": name, "brief": stage["brief"], "worktree": worktree,
                            "decisions": card.get("decisions", {}), "planning_assumptions": card.get("planning_assumptions", []),
                            "planning_questions": unanswered(card)})
            if stage.get('steps'):
                request['steps'] = stage['steps']
            if selected.get("engineering_policy"):
                request["engineering_policy"] = selected["engineering_policy"]
            if role == 'review' and stage.get('review_disputes'):
                request['review_disputes'] = stage['review_disputes']
            if role == 'review':
                verified = behavior_evidence(root, key, card, name, stage, head)
                if verified:
                    request['behavior_evidence'], behavior_binding = verified
                verified_gate = current_gate_evidence(card, stage, selected, worktree, head)
                if verified_gate:
                    request['gate_evidence'], gate_binding = verified_gate
        if role == "develop" and feedback:
            request["feedback"] = feedback
        if role in ("review", "blind-code"):
            if gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
                raise FridayError("dirty_tree", "Commit changes before reviewing the diff.")
            request["diff"] = review_diff(worktree, card, stage)
        with tempfile.TemporaryDirectory(prefix="friday-blind-") as isolated, contextlib.ExitStack() as stack:
            cwd = isolated if role in ("blind-code", "blind-behavior") else worktree
            environment = runners.environment(runner)
            if role == "blind-behavior":
                request["preview_url"] = stack.enter_context(isolated_preview.open_preview(selected, worktree))
                environment["FRIDAY_BROWSER_CONFIG"] = json.dumps(selected.get("browser", {}))
            try:
                result = subprocess.run(command, cwd=cwd, input=json.dumps(request, ensure_ascii=False), text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, env=environment)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FridayError("runner_unavailable", "Runner could not finish: " + type(exc).__name__)
        if result.returncode:
            detail = (result.stderr or "").strip()[-800:] if isinstance(result.stderr, str) else ""
            raise FridayError("runner_failed", "Model runner exited %s.%s" % (result.returncode, ("\n" + detail) if detail else ""))
        if role in ("develop", "review") and profile.load(card["profile"]).get("engineering_policy", []) != selected.get("engineering_policy", []):
            raise FridayError("stale_evidence", "Project engineering policy changed while the agent was running.")
        if role != "develop" and (gate.git(worktree, "rev-parse", "HEAD").decode().strip() != head or
                                  gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all")):
            raise FridayError("stale_evidence", "The worktree changed while the review was running.")
        snapshot = None
        challenges = []
        if role == "develop":
            if gate.git(worktree, "rev-parse", "HEAD").decode().strip() != head:
                raise FridayError("stale_evidence", "The developer changed HEAD; Friday cannot safely commit the result.")
            snapshot = worktrees.fingerprint(worktree)
            challenges = disputes.from_developer(worktree, result.stdout, feedback)
        verdict = None
        if role != "develop":
            try:
                answer = json.loads(result.stdout)
                if (stage.get('structured_criteria') or fallback_for) and 'criteria_results' not in answer:
                    raise ValueError('Review must report a result for each assigned criterion.')
                verdict = schemas.validate_review(answer, observed=role == "blind-behavior" or bool(fallback_for), criteria=selected_criteria)
            except ValueError as exc:
                raise FridayError("invalid_verdict", str(exc))
        evidence = state.task_path(root, key).parent / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        path = evidence / (uuid.uuid4().hex + ".txt")
        if role == 'blind-behavior':
            if any(redact_browser_text(item['criterion']) != item['criterion'] for item in answer.get('criteria_results', [])):
                raise FridayError('invalid_verdict', 'Browser criterion contains credential-like data; evidence cannot be safely recorded.')
            def redact(value):
                if isinstance(value, str):
                    return redact_browser_text(value)
                if isinstance(value, list):
                    return [redact(item) for item in value]
                if isinstance(value, dict):
                    return {key: ('[REDACTED]' if re.search(r'(?i)(token|authorization|password|secret|cookie|api[_-]?key)', key) else redact(item)) for key, item in value.items()}
                return value
            content = json.dumps(redact(answer), ensure_ascii=False)
        else:
            content = result.stdout
        path.write_text(content, encoding="utf-8")
        def record(latest):
            if latest["stages"].get(name, {}).get("status") != "open":
                raise FridayError("invalid_stage", "Stage changed while the runner was working.")
            if latest.get("decision_revision", 0) != card.get("decision_revision", 0):
                raise FridayError("stale_evidence", "A decision changed while the runner was working.")
            if latest.get("plan_revision", 0) != card.get("plan_revision", 0):
                raise FridayError("stale_evidence", "The plan changed while the runner was working.")
            event = {"type": "model_run", "stage": name, "role": role, "model": runner["model"],
                     "evidence": str(path), "at": state.now(), "head": head, "verdict": verdict,
                     "worktree_snapshot": snapshot, "decision_revision": card.get("decision_revision", 0),
                     "plan_revision": card.get("plan_revision", 0)}
            if role == 'blind-behavior':
                event['evidence_sha256'] = hashlib.sha256(content.encode('utf-8')).hexdigest()
            if fallback_for:
                event.update(fallback_for=fallback_for, verified_criteria=selected_criteria)
            if behavior_binding:
                event['behavior_evidence_binding'] = behavior_binding
            if gate_binding:
                event['gate_evidence_binding'] = gate_binding
            if browser_context is not None:
                event['browser_test_context'] = browser_context
            latest["events"].append(event)
            if challenges:
                latest['stages'][name].setdefault('review_disputes', []).extend(dict(item, head=head) for item in challenges)
                latest['events'].append({'type': 'review_disputed', 'stage': name, 'at': state.now()})
            return event
        return state.update(root, key, record)
