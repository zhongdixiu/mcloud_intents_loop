from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openpyxl import Workbook, load_workbook

from .router import IntentRouter
from .skills import SkillRegistry
from .types import (
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    RouteResult,
    SkillDefinition,
)


CURRENT_QUERY_HEADER = "当前对话"
CURRENT_EXPECTED_HEADER = "当前预期意图"
CURRENT_ALTERNATE_HEADER = "备注意图"
SEARCH_SKILL_ID = "mcloud_search_skill"
SEARCH_022_CODE = "022"
ORDINARY_DIALOGUE_CODE = "000"


@dataclass(frozen=True)
class HistoryCaseTurn:
    query: str
    expected_code: str


@dataclass(frozen=True)
class XlsxEvalCase:
    row_index: int
    query: str
    expected_code: str
    alternate_codes: list[str]
    history_turns: list[HistoryCaseTurn]


async def evaluate_xlsx_cases(
    cases_path: Path,
    *,
    skills_path: str | Path = "skills",
    output_path: Path | None = None,
    trace_enabled: bool = False,
    router: IntentRouter | None = None,
    progress_callback: Callable[[int, int, dict[str, Any], int], None] | None = None,
) -> dict[str, Any]:
    registry = SkillRegistry.from_path(skills_path)
    router = router or IntentRouter.from_config(skills_path=skills_path)
    cases = load_xlsx_cases(cases_path)
    code_index = _build_code_index(registry)
    search_equivalent_codes = _search_022_equivalent_codes(registry)
    output_path = normalize_output_path(output_path or default_output_path(cases_path))

    total = 0
    passed = 0
    elapsed_values: list[float] = []
    loop_counts: list[int] = []
    status_counts: dict[str, int] = {}
    records: list[dict[str, Any]] = []

    for case in cases:
        total += 1
        history = build_gold_dialogue_history(case.history_turns, registry, code_index)
        trace: list[dict[str, Any]] | None = [] if trace_enabled else None

        started = time.perf_counter()
        result = await router.route(
            case.query,
            dialogue_history=history,
            trace=trace,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        elapsed_values.append(elapsed_ms)
        loop_counts.append(result.loop_count)
        status_counts[result.status] = status_counts.get(result.status, 0) + 1

        match = result_matches_expected_codes(
            result,
            expected_code=case.expected_code,
            alternate_codes=case.alternate_codes,
            search_equivalent_codes=search_equivalent_codes,
        )
        passed += int(match["matched"])

        record = _build_record(
            case=case,
            result=result,
            matched=match["matched"],
            match_reason=match["reason"],
            elapsed_ms=elapsed_ms,
        )
        if trace_enabled:
            record["trace"] = trace
        records.append(record)
        if progress_callback is not None:
            progress_callback(total, len(cases), record, passed)

    failed = total - passed
    summary = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "accuracy": (passed / total) if total else 0.0,
        "avg_elapsed_ms": _average(elapsed_values),
        "avg_loop_count": _average(loop_counts),
        "matched_count": status_counts.get("matched", 0),
        "clarify_count": status_counts.get("clarify", 0),
        "no_match_count": status_counts.get("no_match", 0),
        "output_path": str(output_path),
    }
    write_xlsx_result(output_path, records, summary)
    return summary


def default_output_path(cases_path: Path) -> Path:
    return cases_path.with_name(f"{cases_path.stem}_测试结果.xlsx")


def normalize_output_path(output_path: Path) -> Path:
    if not output_path.suffix:
        return output_path.with_suffix(".xlsx")
    if output_path.suffix.lower() != ".xlsx":
        raise ValueError(f"结果文件必须是 .xlsx: {output_path}")
    return output_path


def load_xlsx_cases(cases_path: Path) -> list[XlsxEvalCase]:
    workbook = load_workbook(cases_path, read_only=True, data_only=True)
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        return []

    headers = [_cell_text(cell) for cell in header_row]
    header_to_index = {
        header: index
        for index, header in enumerate(headers)
        if header
    }
    current_query_index = _require_header(header_to_index, CURRENT_QUERY_HEADER)
    current_expected_index = _require_header(header_to_index, CURRENT_EXPECTED_HEADER)
    current_alternate_index = header_to_index.get(CURRENT_ALTERNATE_HEADER)
    history_pairs = _history_column_pairs(headers, header_to_index)

    cases: list[XlsxEvalCase] = []
    for row_index, row in enumerate(rows, start=2):
        query = _cell_text(_row_value(row, current_query_index))
        expected_code = normalize_code(_row_value(row, current_expected_index))
        if not query or not expected_code:
            continue

        alternate_codes: list[str] = []
        if current_alternate_index is not None:
            alternate_codes = split_codes(_row_value(row, current_alternate_index))

        history_turns = []
        for query_index, expected_index in history_pairs:
            history_query = _cell_text(_row_value(row, query_index))
            history_code = normalize_code(_row_value(row, expected_index))
            if not history_query or not history_code:
                continue
            history_turns.append(
                HistoryCaseTurn(query=history_query, expected_code=history_code),
            )

        cases.append(
            XlsxEvalCase(
                row_index=row_index,
                query=query,
                expected_code=expected_code,
                alternate_codes=alternate_codes,
                history_turns=history_turns,
            ),
        )
    return cases


def build_gold_dialogue_history(
    history_turns: list[HistoryCaseTurn],
    registry: SkillRegistry,
    code_index: dict[str, tuple[SkillDefinition, str]],
) -> DialogueHistory:
    turns: list[DialogueTurn] = []
    for history_turn in history_turns:
        summary = _summary_for_expected_code(
            history_turn.expected_code,
            registry,
            code_index,
        )
        turns.append(
            DialogueTurn(
                user_query=history_turn.query,
                result=summary,
                metadata={"source": "xlsx_gold_history"},
            ),
        )
    return DialogueHistory(turns=turns)


def result_matches_expected_codes(
    result: RouteResult,
    *,
    expected_code: str,
    alternate_codes: list[str],
    search_equivalent_codes: set[str],
) -> dict[str, Any]:
    accepted_codes = [expected_code, *alternate_codes]
    predicted_code = result.code
    predicted_skill_id = result.skill.id if result.skill else None

    for accepted_code in accepted_codes:
        if predicted_code == accepted_code:
            return {"matched": True, "reason": "exact_code"}
        if (
            accepted_code == SEARCH_022_CODE
            and predicted_skill_id == SEARCH_SKILL_ID
            and predicted_code in search_equivalent_codes
        ):
            return {"matched": True, "reason": "022_search_equivalent"}

    return {"matched": False, "reason": "code_mismatch"}


def split_codes(value: Any) -> list[str]:
    text = _cell_text(value)
    if not text:
        return []
    codes = []
    for item in re.split(r"[、,，;；/]", text):
        code = normalize_code(item)
        if code and code not in codes:
            codes.append(code)
    return codes


def write_xlsx_result(
    output_path: Path,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    result_sheet = workbook.active
    result_sheet.title = "results"
    headers = [
        "row_index",
        "history_turn_count",
        "query",
        "expected_code",
        "alternate_codes",
        "predicted_status",
        "predicted_skill_id",
        "predicted_intent",
        "predicted_code",
        "matched",
        "match_reason",
        "elapsed_ms",
        "loop_count",
        "correction_scopes",
        "resolved_query",
        "context_relation",
        "question",
        "options",
        "reason",
        "trace",
    ]
    result_sheet.append(headers)
    for record in records:
        result_sheet.append([_excel_cell(record.get(header)) for header in headers])

    summary_sheet = workbook.create_sheet("summary")
    summary_sheet.append(["metric", "value"])
    for key, value in summary.items():
        summary_sheet.append([key, _excel_cell(value)])

    workbook.save(output_path)


def normalize_code(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return str(value).strip()
        value = int(value)
    if isinstance(value, int):
        if value == 0:
            return ORDINARY_DIALOGUE_CODE
        if 0 < value < 1000:
            return f"{value:03d}"
        if 10000 <= value < 100000:
            return f"{value:06d}"
        return str(value)

    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"0+", text):
        return ORDINARY_DIALOGUE_CODE
    if re.fullmatch(r"\d+", text):
        number = int(text)
        if number == 0:
            return ORDINARY_DIALOGUE_CODE
        if len(text) < 3 and number < 1000:
            return f"{number:03d}"
        if len(text) == 5 and 10000 <= number < 100000:
            return f"{number:06d}"
    return text


def _summary_for_expected_code(
    code: str,
    registry: SkillRegistry,
    code_index: dict[str, tuple[SkillDefinition, str]],
) -> DialogueRouteSummary:
    if code == ORDINARY_DIALOGUE_CODE:
        return DialogueRouteSummary(
            status="matched",
            intent="普通对话",
            code=ORDINARY_DIALOGUE_CODE,
        )

    if code == SEARCH_022_CODE and registry.has(SEARCH_SKILL_ID):
        skill = registry.get(SEARCH_SKILL_ID)
        return DialogueRouteSummary(
            status="matched",
            skill_id=skill.id,
            skill_name=skill.name,
            intent="搜索类历史期望",
            code=SEARCH_022_CODE,
        )

    indexed = code_index.get(code)
    if indexed is None:
        return DialogueRouteSummary(
            status="matched",
            intent=f"历史意图{code}",
            code=code,
        )

    skill, intent_name = indexed
    return DialogueRouteSummary(
        status="matched",
        skill_id=skill.id,
        skill_name=skill.name,
        intent=intent_name,
        code=code,
    )


def _build_code_index(
    registry: SkillRegistry,
) -> dict[str, tuple[SkillDefinition, str]]:
    code_index: dict[str, tuple[SkillDefinition, str]] = {}
    if registry.has(SEARCH_SKILL_ID):
        search_skill = registry.get(SEARCH_SKILL_ID)
        for intent_name, intent_schema in search_skill.intents.items():
            if re.fullmatch(r"01\d", intent_schema.code):
                code_index.setdefault(intent_schema.code, (search_skill, intent_name))

    for card in registry.cards():
        skill = registry.get(card.id)
        for intent_name, intent_schema in skill.intents.items():
            code_index.setdefault(intent_schema.code, (skill, intent_name))
    return code_index


def _search_022_equivalent_codes(registry: SkillRegistry) -> set[str]:
    if not registry.has(SEARCH_SKILL_ID):
        return set()
    search_skill = registry.get(SEARCH_SKILL_ID)
    return {
        intent_schema.code
        for intent_schema in search_skill.intents.values()
        if re.fullmatch(r"01\d", intent_schema.code)
    }


def _build_record(
    *,
    case: XlsxEvalCase,
    result: RouteResult,
    matched: bool,
    match_reason: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    skill_id = result.skill.id if result.skill else None
    return {
        "row_index": case.row_index,
        "history_turn_count": len(case.history_turns),
        "query": case.query,
        "expected_code": case.expected_code,
        "alternate_codes": case.alternate_codes,
        "predicted_status": result.status,
        "predicted_skill_id": skill_id,
        "predicted_intent": result.intent,
        "predicted_code": result.code,
        "matched": matched,
        "match_reason": match_reason,
        "elapsed_ms": round(elapsed_ms, 3),
        "loop_count": result.loop_count,
        "correction_scopes": result.correction_scopes,
        "resolved_query": result.resolved_query,
        "context_relation": result.context_relation,
        "question": result.question,
        "options": result.options,
        "reason": result.reason,
    }


def _history_column_pairs(
    headers: list[str],
    header_to_index: dict[str, int],
) -> list[tuple[int, int]]:
    pairs = []
    for index, header in enumerate(headers):
        match = re.fullmatch(r"第(.+)轮对话", header)
        if not match:
            continue
        expected_header = f"第{match.group(1)}轮预期意图"
        expected_index = header_to_index.get(expected_header)
        if expected_index is None:
            continue
        pairs.append((index, expected_index))
    return pairs


def _require_header(header_to_index: dict[str, int], header: str) -> int:
    index = header_to_index.get(header)
    if index is None:
        raise ValueError(f"Excel 缺少必需表头: {header}")
    return index


def _row_value(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if index < len(row) else None


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _average(values: list[float] | list[int]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _excel_cell(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        import json

        return json.dumps(value, ensure_ascii=False)
    return value
