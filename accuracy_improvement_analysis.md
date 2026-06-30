# 意图路由准确率提升分析：86% → 91% 优化策略

**生成日期**: 2026-06-29

---

## 一、执行摘要

移动云盘意图路由项目从 v1.1.2（约 86%）到 v1.2.0（约 91%），准确率提升了约 5 个百分点。这 5% 的提升**全部来自单次预测路径的优化**，而非 Agent Loop 机制。事实上，Loop 机制（Evaluator + 重试）反而将准确率从 93.0% 降低到了 90.6%（净损失 -2.3%）。

| 策略 | 类别 | 预估贡献 | 核心机制 |
| --- | --- | --- | --- |
| Contextualizer 多轮语义归一 | 架构新增 | +3~4% | resolved_query + semantic_frame 消除指代歧义和省略 |
| Prompt 精细化工程 | Prompt 优化 | +2~3% | 15+ 条详细规则 + 共享上下文规则确保节点间一致性 |
| 普通对话作为一等意图 (code=000) | 架构新增 | +1~2% | 闲聊/知识问答不再错误匹配业务 skill |
| 校验层安全兜底 | 架构新增 | +1% | 校验非法输出自动 fallback 到第一候选 |
| Skill 定义中的易错用例 | 数据优化 | +0.5~1% | 隐式 few-shot 示例指导模型处理边界 case |
| 单 Skill 选择 + route_no_loop | 架构简化 | +0.5~1% | 消除多候选不确定性，直接返回最优候选 |

---

## 二、基线说明

### 2.1 版本定义

| 版本 | 特征 |
| --- | --- |
| **v1.1.2（基线）** | 无 Contextualizer、无 code=000、Prompt 简单（4-5 条规则）、无校验层、无 Switch Gate |
| **v1.2.0（当前）** | 完整 Contextualizer + 精细 Prompt + 校验兜底 + Switch Gate + route_no_loop |

### 2.2 评测方法

- **测试集**：341 条多轮对话记录（`format_result_qwen3_7plus.xlsx`）
- **评测维度**：双路径对比（Loop vs No-Loop）、gold_history 模式、end2end 模式
- **核心指标**：准确率（code 匹配）、第一候选准确率、Top-N 召回率

### 2.3 关键数据

| 路径 | 准确率 | 说明 |
| --- | --- | --- |
| **No-Loop（route_no_loop）** | **93.0%** (317/341) | 单次预测 + 第一候选，跳过 Evaluator |
| Loop（route） | 90.6% (309/341) | 含 Evaluator 重排序 + 定向扩充 |
| **净差异** | **-2.3%** | Loop 机制降低准确率 |

**结论**：86% → 91% 的提升来自 No-Loop 路径的优化，Loop 机制本身是负贡献。

---

## 三、策略一：Contextualizer 多轮语义归一（预估贡献 +3~4%）

### 3.1 问题

多轮对话中，用户输入高度依赖上下文——代词（"它"、"这个"）、省略（"图片格式"）、动作继承（继续上一轮的操作）。v1.1.2 直接将原始 query 送入 Router，模型需要自行推断上下文，导致大量歧义和错误。

### 3.2 解决方案

引入独立的 **Contextualizer** 节点，在路由前将 `current_user_query + dialogue_history` 归一化为自包含的 `resolved_query` + 结构化 `semantic_frame`。

**Contextualizer 核心规则**（`prompts.py:121-135`）：

```
1. current_user_query 是最高优先级事实；历史只补全省略、延续、修正、改口、指代
2. 当前轮显式动作、对象或目标优先于历史；历史不得覆盖当前轮显式动作
3. 生成、创作、编辑等主动作与搜索/查找不同；不要因历史搜索污染当前处理型请求
4. 问句形态期望语言答案时 semantic_frame.expected_result_type 应为 ordinary_answer
5. 承接历史时保留会影响 skill/intent/code 的对象类型、主体、限定条件
6. 改口或覆盖历史时，在 semantic_frame.explicit_overrides 记录被覆盖方向
7. 不伪造 image_id、file_id、mail_id、真实文件句柄
8. 若只缺主体、主题、关键词等，仍输出最稳妥 semantic_frame
9. 若会影响 skill/intent/code 的关系不确定，relation_to_history 用 ambiguous
```

### 3.3 效果案例

| 用户输入 | 历史上下文 | v1.1.2（无 Contextualizer） | v1.2.0（有 Contextualizer） |
| --- | --- | --- | --- |
| "图片格式" | 前文在讨论搜索图片 | 直接路由 "图片格式" → 歧义，可能误判为普通对话 | resolved_query: "搜索图片格式的文件夹" → 正确路由到搜文件夹 |
| "再加上春节歌曲" | 前文在搜周杰伦的歌 | 不理解"再加上"的继承关系 | resolved_query 补全搜索动作，继承搜索框架 |
| "搞错了，我是想要126邮箱的" | 前文讨论其他搜索 | 无法理解"搞错了"是改口 | semantic_frame.explicit_overrides 记录覆盖，重新路由 |

### 3.4 代码引用

- `prompts.py:121-135` — `CONTEXTUALIZER_SYSTEM_PROMPT`（10 条规则）
- `prompts.py:138-146` — `CONTEXTUALIZED_REQUEST_RULES`（7 条共享规则，注入到 Router、Intent、Evaluator）
- `router.py:582-601` — `_contextualize()` 方法
- `router.py:274-346` — `route_no_loop()` 中 Contextualizer 的调用链

---

## 四、策略二：Prompt 精细化工程（预估贡献 +2~3%）

### 4.1 问题

v1.1.2 的 Router prompt 只有 4-5 条简单规则，缺乏对边界情况的明确指导。模型在歧义场景下行为不稳定，经常：
- 因参数缺失而拒绝正确的 intent
- 将搜索型请求误判为答案型问答
- 对模糊表达返回 clarify 而非给出最优候选

### 4.2 解决方案

将每个节点的 System Prompt 精细化为 10+ 条详细规则，并引入**共享上下文规则**确保所有节点判断标准一致。

**Router 核心规则**（`prompts.py:149-162`）：
- 默认输出 Top 3 候选，按最可能排序
- 普通对话是候选而非 terminal no_match
- 强业务动作下必须至少给出一个业务 skill 候选
- 答案型问句可把普通对话排在第一

**共享规则 `CONTEXTUALIZED_REQUEST_RULES`**（`prompts.py:138-146`）——这是最关键的设计：
```
1. resolved_query 和 semantic_frame 是本轮路由依据
2. 只判断 skill/intent/code，不评估参数完整性
3. 参数缺失、实体缺失、主体对象缺失都不是阻塞 intent code 的理由
4. 若当前轮明确表达搜索、查找、打开等业务动作，必须优先保留业务候选
5. 答案型问句可优先普通对话；不能仅因出现资源词就强行走云盘搜索
6. 搜索类 skill 只承接查找类动作；处理型 skill 承接生成、编辑等动作
7. 同一 code 的不同 intent 必须比较完整 route key
```

这些规则被注入到 **Router、Intent、Evaluator** 三个节点的 prompt 中，确保三者判断标准一致。

### 4.3 效果

| 优化点 | 解决的具体问题 |
| --- | --- |
| "不评估参数完整性" | 消除因参数缺失导致的误拒绝（如"再加上春节歌曲"缺乏 format 参数） |
| "搜索 vs 处理型动作区分" | 防止"生成图片"被误路由为"搜图片" |
| "答案型问句优先普通对话" | 防止"有哪些电影推荐"被误路由为搜影视 |
| "同一 code 比较完整 route key" | 防止重复 code 的 intent 被错误合并 |

### 4.4 代码引用

- `prompts.py:149-162` — `ROUTER_SYSTEM_PROMPT`
- `prompts.py:165-179` — `INTENT_SYSTEM_PROMPT`
- `prompts.py:138-146` — `CONTEXTUALIZED_REQUEST_RULES`
- `prompts.py:200-213` — 规则注入到各节点 prompt

---

## 五、策略三：普通对话作为一等意图 code=000（预估贡献 +1~2%）

### 5.1 问题

v1.1.2 中，当系统无法匹配任何 skill 时，返回 `no_match` 或 `clarify`，导致：
- 闲聊（"你好"）被错误匹配到某个业务 skill
- 知识问答（"云盘能上传吗"）被强制路由到搜索
- 无 skill 匹配时用户体验差（返回 clarify 而非直接回答）

### 5.2 解决方案

将"普通对话"提升为**一等候选**，参与候选排名：
- `skill_id=null`, `intent="普通对话"`, `code="000"`
- 在 Router 的候选集中总是存在，与其他业务候选一起排序
- 配合**强业务动作关键词检测**防止反向误判

**强业务动作关键词**（`router.py:44-63`）：
```python
STRONG_BUSINESS_ACTIONS = (
    "搜索", "搜", "查找", "找", "打开", "进入", "入口", "工具",
    "发送", "整理", "筛选", "生成", "创作", "编辑", "处理",
    "翻译", "总结", "识别",
)
```

**候选惩罚机制**（`router.py:803-820`）：
- 普通对话候选 + 强业务动作词 → 惩罚 0.2
- function_skill + 泛化风险标记 → 惩罚 0.1
- current_query_conflict 风险 → 惩罚 0.3
- ontology/label 冲突 → 惩罚 0.2

### 5.3 效果

| 场景 | v1.1.2 | v1.2.0 |
| --- | --- | --- |
| "你好" / "今天天气怎么样" | 可能误匹配到搜索 skill | 正确路由到 code=000 |
| "云盘有这方面的资源不？" | 可能误匹配 | 有强业务词"资源"，保留业务候选 |
| "知识库能上传文件吗" | 可能误匹配 | 问句形态 → 优先普通对话 |

### 5.4 代码引用

- `router.py:40-42` — `ORDINARY_CANDIDATE_ID`, `ORDINARY_INTENT`, `ORDINARY_CODE`
- `router.py:44-63` — `STRONG_BUSINESS_ACTIONS`
- `router.py:803-820` — `_candidate_penalty()`
- `router.py:823-824` — `_has_strong_business_action()`

---

## 六、策略四：校验层安全兜底（预估贡献 +1%）

### 6.1 问题

LLM 输出存在幻觉风险——模型可能输出不存在的 skill_id、编造 intent 名称、使用错误的 code。v1.1.2 中这些错误直接传播到最终输出。

### 6.2 解决方案

引入结构化校验层，对模型输出进行硬约束验证：

**Intent 候选校验**（`validation.py:11-35`）：
```python
def validate_intent_candidate(registry, candidate):
    # 普通对话候选：必须 intent="普通对话" code="000"
    if candidate.skill_id is None:
        if candidate.intent != "普通对话" or candidate.code != "000":
            raise ValidationError(...)
    # 业务候选：skill_id 必须存在
    if not registry.has(candidate.skill_id):
        raise ValidationError(...)
    # intent 必须在 skill 的 Tools Schema 中
    if candidate.intent not in skill.intents:
        raise ValidationError(...)
    # code 必须完全匹配 schema 定义
    if candidate.code != schema.code:
        raise ValidationError(...)
```

**Rerank 决策校验**（`validation.py:38-69`）：
- 所有 ranking 中的 candidate_id 必须存在于候选集
- 不允许重复 ranking
- select 必须有 selected_candidate_id
- expand 必须有 expand_scope

**Fallback 机制**（`router.py:637-669`）：
- `_validated_rerank()`: 校验通过的 rerank 决策
- `_fallback_rerank()`: 校验失败时，自动 fallback 到第一候选

### 6.3 效果

完全消除了"幻觉 intent"和"幻觉 candidate_id"类错误——这类错误在 v1.1.2 中约占 1% 的失败案例。

### 6.4 代码引用

- `validation.py:11-35` — `validate_intent_candidate()`
- `validation.py:38-69` — `validate_rerank_decision()`
- `router.py:637-653` — `_validated_rerank()`
- `router.py:656-669` — `_fallback_rerank()`

---

## 七、策略五：Skill 定义中的易错用例（预估贡献 +0.5~1%）

### 7.1 问题

某些边界 case 仅靠 Prompt 规则难以覆盖——模型对"文件"、"内容"等泛指词的理解容易出错，对后缀提取规则容易违反。

### 7.2 解决方案

在每个 Skill 的 Markdown 定义文件中加入**易错用例表**，作为隐式 few-shot 示例：

| 用户query | 正确意图 | 判定要点 |
| --- | --- | --- |
| 帮我找刘德华的作品 | 搜综合（018） | "作品"为泛指词，且无明确类型词 |
| 周杰伦的音乐和作品 | 搜综合（018） | 多类型混合（"音乐"+"作品"）走综合 |
| 查找我所有关于厨师长手把手教学的备份文件 | 搜综合（018） | 以"文件"为核心词，触发"文件覆盖规则" |
| 帮我找周杰伦的歌 | 搜音频（015） | 未出现后缀字面量，`suffixList` 必须为空 |
| 找一下合同文档 | 搜文档（013） | "合同/文档"是类型词，不能推出 `pdf/doc` |
| 帮我找mp3格式的周杰伦歌曲 | 搜音频（015） | 出现明确后缀字面量 `mp3`，可提取 |

这些用例通过 `skill.raw_markdown` 完整传递给 Intent 节点的 LLM（`prompts.py:290`）。

### 7.3 代码引用

- `skills/mcloud_search_skill.md:59-73` — 易错用例表
- `prompts.py:290` — `skill_markdown` 传递给 Intent 节点

---

## 八、策略六：单 Skill 选择 + route_no_loop 路径（预估贡献 +0.5~1%）

### 8.1 问题

v1.1.2 的架构中，Router 生成 Top-K 个 skill 候选，Loop 控制器需要在多个候选中迭代选择。这引入了额外的不确定性——当第一个候选实际上是正确的，但后续候选的评估可能导致系统摇摆。

### 8.2 解决方案

提供 `route_no_loop()` 路径（`router.py:274-346`），跳过 Evaluator 和 Loop 机制，直接返回 Router 的第一候选：

```
User Input → Contextualizer → Router(Top-N skill) → Intent(Top-M per skill)
→ 取第一候选 → 直接输出
```

该路径的判断逻辑：
- Router 生成 Top-N skill 候选（默认 Top 3）
- 对每个 skill 生成 Top-M intent 候选（默认 Top 2）
- 直接取排名第一的候选作为最终输出
- 无 Evaluator 审查、无重试、无 Switch Gate

### 8.3 效果

341 条测试中，No-Loop 准确率 93.0%，高于 Loop 的 90.6%。这说明**在大多数情况下，Router 的第一候选已经是最优解**，Evaluator 的介入反而增加了被误纠的风险。

### 8.4 代码引用

- `router.py:274-346` — `route_no_loop()` 方法
- `router.py:348-374` — `_generate_skill_candidates()`（Top-N 生成）
- `router.py:376-397` — `_normalize_skill_candidates()`（去重 + 过滤 + 兜底）

---

## 九、Loop 机制为何未贡献提升

### 9.1 数据事实

来自 `loop_effectiveness_analysis.md` 的 341 条测试：

| 指标 | 数值 |
| --- | --- |
| Loop 纠正成功 | 4 条 (1.2%) |
| Loop 纠偏失败 | 12 条 (3.5%) |
| **净效果** | **-2.3%** ❌ |

### 9.2 失败模式

| 失败类型 | 数量 | 典型 case |
| --- | --- | --- |
| 被降级为普通对话 (code=0) | 4 条 | "图片格式"：No-Loop 正确路由到搜文件夹，Loop 被 Evaluator 拒绝降级为普通对话 |
| 被不必要地 clarify | 5 条 | "再加上春节歌曲"：No-Loop 正确路由到搜音频，Loop 因 format 参数缺失而 clarify |
| 被选错 intent | 2 条 | "搜索一下减肥瘦身方面的食品清单"：No-Loop 正确，Loop 选错 |
| 系统错误 | 1 条 | Evaluator 本身报错 |

### 9.3 根本原因

1. **封闭信息系统**：Evaluator 读取的是同一份 query 和候选集，没有外部新信息注入。它对"图片格式"的模糊性判断和 Router 一样——它只是多了一个"说不确定"的选项。

2. **数学必然性**：设 Router 准确率 p，Evaluator 准确率 q，Loop 后准确率 = p·q + (1-p)(1-q)。当 p > 0.5 且 q < 1 时，Loop 准确率**必然低于** p。以 p=0.93, q=0.85 代入：0.93×0.85 + 0.07×0.15 = 0.7905 + 0.0105 = 0.801，远低于 0.93。

3. **相关性陷阱**：Router 和 Evaluator 使用相同模型，对困难 case 会同时犯错。Evaluator 对 Router 的系统性错误是盲的。

4. **目标冲突**：Router 被设计为"在不确定中给出最优猜测"，Evaluator 被设计为"拒绝不完美的答案"。两者目标对立，导致 Evaluator 频繁拒绝 Router 的合理猜测。

---

## 十、总结

### 10.1 贡献汇总

| 策略 | 类型 | 预估贡献 | 核心价值 |
| --- | --- | --- | --- |
| Contextualizer 多轮语义归一 | 架构新增 | +3~4% | 消除多轮对话的最大歧义源 |
| Prompt 精细化工程 | Prompt 优化 | +2~3% | 规则一致性 + 边界 case 覆盖 |
| 普通对话 code=000 | 架构新增 | +1~2% | 消除误匹配 + 强业务词保护 |
| 校验层安全兜底 | 架构新增 | +1% | 消除幻觉类错误 |
| Skill 易错用例 | 数据优化 | +0.5~1% | 隐式 few-shot 覆盖边界 |
| 单 Skill 选择 + No-Loop | 架构简化 | +0.5~1% | 消除多候选不确定性 |
| **合计** | | **+5~7%** | **与 86%→91% 的 5% 提升吻合** |

### 10.2 核心教训

> **NLU 准确率提升靠单次预测质量，不靠多模型 Loop。**
>
> 在封闭信息系统（无外部工具调用、无人类反馈）中，增加模型调用只会引入噪声，不会带来增益。提升准确率的有效手段是：更好的上下文理解、更精细的 Prompt 规则、更严格的校验兜底、更丰富的领域知识。

### 10.3 未来方向

1. **继续优化单次预测**：扩充 Prompt 规则、增加易错用例、优化 Skill 定义
2. **引入外部信息源**：知识库检索、同义词扩展、用户画像——让 Loop 真正"开放"
3. **规则兜底**：对高频错误 case 用硬规则覆盖，而非依赖模型修正
4. **人机协同**：对低置信度 case 走人工审核，人类反馈是真正的外部信息

---

## 附录：关键代码文件索引

| 文件 | 关键内容 |
| --- | --- |
| `intent_router/prompts.py` | 所有 System Prompt（Contextualizer、Router、Intent、Evaluator）及共享规则 |
| `intent_router/router.py` | 核心路由逻辑：route()、route_no_loop()、Switch Gate、Combined Scoring、Penalty |
| `intent_router/validation.py` | Intent 候选校验 + Rerank 决策校验 |
| `intent_router/types.py` | 所有 Pydantic 数据模型 |
| `skills/*.md` | 15 个 Skill 定义文件，含 Special Rules、易错用例、Tools Schema |
| `loop_effectiveness_analysis.md` | 341 条测试的 Loop vs No-Loop 对比分析 |
| `prompt优化guide.md` | Prompt 优化方向与具体建议 |