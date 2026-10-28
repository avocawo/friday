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
    command = ["claude", "-p", "--safe-mode", "--restricted", "--strict-mcp-config", "--no-chrome",
               "--no-session-persistence", "--settings", json.dumps(settings), "--tools", tools, "--model", model, "--output-format", "json"]
    if role == "develop":
        command.extend(["--permission-mode", "acceptEdits"])
    if role in REVIEW_ROLES or role == "plan":
        schema = schemas.plan() if role == "plan" else schemas.review()
        command.extend(["--json-schema", json.dumps(schema)])
    planning = ("Produce a bounded plan and optionally propose useful agents from available_agents and skills from "
                "available_skills. Each nonempty crew must include review, blind-code and blind-behavior, "
                "all depending on every developer. Respect max_agents_per_stage and use a different vendor for review. "
                "Keep each stage within 10 files and 500 added lines. Resolve uncertainties independently from available evidence, "
                "record concrete choices and rationale in assumptions, and leave questions empty. " if role == "plan" else "")
    review_rule = ("Return only actionable defects in findings. A pass requires findings to be an empty array; "
                   "a fail requires at least one actionable finding. Put observations in evidence. "
                   if role in REVIEW_ROLES else "")
    if role == "blind-behavior":
        review_rule += "If the preview or browser tools are unavailable, return fail with a finding starting exactly ENVIRONMENT-UNAVAILABLE:. "
    if request.get("engineering_policy"):
        review_rule += "Apply the supplied engineering_policy to this assignment and record supporting evidence. "
    prompt = ("Perform only the assigned role and work in the provided workspace. Resolve legacy planning questions using available evidence and document concrete assumptions; do not wait for answers. " + planning +
              review_rule +
              "Never publish, send messages, deploy, or contact production systems. "
              "Use only the supplied criteria and context; report uncertainty.\n\n" + json.dumps(request, ensure_ascii=False))
    result = subprocess.run(command, input=prompt, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    if result.returncode:
        raise RuntimeError("Claude Code exited with status " + str(result.returncode))
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
