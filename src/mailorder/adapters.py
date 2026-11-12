"""In-memory and file-based implementations of the ports, used by the samples and tests."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from mailorder.models import Customer, OrderRequest, Quote, ThreadState


class StaticCustomerDirectory:
    """Matches the sender's e-mail domain. Free-mail and unknown domains match nobody."""

    def __init__(self, customers: list[Customer]):
        self._by_domain = {d.lower(): c for c in customers for d in c.domains}

    def match(self, sender: str) -> Customer | None:
        return self._by_domain.get(sender.rsplit("@", 1)[-1].strip().lower())


class RateCard:
    """Price per kilogram and transit time for each lane, with a minimum charge."""

    def __init__(
        self,
        lanes: dict[tuple[str, str], tuple[Decimal, int]],
        minimum: Decimal,
        currency: str = "EUR",
    ):
        self._lanes = {(a.casefold(), b.casefold()): v for (a, b), v in lanes.items()}
        self._minimum = minimum
        self._currency = currency

    @property
    def cities(self) -> list[str]:
        return sorted({city for lane in self._lanes for city in lane})

    def quote(self, request: OrderRequest, customer: Customer, today: date) -> Quote | None:
        lane = self._lanes.get((request.origin.casefold(), request.destination.casefold()))
        if lane is None:
            return None
        per_kg, days = lane
        price = (
            max(per_kg * request.weight_kg, self._minimum) * (100 - customer.discount_percent) / 100
        )
        return Quote(
            price=price.quantize(Decimal("0.01"), ROUND_HALF_UP),
            currency=self._currency,
            transit_days=days,
            valid_until=today + timedelta(days=7),
        )


class MemoryStateStore:
    def __init__(self):
        self._states: dict[str, str] = {}

    def load(self, thread_id: str) -> ThreadState | None:
        raw = self._states.get(thread_id)
        return ThreadState.model_validate_json(raw) if raw else None

    def save(self, state: ThreadState) -> None:
        self._states[state.thread_id] = state.model_dump_json()


class JsonFileStateStore:
    """One JSON file per thread, so a restart picks up where the last run stopped."""

    def __init__(self, folder: str | Path):
        self._folder = Path(folder)
        self._folder.mkdir(parents=True, exist_ok=True)

    def _path(self, thread_id: str) -> Path:
        return self._folder / f"{thread_id}.json"

    def load(self, thread_id: str) -> ThreadState | None:
        path = self._path(thread_id)
        return (
            ThreadState.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def save(self, state: ThreadState) -> None:
        self._path(state.thread_id).write_text(state.model_dump_json(indent=2), encoding="utf-8")


class RecordingOutbox:
    """Collects what would be sent. A real one would call the mail API."""

    def __init__(self):
        self.sent: list[dict[str, str]] = []
        self.escalations: list[dict[str, str]] = []

    def send(self, to: str, thread_id: str, body: str) -> None:
        self.sent.append({"to": to, "thread_id": thread_id, "body": body})

    def escalate(self, thread_id: str, reason: str) -> None:
        self.escalations.append({"thread_id": thread_id, "reason": reason})


def load_reference_data(path: str | Path) -> tuple[StaticCustomerDirectory, RateCard]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    customers = [Customer.model_validate(c) for c in data["customers"]]
    lanes = {
        (lane["from"], lane["to"]): (Decimal(lane["per_kg"]), lane["days"])
        for lane in data["lanes"]
    }
    return StaticCustomerDirectory(customers), RateCard(
        lanes, Decimal(data["minimum_charge"]), data["currency"]
    )
