"""Two ways to read an e-mail: patterns (offline baseline) and a structured-output model."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, TypeVar

from pydantic import BaseModel

from mailorder.models import Email, OrderRequest

T = TypeVar("T", bound=BaseModel)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
    "eylül": 9, "ekim": 10, "kasim": 11, "aralik": 12,
}  # fmt: skip
_REQUEST_WORDS = (
    "teklif",
    "sevkiyat",
    "nakliye",
    "yükleme",
    "quote",
    "shipment",
    "pickup",
    "transport",
    "fiyat",
)


def fold(text: str) -> str:
    """Lower-case with İ, I and ı mapped to i, so Turkish capitals compare equal."""
    return text.replace("İ", "i").replace("ı", "i").lower()


def _number(raw: str) -> Decimal | None:
    s = raw.strip()
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _date(text: str, today: date) -> date | None:
    for pattern, order in (
        (r"(\d{4})-(\d{2})-(\d{2})", "ymd"),
        (r"(\d{1,2})[./](\d{1,2})[./](\d{4})", "dmy"),
    ):
        if m := re.search(pattern, text):
            y, mo, d = (
                (m.group(1), m.group(2), m.group(3))
                if order == "ymd"
                else (m.group(3), m.group(2), m.group(1))
            )
            try:
                return date(int(y), int(mo), int(d))
            except ValueError:
                return None
    day_first = r"(?<!\d)(?P<day>\d{1,2})\s+(?P<month>[^\W\d]+)(?:\s+(?P<year>\d{4}))?"
    month_first = r"(?P<month>[^\W\d]+)\s+(?P<day>\d{1,2})(?!\d)(?:,?\s+(?P<year>\d{4}))?"
    for pattern in (day_first, month_first):
        for m in re.finditer(pattern, text):
            if m["month"] not in _MONTHS:
                continue
            try:
                found = date(
                    int(m["year"]) if m["year"] else today.year, _MONTHS[m["month"]], int(m["day"])
                )
            except ValueError:
                continue
            return found if m["year"] or found >= today else found.replace(year=today.year + 1)
    return None


class RuleExtractor:
    """Finds what is stated in a common form and leaves the rest empty.

    It does not understand relative dates ("next Monday"), cities mentioned
    without a direction word, or anything phrased unusually. That is the point
    of the baseline: it shows what the workflow does when extraction is partial.
    """

    def __init__(self, cities: dict[str, list[str]]):
        self._aliases = {
            fold(alias): name for name, aliases in cities.items() for alias in [name, *aliases]
        }

    def extract(self, email: Email, today: date) -> OrderRequest:
        text = fold(f"{email.subject}\n{email.body}")
        origin, destination = self._lane(text)
        weight = None
        if m := re.search(r"(\d[\d.,]*)\s*(kg|kilogram|ton|tons|tonnes)\b", text):
            weight = _number(m.group(1))
            if weight is not None and m.group(2).startswith("ton"):
                weight *= 1000
        pallets = re.search(r"(\d+)\s*(?:palet|pallet)", text)
        return OrderRequest(
            is_order_request=any(word in text for word in _REQUEST_WORDS),
            origin=origin,
            destination=destination,
            pickup_date=_date(text, today),
            weight_kg=weight,
            pallets=int(pallets.group(1)) if pallets else None,
        )

    def _lane(self, text: str) -> tuple[str | None, str | None]:
        city = "|".join(sorted((re.escape(a) for a in self._aliases), key=len, reverse=True))
        origin = destination = None
        patterns = [
            (rf"\b({city})['’]?(?:dan|den|tan|ten)\b", "o"),
            (rf"\b({city})['’]?(?:ya|ye|a|e)\b", "d"),
            (rf"\bfrom\s+({city})\b", "o"),
            (rf"\bto\s+({city})\b", "d"),
            (rf"(?:çikiş|yükleme yeri|origin|pickup city)\s*:\s*({city})\b", "o"),
            (rf"(?:variş|teslimat yeri|destination|delivery city)\s*:\s*({city})\b", "d"),
        ]
        for pattern, role in patterns:
            if m := re.search(pattern, text):
                if role == "o" and origin is None:
                    origin = self._aliases[m.group(1)]
                elif role == "d" and destination is None:
                    destination = self._aliases[m.group(1)]
        pair = re.search(rf"\b({city})\s*(?:-|–|→|->)\s*({city})\b", text)
        if origin is None and destination is None and pair:
            origin, destination = self._aliases[pair.group(1)], self._aliases[pair.group(2)]
        return origin, destination


class StructuredModel(Protocol):
    def parse(self, system: str, user: str, schema: type[T]) -> T: ...


SYSTEM = (
    "You read e-mails sent to a freight forwarder and fill the given schema.\n"
    "Set is_order_request to false for anything that is not a request to ship goods "
    "(invoices, complaints, newsletters, automatic replies).\n"
    "Report only what the e-mail states. Leave a field null when it is not stated; never guess.\n"
    "Resolve relative dates against today's date: {today:%A, %Y-%m-%d}.\n"
    "Convert weights to kilograms. Use these city names when the e-mail refers to one of them: {cities}."
)


class LLMExtractor:
    def __init__(self, model: StructuredModel, cities: list[str]):
        self._model = model
        self._cities = ", ".join(cities)

    def extract(self, email: Email, today: date) -> OrderRequest:
        system = SYSTEM.format(today=today, cities=self._cities)
        return self._model.parse(system, f"Subject: {email.subject}\n\n{email.body}", OrderRequest)


class AzureOpenAIModel:
    """Structured outputs on an Azure OpenAI chat deployment."""

    def __init__(self, deployment: str | None = None):
        from openai import AzureOpenAI

        self._client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        self._deployment = deployment or os.environ["AZURE_OPENAI_DEPLOYMENT"]

    def parse(self, system: str, user: str, schema: type[T]) -> T:
        completion = self._client.beta.chat.completions.parse(
            model=self._deployment,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=schema,
        )
        message = completion.choices[0].message
        if message.parsed is None:
            raise ValueError(f"model returned no structured output: {message.refusal}")
        return message.parsed


def parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw)
