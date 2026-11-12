from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class Email(BaseModel):
    id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime


class OrderRequest(BaseModel):
    """What a shipment request must state before it can be priced.

    Every field is optional because requests arrive incomplete; the workflow
    asks for the missing ones instead of guessing them.
    """

    is_order_request: bool = Field(
        True, description="False for anything that is not a request to ship goods"
    )
    origin: str | None = Field(None, description="City the goods are picked up from")
    destination: str | None = Field(None, description="City the goods are delivered to")
    pickup_date: date | None = None
    weight_kg: Decimal | None = Field(None, description="Total gross weight in kilograms")
    pallets: int | None = None
    cargo: str | None = Field(None, description="Short description of the goods")

    def merge(self, newer: OrderRequest) -> OrderRequest:
        """Fields stated in a later message replace earlier ones; unstated fields are kept."""
        update = {
            k: v for k, v in newer.model_dump(exclude={"is_order_request"}).items() if v is not None
        }
        return self.model_copy(update=update)


REQUIRED = ("origin", "destination", "pickup_date", "weight_kg")


class Customer(BaseModel):
    id: str
    name: str
    domains: list[str]
    discount_percent: Decimal = Decimal(0)


class Quote(BaseModel):
    price: Decimal
    currency: str
    transit_days: int
    valid_until: date


class ThreadStatus(str, Enum):
    WAITING_INFO = "waiting_info"
    QUOTED = "quoted"
    ESCALATED = "escalated"
    IGNORED = "ignored"


class ThreadState(BaseModel):
    thread_id: str
    status: ThreadStatus
    customer_id: str | None = None
    request: OrderRequest = Field(default_factory=OrderRequest)
    follow_ups: int = 0
    processed_email_ids: list[str] = Field(default_factory=list)
    quote: Quote | None = None


class ActionKind(str, Enum):
    ASK_MISSING = "ask_missing"
    SEND_QUOTE = "send_quote"
    ESCALATE = "escalate"
    IGNORE = "ignore"
    NONE = "none"


class Action(BaseModel):
    kind: ActionKind
    reason: str
    missing: list[str] = Field(default_factory=list)
    reply: str | None = None
    state: ThreadState | None = None
