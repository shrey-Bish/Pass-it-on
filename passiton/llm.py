"""The one place that calls the model. Used by vision.py, parse.py and voice.py, and nothing else.

Works with Claude or any Anthropic-compatible endpoint set in ANTHROPIC_BASE_URL (ASU Voyager ignores structured
output settings, so each prompt asks for JSON and pydantic checks the reply).
"""

import base64
import functools
import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-sonnet-5-5"


class ModelError(Exception):
    """The model's reply couldn't be turned into the expected shape."""


def parse_model(model: type[T], text: str) -> T:
    # Tolerates ```json fences and any words before or after the object.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ModelError("no JSON object in the reply")
    try:
        return model.model_validate_json(text[start : end + 1])
    except ValidationError as e:
        raise ModelError(str(e)) from e


def image_block(data: bytes, media_type: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                       "data": base64.standard_b64encode(data).decode()}}


@functools.cache
def client() -> anthropic.AsyncAnthropic:
    # Voyager can queue requests when it's busy, so allow a long read but only one retry.
    return anthropic.AsyncAnthropic(timeout=120, max_retries=1)


async def ask(model_cls: type[T], system: str, content: list[dict], *, max_tokens: int = 3000,
              llm: anthropic.AsyncAnthropic | None = None) -> T:
    """One question, one JSON answer. A reply that doesn't fit the shape is asked again once, then raises."""
    llm = llm or client()
    error = None
    for _ in range(2):
        response = await llm.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL), max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
        )
        try:
            return parse_model(model_cls, "".join(b.text for b in response.content if b.type == "text"))
        except ModelError as e:
            error = e
    raise error
