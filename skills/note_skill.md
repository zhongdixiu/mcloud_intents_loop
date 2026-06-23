---
name: 笔记工具
description: 在云盘资产中搜索笔记文件。
---

### Special Rules


### Tools Schema
{
    "搜笔记": {"code": "017", "desc": "搜索笔记文件", "params": {"timeList": {"type": "list[str]", "desc": "提取的时间信息，如[\"去年\"、\"上周\"、\"2023年\"]"}, "metadataList": {"type": "list[str]", "desc": "提取的关键词/内容，如[\"会议记录\"、\"购物清单\"、\"待办事项\"]"}, "titleList": {"type": "list[str]", "desc": "提取的标题，如[\"项目计划\"、\"周报\"]"}}}
}
