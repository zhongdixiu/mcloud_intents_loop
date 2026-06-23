from intent_router.skills import SkillRegistry


def test_load_current_flat_skills() -> None:
    registry = SkillRegistry.from_path("skills")

    assert len(registry) == 12
    cards = {card.id: card for card in registry.cards()}
    assert "mcloud_search_skill" in cards
    assert "搜图片" in cards["mcloud_search_skill"].intents

    search_skill = registry.get("mcloud_search_skill")
    assert search_skill.name == "云盘搜索"
    assert search_skill.intents["搜图片"].code == "012"
    assert "metadataList" in search_skill.intents["搜图片"].params


def test_activity_allowed_values_are_extracted() -> None:
    registry = SkillRegistry.from_path("skills")
    activity = registry.get("activity_search_skill")
    metadata = activity.intents["搜活动"].params["metadataList"]

    assert "云朵中心 签到领会员" in metadata.allowed_values
    assert "立省29！1元购会员" in metadata.allowed_values
