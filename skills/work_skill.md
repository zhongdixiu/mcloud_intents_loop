---
id: work_skill
name: 办公效率与创作
description: 承接 PPT、会议纪要、编程、润色、总结、工作规划、营销内容、PDF 和脑图等专用办公工具；不承接无对应工具的泛写作。
version: '1.0'
scope:
- PPT 和脑图生成
- 会议纪要
- AI 编程
- 文本润色与总结
- 工作与营销内容生成
- PDF 工具
out_of_scope:
- 无对应专用工具的泛写作
- 图片生成与处理
- 邮件收发操作
aliases:
- 办公工具
- AI 办公
status: active
---

# 办公效率与创作 Skill

## Skill Scope

本 Skill 负责：

- PPT 和脑图生成；
- 会议纪要；
- AI 编程；
- 文本润色与总结；
- 工作与营销内容生成；
- PDF 工具；

本 Skill 不负责：

- 无对应专用工具的泛写作；
- 图片生成与处理；
- 邮件收发操作；

## Intent Routing Principles

1. 根据用户当前主动作选择 Intent，参数缺失不影响 Route 判断。
2. 专用 Intent 优先于通用入口；同 code 的不同 Intent 仍按完整 Route Key 区分。
3. 当前 Skill 不支持请求时允许返回空候选，不强制选择相近 Intent。
4. 二级阶段保留现有参数输出兼容，但不得因参数不全降低正确 Route 的优先级。

现有业务规则：

1. 创作类型检查：用户提出通用创作请求（写作文、写信、写对联、翻译句子、数学题等），若无精确匹配的工具，本 Skill 返回空候选。
2. 如果用户的需求是编程任务，无论任务难易程度，均输出"AI编程"工具。
3. "AI生成会议纪要" vs "总结概括"：
    - "AI生成会议纪要"仅适用于会议内容，且仅针对会议内容进行总结。
    - 若总结需求不是针对会议，即使要求生成纪要，也是输出"总结概括"工具。
4. 广告相关规则：
    - 用户提出生成广告、广告创意、广告文案、广告方案等广告诉求时，使用“广告创意”工具。
    - 当“营销方案”和“广告创意”均可匹配时（如“策划一个广告方案”），优先使用“广告创意”工具。
    - 仅当用户明确提出目标用户群、目标市场/销售平台等营销规划诉求时，使用“营销方案”工具。
5. 所有工具的 params 字段均为非必选参数。
6. 参数抽取规则：仅提取用户输入中明确出现或可直接确定的参数；若输入未提供对应参数信息，则不要补全、不要猜测、不要生成该参数字段。
7. 若用户仅询问是否有对应工具时，无需抽取参数，不要生成参数字段
8. 所有工具均支持推送工具入口；当用户表达“搜一下xx工具”等查找工具入口诉求时，或询问有无xx工具等工具咨询类场景时，也可触发对应工具。
9. 严禁隐式补全参数：不得根据任务类型或常识推断参数值（如将“写一个演示文档”推断为 style="专业"）。仅当用户明确提及风格/语气等信息时，才可抽取 `style` 等对应字段。
10. 示例（以 AI生成PPT 为例）：
    - 用户输入：“写一个演示文档” -> 不得抽取 `style`、`keyword`
    - 用户输入：“写一个5页的专业风格的演示文档” -> 可抽取 `{ "page": "5", "style": "专业" }`

## Intent Contrast Rules

### AI生成会议纪要 vs 总结概括

- 会议内容的纪要整理选择“AI生成会议纪要”；非会议文本总结选择“总结概括”。

### 广告创意 vs 营销方案

- 广告文案、广告创意和广告方案选择“广告创意”；明确目标人群或目标市场规划时选择“营销方案”。

### 内容润色 vs 从零写作

- 只有提供待处理原文时选择“内容润色”；无原文的泛写作不由该 Intent 承接。

## Tools Schema

```json
{
  "AI生成PPT": {
    "code": "036001",
    "desc": "借助AI工具帮助用户生成PPT、制作PPT",
    "params": {
      "keyword": {
        "type": "string",
        "required": false,
        "desc": "对生成PPT的关键词"
      },
      "page": {
        "type": "integer",
        "required": false,
        "desc": "生成的PPT页数"
      },
      "style": {
        "type": "string",
        "required": false,
        "desc": "用户想生成的PPT风格，如专业、幽默、亲切、日常等"
      }
    }
  },
  "AI生成会议纪要": {
    "code": "036002",
    "desc": "借助AI工具对会议内容或文档整理会议纪要",
    "params": {
      "content": {
        "type": "string",
        "required": false,
        "desc": "会议主要内容的来源"
      }
    }
  },
  "AI编程": {
    "code": "036003",
    "desc": "当用户要求借助AI技术生成、修复或推荐编程代码、脚本、算法或项目时，以及生成SQL语句时，应识别为AI编程工具",
    "params": {
      "aiCoderFileName": {
        "type": "string",
        "required": false,
        "desc": "功能名称"
      },
      "codeLanguage": {
        "type": "string",
        "required": false,
        "desc": "代码语言，如Python、Java、C++等"
      }
    }
  },
  "图书快速阅读": {
    "code": "036007",
    "desc": "辅助用户快速高效阅读书籍文档，又叫文档阅读助手，可快速获取书籍文档的核心内容",
    "params": {}
  },
  "爆款文案": {
    "code": "036008",
    "desc": "AI生成文案功能。该工具仅适用于带有传播、种草、营销、吸引点击、平台发布、宣传转化等目的的文案生成。普通文章、短文、主题写作、朋友圈、编写标题、广告文案等场景不应使用此工具",
    "params": {
      "description": {
        "type": "string",
        "required": false,
        "desc": "对生成的文案的描述"
      },
      "topic": {
        "type": "string",
        "required": false,
        "desc": "文案的主题，如产品、公司、品牌、主旨、概念等"
      },
      "style": {
        "type": "string",
        "required": false,
        "desc": "生成的文案风格，如专业、幽默、亲切、日常等"
      }
    }
  },
  "创意标题": {
    "code": "036009",
    "desc": "AI生成创意标题。",
    "params": {
      "description": {
        "type": "string",
        "required": false,
        "desc": "对生成的标题的描述"
      },
      "content": {
        "type": "string",
        "required": false,
        "desc": "文章内容来源"
      },
      "oriTitle": {
        "type": "string",
        "required": false,
        "desc": "待修改的标题"
      }
    }
  },
  "内容润色": {
    "code": "036010",
    "desc": "对文本内容进行润色。该工具仅针对用户给出的文本内容进行润色优化，不支持没有原始文本从零开始生成写作内容，也不支持针对书籍进行润色，或编写读后感。",
    "params": {
      "content": {
        "type": "string",
        "required": false,
        "desc": "待润色的文本内容来源"
      }
    }
  },
  "总结概括": {
    "code": "036011",
    "desc": "对用户给出的文本内容进行总结概括。该工具仅适用于针对用户给出的文本内容进行总结。若用户需求是针对书籍进行摘要或概括，则不应使用该工具。",
    "params": {
      "content": {
        "type": "string",
        "required": false,
        "desc": "待总结的文本内容来源"
      }
    }
  },
  "写工作计划": {
    "code": "036012",
    "desc": "对文本提及的工作项进行写计划",
    "params": {
      "task": {
        "type": "string",
        "required": false,
        "desc": "工作的主要任务和活动"
      },
      "target": {
        "type": "string",
        "required": false,
        "desc": "工作目标"
      },
      "time": {
        "type": "string",
        "required": false,
        "desc": "时间，如一周、一个月、季度等"
      }
    }
  },
  "语法校对": {
    "code": "036013",
    "desc": "对文章进行语法纠正校对的功能",
    "params": {
      "content": {
        "type": "string",
        "required": false,
        "desc": "用户输入中待校对待的内容"
      }
    }
  },
  "职业规划": {
    "code": "036014",
    "desc": "根据行业、技能进行职业规划/晋升计划的功能",
    "params": {
      "industry": {
        "type": "string",
        "required": false,
        "desc": "用户输入中的行业信息"
      },
      "job": {
        "type": "string",
        "required": false,
        "desc": "用户输入中的岗位信息"
      },
      "skill": {
        "type": "string",
        "required": false,
        "desc": "用户输入中的技能信息"
      },
      "experience": {
        "type": "string",
        "required": false,
        "desc": "用户输入中的工作年限"
      }
    }
  },
  "广告创意": {
    "code": "036015",
    "desc": "根据商品信息及创意描述AI生成创意广告，包括广告文案编写、广告方案策划等。",
    "params": {
      "product": {
        "type": "string",
        "required": false,
        "desc": "商品信息"
      },
      "topic": {
        "type": "string",
        "required": false,
        "desc": "广告的主题"
      }
    }
  },
  "营销方案": {
    "code": "036016",
    "desc": "根据商品信息、目标用户群、目标市场进行营销方案生成",
    "params": {
      "product": {
        "type": "string",
        "required": false,
        "desc": "商品信息"
      },
      "targetAudience": {
        "type": "string",
        "required": false,
        "desc": "目标用户群，如青少年、成人、老年等"
      },
      "targetMarket": {
        "type": "string",
        "required": false,
        "desc": "目标市场或销售平台，如海外、国内、淘宝、京东等"
      }
    }
  },
  "商品好评": {
    "code": "036017",
    "desc": "对商品进行写好评的功能",
    "params": {
      "product": {
        "type": "string",
        "required": false,
        "desc": "商品信息"
      }
    }
  },
  "会议通知": {
    "code": "036019",
    "desc": "通过邮件等方式提醒用户进行会议。该工具仅适用于与“会议”直接相关的通知场景，若用户只是要求写一般通知或告示，则不允许选择该工具。",
    "params": {
      "titleList": {
        "type": "string",
        "required": false,
        "desc": "会议主题"
      },
      "timeList": {
        "type": "string",
        "required": false,
        "desc": "会议时间"
      },
      "placeList": {
        "type": "string",
        "required": false,
        "desc": "会议地点"
      },
      "recipientList": {
        "type": "string",
        "required": false,
        "desc": "参会人员"
      }
    }
  },
  "查看PPT": {
    "code": "036024",
    "desc": "通过对话调用工具预览/查看PPT",
    "params": {
      "content": {
        "type": "string",
        "required": false,
        "desc": "PPT内容"
      }
    }
  },
  "PDF工具": {
    "code": "021",
    "desc": "提供PDF文件编辑/转换/合并等处理功能，支持将PDF转为Word、Excel、PPT的工具",
    "params": {}
  },
  "生成脑图": {
    "code": "036035",
    "desc": "根据内容生成思维导图/脑图",
    "params": {
      "content": {
        "type": "string",
        "required": false,
        "desc": "待生成脑图的内容"
      }
    }
  }
}
```

## Intent-Specific Rules

- 每个 Intent 的适用范围以 Tools Schema 的 `desc` 和上述对比规则为准。
- 只抽取用户当前输入或上下文中明确存在的参数，不猜测实体或真实资源句柄。
- 参数缺失不改变已经确定的 Intent；同 code Intent 必须根据名称语义区分。

## Positive Examples

- “根据会议录音生成会议纪要” → AI生成会议纪要
- “写一个 Python 脚本” → AI编程

## Negative Examples

- “生成一张产品海报” → 图像与视觉工具
- “给张三发邮件” → 邮件服务

## Execution Instructions

- 最终 Route 确定后，按照该 Intent 的参数 Schema 做类型、数组元素和枚举校验。
- 未明确提供的可选参数不阻塞路由；不得伪造文件、图片、邮件等业务句柄。
- 涉及删除、覆盖、外发或权限变更时，由执行阶段完成对象确认和风险确认。
