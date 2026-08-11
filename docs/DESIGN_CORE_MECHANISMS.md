# 核心机制设计：核查清单 · 未核实标记 · 风险评分卡

> Stage 1/2 编码前的地基设计。这两套机制是耦合的——**数据完整度直接约束风险评级**，必须一起定。
>
> 上游：`docs/MIGRATION_PLAN.md`（Stage 2.2 / 2.3 / 2.6）

---

## 零、设计出发点

普通做法是给模型加一句"不要编造"，然后祈祷。这在尽调场景不成立，因为：

**尽调的本质不是"收集到什么写什么"，而是"有一张必查清单，逐项给出结论"。**

真实信贷尽调有固定的必查项：股权结构、实际控制人、涉诉记录、对外担保、财务指标……**查不到本身就是结论**，而且往往是比"查到了"更重要的结论。一份写着"未发现涉诉记录"的报告，如果实际是"没查到司法数据源"，那是骗人；如果实际是"查了三个数据源都没有记录"，那才是尽调。

所以设计从"事实收集"翻转成**"清单驱动的逐项核查"**：

```
传统：搜索 → 提取事实 → 写报告          （缺失是静默的）
本项目：生成必查清单 → 逐项核查 → 每项都有明确状态 → 写报告
                                        （缺失是显式的、可计数的、影响评级的）
```

这一个翻转同时解决四个问题：
1. **反幻觉**：模型不能"跳过"某项，因为清单是闭集，每项都要交代
2. **可评测**：清单是有限集合，逐项比对 ground truth 即可算准确率（Stage 5.1 的前提）
3. **业务真实**：这就是真实尽调的工作方式
4. **风控正确**：缺失数据能反向约束评级，而不是被无视

---

## 一、核查清单（FieldCheck）机制

### 1.1 状态定义

```python
FieldStatus = Literal[
    "verified",       # 已核实：取到值且有可溯源来源
    "unverified",     # 未核实：尝试过但未取到（必须记录尝试了哪些源、为什么失败）
    "conflicting",    # 存在冲突：多源数据不一致（尽调里这往往是重大风险信号）
    "not_applicable", # 不适用：该字段对此类主体无意义（如个体工商户无股权结构）
]
```

`conflicting` 是刻意加的。真实尽调中"工商登记注册资本 5000 万 / 财报实收资本 800 万"这类矛盾是**核心风险线索**，而朴素系统会随便挑一个值然后自信地写进报告。把冲突建模为一等状态，才能让它进入风险评分。

### ⚠️ v0.2 实施时补充的关键区分：「查了但无记录」≠「未查询」

实现时发现原设计漏了一种情况，且这种遗漏会直接误导授信决策：

| 实际情况 | 正确状态 | 业务含义 |
|---|---|---|
| 查询了司法数据源，返回空 | `verified`，值为"经查询，无相关记录" | **正面结论**，可支持授信 |
| 根本没查司法数据源 | `unverified` | 信息缺口，必须补查 |

朴素实现会把两者都当成"没有数据"，于是"无失信记录"这个有利结论被误算成缺口，
或者更危险地——反过来把缺口当成"无记录"。

因此数据源必须**显式声明覆盖范围**（`coverage.queried` 列出实际查询了哪些项），
而不是靠"档案里有没有这个字段"来推断。这也贴合真实数据源 API 的语义：
查询返回空结果集，与查询未发生，是两回事。

### 1.2 数据结构

新增到 `deep_research_v2/state.py`：

```python
class FieldCheck(TypedDict):
    field_id: str            # 稳定标识，如 "shareholders" / "judicial_litigation"
    field_name: str          # 展示名，如 "股东结构"
    category: str            # 所属维度：basic|equity|financial|judicial|relation|opinion
    required: bool           # 是否必查项（必查项未核实会触发降级）
    status: FieldStatus
    value: Any               # verified 时的值；其它状态为 None
    sources: List[str]       # fact_id 列表，指向 facts 里的取证记录
    attempted_sources: List[str]   # 尝试过的数据源适配器名，如 ["business_registry","judicial"]
    failure_reason: str      # unverified 时必填：为什么没取到
    conflict_detail: List[Dict[str, Any]]  # conflicting 时填：[{source, value}]
    checked_at: str          # ISO 时间
```

`ResearchState` 新增两个字段：

```python
    field_checks: List[FieldCheck]      # 核查清单（Architect 生成骨架，Scout 填充）
    completeness: Dict[str, Any]        # 完整度统计（DataAnalyst 计算，见 2.4）
```

> 注意：`ResearchState` 是 `TypedDict` 且会整体写入 `research_checkpoints.state_json`，所以这里用 TypedDict 而非 dataclass，与现有 `facts`/`data_points` 保持一致（原项目的 dataclass 只是"理想类型"，主流程实际存字典——见 `DATA_STRUCTURES.md` §10.6）。

### 1.3 必查项清单（v1 草案）

由 Architect 根据主体类型 + 授信额度裁剪生成。基础全集：

| category | field_id | 字段 | 必查 | 主要数据源 |
|---|---|---|---|---|
| basic | `registration` | 工商登记基本信息 | ✅ | business_registry |
| basic | `business_scope` | 经营范围 | ✅ | business_registry |
| basic | `operating_status` | 登记状态（存续/吊销/注销） | ✅ | business_registry |
| equity | `shareholders` | 股东结构与持股比例 | ✅ | business_registry |
| equity | `actual_controller` | 实际控制人 | ✅ | business_registry |
| equity | `external_investment` | 对外投资 | ➖ | business_registry |
| financial | `revenue` | 营业收入 | ✅ | 上传财报 / financial_records |
| financial | `net_profit` | 净利润 | ✅ | 同上 |
| financial | `debt_ratio` | 资产负债率 | ✅ | 同上 |
| financial | `cash_flow` | 经营性现金流 | ➖ | 同上 |
| judicial | `litigation` | 涉诉记录 | ✅ | judicial |
| judicial | `enforcement` | 被执行记录 | ✅ | judicial |
| judicial | `dishonesty` | 失信记录 | ✅ | judicial |
| judicial | `equity_freeze` | 股权冻结 | ➖ | judicial |
| relation | `guarantee` | 对外担保 | ✅ | business_registry + 财报附注 |
| relation | `guarantee_circle` | 担保圈 | ✅ | 图谱推导 |
| relation | `related_party` | 关联方交易 | ➖ | 财报附注 |
| operation | `bidding_record` | 中标记录（经营能力佐证） | ➖ | bidding |
| opinion | `negative_news` | 负面舆情 | ✅ | public_opinion + web |
| opinion | `regulatory_penalty` | 监管处罚 | ✅ | public_opinion |

**15 项必查、5 项选查。** 这个规模刚好：够撑起一份像样的报告，又不至于让评测集难以标注。

> 实现见 `backend/app/config/dd_checklist.py`。清单用**代码常量**定义而非 LLM 生成——
> 该结构要被计数、被评测、与 ground truth 比对，引入模型变异性会让核实率失去意义。

### 1.4 五层流转

```
Architect  ── 生成 field_checks 骨架（全部 status="unverified"，按主体类型裁剪）
              ↓
Scout      ── 逐项调用数据源适配器取证
              取到 → status="verified" + value + sources[fact_id]
              取不到 → status 保持 "unverified" + failure_reason + attempted_sources
              多源不一致 → status="conflicting" + conflict_detail
              ⛔ 严禁用推断/常识填充，严禁跳过
              ↓
DataAnalyst ─ 计算 completeness + 风险评分（见第二部分）
              ↓
Writer     ── 渲染报告，对每一项：
              verified     → 正常撰写，句末标注来源
              unverified   → 固定模板：「未核实（尝试来源：X；原因：Y）」
              conflicting  → 固定模板：「数据存在冲突：源A=值1，源B=值2，需人工核实」
              ⛔ 禁止推断、禁止省略、禁止用"暂无"替代"未核实"
              ↓
Critic     ── 交叉校验（见 1.5）
              ↓
前端       ── 核实率进度条 + 未核实清单 + 冲突项高亮
```

### 1.5 Critic 的强制校验（反幻觉的最后一道闸）

`CriticFeedback.issue_type` 新增三类：

```python
issue_type: Literal[
    # 原有
    "missing_source", "logic_error", "bias", "hallucination", "outdated", "incomplete",
    # 新增
    "unverified_as_fact",          # 把 unverified 字段当作事实断言
    "conflict_silently_resolved",  # 把 conflicting 字段单方面采信而未披露冲突
    "unsupported_risk_conclusion", # 风险结论无 field_checks 证据支撑
]
```

`unverified_as_fact` 的检测方式（Critic prompt 的核心指令）：

> 对 `field_checks` 中每一个 `status != "verified"` 的字段，在报告正文中检索是否出现了针对该字段的**实质性断言**。
> 例：`litigation` 状态为 `unverified`，而报告写"该企业无重大诉讼"——这是 critical 级别问题，必须打回。
> 正确写法只能是"涉诉记录未核实（尝试来源：judicial；原因：数据源无返回）"。

**注意反向陷阱**：`unverified` 不等于"无记录"，也不等于"有记录"。报告不得向任何一个方向倾斜。这条要写死在 Writer 和 Critic 两边的 prompt 里。

---

## 一·五、消融实验（v2，已修正实验设计缺陷）

> 2026-08-11 重做。触发问题：换用更强模型后误报大幅下降，
> 是否意味着此前的架构工作被模型能力溢出？
>
> **v1 版本的两个结论已撤回**（实验设计有缺陷），下方为修正后的数据。

### v1 的两个设计缺陷（已修）

1. **`--ablate scanner` 并未真正关闭扫描器。** 它只绕过了结果合并，
   而 `_review_content()` 自身仍调用 `scan_report()` 并把结论写进提示词。
   消融组的 LLM 依然看得到扫描结果，故当时算出的"扫描器 +6.7 个百分点"无效。
   现已四处同时关闭：执行、提示词注入、结果合并、类型过滤。
2. **误报口径过窄。** 此前只统计 `unverified_as_fact` /
   `conflict_silently_resolved` / `unsupported_risk_conclusion` 三类；
   模型若把同样的错误意见标为 `hallucination`、`logic_error`，
   或返回字符串形态的 issue，均不计入，导致误报被系统性低估。
   现改为**任何 critical/major 级问题都算误报**。

### 修正后数据（28 例 × 3 次，deepseek-v4-flash，温度 0）

| 配置 | 逐次检出率 | 漏检率 | 逐次无误报率 | 误报率 | 检出稳定性 |
|---|---|---|---|---|---|
| **A 完整架构** | **100%** | **0%** | 94.4% | 5.6% | 10/10 零方差 |
| B 真正关闭扫描器 | 96.7% | 3.3% | 92.5% | 7.5% | 9/10（1 例不稳定） |

> C 组（无清单）与 D 组（弱模型）的旧数据采用的是窄口径，
> 与上表不可比，需按新口径重跑后再引用。

### 扫描器的真实贡献

**漏检率 3.3% → 0%，且检出侧方差归零。**

数值上不大，但性质关键：在尽调场景中，"3.3% 的『把未核实写成无记录』会漏过去"
与"一次都不会漏"是两种产品。**确定性组件的意义不在于提升平均分，
而在于消除尾部风险。**

其余价值仍是工程属性：零 token、毫秒级、可进 CI、LLM 不可用时照常工作。

### 指标口径（务必按此表述）

| 说法 | 含义 |
|---|---|
| 逐次检出率 100% | 30 次运行全部检出目标违规 |
| 漏检率 0% | 检出率的补集 |
| 逐次无误报率 94.4% | 54 次对照运行中 51 次未产生 critical/major 告警 |
| **误报率 5.6%** | 无误报率的补集。**此前误称"误报率 98.1%"，实为无误报率且口径更窄** |

### 尚未解决的方法论限制

- **开发集过拟合**：这 28 例已反复用于提示词调优、架构修改与模型选型。
  现有数据只支持趋势性结论，**不足以证明"架构与模型完全正交"**（该结论已撤回）。
  需新增未参与任何调优的独立留出集。
- C/D 组需按新口径重跑才能纳入同一张表。

### 附带发现：结构化输入还在锚定输出形状

C 组首次运行崩溃：`AttributeError: 'str' object has no attribute 'get'`。
去掉结构化清单后，模型开始返回 `"issues": ["报告存在幻觉", ...]`——
字符串数组而非对象数组，完整架构下数十轮从未出现。

**结构化输入不止提供信息，也在约束输出结构。** 该现象在准确率指标中不可见——
它表现为下游代码崩溃，而生产环境中这比掉几个点严重得多。

### 实验产物

每次运行落盘 `eval/runs/<时间戳>_<标签>.jsonl`，含配置元数据、逐次耗时、
裁决、门控留痕与全部 issues，确保任一百分比可回溯复算。

---

## 二、风险评分卡

### 2.1 为什么必须是规则而非 LLM 打分

业务上：信贷评审会要问"为什么是高风险"，答案必须是"资产负债率 82% 超过阈值 70%，且存在 3 笔被执行记录"，而不是"模型认为"。
技术上：规则可单测、可复现、可作为 Stage 5.1 评测的 ground truth 基准。LLM 打分不可复现，评测就无从谈起。

**LLM 负责把非结构化信息转成结构化字段，规则负责打分。** 职责分离。

### 2.2 五个维度

| 维度 | 权重 | 主要指标 |
|---|---|---|
| 财务 financial | 30% | 资产负债率、净利润率、营收趋势、经营性现金流 |
| 司法 judicial | 30% | 被执行次数与金额、失信记录、涉诉金额占营收比 |
| 关联 relation | 20% | 担保圈规模、对外担保占净资产比、关联方交易占比 |
| 经营 operation | 10% | 登记状态、成立年限、参保人数趋势、中标记录 |
| 舆情 opinion | 10% | 负面新闻数量与严重度、监管处罚 |

每个维度产出 0–100 分（分越高风险越大），加权得综合分。

### 2.3 阈值示例（财务维度，其余同构）

```
资产负债率  ≤50%→0分   50-70%→30分   70-85%→60分   >85%→100分
净利润率    ≥10%→0分   0-10%→30分    -10-0%→70分   <-10%→100分
营收趋势    增长→0分    持平→30分      下滑<20%→60分  下滑≥20%→100分
经营现金流  为正→0分    小幅为负→50分  持续为负→100分
```

维度得分 = 各指标均值（仅计入 `verified` 的指标，见 2.4）。

综合分 → 等级：
```
0-25   低风险    建议授信
26-50  中风险    建议授信，需增信措施
51-75  高风险    审慎，建议降额或追加担保
76-100 拒绝      不建议授信
```

### 2.4 ⭐ 完整度闸门（本设计最关键的一条）

**核心风控原则：查不到 ≠ 没问题。**

若不加约束，会出现灾难性行为：一家企业因为司法数据源没返回，涉诉/被执行/失信三项全部 `unverified`，司法维度无扣分，综合分很低 → 系统输出"低风险，建议授信"。**这正是尽调系统最危险的失效模式**，也是朴素 LLM 方案必然踩的坑。

```python
completeness = {
    "required_total": int,        # 必查项总数
    "required_verified": int,     # 已核实的必查项数
    "verified_rate": float,       # 核实率 = verified / required_total
    "unverified_fields": [...],   # 未核实的必查项 field_id 列表
    "conflicting_fields": [...],  # 冲突项
    "by_category": {...},         # 分维度核实率
}
```

闸门规则（**在综合评分之后强制施加，不可被评分覆盖**）：

| 条件 | 动作 |
|---|---|
| 总体核实率 < 60% | 等级强制置为「数据不足，无法评级」，**不得输出授信建议** |
| 某维度核实率 < 50% | 该维度不参与加权，且综合等级**至少为中风险** |
| 司法维度任一必查项未核实 | 综合等级**至少为中风险**（司法是硬约束，缺失即风险） |
| 存在 `conflicting` 必查项 | 等级上调一级，且必须触发人工复核 |
| 存在被执行/失信记录 | 综合等级**至少为高风险**（一票否决类指标） |

规则实现放在 `service/risk_scorecard.py`，纯函数、无 LLM、可单测。评分结果结构：

```python
RiskAssessment = {
    "composite_score": float,
    "level": "低风险|中风险|高风险|拒绝|数据不足",
    "dimension_scores": {financial: 45.0, judicial: 80.0, ...},
    "triggered_rules": [                       # 可解释性的来源
        {"rule": "judicial_enforcement_exists", "detail": "存在3笔被执行记录",
         "effect": "等级下限提升至高风险", "evidence": ["fact_id_12","fact_id_15"]}
    ],
    "gate_applied": ["judicial_incomplete"],   # 触发了哪些闸门
    "requires_human_review": bool,             # 对接 Stage 3.1 人机协同
    "completeness": {...},
}
```

`triggered_rules` 是整个评分卡的可解释性来源——报告里的每一句风险结论都能指回某条规则和某个 fact_id。

---

## 三、与其它 Stage 的接口

| 对接 | 约定 |
|---|---|
| **Stage 1.1 数据模型** | `DueDiligenceReport` 增 `field_checks_json JSONB`、`completeness_json JSONB`、`risk_assessment_json JSONB`（含 triggered_rules） |
| **Stage 1.3 模拟数据** | 生成企业时**同时生成 ground truth**：每家企业的 20 项字段真值 + 正确风险等级 + 应触发的规则列表。刻意留一部分字段"数据源查不到"，用来测未核实识别 |
| **Stage 3.1 人机协同** | `requires_human_review=True` 触发 LangGraph `interrupt`；冲突项与高风险结论必须人工确认 |
| **Stage 5.1 评测** | 四个指标直接由本设计导出：<br>① 字段准确率 = verified 字段值与真值一致比例<br>② **未核实识别率** = 真实取不到的字段被正确标 unverified 的比例（漏标最危险）<br>③ 幻觉率 = 报告中对 non-verified 字段做实质断言的次数<br>④ 等级一致性 = 与 ground truth 等级吻合率 |
| **Stage 4 前端** | 核实率进度条、未核实清单面板、冲突项高亮、`triggered_rules` 展开即为"为什么是这个等级" |

---

## 四、面试可讲的点

1. **把"不要幻觉"从提示词约束变成了架构约束**——闭集清单 + 状态机 + 独立校验 Agent，模型没有"悄悄跳过"的路径
2. **缺失数据反向约束结论**——完整度闸门防住了"查不到所以没问题"这个最危险的失效模式，这是业务理解而非技术炫技
3. **LLM 与规则的职责分离**——模型做非结构化到结构化的转换，规则做判断，因此结论可解释、可单测、可评测
4. **`conflicting` 作为一等状态**——多源冲突在尽调中是核心风险信号，而非需要消解的噪声

---

## 五、待办

- [ ] `state.py` 增 `FieldCheck` TypedDict + `field_checks` / `completeness` 字段
- [ ] `service/risk_scorecard.py` 纯函数实现 + 单测
- [ ] Architect 生成清单骨架的 prompt
- [ ] Scout 逐项取证 + 冲突检测逻辑
- [ ] Writer 三种状态的固定渲染模板
- [ ] Critic 三类新 issue 的检测 prompt
- [ ] 阈值需要业务复核：当前数值参考公开信贷资料，非权威，README 中需说明为演示用途
