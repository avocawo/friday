# Security policy

Friday runs coding agents on your machine, gives them a sandbox, and lets them reach a browser and a staging site only through allowlists. A bug in any of those boundaries is a security issue, even if it looks like an ordinary bug.

## Reporting a vulnerability

Please report it privately through [GitHub's private vulnerability reporting](https://github.com/avocawo/friday/security/advisories/new). Do not open a public issue, pull request or discussion about it.

Include what you can:

- what an attacker gains: for example, the executor escapes its sandbox, an agent reaches the network, a token or credential reaches an agent or a log, or the browser leaves its allowlist;
- the Friday version (`friday --version`), operating system and agent CLI versions;
- the profile settings involved, **with every secret removed**;
- steps to reproduce, or a proof of concept.

You will get an answer within 7 days. Once a fix is ready, it is released and the advisory is published with credit to you, unless you prefer to stay anonymous.

## Supported versions

Friday is in early development (0.x). Only the latest release gets security fixes.

## Scope

In scope: Friday's own code in this repository, its sandbox and allowlist settings for Claude Code and Codex, the gate and preview containers, and the browser session it scopes.

Out of scope: vulnerabilities in Claude Code, Codex, Docker, Chrome or chrome-devtools-mcp themselves (report those to their maintainers), and what an agent writes in your repository after you approve its plan.
