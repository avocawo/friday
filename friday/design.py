"""Design tools for UI tasks: the Figma frame of the ticket for planning, /impeccable references as prompt text."""
import base64
import binascii
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import card as cards
from .errors import FridayError

READ_TOOLS = ("get_metadata", "get_design_context", "get_screenshot", "get_variable_defs")
FIGMA_URL = "http://127.0.0.1:3845/mcp"
ROLES = ("develop", "review", "final-review")
LINK = re.compile(r"https://(?:www\.)?figma\.com/(?:design|file)/([A-Za-z0-9]+)/[^\s)\]>\"']*?[?&]node-id=([0-9]+[-:][0-9]+)")
LOOPBACK = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\]):[0-9]+/")

PLAN_FIGMA = (
    "Design: the ticket links the Figma frames listed under design_frames, and the figma tools read them from the Figma "
    "desktop app. Call get_screenshot and get_design_context on each frame and its state variants before planning. Put "
    "the measurable design specification into design, one item per fact with its node id: size, padding and gaps, corner "
    "radius, borders, icon and its size, typography, colors and tokens, and every state the frame shows (default, hover, "
    "focus, active, disabled, error). Turn each fact the change affects into a stage criterion. Executors and reviewers do "
    "not see Figma, only design.")
REVIEW_FIGMA = (
    "The ticket links Figma frames (design_frames) and you can read them with the figma tools. Check that design covers "
    "every state and measurement of the frame the change affects and that criteria hold the executor to it; a missing or "
    "wrong design fact is blocking.")
FOLLOW_SPEC = (
    "design_spec is the owner-approved specification taken from the Figma frame. Match it exactly: sizes, radii, icons, "
    "tokens and every state.")
REVIEW_SPEC = (
    "design_spec is the owner-approved specification taken from the Figma frame. A deviation from it in the changed "
    "component is blocking; cite the spec item and the code line.")
BROWSER_DESIGN = (
    "Also check the rendered component against the design. design_spec is the owner-approved specification; "
    "figma_screenshots are the Figma frames it comes from, saved in your working folder: Read each one. For every state "
    "the spec or the frames show, bring the component into that state (hover for Hover, Tab for focus), take_screenshot "
    "and call computed_style on the element and its parts for the properties the spec names. A measured value that "
    "differs from a design_spec item is a blocking finding: name the spec item, the state and the measured value. A "
    "difference you only see by eye between the screenshots (spacing, alignment, icon, a composition that reads as "
    "separate elements in a box rather than one whole) is a minor finding. Use file \"design\" when the source file is "
    "not known.")
IMPECCABLE = (
    "Design guidance from the /impeccable skill follows. Apply it to the interface this milestone changes, within the "
    "project's own UI kit and design_spec; its commands, scripts and browser steps are not available here, skip them.")


def settings(profile):
    value = profile.get("design")
    return value if isinstance(value, dict) else {}


def validate(value):
    """Profile check of the design key; returns nothing, raises invalid_profile."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - {"figma", "impeccable"}:
        raise FridayError("invalid_profile", "design takes figma and impeccable.")
    figma = value.get("figma")
    if figma is not None:
        if not isinstance(figma, dict) or set(figma) - {"url", "tools"}:
            raise FridayError("invalid_profile", "design.figma takes url and tools.")
        if not isinstance(figma.get("url", FIGMA_URL), str) or not LOOPBACK.match(figma.get("url", FIGMA_URL)):
            raise FridayError("invalid_profile", "design.figma.url must be the local Figma desktop server, e.g. " + FIGMA_URL)
        tools = figma.get("tools", list(READ_TOOLS))
        if not isinstance(tools, list) or not tools or any(tool not in READ_TOOLS for tool in tools):
            raise FridayError("invalid_profile", "design.figma.tools may only name read tools: " + ", ".join(READ_TOOLS))
    impeccable = value.get("impeccable")
    if impeccable is not None:
        if not isinstance(impeccable, dict) or not isinstance(impeccable.get("skill_dir"), str) or not impeccable["skill_dir"]:
            raise FridayError("invalid_profile", "design.impeccable needs skill_dir, the folder with the skill's SKILL.md.")
        references = impeccable.get("references")
        if not isinstance(references, dict) or not references or set(references) - set(ROLES):
            raise FridayError("invalid_profile", "design.impeccable.references maps %s to file lists." % ", ".join(ROLES))
        for files in references.values():
            if not isinstance(files, list) or any(not isinstance(name, str) or not name or name.startswith("/")
                                                  or ".." in name.split("/") for name in files):
                raise FridayError("invalid_profile", "design.impeccable.references lists files inside the skill's reference folder.")


def links(text):
    """The Figma frames a ticket links, in order, without repeats: [{"file", "node", "url"}]."""
    found, seen = [], set()
    for match in LINK.finditer(text or ""):
        node = match.group(2).replace("-", ":")
        if (match.group(1), node) not in seen:
            seen.add((match.group(1), node))
            found.append({"file": match.group(1), "node": node, "url": match.group(0)})
    return found


def message(text):
    """The JSON of a plain response or of the first server-sent event; an event's data may span several data: lines."""
    if not text.lstrip().startswith(("event:", "data:", "id:", ":")):
        return json.loads(text) if text.strip() else None
    data = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):  # not splitlines(): Unicode separators occur in layer names
        if not line.strip():
            if data:
                break
            continue
        if line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    return json.loads("\n".join(data)) if data else None


class Figma:
    """A minimal client of the Figma desktop Dev Mode MCP server (streamable HTTP)."""

    def __init__(self, url, timeout=20):
        self.url, self.timeout, self.session, self.counter = url, timeout, None, 0

    def post(self, body):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["mcp-session-id"] = self.session
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            self.session = response.headers.get("mcp-session-id") or self.session
            text = response.read().decode("utf-8", "replace")
        return message(text)

    def request(self, method, params=None):
        self.counter += 1
        answer = self.post({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params or {}})
        if not isinstance(answer, dict) or "error" in answer:
            raise FridayError("design_unavailable", "Figma answered %s with an error: %s" % (method, (answer or {}).get("error")))
        return answer.get("result") or {}

    def open(self):
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "friday", "version": "1"}})
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def call(self, tool, **arguments):
        return self.request("tools/call", {"name": tool, "arguments": arguments})


def screenshot(client, frame, folder):
    """Save the frame's Figma rendering for the browser check; returns the file name."""
    result = client.call("get_screenshot", nodeId=frame["node"])
    image = next((part for part in result.get("content", []) if isinstance(part, dict) and part.get("type") == "image"), None)
    if result.get("isError") or image is None or image.get("mimeType") != "image/png":
        raise FridayError("design_unavailable", "Figma did not render node %s." % frame["node"])
    name = "figma-%s.png" % frame["node"].replace(":", "-")
    (folder / name).write_bytes(base64.b64decode(image["data"], validate=True))
    return name


def check_frames(url, frames, folder=None):
    """Every linked node must be found in the active Figma tab; returns the frames with their layer names and, given a
    folder, saves each frame's rendering there."""
    try:
        client = Figma(url).open()
        checked = []
        for frame in frames:
            result = client.call("get_metadata", nodeId=frame["node"])
            text = " ".join(part.get("text", "") for part in result.get("content", []) if isinstance(part, dict))
            if result.get("isError"):
                raise FridayError("design_unavailable", "Figma does not show node %s: %s Open the file %s (%s) in the "
                                  "Figma desktop app as the active tab and run start again." % (frame["node"], text.strip(),
                                                                                              frame["file"], frame["url"]))
            name = re.search(r'name="([^"]*)"', text)
            checked.append(dict(frame, name=name.group(1) if name else ""))
        if folder is not None:
            Path(folder).mkdir(parents=True, exist_ok=True)
            for frame in checked:
                frame["screenshot"] = screenshot(client, frame, Path(folder))
        return checked
    except (OSError, ValueError, binascii.Error, urllib.error.URLError) as exc:
        raise FridayError("design_unavailable", "The Figma desktop MCP server at %s does not answer (%s). Start Figma, sign "
                          "in, enable the Dev Mode MCP server and open the ticket's file, then run start again; "
                          "start --no-design plans without the mockup." % (url, exc))


def prepare(root, key, profile, ticket_text, enabled=True):
    """Before planning: the Figma frames this task is held to, recorded on the card; None when there are none."""
    figma = settings(profile).get("figma")
    if figma is None:
        cards.update(root, key, lambda value: value.pop("design", None))  # no frames left from an earlier start
        return None
    frames = links(ticket_text)
    reason = None if enabled else "owner: --no-design"
    if reason is None and not frames:
        reason = "the ticket links no Figma frame"
    if reason:
        cards.update(root, key, lambda value: value.update(design={"skipped": reason}))
        cards.record(root, key, "design_skipped", reason=reason)
        return None
    try:
        frames = check_frames(figma.get("url", FIGMA_URL), frames, folder(root, key))
    except FridayError as exc:
        cards.record(root, key, "design_unavailable", error=str(exc))
        raise
    cards.update(root, key, lambda value: value.update(design={"frames": frames}))
    cards.record(root, key, "design_frames", frames=[frame["node"] for frame in frames])
    return frames


def folder(root, key):
    return cards.directory(root, key) / "design"


def frames(card):
    return (card.get("design") or {}).get("frames") or []


def figma_server(profile):
    figma = settings(profile)["figma"]
    return {"name": "figma", "url": figma.get("url", FIGMA_URL), "tools": list(figma.get("tools", READ_TOOLS))}


def references(profile, role):
    """The /impeccable reference files of a role as one prompt block, or an empty string."""
    impeccable = settings(profile).get("impeccable")
    if not impeccable:
        return ""
    names = impeccable["references"].get(role, [])
    folder = Path(impeccable["skill_dir"]).expanduser() / "reference"
    parts = []
    for name in names:
        try:
            parts.append("--- impeccable/reference/%s\n%s" % (name, (folder / name).read_text(encoding="utf-8").strip()))
        except OSError as exc:
            raise FridayError("design_unavailable", "Cannot read the /impeccable reference %s: %s" % (name, exc))
    return IMPECCABLE + "\n\n" + "\n\n".join(parts) if parts else ""


def guidance(profile, role, document, ui):
    """What a role that works on code adds to its rules for a UI milestone: the spec rule and the /impeccable block."""
    if not ui:
        return ""
    parts = []
    if document.get("design"):
        parts.append(FOLLOW_SPEC if role == "develop" else REVIEW_SPEC)
    block = references(profile, role)
    if block:
        parts.append(block)
    return "\n\n" + "\n\n".join(parts) if parts else ""


def browser_text(document, frames_with_screenshots):
    """The browser check's design part: the approved spec and the Figma renderings placed in its working folder."""
    if not document.get("design") and not frames_with_screenshots:
        return ""
    value = {"design_spec": document.get("design", []),
             "figma_screenshots": [{"file": frame["screenshot"], "node": frame["node"], "name": frame.get("name", "")}
                                   for frame in frames_with_screenshots]}
    return "\n\n" + BROWSER_DESIGN + "\n\n" + json.dumps(value, ensure_ascii=False, indent=2)


def copy_screenshots(root, key, card, destination):
    """Put the task's Figma renderings into the browser check's working folder; returns the frames that have one."""
    found = []
    for frame in frames(card):
        source = folder(root, key) / frame.get("screenshot", "")
        if frame.get("screenshot") and source.is_file():
            (Path(destination) / frame["screenshot"]).write_bytes(source.read_bytes())
            found.append(frame)
    return found


def doctor_checks(profile):
    """(name, operation, warning) for doctor: the references must exist; Figma may be closed at check time."""
    checks = []
    if settings(profile).get("impeccable"):
        def impeccable():
            for role in ROLES:
                references(profile, role)
        checks.append(("design_impeccable", impeccable, False))
    if settings(profile).get("figma") is not None:
        def figma():
            Figma(figma_server(profile)["url"], timeout=5).open()
            return "Figma desktop MCP answers; open the ticket's file before start"
        checks.append(("design_figma", lambda: wrap(figma), True))
    return checks


def wrap(operation):
    try:
        return operation()
    except (OSError, ValueError) as exc:
        raise FridayError("design_unavailable", "Figma desktop MCP does not answer: %s" % exc)
