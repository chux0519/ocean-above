"""Airline-side capabilities behind one interface (design §2). The agent and the HTTP layer depend
only on this package; it could later be served as an MCP server or backed by a real booking
system. Schedules, seats and inventory are intentionally absent: locations.db has no such data."""
from .locks import ConversationBusy, ConversationLocks
from .service import QUOTE_TTL, Airline, BookingError, NotFound, Result

__all__ = ["QUOTE_TTL", "Airline", "BookingError", "ConversationBusy", "ConversationLocks",
           "NotFound", "Result"]
