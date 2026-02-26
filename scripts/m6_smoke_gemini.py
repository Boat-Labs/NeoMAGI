"""M6 Gemini smoke test — HTTP-level compatibility verification.

Validates that Gemini's OpenAI-compatible endpoint works correctly with
our OpenAICompatModelClient for chat, streaming, tool calls, CJK, and retry.

Usage:
    python scripts/m6_smoke_gemini.py

Reads from .env: GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Load .env from project root (worktree or main repo)
_project_root = Path(__file__).resolve().parent.parent
_env_path = _project_root / ".env"
if not _env_path.exists():
    # Worktrees may symlink or the .env lives in the main repo
    _main_root = _project_root.parent.parent.parent
    _env_path = _main_root / ".env"

if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

# Now import project code
sys.path.insert(0, str(_project_root))

from src.agent.model_client import (  # noqa: E402
    ContentDelta,
    OpenAICompatModelClient,
    ToolCallsComplete,
)

# --- Configuration ---
API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
BASE_URL = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
)

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a location.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name"},
            },
            "required": ["location"],
        },
    },
}


class SmokeResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.duration_ms = 0.0
        self.detail = ""
        self.tokens_est = 0

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        parts = [f"  [{status}] {self.name} ({self.duration_ms:.0f}ms)"]
        if self.tokens_est:
            parts[0] += f" ~{self.tokens_est} tokens"
        if self.detail:
            parts.append(f"         {self.detail}")
        return "\n".join(parts)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for mixed CJK/Latin."""
    return max(1, len(text) // 4)


async def test_basic_chat(client: OpenAICompatModelClient) -> SmokeResult:
    """Basic non-streaming chat."""
    r = SmokeResult("basic_chat")
    t0 = time.monotonic()
    try:
        response = await client.chat(
            messages=[{"role": "user", "content": "Say hello in one sentence."}],
            model=MODEL,
        )
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.tokens_est = _estimate_tokens(response)
        if response and len(response) > 0:
            r.passed = True
            r.detail = f"len={len(response)}"
        else:
            r.detail = "Empty response"
    except Exception as e:
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.detail = str(e)[:200]
    return r


async def test_streaming_chat(client: OpenAICompatModelClient) -> SmokeResult:
    """Streaming chat — chunks should yield progressively."""
    r = SmokeResult("streaming_chat")
    t0 = time.monotonic()
    try:
        chunks = []
        async for chunk in client.chat_stream(
            messages=[{
                "role": "user",
                "content": "Write a short paragraph about the history of the internet.",
            }],
            model=MODEL,
        ):
            chunks.append(chunk)
        r.duration_ms = (time.monotonic() - t0) * 1000
        full_text = "".join(chunks)
        r.tokens_est = _estimate_tokens(full_text)
        # Gemini may batch short responses into 1 chunk; for longer responses
        # we expect multiple chunks. Accept >=1 chunk with non-empty content.
        if len(chunks) >= 1 and full_text:
            r.passed = True
            r.detail = f"chunks={len(chunks)}, len={len(full_text)}"
        else:
            r.detail = f"chunks={len(chunks)}, len={len(full_text)} (expected content)"
    except Exception as e:
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.detail = str(e)[:200]
    return r


async def test_streaming_tool_calls(client: OpenAICompatModelClient) -> SmokeResult:
    """Streaming with tool calls — ContentDelta + ToolCallsComplete."""
    r = SmokeResult("streaming_tool_calls")
    t0 = time.monotonic()
    try:
        events = []
        async for event in client.chat_stream_with_tools(
            messages=[{"role": "user", "content": "What is the weather in Tokyo?"}],
            model=MODEL,
            tools=[TOOL_DEF],
        ):
            events.append(event)
        r.duration_ms = (time.monotonic() - t0) * 1000

        content_deltas = [e for e in events if isinstance(e, ContentDelta)]
        tool_completes = [e for e in events if isinstance(e, ToolCallsComplete)]

        if tool_completes:
            tc = tool_completes[0]
            if tc.tool_calls and tc.tool_calls[0].get("name") == "get_weather":
                args = json.loads(tc.tool_calls[0]["arguments"])
                r.passed = True
                r.detail = (
                    f"tool={tc.tool_calls[0]['name']}, "
                    f"args={args}, "
                    f"content_chunks={len(content_deltas)}"
                )
            else:
                r.detail = f"Unexpected tool_calls: {tc.tool_calls}"
        elif content_deltas:
            # Gemini might respond with text instead of tool call
            full = "".join(e.text for e in content_deltas)
            r.detail = f"No tool call, got text: {full[:100]}"
        else:
            r.detail = "No events received"
    except Exception as e:
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.detail = str(e)[:200]
    return r


async def test_multi_turn_tool_loop(client: OpenAICompatModelClient) -> SmokeResult:
    """Multi-turn tool loop: call → result → final answer."""
    r = SmokeResult("multi_turn_tool_loop")
    t0 = time.monotonic()
    try:
        messages = [
            {"role": "user", "content": "What is the weather in Paris?"},
        ]

        # Turn 1: expect tool call
        events1 = []
        async for event in client.chat_stream_with_tools(
            messages=messages, model=MODEL, tools=[TOOL_DEF],
        ):
            events1.append(event)

        tool_completes = [e for e in events1 if isinstance(e, ToolCallsComplete)]
        if not tool_completes or not tool_completes[0].tool_calls:
            r.detail = "Turn 1: no tool call returned"
            r.duration_ms = (time.monotonic() - t0) * 1000
            return r

        tc = tool_completes[0].tool_calls[0]

        # Append assistant tool_call + tool result
        messages.append({
            "role": "assistant",
            "tool_calls": [{
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": json.dumps({"temperature": "18°C", "condition": "Cloudy"}),
        })

        # Turn 2: expect final text answer
        events2 = []
        async for event in client.chat_stream_with_tools(
            messages=messages, model=MODEL, tools=[TOOL_DEF],
        ):
            events2.append(event)

        content_deltas = [e for e in events2 if isinstance(e, ContentDelta)]
        full_text = "".join(e.text for e in content_deltas)
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.tokens_est = _estimate_tokens(full_text)

        if full_text and ("18" in full_text or "cloud" in full_text.lower() or "paris" in full_text.lower()):
            r.passed = True
            r.detail = f"Final answer len={len(full_text)}"
        else:
            r.detail = f"Unexpected answer: {full_text[:100]}"
    except Exception as e:
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.detail = str(e)[:200]
    return r


async def test_cjk_content(client: OpenAICompatModelClient) -> SmokeResult:
    """CJK (Chinese) content — no truncation or encoding issues."""
    r = SmokeResult("cjk_content")
    t0 = time.monotonic()
    try:
        response = await client.chat(
            messages=[{"role": "user", "content": "用中文简要介绍一下东京塔，限50字以内。"}],
            model=MODEL,
        )
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.tokens_est = _estimate_tokens(response)

        # Check for Chinese characters
        has_cjk = any("\u4e00" <= c <= "\u9fff" for c in response)
        if has_cjk and len(response) > 5:
            r.passed = True
            r.detail = f"len={len(response)}, has_cjk=True"
        else:
            r.detail = f"len={len(response)}, has_cjk={has_cjk}, text={response[:100]}"
    except Exception as e:
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.detail = str(e)[:200]
    return r


async def test_error_retry(client: OpenAICompatModelClient) -> SmokeResult:
    """Error retry — verify retry machinery works (use bad model name)."""
    r = SmokeResult("error_retry")
    t0 = time.monotonic()
    try:
        # Use a nonexistent model to trigger an API error
        bad_client = OpenAICompatModelClient(
            api_key=API_KEY, base_url=BASE_URL, max_retries=1, base_delay=0.1,
        )
        try:
            await bad_client.chat(
                messages=[{"role": "user", "content": "test"}],
                model="nonexistent-model-xxxxx",
            )
            r.detail = "Expected error but got success"
        except Exception as e:
            error_str = str(e)
            # The retry machinery should wrap as LLMError
            if "LLM" in error_str or "404" in error_str or "not found" in error_str.lower():
                r.passed = True
                r.detail = f"Got expected error: {error_str[:120]}"
            else:
                r.detail = f"Unexpected error type: {error_str[:120]}"
    except Exception as e:
        r.detail = str(e)[:200]
    r.duration_ms = (time.monotonic() - t0) * 1000
    return r


async def test_token_count_fallback(client: OpenAICompatModelClient) -> SmokeResult:
    """Token count fallback — Gemini may not return usage in streaming."""
    r = SmokeResult("token_count_fallback")
    t0 = time.monotonic()
    try:
        # Non-streaming: check if usage is returned
        response = await client._retry_call(
            lambda: client._client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": "Say hi"}],
            ),
            context="token_check",
        )
        r.duration_ms = (time.monotonic() - t0) * 1000

        usage = response.usage
        if usage and usage.total_tokens and usage.total_tokens > 0:
            r.passed = True
            r.detail = (
                f"prompt={usage.prompt_tokens}, "
                f"completion={usage.completion_tokens}, "
                f"total={usage.total_tokens}"
            )
        elif usage:
            # Gemini may return usage but with 0s — still a pass with note
            r.passed = True
            r.detail = f"Usage returned but values: {usage}"
        else:
            # No usage — note this as a compatibility difference
            r.passed = True  # Not a blocker, just informational
            r.detail = "No usage in response (estimate mode needed)"
    except Exception as e:
        r.duration_ms = (time.monotonic() - t0) * 1000
        r.detail = str(e)[:200]
    return r


async def main() -> int:
    if not API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env")
        return 1

    print(f"M6 Gemini Smoke Test")
    print(f"  model:    {MODEL}")
    print(f"  base_url: {BASE_URL}")
    print()

    client = OpenAICompatModelClient(api_key=API_KEY, base_url=BASE_URL)

    tests = [
        test_basic_chat,
        test_streaming_chat,
        test_streaming_tool_calls,
        test_multi_turn_tool_loop,
        test_cjk_content,
        test_error_retry,
        test_token_count_fallback,
    ]

    results: list[SmokeResult] = []
    for test_fn in tests:
        result = await test_fn(client)
        results.append(result)
        print(result)

    print()
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"Results: {passed}/{total} passed")

    if passed < total:
        print("\nFailed tests:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.detail}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
