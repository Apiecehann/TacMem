"""LLM client utilities for querying language models.

Extracted from capx/utils/launch_utils.py to separate LLM query logic
from launch/config utilities.
"""

from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import random
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from capx.envs.launch import LaunchArgs

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

GPT_MODELS = [
    "openai/gpt-5.4",
    "openai/o4-mini",
]
# Models the provider serves ONLY over the Responses API.  They cannot simply be
# added to GPT_MODELS: that list also picks the chat-completions payload, and
# these names are rejected there outright ("protocol_not_supported", 2026-10-13,
# gpt-6-sol and gpt-6-astra on api.codexzh.com).  Reach them through a server
# that speaks the Responses protocol -- capx/serving/openrouter_server.py
# exposes /responses.
RESPONSES_MODELS = [
    "gpt-6-sol",
    "gpt-6-astra",
]
VLM_MODELS = [
    # The gpt-6 line is Responses-only (see RESPONSES_MODELS above) but it is
    # still a vision-language model, and the piano task's visual-differencing
    # channel names it.  A name missing here fails the trial on an assertion
    # before the first action, which is how this entry was found missing.
    "gpt-6-astra",
    "google/gemini-3.1-pro-preview",
    "google/gemini-2.5-flash-lite",
    "anthropic/claude-opus-4-5",
    "anthropic/claude-haiku-4-5",
    "openai/gpt-5.4",
    "openai/o1",
    "openai/o4-mini",
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-r1-0528",
    "deepseek/deepseek-r1",
    "qwen/qwen3.5-122b-a10b",
    "moonshotai/kimi-k2",
    "moonshotai/kimi-k2.6",
    "meta-llama/llama-4-maverick",
]
CLAUDE_MODELS = ["anthropic/claude-opus-4-5", "anthropic/claude-haiku-4-5"]
OSS_MODELS = [
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-r1-0528",
    "deepseek/deepseek-r1",
    "qwen/qwen3.5-122b-a10b",
    "moonshotai/kimi-k2",
]
OPENROUTER_MODELS = [
    "openrouter/google/gemini-2.5-pro-preview",
    "openrouter/google/gemini-2.5-flash-preview",
    "openrouter/anthropic/claude-sonnet-4",
    "openrouter/anthropic/claude-opus-4",
    "openrouter/deepseek/deepseek-r1",
    "openrouter/deepseek/deepseek-chat-v3-0324",
    "openrouter/openai/gpt-4.1",
    "openrouter/openai/o4-mini",
    "openrouter/meta-llama/llama-4-maverick",
    "openrouter/qwen/qwen3-235b-a22b",
]
OPENROUTER_SERVER_URL = "http://localhost:8110/chat/completions"

# ---------------------------------------------------------------------------
# Ensemble configuration
# ---------------------------------------------------------------------------

ENSEMBLE_CONFIGS = [
    # Gemini-3-Pro only — best single model per CaP-Bench (Figure 1).
    # 3 temps for diversity; synthesis still uses Gemini-3-Pro.
    # ~45% faster than full multimodel (no Claude/GPT latency bottleneck).
    ("openai/gpt-5.4", [0.1, 0.5, 0.9]),
]

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _strip_openrouter_prefix(model: str) -> str:
    """Strip 'openrouter/' prefix if present, returning the native model ID."""
    if model.startswith("openrouter/"):
        return model[len("openrouter/"):]
    return model


def is_openrouter_model(model: str) -> bool:
    """Return True if the model should be routed through the OpenRouter proxy."""
    return model.startswith("openrouter/") or model in OPENROUTER_MODELS


def is_responses_model(model: str) -> bool:
    """Return True if the model has to be queried through the Responses API.

    The gpt-6 line is matched by prefix, not by name: the whole family is
    Responses-only on this provider, and a name missing from the list does not
    degrade gracefully -- the request lands on chat completions, the provider
    answers 400 "protocol_not_supported" wrapped in a 500, and query_model
    retries that 500 forever (2026-10-13: gpt-6-sol, then gpt-6-astra, the
    second one inside a running trial).
    """
    return (
        model in RESPONSES_MODELS
        or model.startswith("gpt-6")
        or (model in GPT_MODELS and "codex" in model)
    )


def _responses_server_url(server_url: str) -> str:
    """Point a chat-completions server URL at the same server's Responses route.

    Callers default to the chat URL (launch.py: --server-url), so without this
    a responses-only model would POST a Responses payload at /chat/completions.
    """
    if server_url.endswith("/chat/completions"):
        return server_url[: -len("/chat/completions")] + "/responses"
    if server_url.rstrip("/").endswith("/v1"):
        return server_url.rstrip("/") + "/responses"
    return server_url


def _responses_content(body: dict) -> str | None:
    """Extract the text of a Responses API body.

    The local proxy adds a top-level ``output_text``; a bare Responses endpoint
    does not carry one (it is an SDK convenience, not part of the wire format),
    so fall back to walking the output message items.
    """
    if body.get("output_text") is not None:
        return body["output_text"]
    parts = [
        part.get("text", "")
        for item in body.get("output", [])
        for part in (item.get("content") or [])
        if part.get("type") == "output_text"
    ]
    return "".join(parts) or None


def _read_responses_stream(response, folder: Path | None, deadline: float):
    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
        if folder is not None:
            (folder / "response.json").write_text(response.text, encoding="utf-8")
        raise RuntimeError("Responses requires SSE; non-streaming response rejected")
    response.encoding = "utf-8"
    stream_log = (folder / "response.events.sse").open("w", encoding="utf-8") if folder else None
    deltas = {}
    finalized = {}
    final_response = None

    def finish_part(key, text):
        if not isinstance(text, str):
            raise RuntimeError("Responses stream contains invalid text")
        if key in deltas and "".join(deltas[key]) != text:
            raise RuntimeError("Responses stream text does not match its final text")
        if key in finalized and finalized[key] != text:
            raise RuntimeError("Responses stream contains conflicting final text")
        finalized[key] = text

    def consume(data):
        nonlocal final_response
        if data == "[DONE]":
            return True
        event = json.loads(data)
        kind = event.get("type")
        key = (event.get("output_index", 0), event.get("content_index", 0))
        if kind == "response.output_text.delta":
            deltas.setdefault(key, []).append(event["delta"])
        elif kind == "response.output_text.done":
            finish_part(key, event["text"])
        elif kind == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "message" and item.get("status") == "completed":
                for index, part in enumerate(item.get("content") or []):
                    if part.get("type") == "output_text":
                        finish_part((key[0], index), part["text"])
        elif kind in {"response.completed", "response.failed", "response.incomplete"}:
            final_response = event.get("response", {})
            if folder is not None:
                (folder / "response.json").write_text(json.dumps(final_response, ensure_ascii=False), encoding="utf-8")
            if kind != "response.completed" or final_response.get("status") != "completed" or final_response.get("error"):
                raise RuntimeError(f"Responses stream ended with {kind}")
            return True
        elif kind == "error":
            raise RuntimeError("Responses stream reported an error")
        return False

    data_lines = []
    try:
        for line in response.iter_lines(decode_unicode=True):
            if time.monotonic() >= deadline:
                raise TimeoutError("Responses stream exceeded the model request deadline")
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            if stream_log is not None:
                stream_log.write(line + "\n")
                stream_log.flush()
            if not line:
                if data_lines:
                    data = "\n".join(data_lines)
                    data_lines = []
                    if consume(data):
                        break
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
        else:
            if data_lines:
                consume("\n".join(data_lines))
        if final_response is None:
            raise RuntimeError("Responses stream ended without response.completed")
        body_text = _responses_content(final_response)
        text = "".join(finalized[key] for key in sorted(finalized)) if finalized else body_text
        if deltas and not set(deltas).issubset(finalized):
            delta_text = "".join("".join(deltas[key]) for key in sorted(deltas))
            if not body_text or delta_text != body_text:
                raise RuntimeError("Responses stream ended with unfinished text")
        if body_text and text != body_text:
            raise RuntimeError("Responses final response disagrees with streamed text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Responses stream completed without output text")
        if folder is not None:
            (folder / "response_text.txt").write_text(text, encoding="utf-8")
        return final_response, text
    finally:
        if stream_log is not None:
            stream_log.close()


def is_vlm_model(model: str) -> bool:
    """Return True if the model is a vision-language model (VLM)."""
    return _strip_openrouter_prefix(model) in VLM_MODELS


#: Every model call's usage since :func:`reset_token_usage` was last called.
#: The trial harness reads the sum once per trial and writes it into the
#: summary; the API's own usage block is the only honest source, and this is
#: where it arrives.
_TOKEN_USAGE: list[dict[str, int]] = []


def _usage_from_body(body: dict) -> dict[str, int]:
    """Normalize the usage block of either protocol into one dict.

    The Responses API reports ``input_tokens`` / ``output_tokens`` and puts
    the reasoning subset under ``output_tokens_details``; chat completions
    reports ``prompt_tokens`` / ``completion_tokens``.  A provider that
    reports nothing yields zeros rather than an error -- a missing usage
    block must not fail a trial.
    """
    usage = body.get("usage") or {}
    prompt_details = (
        usage.get("input_tokens_details")
        or usage.get("prompt_tokens_details")
        or {}
    )
    completion_details = (
        usage.get("output_tokens_details")
        or usage.get("completion_tokens_details")
        or {}
    )
    return {
        "input_tokens": int(
            usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        "output_tokens": int(
            usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(
            completion_details.get("reasoning_tokens") or 0),
        "cached_tokens": int(prompt_details.get("cached_tokens") or 0),
    }


def reset_token_usage() -> None:
    """Forget the calls so far; the trial harness calls this at trial start."""
    _TOKEN_USAGE.clear()


def token_usage() -> dict[str, int]:
    """The summed usage of every model call since the last reset.

    ``output_tokens`` is the API's own output count and already includes
    ``reasoning_tokens``, which is reported alongside it because a reasoning
    model's visible text is only part of what it is billed for.
    """
    total = {
        "calls": len(_TOKEN_USAGE),
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
    }
    for usage in _TOKEN_USAGE:
        for key, value in usage.items():
            total[key] += value
    return total


@dataclass
class ModelQueryArgs:
    """Arguments for querying a model."""

    model: str
    server_url: str
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4096
    reasoning_effort: str = "medium"
    debug: bool = False


def collapse_text_image_inputs(messages: list[dict]) -> list[dict]:
    """
    Collapse a list of messages with sequential text into a single text input, images are still in the same relative position
    """
    new_prompt = []
    current_text_input = ""
    for message in messages:
        if message["type"] == "text":
            current_text_input += message["text"] + "\n"
        else:
            if current_text_input != "":
                new_prompt.append({"type": "text", "text": current_text_input})
                current_text_input = ""
            new_prompt.append(message)
    if current_text_input != "":
        new_prompt.append({"type": "text", "text": current_text_input})
    return new_prompt


def _completions_to_responses_convert_prompt(prompt: list[dict]) -> list[dict]:
    """Convert completions api format to responses api format.

    Args:
        prompt: The prompt in completions api format

    Returns:
        The prompt in responses api format

    Switch prompt structure to api responses api format e.g.:
    From
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe the image in detail."},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }
                }
            ]
        }
    ]
    To
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Describe the image in detail."},
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{base64_image}"
                }
            ]
        }
    ]
    """

    for message in prompt:
        for content in message["content"]:
            if type(content) == str:
                continue
            if content.get("type") == "text":
                content["type"] = "input_text"
                content["text"] = content.pop("text")

            elif content.get("type") == "image_url":
                content["type"] = "input_image"
                content["image_url"] = content["image_url"]["url"]
    return prompt


# ---------------------------------------------------------------------------
# Core query functions
# ---------------------------------------------------------------------------


def _query_responses(args, prompt):
    if getattr(args, "single_program", False):
        if getattr(args, "_code_request_count", 0):
            raise RuntimeError("The single-program request budget is exhausted")
        args._code_request_count = 1
    payload = {
        "model": args.model,
        "input": _completions_to_responses_convert_prompt(copy.deepcopy(prompt)),
        "reasoning": {"effort": args.reasoning_effort},
        "max_output_tokens": args.max_tokens,
        "stream": True,
    }
    folder_name = getattr(args, "request_log_dir", None) or os.getenv("CAPX_MODEL_LOG_DIR")
    folder = Path(folder_name) if folder_name else None
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "request.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    timeout = float(getattr(args, "model_timeout_seconds", 600))
    started = time.monotonic()
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    key = args.api_key or os.getenv("CAPX_API_KEY") or os.getenv("OPENAI_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    status = {"completed": False, "stream": True, "http_status": None}
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(_responses_server_url(args.server_url), headers=headers,
                              json=payload, stream=True, timeout=(30, timeout)) as response:
                status["http_status"] = response.status_code
                if response.status_code != 200 and folder is not None:
                    (folder / "response.json").write_text(response.text, encoding="utf-8")
                response.raise_for_status()
                body, content = _read_responses_stream(response, folder, started + timeout)
        usage = _usage_from_body(body)
        _TOKEN_USAGE.append(usage)
        status.update(completed=True, usage=body.get("usage"), content_chars=len(content))
        return {"content": content, "reasoning": None, "usage": usage}
    except BaseException as exc:
        status.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        status["elapsed_seconds"] = time.monotonic() - started
        if folder is not None:
            (folder / "request_status.json").write_text(json.dumps(status), encoding="utf-8")


def query_model(args: LaunchArgs | ModelQueryArgs, prompt: list[dict]) -> str:
    """Query vLLM server for code generation.

    Args:
        args: Configuration with server URL and model settings
        prompt: Full prompt containing environment observation and possibly multi-turn decision prompt
    Returns:
        Model response content
    """

    if is_responses_model(args.model):
        return _query_responses(args, prompt)

    # Route OpenRouter models to the OpenRouter proxy server
    if is_openrouter_model(args.model):
        server_url = OPENROUTER_SERVER_URL
    elif is_responses_model(args.model):
        server_url = _responses_server_url(args.server_url)
        if server_url != args.server_url:
            print(f"Responses-API model {args.model}: querying {server_url}")
    else:
        server_url = args.server_url

    if is_responses_model(args.model):
        prompt = _completions_to_responses_convert_prompt(prompt)
        payload = {
            "model": args.model,
            "input": prompt,
            # Same knob as the chat path, under the Responses API's own name.
            # Left unset the provider picks its default, which for reasoning
            # models is the slow-and-expensive direction.
            "reasoning": {"effort": args.reasoning_effort},
        }
    elif args.model in GPT_MODELS:
        payload = {
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "max_completion_tokens": args.max_tokens,  # Total completion tokens = reasoning + output tokens
            "messages": prompt,
        }
    elif is_openrouter_model(args.model):
        # reasoning_effort matters far more than max_tokens here: left unset,
        # reasoning models deliberate until the whole budget is gone, so the
        # request returns finish_reason="length" with content=None and the
        # whole trial dies (2026-09-13, glm-5.3-flash: 47.8k reasoning tokens
        # / 390 s unset, vs 693 tokens / 28 s at "medium").
        payload = {
            "model": args.model,
            "messages": prompt,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "reasoning_effort": args.reasoning_effort,
        }
    elif args.model in CLAUDE_MODELS:
        payload = {
            "model": args.model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "messages": prompt,
        }
    elif args.model in OSS_MODELS:
        payload = {
            "model": args.model,
            "messages": prompt,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
        }
    else:
        payload = {
            "model": args.model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "messages": prompt,
        }
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    elif os.getenv("OPENAI_API_KEY") is not None and args.model in GPT_MODELS:
        headers["Authorization"] = f"Bearer {os.getenv('OPENAI_API_KEY')}"
    start_time = time.time()

    # keep calling until it works
    response = requests.post(
        server_url, headers=headers, data=json.dumps(payload), timeout=600
    )
    retry = 1
    while response.status_code in [404, 500, 502, 503, 504]:
        sleep_time = 240 + random.uniform(-90, 90)
        print(f"Retry {retry}. Model query failed with status code {response.status_code}. Error: {response.text}. Retrying in {sleep_time} seconds...")
        time.sleep(sleep_time)
        response = requests.post(
            server_url, headers=headers, data=json.dumps(payload), timeout=600
        )
        retry += 1

    end_time = time.time()
    print(f"Time taken to query model: {end_time - start_time:.2f} seconds")
    response.raise_for_status()
    body = response.json()
    out = {}
    if args.debug:
        print(json.dumps(body, indent=2))
    try:
        if is_responses_model(args.model):
            out["content"] = _responses_content(body)
        else:
            out["content"] = body["choices"][0]["message"]["content"]
        if out["content"] is None:
            raise RuntimeError(
                f"Model returned None content. "
                f"Raw response: {json.dumps(body, indent=2)[:500]}"
            )
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected response format: {body}") from exc
    if body.get("choices") is not None:
        out["reasoning"] = body.get("choices")[0].get("message").get("reasoning", None)
    else:
        out["reasoning"] = None
    # The API's own count of what this call cost.  Kept on the accumulator as
    # well as on the reply, so the trial harness can sum it without threading
    # a return value through every caller (policy, VDM, ensemble).
    usage = _usage_from_body(body)
    _TOKEN_USAGE.append(usage)
    out["usage"] = usage
    return out  # type: ignore[return-value]


def query_model_streaming(
    args: LaunchArgs | ModelQueryArgs,
    prompt: list[dict],
) -> Iterable[dict]:
    """Query model with streaming enabled, yielding partial responses.

    Yields dictionaries with:
      - {"type": "content_delta", "content": "partial text"}
      - {"type": "reasoning_delta", "content": "partial reasoning"} (if supported)
      - {"type": "done", "content": "full content", "reasoning": "full reasoning or None"}

    Args:
        args: Configuration with server URL and model settings
        prompt: Full prompt containing environment observation

    Yields:
        Partial response chunks as they arrive
    """
    if args.model in GPT_MODELS:
        payload = {
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "max_completion_tokens": args.max_tokens,
            "messages": prompt,
            "stream": True,
        }
    elif args.model in CLAUDE_MODELS:
        payload = {
            "model": args.model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "messages": prompt,
            "stream": True,
        }
    else:
        payload = {
            "model": args.model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "messages": prompt,
            "stream": True,
        }

    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    elif os.getenv("OPENAI_API_KEY") is not None and args.model in GPT_MODELS:
        headers["Authorization"] = f"Bearer {os.getenv('OPENAI_API_KEY')}"

    full_content = ""
    full_reasoning = ""

    start_time = time.time()

    with requests.post(
        args.server_url,
        headers=headers,
        data=json.dumps(payload),
        timeout=600,
        stream=True,
    ) as response:
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        is_sse = "text/event-stream" in content_type
        is_json = "application/json" in content_type

        # If it's a regular JSON response (server doesn't support streaming),
        # fall back to non-streaming behavior
        if is_json and not is_sse:
            print("Warning: Server returned JSON instead of SSE stream, falling back to non-streaming")
            body = response.json()
            try:
                full_content = body["choices"][0]["message"]["content"]
                full_reasoning = body.get("choices", [{}])[0].get("message", {}).get("reasoning")
                if full_reasoning:
                    print(f"Reasoning extracted ({len(full_reasoning)} chars)")
                else:
                    print("No reasoning returned by model")
            except (KeyError, IndexError) as exc:
                raise RuntimeError(f"Unexpected response format: {body}") from exc

            yield {"type": "content_delta", "content": full_content}
            yield {
                "type": "done",
                "content": full_content,
                "reasoning": full_reasoning if full_reasoning else None,
            }
            end_time = time.time()
            print(f"Time taken to query model (streaming fallback): {end_time - start_time:.2f} seconds")
            return

        for line in response.iter_lines():
            if not line:
                continue

            line_str = line.decode("utf-8")

            # SSE format: "data: {...}" or "data: [DONE]"
            if line_str.startswith("data: "):
                data_str = line_str[6:]  # Remove "data: " prefix

                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})

                    # Handle content delta
                    content_delta = delta.get("content", "")
                    if content_delta:
                        full_content += content_delta
                        yield {"type": "content_delta", "content": content_delta}

                    # Handle reasoning delta (some APIs support this)
                    reasoning_delta = delta.get("reasoning", "")
                    if reasoning_delta:
                        full_reasoning += reasoning_delta
                        yield {"type": "reasoning_delta", "content": reasoning_delta}

                except json.JSONDecodeError:
                    continue
            else:
                # Try parsing as raw JSON (non-SSE format)
                try:
                    data = json.loads(line_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content_delta = delta.get("content", "")
                        if content_delta:
                            full_content += content_delta
                            yield {"type": "content_delta", "content": content_delta}
                except json.JSONDecodeError:
                    continue

    end_time = time.time()
    print(f"Time taken to query model (streaming): {end_time - start_time:.2f} seconds")
    if full_reasoning:
        print(f"Reasoning extracted ({len(full_reasoning)} chars)")
    else:
        print("No reasoning returned by model")

    yield {
        "type": "done",
        "content": full_content,
        "reasoning": full_reasoning if full_reasoning else None,
    }


def query_model_ensemble(
    args: LaunchArgs | ModelQueryArgs,
    prompt: list[dict],
    synthesis_model: str = "openai/gpt-5.4",
    is_multiturn = False
) -> dict[str, Any]:
    """Query 9 models (3 models x 3 temperatures) and synthesize final output."""

    def query_single(model: str, temp: float) -> dict:
        query_args = ModelQueryArgs(
            model=model,
            server_url=args.server_url,
            api_key=args.api_key,
            temperature=temp,
            max_tokens=args.max_tokens,
            reasoning_effort=getattr(args, "reasoning_effort", "medium"),
        )
        try:
            result = query_model(query_args, copy.deepcopy(prompt))
            return {"model": model, "temp": temp, "content": result["content"], "ok": True}
        except Exception as e:
            error_msg = str(e)
            print(f"[Multimodel Ensemble] {model} temp={temp} FAILED: {error_msg}")
            return {"model": model, "temp": temp, "content": error_msg, "ok": False}

    # Build all (model, temp) pairs and query in parallel
    tasks = [(m, t) for m, temps in ENSEMBLE_CONFIGS for t in temps]
    responses = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as executor:
        futures = {executor.submit(query_single, m, t): (m, t) for m, t in tasks}
        for future in concurrent.futures.as_completed(futures):
            resp = future.result()
            responses.append(resp)
            if resp['ok']:
                print(f"[Multimodel Ensemble] {resp['model']} temp={resp['temp']} ok={resp['ok']}")

    successful = [r for r in responses if r["ok"]]
    if not successful:
        # Print all errors for debugging
        print("\n=== All ensemble queries failed. Errors: ===")
        for r in responses:
            print(f"  {r['model']} temp={r['temp']}: {r['content']}")
        raise RuntimeError("All ensemble queries failed")

    # Build synthesis prompt
    original_text = ""
    for msg in prompt:
        if msg["role"] == "user":
            c = msg["content"]
            if isinstance(c, list):
                original_text += "".join(x.get("text", "") for x in c if isinstance(x, dict))
            elif isinstance(c, str):
                original_text += c

    candidates = "\n\n".join(
        f"--- Candidate ({r['model']}, temp={r['temp']}) ---\n{r['content']}"
        for r in successful
    )

    # Detect if this is a multiturn decision (candidates contain REGENERATE/FINISH)
    regenerate_count = sum(1 for r in successful if isinstance(r.get("content"), str) and "REGENERATE" in r["content"])
    finish_count = sum(1 for r in successful if isinstance(r.get("content"), str) and "FINISH" in r["content"])

    if is_multiturn:
        synthesis_system_prompt = f"""You are synthesizing {len(successful)} candidate responses for a multi-turn robot control task.

    DECISION ANALYSIS:
    - {regenerate_count} candidates voted REGENERATE
    - {finish_count} candidates voted FINISH

    SYNTHESIS RULES:
    1. Analyze critically and assume no candidate is fully correct
    2. Prefer explicit checks over assumptions
    3. Combine the best ideas from multiple candidates when appropriate
    4. If candidates disagree fundamentally, choose the more robust approach
    5. Combine best code ideas from REGENERATE candidates

    OUTPUT FORMAT (strict):
    - You may include brief reasoning first
    - Then output "REGENERATE" on its own line followed by exactly ONE fenced code block, OR output "FINISH" on its own line
    """
    else:
        synthesis_system_prompt = f"""You are synthesizing {len(successful)} candidate Python solutions into one optimal program.

    SYNTHESIS RULES:
    1. Analyze critically and assume no candidate is fully correct
    2. Prefer explicit checks over assumptions
    3. Combine the best ideas from multiple candidates when appropriate
    4. If candidates disagree fundamentally, choose the more robust approach

    OUTPUT FORMAT (strict):
    You may include reasoning before the fenced code block.
    Output ONLY ONE fenced code block (```python...```) containing the complete final solution.
    Do NOT include any other code blocks or code snippets outside this single block.
    """

    synthesis_user_prompt = f"""Synthesize the best solution.

    <original_task_description>
    {original_text}
    </original_task_description>

    <candidate_solutions>
    {candidates}
    </candidate_solutions>
    """

    synthesis_prompt = [
        {
            "role": "system",
            "content": synthesis_system_prompt,
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": synthesis_user_prompt,
                }
            ],
        },
    ]

    synth_args = ModelQueryArgs(
        model=synthesis_model,
        server_url=args.server_url,
        api_key=args.api_key,
        temperature=0.2,
        max_tokens=args.max_tokens,
    )
    final = query_model(synth_args, synthesis_prompt)

    # Build text content for saving
    candidates_txt = "\n\n".join(
        f"{'='*60}\nModel: {r['model']}\nTemperature: {r['temp']}\nSuccess: {r['ok']}\n{'='*60}\n{r['content']}"
        for r in responses
    )
    synthesis_txt = f"Model: {synthesis_model}\n\n"
    synthesis_txt += f"{'='*60}\nREASONING\n{'='*60}\n{final.get('reasoning') or '(none)'}\n\n"
    synthesis_txt += f"{'='*60}\nOUTPUT\n{'='*60}\n{final['content']}"

    return {
        "content": final["content"],
        "reasoning": final.get("reasoning"),
        "all_responses": responses,
        "ensemble_candidates_txt": candidates_txt,
        "ensemble_synthesis_txt": synthesis_txt,
    }


def query_single_model_ensemble(
    args: LaunchArgs | ModelQueryArgs,
    prompt: list[dict],
    model: str,
    is_multiturn = False,
) -> dict[str, Any]:
    """Query the same model 9 times (with temperatures 0.1 to 0.9) and synthesize final output.

    Args:
        args: Configuration with server URL and model settings
        prompt: Full prompt containing environment observation and possibly multi-turn decision prompt
        model: The model to use for both candidate generation and synthesis

    Returns:
        Dictionary containing synthesized content, reasoning, all responses, and text artifacts
    """

    def query_single(temp: float) -> dict:
        query_args = ModelQueryArgs(
            model=model,
            server_url=args.server_url,
            api_key=args.api_key,
            temperature=temp,
            max_tokens=args.max_tokens,
            reasoning_effort=getattr(args, "reasoning_effort", "medium"),
        )
        try:
            result = query_model(query_args, copy.deepcopy(prompt))
            return {"model": model, "temp": temp, "content": result["content"], "ok": True}
        except Exception as e:
            error_msg = str(e)
            print(f"[Single Model Ensemble] {model} temp={temp} FAILED: {error_msg}")
            return {"model": model, "temp": temp, "content": error_msg, "ok": False}

    # Query same model with 9 different temperatures (0.1 to 0.9)
    temperatures = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    responses = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as executor:
        futures = {executor.submit(query_single, t): t for t in temperatures}
        for future in concurrent.futures.as_completed(futures):
            resp = future.result()
            responses.append(resp)
            if resp['ok']:
                print(f"[Single Model Ensemble] {resp['model']} temp={resp['temp']} ok={resp['ok']}")

    successful = [r for r in responses if r["ok"]]
    if not successful:
        # Print all errors for debugging
        print("\n=== All single model ensemble queries failed. Errors: ===")
        for r in responses:
            print(f"  {r['model']} temp={r['temp']}: {r['content']}")
        raise RuntimeError("All single model ensemble queries failed")

    # Build synthesis prompt
    original_text = ""
    for msg in prompt:
        if msg["role"] == "user":
            c = msg["content"]
            if isinstance(c, list):
                original_text += "".join(x.get("text", "") for x in c if isinstance(x, dict))
            elif isinstance(c, str):
                original_text += c

    candidates = "\n\n".join(
        f"--- Candidate (temp={r['temp']}) ---\n{r['content']}"
        for r in successful
    )

    # Detect if this is a multiturn decision (candidates contain REGENERATE/FINISH)
    regenerate_count = sum(1 for r in successful if isinstance(r.get("content"), str) and "REGENERATE" in r["content"])
    finish_count = sum(1 for r in successful if isinstance(r.get("content"), str) and "FINISH" in r["content"])

    if is_multiturn:
        synthesis_system_prompt = f"""You are synthesizing {len(successful)} candidate responses for a multi-turn robot control task.

    DECISION ANALYSIS:
    - {regenerate_count} candidates voted REGENERATE
    - {finish_count} candidates voted FINISH

    SYNTHESIS RULES:
    1. Analyze critically and assume no candidate is fully correct
    2. Prefer explicit checks over assumptions
    3. Combine the best ideas from multiple candidates when appropriate
    4. If candidates disagree fundamentally, choose the more robust approach
    5. Combine best code ideas from REGENERATE candidates

    OUTPUT FORMAT (strict):
    - You may include brief reasoning first
    - Then output "REGENERATE" on its own line followed by exactly ONE fenced code block, OR output "FINISH" on its own line
    """
    else: # first generation has no REGEN/FINISH candidates
        synthesis_system_prompt = f"""You are synthesizing {len(successful)} candidate Python solutions into one optimal program.

    SYNTHESIS RULES:
    1. Analyze critically and assume no candidate is fully correct
    2. Prefer explicit checks over assumptions
    3. Combine the best ideas from multiple candidates when appropriate
    4. If candidates disagree fundamentally, choose the more robust approach

    OUTPUT FORMAT (strict):
    You may include reasoning before the fenced code block.
    Output ONLY ONE fenced code block (```python...```) containing the complete final solution.
    Do NOT include any other code blocks or code snippets outside this single block.
    """

    synthesis_user_prompt = f"""Synthesize the best solution.

    <original_task_description>
    {original_text}
    </original_task_description>

    <candidate_solutions>
    {candidates}
    </candidate_solutions>
    """

    synthesis_prompt = [
        {
            "role": "system",
            "content": synthesis_system_prompt,
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": synthesis_user_prompt,
                }
            ],
        },
    ]

    # Use the same model for synthesis
    synth_args = ModelQueryArgs(
        model=model,
        server_url=args.server_url,
        api_key=args.api_key,
        temperature=0.2,
        max_tokens=args.max_tokens,
    )
    final = query_model(synth_args, synthesis_prompt)

    # Build text content for saving
    candidates_txt = "\n\n".join(
        f"{'='*60}\nModel: {r['model']}\nTemperature: {r['temp']}\nSuccess: {r['ok']}\n{'='*60}\n{r['content']}"
        for r in responses
    )
    synthesis_txt = f"Model: {model}\n\n"
    synthesis_txt += f"{'='*60}\nREASONING\n{'='*60}\n{final.get('reasoning') or '(none)'}\n\n"
    synthesis_txt += f"{'='*60}\nOUTPUT\n{'='*60}\n{final['content']}"

    return {
        "content": final["content"],
        "reasoning": final.get("reasoning"),
        "all_responses": responses,
        "ensemble_candidates_txt": candidates_txt,
        "ensemble_synthesis_txt": synthesis_txt,
    }


# ---------------------------------------------------------------------------
# Backward-compatible aliases (underscore-prefixed names)
# ---------------------------------------------------------------------------

_query_model = query_model
_query_model_streaming = query_model_streaming
_query_model_ensemble = query_model_ensemble
_query_single_model_ensemble = query_single_model_ensemble
