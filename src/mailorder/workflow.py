"""The state machine that decides what happens to each incoming e-mail."""

from __future__ import annotations

from datetime import date

from mailorder import replies
from mailorder.models import REQUIRED, Action, ActionKind, Email, ThreadState, ThreadStatus
from mailorder.ports import CustomerDirectory, Extractor, Outbox, Pricing, StateStore

MAX_FOLLOW_UPS = 2


class Workflow:
    """One call to ``handle`` per incoming e-mail.

    new thread ──> not a request ─────────────────────> IGNORED
               └─> unknown sender ────────────────────> ESCALATED
               └─> fields missing ──> WAITING_INFO ──┐
                                        ^   reply    │ still missing after two follow-ups
                                        └────────────┤──────────────────────────> ESCALATED
               └─> complete ──> lane not priced ─────────────────────────────────> ESCALATED
                            └─> QUOTED

    The extractor reads; everything that commits the company to something (who
    the customer is, the price, when to hand over to a person) is decided here in code.
    """

    def __init__(
        self,
        extractor: Extractor,
        customers: CustomerDirectory,
        pricing: Pricing,
        store: StateStore,
        outbox: Outbox,
    ):
        self.extractor = extractor
        self.customers = customers
        self.pricing = pricing
        self.store = store
        self.outbox = outbox

    def handle(self, email: Email, today: date | None = None) -> Action:
        today = today or email.received_at.date()
        state = self.store.load(email.thread_id)
        if state and email.id in state.processed_email_ids:
            return Action(
                kind=ActionKind.NONE, reason="this e-mail was already processed", state=state
            )
        if state and state.status is not ThreadStatus.WAITING_INFO:
            return self._finish(
                state, email, ActionKind.NONE, f"thread is already {state.status.value}"
            )

        extracted = self.extractor.extract(email, today)
        if state is None:
            if not extracted.is_order_request:
                state = ThreadState(thread_id=email.thread_id, status=ThreadStatus.IGNORED)
                return self._finish(state, email, ActionKind.IGNORE, "not a shipment request")
            customer = self.customers.match(email.sender)
            state = ThreadState(
                thread_id=email.thread_id, status=ThreadStatus.WAITING_INFO, request=extracted
            )
            if customer is None:
                return self._escalate(state, email, "sender does not belong to a known customer")
            state.customer_id = customer.id
        else:
            customer = self.customers.match(email.sender)
            if customer is None or customer.id != state.customer_id:
                return self._escalate(
                    state, email, "reply came from a different sender than the request"
                )
            state.request = state.request.merge(extracted)

        lang = replies.language_of(email.body)
        missing = [name for name in REQUIRED if getattr(state.request, name) is None]
        if missing:
            if state.follow_ups >= MAX_FOLLOW_UPS:
                return self._escalate(
                    state,
                    email,
                    f"still missing after {MAX_FOLLOW_UPS} follow-ups: {', '.join(missing)}",
                )
            state.follow_ups += 1
            reply = replies.ask_missing(lang, missing)
            self.outbox.send(email.sender, email.thread_id, reply)
            return self._finish(
                state, email, ActionKind.ASK_MISSING, "required fields are missing", missing, reply
            )
        if state.request.pickup_date < today:
            return self._escalate(state, email, "pickup date is in the past")
        if state.request.weight_kg <= 0:
            return self._escalate(state, email, "weight is not positive")

        quote = self.pricing.quote(state.request, customer, today)
        if quote is None:
            return self._escalate(
                state, email, f"no rate for {state.request.origin} to {state.request.destination}"
            )
        state.quote = quote
        state.status = ThreadStatus.QUOTED
        reply = replies.quote(lang, state.request, quote)
        self.outbox.send(email.sender, email.thread_id, reply)
        return self._finish(
            state,
            email,
            ActionKind.SEND_QUOTE,
            "request is complete and the lane is priced",
            reply=reply,
        )

    def _escalate(self, state: ThreadState, email: Email, reason: str) -> Action:
        state.status = ThreadStatus.ESCALATED
        self.outbox.escalate(email.thread_id, reason)
        return self._finish(state, email, ActionKind.ESCALATE, reason)

    def _finish(
        self,
        state: ThreadState,
        email: Email,
        kind: ActionKind,
        reason: str,
        missing: list[str] | None = None,
        reply: str | None = None,
    ) -> Action:
        state.processed_email_ids.append(email.id)
        self.store.save(state)
        return Action(kind=kind, reason=reason, missing=missing or [], reply=reply, state=state)
