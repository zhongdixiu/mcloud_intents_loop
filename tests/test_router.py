from intent_router.model_client import FakeStructuredClient
from intent_router.router import IntentRouter
from intent_router.types import EvaluationDecision, IntentDecision, SkillRouteDecision


async def test_router_returns_matched_result() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                candidate_skill_ids=["mcloud_search_skill"],
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={
                    "timeList": ["上个月"],
                    "metadataList": ["猫"],
                    "placeList": ["北京"],
                },
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.7),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我找上个月北京拍的猫照片")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜图片"
    assert result.code == "012"
    assert result.params["metadataList"] == ["猫"]


async def test_router_retries_after_skill_no_match() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                candidate_skill_ids=["file_skill"],
                confidence=0.9,
            ),
            IntentDecision(status="no_match", reason="search request"),
            SkillRouteDecision(
                status="route",
                candidate_skill_ids=["mcloud_search_skill"],
                confidence=0.8,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同", "文件"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索合同文件")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜综合"
    assert result.visited_skills == ["file_skill", "mcloud_search_skill"]


async def test_router_returns_clarification_and_resumes() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="clarify",
                question="你想搜索资源还是打开入口？",
                options=[
                    {"label": "搜索资源", "value": "search"},
                    {"label": "打开入口", "value": "open"},
                ],
            ),
            SkillRouteDecision(
                status="route",
                candidate_skill_ids=["file_skill"],
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="文件",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    first = await router.route("帮我找一下")
    assert first.status == "clarify"
    assert first.resume_token

    second = await router.route("打开文件入口", resume_token=first.resume_token)
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "file_skill"
    assert second.intent == "文件"


async def test_router_rejects_invalid_candidate_and_returns_no_match() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                candidate_skill_ids=["mcloud_search_skill"],
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"suffixList": ["jpg"]},
                confidence=0.8,
            ),
            SkillRouteDecision(status="no_match", reason="no remaining skill"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("找猫照片")

    assert result.status == "no_match"
    assert "mcloud_search_skill" in result.visited_skills


if __name__ == "__main__":
    test_router_returns_clarification_and_resumes()
    test_router_retries_after_skill_no_match()
    test_router_rejects_invalid_candidate_and_returns_no_match()