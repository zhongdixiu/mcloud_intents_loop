# Skill 规范化要求

> 适用项目：`mcloud_intents_loop`  
> 适用架构：两级 LLM 意图决策  
> 文档目标：规范当前 `Skill.md` 的内容结构、解析方式和分阶段加载策略，在保留“一级选择 Skill 大类、二级加载 Skill 规则选择子意图”的设计前提下，提高路由准确性、可维护性和扩展性。

---

## 1. 设计定位

当前项目采用两级意图体系：

- **一级意图：Skill**
  - 一个 `Skill.md` 对应一个业务大类；
  - 一级 Skill Router 从全部 Skill 中召回 Top-N 大类候选；
  - 一级只需要读取轻量级 Skill 摘要，不加载所有 Skill 的完整规则。

- **二级意图：Tools Schema 中定义的 Intent**
  - 每个 Skill 内包含多个二级子意图；
  - 一级选中 Skill 后，二级 Intent Router 加载该 Skill 的路由规则和 Tools Schema；
  - 二级根据 Skill 内部的子意图边界、对比规则和示例，生成 Top-M Intent 候选。

规范化必须坚持：

1. `Skill.md` 是一级业务域的定义载体；
2. `Tools Schema` 是该业务域内二级 Intent 的机器定义；
3. 二级 Intent 选择规则必须与 Tools Schema 一起加载；
4. 一级路由信息与二级路由信息应逻辑分层，但当前不要求拆成多个物理文件；
5. 执行说明不得干扰一级和二级意图识别。

---

## 2. 优化目标

Skill 规范化主要解决：

- 不同 Skill 的文档结构不一致；
- 一级 Skill 描述过于宽泛，边界不清晰；
- 二级 Intent 只有名称和简短描述，缺少区分规则；
- 相似 Intent 容易混淆；
- Tools Schema、路由规则和执行说明混杂；
- 二级路由直接加载完整原始 Markdown，容易引入无关内容；
- 参数约束主要依赖 Prompt，缺少机器校验；
- 新增 Skill 后难以自动检测结构错误；
- Skill 内容膨胀后，Prompt 长度和模型判断稳定性下降。

规范化后的加载目标：

```text
一级路由：
只读取 Skill 摘要和业务边界

二级路由：
只读取当前 Skill 的二级路由上下文
包括 Tools Schema、选择规则、对比规则和示例

最终路由确定后：
再读取参数和执行相关说明
```

---

## 3. 推荐文件组织方式

### 3.1 当前阶段：保留单文件 Skill.md

当前推荐继续采用：

```text
skills/
├── file_skill.md
├── mail_skill.md
├── function_skill.md
└── ...
```

每个文件内部使用统一章节结构，由 Loader 按章节解析。

优点：

- 不改变当前 Skill 接入方式；
- 不增加文件管理复杂度；
- 保留二级 LLM 对完整业务规则的理解能力；
- 可以渐进改造现有 Skill；
- 兼容当前 `skills/*.md` 发现机制。

### 3.2 后续可选：目录化拆分

当单个 Skill 出现以下情况时，可拆为目录结构：

- 文件过长，路由 Prompt Token 明显增加；
- 执行说明远多于路由说明；
- API、权限和异常处理频繁变化；
- 路由规则和执行逻辑由不同团队维护；
- 同一个 Skill 包含大量子意图和复杂示例。

推荐结构：

```text
skills/
└── file_skill/
    ├── SKILL.md
    ├── ROUTING.md
    └── EXECUTION.md
```

| 文件 | 内容 | 加载时机 |
|---|---|---|
| `SKILL.md` | Frontmatter、一级业务范围、引用关系 | 系统启动、一级路由 |
| `ROUTING.md` | Tools Schema、二级选择规则、对比规则、正反例 | 一级召回后、二级路由 |
| `EXECUTION.md` | 参数转换、接口调用、权限、确认和异常处理 | 最终 Route 确定后 |

拆分时，**Tools Schema 与二级路由规则必须位于同一二级路由上下文中**，不能只保留简短 Intent 清单。

---

## 4. Skill.md 标准结构

推荐所有 Skill 使用以下结构：

````markdown
---
id: file_skill
name: 文件管理
description: >
  处理云盘文件、文档、图片、视频和音频的浏览、管理、
  上传、恢复、收藏及私密存储入口。
version: "1.0"
scope:
  - 文件浏览
  - 文件管理
  - 文件上传
  - 回收站
  - 文件收藏
out_of_scope:
  - 根据内容搜索云盘资源
  - AI 图片生成与编辑
  - 普通文件知识问答
---

# 文件管理 Skill

## Skill Scope

## Intent Routing Principles

## Intent Contrast Rules

## Tools Schema

## Intent-Specific Rules

## Positive Examples

## Negative Examples

## Execution Instructions
````

内容用途：

- Frontmatter 和一级范围说明供一级 Skill Router 使用；
- Intent Routing、Intent Contrast、Tools Schema 和示例供二级 Intent Router 使用；
- Execution Instructions 仅在最终 Route 确定后使用。

---

## 5. Frontmatter 规范

### 5.1 必填字段

```yaml
id: file_skill
name: 文件管理
description: >
  处理云盘文件、文档、图片、视频和音频的浏览、管理、
  上传、恢复、收藏及私密存储入口。
version: "1.0"
```

| 字段 | 要求 |
|---|---|
| `id` | 全局唯一、稳定，不因展示名称变化而修改 |
| `name` | 面向业务和模型的一级意图名称 |
| `description` | 描述 Skill 承接的核心请求类型，不应只是产品宣传语 |
| `version` | Skill 路由定义版本，用于缓存、回滚和评测对比 |

### 5.2 推荐字段

```yaml
scope:
  - 文件浏览
  - 文件管理
  - 文件上传
out_of_scope:
  - 云盘内容搜索
  - AI 图片处理
aliases:
  - 文件工具
  - 文件管理入口
owner: mcloud-team
status: active
```

说明：

- `scope` 描述明确承接的业务边界；
- `out_of_scope` 描述容易误召回但不属于该 Skill 的场景；
- `aliases` 只补充产品名称和常见叫法，不堆砌关键词；
- `status` 可使用 `active`、`deprecated`、`disabled`；
- `owner` 便于维护和问题归属。

### 5.3 一级描述要求

一级描述应回答：

1. 这个 Skill 主要解决什么问题；
2. 用户通常会提出什么动作；
3. 主要涉及什么业务对象；
4. 与相邻 Skill 的边界在哪里。

不推荐：

```yaml
description: 提供文件相关能力
```

推荐：

```yaml
description: >
  承接云盘内文件、文档、图片、视频和音频的浏览、管理、
  上传、恢复、收藏及私密存储入口请求；不承接按内容搜索资源
  或 AI 图片生成与编辑。
```

---

## 6. Skill Scope 规范

`Skill Scope` 用于详细解释一级业务域，应包含：

- 主要承接动作；
- 主要承接对象；
- 典型业务结果；
- 与相邻 Skill 的边界；
- 不支持的请求。

示例：

```markdown
## Skill Scope

本 Skill 负责云盘内已有文件和文件入口的管理，包括：

- 浏览文件、文档、图片、视频和音频；
- 上传已有本地文件；
- 现场拍照并上传；
- 查看回收站和恢复已删除文件；
- 打开收藏、保险箱和卡包等文件相关入口。

本 Skill 不负责：

- 根据关键词搜索云盘中的资源内容；
- 生成或编辑图片；
- 回答文件格式、文件知识等普通咨询问题。
```

一级 Router 使用 Frontmatter 的精简范围；二级 Router 可同时读取本章节作为业务域背景。

---

## 7. Intent Routing Principles 规范

该章节描述当前 Skill 内所有二级 Intent 共用的选择原则，应包含：

- 专用 Intent 与通用 Intent 的优先级；
- 用户动作如何影响 Intent 选择；
- 参数缺失是否影响 Intent 判断；
- 同 code 不同 Intent 如何处理；
- 二级候选是否允许为空；
- 何时保留多个候选。

示例：

```markdown
## Intent Routing Principles

1. 根据用户主动作选择 Intent，不因参数缺失而降低正确 Intent 的优先级。
2. 专用 Intent 优先于通用入口 Intent。
3. 已有文件上传选择“文件上传”；现场拍摄后上传选择“拍照上传”。
4. 浏览图片选择“图片”；清理重复、模糊或无用图片选择“图片清理”。
5. 同一 code 下的不同 Intent 仍然是不同 Route，必须按 Intent 语义区分。
6. 当前 Skill 不支持用户请求时，允许输出空候选。
7. 二级阶段只判断 Route，不要求完成所有业务参数。
```

---

## 8. Intent Contrast Rules 规范

该章节专门说明容易混淆的 Intent 对。

推荐格式：

```markdown
## Intent Contrast Rules

### 文件上传 vs 拍照上传

选择“文件上传”：

- 用户上传手机或本地已经存在的文件；
- 用户表达“上传、导入、同步、保存到云盘”。

选择“拍照上传”：

- 用户明确表达“拍照、拍一张、现场拍摄”；
- 拍摄与上传属于同一个请求。

边界示例：

- “把手机里的照片传进去” → 文件上传
- “拍一张身份证照片上传” → 拍照上传

### 图片 vs 图片清理

选择“图片”：

- 打开、查看、浏览已有图片。

选择“图片清理”：

- 清理重复、模糊、相似或无用图片。
```

规范要求：

1. 对所有高混淆 Intent 对建立对比规则；
2. 对比必须包含正向选择条件和排除条件；
3. 尽量使用业务语义，不依赖单个关键词；
4. 对跨 Skill 易混淆场景，可说明应由一级 Router 选择其他 Skill；
5. 不把所有系统级通用规则复制到每个 Skill。

---

## 9. Tools Schema 规范

### 9.1 基本格式

Tools Schema 必须是可解析的 JSON 对象：

```json
{
  "文件上传": {
    "code": "021",
    "desc": "上传手机或本地已经存在的文件至云盘",
    "params": {
      "file_type": {
        "type": "string",
        "required": false,
        "desc": "用户明确提到的文件类型",
        "allowed_values": ["图片", "视频", "文档", "音频", "其他"]
      }
    }
  }
}
```

### 9.2 Intent 定义要求

每个 Intent 必须包含：

| 字段 | 要求 |
|---|---|
| Intent 名称 | Skill 内唯一，语义稳定 |
| `code` | 字符串类型，不使用数字类型 |
| `desc` | 描述适用场景和核心动作 |
| `params` | 参数定义对象，无参数时使用 `{}` |

### 9.3 Route Key

系统唯一语义路由使用：

```text
(skill_id, intent, code)
```

禁止只按 `code` 判断两个 Intent 是否相同。

### 9.4 参数定义要求

推荐结构：

```json
{
  "keyword": {
    "type": "string",
    "required": false,
    "desc": "用户明确表达的搜索关键词",
    "allowed_values": []
  }
}
```

参数字段建议包含：

- `type`：`string`、`integer`、`number`、`boolean`、`array`、`object`；
- `required`：执行阶段是否必需；
- `desc`：参数业务含义；
- `allowed_values`：枚举约束；
- `items`：数组元素类型；
- `default`：可选默认值；
- `normalization`：可选归一化说明。

不得只通过自然语言中的“可选值包括……”让 Loader 猜测枚举值。

### 9.5 Schema 校验要求

Skill 加载时必须检查：

- JSON 是否可解析；
- Intent 名称是否重复；
- code 是否存在；
- params 是否为对象；
- 参数类型是否合法；
- `allowed_values` 是否与类型匹配；
- Intent-Specific Rules 是否引用了不存在的 Intent；
- 废弃 Intent 是否有版本或状态标记。

---

## 10. Intent-Specific Rules 规范

每个复杂 Intent 应有独立规则说明。

```markdown
## Intent-Specific Rules

### 文件上传

适用条件：

- 用户希望把已有本地文件保存到云盘；
- 文件可以是图片、视频、文档、音频或其他格式。

排除条件：

- 用户明确表示现场拍照，应选择“拍照上传”；
- 用户只是查看图片，应选择“图片”；
- 用户根据内容查找已有文件，应进入搜索类 Skill。

参数说明：

- Intent 判断不依赖文件真实句柄是否存在；
- `file_type` 只抽取用户明确表达的信息。

### 回收站

适用条件：

- 打开或查看回收站；
- 恢复误删文件；
- 管理已删除文件。

排除条件：

- 删除当前文件不是回收站入口请求；
- 永久删除操作应由执行阶段进一步确认。
```

---

## 11. 正反例规范

### 11.1 Positive Examples

```markdown
## Positive Examples

- “把手机里的照片传到云盘” → 文件上传
- “拍一张身份证上传” → 拍照上传
- “打开回收站” → 回收站
- “恢复昨天误删的合同” → 回收站
```

### 11.2 Negative Examples

```markdown
## Negative Examples

- “帮我找昨天的合同” → 不属于文件管理，应进入搜索类 Skill
- “生成一张海报” → 不属于文件管理，应进入图片生成或编辑 Skill
- “PDF 是什么格式” → 普通对话
```

要求：

- 示例覆盖常见表达和长尾表达；
- 示例用于解释规则，不应成为唯一判断依据；
- 不堆砌大量近似重复语句；
- 每次发现稳定混淆案例后，应补充到规则或示例中。

---

## 12. Execution Instructions 规范

执行说明仅在最终 Route 确定后使用，可包含：

- 参数补全和归一化；
- 权限要求；
- 是否需要用户确认；
- API 或工具调用方式；
- 错误处理；
- 返回结构；
- 幂等和重试要求。

执行说明不得改变已经确定的 Intent。

```markdown
## Execution Instructions

### 回收站

- 恢复文件前，如存在多个匹配对象，应先获取候选列表；
- 永久删除属于高风险操作，必须二次确认；
- 缺少真实 file_id 时，不得伪造句柄；
- 执行失败时返回可理解的错误信息，不重新修改 Intent。
```

---

## 13. 分阶段加载规范

### 13.1 系统启动阶段

系统启动时：

1. 扫描 `skills/*.md` 和 `skills/*/SKILL.md`；
2. 解析 Frontmatter；
3. 解析 Tools Schema；
4. 校验 Skill 结构；
5. 构造内存中的 `SkillDefinition`；
6. 构造一级使用的 `SkillCard`；
7. 建立 Skill 版本和缓存。

推荐数据结构：

```python
class SkillDefinition:
    id: str
    name: str
    description: str
    version: str
    scope: list[str]
    out_of_scope: list[str]

    skill_scope: str
    routing_principles: str
    contrast_rules: str
    intents: dict[str, IntentSchema]
    intent_specific_rules: str
    positive_examples: str
    negative_examples: str
    execution_instructions: str

    raw_markdown: str
```

### 13.2 一级 Skill Router 加载

一级 Router 只接收：

```python
class SkillCard:
    id: str
    name: str
    description: str
    scope: list[str]
    out_of_scope: list[str]
```

一级不得接收：

- 完整 Tools Schema；
- 详细参数；
- API 调用说明；
- 大量二级正反例；
- 执行错误码和权限规则。

### 13.3 二级 Intent Router 加载

一级选中 Skill 后，二级 Router 加载：

```text
Skill 基本信息
+ Skill Scope
+ Intent Routing Principles
+ Intent Contrast Rules
+ Tools Schema
+ Intent-Specific Rules
+ Positive/Negative Examples
```

二级不加载与意图判断无关的长篇执行说明。

二级输出只包含：

```text
candidate_id
skill_id
intent
code
confidence
matched_evidence
risk_flags
reason
```

不在本阶段完成最终参数抽取。

### 13.4 参数抽取和执行阶段

最终 Route 确定后：

1. 获取选中 Intent 的参数 Schema；
2. 加载该 Skill 的执行说明；
3. 针对唯一 Route 抽取参数；
4. 本地校验参数类型、枚举和必填约束；
5. 决定执行、澄清或拒绝；
6. 调用实际 Skill。

---

## 14. 缓存和热更新要求

### 14.1 缓存

建议缓存 Key：

```text
skill_id + version + file_hash
```

缓存内容：

- SkillCard；
- Tools Schema；
- 二级路由上下文；
- 执行上下文。

### 14.2 热更新

Skill 文件变化时：

1. 重新解析目标 Skill；
2. 完成结构校验；
3. 校验通过后原子替换内存版本；
4. 校验失败时保留旧版本；
5. 记录版本、修改时间和错误原因。

### 14.3 版本兼容

修改以下内容时应提升版本：

- Intent 名称；
- code；
- 参数 Schema；
- 二级选择规则；
- 一级业务范围。

不得直接删除线上仍可能被历史会话引用的 Intent；应先标记 deprecated，并提供迁移策略。

---

## 15. Skill 质量检查清单

### 一级定义

- [ ] `id` 全局唯一；
- [ ] `description` 清晰说明业务范围；
- [ ] `scope` 与 `out_of_scope` 完整；
- [ ] 与相邻 Skill 的边界明确。

### 二级定义

- [ ] Tools Schema 可解析；
- [ ] 每个 Intent 有明确 `desc`；
- [ ] 高混淆 Intent 有 Contrast Rules；
- [ ] 同 code Intent 仍按完整 Route Key 区分；
- [ ] 当前 Skill 不支持请求时允许输出空候选。

### 参数定义

- [ ] 参数类型明确；
- [ ] 枚举值结构化定义；
- [ ] 必填与可选含义清晰；
- [ ] 参数缺失不影响二级 Route 判断；
- [ ] 最终参数由独立阶段抽取和校验。

### 示例与执行

- [ ] 有典型正例；
- [ ] 有跨 Skill 反例；
- [ ] 执行说明不污染路由规则；
- [ ] 高风险操作有确认要求。

---

## 16. 推荐实施顺序

### 第一阶段：统一文档结构

- 为现有 Skill 补充规范 Frontmatter；
- 增加 `Skill Scope`；
- 增加 `Intent Routing Principles`；
- 增加 `Intent Contrast Rules`；
- 统一 Tools Schema 参数格式。

### 第二阶段：改造 Loader

- 按章节解析 Skill.md；
- 构造一级 SkillCard；
- 构造二级 IntentRoutingContext；
- 构造执行上下文；
- 增加结构校验和错误提示。

### 第三阶段：改造路由 Prompt

- 一级只输入 SkillCard；
- 二级只输入路由相关章节；
- 参数抽取移至最终 Route 之后；
- 执行阶段按需加载执行说明。

### 第四阶段：建立评测和治理

- 按 Skill 统计一级召回率；
- 按 Intent 统计二级召回率；
- 建立高混淆 Intent 对清单；
- 将稳定错误样本沉淀为规则和示例；
- 对 Skill 版本变更进行回归评测。

---

## 17. 最终原则

Skill 规范化不是把 Skill 简化为关键词表，也不是将二级规则拆散到多个无法共同加载的文件中。

最终保持：

```text
一级：
通过轻量 SkillCard 选择业务大类

二级：
加载选中 Skill 的完整路由上下文，
结合 Tools Schema、规则和示例选择子意图

最终：
针对唯一 Route 抽取参数并执行
```

> 保留一个 Skill 对应一个一级意图大类、Tools Schema 对应二级子意图的设计。优先规范 Skill.md 内部结构并实现分章节加载；只有在文档明显膨胀后，才将路由信息和执行信息拆成独立文件。二级子意图的 Tools Schema、选择规则、对比规则和示例必须始终作为一个完整路由上下文加载。
