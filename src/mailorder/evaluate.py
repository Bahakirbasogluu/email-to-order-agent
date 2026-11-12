"""Replays labelled e-mail threads through the workflow and scores the outcome."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mailorder.adapters import MemoryStateStore, RecordingOutbox, load_reference_data
from mailorder.extract import RuleExtractor
from mailorder.models import ActionKind, Email
from mailorder.ports import Extractor
from mailorder.workflow import Workflow

FIELDS = ("origin", "destination", "pickup_date", "weight_kg")


def build(
    reference: str | Path, extractor: Extractor | None = None
) -> tuple[Workflow, RecordingOutbox]:
    customers, rates = load_reference_data(reference)
    cities = json.loads(Path(reference).read_text(encoding="utf-8"))["cities"]
    outbox = RecordingOutbox()
    return Workflow(
        extractor or RuleExtractor(cities), customers, rates, MemoryStateStore(), outbox
    ), outbox


def replay(workflow: Workflow, thread: dict[str, Any], today: date) -> list:
    start = datetime.combine(today, datetime.min.time()) + timedelta(hours=9)
    actions = []
    for i, mail in enumerate(thread["emails"]):
        email = Email(id=f"{thread['thread_id']}-{i + 1}", thread_id=thread["thread_id"], sender=thread["sender"],
                      subject=mail["subject"], body=mail["body"], received_at=start + timedelta(minutes=i))  # fmt: skip
        actions.append(workflow.handle(email, today))
    return actions


def _same(field: str, got: Any, want: str) -> bool:
    if got is None:
        return False
    return Decimal(str(got)) == Decimal(want) if field == "weight_kg" else str(got) == want


def evaluate(
    threads_file: str | Path, reference: str | Path, extractor: Extractor | None = None
) -> dict[str, Any]:
    data = json.loads(Path(threads_file).read_text(encoding="utf-8"))
    today = date.fromisoformat(data["today"])
    workflow, _ = build(reference, extractor)
    rows, correct, wrong_quotes, quotes_sent, field_hits, field_total = [], 0, 0, 0, 0, 0
    for thread in data["threads"]:
        actions = replay(workflow, thread, today)
        got = [a.kind.value for a in actions]
        want = thread["expected"]
        final = actions[-1].state
        ok = got == want["actions"] and final.status.value == want["status"]
        if want["request"]:
            hits = [_same(f, getattr(final.request, f), want["request"][f]) for f in FIELDS]
            field_hits += sum(hits)
            field_total += len(hits)
            ok = ok and all(hits)
        if ActionKind.SEND_QUOTE.value in got:
            quotes_sent += 1
            right = want["request"] and all(
                _same(f, getattr(final.request, f), want["request"][f]) for f in FIELDS
            )
            wrong_quotes += not right
        correct += ok
        rows.append(
            {
                "thread": thread["thread_id"],
                "ok": ok,
                "expected": want["actions"],
                "got": got,
                "note": thread["note"],
            }
        )
    return {
        "threads": len(rows),
        "threads_handled_as_expected": correct,
        "quotes_sent": quotes_sent,
        "quotes_sent_with_a_wrong_field_or_unexpectedly": wrong_quotes,
        "request_fields_correct": f"{field_hits} / {field_total}",
        "mismatches": [r for r in rows if not r["ok"]],
    }
