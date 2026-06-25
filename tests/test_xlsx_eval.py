from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from intent_router.skills import SkillRegistry
from intent_router.types import DialogueHistory, RouteResult, SkillRef
from intent_router.xlsx_eval import (
    HistoryCaseTurn,
    build_gold_dialogue_history,
    default_output_path,
    evaluate_xlsx_cases,
    load_xlsx_cases,
    normalize_code,
    result_matches_expected_codes,
    split_codes,
)
from intent_router.xlsx_eval import _build_code_index


def test_normalize_and_split_codes() -> None:
    assert normalize_code(0) == "000"
    assert normalize_code("0000") == "000"
    assert normalize_code(22) == "022"
    assert normalize_code("22") == "022"
    assert normalize_code(36006) == "036006"
    assert split_codes("012、22, 036006/0") == ["012", "022", "036006", "000"]


def test_result_matches_022_only_for_mcloud_search_01x() -> None:
    search_result = RouteResult(
        status="matched",
        skill=SkillRef(id="mcloud_search_skill", name="云盘搜索"),
        intent="搜图片",
        code="012",
    )
    note_result = RouteResult(
        status="matched",
        skill=SkillRef(id="note_skill", name="AI笔记"),
        intent="搜笔记",
        code="017",
    )

    assert result_matches_expected_codes(
        search_result,
        expected_code="022",
        alternate_codes=[],
        search_equivalent_codes={"012", "013", "014", "015", "016", "017", "018"},
    ) == {"matched": True, "reason": "022_search_equivalent"}
    assert result_matches_expected_codes(
        note_result,
        expected_code="022",
        alternate_codes=[],
        search_equivalent_codes={"012", "013", "014", "015", "016", "017", "018"},
    ) == {"matched": False, "reason": "code_mismatch"}


def test_load_xlsx_cases_skips_empty_history_cells(tmp_path: Path) -> None:
    path = tmp_path / "cases.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "第一轮对话",
            "第一轮预期意图",
            "第二轮对话",
            "第二轮预期意图",
            "当前对话",
            "当前预期意图",
            "备注意图",
        ],
    )
    sheet.append(["搜合同", "018", None, None, "蓝色", "022", "012、013"])
    workbook.save(path)

    cases = load_xlsx_cases(path)

    assert len(cases) == 1
    assert cases[0].row_index == 2
    assert cases[0].query == "蓝色"
    assert cases[0].expected_code == "022"
    assert cases[0].alternate_codes == ["012", "013"]
    assert cases[0].history_turns == [
        HistoryCaseTurn(query="搜合同", expected_code="018"),
    ]


def test_build_gold_history_maps_022_to_search_context() -> None:
    registry = SkillRegistry.from_path("skills")
    history = build_gold_dialogue_history(
        [HistoryCaseTurn(query="搜试卷", expected_code="022")],
        registry,
        _build_code_index(registry),
    )

    turn = history.turns[0]
    assert turn.user_query == "搜试卷"
    assert turn.result.status == "matched"
    assert turn.result.skill_id == "mcloud_search_skill"
    assert turn.result.code == "022"


async def test_evaluate_xlsx_cases_writes_rows_and_summary(tmp_path: Path) -> None:
    path = tmp_path / "cases.xlsx"
    output = tmp_path / "result.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "第一轮对话",
            "第一轮预期意图",
            "当前对话",
            "当前预期意图",
            "备注意图",
        ],
    )
    sheet.append(["搜合同文件", "018", "蓝色", "022", None])
    sheet.append([None, None, "闲聊一下", "000", None])
    workbook.save(path)

    router = FakeRouter(
        [
            RouteResult(
                status="matched",
                skill=SkillRef(id="mcloud_search_skill", name="云盘搜索"),
                intent="搜图片",
                code="012",
                loop_count=2,
                correction_scopes=["intent_mismatch"],
            ),
            RouteResult(
                status="matched",
                skill=None,
                intent="普通对话",
                code="000",
                loop_count=1,
            ),
        ],
    )

    summary = await evaluate_xlsx_cases(
        path,
        skills_path="skills",
        output_path=output,
        router=router,  # type: ignore[arg-type]
    )

    assert summary["total"] == 2
    assert summary["passed"] == 2
    assert summary["accuracy"] == 1.0
    assert summary["output_path"] == str(output)
    assert [len(history.turns) for history in router.histories] == [1, 0]

    workbook = load_workbook(output)
    result_sheet = workbook["results"]
    summary_sheet = workbook["summary"]
    headers = [cell.value for cell in result_sheet[1]]
    first = dict(zip(headers, [cell.value for cell in result_sheet[2]], strict=True))
    second = dict(zip(headers, [cell.value for cell in result_sheet[3]], strict=True))
    assert first["matched"] is True
    assert first["match_reason"] == "022_search_equivalent"
    assert first["loop_count"] == 2
    assert first["correction_scopes"] == '["intent_mismatch"]'
    assert second["predicted_code"] == "000"
    summary_rows = {
        row[0].value: row[1].value
        for row in summary_sheet.iter_rows(min_row=2, max_col=2)
    }
    assert summary_rows["passed"] == 2


async def test_evaluate_xlsx_cases_defaults_output_to_same_directory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cases.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["当前对话", "当前预期意图"])
    sheet.append(["闲聊一下", "000"])
    workbook.save(path)

    router = FakeRouter(
        [
            RouteResult(
                status="matched",
                skill=None,
                intent="普通对话",
                code="000",
                loop_count=1,
            ),
        ],
    )

    summary = await evaluate_xlsx_cases(
        path,
        skills_path="skills",
        router=router,  # type: ignore[arg-type]
    )

    expected_output = default_output_path(path)
    assert summary["output_path"] == str(expected_output)
    assert expected_output.exists()


async def test_evaluate_xlsx_cases_reports_progress(tmp_path: Path) -> None:
    path = tmp_path / "cases.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["当前对话", "当前预期意图"])
    sheet.append(["闲聊一下", "000"])
    workbook.save(path)

    router = FakeRouter(
        [
            RouteResult(
                status="matched",
                skill=None,
                intent="普通对话",
                code="000",
                loop_count=1,
            ),
        ],
    )
    progress_events = []

    await evaluate_xlsx_cases(
        path,
        skills_path="skills",
        router=router,  # type: ignore[arg-type]
        progress_callback=lambda processed, total, record, passed: progress_events.append(
            (processed, total, record["matched"], passed),
        ),
    )

    assert progress_events == [(1, 1, True, 1)]


class FakeRouter:
    def __init__(self, results: list[RouteResult]) -> None:
        self.results = results
        self.histories: list[DialogueHistory] = []
        self.queries: list[str] = []

    async def route(
        self,
        query: str,
        *,
        dialogue_history: DialogueHistory | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> RouteResult:
        self.queries.append(query)
        self.histories.append(dialogue_history or DialogueHistory())
        if trace is not None:
            trace.append({"event": "fake"})
        return self.results.pop(0)
