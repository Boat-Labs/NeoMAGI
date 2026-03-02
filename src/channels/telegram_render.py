"""Telegram response rendering: message splitting, formatting, error mapping."""

from __future__ import annotations

import re

# ── Error code → user-friendly message ──────────────────────────────────────

_ERROR_MESSAGES: dict[str, str] = {
    "SESSION_BUSY": "当前正在处理中，请稍后重试",
    "BUDGET_EXCEEDED": "预算额度已用完",
    "PROVIDER_NOT_AVAILABLE": "模型服务暂不可用",
}

_DEFAULT_ERROR = "处理消息时遇到了问题，请稍后重试"


def friendly_error_message(code: str | None) -> str:
    """Map GatewayError code to user-friendly Chinese message."""
    if code and code in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[code]
    return _DEFAULT_ERROR


# ── Message splitting ────────────────────────────────────────────────────────

_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n[\s\S]*?```")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])\s+")


def split_message(text: str, max_length: int = 4096) -> list[str]:
    """Split text into chunks within Telegram's message length limit.

    Split priority: code-block boundaries → paragraphs → sentences → hard cut.
    Code blocks (```) are kept intact when possible.
    """
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    segments = _split_preserving_code_blocks(text)

    result: list[str] = []
    buf = ""

    for seg in segments:
        if buf and len(buf) + len(seg) <= max_length:
            buf += seg
            continue

        if buf:
            result.append(buf)
            buf = ""

        if len(seg) <= max_length:
            buf = seg
            continue

        # Segment too long — split further
        if seg.startswith("```"):
            sub = _split_code_block(seg, max_length)
        else:
            sub = _split_paragraphs(seg, max_length)

        for s in sub[:-1]:
            result.append(s)
        buf = sub[-1] if sub else ""

    if buf:
        result.append(buf)

    return [c for c in result if c.strip()]


def _split_preserving_code_blocks(text: str) -> list[str]:
    """Split text into alternating regular-text / code-block segments."""
    parts: list[str] = []
    last = 0
    for m in _CODE_BLOCK_RE.finditer(text):
        if m.start() > last:
            parts.append(text[last : m.start()])
        parts.append(m.group())
        last = m.end()
    if last < len(text):
        parts.append(text[last:])
    return parts


def _split_code_block(block: str, max_length: int) -> list[str]:
    """Split an oversized code block by lines, re-wrapping with ``` fences."""
    try:
        first_nl = block.index("\n")
    except ValueError:
        return [block[:max_length]]

    header = block[:first_nl]  # e.g. ```python
    body = block[first_nl + 1 :]
    if body.endswith("```"):
        body = body[:-3]
    body = body.rstrip("\n")
    footer = "```"

    if not body:
        return [block[:max_length]]

    body_lines = body.split("\n")
    overhead = len(header) + len(footer) + 2  # newlines after header / before footer
    effective = max(max_length - overhead, 20)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in body_lines:
        line_len = len(line) + 1  # +1 for \n
        if current_len + line_len > effective and current:
            chunks.append(header + "\n" + "\n".join(current) + "\n" + footer)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append(header + "\n" + "\n".join(current) + "\n" + footer)

    return chunks or [block[:max_length]]


def _split_paragraphs(text: str, max_length: int) -> list[str]:
    """Split on paragraph boundaries (\\n\\n), falling back to sentences."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = current + "\n\n" + para if current else para
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(para) <= max_length:
            current = para
        else:
            sub = _split_sentences(para, max_length)
            for s in sub[:-1]:
                chunks.append(s)
            current = sub[-1] if sub else ""

    if current:
        chunks.append(current)
    return chunks


def _split_sentences(text: str, max_length: int) -> list[str]:
    """Split on sentence boundaries, falling back to hard cut."""
    parts = _SENTENCE_BOUNDARY_RE.split(text)
    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = current + " " + part if current else part
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(part) <= max_length:
            current = part
        else:
            for i in range(0, len(part), max_length):
                chunks.append(part[i : i + max_length])
            current = ""

    if current:
        chunks.append(current)
    return chunks


# ── MarkdownV2 formatting ───────────────────────────────────────────────────

_HAS_MARKDOWN_RE = re.compile(r"```|`[^`]+`|\*\*")
_MD2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_CODE_OR_INLINE_RE = re.compile(r"```[^\n]*\n[\s\S]*?```|`[^`\n]+`")


def format_for_telegram(text: str) -> tuple[str, str | None]:
    """Format LLM response for Telegram. Returns (text, parse_mode).

    Attempts MarkdownV2 when markdown patterns are detected.
    Falls back to plain text (parse_mode=None) on failure or absence of markdown.
    """
    if not text:
        return ("", None)

    if not _HAS_MARKDOWN_RE.search(text):
        return (text, None)

    try:
        formatted = _to_markdownv2(text)
        return (formatted, "MarkdownV2")
    except Exception:
        return (text, None)


def _to_markdownv2(text: str) -> str:
    """Convert standard Markdown to Telegram MarkdownV2.

    Preserves code blocks and inline code. Converts **bold** → *bold*.
    Escapes all other MarkdownV2 special characters.
    """
    tokens: list[str] = []
    last = 0

    for m in _CODE_OR_INLINE_RE.finditer(text):
        before = text[last : m.start()]
        tokens.append(_format_plain(before))

        code = m.group()
        if code.startswith("```"):
            tokens.append(_escape_code_block_content(code))
        else:
            inner = code[1:-1].replace("\\", "\\\\").replace("`", "\\`")
            tokens.append(f"`{inner}`")

        last = m.end()

    tokens.append(_format_plain(text[last:]))
    return "".join(tokens)


def _format_plain(text: str) -> str:
    """Format a non-code text segment for MarkdownV2.

    Converts **bold** → *bold* and escapes remaining special characters.
    """
    if not text:
        return ""

    protected: dict[str, str] = {}
    counter = [0]

    def _protect(val: str) -> str:
        key = f"\x00P{counter[0]}\x00"
        protected[key] = val
        counter[0] += 1
        return key

    def _bold(m: re.Match[str]) -> str:
        inner = _MD2_ESCAPE_RE.sub(r"\\\1", m.group(1))
        return _protect(f"*{inner}*")

    result = re.sub(r"\*\*(.+?)\*\*", _bold, text)
    result = _MD2_ESCAPE_RE.sub(r"\\\1", result)

    for key, val in protected.items():
        result = result.replace(key, val)

    return result


def _escape_code_block_content(block: str) -> str:
    """Escape fenced code block content for MarkdownV2 (only \\\\ and `)."""
    try:
        nl = block.index("\n")
    except ValueError:
        return block

    header = block[:nl]
    content = block[nl + 1 : -3]  # between first \\n and closing ```
    content = content.replace("\\", "\\\\").replace("`", "\\`")
    return f"{header}\n{content}```"
