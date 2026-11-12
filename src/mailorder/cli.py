from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from mailorder.evaluate import build, evaluate, replay


def _extractor(backend: str, reference: Path):
    if backend == "rules":
        return None
    from mailorder.extract import AzureOpenAIModel, LLMExtractor

    cities = list(json.loads(reference.read_text(encoding="utf-8"))["cities"])
    return LLMExtractor(AzureOpenAIModel(), cities)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailorder")
    parser.add_argument("--backend", choices=("rules", "azure"), default="rules")
    parser.add_argument("--threads", type=Path, default=Path("samples/threads.json"))
    parser.add_argument("--reference", type=Path, default=Path("samples/reference.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("eval", help="replay all labelled threads and print the score")
    show = sub.add_parser("show", help="replay one thread and print each step")
    show.add_argument("thread_id")
    args = parser.parse_args(argv)

    extractor = _extractor(args.backend, args.reference)
    if args.command == "eval":
        json.dump(
            evaluate(args.threads, args.reference, extractor),
            sys.stdout,
            indent=2,
            ensure_ascii=False,
        )
        print()
        return 0
    data = json.loads(args.threads.read_text(encoding="utf-8"))
    thread = next((t for t in data["threads"] if t["thread_id"] == args.thread_id), None)
    if thread is None:
        parser.error(f"no thread {args.thread_id}")
    workflow, _ = build(args.reference, extractor)
    for mail, action in zip(
        thread["emails"], replay(workflow, thread, date.fromisoformat(data["today"]))
    ):
        print(f"<<< {mail['subject']}\n{mail['body']}\n")
        print(f"=== {action.kind.value}: {action.reason}")
        if action.reply:
            print(f">>>\n{action.reply}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
