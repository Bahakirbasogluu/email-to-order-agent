import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mailorder.evaluate import evaluate
from mailorder.extract import LLMExtractor, RuleExtractor
from mailorder.models import Email, OrderRequest

SAMPLES = Path(__file__).parent.parent / "samples"
CITIES = json.loads((SAMPLES / "reference.json").read_text(encoding="utf-8"))["cities"]
TODAY = date(2026, 3, 2)


def read(body: str, subject: str = "") -> OrderRequest:
    email = Email(
        id="1",
        thread_id="t",
        sender="a@b.example",
        subject=subject,
        body=body,
        received_at=datetime(2026, 3, 2),
    )
    return RuleExtractor(CITIES).extract(email, TODAY)


@pytest.mark.parametrize(
    ("body", "origin", "destination"),
    [
        ("İstanbul'dan Münih'e teklif rica ederiz", "Istanbul", "Munich"),
        ("ISTANBUL'DAN MILANO'YA sevkiyat", "Istanbul", "Milan"),
        ("quote from Izmir to Rotterdam please", "Izmir", "Rotterdam"),
        ("we ship to Rotterdam from Istanbul", "Istanbul", "Rotterdam"),
        ("Çıkış: İzmir\nVarış: Münih", "Izmir", "Munich"),
        ("Bursa - Milan shipment", "Bursa", "Milan"),
    ],
)
def test_lane(body, origin, destination):
    request = read(body)
    assert (request.origin, request.destination) == (origin, destination)


@pytest.mark.parametrize(
    ("body", "kg"),
    [
        ("4.200 kg", "4200"),
        ("6,500 kg", "6500"),
        ("2,5 ton", "2500"),
        ("950 kg in total", "950"),
        ("1.250,5 kg", "1250.5"),
    ],
)
def test_weight(body, kg):
    assert read(f"shipment of {body}").weight_kg == Decimal(kg)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("pickup 2026-03-18", date(2026, 3, 18)),
        ("yükleme 20.03.2026", date(2026, 3, 20)),
        ("800 kg on 30 March 2026", date(2026, 3, 30)),
        ("2,5 ton, yükleme 16 Mart.", date(2026, 3, 16)),
        ("pickup on March 9", date(2026, 3, 9)),
        ("yükleme 10 Ocak", date(2027, 1, 10)),
    ],
)
def test_date(body, expected):
    assert read(body).pickup_date == expected


def test_what_the_baseline_does_not_read():
    request = read("Yarın Ankara depomuzdan 600 kilo ürün gidecek, fiyat alabilir miyiz?")
    assert request.is_order_request
    assert (request.origin, request.pickup_date, request.weight_kg) == (None, None, None)


def test_other_mail_is_not_a_request():
    assert not read("Geçen ayın faturasını tekrar gönderebilir misiniz?").is_order_request


def test_llm_extractor_gives_the_model_today_and_the_city_names():
    class Model:
        def parse(self, system, user, schema):
            self.system, self.user, self.schema = system, user, schema
            return OrderRequest(origin="Istanbul")

    model = Model()
    email = Email(
        id="1",
        thread_id="t",
        sender="a@b.example",
        subject="Quote",
        body="pickup next Monday",
        received_at=datetime(2026, 3, 2),
    )
    assert LLMExtractor(model, list(CITIES)).extract(email, TODAY).origin == "Istanbul"
    assert "Monday, 2026-03-02" in model.system and "Rotterdam" in model.system
    assert model.schema is OrderRequest and "pickup next Monday" in model.user


def test_bundled_threads_with_the_rule_baseline():
    result = evaluate(SAMPLES / "threads.json", SAMPLES / "reference.json")
    assert result["threads"] == 16
    assert result["threads_handled_as_expected"] == 14
    assert result["quotes_sent_with_a_wrong_field_or_unexpectedly"] == 0
    assert {m["thread"] for m in result["mismatches"]} == {"t11", "t16"}
