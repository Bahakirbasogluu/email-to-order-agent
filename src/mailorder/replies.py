"""Reply drafts. Templates, not model output: a quote must say exactly what was priced."""

from __future__ import annotations

from mailorder.models import OrderRequest, Quote

LABELS = {
    "tr": {
        "origin": "yükleme şehri",
        "destination": "teslimat şehri",
        "pickup_date": "yükleme tarihi",
        "weight_kg": "toplam ağırlık (kg)",
    },
    "en": {
        "origin": "pickup city",
        "destination": "delivery city",
        "pickup_date": "pickup date",
        "weight_kg": "total weight (kg)",
    },
}
_TURKISH_HINTS = (
    "merhaba",
    "teklif",
    "rica",
    "yükleme",
    "sevkiyat",
    "teşekkür",
    "palet",
    "iyi çalışmalar",
    "ğ",
    "ş",
    "ı",
)


def language_of(text: str) -> str:
    low = text.lower()
    return "tr" if any(hint in low for hint in _TURKISH_HINTS) else "en"


def ask_missing(lang: str, missing: list[str]) -> str:
    items = "\n".join(f"- {LABELS[lang][name]}" for name in missing)
    if lang == "tr":
        return f"Merhaba,\n\nTalebiniz için teşekkür ederiz. Teklif hazırlayabilmemiz için şu bilgilere ihtiyacımız var:\n{items}\n\nİyi çalışmalar"
    return f"Hello,\n\nThank you for your request. To prepare a quote we need the following:\n{items}\n\nKind regards"


def quote(lang: str, request: OrderRequest, q: Quote) -> str:
    if lang == "tr":
        return (
            f"Merhaba,\n\n{request.origin} - {request.destination} sevkiyatınız için teklifimiz:\n"
            f"- Yükleme tarihi: {request.pickup_date:%d.%m.%Y}\n- Ağırlık: {request.weight_kg} kg\n"
            f"- Fiyat: {q.price} {q.currency}\n- Transit süre: {q.transit_days} gün\n"
            f"- Geçerlilik: {q.valid_until:%d.%m.%Y}\n\nOnayınızla siparişi açabiliriz.\n\nİyi çalışmalar"
        )
    return (
        f"Hello,\n\nOur quote for your {request.origin} to {request.destination} shipment:\n"
        f"- Pickup date: {request.pickup_date:%d %B %Y}\n- Weight: {request.weight_kg} kg\n"
        f"- Price: {q.price} {q.currency}\n- Transit time: {q.transit_days} days\n"
        f"- Valid until: {q.valid_until:%d %B %Y}\n\nWe can open the order on your confirmation.\n\nKind regards"
    )
