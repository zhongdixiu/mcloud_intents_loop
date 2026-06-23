from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .router import IntentRouter


def main() -> None:
    parser = argparse.ArgumentParser(prog="intent_router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("query")
    route_parser.add_argument("--skills", default="skills")
    route_parser.add_argument("--resume-token")

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--cases", required=True)
    eval_parser.add_argument("--skills", default="skills")

    args = parser.parse_args()
    if args.command == "route":
        asyncio.run(_route(args.query, args.skills, args.resume_token))
    elif args.command == "eval":
        asyncio.run(_eval(Path(args.cases), args.skills))


async def _route(query: str, skills: str, resume_token: str | None) -> None:
    router = IntentRouter.from_config(skills_path=skills)
    result = await router.route(query, resume_token=resume_token)
    print(result.model_dump_json(ensure_ascii=False, indent=2))


async def _eval(cases_path: Path, skills: str) -> None:
    router = IntentRouter.from_config(skills_path=skills)
    total = 0
    matched = 0
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        case = json.loads(line)
        result = await router.route(case["query"])
        expected = case.get("expected", {})
        ok = all(getattr(result, key) == value for key, value in expected.items())
        matched += int(ok)
        print(
            json.dumps(
                {
                    "query": case["query"],
                    "ok": ok,
                    "result": result.model_dump(mode="json"),
                },
                ensure_ascii=False,
            ),
        )
    print(json.dumps({"total": total, "passed": matched}, ensure_ascii=False))


if __name__ == "__main__":
    main()
