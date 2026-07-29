---
id: mail_skill
name: 邮件/简报服务
description: 承接邮件搜索、问答与简报、写信、回信、整理和编辑；不承接普通文本写作或云盘文件搜索。
version: '1.0'
scope:
- 邮件搜索
- 邮件问答与简报
- 写信和回信
- 邮件整理
- 编辑邮件
out_of_scope:
- 普通文本写作
- 会议通知工具
- 云盘文件搜索
aliases:
- 云盘邮箱
- 邮件服务
- AI 简报
status: active
---

# 邮件/简报服务 Skill

## Skill Scope

本 Skill 负责：

- 邮件搜索；
- 邮件问答与简报；
- 写信和回信；
- 邮件整理；
- 编辑邮件；

本 Skill 不负责：

- 普通文本写作；
- 会议通知工具；
- 云盘文件搜索；

## Intent Routing Principles

1. 根据用户当前主动作选择 Intent，参数缺失不影响 Route 判断。
2. 专用 Intent 优先于通用入口；同 code 的不同 Intent 仍按完整 Route Key 区分。
3. 当前 Skill 不支持请求时允许返回空候选，不强制选择相近 Intent。
4. 二级阶段保留现有参数输出兼容，但不得因参数不全降低正确 Route 的优先级。

现有业务规则：

1. 参数抽取通用规则：
    - 所有参数字段默认输出数组；若用户未明确提及该信息，统一返回空数组 `[]`。
    - 对于模糊描述不做强行提取，例如时间类“我结婚的时候”、发件人类“那个人”等，不作为结构化参数输出。
    - `timeList` 遇到连续时间范围时必须合并为一个完整片段，不得拆分为多个时间点；如“今年9月至10月”应提取为["今年9月至10月"]，“2024年3月-5月”应提取为["2024年3月-5月"]。
    - 地理区域词（国家/地区/城市）默认不计入 `senderList`/`recipientList`；仅当其与明确的人名、组织名或邮箱组合表达发件人/收件人时才可提取对应联系人信息。
    - 地理区域词可用于 `mailBox` 语义映射：当表达“境外/海外/国外”或明确中国境外国家地区（如“美国”“日本”“欧洲”）的邮件筛选意图时，`mailBox` 提取为["境外邮件"]。
2. 搜邮件参数抽取说明：
    - 时间(timeList)：包括具体日期、月份、年份、最近几天或时间范围；未提及则为 `[]`。
    - 元数据信息(metadataList)：抽取搜索实体；未提及则为 `[]`。
    - 文件后缀(suffixList)：抽取与邮件搜索相关的文件后缀或文件格式；未提及则为 `[]`。
    - 邮件状态(statusList)：包括【全部】【未读】【已读】；未明确提及则为 `[]`。
    - 邮件发件人(senderList)：包括【发件人】【姓名】【简称】【昵称】【外号】【发件人邮箱】；未明确提及则为 `[]`，模糊称谓不提取；国家/地区/城市等地理词不提取为发件人（如“美国”不进入 `senderList`）。
    - 邮件收件人(recipientList)：包括【收件人】【姓名】【简称】【昵称】【外号】【收件人邮箱】；未明确提及则为 `[]`。
    - 邮件类型(typeList)：包括【重要邮件】【普通邮件】；未明确提及则为 `[]`。
    - 邮件主题(titleList)：抽取邮件主题；最多20个邮件主题，超出后舍弃；未明确提及则为 `[]`。
    - 邮件数量(countList)：若用户在问询邮件数量，返回 `-1`；未提及数量则为 `[]`。
    - 邮件文件夹(mailBox)：包括“草稿箱”“已发送”“已删除”“垃圾箱”“收件箱”“境外邮件”“广告邮件”“电子发票”；未明确提及则为 `[]`。当 query 提及中国境外地域（如“美国/日本/欧洲/海外/国外”）且语义上用于筛选邮件时，提取为["境外邮件"]。
    - 邮件附件名称(attachmentList)：抽取邮件附件名称；未明确提及则为 `[]`。
    - 邮件标签(tagList)：仅在用户明确提及标签本身或明确的标签操作时提取，包括“VIP邮件”“稍后处理”“星标”“置顶”“订阅邮件”“系统通知”；未明确提及则为 `[]`。
      - 明确提及示例：“找星标邮件”“看VIP邮件”“订阅邮件有哪些”“系统通知邮件”“查找置顶邮件”。
      - 不提取示例：未提及“星标”“VIP”“订阅”等关键词时，`tagList` 必须为 `[]`。
    - 联系角色(contactRoleList)：包括“@”“主送”“抄送”“往来邮件”；未明确提及则为 `[]`。
3. 邮件写信参数抽取说明：
    - 邮件收件人(recipientList)：包括邮件【收件人】、【姓名】、【简称】、【昵称】、【外号】、【收件人邮箱】；如果文本中没有明确提到符合的收件人，则保留空数组 `[]`。
    - 邮件主题(titleList)：邮件主题，如果文本中没有明确提到邮件主题，则保留空数组 `[]`。
4. 搜邮件 与 邮件问答 的区分规则（按优先级执行）：
    - 默认路由到【邮件问答】；仅在满足“明确搜索指令”时才路由【搜邮件】。
    - 只有当 query 同时满足以下两点才使用【搜邮件】：
      1) 出现明确搜索动作词：“搜/搜索”“查/查询”“找/查找/检索”；
      2) 动作词直接作用于“邮件检索目标”（如邮件/发件人/主题/标签/附件/文件夹），而非问答、简报、是否存在类表达。
    - 以下场景一律使用【邮件问答】（即使包含“找/查”）：
      - 邮件问答/存在性询问：如“有什么/有哪些/有没有/有…吗/多少封/谁发了什么/我还没读哪些/哪些邮件”。
      - 邮件内容理解：如总结、解释、提炼、问邮件里说了什么。
      - AI简报相关：只要出现“简报”，包括“AI简报、给我个简报、找到/翻出/查收/能找到…简报”等，都使用【邮件问答】。
    - 明确搜索指令示例（走【搜邮件】）：
      - “帮我搜上周张三发来的未读邮件”
      - “查一下收件箱里主题含‘合同’的邮件”
      - “找 2024 年 3 月到 5 月的报销邮件”
    - 邮件问答示例（走【邮件问答】）：
      - “我有哪些重要邮件？”
      - “今天有什么邮件？”
      - “未读邮件有多少封？”
      - “给我个 AI 简报”
      - “找到今天的简报”
5. 邮件写信(040003)规则：
    - 当用户表达写邮件/发邮件的意图时，走【邮件写信】。
    - 典型触发词："写邮件"、"发送邮件"、"给...写邮件"、"帮我写一封邮件"、"写封信"、"发邮件给..."。
6. 邮件回信(040004)参数抽取说明：
    - 参数抽取规则与搜邮件参数抽取说明（规则2）保持一致，额外增加以下字段：
    - 邮件编号(mailNumberList)：用户通过序号/编号指定要回复的邮件，如"第一封"、"第3封"、"编号5"等；提取对应的编号数字，如[1]、[3]、[5]；未提及则为 `[]`。
    - 典型触发词："回复"、"给...回复邮件"、"回信"、"回复邮件"。
    - 工作流程：根据抽取的实体参数搜索邮件 → 用户选择邮件 → 基于单封邮件内容生成回复。
7. 邮件整理(040005)参数抽取说明：
    - 参数抽取规则与邮件回信(040004)保持一致，额外增加以下字段：
    - 邮件编号(mailNumberList)：用户通过序号/编号指定要操作的邮件，如"第一封"、"第3封"等；提取对应的编号数字，如[1]、[3]；未提及则为 `[]`。
    - 操作动作(actionList)：用户对邮件执行的操作类型，仅限["标记已读"、"删除"、"星标"、"打标签"、"移动文件夹"]；未提及则为 `[]`。
    - 动作关键词(actionKeywordList)：提取与操作动作相关的关键词，如标签名称、文件夹名称；未提及则为 `[]`。
    - 特殊处理：
        - "把xxx发的邮件标记为重要"：typeList 应为["普通邮件"]，不应提取为["重要邮件"]；actionList 根据具体操作判定。
        - 若用户提到的标签名不在预定义标签范围内，actionList 提取为["打标签"]，actionKeywordList 提取标签名，若标签名无法确定则为 `[]`。
        - 若用户提到的文件夹名不在预定义范围内，actionKeywordList 为 `[]`。
        - 若用户的操作动作不在预定义范围内，actionList 为 `[]`。
    - 典型触发词："删除"、"标记"、"移动"、"星标"、"打标签"、"整理邮件"、"批量处理"。
8. 邮件意图区分总规则：
    - 搜邮件(040001)：明确表达"搜/查/找某封邮件"的检索意图。
    - 邮件问答(040002)：对某封邮件内容进行提问、总结、解释。
    - 邮件写信(040003)：撰写邮件内容。
    - 邮件回信(040004)：对已有邮件进行回复。
    - 邮件整理(040005)：对邮件进行批量操作管理。
    - 多轮对话中，若上文已触发搜邮件/邮件问答，用户接着说"回复"或"删除"等操作，应根据当前query的主要意图匹配对应工具。

## Intent Contrast Rules

### 搜邮件 vs 邮件问答

- 明确使用搜、查、检索等动作定位邮件时选择“搜邮件”。
- 存在性询问、数量询问、内容理解、总结或简报请求选择“邮件问答”。

### 邮件写信 vs 邮件回信

- 创建新邮件选择“邮件写信”；回复已有邮件或搜索结果选择“邮件回信”。

### 邮件回信 vs 邮件整理

- 生成回复内容选择“邮件回信”；删除、标记、移动、加标签等管理动作选择“邮件整理”。

## Tools Schema

```json
{
  "搜邮件": {
    "code": "040001",
    "desc": "搜索邮件",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"昨天\"、\"上周\"、\"2025年3月\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的搜索实体关键词，如[\"合同\"、\"报销\"、\"周报\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀或格式，如[\"pdf\"、\"xlsx\"、\"eml\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "statusList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件状态，仅限[\"未读\"、\"已读\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "未读",
          "已读"
        ]
      },
      "senderList": {
        "type": "array",
        "required": false,
        "desc": "提取的发件人信息，如姓名/昵称/邮箱；模糊描述不提取，未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "recipientList": {
        "type": "array",
        "required": false,
        "desc": "提取的收件人信息，如姓名/昵称/邮箱；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "typeList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件类型，仅限[\"重要邮件\"、\"普通邮件\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "重要邮件",
          "普通邮件"
        ]
      },
      "titleList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件主题；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "countList": {
        "type": "array",
        "required": false,
        "desc": "若用户问询邮件数量则返回[-1]；未提及数量返回[]",
        "items": {
          "type": "integer"
        }
      },
      "mailBox": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件文件夹，如[\"收件箱\"、\"草稿箱\"、\"已发送\"、\"垃圾箱\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "草稿箱",
          "已发送",
          "已删除",
          "垃圾箱",
          "收件箱",
          "境外邮件",
          "广告邮件",
          "电子发票"
        ]
      },
      "attachmentList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件附件名称；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "tagList": {
        "type": "array",
        "required": false,
        "desc": "仅在用户明确提及邮件标签名或标签检索意图时提取，仅包括[\"VIP邮件\"、\"星标\"、\"稍后处理\"]；若不包含相应关键词则返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "VIP邮件",
          "星标",
          "稍后处理",
          "置顶",
          "订阅邮件",
          "系统通知"
        ]
      },
      "contactRoleList": {
        "type": "array",
        "required": false,
        "desc": "提取的联系角色，如[\"@\"、\"主送\"、\"抄送\"、\"往来邮件\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "@",
          "主送",
          "抄送",
          "往来邮件"
        ]
      }
    }
  },
  "邮件问答": {
    "code": "040002",
    "desc": "提供邮件相关的问答/邮件简报功能",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"昨天\"、\"上周\"、\"2025年3月\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的搜索实体关键词，如[\"合同\"、\"报销\"、\"周报\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀或格式，如[\"pdf\"、\"xlsx\"、\"eml\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "statusList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件状态，仅限[\"未读\"、\"已读\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "未读",
          "已读"
        ]
      },
      "senderList": {
        "type": "array",
        "required": false,
        "desc": "提取的发件人信息，如姓名/昵称/邮箱；模糊描述不提取，未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "recipientList": {
        "type": "array",
        "required": false,
        "desc": "提取的收件人信息，如姓名/昵称/邮箱；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "typeList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件类型，仅限[\"重要邮件\"、\"普通邮件\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "重要邮件",
          "普通邮件"
        ]
      },
      "titleList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件主题；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "countList": {
        "type": "array",
        "required": false,
        "desc": "若用户问询邮件数量则返回[-1]；未提及数量返回[]",
        "items": {
          "type": "integer"
        }
      },
      "mailBox": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件文件夹，如[\"收件箱\"、\"草稿箱\"、\"已发送\"、\"垃圾箱\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "草稿箱",
          "已发送",
          "已删除",
          "垃圾箱",
          "收件箱",
          "境外邮件",
          "广告邮件",
          "电子发票"
        ]
      },
      "attachmentList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件附件名称；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "tagList": {
        "type": "array",
        "required": false,
        "desc": "仅在用户明确提及邮件标签名或标签检索意图时提取，如[\"VIP邮件\"、\"星标\"、\"稍后处理\"]；若仅有普通语义词（如\"重要\"、\"通知\"、\"订阅\"）则返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "VIP邮件",
          "星标",
          "稍后处理",
          "置顶",
          "订阅邮件",
          "系统通知"
        ]
      },
      "contactRoleList": {
        "type": "array",
        "required": false,
        "desc": "提取的联系角色，如[\"@\"、\"主送\"、\"抄送\"、\"往来邮件\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "@",
          "主送",
          "抄送",
          "往来邮件"
        ]
      }
    }
  },
  "邮件写信": {
    "code": "040003",
    "desc": "撰写邮件。典型语句如\"写邮件\"、\"帮我写一封邮件\"、\"给张三写邮件\"、\"发送邮件给李四\"。该工具不抽取实体参数，由后续处理实体提取",
    "params": {
      "recipientList": {
        "type": "array",
        "required": false,
        "desc": "提取的收件人信息，如姓名/昵称/邮箱地址；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "titleList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件主题；未提及返回[]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "邮件回信": {
    "code": "040004",
    "desc": "回复邮件。典型语句如\"回复\"、\"给张三回复邮件\"、\"回信\"、\"回复第二封邮件\"。根据抽取的实体搜索邮件后，基于单封邮件内容生成回复",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"昨天\"、\"上周\"、\"2025年3月\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的搜索实体关键词，如[\"合同\"、\"报销\"、\"周报\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀或格式，如[\"pdf\"、\"xlsx\"、\"eml\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "statusList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件状态，仅限[\"未读\"、\"已读\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "未读",
          "已读"
        ]
      },
      "senderList": {
        "type": "array",
        "required": false,
        "desc": "提取的发件人信息，如姓名/昵称/邮箱；模糊描述不提取，未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "recipientList": {
        "type": "array",
        "required": false,
        "desc": "提取的收件人信息，如姓名/昵称/邮箱；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "typeList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件类型，仅限[\"重要邮件\"、\"普通邮件\"]；未提及则默认返回[\"普通邮件\"]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "重要邮件",
          "普通邮件"
        ]
      },
      "titleList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件主题；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "countList": {
        "type": "array",
        "required": false,
        "desc": "若用户问询邮件数量则返回[-1]；未提及数量返回[]",
        "items": {
          "type": "integer"
        }
      },
      "mailBox": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件文件夹，如[\"收件箱\"、\"草稿箱\"、\"已发送\"、\"垃圾箱\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "草稿箱",
          "已发送",
          "已删除",
          "垃圾箱",
          "收件箱",
          "境外邮件",
          "广告邮件",
          "电子发票"
        ]
      },
      "attachmentList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件附件名称；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "tagList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件标签，如[\"VIP邮件\"、\"星标\"、\"稍后处理\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "VIP邮件",
          "星标",
          "稍后处理",
          "置顶",
          "订阅邮件",
          "系统通知"
        ]
      },
      "contactRoleList": {
        "type": "array",
        "required": false,
        "desc": "提取的联系角色，如[\"@\"、\"主送\"、\"抄送\"、\"往来邮件\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "@",
          "主送",
          "抄送",
          "往来邮件"
        ]
      },
      "mailNumberList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件编号，用户通过序号指定邮件（如\"第一封\"提取为[1]、\"第3封\"提取为[3]、\"最后一封\"固定提取为[20]）；未提及返回[]",
        "items": {
          "type": "integer"
        }
      }
    }
  },
  "邮件整理": {
    "code": "040005",
    "desc": "批量管理邮件，支持标记已读、删除、星标、打标签、移动文件夹等操作。典型语句如\"删除未读邮件\"、\"把张三的邮件标记为已读\"、\"将这些邮件移到垃圾箱\"、\"给这封邮件打上重要标签\"、\"星标第二封邮件\"",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"昨天\"、\"上周\"、\"2025年3月\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的搜索实体关键词，如[\"合同\"、\"报销\"、\"周报\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀或格式，如[\"pdf\"、\"xlsx\"、\"eml\"]；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "statusList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件状态，仅限[\"未读\"、\"已读\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "未读",
          "已读"
        ]
      },
      "senderList": {
        "type": "array",
        "required": false,
        "desc": "提取的发件人信息，如姓名/昵称/邮箱；模糊描述不提取，未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "recipientList": {
        "type": "array",
        "required": false,
        "desc": "提取的收件人信息，如姓名/昵称/邮箱；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "typeList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件类型，仅限[\"重要邮件\"、\"普通邮件\"]；未提及则默认返回[\"普通邮件\"]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "重要邮件",
          "普通邮件"
        ]
      },
      "titleList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件主题；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "countList": {
        "type": "array",
        "required": false,
        "desc": "若用户问询邮件数量则返回[-1]；未提及数量返回[]",
        "items": {
          "type": "integer"
        }
      },
      "mailBox": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件文件夹，如[\"收件箱\"、\"草稿箱\"、\"已发送\"、\"垃圾箱\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "草稿箱",
          "已发送",
          "已删除",
          "垃圾箱",
          "收件箱",
          "境外邮件",
          "广告邮件",
          "电子发票"
        ]
      },
      "tagList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件标签，如[\"VIP邮件\"、\"星标\"、\"稍后处理\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "VIP邮件",
          "星标",
          "稍后处理",
          "置顶",
          "订阅邮件",
          "系统通知"
        ]
      },
      "contactRoleList": {
        "type": "array",
        "required": false,
        "desc": "提取的联系角色，如[\"@\"、\"主送\"、\"抄送\"、\"往来邮件\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "@",
          "主送",
          "抄送",
          "往来邮件"
        ]
      },
      "attachmentList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件附件名称；未提及返回[]",
        "items": {
          "type": "string"
        }
      },
      "mailNumberList": {
        "type": "array",
        "required": false,
        "desc": "提取的邮件编号，用户通过序号指定邮件（如\"第一封\"提取为[1]、\"第3封\"提取为[3]、\"最后一封\"固定提取为[20]）；未提及返回[]",
        "items": {
          "type": "integer"
        }
      },
      "actionList": {
        "type": "array",
        "required": false,
        "desc": "提取的操作动作，仅限[\"标记已读\"、\"删除\"、\"星标\"、\"打标签\"、\"移动文件夹\"]；未提及返回[]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "标记已读",
          "删除",
          "星标",
          "打标签",
          "移动文件夹"
        ]
      },
      "actionKeywordList": {
        "type": "array",
        "required": false,
        "desc": "提取的操作关键词，如标签名称、文件夹名称；未提及返回[]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "编辑邮件": {
    "code": "036020",
    "desc": "编辑邮件",
    "params": {}
  }
}
```

## Intent-Specific Rules

- 每个 Intent 的适用范围以 Tools Schema 的 `desc` 和上述对比规则为准。
- 只抽取用户当前输入或上下文中明确存在的参数，不猜测实体或真实资源句柄。
- 参数缺失不改变已经确定的 Intent；同 code Intent 必须根据名称语义区分。

## Positive Examples

- “查上周张三发的邮件” → 搜邮件
- “未读邮件有多少封” → 邮件问答

## Negative Examples

- “写一份普通通知” → 不属于邮件服务
- “搜索邮件附件对应的云盘文件” → 云盘搜索

## Execution Instructions

- 最终 Route 确定后，按照该 Intent 的参数 Schema 做类型、数组元素和枚举校验。
- 未明确提供的可选参数不阻塞路由；不得伪造文件、图片、邮件等业务句柄。
- 涉及删除、覆盖、外发或权限变更时，由执行阶段完成对象确认和风险确认。
