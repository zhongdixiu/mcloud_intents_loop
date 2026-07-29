---
id: function_skill
name: 云盘功能工具
description: 承接分享管理列表、发现广场和 AI 超市等通用功能入口；不替代具体 AI 工具或云盘资源搜索。
version: '1.0'
scope:
- 分享管理列表
- 发现广场入口
- AI 超市入口
out_of_scope:
- 具体 AI 工具执行
- 云盘内容搜索
- 文件分享操作
aliases:
- 云盘功能入口
- 通用功能入口
status: active
---

# 云盘功能工具 Skill

## Skill Scope

本 Skill 负责：

- 分享管理列表；
- 发现广场入口；
- AI 超市入口；

本 Skill 不负责：

- 具体 AI 工具执行；
- 云盘内容搜索；
- 文件分享操作；

## Intent Routing Principles

1. 根据用户当前主动作选择 Intent，参数缺失不影响 Route 判断。
2. 专用 Intent 优先于通用入口；同 code 的不同 Intent 仍按完整 Route Key 区分。
3. 当前 Skill 不支持请求时允许返回空候选，不强制选择相近 Intent。
4. 二级阶段保留现有参数输出兼容，但不得因参数不全降低正确 Route 的优先级。

现有业务规则：

无额外专用规则。

## Intent Contrast Rules

### AI超市 vs 具体 AI 工具（跨 Skill）

- 仅进入 AI 工具聚合平台时选择“AI超市”。
- 用户已经点明 PPT、修图、编程等具体能力时，应选择对应业务 Skill。

### 发现 vs 云盘搜索（跨 Skill）

- 明确打开发现广场时选择“发现”；查找已有资源时进入云盘搜索。

## Tools Schema

```json
{
  "分享管理列表": {
    "code": "021",
    "desc": "管理已分享文件的状态及权限",
    "params": {}
  },
  "发现": {
    "code": "021",
    "desc": "内容资源广场，仅在用户明确表达【打开|查找|使用|开启|进入】【发现广场】时触发",
    "params": {}
  },
  "AI超市": {
    "code": "021",
    "desc": "集中提供各类AI工具的入口平台",
    "params": {}
  }
}
```

## Intent-Specific Rules

- 每个 Intent 的适用范围以 Tools Schema 的 `desc` 和上述对比规则为准。
- 只抽取用户当前输入或上下文中明确存在的参数，不猜测实体或真实资源句柄。
- 参数缺失不改变已经确定的 Intent；同 code Intent 必须根据名称语义区分。

## Positive Examples

- “进入 AI 超市” → AI超市
- “打开发现广场” → 发现

## Negative Examples

- “生成一份 PPT” → 办公效率与创作
- “找昨天的文件” → 云盘搜索

## Execution Instructions

- 最终 Route 确定后，按照该 Intent 的参数 Schema 做类型、数组元素和枚举校验。
- 未明确提供的可选参数不阻塞路由；不得伪造文件、图片、邮件等业务句柄。
- 涉及删除、覆盖、外发或权限变更时，由执行阶段完成对象确认和风险确认。
