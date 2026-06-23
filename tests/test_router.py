import json

from intent_router.model_client import FakeStructuredClient
from intent_router.router import IntentRouter
from intent_router.types import EvaluationDecision, IntentDecision, SkillRouteDecision


async def test_router_returns_matched_result() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
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
                skill_id="file_skill",
                confidence=0.9,
            ),
            IntentDecision(status="no_match", reason="search request"),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
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
    assert model.calls[0][2] is SkillRouteDecision
    assert [call[2] for call in model.calls].count(SkillRouteDecision) == 2


async def test_router_retries_after_skill_mismatch_evaluation() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="file_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="文件",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="skill_mismatch",
                reason="搜索资源应选择云盘搜索",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同"]},
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

    second_router_prompt = json.loads(model.calls[3][1])
    assert second_router_prompt["rejected_skill_ids"] == ["file_skill"]


async def test_router_retries_same_skill_after_intent_mismatch() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI美颜修图",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="intent_mismatch",
                reason="具体修图操作应使用 AI改图",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI改图",
                code="037",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我修一下这张图，把背景换成海边")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "image_skill"
    assert result.intent == "AI改图"
    assert result.code == "037"
    assert result.visited_skills == ["image_skill"]

    second_intent_prompt = model.calls[4][1]
    assert "AI美颜修图" in second_intent_prompt
    assert "具体修图操作应使用 AI改图" in second_intent_prompt


async def test_router_returns_no_match_for_unsupported_capability() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="现有 skills 不支持文生视频",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("生成一段小狗奔跑的视频")

    assert result.status == "no_match"
    assert result.reason == "现有 skills 不支持文生视频"


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
                skill_id="file_skill",
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
    state = json.loads(first.resume_token)
    assert state["original_query"] == "帮我找一下"
    assert state["clarifications"] == []

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
                skill_id="mcloud_search_skill",
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


async def test_router_returns_clarification_for_overlapping_baby_intents() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想基于哪类照片预测宝宝样子？",
                options=[
                    {"label": "已出生宝宝照片", "value": "baby_photo"},
                    {"label": "父母双方照片", "value": "parents_photo"},
                ],
                reason="宝宝时光机和宝宝长相预测依赖不同输入来源",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("我宝宝未来的样子")

    assert result.status == "clarify"
    assert result.question == "你想基于哪类照片预测宝宝样子？"
    assert result.resume_token

    intent_system_prompt = model.calls[1][0]
    intent_user_prompt = model.calls[1][1]
    assert "竞争意图检查" in intent_system_prompt
    assert "输入来源" in intent_system_prompt
    assert "二级意图选择通用原则" in intent_user_prompt


async def test_router_resumes_baby_clarification_to_time_machine() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想基于哪类照片预测宝宝样子？",
                options=[
                    {"label": "已出生宝宝照片", "value": "baby_photo"},
                    {"label": "父母双方照片", "value": "parents_photo"},
                ],
            ),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="宝宝时光机",
                code="029",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    first = await router.route("我宝宝未来的样子")
    second = await router.route("我有已出生宝宝照片", resume_token=first.resume_token)

    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "image_skill"
    assert second.intent == "宝宝时光机"
    assert second.code == "029"


async def test_router_resumes_baby_clarification_to_appearance_prediction() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想基于哪类照片预测宝宝样子？",
                options=[
                    {"label": "已出生宝宝照片", "value": "baby_photo"},
                    {"label": "父母双方照片", "value": "parents_photo"},
                ],
            ),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="宝宝长相预测",
                code="030",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    first = await router.route("我宝宝未来的样子")
    second = await router.route("用父母双方照片预测", resume_token=first.resume_token)

    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "image_skill"
    assert second.intent == "宝宝长相预测"
    assert second.code == "030"


async def test_router_clarifies_ambiguous_photo_repair() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想修复老照片，还是提升照片清晰度？",
                options=[
                    {"label": "老照片修复", "value": "old_photo"},
                    {"label": "提升清晰度", "value": "quality"},
                ],
                reason="修复照片未说明处理对象或目标",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我修复照片")

    assert result.status == "clarify"
    assert result.question == "你想修复老照片，还是提升照片清晰度？"


async def test_router_matches_specific_photo_repair_intents() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="老照片修复",
                code="008",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="画质修复",
                code="009",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    old_photo = await router.route("老照片修复一下")
    high_quality = await router.route("让这张照片更清晰")

    assert old_photo.status == "matched"
    assert old_photo.intent == "老照片修复"
    assert high_quality.status == "matched"
    assert high_quality.intent == "画质修复"


async def test_router_distinguishes_entry_and_specific_edit_operation() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI美颜修图",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI改图",
                code="037",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    entry = await router.route("打开AI修图")
    edit = await router.route("把这张图背景换成海边")

    assert entry.status == "matched"
    assert entry.intent == "AI美颜修图"
    assert edit.status == "matched"
    assert edit.intent == "AI改图"


async def test_router_rejects_invalid_resume_token() -> None:
    router = IntentRouter.from_config("skills", model_client=FakeStructuredClient([]))

    try:
        await router.route("继续", resume_token="not-json")
    except ValueError as exc:
        assert str(exc) == "Invalid resume token: expected JSON state"
    else:
        raise AssertionError("Expected invalid resume token to raise ValueError")


if __name__ == "__main__":
    test_router_returns_clarification_and_resumes()
    test_router_retries_after_skill_no_match()
    test_router_retries_same_skill_after_intent_mismatch()
    test_router_rejects_invalid_candidate_and_returns_no_match()
