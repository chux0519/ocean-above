"""The booking assistant's agent loop (design §4). Pydantic AI (ADR-001) runs the model stream and
the tool dispatch; the loop around it is ours. It understands the request, calls the airline's
tools and phrases what they returned; it never computes a route or a price and it has no way to
book."""
from .concurrency import ServerBusy, TurnConcurrency
from .config import LLMConfig, LLMNotConfigured
from .guard import CityIndex, ReplyGuard
from .loop import MAX_STEPS, Agent, Event
from .model import ChatModel, Completion, LLMError
from .scripted import ScriptedLLM, TextDelta
from .sse import encode, encode_stream
from .templates import fallback_reply
from .tools import TOOL_SCHEMAS, Toolbox

__all__ = ["MAX_STEPS", "TOOL_SCHEMAS", "Agent", "ChatModel", "CityIndex", "Completion", "Event",
           "LLMConfig", "LLMError", "LLMNotConfigured", "ReplyGuard", "ScriptedLLM",
           "ServerBusy", "TextDelta", "Toolbox", "TurnConcurrency", "encode", "encode_stream", "fallback_reply"]
