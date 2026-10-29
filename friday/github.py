"""Read issue metadata from a configured GitHub repository using gh."""
import json
import re
import subprocess
import urllib.parse
import hashlib

from .errors import FridayError

REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]+$")
PRIVATE_DATA = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:gh[pousr]_|github_pat_|glpat-)[A-Za-z0-9_-]{15,}|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|(?<!\d)(?:\+?\d[\s().-]*){10,40}(?!\d)|"
    r"\b(?:bearer|oauth)\s+[A-Za-z0-9._~+/-]{16,}", re.I)


def repository(selected):
    config = selected.get("github")
    name = config.get("repository") if isinstance(config, dict) else None
    if not isinstance(name, str) or not REPOSITORY.fullmatch(name) or name.split("/")[1] in (".", ".."):
        raise FridayError("invalid_profile", "github.repository must be an owner/repository name.")
    return name


def check(selected):
    name = repository(selected)
    try:
        result = subprocess.run(["gh", "api", "--hostname", "github.com", "repos/" + name, "--jq", ".full_name"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise FridayError("github_unavailable", "Cannot inspect GitHub repository access.")
    if result.returncode or result.stdout.strip().casefold() != name.casefold():
        raise FridayError("github_unavailable", "Cannot inspect GitHub repository access.")


def check_delivery(selected, settings):
    name = repository(selected)
    repo = api(selected, "repos/" + name)
    if not isinstance(repo, dict) or not isinstance(repo.get("permissions"), dict) or repo["permissions"].get("push") is not True:
        raise FridayError("github_unavailable", "GitHub repository write access is unavailable.")
    if selected["github"].get("require_private") is True and repo.get("private") is not True:
        raise FridayError("unsafe_remote", "GitHub repository is not private.")
    if selected["github"].get("require_no_webhooks") is True:
        hooks = api(selected, "repos/" + name + "/hooks?per_page=100")
        if not isinstance(hooks, list) or len(hooks) >= 100 or any(not isinstance(hook, dict) or hook.get("active") is not False for hook in hooks):
            raise FridayError("unsafe_remote", "Active or unverified GitHub repository webhooks prevent autonomous delivery.")
    policy = settings.get("ci_policy") if isinstance(settings, dict) else None
    expected = policy.get("workflow_tree") if isinstance(policy, dict) else None
    target = settings.get("target_branch") if isinstance(settings, dict) else None
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{40}", expected) or not isinstance(target, str) or not target:
        raise FridayError("unsafe_ci", "GitHub target branch and workflow tree must be configured.")
    branch = api(selected, "repos/" + name + "/branches/" + urllib.parse.quote(target, safe=""))
    commit = branch.get("commit") if isinstance(branch, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise FridayError("unsafe_ci", "GitHub target commit is unavailable.")
    tree = api(selected, "repos/" + name + "/git/trees/" + sha + "?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise FridayError("unsafe_ci", "GitHub workflow tree cannot be inspected completely.")
    workflows = [item.get("sha") for item in tree["tree"] if isinstance(item, dict) and item.get("path") == ".github/workflows"]
    if workflows != [expected]:
        raise FridayError("unsafe_ci", "GitHub target workflow tree changed since review.")
    return sha


def backlog(selected):
    name = repository(selected)
    command = ["gh", "issue", "list", "--repo", "github.com/" + name, "--state", "open", "--limit", "100",
               "--json", "number,title,url,labels"]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=60)
        items = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        items = None
    if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("number"), int) or
                                         isinstance(item.get("number"), bool) or not isinstance(item.get("title"), str) or
                                         not isinstance(item.get("url"), str) or not isinstance(item.get("labels"), list)
                                         for item in items):
        raise FridayError("github_unavailable", "Cannot read GitHub issue metadata.")
    return {"repository": name, "issues": items, "possibly_truncated": len(items) >= 100}


def api(selected, path, payload=None, read_only=False):
    repository(selected)
    args = ["gh", "api", "--hostname", "github.com"]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    args.append(path)
    for _ in range(3 if payload is None or read_only else 1):
        try:
            result = subprocess.run(args, input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=120)
            data = json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            data = None
        if data is not None:
            return data
    raise FridayError("github_unavailable" if payload is None or read_only else "delivery_unknown", "GitHub request was not confirmed.")


def graphql(selected, query, **variables):
    payload = {"query": query, "variables": variables}
    result = api(selected, "graphql", payload, read_only=True)
    if not isinstance(result, dict) or result.get("errors") or not isinstance(result.get("data"), dict):
        raise FridayError("github_unavailable", "GitHub GraphQL returned an incomplete response.")
    return result["data"]


def has_more(connection):
    value = connection["pageInfo"]["hasNextPage"]
    if not isinstance(value, bool):
        raise FridayError("github_error", "GitHub pagination state is invalid.")
    return value


def pull_path(selected):
    return "repos/" + repository(selected) + "/pulls"


def normalize_pull(value):
    if not isinstance(value, dict):
        raise FridayError("github_error", "GitHub returned an invalid pull request.")
    head, base = value.get("head") or {}, value.get("base") or {}
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise FridayError("github_error", "GitHub returned an invalid pull request identity.")
    source_repo, target_repo = head.get("repo") or {}, base.get("repo") or {}
    if not isinstance(source_repo, dict) or not isinstance(target_repo, dict):
        raise FridayError("github_error", "GitHub returned an invalid repository identity.")
    return {"iid": value.get("number"), "state": "opened" if value.get("state") == "open" else value.get("state"),
            "source_branch": head.get("ref"), "target_branch": base.get("ref"), "sha": head.get("sha"),
            "source_project_id": source_repo.get("id"), "target_project_id": target_repo.get("id"),
            "title": value.get("title"), "description": value.get("body"), "draft": value.get("draft"),
            "merged": value.get("merged") is True}


def matching_pulls(selected, branch):
    owner = repository(selected).split("/")[0]
    query = urllib.parse.urlencode({"state": "open", "head": owner + ":" + branch, "per_page": 100})
    values = api(selected, pull_path(selected) + "?" + query)
    if not isinstance(values, list) or len(values) >= 100:
        raise FridayError("github_error", "Cannot establish a unique pull request.")
    return [normalize_pull(value) for value in values]


def get_pull(selected, number):
    return normalize_pull(api(selected, pull_path(selected) + "/" + str(number)))


def create_pull(selected, branch, target, title, description):
    payload = {"head": branch, "base": target, "title": "Draft: " + title, "body": description, "draft": True}
    result = normalize_pull(api(selected, pull_path(selected), payload))
    if (not isinstance(result["iid"], int) or result["source_branch"] != branch or result["target_branch"] != target or
            result["title"] != payload["title"] or result["description"] != description or result["draft"] is not True or
            not isinstance(result["source_project_id"], int) or result["source_project_id"] != result["target_project_id"]):
        raise FridayError("delivery_unknown", "Draft pull request creation was not confirmed.")
    return result


def review_notes(selected, iid, branch, target, head):
    from . import mission
    pull_status = get_pull(selected, iid)
    if not mission.matches_mr(pull_status, iid, branch, target, head) or pull_status.get("draft") is not True:
        raise FridayError("remote_conflict", "Pull request or source head no longer matches this task.")
    owner, name = repository(selected).split("/")
    query = '''query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
      pullRequest(number:$number){reviewThreads(first:100){pageInfo{hasNextPage} nodes{isResolved isOutdated
      comments(first:100){pageInfo{hasNextPage} nodes{id body}}}} reviews(first:100){pageInfo{hasNextPage} nodes{id body state}}
      comments(first:100){pageInfo{hasNextPage} nodes{id body}}}}}'''
    result = graphql(selected, query, owner=owner, name=name, number=iid)
    try:
        pull = result["repository"]["pullRequest"]
        threads = pull["reviewThreads"]
        if has_more(threads):
            raise ValueError()
        notes = []
        def append(comment):
            body, identifier = comment.get("body"), comment.get("id")
            if not isinstance(body, str) or not body.strip():
                return
            if not isinstance(identifier, str) or len(body) > 10000 or PRIVATE_DATA.search(body):
                raise ValueError()
            notes.append({"id": identifier, "body": body, "digest": hashlib.sha256(body.encode()).hexdigest()})
        for thread in threads["nodes"]:
            if thread["isResolved"] or thread["isOutdated"]:
                continue
            comments = thread["comments"]
            if has_more(comments):
                raise ValueError()
            for comment in comments["nodes"]:
                append(comment)
        for field in ("reviews", "comments"):
            connection = pull[field]
            if has_more(connection):
                raise ValueError()
            for comment in connection["nodes"]:
                if field != "reviews" or comment.get("state") not in ("DISMISSED", "PENDING"):
                    append(comment)
        return notes
    except (AttributeError, KeyError, TypeError, ValueError):
        raise FridayError("github_error", "GitHub review threads are incomplete or too large.")


def pull_mentions(pull, name):
    text = (pull.get("title") or "") + "\n" + (pull.get("body") or "")
    short = re.findall(r"(?<![A-Za-z0-9])#([0-9]+)\b", text)
    links = re.findall(r"https://github\.com/" + re.escape(name) + r"/issues/([0-9]+)\b", text, re.I)
    return {int(number) for number in short + links}


def open_pulls(selected):
    owner, repo = repository(selected).split("/")
    query = '''query($owner:String!,$name:String!){repository(owner:$owner,name:$name){
      pullRequests(first:100,states:OPEN){pageInfo{hasNextPage} nodes{headRefName title body
      closingIssuesReferences(first:100){pageInfo{hasNextPage} nodes{number repository{nameWithOwner}}}}}}}'''
    pulls = graphql(selected, query, owner=owner, name=repo)["repository"]["pullRequests"]
    if has_more(pulls):
        raise FridayError("github_error", "Too many open pull requests to select safely.")
    for pull in pulls["nodes"]:
        if has_more(pull["closingIssuesReferences"]):
            raise FridayError("github_error", "Pull request issue links are incomplete.")
    return pulls["nodes"]


def referenced_issues(pull, name):
    references = pull["closingIssuesReferences"]["nodes"]
    return pull_mentions(pull, name) | {item["number"] for item in references
                                         if item["repository"]["nameWithOwner"].casefold() == name.casefold()}


def selection_rules(selected):
    from . import state
    config = selected["github"].get("selection", {})
    if not isinstance(config, dict):
        raise FridayError("invalid_profile", "GitHub selection must be an object.")
    required, excluded = config.get("required_labels"), config.get("excluded_labels", [])
    priorities, prefix = config.get("priority_labels"), config.get("key_prefix")
    if (not isinstance(required, list) or not required or not isinstance(excluded, list) or
            not isinstance(priorities, list) or not priorities or
            any(not isinstance(item, str) or not item for item in required + excluded + priorities) or
            not isinstance(prefix, str)):
        raise FridayError("invalid_profile", "GitHub selection requires label lists and a key prefix.")
    state.validate_key(prefix)
    return required, excluded, priorities, prefix


def open_issues(selected):
    owner, repo = repository(selected).split("/")
    query = '''query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){
      issues(first:25,after:$after,states:OPEN){pageInfo{hasNextPage endCursor} nodes{number title
      issueDependenciesSummary{blockedBy} assignees{totalCount} labels(first:50){pageInfo{hasNextPage} nodes{name}}}}}}'''
    issues, cursor = [], None
    for _ in range(10):
        connection = graphql(selected, query, owner=owner, name=repo, after=cursor)["repository"]["issues"]
        issues.extend(connection["nodes"])
        if not has_more(connection):
            break
        cursor = connection["pageInfo"]["endCursor"]
        if not isinstance(cursor, str) or not cursor:
            raise FridayError("github_error", "Issue pagination is incomplete.")
    else:
        raise FridayError("github_error", "Too many issues to select safely.")
    return issues


def _select_issue(selected, root):
    from . import gate, state
    name = repository(selected)
    required, excluded, priorities, prefix = selection_rules(selected)
    issues = open_issues(selected)
    active = set()
    branches = gate.git(selected["repository"], "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin").decode("utf-8", "replace").splitlines()
    for pull in open_pulls(selected):
        branches.append(pull["headRefName"])
        active.update(referenced_issues(pull, name))
    candidates = []
    for issue in issues:
        number = issue["number"]
        labels = issue["labels"]
        if has_more(labels):
            raise FridayError("github_error", "Issue labels are incomplete.")
        tags = {item["name"] for item in labels["nodes"]}
        if (not isinstance(number, int) or isinstance(number, bool) or number < 1 or
                not set(required).issubset(tags) or set(excluded) & tags or
                issue["issueDependenciesSummary"]["blockedBy"] or issue["assignees"]["totalCount"] or
                number in active or any(re.search(r"(?<!\d)" + str(number) + r"(?!\d)", branch) for branch in branches) or
                state.task_path(root, prefix + "-" + str(number)).exists()):
            continue
        ranks = [priorities.index(tag) for tag in tags if tag in priorities]
        if ranks:
            candidates.append((min(ranks), number, issue))
    if not candidates:
        return None
    unsafe = False
    for _, number, _ in sorted(candidates, key=lambda item: (item[0], item[1])):
        detail = api(selected, "repos/" + name + "/issues/" + str(number))
        if not isinstance(detail, dict) or detail.get("number") != number:
            raise FridayError("github_error", "GitHub returned an unexpected issue.")
        if detail.get("state") != "open":
            continue
        title, body = detail.get("title"), detail.get("body") or ""
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str) or len(body) > 50000 or PRIVATE_DATA.search(title + "\n" + body):
            unsafe = True
            continue
        return {"key": prefix + "-" + str(number), "kind": "github_issue", "number": number,
                "summary": title, "description": body, "url": detail.get("html_url", "")}
    if unsafe:
        raise FridayError("unsafe_issue", "Eligible issues contain oversized or potentially private data.")
    return None


def select_issue(selected, root):
    try:
        return _select_issue(selected, root)
    except (AttributeError, KeyError, TypeError):
        raise FridayError("github_error", "GitHub backlog data is incomplete or invalid.")


def assert_issue_ready(selected, number, owned_branch=None):
    from . import gate
    owner, repo = repository(selected).split("/")
    query = '''query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
      issue(number:$number){state issueDependenciesSummary{blockedBy} assignees{totalCount}
      labels(first:50){pageInfo{hasNextPage} nodes{name}}}}}'''
    try:
        result = graphql(selected, query, owner=owner, name=repo, number=number)["repository"]
        issue = result["issue"]
        required, excluded, _, _ = selection_rules(selected)
        labels = {item["name"] for item in issue["labels"]["nodes"]}
        if (issue["state"] != "OPEN" or issue["issueDependenciesSummary"]["blockedBy"] or issue["assignees"]["totalCount"] or
                has_more(issue["labels"]) or
                not set(required).issubset(labels) or set(excluded) & labels):
            raise FridayError("stale_issue", "Issue is no longer eligible for autonomous delivery.")
        branches = gate.git(selected["repository"], "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin").decode("utf-8", "replace").splitlines()
        own = {owned_branch, "origin/" + owned_branch} if owned_branch else set()
        if any(branch not in own and re.search(r"(?<!\d)" + str(number) + r"(?!\d)", branch) for branch in branches):
            raise FridayError("stale_issue", "Another branch may already be working on this issue.")
        for pull in open_pulls(selected):
            if number in referenced_issues(pull, repository(selected)) or re.search(r"(?<!\d)" + str(number) + r"(?!\d)", pull["headRefName"]):
                raise FridayError("stale_issue", "Issue may already have an open pull request.")
    except (AttributeError, KeyError, TypeError):
        raise FridayError("github_error", "Cannot verify current issue eligibility.")
