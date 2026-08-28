# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""
DeepResearch V2.0 - 首席笔杆 Agent (LeadWriter)

职责：
1. 深度写作 - 将零散信息串联成逻辑严密的报告
2. Markdown排版 - 专业的格式排版
3. 图文混排 - 整合文字、图表、数据
4. 参考文献 - 规范的引用格式
"""

import uuid
from typing import Dict, Any, List
from datetime import datetime

from .base import BaseAgent
from ..state import ResearchState, ResearchPhase

try:
    from service.risk_scorecard import RISK_BLOCK_END, RISK_BLOCK_MARKER, render_markdown
    from service.investigation_layer import (
        SECTION_MARKER as INVESTIGATION_MARKER,
        canonicalize_investigation_section, render_investigation_section,
    )
    from service.evidence_appendix import (
        APPENDIX_MARKER, canonicalize_appendix, format_provenance, render_appendix,
    )
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.risk_scorecard import RISK_BLOCK_END, RISK_BLOCK_MARKER, render_markdown
    from app.service.investigation_layer import (
        SECTION_MARKER as INVESTIGATION_MARKER,
        canonicalize_investigation_section, render_investigation_section,
    )
    from app.service.evidence_appendix import (
        APPENDIX_MARKER, canonicalize_appendix, format_provenance, render_appendix,
    )


def _excise_risk_block(text: str) -> str:
    """
    从文本中切除评级块，保留其余正文。

    评级块以 `RISK_BLOCK_MARKER` 开头、`RISK_BLOCK_END` 结尾。
    模型被要求原样保留整块，因此正常情况下两个标记会成对出现，可精确切除。

    若只找到起始标记（模型把结尾注释删了），无法确定块在何处结束——
    此时只保留起始标记之前的内容并接受尾部损失，**不能保留残块**：
    一段被截断的评级表格比丢掉几句正文危险得多，读者会当它是完整结论。
    """
    start = text.find(RISK_BLOCK_MARKER)
    if start < 0:
        return text.strip()
    # 锚点位于 `**…**` 之内，需回退到该行行首才能整行切除
    line_start = text.rfind("\n", 0, start) + 1
    head = text[:line_start].rstrip()

    end = text.find(RISK_BLOCK_END, start)
    tail = text[end + len(RISK_BLOCK_END):].strip() if end >= 0 else ""
    return "\n\n".join(p for p in (head, tail) if p)


def _canonicalize_risk_block(text: str, block: str) -> str:
    """把正文中的评级块收敛为一份规则引擎原文。"""
    start = text.find(RISK_BLOCK_MARKER)
    if start < 0:
        return (text.rstrip() + "\n\n---\n\n" + block).lstrip()

    line_start = text.rfind("\n", 0, start) + 1
    head = text[:line_start].rstrip()
    end = text.find(RISK_BLOCK_END, start)
    if end < 0:
        # 起始标记存在但块已被模型截断，无法辨认其尾部。沿用
        # _excise_risk_block 的 fail-closed 策略：不保留可被误读的残块。
        return "\n\n".join(p for p in (head, block) if p)

    tail = text[end + len(RISK_BLOCK_END):].strip()
    # 模型可能复制出多份评级；逐份切除后只放回一份权威版本。
    while RISK_BLOCK_MARKER in tail:
        new_tail = _excise_risk_block(tail)
        if new_tail == tail:
            break
        tail = new_tail
    return "\n\n".join(p for p in (head, block, tail) if p)

# 风险汇总与授信建议章节。提纲固定 8 章（见 architect.PLANNING_PROMPT），
# 但模型偶尔会改标题，因此再留一条按标题识别的兜底。
RISK_SECTION_ID = "sec_8"


class LeadWriter(BaseAgent):
    """
    首席笔杆 - 最终输出的打磨者

    特点：
    - 深度写作能力
    - 专业的行业研究报告风格
    - 逻辑严密的叙述结构
    - 规范的引用和排版
    """

    SECTION_WRITING_PROMPT = """你是信贷机构的尽职调查分析师，正在撰写《贷前尽职调查报告》的一个章节。

## 尽调任务
{query}

## 当前章节信息
标题: {section_title}
描述: {section_description}
类型: {section_type}

## 📋 本章节核查清单（**最重要，必须逐项交代**）

{field_checks}

每一项的核查状态**已由数据源判定完毕**，你的任务是准确表述，不是重新判断。
三种状态的写法要求完全不同：

| 状态 | 写法要求 |
|---|---|
| 已核实：`<具体值>` | 正常陈述该事实，并在句末标注来源与日期 |
| 已核实：`经查询，无相关记录` | **这是正面结论**，写"经查询未发现XX记录"，可作为有利因素 |
| **未核实** | 必须写"该项未核实（原因：…）"，**严禁**写成"无""未发现""不存在" |
| 数据冲突 | 并列披露各来源的值，明确指出冲突，要求人工核实 |

⚠️ 最易犯的致命错误：把「未核实」写成「无」。
「经查询无失信记录」是可支持授信的结论；「失信记录未核实」是必须补查的缺口。
二者混淆会直接误导信贷审批。

## 🔒 风险评级（由规则引擎判定，不是你的判断）

{risk_scorecard}

如上方为具体评级内容，则本章必须遵守：
1. **等级、授信建议、闸门结论一律原样采用**，不得上调、下调或改写为"综合来看…"
2. **不得只引用综合评分**。分数会被表现好的维度稀释（例如已列入失信名单的企业
   综合分仍可能落在中风险区间），真正决定等级的往往是闸门。
   必须把触发的闸门逐条写出来，说明等级为何是这个结果
3. 你的任务是**解释评级依据**并归纳各维度发现，不是重新评级

## 可用素材（这是你唯一可以依据的信息）

### 已核实事实
{facts}

### 数据点
{data_points}

### 已有洞察
{insights}

### 相关图表
{charts_info}

## ⛔ 最高优先级规则：不得超出素材范围

尽调报告是信贷审批的依据，一句编造的结论可能导致坏账。因此：

1. **清单中标为「未核实」的项，不得以任何方式给出实质性结论**
2. **禁止推断填充**。例如清单给了股东名单但实际控制人未核实，
   不得自行推断"周某为实际控制人"
3. **禁止把「未核实」写成「无」**（见上表，这是最易犯的致命错误）
4. **禁止把缺失当利好**。查不到负面信息 ≠ 没有负面信息
5. 每个事实性陈述后标注素材中给出的来源与证据日期，格式：`（来源：工商登记信息，YYYY-MM-DD）`；素材未给日期时明确写“日期未确认”，不得自行补日期

## 写作要求
1. **客观中立**：陈述事实与风险，不做营销式表述
2. **风险导向**：尽调的目的是发现问题，对异常指标要明确指出
3. **数据支撑**：涉及数字的结论必须引用素材中的具体数值
4. **证据等级不可混用**：跨年度做趋势分析时，若各期数据的证据等级不同
   （如前两年为经审计资料、最近一年为企业自报未经审计），**必须显式指出这一点**，
   并说明趋势结论因此存在的不确定性。不得把不同可信度的数据直接并列
   得出确定性结论——这是尽调报告的常见误导来源
4. **图表整合**：在合适位置插入图表引用 ![图表标题](chart_id)
5. **字数控制**：本章节 400-800 字
6. **不要重复标题**：正文开头不要再写章节标题

## 输出格式
```json
{{
    "content": "章节正文内容（Markdown格式，不包含章节标题）",
    "key_points": ["本章节的核心要点"],
    "unverified_items": ["本章节中未能核实的关键事项"],
    "citations": [
        {{"source": "来源名称", "url": "完整URL或空字符串"}}
    ],
    "suggested_improvements": ["需要补充核查的方向"]
}}
```

## 写作示例
- ✅ 正确："2025年度资产负债率升至72.9%，较2023年上升17.1个百分点，
  同期经营性现金流由+1240万元转为-1580万元（来源：企业自报未经审计，YYYY-MM-DD），
  需关注其偿债能力与数据可靠性。"
- ✅ 正确："实际控制人未核实（原因：工商登记数据未包含实际控制人认定信息）。"
- ❌ 错误："公司治理结构清晰，实际控制人为持股52%的周立群。"（素材未提供，属推断）
- ❌ 错误："未发现对外担保。"（素材未提供该项，应写"未核实"而非断言无）

开始撰写："""

    SYNTHESIS_PROMPT = """你是尽职调查报告主编，需要将各章节整合成完整的《贷前尽职调查报告》。

## 尽调任务
{query}

## 各章节内容
{sections_content}

## 收集的所有引用来源
{all_sources}

## 任务
1. 撰写尽调结论摘要（供信贷评审会快速阅读）
2. 整合各章节，确保逻辑连贯，使用层级编号
3. 汇总风险点并给出授信意见
4. 整理资料来源清单

## ⛔ 最高优先级规则

1. **不得引入任何章节素材中不存在的信息**。整合阶段只做组织和归纳，不做补充
2. **各章节标注为"未核实"的事项，必须在摘要与风险汇总中保留**，不得在整合时悄悄抹去
3. **摘要中必须包含"信息缺口"部分**，列出所有未核实的关键事项
4. **授信意见必须与已核实的证据一致**。若关键信息大量缺失，
   结论应为"信息不足，建议补充尽调后再议"，而不是给出乐观或悲观的倾向性判断
5. **第 8 章中的「风险评级（规则引擎判定）」整块内容必须原样保留**，
   包括等级、闸门列表与授信建议。该块由规则引擎产出，不是可以润色或概括的行文；
   摘要中给出的风险结论也必须与它一致，不得出现两个不同的等级

## 关键要求

### 1. 标题编号规则（必须严格遵守）
- 一级标题：1、2、3...（如：1 企业基本情况）
- 二级标题：1.1、1.2、2.1...（如：4.1 偿债能力）
- 三级标题：1.1.1、1.1.2...
- **禁止标题重复**：每个标题必须唯一，不要在正文中重复章节标题

### 2. 引用格式规则
- 内部数据源：在事实后标注 `（来源：工商登记信息，YYYY-MM-DD）`，日期只能采用素材给出的证据日期
- 网络来源：使用 [来源名称](URL) 格式
- 文末资料来源清单：列出全部数据源及获取时间

### 3. 报告结构规范
- 不要在报告开头使用 # 一级标题
- 直接从"执行摘要"开始
- 各章节使用 ## 二级标题
- 子章节使用 ### 三级标题

## 输出格式
```json
{{
    "executive_summary": "尽调结论摘要（300-500字，须含信息缺口说明）",
    "full_report": "完整报告（Markdown格式，按下方结构生成）",
    "conclusions": ["核心风险结论1", "核心风险结论2"],
    "outlook": "授信意见与增信建议",
    "unverified_summary": ["全部未核实的关键事项"],
    "references": [
        {{"id": 1, "title": "数据源名称", "url": "URL或空字符串", "author": "提供方", "date": "获取日期"}}
    ]
}}
```

## 报告结构模板
```markdown
## 尽调结论摘要

[核心发现与主要风险，300-500字]

**信息缺口**：[列出未能核实的关键事项，若无则写"无"]

---

## 1 企业基本情况

### 1.1 [子章节标题]

[内容，事实后标注（来源：xxx，日期）]

---

## 2 股权结构与实际控制人

...（依次至第 7 章）

---

## 8 风险汇总与授信建议

### 8.1 各维度风险归纳

| 维度 | 主要发现 | 风险提示 | 核实状态 |
|---|---|---|---|
| 财务 | ... | ... | 已核实/未核实 |
| 司法 | ... | ... | ... |

### 8.2 未核实事项清单

[逐条列出，注明未能核实的原因]

### 8.3 授信意见

[基于已核实证据的意见。若关键信息缺失较多，明确写"信息不足，建议补充尽调后再议"]

---

## 资料来源

1. [数据源名称] - 提供方, 获取日期
...
```"""

    REVISION_PROMPT = """你是首席笔杆，需要根据审核反馈修订报告。

## 原始报告
{original_content}

## 审核反馈
{feedback}

## 补充的新信息
{new_info}

## 任务
根据反馈修订报告，解决指出的问题。

## 修订原则
1. 针对性修改：只修改有问题的部分
2. 补充来源：对缺少来源的观点补充引用
3. 修正错误：纠正事实错误或逻辑漏洞
4. 保持风格：修订后保持报告整体风格一致

输出JSON：
```json
{{
    "revised_content": "修订后的内容",
    "changes_made": ["修改1", "修改2"],
    "addressed_issues": ["已解决的问题ID"],
    "unable_to_address": ["无法解决的问题及原因"]
}}
```"""

    def __init__(self, llm_api_key: str, llm_base_url: str, model: str = "qwen-max"):
        super().__init__(
            name="LeadWriter",
            role="首席笔杆",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """处理入口"""
        if state["phase"] == ResearchPhase.WRITING.value:
            return await self._write_report(state)
        elif state["phase"] == ResearchPhase.REVISING.value:
            return await self._revise_report(state)
        else:
            return state

    async def _write_report(self, state: ResearchState) -> ResearchState:
        """撰写报告"""
        # 发送 research_step 开始事件
        # 注意: step_type 必须是 "writing" 以匹配 graph.py 发送的 phase 事件
        self.add_message(state, "research_step", {
            "step_id": f"step_writing_{uuid.uuid4().hex[:8]}",
            "step_type": "writing",
            "title": "内容生成",
            "subtitle": "撰写研究报告",
            "status": "running",
            "stats": {"sections_count": len(state["outline"]), "word_count": 0}
        })

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": "开始撰写深度研究报告..."
        })

        if state.get("due_diligence_mode"):
            # 尽调交付物只能来自结构化清单、证据库和规则评分。让 LLM 重新
            # 叙述这些字段，实测会把“计划查询的数据源”写成“已引用接口”，
            # 或猜测未核实字段的局部取值。LLM 保留候选抽取职责，不拥有
            # 在最终报告新增事实、来源或结论的权限。
            self._write_structured_due_diligence_report(state)
            word_count = len(state.get("final_report", ""))
            self.add_message(state, "research_step", {
                "step_type": "writing", "title": "内容生成",
                "subtitle": "已从结构化证据链生成确定性尽调报告", "status": "completed",
                "stats": {"sections_count": len(state["outline"]),
                          "word_count": word_count, "references_count": 0},
            })
            state["phase"] = ResearchPhase.REVIEWING.value
            return state

        # 逐章节撰写
        for section in state["outline"]:
            if section.get("status") not in ["final", "drafted"]:
                await self._write_section(state, section)

        # 整合报告
        await self._synthesize_report(state)

        # 发送 research_step 完成事件
        word_count = len(state.get("final_report", ""))
        self.add_message(state, "research_step", {
            "step_type": "writing",
            "title": "内容生成",
            "subtitle": "撰写研究报告",
            "status": "completed",
            "stats": {
                "sections_count": len(state["outline"]),
                "word_count": word_count,
                "references_count": len(state.get("references", []))
            }
        })

        # 更新阶段
        state["phase"] = ResearchPhase.REVIEWING.value

        return state

    def _write_structured_due_diligence_report(self, state: ResearchState) -> None:
        """从清单/证据/评分确定性渲染尽调报告，禁止模型补全。"""
        checks = state.get("field_checks") or []
        completeness = state.get("completeness") or {}
        assessment = state.get("risk_assessment") or {}
        as_of = state.get("as_of") or "未设置"
        company = state.get("company_name") or state.get("subject_name") or "待核实主体"
        required_verified = completeness.get("required_verified", 0)
        required_total = completeness.get("required_total", 0)
        lines = [
            f"# {company} 尽职调查报告",
            "",
            "## 结论",
            "",
            f"研究截止日：{as_of}。必查项核实率为 {required_verified}/{required_total}。",
            f"规则评级：{assessment.get('level') or '尚未评级'}。",
            f"授信结论：{assessment.get('credit_advice') or '尚未形成授信结论'}。",
            "“未核实”既不表示存在风险，也不表示不存在风险；在补齐证据并重新核验前，不得据此放款。",
        ]

        sections = {str(row.get("id")): row.get("title") for row in (state.get("outline") or [])}
        section_ids = list(dict.fromkeys(
            [str(row.get("id")) for row in (state.get("outline") or [])]
            + [str(check.get("section_id")) for check in checks]
        ))
        for section_id in section_ids:
            section_checks = [c for c in checks if str(c.get("section_id")) == section_id]
            if not section_checks:
                continue
            lines.extend(["", f"## {sections.get(section_id) or section_id}", ""])
            for check in section_checks:
                status = check.get("status") or "unverified"
                name = check.get("field_name") or check.get("field_id")
                if status == "verified":
                    lines.append(
                        f"- **{name}：已核实**；取值：{check.get('value')}；"
                        f"{format_provenance(check)}"
                    )
                elif status == "conflicting":
                    detail = "；".join(
                        f"{row.get('source')}={row.get('value')}"
                        for row in (check.get("conflict_detail") or [])
                    )
                    lines.append(f"- **{name}：数据冲突**；{detail}；须人工复核，未自动采信任何一方。")
                elif status == "not_applicable":
                    lines.append(f"- {name}：不适用；原因：{check.get('failure_reason') or '未说明'}")
                else:
                    reason = check.get("failure_reason") or "未取得满足核验规则的证据"
                    lines.append(f"- **{name}：未核实**；原因：{reason}")
        state["references"] = []
        state["final_report"] = "\n".join(lines)
        self._finalize_report(state)
        self.add_message(state, "report_draft", {
            "agent": self.name, "content": state["final_report"],
            "executive_summary": f"必查项核实 {required_verified}/{required_total}；"
                                 f"{assessment.get('level') or '尚未评级'}。",
            "conclusions": [assessment.get("credit_advice") or "尚未形成授信结论"],
            "word_count": len(state["final_report"]), "references_count": 0,
        })

    @staticmethod
    def _is_risk_section(section: Dict) -> bool:
        """是否为风险汇总章节（评级结果的归属章节）"""
        if section.get("id") == RISK_SECTION_ID:
            return True
        title = section.get("title") or ""
        return "风险汇总" in title or "授信建议" in title

    @staticmethod
    def _format_risk_scorecard(state: ResearchState, section: Dict) -> str:
        """
        渲染供撰写使用的评级块。非风险章节返回占位说明。

        评级来自规则引擎，Writer 只负责表述——与核查清单的分工完全一致：
        判断由规则做，模型只把结构化结论写成人话。
        """
        if not LeadWriter._is_risk_section(section):
            return "（本章节不涉及风险评级，无需引用等级结论）"
        assessment = state.get("risk_assessment") or {}
        if not assessment:
            # 评分卡未产出（非尽调流程，或分析阶段未执行）。
            # 不得让模型自行补一个等级——那正是规则化评分要消除的东西。
            return (
                "（⚠️ 本次未产出规则评级。**不得自行给出风险等级或授信结论**，"
                "本章只归纳各维度已核实发现与信息缺口，并写明"
                "「风险评级未生成，需人工评定」）"
            )
        return render_markdown(assessment)

    @staticmethod
    def _format_field_checks(state: ResearchState, section_id: str) -> str:
        """
        渲染本章节的核查清单（v0.2）。

        这是从"希望模型标注未核实"到"清单要求逐项交代"的转变：
        每一项的状态由数据源判定并传入，模型只负责表述，不负责判断有没有。

        三种状态的表述要求截然不同，尤其「已核实·无记录」是正面结论，
        不能与「未核实」混为一谈。
        """
        checks = [c for c in state.get("field_checks", []) if c.get("section_id") == section_id]
        if not checks:
            return "（本章节无对应核查项，请依据上方素材撰写）"

        lines = []
        for c in checks:
            tag = "必查" if c.get("required") else "选查"
            status = c.get("status")
            if status == "verified":
                # 来源与取证时间必须一并给出。
                #
                # 本提示词要求"在句末标注来源与日期"，而此前清单里根本没有这两项——
                # 让模型标注它拿不到的东西，在一个以反幻觉为目的的系统里
                # 等于邀请它编造。溯源信息 v0.6a 起就存在于 field_check 上，
                # 只是从没传到撰写环节（BC-48）。
                lines.append(
                    f"- [{tag}] {c['field_name']}｜已核实：{c.get('value')}"
                    f"｜{format_provenance(c)}"
                )
            elif status == "unverified":
                lines.append(
                    f"- [{tag}] {c['field_name']}｜**未核实**，原因：{c.get('failure_reason') or '数据源未覆盖'}"
                )
            elif status == "conflicting":
                detail = "；".join(
                    f"{d.get('source')}={d.get('value')}" for d in (c.get("conflict_detail") or [])
                )
                lines.append(f"- [{tag}] {c['field_name']}｜**数据冲突**：{detail}")
            else:
                lines.append(f"- [{tag}] {c['field_name']}｜不适用于本主体")
        return "\n".join(lines)

    async def _write_section(self, state: ResearchState, section: Dict) -> None:
        """撰写单个章节"""
        section_id = section["id"]
        self.logger.info(f"Writing section: {section.get('title')}")

        self.add_message(state, "action", {
            "agent": self.name,
            "tool": "writing_section",
            "section": section.get("title")
        })

        # 收集相关素材
        fact_pool = state["facts"]
        if state.get("due_diligence_mode"):
            # 尽调模式下，Scout 的普通抽取只是候选。只有经 RAG 证据桥逐字、
            # 主体和截止日校验后标为 verified 的事实才能进入写作上下文。
            fact_pool = [fact for fact in fact_pool if fact.get("verified") is True]
        related_facts = [f for f in fact_pool if section_id in f.get("related_sections", [])]
        if not related_facts:
            # 如果没有特定关联，使用所有事实
            related_facts = fact_pool[:10]

        # 格式化事实。
        # 注意用语义化的证据等级而非裸分数——实测模型会把 "可信度: 0.95"
        # 原样抄进报告正文，把内部字段泄漏给读者。
        def _evidence_level(score) -> str:
            try:
                s = float(score)
            except (TypeError, ValueError):
                return "证据等级未知"
            if s >= 0.9:
                return "官方登记信息"
            if s >= 0.8:
                return "经审计资料"
            if s >= 0.6:
                return "企业自报未经审计"
            return "公开报道，需佐证"

        facts_text = []
        for fact in related_facts:
            fact_date = (fact.get("metadata") or {}).get("as_of_date") or "日期未确认"
            facts_text.append(
                f"- {fact.get('content')}"
                f"（来源：{fact.get('source_name')}；证据日期：{fact_date}；"
                f"证据等级：{_evidence_level(fact.get('credibility_score'))}）"
            )

        # 格式化数据点
        data_text = []
        # data_points/insights 是 LLM 从普通事实派生的二级产物，目前不带独立
        # 证据 ID。尽调模式下不把它们作为素材，避免候选事实虽被拒绝，派生
        # 数字却从侧门进入报告。已核实数值已经在 field_checks 中提供。
        safe_data_points = [] if state.get("due_diligence_mode") else state["data_points"][:10]
        for dp in safe_data_points:
            data_text.append(f"- {dp.get('name')}: {dp.get('value')} {dp.get('unit', '')} ({dp.get('year', 'N/A')})")

        # 格式化图表信息
        charts_info = []
        for chart in ([] if state.get("due_diligence_mode") else state["charts"]):
            if chart.get("section_id") == section_id:
                charts_info.append(f"- 图表: {chart.get('title')} (ID: {chart.get('id')})")

        prompt = self.SECTION_WRITING_PROMPT.format(
            query=state["query"],
            section_title=section.get("title", ""),
            section_description=section.get("description", ""),
            section_type=section.get("section_type", "mixed"),
            facts="\n".join(facts_text) if facts_text else "（暂无相关事实）",
            data_points="\n".join(data_text) if data_text else "（暂无数据点）",
            insights=("（尽调模式不采用无独立证据ID的派生洞察）"
                      if state.get("due_diligence_mode")
                      else "\n".join([f"- {i}" for i in state["insights"][:5]]) if state["insights"] else "（暂无洞察）"),
            charts_info="\n".join(charts_info) if charts_info else "（暂无图表）",
            field_checks=self._format_field_checks(state, section_id),
            risk_scorecard=self._format_risk_scorecard(state, section)
        )

        response = await self.call_llm(
            system_prompt=(
                "你是信贷机构的尽职调查分析师，撰写贷前尽调报告。"
                "只依据给定素材写作，素材中没有的信息一律标注为未核实，严禁推断填充。"
            ),
            user_prompt=prompt,
            json_mode=True,
            temperature=0.4,
            # 供应商上限 8192，实测超了直接 400（见 base.MAX_OUTPUT_TOKENS）。
            max_tokens=self.max_output_tokens
        )

        result = self.parse_json_response(response)

        if result and result.get("content"):
            section_content = result["content"]
            state["draft_sections"][section_id] = section_content
            section["status"] = "drafted"

            # 收集引用
            for citation in result.get("citations", []):
                state["references"].append({
                    "id": len(state["references"]) + 1,
                    "marker": citation.get("marker"),
                    "source": citation.get("source"),
                    "url": citation.get("url", "")
                })

            # 发送章节内容到"过程报告" - 包含完整内容用于流式显示
            self.add_message(state, "section_content", {
                "agent": self.name,
                "section_id": section_id,
                "section_title": section.get("title"),
                "content": section_content,  # 完整章节内容
                "word_count": len(section_content),
                "key_points": result.get("key_points", [])
            })

            # 发送观察消息（显示在左侧步骤流程）
            self.add_message(state, "observation", {
                "agent": self.name,
                "content": f"章节「{section.get('title')}」撰写完成\n字数: {len(section_content)}\n要点: {', '.join(result.get('key_points', [])[:2]) if result.get('key_points') else '无'}"
            })

        # 风险章节：评级块由代码写入，不依赖模型是否照抄，也不依赖本次调用是否成功
        if self._is_risk_section(section):
            self._pin_risk_block(state, section)

    def _pin_risk_block(self, state: ResearchState, section: Dict) -> None:
        """
        把规则引擎的评级块钉进风险章节草稿。

        为什么不能只靠提示词：提示词能可靠表达"要什么"，但表达"不要改写什么"
        不可靠——模型会概括、会换词、会在整合时把等级抹平成"总体风险可控"。
        评级是规则产出的结论，必须由代码保证它原文进入报告。

        同时它不依赖模型是否产出可用内容：JSON 解析失败或返回空时，
        草稿里至少仍有评级，而不是既没有正文也没有等级。
        （注意边界：若 `call_llm` 直接抛异常，异常会先于本方法逃逸出 _write_section，
        此时整个撰写阶段都没有产物，不属于本兜底的覆盖范围。）
        """
        assessment = state.get("risk_assessment") or {}
        if not assessment:
            return
        section_id = section["id"]
        draft = state["draft_sections"].get(section_id, "")
        block = render_markdown(assessment)

        if RISK_BLOCK_MARKER in draft:
            # 模型照抄了评级块——提示词正是这么要求的，所以这是**预期路径而非边界情况**。
            # 必须**替换**而非前置：直接前置会让报告出现两个评级块；
            # 若模型顺手改了措辞，就成了"正确等级 + 被改写的等级"并列，
            # 恰恰是提示词自己警告的「不得出现两个不同的等级」。
            rest = _excise_risk_block(draft)
            state["draft_sections"][section_id] = (block + "\n\n" + rest) if rest else block
            self.logger.warning(
                f"[LeadWriter] {section_id} 草稿中已含评级块，已替换为规则引擎版本"
            )
        else:
            state["draft_sections"][section_id] = (block + "\n\n" + draft) if draft else block

        section["status"] = "drafted"
        self.logger.info(f"[LeadWriter] 已将风险评级（{assessment.get('level')}）写入 {section_id}")

    async def _synthesize_report(self, state: ResearchState) -> None:
        """整合完整报告"""
        self.add_message(state, "thought", {
            "agent": self.name,
            "content": "正在整合各章节，生成完整研究报告..."
        })

        # 准备各章节内容
        sections_content = []
        for section in state["outline"]:
            section_id = section["id"]
            content = state["draft_sections"].get(section_id, "")
            if content:
                sections_content.append(f"## {section.get('title')}\n{content}")

        # 收集所有来源
        all_sources = []
        for ref in state["references"]:
            all_sources.append(f"- {ref.get('source')} ({ref.get('url', 'N/A')})")

        for fact in state["facts"]:
            source_entry = f"- {fact.get('source_name')} ({fact.get('source_url', 'N/A')})"
            if source_entry not in all_sources:
                all_sources.append(source_entry)

        prompt = self.SYNTHESIS_PROMPT.format(
            query=state["query"],
            sections_content="\n\n".join(sections_content) if sections_content else "（暂无章节内容）",
            all_sources="\n".join(all_sources[:30]) if all_sources else "（暂无来源）"
        )

        self.logger.info(f"[LeadWriter] 调用 LLM 整合报告...")
        response = await self.call_llm(
            system_prompt=(
                "你是尽职调查报告主编，负责整合各章节。"
                "整合阶段只做组织归纳，不得引入新信息，不得抹去各章节标注的未核实事项。"
            ),
            user_prompt=prompt,
            json_mode=True,
            temperature=0.3,
            # 供应商上限 8192，实测超了直接 400（见 base.MAX_OUTPUT_TOKENS）。
            max_tokens=self.max_output_tokens
        )

        result = self.parse_json_response(response)
        self.logger.info(f"[LeadWriter] JSON 解析结果: {bool(result)}, keys: {result.keys() if result else 'N/A'}")

        executive_summary = ""
        conclusions = []

        if result and result.get("full_report"):
            state["final_report"] = result.get("full_report", "")
            executive_summary = result.get("executive_summary", "")
            conclusions = result.get("conclusions", [])
            self.logger.info(f"[LeadWriter] ✅ 报告整合成功，长度: {len(state['final_report'])}")

            # 更新参考文献
            for ref in result.get("references", []):
                if ref not in state["references"]:
                    state["references"].append(ref)
        else:
            # JSON 解析失败时的备选方案：使用已有章节内容组装报告
            self.logger.warning(f"[LeadWriter] ⚠️ JSON 解析失败，使用章节内容作为备选")
            fallback_report = f"# {state['query']} 研究报告\n\n"
            for section in state["outline"]:
                section_id = section["id"]
                content = state["draft_sections"].get(section_id, "")
                if content:
                    fallback_report += f"## {section.get('title', section_id)}\n\n{content}\n\n"
            state["final_report"] = fallback_report
            self.logger.info(f"[LeadWriter] 使用备选报告，长度: {len(state['final_report'])}")

        self._finalize_report(state)

        # 发送报告完成事件 - 包含完整报告内容用于前端流式显示
        self.add_message(state, "report_draft", {
            "agent": self.name,
            "content": state["final_report"],  # 完整报告内容
            "executive_summary": executive_summary,
            "conclusions": conclusions,
            "word_count": len(state["final_report"]),
            "references_count": len(state["references"])
        })

    def _finalize_report(self, state: ResearchState) -> None:
        """
        报告的**唯一收口入口**：把所有由代码保证的区块重新装回正文。

        ## 为什么必须是单一入口

        整合与修订都是 LLM 步骤，都会重写全文，因此每条路径都要重跑全部收口。
        此前两条路径各自调用：`_synthesize_report` 调了评级块与溯源附录，
        `_revise_report` 只调了评级块——**只要 Critic 要求修订一次，
        证据溯源附录就从最终报告里消失了**（BC-50）。

        真实运行验证时发现：复核卡点上的报告有附录（8416 字），
        终局报告没有（6651 字）。

        新增收口区块时只改这一个方法，不必记得同步几个调用点——
        "记得同步"这种要求迟早会失效，上一次就失效了。
        """
        self._ensure_risk_block(state)
        self._ensure_investigation_section(state)
        self._ensure_evidence_appendix(state)

    def _ensure_investigation_section(self, state: ResearchState) -> bool:
        """
        保证最终报告带有调查层章节，位置固定在正文之后、证据附录之前。

        ## 为什么必须挂在收口入口而不是渲染处

        尽调正文由 `_write_structured_due_diligence_report` 一次性渲染，
        但 Critic 要求修订时会走 `_revise_report`——那是一次 LLM 全文重写。
        章节若只在渲染处插入，一次修订就会把它连同那句"未经核实"的声明
        一起改掉或删掉。**这正是 BC-50 的形态**，所以复用它的解法：
        新增收口区块只改这一个方法。

        ## 顺序为什么是 A 层 → B 层 → 附录

        附录由 `canonicalize_appendix` 另行置底，所以这里只要保证 B 层
        在正文之后即可。三者的相对位置因此恒定，不依赖调用顺序的巧合。

        Returns: 是否改动了正文
        """
        report = state.get("final_report") or ""
        if not report:
            return False
        block = render_investigation_section(state)
        had_marker = INVESTIGATION_MARKER in report
        if not block and not had_marker:
            # 普通研究流程没有 B 层，也没有残留锚点：一个字都不该动。
            # 收敛函数会顺手 strip 正文，那点空白无害，但它会把
            # "本次没有 B 层"记成一次改动——一个恒亮的告警等于没有告警。
            return False
        canonical = canonicalize_investigation_section(report, block)
        changed = canonical != report
        state["final_report"] = canonical
        if changed and block:
            box = state.get("investigation") or {}
            self.logger.info(
                f"[LeadWriter] 调查层章节已"
                f"{'重建' if had_marker else '追加'}"
                f"（图表 {len(box.get('charts') or [])} 张，"
                f"发现 {len(box.get('findings') or [])} 条）")
        elif changed:
            # 有残留锚点但无内容可放回：切除而非留残块，与评级块同一策略
            self.logger.warning("[LeadWriter] 正文中的调查层章节无对应内容，已切除")
        return changed

    def _ensure_evidence_appendix(self, state: ResearchState) -> bool:
        """
        保证最终报告带有证据溯源附录，由代码收口。

        与评级块同一理由：整合与修订都会重写全文，附录若交给模型生成，
        会被改写、被精简、被"综合来看"掉——**一张被模型改过的证据清单
        比没有更危险**，读者会以为它是原始记录。

        Returns: 是否触发了兜底
        """
        checks = state.get("field_checks") or []
        if not checks:
            return False        # 非尽调流程没有清单，不强加附录
        report = state.get("final_report") or ""
        if not report:
            return False
        block = render_appendix(checks, state.get("evidence_store") or {},
                                state.get("completeness") or {},
                                search_failures=state.get("search_failures") or [],
                                as_of=state.get("as_of", "") or "",
                                section_failures=state.get("section_failures") or [])
        canonical = canonicalize_appendix(report, block)
        changed = canonical != report
        state["final_report"] = canonical
        if changed:
            self.logger.info(
                f"[LeadWriter] 证据溯源附录已"
                f"{'重建' if APPENDIX_MARKER in report else '追加'}"
                f"（{len(checks)} 项核查）")
        return changed

    def _ensure_risk_block(self, state: ResearchState) -> bool:
        """
        保证最终报告正文带有评级块，模型丢弃时由代码补回。

        整合与修订都是 LLM 步骤，都会重写全文。若评级只存在于章节草稿，
        它随时可能在这两步中消失——而一份没有等级的尽调报告，
        读者会自行按行文语气脑补一个结论，那正是这套规则化评分要消除的。

        Returns: 是否触发了兜底（用于观测模型丢弃评级的频次）
        """
        assessment = state.get("risk_assessment") or {}
        if not assessment:
            return False
        report = state.get("final_report") or ""
        canonical = _canonicalize_risk_block(report, render_markdown(assessment))
        changed = canonical != report
        state["final_report"] = canonical
        if changed:
            action = "替换为规则引擎版本" if RISK_BLOCK_MARKER in report else "由代码补回"
            self.logger.warning(
                f"[LeadWriter] 报告正文风险评级块已{action}（等级：{assessment.get('level')}）"
            )
        return changed

    def _rerender_due_diligence_report(self, state: ResearchState) -> ResearchState:
        """尽调模式的"修订"：重跑确定性渲染，**模型不参与**（BC-71）。

        ## 这里修的是什么

        `_write_report` 写着一条契约：「LLM 保留候选抽取职责，**不拥有在最终
        报告新增事实、来源或结论的权限**」。但修订这条路整个绕过了它——
        原实现把 `final_report` 截断到 6000 字喂给模型，再用模型的输出
        **整份替换**，然后 `_finalize_report` 只找回三个带锚点的区块。

        2026-08-22 真实运行实测（会话 dd-1787413212824）：

            复核人看到    7284 字，17 行逐项核查，有结论段
            落盘终稿      5147 字，**0 行逐项核查，无结论段**

        没被收口的全部消失——包括报告标题、主体名称、研究截止日，
        以及那句「未核实既不表示存在风险，也不表示不存在风险；
        在补齐证据并重新核验前，**不得据此放款**」。
        那是整个系统的核心免责语义，它在交付件上消失了。

        更麻烦的是第二层后果：`report_draft` 只由确定性渲染器发出，
        修订不发，所以**界面永远停在旧版**——复核人签的 7284 字那份，
        与交付的 5147 字那份不是同一个文件。审计上这是签发不一致。

        ## 为什么是重跑渲染而不是"别让模型删东西"

        提示词约束是软约定（BC 里反复出现的「软约定干硬活」）。
        尽调正文是清单、证据库、评分卡的**纯函数**，重跑一次必然逐字相同，
        因此让它重跑既恢复了正文，也顺带让界面与终稿同步。

        Critic 的意见本来就无法靠改写正文满足——正文没有可改的自由度。
        所以循环照旧跑满迭代再强制转人工，行为不变，只是终稿不再被吃掉。
        """
        unresolved = [f for f in (state.get("critic_feedback") or [])
                      if not f.get("resolved")]
        self.add_message(state, "thought", {
            "agent": self.name,
            "content": "尽调正文由代码从清单与证据渲染，重跑渲染以保证终稿一致",
        })
        self._write_structured_due_diligence_report(state)

        if unresolved:
            # 结构上无法处理的意见必须留痕。此前这三轮迭代静默烧掉，
            # 只在最后留一句"达到最大迭代轮次"，读者无从知道评审说了什么、
            # 以及为什么一条都没被处理（与 BC-51 同一条纪律）。
            detail = "；".join(
                str(f.get("description") or "")[:60] for f in unresolved[:5])
            note = (f"评审提出 {len(unresolved)} 条意见，但尽调正文由代码从清单与"
                    f"证据确定性渲染，撰写环节无权改写，须由人工处理：{detail}")
            errors = state.setdefault("errors", [])
            if note not in errors:
                errors.append(note)
            self.add_message(state, "warning", {"agent": self.name, "content": note})
            self.logger.warning(f"[LeadWriter] {note}")

        state["phase"] = ResearchPhase.REVIEWING.value
        return state

    async def _revise_report(self, state: ResearchState) -> ResearchState:
        """根据反馈修订报告"""
        if state.get("due_diligence_mode"):
            return self._rerender_due_diligence_report(state)

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": "根据审核反馈修订报告..."
        })

        # 收集未解决的问题
        unresolved = [f for f in state["critic_feedback"] if not f.get("resolved")]
        feedback_text = []
        for issue in unresolved:
            feedback_text.append(f"- [{issue.get('severity')}] {issue.get('description')}\n  建议: {issue.get('suggestion')}")

        # 收集新信息（如果有补充搜索）
        new_facts = state["facts"][-5:] if state["facts"] else []
        new_info = "\n".join([f"- {f.get('content', '')[:200]}" for f in new_facts])

        prompt = self.REVISION_PROMPT.format(
            original_content=state.get("final_report", "")[:6000],
            feedback="\n".join(feedback_text) if feedback_text else "无具体反馈",
            new_info=new_info if new_info else "无补充信息"
        )

        response = await self.call_llm(
            system_prompt=(
                "你是负责修订尽调报告的资深编辑。"
                "修订时不得引入未经核实的新信息，不得将未核实事项改写为事实断言。"
            ),
            user_prompt=prompt,
            json_mode=True,
            temperature=0.3,
            # 供应商上限 8192，实测超了直接 400（见 base.MAX_OUTPUT_TOKENS）。
            max_tokens=self.max_output_tokens
        )

        result = self.parse_json_response(response)

        if result and result.get("revised_content"):
            state["final_report"] = result["revised_content"]
            # 修订同样会重写全文，所有代码层收口都要重跑
            self._finalize_report(state)

            # 标记已解决的问题
            for issue_id in result.get("addressed_issues", []):
                for feedback in state["critic_feedback"]:
                    if feedback.get("id") == issue_id:
                        feedback["resolved"] = True

            self.add_message(state, "revision_complete", {
                "agent": self.name,
                "changes_count": len(result.get("changes_made", [])),
                "addressed_issues": result.get("addressed_issues", []),
                "unable_to_address": result.get("unable_to_address", [])
            })

        # 回到审核阶段
        state["phase"] = ResearchPhase.REVIEWING.value

        return state
