"""Friday JSON runner for the installed Codex CLI; no remote call until invoked."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from friday import codex_policy, schemas


def main():
    request = json.load(sys.stdin)
    role = request["role"]
    model = request["model"]
    if role not in ("plan", "research", "develop", "review", "blind-code", "blind-behavior") or not isinstance(model, str) or not model:
        raise ValueError("Invalid role or model")
    instructions = {
        "plan": "Research the ticket and repository, then produce a bounded implementation plan. Each stage must list exact files, at most 10 files and 500 added lines including tests, acceptance criteria and dependencies. Optionally propose a crew using only available_agents, their permitted roles and available_skills; use an empty crew when none are configured. Each nonempty crew must include review, blind-code and blind-behavior; all must depend on every developer. Choose only useful assignments, with concise separate briefs and dependencies. A reviewer must use a different vendor from developers. Preserve project rules. Resolve uncertainties independently from available evidence and record concrete choices in assumptions, including their rationale; keep questions empty. If information is unavailable, identify the limitation and choose a reversible approach without inventing facts. Suggest separately any work that should be split out. Do not edit files, publish, or send messages.",
        "develop": "Implement the given stage. Resolve any legacy planning questions using available evidence, document concrete assumptions in your output, and do not wait for answers. Leave local commits to Friday. Do not publish, send messages, deploy, or delete user data.",
        "research": "Investigate the assigned question and report findings with evidence. Do not edit files or send messages.",
        "review": "Review the supplied change against its criteria. Do not edit files or send messages.",
        "blind-code": "Review only the supplied diff against the criteria. Do not edit files or send messages.",
        "blind-behavior": "Verify only the supplied behavioral criteria using the provided preview and actual browser tools. Follow behavior_test_policy when present. Use real click, fill or keyboard input, not synthetic events; observe UI and relevant network requests. Do not call get_network_request on authenticated requests. If the preview or browser tools are unavailable, return fail with a finding starting exactly ENVIRONMENT-UNAVAILABLE:. If evidence is insufficient, return fail. Do not edit files or send messages.",
    }
    boundary = ("Only inspect files within the supplied worktree and the task data in this request. "
                "Do not inspect parent directories, other projects, personal files, or credentials. "
                "If necessary information is outside this boundary, report it as unavailable.")
    if request.get("engineering_policy"):
        boundary += " Apply the supplied engineering_policy to this assignment and record supporting evidence."
    prompt = boundary + "\n\n" + instructions[role] + "\n\n" + json.dumps(request, ensure_ascii=False, indent=2)
    review = role in ("review", "blind-code", "blind-behavior")
    if review:
        prompt += "\n\nReturn pass only if the criteria are supported by evidence and no actionable issue remains."
    with tempfile.TemporaryDirectory(prefix="friday-adapter-") as directory:
        output = Path(directory) / "last-message"
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("Codex CLI is not available")
        codex_policy.verify_environment(executable, Path.cwd().resolve())
        source = Path(os.path.realpath(executable))
        local_executable = Path(directory) / "codex"
        for original, destination in ((source, local_executable), (source.parent / "codex-code-mode-host", Path(directory) / "codex-code-mode-host")):
            if not original.is_file():
                continue
            try:
                os.link(original, destination)
            except OSError:
                shutil.copy2(original, destination)
        command = [str(local_executable), "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                   "--disable", "hooks", "--disable", "plugins", "--disable", "apps",
                   "--disable", "browser_use", "--disable", "computer_use",
                   "--disable", "responses_websockets", "--disable", "responses_websockets_v2",
                   *codex_policy.options(role, directory), "-c", 'web_search="disabled"',
                   "-c", 'approval_policy="never"', "--model", model,
                   "--output-last-message", str(output)]
        if role == "blind-behavior":
            from friday.browser_session import TOOLS, configuration
            spec = json.loads(os.environ["FRIDAY_BROWSER_CONFIG"])
            preview = request.get("preview_url")
            configuration(spec, preview)
            server = str(Path(__file__).resolve().parent / "browser_mcp.py")
            command.extend(["-c", "mcp_servers.friday.command=" + json.dumps(sys.executable),
                            "-c", "mcp_servers.friday.args=" + json.dumps([server, json.dumps(spec), preview]),
                            "-c", "mcp_servers.friday.required=true",
                            "-c", "mcp_servers.friday.startup_timeout_sec=180"])
            for tool in TOOLS:
                command.extend(["-c", "mcp_servers.friday.tools." + tool + '.approval_mode="approve"'])
        if request.get("context_only") is True:
            command.extend(["--disable", "shell_tool"])
        if review:
            schema = Path(directory) / "schema.json"
            schema.write_text(json.dumps(schemas.review()))
            command.extend(["--output-schema", str(schema)])
        elif role == "plan":
            schema = Path(directory) / "schema.json"
            schema.write_text(json.dumps(schemas.plan()))
            command.extend(["--output-schema", str(schema)])
        command.append("-")
        result = subprocess.run(command, input=prompt, text=True, stdout=subprocess.DEVNULL)
        if result.returncode:
            return result.returncode
        content = output.read_text(encoding="utf-8")
        if review:
            value = json.loads(content)
            schemas.validate_review(value, strict=True)
        print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
