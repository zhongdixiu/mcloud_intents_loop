---
id: mcloud_person_skill
name: 会员与增值服务
description: 承接流量和话费查询、云盘会员查询与订购，以及保险箱和发现入口；不承接文件管理或普通会员知识问答。
version: '1.0'
scope:
- 查询流量和话费
- 会员状态与权益查询
- 会员订购
- 保险箱入口
- 发现广场入口
out_of_scope:
- 文件管理
- 云盘活动搜索
- 普通会员知识问答
aliases:
- 云盘会员
- 增值服务
status: active
---

# 会员与增值服务 Skill

## Skill Scope

本 Skill 负责：

- 查询流量和话费；
- 会员状态与权益查询；
- 会员订购；
- 保险箱入口；
- 发现广场入口；

本 Skill 不负责：

- 文件管理；
- 云盘活动搜索；
- 普通会员知识问答；

## Intent Routing Principles

1. 根据用户当前主动作选择 Intent，参数缺失不影响 Route 判断。
2. 专用 Intent 优先于通用入口；同 code 的不同 Intent 仍按完整 Route Key 区分。
3. 当前 Skill 不支持请求时允许返回空候选，不强制选择相近 Intent。
4. 二级阶段保留现有参数输出兼容，但不得因参数不全降低正确 Route 的优先级。

现有业务规则：

无额外专用规则。

## Intent Contrast Rules

### 会员查询 vs 会员订购

- 查询会员状态、价格或权益时选择“会员查询”。
- 明确购买、续费或开通会员时选择“会员订购”。

### 保险箱 vs 文件管理（跨 Skill）

- 仅打开私密存储入口可选择“保险箱”；浏览、上传或管理普通文件进入文件管理。

## Tools Schema

```json
{
  "查流量": {
    "code": "036021",
    "desc": "查询流量",
    "params": {}
  },
  "查话费": {
    "code": "036022",
    "desc": "查询话费",
    "params": {}
  },
  "保险箱": {
    "code": "021",
    "desc": "加密存储私密文件的安全空间",
    "params": {}
  },
  "发现": {
    "code": "021",
    "desc": "内容资源广场，仅在用户明确表达【打开|查找|使用|开启|进入】【发现广场】时触发",
    "params": {}
  },
  "会员查询": {
    "code": "021",
    "desc": "查询当前会员状态、价格及权益",
    "params": {}
  },
  "会员订购": {
    "code": "021",
    "desc": "会员订购",
    "params": {}
  }
}
```

## Intent-Specific Rules

- 每个 Intent 的适用范围以 Tools Schema 的 `desc` 和上述对比规则为准。
- 只抽取用户当前输入或上下文中明确存在的参数，不猜测实体或真实资源句柄。
- 参数缺失不改变已经确定的 Intent；同 code Intent 必须根据名称语义区分。

## Positive Examples

- “查一下我的会员权益” → 会员查询
- “我要续费云盘会员” → 会员订购

## Negative Examples

- “管理我的文件” → 文件管理
- “会员有什么区别” → 普通问答

## Execution Instructions

- 最终 Route 确定后，按照该 Intent 的参数 Schema 做类型、数组元素和枚举校验。
- 未明确提供的可选参数不阻塞路由；不得伪造文件、图片、邮件等业务句柄。
- 涉及删除、覆盖、外发或权限变更时，由执行阶段完成对象确认和风险确认。
