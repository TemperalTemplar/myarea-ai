"""
SSE (Server-Sent Events) helpers.

Format spec: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events
"""
import json
from typing import Generator, Any


def sse_event(data: Any, event: str | None = None) -> str:
    """Format a single SSE message."""
    lines = []
    if event:
        lines.append(f"event: {event}")
    if isinstance(data, (dict, list)):
        lines.append(f"data: {json.dumps(data)}")
    else:
        # Text chunk — split on newlines so SSE framing stays valid
        for line in str(data).splitlines():
            lines.append(f"data: {line}")
    lines.append("")   # blank line terminates the event
    lines.append("")
    return "\n".join(lines)


def sse_error(message: str) -> str:
    return sse_event({"error": message}, event="error")


def sse_done(meta: dict | None = None) -> str:
    return sse_event(meta or {}, event="done")


def stream_to_sse(
    text_gen: Generator[str, None, None],
    meta: dict | None = None,
) -> Generator[str, None, None]:
    """
    Wrap a text chunk generator as SSE events.
    Yields `token` events for each chunk, then a final `done` event with metadata.
    """
    for chunk in text_gen:
        yield sse_event(chunk, event="token")
    yield sse_done(meta)
