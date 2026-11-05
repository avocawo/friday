"""Docker images a profile needs, built by the owner with `friday image build` and checked by doctor.

The build runs on the host, never inside a role: it gets the private registry's .npmrc as a BuildKit secret by path,
and Friday never opens that file. The context is a git archive of the source repository's origin/<base branch>, so
whatever branch the main checkout is on stays untouched. The lockfile's digest goes into an image label, which lets
doctor tell a missing image from one built before the lockfile changed."""
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from . import gate, stories
from .errors import FridayError

LABEL = "friday.lockfile.sha256"
COMMIT_LABEL = "friday.source.commit"
NPMRC = "~/.npmrc"
FIELDS = {"repository", "base_branch", "dockerfile", "lockfile", "npmrc", "add_hosts"}


def validate(value, field):
    """A build block: where the source comes from, which Dockerfile, which lockfile, the .npmrc path, extra hosts."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise FridayError("invalid_profile", "%s takes %s." % (field, ", ".join(sorted(FIELDS))))
    for name in ("repository", "base_branch", "dockerfile"):
        if name in value and (not isinstance(value[name], str) or not value[name]):
            raise FridayError("invalid_profile", "%s.%s must be a non-empty string." % (field, name))
    if value.get("base_branch", "").startswith("-"):
        raise FridayError("invalid_profile", "%s.base_branch is not a branch name." % field)
    if "lockfile" in value and not stories.safe_relative(value["lockfile"]):
        raise FridayError("invalid_profile", "%s.lockfile must be a path inside the source repository." % field)
    if "npmrc" in value and value["npmrc"] is not None and (not isinstance(value["npmrc"], str) or not value["npmrc"]):
        raise FridayError("invalid_profile", "%s.npmrc must be a file path or null." % field)
    hosts = value.get("add_hosts", [])
    if not isinstance(hosts, list) or any(not isinstance(item, str) or ":" not in item or item.startswith("-") for item in hosts):
        raise FridayError("invalid_profile", "%s.add_hosts must be an array of host:address entries." % field)


def resolve_paths(value, directory):
    """Paths of a build block, as written in a profile layer: relative to that profile file, ~ expanded."""
    if not isinstance(value, dict):
        return
    for name in ("repository", "dockerfile", "npmrc"):
        if isinstance(value.get(name), str) and value[name]:
            value[name] = str((directory / Path(value[name]).expanduser()).resolve())


def specs(profile):
    """The images this profile builds: the showcase's (stories.image) and the gate's when gate_build describes it."""
    result = []
    showcase = stories.settings(profile)
    if showcase and showcase.get("image"):
        build = showcase.get("build") or {}
        result.append(spec("stories", showcase["image"], build, showcase["repository"], showcase["base_branch"], profile))
    if profile.get("gate_image") and isinstance(profile.get("gate_build"), dict):
        result.append(spec("gate", profile["gate_image"], profile["gate_build"], profile["repository"],
                           profile.get("base_branch", "main"), profile))
    return result


def spec(name, image, build, repository, base_branch, profile):
    npmrc = build.get("npmrc", NPMRC)
    return {"name": name, "image": image, "repository": build.get("repository", repository),
            "ref": "refs/remotes/origin/" + build.get("base_branch", base_branch),
            "dockerfile": build.get("dockerfile"), "lockfile": build.get("lockfile", "package-lock.json"),
            "npmrc": str(Path(npmrc).expanduser()) if npmrc else None, "add_hosts": list(build.get("add_hosts", [])),
            "profile": profile["path"]}


def hint(item):
    return "friday image build --profile %s --only %s" % (item["profile"], item["name"])


def commit(item):
    try:
        return gate.git(item["repository"], "rev-parse", "--verify", "-q", item["ref"] + "^{commit}").decode().strip()
    except FridayError:
        raise FridayError("missing", "%s has no %s; fetch it first (git -C %s fetch origin)."
                          % (item["repository"], item["ref"], item["repository"]))


def lockfile_digest(item, revision=None):
    """sha256 of the lockfile at the source commit, read from git: the same bytes the build context gets."""
    revision = revision or commit(item)
    try:
        content = gate.git(item["repository"], "show", "%s:%s" % (revision, item["lockfile"]))
    except FridayError:
        raise FridayError("missing", "%s has no %s at %s." % (item["repository"], item["lockfile"], item["ref"]))
    return hashlib.sha256(content).hexdigest()


def labels(image):
    """The image's Friday labels, or None when the image is not present locally."""
    try:
        result = subprocess.run(["docker", "image", "inspect", "--format",
                                 '{{index .Config.Labels "%s"}}' % LABEL, image], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise FridayError("environment_unavailable", "Docker is not available.")
    if result.returncode:
        return None
    value = result.stdout.strip()
    return {LABEL: "" if value == "<no value>" else value}


def status(item):
    """missing, stale (built from another lockfile, or without the label) or ok; with the reason and the command to run."""
    found = labels(item["image"])
    if found is None:
        return {"state": "missing", "reason": "Image %s is not built. Build it: %s" % (item["image"], hint(item))}
    expected = lockfile_digest(item)
    if found[LABEL] != expected:
        why = "was built without Friday's lockfile label" if not found[LABEL] else "is older than %s on %s" % (item["lockfile"], item["ref"])
        return {"state": "stale", "reason": "Image %s %s. Rebuild it: %s" % (item["image"], why, hint(item))}
    return {"state": "ok", "reason": "%s matches %s on %s" % (item["image"], item["lockfile"], item["ref"])}


def command(item, context, digest, revision, npmrc=None):
    npmrc = npmrc or item["npmrc"]
    argv = ["docker", "build"]
    if npmrc:
        argv += ["--secret", "id=npmrc,src=" + npmrc]
    for host in item["add_hosts"]:
        argv += ["--add-host", host]
    argv += ["--label", "%s=%s" % (LABEL, digest), "--label", "%s=%s" % (COMMIT_LABEL, revision)]
    if item["dockerfile"]:
        argv += ["-f", item["dockerfile"]]
    return argv + ["-t", item["image"], str(context)]


def build(item, npmrc=None, echo=print):
    """Build one image from a git archive of its source; docker's output goes straight to the terminal. Returns its exit code."""
    secret = npmrc or item["npmrc"]
    # A stat only: the file is handed to docker by path and never opened here.
    if secret and not os.path.isfile(secret):
        raise FridayError("missing", "No .npmrc at %s; pass --npmrc or set build.npmrc in the profile." % secret)
    revision = commit(item)
    digest = lockfile_digest(item, revision)
    with tempfile.TemporaryDirectory(prefix="friday-image-") as directory:
        gate.extract(gate.git(item["repository"], "archive", "--format=tar", revision), directory)
        if not item["dockerfile"] and not (Path(directory) / "Dockerfile").is_file():
            raise FridayError("missing", "%s has no Dockerfile at %s; set build.dockerfile." % (item["repository"], item["ref"]))
        argv = command(item, directory, digest, revision, secret)
        echo("Сборка %s из %s (%s, %s)" % (item["image"], item["repository"], item["ref"], revision[:12]))
        try:
            result = subprocess.run(argv, env=dict(os.environ, DOCKER_BUILDKIT="1"))
        except FileNotFoundError:
            raise FridayError("environment_unavailable", "Docker is not available.")
    return result.returncode


def select(profile, only=None):
    items = specs(profile)
    if only:
        items = [item for item in items if item["name"] == only]
    if not items:
        raise FridayError("missing", "The profile describes no image to build%s (stories.image, or gate_image with gate_build)."
                          % (" named " + only if only else ""))
    return items
