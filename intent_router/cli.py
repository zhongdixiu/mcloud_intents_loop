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
    route_parser.add_argument("--trace", action="store_true")

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--cases", required=True)
    eval_parser.add_argument("--skills", default="skills")
    eval_parser.add_argument("--trace", action="store_true")

    args = parser.parse_args()
    if args.command == "route":
        try:
            asyncio.run(_route(args.query, args.skills, args.resume_token, args.trace))
        except ValueError as exc:
            parser.error(str(exc))
    elif args.command == "eval":
        asyncio.run(_eval(Path(args.cases), args.skills, args.trace))


async def _route(
    query: str,
    skills: str,
    resume_token: str | None,
    trace_enabled: bool,
) -> None:
    router = IntentRouter.from_config(skills_path=skills)
    trace: list[dict] | None = [] if trace_enabled else None
    result = await router.route(query, resume_token=resume_token, trace=trace)
    if trace_enabled:
        print(
            json.dumps(
                {
                    "result": result.model_dump(mode="json"),
                    "trace": trace,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return
    print(result.model_dump_json(ensure_ascii=False, indent=2))


async def _eval(cases_path: Path, skills: str, trace_enabled: bool) -> None:
    router = IntentRouter.from_config(skills_path=skills)
    total = 0
    passed = 0
    first_pass_ok = 0
    loop_triggered = 0
    loop_corrected = 0
    loop_failed = 0
    correction_scope_counts: dict[str, int] = {}
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        case = json.loads(line)
        trace: list[dict] = []
        result = await router.route(case["query"], trace=trace)
        expected = case.get("expected", {})
        final_ok = _result_matches_expected(result, expected)
        assessment = _assess_loop(trace, expected, final_ok)
        passed += int(final_ok)
        first_pass_ok += int(assessment["first_pass_ok"])
        loop_triggered += int(assessment["loop_triggered"])
        loop_corrected += int(assessment["loop_corrected"])
        loop_failed += int(assessment["loop_failed"])
        for scope in assessment["correction_scopes"]:
            correction_scope_counts[scope] = correction_scope_counts.get(scope, 0) + 1

        payload = {
            "id": case.get("id"),
            "query": case["query"],
            "final_ok": final_ok,
            **assessment,
            "result": result.model_dump(mode="json"),
        }
        if trace_enabled:
            payload["trace"] = trace
        print(json.dumps(payload, ensure_ascii=False))

    print(
        json.dumps(
            {
                "total": total,
                "passed": passed,
                "first_pass_ok": first_pass_ok,
                "loop_triggered": loop_triggered,
                "loop_corrected": loop_corrected,
                "loop_failed": loop_failed,
                "correction_scopes": correction_scope_counts,
            },
            ensure_ascii=False,
        ),
    )


def _result_matches_expected(result: object, expected: dict) -> bool:
    result_data = _route_result_data(result)
    for key, expected_value in expected.items():
        if key == "absent_params":
            params = result_data.get("params") or {}
            if any(param_name in params for param_name in expected_value):
                return False
            continue
        if key == "params":
            params = result_data.get("params") or {}
            if not _dict_contains(params, expected_value):
                return False
            continue
        if result_data.get(key) != expected_value:
            return False
    return True


def _assess_loop(
    trace: list[dict],
    expected: dict,
    final_ok: bool,
) -> dict:
    first_candidate = _first_candidate(trace)
    first_pass_ok = (
        _candidate_matches_expected(first_candidate, expected)
        if first_candidate is not None
        else False
    )
    correction_scopes = _correction_scopes(trace)
    loop_triggered = bool(correction_scopes) or _has_repeated_model_stage(trace)
    return {
        "first_pass_ok": first_pass_ok,
        "loop_triggered": loop_triggered,
        "loop_corrected": bool((not first_pass_ok) and loop_triggered and final_ok),
        "loop_failed": bool((not first_pass_ok) and (not final_ok)),
        "correction_scopes": correction_scopes,
    }


def _route_result_data(result: object) -> dict:
    data = result.model_dump(mode="json")
    skill = data.get("skill") or {}
    data["skill_id"] = skill.get("id")
    return data


def _first_candidate(trace: list[dict]) -> dict | None:
    for event in trace:
        if event.get("event") not in {"intent_select", "param_repair"}:
            continue
        decision = event.get("decision") or {}
        if decision.get("status") != "matched":
            continue
        return {
            "skill_id": event.get("skill_id"),
            "intent": decision.get("intent"),
            "code": decision.get("code"),
            "params": decision.get("params") or {},
        }
    return None


def _candidate_matches_expected(candidate: dict | None, expected: dict) -> bool:
    if candidate is None:
        return False
    for key, expected_value in expected.items():
        if key == "status":
            continue
        if key == "absent_params":
            params = candidate.get("params") or {}
            if any(param_name in params for param_name in expected_value):
                return False
            continue
        if key == "params":
            params = candidate.get("params") or {}
            if not _dict_contains(params, expected_value):
                return False
            continue
        if candidate.get(key) != expected_value:
            return False
    return True


def _correction_scopes(trace: list[dict]) -> list[str]:
    scopes: list[str] = []
    for event in trace:
        scope = None
        if event.get("event") == "retry":
            scope = event.get("scope")
        elif event.get("event") == "validation_error":
            scope = event.get("correction_scope")
        elif event.get("event") == "evaluation_invalid":
            scope = "invalid_evaluation"
        elif event.get("event") == "invalid_param_repair":
            scope = "invalid_param_repair"
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes


def _has_repeated_model_stage(trace: list[dict]) -> bool:
    counted_events = {"skill_route", "intent_select", "param_repair"}
    counts: dict[str, int] = {}
    for event in trace:
        event_name = event.get("event")
        if event_name not in counted_events:
            continue
        counts[event_name] = counts.get(event_name, 0) + 1
        if counts[event_name] > 1:
            return True
    return False


def _dict_contains(actual: dict, expected: dict) -> bool:
    for key, expected_value in expected.items():
        if actual.get(key) != expected_value:
            return False
    return True


if __name__ == "__main__":
    main()
