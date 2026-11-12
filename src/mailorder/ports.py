"""Interfaces to the outside world. The workflow depends on these, not on any service."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from mailorder.models import Customer, Email, OrderRequest, Quote, ThreadState


class Extractor(Protocol):
    def extract(self, email: Email, today: date) -> OrderRequest: ...


class CustomerDirectory(Protocol):
    def match(self, sender: str) -> Customer | None: ...


class Pricing(Protocol):
    def quote(self, request: OrderRequest, customer: Customer, today: date) -> Quote | None:
        """Returns None when the lane is not in the rate card."""
        ...


class StateStore(Protocol):
    def load(self, thread_id: str) -> ThreadState | None: ...

    def save(self, state: ThreadState) -> None: ...


class Outbox(Protocol):
    def send(self, to: str, thread_id: str, body: str) -> None: ...

    def escalate(self, thread_id: str, reason: str) -> None: ...
