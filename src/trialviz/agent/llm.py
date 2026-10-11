"""Thin LLM abstraction: "give me an instance of this Pydantic model".

Nodes depend on the ``StructuredLLM`` protocol, not on OpenAI, so tests can inject a
fake and the provider can be swapped without touching the graph.
"""

from collections import Counter
from typing import Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from trialviz.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class StructuredLLM(Protocol):
    async def parse(self, *, model: str, system: str, user: str, schema: type[T]) -> T: ...


class OpenAIStructuredLLM:
    """OpenAI Responses API with strict structured outputs (schema-constrained decoding)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=settings.planner_timeout_s,
        )
        # Running token totals across calls, for cost reporting.
        self.usage: Counter[str] = Counter()

    async def parse(self, *, model: str, system: str, user: str, schema: type[T]) -> T:
        resp = await self._client.responses.parse(
            model=model, instructions=system, input=user, text_format=schema
        )
        if resp.usage:
            self.usage.update(
                calls=1,
                input_tokens=resp.usage.input_tokens,
                cached_tokens=resp.usage.input_tokens_details.cached_tokens,
                output_tokens=resp.usage.output_tokens,
                reasoning_tokens=resp.usage.output_tokens_details.reasoning_tokens,
            )
        if resp.output_parsed is None:
            raise LLMError(f"model returned no parseable {schema.__name__} (refusal or empty)")
        return resp.output_parsed
