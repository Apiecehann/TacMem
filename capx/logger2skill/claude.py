from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .processes import without_proxy
from .storage import read_json, write_json


def usage_from_stream(path: Path) -> dict:
    final = None
    messages = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("type") == "result":
                final = item
            if item.get("type") == "assistant":
                message = item.get("message", {})
                if message.get("id") and isinstance(message.get("usage"), dict):
                    messages[message["id"]] = message["usage"]
    model_usage = final.get("modelUsage") if final else None
    field_names = {"input_tokens": "inputTokens", "output_tokens": "outputTokens",
                   "cache_read_input_tokens": "cacheReadInputTokens",
                   "cache_creation_input_tokens": "cacheCreationInputTokens"}
    if (isinstance(model_usage, dict) and model_usage
            and all(isinstance(row, dict) and "inputTokens" in row and "outputTokens" in row
                    for row in model_usage.values())):
        rows = [{key: row.get(name, 0) for key, name in field_names.items()} for row in model_usage.values()]
        complete, source = True, "result_model_usage"
    else:
        usage = final.get("usage") if final else None
        complete = isinstance(usage, dict) and "input_tokens" in usage and "output_tokens" in usage
        rows = [usage] if complete else list(messages.values())
        source = "result" if complete else "partial_assistant_messages"
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    counts = {field: sum(int(r.get(field, 0)) for r in rows) if rows else None for field in fields}
    counts.update(complete=complete, source=source,
                  total_tokens=sum(counts.values()) if rows else None,
                  result_error=bool(final.get("is_error")) if final else None)
    return counts


def claude_environment(work: Path, config: dict) -> dict:
    env = without_proxy()
    settings = Path(config.get("credential_settings", "~/.claude/settings.json")).expanduser()
    values = read_json(settings).get("env", {}) if settings.is_file() else {}
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        if key not in env and key in values:
            env[key] = str(values[key])
    for key in list(env):
        if key.startswith("CLAUDE") or key.startswith("ANTHROPIC_DEFAULT_"):
            env.pop(key)
    env.pop("ANTHROPIC_MODEL", None)
    env["CLAUDE_CONFIG_DIR"] = str(work / "session")
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(config.get("max_output_tokens", 8192))
    env["CLAUDE_CODE_MAX_RETRIES"] = "0"
    env["DISABLE_AUTO_COMPACT"] = "1"
    env["DISABLE_AUTOUPDATER"] = "1"
    (work / "session").mkdir(parents=True, exist_ok=True)
    return env


class ClaudeSession:
    def __init__(self, work: Path, config: dict, deadline: float, *, max_calls: int | None = None):
        self.work, self.config, self.deadline = work, config, deadline
        self.calls = []
        self.failed = False
        self.max_calls = max_calls

    def ask(self, prompt: str, *, stage: str) -> str:
        if self.failed:
            raise RuntimeError("A curator request already failed; automatic retry is disabled")
        if self.max_calls is not None and len(self.calls) >= self.max_calls:
            self.failed = True
            raise RuntimeError("Curator model request limit reached; no follow-up request is allowed")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.failed = True
            raise TimeoutError("Curator budget exhausted")
        call_dir = self.work / "calls" / f"{len(self.calls):03d}"
        call_dir.mkdir(parents=True, exist_ok=False)
        (call_dir / "request.txt").write_text(prompt, encoding="utf-8")
        settings = {"disableAllHooks": True, "autoMemoryEnabled": False, "enabledPlugins": {},
                    "env": {key: "" for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                                    "http_proxy", "https_proxy", "all_proxy")}}
        command = [self.config.get("command", "claude"), "-p", "--model", self.config["model"],
                   "--name", f"logger2skill-{stage}",
                   "--output-format", "stream-json", "--verbose", "--no-session-persistence",
                   "--setting-sources", "", "--settings", json.dumps(settings),
                   "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--disable-slash-commands", "--permission-mode", "dontAsk",
                   "--tools", "", "--max-turns", "1",
                   "--system-prompt", "You curate robot skills from the supplied evidence. "
                   "Return only the requested data. Do not use tools or ask follow-up questions."]
        stream = call_dir / "response.jsonl"
        started = time.monotonic()
        record = {"stage": stage, "stream": str(stream.relative_to(self.work)),
                  "max_turns": 1, "tools": [], "max_retries": 0}
        self.calls.append(record)
        write_json(self.work / "calls.json", self.calls)
        try:
            with stream.open("w", encoding="utf-8") as output, (call_dir / "stderr.txt").open("w", encoding="utf-8") as err:
                proc = subprocess.Popen(command, cwd=self.work, env=claude_environment(self.work, self.config),
                                        stdin=subprocess.PIPE, stdout=output, stderr=err,
                                        text=True, encoding="utf-8")
                try:
                    proc.communicate(input=prompt, timeout=remaining)
                except BaseException:
                    proc.kill()
                    proc.wait()
                    raise
            record["exit_code"] = proc.returncode
            if proc.returncode:
                raise RuntimeError(f"Claude returned {proc.returncode}; see {call_dir / 'stderr.txt'}")
            results = []
            assistant_ids = set()
            tool_uses = 0
            for line in stream.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("type") == "result":
                    results.append(item)
                if item.get("type") == "assistant":
                    message = item.get("message", {})
                    assistant_ids.add(message.get("id") or f"unknown-{len(assistant_ids)}")
                    tool_uses += sum(block.get("type") in {"tool_use", "server_tool_use"}
                                     for block in message.get("content", []) if isinstance(block, dict))
            record.update(assistant_messages=len(assistant_ids), tool_uses=tool_uses)
            if len(results) != 1 or results[0].get("is_error"):
                raise RuntimeError("Claude did not return a single successful terminal result")
            record["num_turns"] = results[0].get("num_turns")
            if len(assistant_ids) > 1 or tool_uses or (results[0].get("num_turns") or 0) > 1:
                raise RuntimeError("Claude exceeded the single-response, no-tools contract")
            return str(results[0].get("result", ""))
        except BaseException:
            self.failed = True
            raise
        finally:
            record.update(elapsed_s=time.monotonic() - started, usage=usage_from_stream(stream))
            write_json(self.work / "calls.json", self.calls)


def summarize_calls(work: Path) -> dict:
    calls = read_json(work / "calls.json") if (work / "calls.json").is_file() else []
    for call in calls:
        call["usage"] = usage_from_stream(work / call["stream"])
    totals = {}
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "total_tokens"):
        values = [c["usage"][key] for c in calls]
        totals[key] = sum(x for x in values if x is not None) if any(x is not None for x in values) else (0 if not calls else None)
    totals.update(calls=len(calls), complete=all(c["usage"]["complete"] for c in calls))
    return totals
