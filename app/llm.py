"""LLM backend. Everything else only calls llm_json(); swapping backends happens here."""

import json
import os
import subprocess
import tempfile

from . import config


class LLMError(Exception):
    """The call failed for this input. Counts as a failed attempt."""


class LLMUnavailable(Exception):
    """Usage limit, overload or connectivity. Retry next run without counting an attempt."""


TRANSIENT_MARKERS = ("rate limit", "usage limit", "limit reached", "overloaded", "529", "429",
                     "connection", "network", "timed out", "etimedout", "econnreset")


def llm_json(prompt: str, schema: dict, system: str) -> dict:
    return _claude_cli(prompt, schema, system)


def _claude_cli(prompt: str, schema: dict, system: str) -> dict:
    cfg = config.load()
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--system-prompt", system,
        "--model", cfg.claude_model,
        # The input is untrusted third-party text: no tools, no MCP servers, nothing persisted.
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        # Structured output is delivered through an internal tool call, so this needs 2 turns.
        "--max-turns", "3",
    ]
    env = {**os.environ, "DISABLE_AUTOUPDATER": "1"}
    # Empty working directory so no CLAUDE.md or project settings are picked up.
    with tempfile.TemporaryDirectory() as cwd:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  timeout=cfg.claude_timeout, cwd=cwd, env=env)
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude timed out after {cfg.claude_timeout}s") from e
        except FileNotFoundError as e:
            raise LLMUnavailable("claude CLI not found") from e

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        msg = (proc.stderr or proc.stdout).strip()[-500:]
        _raise_for(msg or f"exit code {proc.returncode}")

    if out.get("is_error") or out.get("subtype") != "success":
        status = out.get("api_error_status")
        msg = f"{out.get('subtype')}: {out.get('result') or ''} (status {status})"
        if status in (429, 529):
            raise LLMUnavailable(msg)
        _raise_for(msg)

    data = out.get("structured_output")
    if not isinstance(data, dict):
        raise LLMError("no structured_output in claude response")
    return data


def _raise_for(msg: str):
    if any(m in msg.lower() for m in TRANSIENT_MARKERS):
        raise LLMUnavailable(msg)
    raise LLMError(msg)
