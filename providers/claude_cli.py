"""Claude Code CLI provider — opt-in only, NEVER the default.

Spawns ``claude -p <prompt> --model <model> --output-format json`` for
hosts that ship the CLI in their image and want it to handle its own
authentication. No OAuth credential reading, no token-refresh hacks —
the CLI owns its auth (this deliberately drops the legacy agent service's
``~/.claude/.credentials.json`` plumbing).
"""
from __future__ import annotations

import json
import subprocess
import tempfile

from ..conf import agent_settings
from .base import LlmProvider, ProviderError, ProviderResult, ProviderTimeout


class ClaudeCodeCLIProvider(LlmProvider):
    name = "claude-code"

    def complete(
        self, *, prompt: str, model: str, system_prompt: str | None = None
    ) -> ProviderResult:
        args = [
            agent_settings.CLI_BINARY,
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
        ]
        if system_prompt:
            args += ["--system-prompt", system_prompt]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=int(agent_settings.CLI_TIMEOUT),
                cwd=tempfile.gettempdir(),
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                "claude CLI not found — install it or pick another provider"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeout("Execution timed out") from exc

        if proc.returncode != 0:
            raise ProviderError(
                proc.stderr.strip() or f"claude CLI exited with code {proc.returncode}"
            )

        stdout = (proc.stdout or "").strip()
        try:
            # Fields per the legacy agent service's ClaudeCodeResult interface.
            result = json.loads(stdout)
        except ValueError:
            # Non-JSON output — treat as plain text (the legacy agent service behaviour).
            return ProviderResult(text=stdout)
        if not isinstance(result, dict):
            return ProviderResult(text=stdout)

        if result.get("is_error"):
            raise ProviderError(str(result.get("result") or "claude CLI reported an error"))

        usage = result.get("usage") or {}
        return ProviderResult(
            text=str(result.get("result") or ""),
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
        )


__all__ = ["ClaudeCodeCLIProvider"]
