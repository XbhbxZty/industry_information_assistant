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

## 一·五、消融实验（v3 已完成）

> 2026-08-11 重做完成。触发问题：换用更强模型后误报大幅下降，
> 是否意味着此前的架构工作被模型能力溢出？
>
> **v1/v2 数字均已撤回**。v2 修掉了提示词注入侧的假消融，
> 但独立审查发现 `merge_review()` 仍重新执行扫描器；首次联网冒烟又发现
> LLM 断线会被算成关闭扫描器组的漏检（BC-24、BC-25）。

### 已发现并修复的实验设计缺陷

1. **`--ablate scanner` 并未真正关闭扫描器。** 它只绕过了结果合并，
   而 `_review_content()` 自身仍调用 `scan_report()` 并把结论写进提示词。
   消融组的 LLM 依然看得到扫描结果，故当时算出的"扫描器 +6.7 个百分点"无效。
   现已四处同时关闭：执行、提示词注入、结果合并、类型过滤。
2. **误报口径过窄。** 此前只统计 `unverified_as_fact` /
   `conflict_silently_resolved` / `unsupported_risk_conclusion` 三类；
   模型若把同样的错误意见标为 `hallucination`、`logic_error`，
   或返回字符串形态的 issue，均不计入，导致误报被系统性低估。
   现改为**任何 critical/major 级问题都算误报**。
3. **合并阶段重新打开扫描器。** `_review_content()` 虽已关闭执行与提示词注入，
   `merge_review()` 却再次无条件调用 `scan_report()`；现在同一开关贯穿四处。
4. **LLM 失败污染消融分母。** 完整组可由扫描器在模型断线时命中，关闭组则漏检，
   会把 API 可用性错算成扫描器贡献。现在降级调用保留 JSONL，但不进入准确率分母，
   且整轮命令非零退出。

### 已撤回的 v2 数据（不得引用为结论）

| 配置 | 逐次检出率 | 漏检率 | 逐次无误报率 | 误报率 | 检出稳定性 |
|---|---|---|---|---|---|
| A 当时声称的完整架构 | 100% | 0% | 94.4% | 5.6% | **无效：B 组未真正关闭扫描器** |
| B 当时声称关闭扫描器 | 96.7% | 3.3% | 92.5% | 7.5% | **无效：合并阶段仍执行扫描器** |

> C 组（无清单）与 D 组（弱模型）的旧数据同样采用旧口径。
> A/B/C/D 全部需要在 v3 harness 上重跑后才能引用。

### v3 结论与原始结果

四组开发集和首次留出集均已完成，每组重复 3 次，LLM 调用失败 0 次。
完整表格、两层指标口径、配置、数据 SHA256 与逐次 JSONL 文件索引见
[`backend/eval/ABLATION_V3.md`](../backend/eval/ABLATION_V3.md)。

可复现的主要结论是：去掉结构化清单后，目标违规命中率从开发集 100% 降至
40%，首次留出集从 83.3% 降至 38.9%；弱模型则以 98.1%/100% 的误报率
换取“几乎全拦”。扫描器在开发集提供小幅增益，但首次留出集暴露 BC-26
词法边界缺陷，尚不能宣称有稳定的总体准确率增益。

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
- A/B/C/D 已按 v3 口径完成；结果见独立实验报告。
- `critic_holdout.json`（6 注入 + 8 对照）首次运行暴露 BC-26，随后被用于修复，
  因而已退役为回归集。不得将修后复跑结果称作新的泛化证据；封板前需另建盲测集。
- 首次留出仍只是 case-level holdout，不是企业分布留出。

### 第二套独立盲测：v0.5 未封板

`critic_holdout_v2.json` 在 commit `c701bb7` 由独立 Agent 创建并冻结，SHA256 为
`299bd3e22c9490e8b253eb1dd773de3f65b77bfc664c80ad5cedefd2d3e638e6`。
首次运行中完整架构 A 的目标命中率 63.9%、坏报告拦截率 83.3%、误报率 36.1%，
并出现 2 个稳定目标漏检与 4 个稳定误报，因此 **v0.5 未封板**。

四个稳定误报均由扫描器不理解否定作用域/开放式冲突披露造成（BC-28）；另有坏报告
在模型已指出逻辑外推时仍因 `minor + pass` 被稳定放行（BC-29）。完整结果、原始文件
哈希与退役规则见
[`backend/eval/BLIND_V2_SEAL_REPORT.md`](../backend/eval/BLIND_V2_SEAL_REPORT.md)。
该集合已退役为回归集，修复后不得复跑并称作独立泛化证明。

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

---

## 三、v0.5 实施后补充的设计决策

> 实现见 `backend/app/service/risk_scorecard.py`（纯函数，无 LLM），
> 断言见 `backend/tests/test_risk_scorecard.py`（13 例）。

### 3.1 ⭐ 综合分不可单独使用

实测发现加权平均存在**稀释效应**。一家失信 + 2 笔被执行 + 资产负债率 89.1%
+ 严重负面舆情 + 连续亏损的企业，综合分仅 **47.5**：

```
financial  70.0 × 0.30 = 21.0
judicial   66.7 × 0.30 = 20.0
relation    0.0 × 0.20 =  0.0   ← 无对外担保，"表现好"的维度
operation  15.0 × 0.10 =  1.5
opinion    50.0 × 0.10 =  5.0
                          47.5   → 单看分数落在"中风险"区间
```

这是加权平均的固有行为，不是缺陷。**但它直接证明了闸门的必要性**：
真正挡住这家企业的是"失信 → 至少高风险"这条硬规则，而非分数。

**因此：`composite_score` 是参考信号，`level` 才是结论。**
任何下游逻辑（前端展示、审批流、导出报告）都不得仅依据分数判断，
必须消费 `level` + `gates_applied`。已用测试
`test_综合分会被表现好的维度稀释_故闸门是必需的` 锁定该行为。

### 3.2 闸门是主要判据，不是补充规则

五家评测企业中，**四家的最终等级由闸门决定而非分数**：

| 企业 | 综合分 | 分数对应等级 | 实际等级 | 决定因素 |
|---|---|---|---|---|
| EVAL-001 | 0.0 | 低风险 | 中风险 | relation 维度缺口 |
| EVAL-002 | 96.4 | 拒绝 | 拒绝 | 分数（唯一一致的） |
| EVAL-003 | 9.0 | 低风险 | **中风险** | 司法维度未核实 |
| EVAL-004 | 0.0 | 低风险 | **数据不足** | 核实率 27% |
| EVAL-005 | 15.0 | 低风险 | **高风险** | 冲突上调一级 |

这与 2.4 节的设计意图一致：**评分负责在数据充分时排序，
闸门负责在数据不充分时兜底**。两者不是主次关系，而是覆盖不同状态空间。

### 3.3 未核实字段不参与打分（而非按 0 分计入）

实现上只有 `status == "verified"` 的字段进入评分。这是闸门的前提——
若未核实字段按"无风险"计 0 分，司法源故障的企业会因"无扣分"而分数极低，
正是要防的失效模式。

对应断言：`test_未核实字段不得贡献无风险信号`。

### 3.4 ⚠️ 已知缺陷：必查项无数据源导致等级不可达

`guarantee_circle`（担保圈）列为必查项，但**当前没有任何数据源能提供它**
——需关联图谱推导，属 v0.6 能力。因此恒为 `unverified`，
relation 维度恒为 1/3 = 33% < `MIN_CATEGORY_RATE`(50%)，
维度恒被排除、等级下限恒被提升至中风险。

**后果：「低风险」等级在当前实现下不可达**，包括刻意构造的优质企业 EVAL-001。

**未通过调阈值掩盖**——把 `MIN_CATEGORY_RATE` 降到 30% 能让数字好看，
但真实缺口并未消失。待 v0.6 图谱能力就绪后按下列方案之一处理：

- **(a)** 能力就绪前把 `guarantee_circle` 降为选查项
- **(b)** 维度核实率的分母只计"存在可用数据源"的项，
  无数据源的项单独标记为**能力缺失**而非**信息缺口**

倾向 (b)：它区分了"这次没查到"与"系统根本查不了"。
后者是产品能力问题，不该按信息缺口计入风险评估。

详见 [`BADCASES.md`](BADCASES.md) BC-18。

### 3.5 为什么规则实现必须可复现

`test_评分可复现` 断言同一输入必须产出完全相同的
`composite_score` / `level` / `gates_applied`。

这条看似多余，实则针对 v0.4 的实测教训：LLM 判定在同一输入下三次给出不同结论
（28 例中 7 例不稳定）。风险评级若带方差，
**同一家企业今天判中风险、明天判高风险**，业务上不可接受，
评测也失去基准。规则实现天然满足此约束，这正是选择规则而非 LLM 的核心理由之一。

---

## 四、评分卡接入主流程（v0.5 完成）

> 实现散布在 `data_analyst.py` / `writer.py` / `graph.py`，
> 行为断言集中在 `backend/tests/test_risk_integration.py`（20 例）。
>
> 与上一节的分工：3.x 回答"算得对不对"，本节回答"**算出来的东西有没有真的生效**"。
> BC-17 的教训是这两个问题必须分开验证。

### 4.1 调用点：为什么在 DataAnalyst 的最前面

```
Architect → Scout → [DataAnalyst: ① 风险评分（纯规则） → ② 数据提取 → ③ 知识图谱 → ④ 图表] → Writer → Critic
```

评分调用刻意排在该阶段所有 LLM 步骤**之前**，且不消费任何 LLM 产物。
理由是 BC-17 的第二条：**确定性逻辑不得写在不确定逻辑之内或之后**——
否则 ②③④ 任何一步抛异常，评级就跟着消失，而这个业务里
"没有评级"会被读成"没有风险"。

对应断言：`test_LLM全部失败时评级仍然产出`（stub 掉 `call_llm` 使其必然抛异常，
断言 `process()` 抛出后 `state["risk_assessment"]` 仍是高风险及以上）。

> 写这条断言时踩到一个小坑，值得记下来：最初的用例里 `facts` 是空的，
> 而 DataAnalyst 三个步骤在无素材时都会提前返回——**LLM 根本没被调用，
> "LLM 全挂"这个前提压根不成立，用例却是绿的**。
> 靠一句 `assert raised` 的前提断言才暴露出来。这与 BC-15 同形：
> 测试跑通了，但测的不是声称的那件事。

### 4.2 三条 fail-closed 路径：算不出 ≠ 没风险

`score()` 的入参是**清单状态 + 结构化档案**两份数据。清单说 `verified`
但档案没进 state 时会发生一件很危险的事：

```python
score({}, 全部 verified 的清单, completeness) → 低风险   # 实测，见测试中的前提断言
```

因为 `judicial_records` 取到空列表 → 打分函数如实输出"未发现失信被执行人记录"
→ 司法维度 0 分。**一个纯粹由链路缺陷凭空制造出来的正面结论**，
且完整度闸门查不出来（清单确实是 verified）。

这是接入环节独有的失效面，评分卡自身的单测覆盖不到。因此 `assess_risk()`
对三种前置条件分别处理：

| 情形 | 处理 | 理由 |
|---|---|---|
| 无核查清单 | 跳过，不产出评级 | 非尽调流程，本就不存在"授信结论"这个产物 |
| 有清单但无档案 | `unratable()` → 数据不足，强制人工复核 | 见上，绝不能当作"无不良记录" |
| 清单 verified 无法由档案逐字段复现 | `verified_profile_mismatches()` 重放映射后 `unratable()` | 非空档案也可能只丢一个字段；整体验空抓不到 |
| 打分抛异常 | `unratable()` + 记 `errors` | 静默降级比报错难查十倍（BC-02） |

`unratable()` 返回与 `score()` 完全一致的结构，下游无需区分两条路径。

### 4.3 完整度按当前清单重算

`assess_risk()` 不直接用 `state["completeness"]`，而是按当前 `field_checks`
重算并写回。闸门的判据必须与被打分的清单同源，否则会出现
"按旧核实率放行、按新清单打分"的错配。断言：`test_完整度按当前清单重算`。

### 4.4 评级如何进入报告：提示词 + 代码兜底两层

提示词能可靠表达"要什么"，但表达"不要改写什么"不可靠（v0.3 的反复实测结论）。
而整合与修订都是重写全文的 LLM 步骤，评级随时可能被抹平成
"总体风险可控"。因此做成两层：

| 层 | 作用 | 失效时的表现 |
|---|---|---|
| 提示词（sec_8） | 给出等级 + 全部闸门，要求原样采用并解释依据 | 模型概括、换词、自行改判 |
| 代码 | `_pin_risk_block()` 把渲染块钉进第 8 章草稿；`_ensure_risk_block()` 在整合/修订后**无条件重建权威块**，缺失、篡改、重复三类输入统一收口并 `WARNING` 留痕 | —— |

`render_markdown()` 是**唯一渲染入口**：模型看到的评级块与最终落进报告的
是同一份文本，避免两处各写一套导致口径不一致。

代码层还顺带覆盖了一个提示词管不到的情形：第 8 章的模型输出不可用
（JSON 解析失败 / 返回空）时，草稿里至少仍有评级，而不是既没有正文也没有等级。
边界要说清楚：若 `call_llm` 直接抛异常，异常会先于兜底逃逸，
此时整个撰写阶段都没有产物，不在这层的覆盖范围内。
断言：`test_模型未产出内容时评级仍进草稿`、`test_整合时模型丢弃评级由代码补回`、
`test_整合时模型保留标记但篡改等级也会被纠正`、`test_修订后评级仍在报告中`。

### 4.5 渲染必须同时给出等级与闸门

`render_markdown()` 的表格里，综合分那一行**强制带上"不可单独使用"的说明**，
并把 `gates_applied` 逐条列出。这不是排版偏好：3.1 已经证明
一家已列入失信名单的企业综合分只有 47.5（落在中风险区间），
只呈现分数会让读者得出与等级相反的结论。断言：`test_评级块同时呈现等级与闸门`。

### 4.6 一个容易忽略的交叉影响：评级块会被自己的扫描器扫到

评级块进入 `final_report`，而 Critic 扫描 `final_report`。
若渲染文本里出现"未发现被执行记录"而该字段并非 `verified`，
**系统就用自己的输出制造了一条 critical**，每一轮都被门控降级，
且排查时极难想到源头是自己。

实际不会发生（`score()` 只为 verified 字段生成规则说明），
但这是"实现正确所以碰巧安全"，而非"被约束保证安全"。
已用 5 家评测企业锁死为回归测试：`test_评级块不触发扫描器误报`。

### 4.7 SSE 与终局事件

- 流式：`risk_assessment` 事件（DataAnalyst 推送），携带
  `level` / `composite_score` / `gates_applied` / `triggered_rules` /
  `dimension_scores` / `requires_human_review` / `credit_advice` / `completeness`
- 终局：`research_complete` 事件增 `risk_assessment` 字段

两处都推是必要的——只在中途推流，只等最终结果的调用方就拿不到评级，
而"拿不到评级"在这个业务里不能表现为"没有风险"。
`build_complete_event()` 独立成函数正是为了让这条可以被断言。

**契约约束**：任何消费方都必须同时读 `level` 与 `gates_applied`，
不得只按 `composite_score` 做判断（理由见 3.1）。
