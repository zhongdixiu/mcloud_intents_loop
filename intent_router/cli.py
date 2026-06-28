from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .dialogue import IntentDialogueAgent
from .router import IntentRouter
from .types import RouteResult
from .xlsx_eval import evaluate_format_xlsx_cases, evaluate_xlsx_cases


def main() -> None:
    parser = argparse.ArgumentParser(prog="intent_router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("query")
    route_parser.add_argument("--skills", default="skills")
    route_parser.add_argument("--trace", action="store_true")
    route_parser.add_argument("--history-limit", type=int, default=5)

    chat_parser = subparsers.add_parser("chat")
    chat_parser.add_argument("--skills", default="skills")
    chat_parser.add_argument("--trace", action="store_true")
    chat_parser.add_argument("--history-limit", type=int, default=5)

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--cases", required=True)
    eval_parser.add_argument("--skills", default="skills")
    eval_parser.add_argument("--trace", action="store_true")
    eval_parser.add_argument("--history-limit", type=int, default=5)

    eval_xlsx_parser = subparsers.add_parser("eval-xlsx")
    eval_xlsx_parser.add_argument("--cases", required=True)
    eval_xlsx_parser.add_argument("--skills", default="skills")
    eval_xlsx_parser.add_argument(
        "--output",
        help="结果 Excel 文件路径，默认保存到用例文件同目录的 *_测试结果.xlsx",
    )
    eval_xlsx_parser.add_argument("--trace", action="store_true")
    eval_xlsx_parser.add_argument("--history-limit", type=int, default=5)
    eval_xlsx_parser.add_argument(
        "--if-end2end",
        "--if_end2end",
        action="store_true",
        dest="if_end2end",
        help="按端到端多轮对话模拟执行历史轮次，默认使用表格期望意图构造历史",
    )
    eval_xlsx_parser.add_argument(
        "--compare-no-loop",
        action="store_true",
        dest="compare_no_loop",
        help="同时执行无 evaluator/无修正 loop 的一级+二级路由链路并写入对比结果",
    )

    eval_format_xlsx_parser = subparsers.add_parser("eval-format-xlsx")
    eval_format_xlsx_parser.add_argument("--cases", required=True)
    eval_format_xlsx_parser.add_argument("--skills", default="skills")
    eval_format_xlsx_parser.add_argument(
        "--output",
        help="结果 Excel 文件路径，默认保存到用例文件同目录的 *_测试结果.xlsx",
    )
    eval_format_xlsx_parser.add_argument("--trace", action="store_true")
    eval_format_xlsx_parser.add_argument("--history-limit", type=int, default=5)
    eval_format_xlsx_parser.add_argument(
        "--if-end2end",
        "--if_end2end",
        action="store_true",
        dest="if_end2end",
        help="按端到端多轮对话模拟执行历史轮次，默认使用表格期望意图构造历史",
    )
    eval_format_xlsx_parser.add_argument(
        "--compare-no-loop",
        action="store_true",
        dest="compare_no_loop",
        help="同时执行无 evaluator/无修正 loop 的一级+二级路由链路并写入对比结果",
    )

    args = parser.parse_args()
    if args.command == "route":
        try:
            asyncio.run(_route(args.query, args.skills, args.trace, args.history_limit))
        except ValueError as exc:
            parser.error(str(exc))
    elif args.command == "chat":
        asyncio.run(_chat(args.skills, args.trace, args.history_limit))
    elif args.command == "eval":
        asyncio.run(_eval(Path(args.cases), args.skills, args.trace, args.history_limit))
    elif args.command == "eval-xlsx":
        output = Path(args.output) if args.output else None
        asyncio.run(
            _eval_xlsx(
                Path(args.cases),
                args.skills,
                output,
                args.trace,
                args.history_limit,
                args.if_end2end,
                args.compare_no_loop,
            ),
        )
    elif args.command == "eval-format-xlsx":
        output = Path(args.output) if args.output else None
        asyncio.run(
            _eval_format_xlsx(
                Path(args.cases),
                args.skills,
                output,
                args.trace,
                args.history_limit,
                args.if_end2end,
                args.compare_no_loop,
            ),
        )


async def _route(
    query: str,
    skills: str,
    trace_enabled: bool,
    history_limit: int = 5,
) -> None:
    router = IntentRouter.from_config(
        skills_path=skills,
        dialogue_history_limit=history_limit,
        mode="production",
    )
    trace: list[dict] | None = [] if trace_enabled else None
    result = await router.route(query, trace=trace)
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


async def _chat(skills: str, trace_enabled: bool, history_limit: int = 5) -> None:
    agent = IntentDialogueAgent.from_config(
        skills_path=skills,
        dialogue_history_limit=history_limit,
        mode="production",
    )
    _print_chat_help()
    while True:
        try:
            query = input("query> ").strip()
        except EOFError:
            print("\n对话结束。")
            break

        if not query:
            print("请输入 query，或输入 :q 结束。")
            continue

        command = query.lower()
        if command in {"exit", "quit", ":q", ":quit"}:
            print("对话结束。")
            break
        if command in {":h", ":help", "help"}:
            _print_chat_help()
            continue
        if command in {":history", ":hist"}:
            _print_chat_history(agent)
            continue
        if command in {":clear", ":reset"}:
            agent.history.turns.clear()
            print("已清空对话历史。")
            continue

        trace: list[dict] | None = [] if trace_enabled else None
        result = await agent.send(query, trace=trace)
        _print_chat_result(query, result)
        if trace_enabled:
            print("Trace:")
            print(json.dumps(trace, ensure_ascii=False, indent=2))


def _print_chat_help() -> None:
    print(
        "意图识别交互模式\n"
        "输入自然语言 query 后回车，系统会结合本轮输入和历史对话输出意图决策。\n"
        "空行会被忽略，不会结束对话。\n"
        "快捷命令：:q / :quit / exit / quit 结束；:h 查看帮助；"
        ":history 查看历史；:clear 清空历史。\n",
    )


def _print_chat_result(query: str, result: RouteResult) -> None:
    data = result.model_dump(mode="json")
    skill = data.get("skill") or {}
    if data.get("resolved_query"):
        print("上下文语义")
        print(f"  resolved_query: {data.get('resolved_query')}")
        if data.get("context_relation"):
            print(f"  relation: {data.get('context_relation')}")
    print("意图决策")
    print(f"  status: {data.get('status')}")
    if skill:
        print(f"  skill: {skill.get('name')} ({skill.get('id')})")
    if data.get("intent"):
        print(f"  intent: {data.get('intent')}")
    if data.get("code"):
        print(f"  code: {data.get('code')}")
    if data.get("params"):
        params = json.dumps(data.get("params"), ensure_ascii=False)
        print(f"  params: {params}")
    if data.get("question"):
        print(f"  question: {data.get('question')}")
    if data.get("options"):
        options = json.dumps(data.get("options"), ensure_ascii=False)
        print(f"  options: {options}")
    if data.get("reason"):
        print(f"  reason: {data.get('reason')}")
    print("Loop")
    print(f"  loop_count: {data.get('loop_count')}")
    print(f"  correction_scopes: {data.get('correction_scopes') or []}")
    if data.get("termination_reason"):
        print(f"  termination_reason: {data.get('termination_reason')}")
    print()


def _print_chat_history(agent: IntentDialogueAgent) -> None:
    if not agent.history.turns:
        print("暂无对话历史。")
        return
    print("对话历史")
    for index, turn in enumerate(agent.history.turns, start=1):
        result = turn.result
        target = result.intent or result.question or result.reason or "-"
        print(f"  {index}. query: {turn.user_query}")
        print(f"     result: {result.status} / {target}")
        if result.skill_id:
            print(f"     skill: {result.skill_name} ({result.skill_id})")


async def _eval(
    cases_path: Path,
    skills: str,
    trace_enabled: bool,
    history_limit: int = 5,
) -> None:
    router = IntentRouter.from_config(
        skills_path=skills,
        dialogue_history_limit=history_limit,
        mode="code_eval",
    )
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


async def _eval_xlsx(
    cases_path: Path,
    skills: str,
    output_path: Path | None,
    trace_enabled: bool,
    history_limit: int = 5,
    if_end2end: bool = False,
    compare_no_loop: bool = False,
) -> None:
    print("开始执行 Excel 多轮意图评测...")
    print(f"用例文件: {cases_path}")
    print(f"评测模式: {'end2end' if if_end2end else 'gold_history'}")
    print(f"无 loop 对比: {'on' if compare_no_loop else 'off'}")
    summary = await evaluate_xlsx_cases(
        cases_path,
        skills_path=skills,
        output_path=output_path,
        trace_enabled=trace_enabled,
        dialogue_history_limit=history_limit,
        if_end2end=if_end2end,
        compare_no_loop=compare_no_loop,
        progress_callback=_print_eval_xlsx_progress,
    )
    print(f"结果文件: {summary.get('output_path')}")
    print(json.dumps(summary, ensure_ascii=False))


async def _eval_format_xlsx(
    cases_path: Path,
    skills: str,
    output_path: Path | None,
    trace_enabled: bool,
    history_limit: int = 5,
    if_end2end: bool = False,
    compare_no_loop: bool = False,
) -> None:
    print("开始执行重构格式 Excel 多轮意图评测...")
    print(f"用例文件: {cases_path}")
    print(f"评测模式: {'end2end' if if_end2end else 'gold_history'}")
    print(f"无 loop 对比: {'on' if compare_no_loop else 'off'}")
    summary = await evaluate_format_xlsx_cases(
        cases_path,
        skills_path=skills,
        output_path=output_path,
        trace_enabled=trace_enabled,
        dialogue_history_limit=history_limit,
        if_end2end=if_end2end,
        compare_no_loop=compare_no_loop,
        progress_callback=_print_eval_xlsx_progress,
    )
    print(f"结果文件: {summary.get('output_path')}")
    print(json.dumps(summary, ensure_ascii=False))


def _print_eval_xlsx_progress(
    processed: int,
    total: int,
    record: dict,
    passed: int,
) -> None:
    status = "OK" if record.get("matched") else "FAIL"
    accuracy = passed / processed if processed else 0.0
    expected = (
        record.get("effective_expected_codes")
        or record.get("expected_codes")
        or record.get("expected_code")
    )
    message = (
        "[{processed}/{total}] row={row} {status} "
        "expected={expected} predicted={predicted} "
        "loop={loop} elapsed_ms={elapsed} acc={accuracy:.2%}".format(
            processed=processed,
            total=total,
            row=record.get("row_index"),
            status=status,
            expected=expected,
            predicted=record.get("predicted_code") or record.get("predicted_status"),
            loop=record.get("loop_count"),
            elapsed=record.get("elapsed_ms"),
            accuracy=accuracy,
        )
    )
    if "no_loop_matched" in record:
        no_loop_status = "OK" if record.get("no_loop_matched") else "FAIL"
        message += (
            " no_loop={status}/{predicted}".format(
                status=no_loop_status,
                predicted=record.get("no_loop_predicted_code")
                or record.get("no_loop_predicted_status"),
            )
        )
    print(message, flush=True)


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
        if event.get("event") != "intent_select":
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
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes


def _has_repeated_model_stage(trace: list[dict]) -> bool:
    counted_events = {"skill_route", "intent_select"}
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
