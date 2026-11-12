# mailorder

An agent workflow for a shared order mailbox. It reads a free-form shipment request, works out what is missing, asks the customer for it, and replies with a quote once the request is complete. Anything it should not decide alone goes to a person with the reason.

The design comes from an e-mail-to-order system I built for a logistics operation. The code here is written from scratch and runs on invented e-mails, customers and rates; it contains no client code or data.

## How it works

```
incoming e-mail
   │
   ├─ already processed ────────────────────────────────> nothing
   ├─ not a shipment request ───────────────────────────> ignored
   ├─ sender is not a known customer ───────────────────> person
   ├─ required fields missing ──> ask ──> wait for reply ─┐
   │                               ^                      │
   │                               └──────────────────────┤ after two follow-ups ──> person
   ├─ pickup date in the past, or lane not priced ──────> person
   └─ complete ──> price from the rate card ──> quote
```

The model has one job: read an e-mail into a schema. Everything that commits the company to something is ordinary code:

- **Who the customer is** comes from the sender's domain, not from the name the e-mail claims.
- **The price** comes from the rate card and the customer's discount. The model never produces a number that goes into a quote.
- **The reply** is a template filled with the values that were priced, in the language of the e-mail.
- **When to stop** is a rule: two follow-ups, then a person.

A thread keeps its state between messages. A reply is merged into the stored request, so a customer can supply a missing field or correct an earlier one. Each e-mail is processed once; a redelivered message sends nothing. State is a JSON document per thread, so a restart continues where the last run stopped.

## Quick start

```bash
pip install -e ".[dev]"
mailorder show t03
mailorder eval
pytest
```

`mailorder show t03` replays one sample thread:

```
<<< Sevkiyat
Merhaba, İstanbul'dan Rotterdam'a kumaş sevkiyatımız olacak, teklif alabilir miyiz?

=== ask_missing: required fields are missing
>>>
Merhaba,

Talebiniz için teşekkür ederiz. Teklif hazırlayabilmemiz için şu bilgilere ihtiyacımız var:
- yükleme tarihi
- toplam ağırlık (kg)

İyi çalışmalar

<<< Re: Sevkiyat
Yükleme 20.03.2026, toplam 3.000 kg.

=== send_quote: request is complete and the lane is priced
>>>
Merhaba,

Istanbul - Rotterdam sevkiyatınız için teklifimiz:
- Yükleme tarihi: 20.03.2026
- Ağırlık: 3000 kg
- Fiyat: 1440.00 EUR
- Transit süre: 7 gün
- Geçerlilik: 09.03.2026

Onayınızla siparişi açabiliriz.

İyi çalışmalar
```

## Results on the sample threads

`samples/threads.json` holds 16 hand-written threads (21 e-mails) in Turkish and English with the expected action for every message and the expected request for every thread that should be quoted. They cover complete requests, missing fields, corrections in a reply, unknown senders, unpriced lanes, past dates, mail that is not a request, and wording a pattern matcher cannot read.

With the rule-based extractor, `mailorder eval`:

| Measure | Result |
| --- | --- |
| Threads handled as expected | 14 / 16 |
| Request fields read correctly (quotable threads) | 34 / 40 |
| Quotes sent | 8 |
| Quotes sent with a wrong field, or where none was expected | 0 |

The two misses are the threads written to defeat patterns: "pickup next Monday", and a request that names the cities indirectly and gives the date as "tomorrow". In both the extractor leaves the fields empty and the workflow asks the customer for them. It is slower than it should be, and nothing wrong goes out. That is the property the workflow is built around: a weak extractor costs follow-up questions, not wrong quotes.

The LLM extractor plugs into the same workflow; the test suite drives that path with a model stub.

## Using the Azure extractor

```bash
pip install -e ".[azure]"
cp .env.example .env   # fill in endpoint, key and deployment, then export them
mailorder --backend azure eval
```

The prompt gives the model today's date and weekday so it can resolve relative dates, and the list of city names from the rate card so that "Münih" and "Munich" come back as the same lane. Any model that can fill a Pydantic schema works: implement `parse(system, user, schema)`.

## Connecting real systems

The workflow depends on five interfaces in `ports.py`:

| Port | Sample implementation | In production |
| --- | --- | --- |
| `Extractor` | patterns, or a structured-output model | the same |
| `CustomerDirectory` | domains in a JSON file | CRM or ERP customer master |
| `Pricing` | rate card in a JSON file | tariff service |
| `StateStore` | memory, or JSON files | a database table keyed by thread |
| `Outbox` | records what would be sent | mail API, ticket queue for escalations |

Reading the mailbox is left to the caller: build an `Email` from each new message and call `Workflow.handle`.

## Layout

```
src/mailorder/
  workflow.py    the state machine
  models.py      Email, OrderRequest, ThreadState, Action
  ports.py       interfaces
  adapters.py    customer directory, rate card, state stores, outbox
  extract.py     rule-based extractor, LLM extractor, Azure OpenAI model
  replies.py     reply templates (Turkish, English)
  evaluate.py    replay and scoring
  cli.py         mailorder show / mailorder eval
samples/         threads and reference data
tests/
```

## License

MIT
