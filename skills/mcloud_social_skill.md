---
id: mcloud_social_skill
name: 社交与共享
description: 承接共享群和家庭云的列表与创建；不承接兴趣圈子、文件分享权限管理或社交内容生成。
version: '1.0'
scope:
- 共享群列表
- 新建共享群
- 家庭云列表
- 新建家庭云
out_of_scope:
- 兴趣圈子
- 文件分享管理
- 普通社交内容生成
aliases:
- 共享群
- 家庭云
status: active
---

# 社交与共享 Skill

## Skill Scope

本 Skill 负责：

- 共享群列表；
- 新建共享群；
- 家庭云列表；
- 新建家庭云；

本 Skill 不负责：

- 兴趣圈子；
- 文件分享管理；
- 普通社交内容生成；

## Intent Routing Principles

1. 根据用户当前主动作选择 Intent，参数缺失不影响 Route 判断。
2. 专用 Intent 优先于通用入口；同 code 的不同 Intent 仍按完整 Route Key 区分。
3. 当前 Skill 不支持请求时允许返回空候选，不强制选择相近 Intent。
4. 二级阶段保留现有参数输出兼容，但不得因参数不全降低正确 Route 的优先级。

现有业务规则：

无额外专用规则。

## Intent Contrast Rules

### 共享群 vs 家庭云

- 普通多人共享空间选择共享群相关 Intent。
- 明确家庭成员或家庭云空间时选择家庭云相关 Intent。

### 新建 vs 列表

- 明确创建时选择“新建”；查看或进入已有空间时选择“列表”。

## Tools Schema

```json
{
  "共享群列表": {
    "code": "021",
    "desc": "查看当前参与的共享群组列表",
    "params": {}
  },
  "新建共享群": {
    "code": "021",
    "desc": "创建新的资源共享群组",
    "params": {}
  },
  "家庭云列表": {
    "code": "021",
    "desc": "家庭云空间成员和内容管理页面",
    "params": {}
  },
  "新建家庭云": {
    "code": "021",
    "desc": "创建新的家庭云共享空间",
    "params": {}
  }
}
```

## Intent-Specific Rules

- 每个 Intent 的适用范围以 Tools Schema 的 `desc` 和上述对比规则为准。
- 只抽取用户当前输入或上下文中明确存在的参数，不猜测实体或真实资源句柄。
- 参数缺失不改变已经确定的 Intent；同 code Intent 必须根据名称语义区分。

## Positive Examples

- “新建家庭云” → 新建家庭云
- “查看共享群” → 共享群列表

## Negative Examples

- “创建兴趣圈子” → 圈子工具
- “管理文件分享权限” → 云盘功能工具

## Execution Instructions

- 最终 Route 确定后，按照该 Intent 的参数 Schema 做类型、数组元素和枚举校验。
- 未明确提供的可选参数不阻塞路由；不得伪造文件、图片、邮件等业务句柄。
- 涉及删除、覆盖、外发或权限变更时，由执行阶段完成对象确认和风险确认。
