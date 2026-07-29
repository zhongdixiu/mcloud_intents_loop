---
id: mcloud_search_skill
name: 云盘搜索
description: 承接云盘中图片、文档、视频、音频、文件夹及其他已有资源的查找与定位；不承接分类入口、内容生成处理或答案型咨询。
version: '1.0'
scope:
- 搜索云盘图片、文档、视频、音频和文件夹
- 综合资源搜索
- 搜索笔记、圈子、书籍、影视、试卷和知识库
out_of_scope:
- 打开文件分类入口
- 生成或编辑内容
- 普通知识问答
aliases:
- 资源搜索
- 云盘查找
status: active
---

# 云盘搜索 Skill

## Skill Scope

本 Skill 负责：

- 搜索云盘图片、文档、视频、音频和文件夹；
- 综合资源搜索；
- 搜索笔记、圈子、书籍、影视、试卷和知识库；

本 Skill 不负责：

- 打开文件分类入口；
- 生成或编辑内容；
- 普通知识问答；

## Intent Routing Principles

1. 根据用户当前主动作选择 Intent，参数缺失不影响 Route 判断。
2. 专用 Intent 优先于通用入口；同 code 的不同 Intent 仍按完整 Route Key 区分。
3. 当前 Skill 不支持请求时允许返回空候选，不强制选择相近 Intent。
4. 二级阶段保留现有参数输出兼容，但不得因参数不全降低正确 Route 的优先级。

现有业务规则：

1. 资源目标 vs 答案目标：
    - 资源目标：用户要搜索、查找、定位或获取可保存、可播放、可浏览的资源载体本身，应使用本技能。
    - 答案目标：用户要普通闲聊、开放问答、推荐清单、事实解释、时事资讯、政策行情、热点查询、内容总结或分析结论，不应使用本技能。
    - 判断重点是用户要“资源”还是“答案”，不是单看关键词。出现“推荐/介绍/有哪些/怎么看/讲讲/最新消息”等答案型表达时，通常不是资源搜索；出现“搜索/查找/帮我找/找一下 + 资源载体”时，通常是资源搜索。
2. 检索主动作 vs 非检索主动作：
    - 本技能只承接查找、搜索、定位、获取已有资源载体的请求。
    - 若用户主动作是生成、创作、编辑、处理、配文、识别、翻译、总结、问答、鉴伪、修复等，不属于云盘搜索；即使提到资源类型、来源状态或时间范围等限定条件，也不能把主动作改成搜索。
    - 历史中曾搜索到的资源语义只能作为后续任务的主体或素材来源，不代表本轮仍要继续搜索。
3. "搜图片" vs "找合照"：
    - 当用户明确要求搜索具体对象的合照时，使用"搜图片"工具。
    - 当且仅当用户输入为"找合照"或者寻找"找合照"功能入口时，才使用"找合照"工具。
4. 搜索对象检查：请认真阅读用户输入，准确理解用户搜索的对象类型，并根据以下规则输出对应的意图。
    **4.1 明确类型词**：
        - 图片/照片/截图 → 搜图片
        - 文档/Word/PDF/Excel/PPT/TXT → 搜文档
        - 视频 → 搜视频 
        - 综艺/电影/电视剧 → 搜影视
        - 短剧 → 搜短剧
        - 音频/音乐/录音/歌曲 → 搜音频
        - 文件夹/目录（明确要找“文件夹”本身）→ 搜文件夹
        - 笔记 → 搜笔记
        - 书籍/小说/小说资源 → 搜书籍
        - 漫画/动漫 → 搜综合
       **4.2 内容描述词**：
           - **文档属性词**：报表、合同、协议、简历、方案、试卷、题目、说明书、论文、课件、账单、发票、脚本、台本、病历、指南以及**其他以文字、表格、图片等文档形式呈现的对象**→ 搜文档
           - **书籍出版物词**：小说、书籍、图书、绘本、期刊、杂志、原著、电子书、教材、画册等 → 搜书籍
           - **影视属性词**：大片、节目、花絮、剧场、影集、纪录片、高清资源、种子、磁力链、在线观看、播放链接以及**其他以视频格式/播放源呈现的对象** → 搜影视
       **4.3 泛指词与特殊规则（018）**：
           - **泛指词**："文件"、"附件"、"材料"、"内容"、"作品"、"攻略"、"资源"、"素材"、"配置" → 搜综合
           - **漫画**："漫画"、"动漫" → 搜综合
           - **文件扩展名**：exe/zip/rar/apk/iso/log/dmg/rar 等**计算机文件与压缩包常见后缀名** → 搜综合
           - ⚠️**"文件"覆盖规则**：只要查询最终以"**文件**"作为核心词（如"搜索包含'合同'的文件"），结果统一为 搜综合。
           - **纯实体名**：单纯的人名、作品名、IP名（如"搜索琅琊榜"、"找一下周杰伦"），无明确类型词 → 搜综合。
5. 搜知识库 vs 其他搜索意图
    - 当用户搜索对象为**知识库中**的实体时，输出"搜知识库"。
    - 当用户搜索对象为**知识库相关**或者**名称包含“知识库”**时，输出其他搜索意图。
    - 示例：
        - "找知识库中的文档" -> "搜知识库"
        - "给我知识库相关的视频" -> "搜视频"
6. 参数提取约束（严格）
    - 仅提取用户query中**明确出现**的信息，禁止基于常识、习惯或语义联想补全参数。
    - `metadataList` 需保留“主题词”和“类型词”的拆分：当 query 同时包含对象描述和类型词时，分别提取，禁止把两者合并成一个元素。
    - 例如“搜索深圳市城市发展的报表”应提取 `metadataList`: ["深圳市城市发展", "报表"]，不能提取为 ["深圳市城市发展的报表"]。
    - `suffixList` 仅在query里出现了明确后缀/格式标记时才提取（如 `mp3`、`mp4`、`pdf`、`doc`、`zip`、`log`等）。
    - 未出现任何后缀/格式标记时，`suffixList` 必须为空或不输出，不能根据“歌曲/音频/文档/视频/书籍/试卷”等类型词推断出后缀。
    - 仅当query出现**后缀**时才可提取 `suffixList`：如 `pdf` / `.pdf` / `mp3` / `zip` / `xls` / `rar` / `epub` / `txt` / `log` / `dmg` / `apk` 等。
    - 若用户只说“音乐/歌曲/录音/文档/视频/电影/小说/课件/合同”等内容类型词，且没有出现任何后缀字面量，`suffixList` 必须为 `[]`（或不输出）。
    - 禁止把“文件类型词”当成“文件后缀”：例如“音频”“图片”“文档”“视频”“书籍”“试卷”都不能写入 `suffixList`。
    - `timeList` 遇到连续时间范围时必须合并为一个完整片段，不得拆分为多个时间点；如“今年9月至10月”应提取为["今年9月至10月"]，“2024年3月-5月”应提取为["2024年3月-5月"]。
    - 对于连续时间范围，禁止重复补全未出现的时间单位或前缀（如“今年年9月”这类结果为错误）。
    - `timeList`、`placeList`、`titleList`、`authorList`、`typeList` 同样遵循“只抽不补”，未提及则为空或不输出。

### 易错用例
| 用户query | 正确意图 | 判定要点 |
|---|---|---|
| 帮我找刘德华的作品 | 搜综合（018） | “作品”为泛指词，且无明确类型词 |
| 周杰伦的音乐和作品 | 搜综合（018） | 多类型混合（“音乐”+“作品”）走综合 |
| 查找我所有关于厨师长手把手教学的备份文件 | 搜综合（018） | 以“文件”为核心词，触发“文件覆盖规则” |
| 我想看看手环DIY教程视频的rar格式文件 | 搜综合（018） | 含“文件”核心词；`suffixList` 可提取 `rar` |
| 搜索厨师长手把手教学的内容 | 搜综合（018） | “内容”为泛指词 |
| 帮我找《新秦时明月(2021)》 | 搜综合（018） | 纯作品名，未显式指明类型 |
| 帮我找修仙狂徒 | 搜综合（018） | 纯实体名，未显式指明类型 |
| 帮我找到知识库相关的文件 | 搜综合（018） | “知识库相关”不走“搜知识库”；且“文件”触发综合 |
| 帮我找周杰伦的歌 | 搜音频（015） | 未出现后缀字面量，`suffixList` 必须为空 |
| 找一下合同文档 | 搜文档（013） | “合同/文档”是类型词，不能推出 `pdf/doc`，`suffixList` 为空 |
| 搜最近的电影资源 | 搜视频（014） | 未出现 `mp4/mkv` 等字面量，`suffixList` 为空 |
| 帮我找mp3格式的周杰伦歌曲 | 搜音频（015） | 出现明确后缀字面量 `mp3`，可提取到 `suffixList` |

## Intent Contrast Rules

### 分类搜索 vs 搜综合

- 用户明确图片、文档、视频、音频、文件夹等对象类型时选择相应分类搜索。
- 未指定资源类型或类型不在专用 Intent 中时选择“搜综合”。

### 搜索资源 vs 文件入口（跨 Skill）

- 用户要定位已有内容时选择搜索；仅打开分类浏览入口时进入文件管理。

### 搜知识库 vs 搜文档

- 明确限定知识库范围时选择“搜知识库”；否则按普通文档选择“搜文档”。

## Tools Schema

```json
{
  "搜图片": {
    "code": "012",
    "desc": "搜索图片",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"1月\"、\"上个月\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的内容/人物描述，如[\"猫\"、\"宝宝\"、\"广州塔\"]",
        "items": {
          "type": "string"
        }
      },
      "placeList": {
        "type": "array",
        "required": false,
        "desc": "提取的地点信息，如[\"北京\"、\"海边\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜文档": {
    "code": "013",
    "desc": "搜索文档",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"上周\"、\"最近\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的关键词/类型，如[\"简历\"、\"会议纪要\"、\"Excel表格\"]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀，如[\"pdf\"、\"doc\"、\"xls\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜视频": {
    "code": "014",
    "desc": "搜索视频",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"上一年\"、\"上个月\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的内容/人物描述，如[\"猫\"、\"宝宝\"]",
        "items": {
          "type": "string"
        }
      },
      "placeList": {
        "type": "array",
        "required": false,
        "desc": "提取的地点信息，如[\"北京\"、\"海边\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜音频": {
    "code": "015",
    "desc": "搜索音频",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"去年\"、\"上个月\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的关键词/歌手/格式，如[\"周杰伦\"、\"录音\"]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀，如[\"wav\"、\"mp3\"、\"flac\"、\"wma\"、\"midi\"], 仅在用户query中明确出现时才提取",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜文件夹": {
    "code": "016",
    "desc": "搜索文件夹",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"最近三天\"、\"上个月\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的内容描述，如[\"合同\"、\"资料\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜笔记": {
    "code": "017",
    "desc": "搜索笔记",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"近期\"、\"上周\"、\"2023年\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的关键词/内容，如[\"会议记录\"、\"购物清单\"、\"待办事项\"]",
        "items": {
          "type": "string"
        }
      },
      "titleList": {
        "type": "array",
        "required": false,
        "desc": "提取的标题，如[\"项目计划\"、\"周报\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜综合": {
    "code": "018",
    "desc": "非特定类型或泛资源的综合搜索",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"2022年\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的关键词，如[\"压缩包\"、\"张三\"、\"项目管理\"]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀/类型，如[\"zip\"、\"xmind\"、\"exe\"], 仅在用户query中明确出现时才提取",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜索圈子": {
    "code": "023",
    "desc": "搜索云盘圈子或圈子中的动态",
    "params": {
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的圈子名称/成员/关键词，如[\"家庭圈\"、\"滑雪群\"、\"小明\"、\"旅游\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜书籍": {
    "code": "013",
    "desc": "搜索书籍",
    "params": {
      "authorList": {
        "type": "array",
        "required": false,
        "desc": "提取的作者信息，如[\"刘慈欣\"、\"余华\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的书名，如[\"三体\"、\"百年孤独\"]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件格式，如[\"PDF\"、\"EPUB\"]",
        "items": {
          "type": "string"
        }
      },
      "typeList": {
        "type": "array",
        "required": false,
        "desc": "提取的类型/题材，如[\"科幻小说\"、\"技术文档\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜影视": {
    "code": "014",
    "desc": "搜索影视",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的年份/时间，如[\"2023年\"、\"最近一个月\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的片名/演员/类型，如[\"泰坦尼克号\"、\"成龙\"、\"科幻片\"]",
        "items": {
          "type": "string"
        }
      },
      "placeList": {
        "type": "array",
        "required": false,
        "desc": "地点信息（通常为空）",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜短剧": {
    "code": "014",
    "desc": "搜索短剧",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"2021年\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的内容/题材/角色，如[\"霸总\"、\"古装\"、\"逆袭\"、\"龙王\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜试卷": {
    "code": "013",
    "desc": "搜索试卷",
    "params": {
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"近3年\"、\"2023年\"]",
        "items": {
          "type": "string"
        }
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的年级/学科/类型，如[\"一年级\"、\"数学\"、\"期末试卷\"]",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "搜知识库": {
    "code": "038",
    "desc": "搜索知识库",
    "params": {
      "typeList": {
        "type": "array",
        "required": false,
        "desc": "搜索目标类型，可为[\"知识库\"]、[\"知识库文档\"]或二者并存。若为“知识库中的xx文档/方案”等，取[\"知识库文档\"]；若为“搜xx的知识库和文档”，取[\"知识库\", \"知识库文档\"]",
        "items": {
          "type": "string"
        },
        "allowed_values": [
          "知识库",
          "知识库文档"
        ]
      },
      "metadataList": {
        "type": "array",
        "required": false,
        "desc": "提取的关键词，如知识库名称[\"产品设计\"]、文档标题[\"会议记录\"]",
        "items": {
          "type": "string"
        }
      },
      "timeList": {
        "type": "array",
        "required": false,
        "desc": "提取的时间信息，如[\"最近\"、\"去年\"]",
        "items": {
          "type": "string"
        }
      },
      "suffixList": {
        "type": "array",
        "required": false,
        "desc": "提取的文件后缀，如[\"pdf\"、\"doc\"、\"xlsx\"], 仅在用户query中明确出现时才提取",
        "items": {
          "type": "string"
        }
      }
    }
  },
  "找合照": {
    "code": "036025",
    "desc": "找合照工具入口，无法执行具体搜索操作",
    "params": {}
  }
}
```

## Intent-Specific Rules

- 每个 Intent 的适用范围以 Tools Schema 的 `desc` 和上述对比规则为准。
- 只抽取用户当前输入或上下文中明确存在的参数，不猜测实体或真实资源句柄。
- 参数缺失不改变已经确定的 Intent；同 code Intent 必须根据名称语义区分。

## Positive Examples

- “找上个月拍的猫照片” → 搜图片
- “搜项目资料” → 搜综合

## Negative Examples

- “打开图片入口” → 文件管理
- “画一张猫的图片” → 图像与视觉工具

## Execution Instructions

- 最终 Route 确定后，按照该 Intent 的参数 Schema 做类型、数组元素和枚举校验。
- 未明确提供的可选参数不阻塞路由；不得伪造文件、图片、邮件等业务句柄。
- 涉及删除、覆盖、外发或权限变更时，由执行阶段完成对象确认和风险确认。
