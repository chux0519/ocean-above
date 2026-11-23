"""The booking assistant's agent loop (design §4): our own code over a plain OpenAI-compatible
HTTP client. It understands the request, calls the airline's tools and phrases what they
returned; it never computes a route or a price and it has no way to book."""
from .config import LLMConfig, LLMNotConfigured
from .guard import CityIndex, ReplyGuard
from .llm import LLM, Completion, LLMError, OpenAIChatClient, TextDelta
from .loop import MAX_STEPS, Agent, Event
from .sse import encode, encode_stream
from .templates import fallback_reply
from .tools import TOOL_SCHEMAS, Toolbox

__all__ = ["LLM", "MAX_STEPS", "TOOL_SCHEMAS", "Agent", "CityIndex", "Completion", "Event",
           "LLMConfig", "LLMError", "LLMNotConfigured", "OpenAIChatClient", "ReplyGuard",
           "TextDelta", "Toolbox", "encode", "encode_stream", "fallback_reply"]
