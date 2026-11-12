from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mailorder import ActionKind, Email, OrderRequest, ThreadStatus, Workflow
from mailorder.adapters import (
    JsonFileStateStore,
    MemoryStateStore,
    RecordingOutbox,
    load_reference_data,
)

REFERENCE = Path(__file__).parent.parent / "samples" / "reference.json"
TODAY = date(2026, 3, 2)
KNOWN = "ops@northwind.example"
COMPLETE = OrderRequest(
    origin="Istanbul", destination="Munich", pickup_date=date(2026, 3, 12), weight_kg=Decimal(1000)
)


class Scripted:
    """An extractor that returns prepared answers in order."""

    def __init__(self, *answers: OrderRequest):
        self.answers = list(answers)
        self.calls = 0

    def extract(self, email, today):
        self.calls += 1
        return self.answers.pop(0)


def mail(n: int, sender: str = KNOWN, thread: str = "t1", body: str = "Please quote.") -> Email:
    return Email(
        id=f"m{n}",
        thread_id=thread,
        sender=sender,
        subject="Quote",
        body=body,
        received_at=datetime(2026, 3, 2, 9, n),
    )


@pytest.fixture
def make():
    def _make(*answers, store=None):
        customers, rates = load_reference_data(REFERENCE)
        outbox = RecordingOutbox()
        extractor = Scripted(*answers)
        return (
            Workflow(extractor, customers, rates, store or MemoryStateStore(), outbox),
            outbox,
            extractor,
        )

    return _make


def test_complete_request_is_quoted_with_the_customer_discount(make):
    workflow, outbox, _ = make(COMPLETE)
    action = workflow.handle(mail(1), TODAY)
    assert action.kind is ActionKind.SEND_QUOTE
    assert action.state.quote.price == Decimal("386.40")  # 1000 kg x 0.42, minus 8%
    assert action.state.quote.valid_until == date(2026, 3, 9)
    assert "386.40 EUR" in outbox.sent[0]["body"]


def test_minimum_charge_applies_to_small_shipments(make):
    workflow, _, _ = make(COMPLETE.model_copy(update={"weight_kg": Decimal(100)}))
    assert workflow.handle(mail(1), TODAY).state.quote.price == Decimal(
        "322.00"
    )  # 350 minimum, minus 8%


def test_missing_fields_are_asked_for_and_the_reply_completes_the_request(make):
    partial = COMPLETE.model_copy(update={"pickup_date": None, "weight_kg": None})
    workflow, outbox, _ = make(
        partial, OrderRequest(pickup_date=date(2026, 3, 12), weight_kg=Decimal(1000))
    )
    first = workflow.handle(mail(1), TODAY)
    assert first.kind is ActionKind.ASK_MISSING
    assert first.missing == ["pickup_date", "weight_kg"]
    assert "pickup date" in outbox.sent[0]["body"]
    second = workflow.handle(mail(2), TODAY)
    assert second.kind is ActionKind.SEND_QUOTE
    assert second.state.request.origin == "Istanbul"


def test_a_later_message_overrides_an_earlier_value(make):
    workflow, _, _ = make(
        COMPLETE.model_copy(update={"pickup_date": None}),
        OrderRequest(pickup_date=date(2026, 3, 12), weight_kg=Decimal(500)),
    )
    workflow.handle(mail(1), TODAY)
    assert workflow.handle(mail(2), TODAY).state.request.weight_kg == Decimal(500)


def test_two_follow_ups_then_a_person_takes_over(make):
    nothing = OrderRequest()
    workflow, outbox, _ = make(COMPLETE.model_copy(update={"weight_kg": None}), nothing, nothing)
    kinds = [workflow.handle(mail(n), TODAY).kind for n in (1, 2, 3)]
    assert kinds == [ActionKind.ASK_MISSING, ActionKind.ASK_MISSING, ActionKind.ESCALATE]
    assert len(outbox.sent) == 2
    assert "weight_kg" in outbox.escalations[0]["reason"]


@pytest.mark.parametrize(
    ("request_", "sender", "reason"),
    [
        (COMPLETE, "someone@freemail.example", "known customer"),
        (COMPLETE.model_copy(update={"destination": "Lyon"}), KNOWN, "no rate"),
        (COMPLETE.model_copy(update={"pickup_date": date(2026, 2, 20)}), KNOWN, "past"),
        (COMPLETE.model_copy(update={"weight_kg": Decimal(0)}), KNOWN, "not positive"),
    ],
)
def test_cases_that_go_to_a_person_without_a_reply(make, request_, sender, reason):
    workflow, outbox, _ = make(request_)
    action = workflow.handle(mail(1, sender=sender), TODAY)
    assert action.kind is ActionKind.ESCALATE and reason in action.reason
    assert outbox.sent == []


def test_non_requests_are_ignored_and_stay_ignored(make):
    workflow, outbox, extractor = make(OrderRequest(is_order_request=False))
    assert workflow.handle(mail(1), TODAY).kind is ActionKind.IGNORE
    assert workflow.handle(mail(2), TODAY).kind is ActionKind.NONE
    assert extractor.calls == 1 and outbox.sent == []


def test_the_same_email_delivered_twice_sends_one_reply(make):
    workflow, outbox, extractor = make(COMPLETE)
    workflow.handle(mail(1), TODAY)
    again = workflow.handle(mail(1), TODAY)
    assert again.kind is ActionKind.NONE
    assert len(outbox.sent) == 1 and extractor.calls == 1


def test_a_reply_from_another_company_does_not_complete_the_request(make):
    workflow, outbox, _ = make(
        COMPLETE.model_copy(update={"weight_kg": None}), OrderRequest(weight_kg=Decimal(10))
    )
    workflow.handle(mail(1), TODAY)
    action = workflow.handle(mail(2, sender="buyer@kuzeyambalaj.example"), TODAY)
    assert action.kind is ActionKind.ESCALATE
    assert len(outbox.sent) == 1


def test_state_survives_a_restart(make, tmp_path):
    partial = COMPLETE.model_copy(update={"weight_kg": None})
    first, _, _ = make(partial, store=JsonFileStateStore(tmp_path))
    first.handle(mail(1), TODAY)
    second, _, _ = make(OrderRequest(weight_kg=Decimal(1000)), store=JsonFileStateStore(tmp_path))
    action = second.handle(mail(2), TODAY)
    assert action.kind is ActionKind.SEND_QUOTE
    assert action.state.status is ThreadStatus.QUOTED and action.state.follow_ups == 1


def test_reply_language_follows_the_email(make):
    workflow, outbox, _ = make(COMPLETE.model_copy(update={"weight_kg": None}))
    workflow.handle(mail(1, body="Merhaba, teklif rica ederiz."), TODAY)
    assert "toplam ağırlık" in outbox.sent[0]["body"]
