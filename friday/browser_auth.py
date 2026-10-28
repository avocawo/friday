"""Borrow a verified staging session without exposing its token to an agent."""
import json
import re
from urllib.parse import urlsplit

from . import browser_probe, browser_protocol
from .errors import FridayError


def read_only_query(query):
    if not isinstance(query, str) or not re.fullmatch(r"\s*query(?:\s+[A-Za-z_][A-Za-z_0-9]*)?\s*\{[A-Za-z_0-9{}\s]+\}\s*", query):
        return False
    selection = query[query.index("{"):].rstrip()
    depth = 0
    for index, char in enumerate(selection):
        depth += (char == "{") - (char == "}")
        if depth < 0 or (depth == 0 and index != len(selection) - 1):
            return False
    return depth == 0


def probe_script(spec, source_check=False):
    settings = spec["auth"]
    source = """const text = await fetch('/src/app/main.ts', {redirect:'error'}).then(r => r.text());
        const match = text.match(/VITE_API_APOLLO_URI[\"']?\\s*[:=]\\s*[\"']([^\"']+)[\"']/);
        if (!match || new URL(match[1]).origin !== __ORIGIN__) return null;""" if source_check else ""
    result = "token" if source_check else "true"
    fingerprint = settings.get("fingerprint")
    fingerprint_script = ""
    if fingerprint:
        fingerprint_script = ("const module = await import(" + json.dumps(fingerprint["module"]) + "); "
                              "headers[" + json.dumps(fingerprint["header"]) + "] = await module[" + json.dumps(fingerprint["export"]) + "]();")
    script = """async () => {
      try {
        __SOURCE__
        const token = localStorage.getItem(__KEY__);
        if (!token) return null;
        const headers = {'Content-Type':'application/json','Authorization':'Bearer ' + token};
        __FINGERPRINT__
        const response = await fetch(__ORIGIN__ + __PATH__, {
          method:'POST', credentials:'omit',
          headers,
          body:JSON.stringify({query:__QUERY__})
        });
        const value = await response.json();
        const identity = __DATA_PATH__.reduce((current, part) => current?.[part], value?.data);
        return response.ok && identity ? __RESULT__ : null;
      } catch (_) { return null; }
    }"""
    return (script.replace("__SOURCE__", source).replace("__RESULT__", result).replace("__FINGERPRINT__", fingerprint_script)
            .replace("__ORIGIN__", json.dumps(spec["stage_origin"]))
            .replace("__KEY__", json.dumps(settings["storage_key"]))
            .replace("__PATH__", json.dumps(settings["api_path"]))
            .replace("__QUERY__", json.dumps(settings["query"]))
            .replace("__DATA_PATH__", json.dumps(settings["data_path"])))


def capture(spec):
    settings = spec.get("auth")
    if settings is None:
        return None
    if not isinstance(settings, dict):
        raise FridayError("invalid_profile", "Browser auth must be an object.")
    key, api_path, port = settings.get("storage_key"), settings.get("api_path"), settings.get("source_port")
    if not isinstance(key, str) or not key or not isinstance(api_path, str) or not api_path.startswith("/") or any(x in api_path for x in ("?", "#")) or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise FridayError("invalid_profile", "Browser auth requires a storage key, staging API path and local source port.")
    query, data_path = settings.get("query"), settings.get("data_path")
    if not read_only_query(query) or not isinstance(data_path, list) or not data_path or any(not isinstance(part, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part) for part in data_path):
        raise FridayError("invalid_profile", "Browser auth requires a read-only query and response path.")
    fingerprint = settings.get("fingerprint")
    if fingerprint is not None:
        if not isinstance(fingerprint, dict):
            raise FridayError("invalid_profile", "Browser fingerprint must be an object.")
        module, exported, header = fingerprint.get("module"), fingerprint.get("export"), fingerprint.get("header")
        if not isinstance(module, str) or not module.startswith("/") or module.startswith("//") or ".." in module.split("/") or "?" in module or "#" in module or not isinstance(exported, str) or not re.fullmatch(r"[A-Za-z_$][A-Za-z_0-9$]*", exported) or not isinstance(header, str) or not re.fullmatch(r"[A-Za-z0-9-]+", header):
            raise FridayError("invalid_profile", "Browser fingerprint requires a local module, export and HTTP header.")
    origin = spec.get("stage_origin")
    target = urlsplit(origin) if isinstance(origin, str) else None
    try:
        valid_port = bool(target) and target.port in (None, 443)
    except ValueError:
        valid_port = False
    if not target or target.scheme != "https" or not target.hostname or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.stage\.[a-z0-9.-]+", target.hostname) or target.username or target.password or target.path or target.query or target.fragment or not valid_port:
        raise FridayError("invalid_profile", "Browser auth requires an HTTPS staging origin.")
    source = probe_script(spec, source_check=True)
    client = browser_protocol.Client(browser_probe.command(browser_probe.server_path({"browser": spec})))
    try:
        listing = browser_protocol.text(client.tool("list_pages", {}))
        for page, url in browser_probe.pages(listing):
            parsed = urlsplit(url)
            if parsed.scheme != "http" or not parsed.hostname or not parsed.hostname.endswith(".localhost") or parsed.port != port:
                continue
            try:
                result = browser_protocol.text(client.tool("evaluate_script", {"pageId": page, "function": source}))
                match = re.search(r"```json\s*([\s\S]*?)\s*```", result)
                token = json.loads(match.group(1)) if match else None
            except (FridayError, ValueError):
                continue
            if isinstance(token, str) and token and len(token) <= 16384:
                return token
    finally:
        client.close()
    raise FridayError("stage_auth_unavailable", "No active authenticated staging session was found in Chrome.")


def verify_preview(spec, client, page):
    settings = spec.get("auth")
    if settings is None:
        return
    source = probe_script(spec)
    result = browser_protocol.text(client.tool("evaluate_script", {"pageId": page, "function": source}, timeout=30))
    if not re.search(r"```json\s*true\s*```", result):
        raise FridayError("stage_auth_unavailable", "The isolated preview could not authenticate with staging.")
