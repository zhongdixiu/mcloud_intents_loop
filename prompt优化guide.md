# Prompt优化指南

**核心问题**: Loop准确率90.6% < No-Loop准确率93.0%  
**根本原因**: Evaluator过度拒绝，导致纠偏失败12条，纠正成功仅4条  
**解决方向**: 调整各节点的设计哲学，从"严格把关"改为"合理容忍"

---

## 优化总览

| 节点 | 原问题 | 优化方向 | 预期效果 |
|------|--------|--------|--------|
| **Contextualizer** | 补全不足 | 简化规则，添加模板 | resolved_query更充分 |
| **Router** | 无confidence | 添加confidence字段 | 支持多层决策 |
| **Intent** | 对params要求严 | 明确不评估完整性 | 减少clarify |
| **Evaluator** | **拒绝标准太严** | **改变哲学，宽松处理** | **提升accuracy** |

---

## 优化1: CONTEXTUALIZER（上下文归一）

### 原问题

当前Contextualizer的规则太复杂且互相冲突：
- 第18条说"颜色、时间、地点是限定条件"
- 第20条说"缺乏具体主题也应输出resolved"
- 导致对"图片格式"这样的查询，Contextualizer不知道应该补全什么

**表现**: 
```
用户查询: "图片格式"
No-Loop resolved_query: "搜索图片格式的文件夹" ✅
Loop resolved_query: "图片格式" ❌ (没有补全)
```

### 优化思路

1. **简化规则优先级**
   - current_user_query 总是事实基础
   - 但必须补全到"不依赖历史也能理解"的程度
   - ambiguous是可以接受的，但resolved_query必须完整

2. **添加常见省略补全模板**
   ```
   "格式" → "搜索XX格式的YY"
   "链接" → "找XX的播放链接"
   "文件" → "搜索YY相关的文件"
   ```

3. **明确补全的方向**
   - 对于搜索类history：继承搜索框架，添加新条件
   - 对于问答类history：继承问题框架，补全背景

### 代码改变

**Before**:
```python
# 18条规则，每条都很长，且互相有冲突
CONTEXTUALIZER_SYSTEM_PROMPT = """...
13. 生成、创作、编辑... vs 搜索/查找主动作... 
...
20. 普通问答、开放问答...
"""
```

**After**:
```python
CONTEXTUALIZER_SYSTEM_PROMPT = """
核心原则（按优先级）：
1. current_user_query 总是事实基础
2. 输出 status=resolved 总是正确的
3. resolved_query 必须补全到不依赖历史也能理解的程度

省略补全的常见模式（优先处理）：
- "格式" / "链接" / "文件" → 根据history推断动作
- "这个" / "它" → 代指历史中的具体对象
- 没有主动作的短语 → 从history继承或推断为搜索
"""
```

**预期改善**:
- resolved_query从"图片格式"改为"搜索图片格式的文件夹"
- Loop的后续节点基于更清晰的input

---

## 优化2: ROUTER（一级路由）

### 原问题

1. 缺乏confidence字段，无法支持多层决策
2. 没有明确的"何时返回普通对话"的标准
3. 对模糊输入没有明确的处理策略

### 优化思路

1. **添加confidence字段**
   - 让Loop可以根据confidence做分层处理
   - 高置信(>0.8)：直接返回，不需要Evaluator复查
   - 低置信(<0.5)：即使返回也加上warning

2. **明确confidence的含义**
   ```
   1.0-0.8: 非常明确
   0.8-0.6: 相当可能，虽然有歧义
   0.6-0.4: 模糊的选择，但仍是最优答案
   <0.4: 真的无法判断
   ```

3. **明确普通对话的条件**
   - 当resolved_query不涉及任何skill时 → code=000
   - 不是"无法匹配就返回普通对话"
   - 而是"纯对话/知识问答/闲聊"才返回普通对话

### 代码改变

**Before**:
```python
class SkillRouteDecision(BaseModel):
    status: Literal["route", "clarify", "no_match"]
    skill_id: str | None
    confidence: float  # 有但Router的prompt中没有使用
    reason: str
```

**After**:
```python
# SkillRouteDecision同上（数据模型不变）
# 但Router的system prompt中明确指导：

ROUTER_SYSTEM_PROMPT = """
对 confidence 的理解：
- 1.0-0.8: 非常明确，可以直接路由
- 0.8-0.6: 相当可能，虽然有歧义但最优选择清晰
- 0.6-0.4: 模糊的选择，但仍给出最优答案
- <0.4: 真的无法判断，需要用户澄清

关键：即使 resolved_query 模糊，也应该给出最可能的 skill（0.5-0.8 confidence）
而不是拒绝。
"""
```

**预期改善**:
- 对于"图片格式"这样的查询，Router会给出"搜索skill, confidence=0.7"
- 而不是"无法匹配，返回普通对话"

---

## 优化3: INTENT（二级意图识别）

### 原问题

1. 缺乏confidence字段
2. 对params的要求与设计不符
   - 规则中说"不评估params"
   - 但实际会因为params不完整而返回clarify
3. 对"缺乏具体context但明确intent"的情况处理不当

### 优化思路

1. **明确对params的态度**
   - 参数必须符合schema的**类型**（字符串/数组/对象）
   - 但**不要求完整**；缺失参数不是拒绝理由
   - 例：intent=搜音频，虽然缺乏format参数，也要返回matched

2. **添加confidence字段**
   - 区分"高置信matched"vs"低置信matched"
   - 支持Loop做不同处理

### 代码改变

**Before**:
```python
# Prompt中没有明确说明params的态度
INTENT_SYSTEM_PROMPT = """
出现问题但未解决的规则：
- 不得因为参数缺失而输出 reject
- params 只是辅助观测

# 但没有明确何时接受不完整的params
"""
```

**After**:
```python
INTENT_SYSTEM_PROMPT = """
对 params 的态度（重要）：
- 参数必须符合 schema 的类型（字符串/数组/对象等）
- 但不要求参数完整；缺失参数不是拒绝的理由
- 如果 intent 明确，即使某些可选参数缺失，仍应返回 matched

例："搜周杰伦的歌" 缺乏格式参数，但 intent=搜音频 很明确 → matched

关键：不要因为"参数不完整"而返回 clarify 或 no_match。
参数完整性是执行时的问题，不是意图识别的问题。
"""
```

**预期改善**:
- "再加上春节歌曲"即使缺乏format参数，也返回intent=搜音频 + matched
- 而不是clarify

---

## 优化4: EVALUATOR（最核心的改进）

### 原问题 🔴

这是Loop失败的主要原因。从测试数据看：

1. **过度拒绝** 
   - Loop的clarify率3.8% vs No-Loop的2.3%
   - 被错误拒绝到普通对话(code=0)的：4条
   - 被不必要澄清的：5条

2. **规则与执行不符**
   - 规则183-184说"不得因为参数缺失而clarify"
   - 但"再加上春节歌曲"就是被拒绝了

3. **接受条件不明确**
   - Prompt详细描述了reject的各种情况
   - 但对accept的条件很模糊
   - 导致模型倾向保守

4. **对模糊的容忍度太低**
   - "图片格式"虽然模糊，但在搜索context中是可以理解的
   - Evaluator拒绝了这个本来可以接受的答案

### 优化思路：改变哲学

**旧哲学**（严格把关）:
- 只接受"完美的答案"
- 对任何不确定都拒绝
- 这导致准确率下降2.3%

**新哲学**（合理容忍）:
- 接受"足够好的答案"
- 在有合理推断空间时倾向接受
- 仅在"真正无法判断"时才澄清

### 代码改变

**Before**:
```python
EVALUATOR_EXTRA_RULES = """
1. 评估目标只包含 skill/intent/code；params 只是辅助观测...
2. 对总结、润色、翻译...
# ... 很多细节规则
# 但最关键的"何时accept"没有明确说明
"""
```

**After**:
```python
EVALUATOR_SYSTEM_PROMPT = """
【1. verdict=accept】接受当前候选结果
   充要条件：
   - skill 与用户需求相符（即使有歧义，该skill也是最合理的）
   - intent 与用户动作相符（即使表达模糊，该intent也是最可能的）
   - 无明确的逻辑矛盾
   
   即使满足以下条件也应接受（不是拒绝理由）：
   - resolved_query 有歧义或模糊 ← 关键改进
   - params 不完整或缺失 ← 关键改进
   - resolved_query 基于history补全，不是用户直接说的
   - 用户的表达简洁或省略

【2. verdict=reject】拒绝当前候选结果
   必须满足以下至少一个条件：
   - 明确的语义矛盾：如用户问"删除吗"但返回"搜索结果"
   - 从对话中明确看出需求与候选skill/intent不符
   
   不是拒绝理由：
   - skill/intent 虽然可能，但不是最优 ← 关键改进
   - resolved_query 对当前候选有歧义 ← 关键改进
   - params 缺失 ← 关键改进
   - 表达模糊或基于历史补全 ← 关键改进

【3. verdict=unclear】无法判断
   仅在以下情况：
   - 多个skill都同样可能，无法选择
   - 用户的动作或对象完全模糊，无法推断
   
   不是unclear的理由：
   - 虽然模糊，但能做出合理推断 ← 关键改进
   - params 不完整
"""
```

**关键改进点对应的失败案例**:

1. "图片格式"
   - 原: resolved_query模糊 → reject
   - 新: resolved_query模糊但可推断 → accept
   - 结果: 从code=0改为code=16 ✅

2. "再加上春节歌曲"
   - 原: params不完整 → clarify
   - 新: intent明确，params不完整不是问题 → accept
   - 结果: 从clarify改为matched ✅

3. "找一下播放链接"
   - 原: 表达不完整 → clarify
   - 新: 在搜索context中能推断 → accept
   - 结果: 从clarify改为matched ✅

### Evaluator额外规则（完全重写）

```python
EVALUATOR_EXTRA_RULES = """
【1. Params 评估】
  - 不评估 params 的完整性
  - 不评估 params 的具体值是否真实存在
  - 只验证 params 的格式是否符合 schema
  
  结论：参数相关的问题不是 intent/skill 错误，不应 reject

【2. 模糊表达处理】
  - 即使 resolved_query 有歧义，如果能做出合理推断，就接受
  - "图片格式" 虽然模糊，但在搜索context中明确是在问文件格式 → accept
  - 不要因为"太模糊"就拒绝

【3. 降低 unclear 的频率】
  - unclear 应该很少见（<2%）
  - 大多数情况下，即使模糊也能给出最可能的答案
  - 仅在"真正无法推断"时才 unclear

【总结：Evaluator 的新哲学】
  旧：严格把关，只接受完美答案
  新：合理容忍，接受充分好的答案

  从实验数据看，旧哲学导致准确率下降 2.3%。
"""
```

---

## 预期改善

### 定量预期

| 指标 | 原方案 | 优化后 | 改善 |
|------|-------|--------|------|
| Loop准确率 | 90.6% | ~92-93% | +1.4-2.4% |
| Evaluator接受率 | ~60% | ~75-80% | +15-20% |
| 纠偏失败数 | 12条 | 3-5条 | -50-75% |
| Clarify率 | 3.8% | 2-2.5% | 接近No-Loop |

### 定性预期

1. **Loop真正起到纠错作用**
   - 纠偏失败大幅减少
   - 纠正成功的案例增加

2. **系统更具容错能力**
   - 对模糊表达的理解更好
   - 对省略表达的补全更完善

3. **用户体验改善**
   - Clarify问题减少
   - 直接给答案的比例增加

---

## 实施建议

### 第一步：Update Evaluator（最关键）

1. 复制优化后的`EVALUATOR_SYSTEM_PROMPT`
2. 调整Evaluator的system prompt中关于accept的部分
3. **特别注意**: 明确说明"模糊不是拒绝理由"、"params不完整不是拒绝理由"

### 第二步：Update Contextualizer

1. 简化规则，删除冗长的18-20条
2. 添加常见省略补全的模板
3. 让resolved_query更充分

### 第三步：Update Intent和Router

1. 在prompt中明确confidence的含义
2. 说明何时接受不完整的params/不明确的intent

### 第四步：验证

1. 在测试集上重新跑Loop
2. 预期Loop准确率应该接近或超过No-Loop
3. 对比失败案例，看是否有新问题

---

## 风险点与应对

### 风险1：过度宽松

**风险**: 如果接受标准太宽，可能接受真正错误的答案  
**应对**: 保留confidence字段，对低置信的accept做后处理（如返回带warning的partial_match）

### 风险2：某些skill需要严格评估

**风险**: 删除/修改/支付类操作需要严格的Evaluator  
**应对**: 可以为不同skill配置不同的Evaluator严格度（后续优化）

### 风险3：模型行为改变

**风险**: 改变prompt后模型的行为可能不符合预期  
**应对**: 充分测试，对比优化前后的结果

---

## 总结

这次优化的核心是**改变Evaluator的哲学**：

- 从"严格把关，确保完美"
- 改为"合理容忍，接受足够好"

这与系统的整体目标一致：提升用户的意图识别准确率。

实验数据有力证明了当前的"严格"策略是错误的（准确率反而下降）。新策略应该通过保留了Loop的纠错能力，同时避免过度拒绝，来实现更好的效果。