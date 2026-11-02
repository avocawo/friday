"""Restricted Claude Code adapter for Anthropic and Z.ai; JSON over stdin/stdout."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from friday import credentials, schemas

REVIEW_ROLES = ("review", "blind-code", "blind-behavior")
ROLES = ("plan", "research", "develop") + REVIEW_ROLES


def main():
    request = json.load(sys.stdin)
    vendor, role, model = request.get("vendor"), request.get("role"), request.get("model")
    if vendor not in ("anthropic", "zai") or role not in ROLES or not isinstance(model, str) or not model.strip():
        raise ValueError("Invalid vendor, role or model")
    environment = {key: value for key, value in os.environ.items()
                   if key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "SHELL", "USER")}
    if vendor == "zai":
        config = {"token_env": "FRIDAY_ZAI_TOKEN", "token_file": os.environ.get("FRIDAY_ZAI_TOKEN_FILE", "~/.config/friday/zai-token")}
        if os.environ.get("FRIDAY_ZAI_TOKEN_KEY"):
            config["token_key"] = os.environ["FRIDAY_ZAI_TOKEN_KEY"]
        token = credentials.token(config, "FRIDAY_ZAI_TOKEN")
        environment.update({"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic", "ANTHROPIC_AUTH_TOKEN": token})
    elif os.environ.get("FRIDAY_CLAUDE_API_KEY"):
        environment["ANTHROPIC_API_KEY"] = os.environ["FRIDAY_CLAUDE_API_KEY"]
    tools = "Read,Glob,Grep,Edit,Write" if role == "develop" else "Read,Glob,Grep"
    settings = {"permissions": {"deny": ["Read(.env)", "Read(.env.*)", "Read(.npmrc)", "Read(*.pem)", "mcp__*"]}}
    browser = []
    if role == "blind-behavior":
        # Only Friday's scoped preview browser, as in the Codex adapter; every other MCP server stays denied.
        from friday.browser_session import TOOLS, configuration
        spec = json.loads(os.environ["FRIDAY_BROWSER_CONFIG"])
        preview = request.get("preview_url")
        configuration(spec, preview)
        server = str(Path(__file__).resolve().parent / "browser_mcp.py")
        servers = {"mcpServers": {"friday": {"command": sys.executable, "args": [server, json.dumps(spec), preview]}}}
        environment.update({"MCP_TIMEOUT": "180000"})
        allowed = ["mcp__friday__" + tool for tool in sorted(TOOLS)]
        settings = {"permissions": {"deny": settings["permissions"]["deny"][:-1], "allow": allowed}}
        tools = ",".join(["Read", "Glob", "Grep"] + allowed)
        browser = ["--mcp-config", json.dumps(servers), "--allowedTools", ",".join(allowed)]
    command = ["claude", "-p", "--restricted", "--strict-mcp-config", "--no-chrome",
               "--no-session-persistence", "--settings", json.dumps(settings), "--tools", tools, "--model", model, "--output-format", "json",
               *browser]
    if role == "develop":
        command.extend(["--permission-mode", "acceptEdits"])
    if role in REVIEW_ROLES or role == "plan":
        schema = schemas.plan() if role == "plan" else schemas.review()
        if role in REVIEW_ROLES and request.get("criteria"):
            schema["properties"]["criteria_results"]["items"]["properties"]["criterion"]["enum"] = list(dict.fromkeys(request["criteria"]))
        command.extend(["--json-schema", json.dumps(schema)])
    planning = ("Produce a bounded plan, using each stage's brief for its result and steps for a short ordered list of what will be done and how, in the task's language. Optionally propose useful agents from available_agents and skills from "
                "available_skills. Each criterion has text and checks: code for diff-verifiable and behavior for browser-observable requirements. Include behavior checks for visible outcomes, not code-only stages. Browser tools do not expose console messages; do not require console inspection. Each nonempty crew must include review and the blind roles required by its criteria, "
                "all depending on every developer. Respect max_agents_per_stage and use a different vendor for review. "
                "Keep each stage within 10 files and 500 added lines. Resolve uncertainties independently from available evidence, "
                "record concrete choices and rationale in assumptions, and leave questions empty. " if role == "plan" else "")
    review_rule = ("Return all independently verified actionable defects in findings, with evidence, in one pass. Return unverifiable when evidence is insufficient, identifying the affected criteria. A pass requires findings to be an empty array; "
                   "a fail requires at least one actionable finding. Put observations in evidence. "
                   if role in REVIEW_ROLES else "")
    if role in REVIEW_ROLES:
        review_rule += "Provide one criteria_results entry per assigned criterion; each pass needs concrete evidence. The overall verdict must match those results. "
        review_rule += "Check review_disputes against the cited rule, precedent and current code; prefix purely cosmetic findings with COSMETIC:. "
    if role == "blind-behavior":
        review_rule += "If the preview, browser tools or evidence are unavailable, return unverifiable rather than inventing a defect. "
    if role == 'review':
        review_rule += "behavior_evidence contains observations by a separate browser reviewer on the same code version. Compare those observations with the assigned criterion and code; if they are insufficient, return unverifiable. Do not claim you personally observed the browser. "
        review_rule += "gate_evidence, when supplied, reports the configured isolated gate command and its exit status for the current snapshot; inspect the script to determine what it checks. Do not claim you ran it, and do not infer checks beyond its actual scope. "
    if request.get("engineering_policy"):
        review_rule += "Apply the supplied engineering_policy to this assignment and record supporting evidence. "
    dispute_rule = ("To dispute a review finding, return JSON with a disputes array; each entry has exactly the fields finding, rule_path, quote, precedent and reason; quote is verbatim and precedent is a tracked code path. " if role == 'develop' else '')
    prompt = ("Perform only the assigned role and work in the provided workspace. Resolve legacy planning questions using available evidence and document concrete assumptions; do not wait for answers. " + planning + dispute_rule +
              review_rule +
              "Never publish, send messages, deploy, or contact production systems. "
              "Use only the supplied criteria and context; report uncertainty.\n\n" + json.dumps(request, ensure_ascii=False))
    result = subprocess.run(command, input=prompt, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    if result.returncode:
        tail = (result.stderr or "").strip()[-500:]
        stdout_tail = (result.stdout or "").strip()[-300:]
        raise RuntimeError("Claude Code exited with status " + str(result.returncode)
                           + (("\nstderr: " + tail) if tail else "") + (("\nstdout: " + stdout_tail) if stdout_tail else ""))
    response = json.loads(result.stdout)
    if response.get("is_error"):
        raise RuntimeError("Claude Code reported an error")
    value = response.get("structured_output") if role in REVIEW_ROLES or role == "plan" else response.get("result")
    if role in REVIEW_ROLES:
        schemas.validate_review(value, strict=True)
        print(json.dumps(value, ensure_ascii=False))
    elif role == "plan" and isinstance(value, dict):
        print(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, str) and value.strip():
        print(value)
    else:
        raise ValueError("Missing agent output")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        sys.exit(1)
