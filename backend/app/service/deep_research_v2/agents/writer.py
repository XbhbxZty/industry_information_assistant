# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

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

1. **素材中没有的信息，一律写"未核实"**，格式：`该项未核实（原因：数据源未提供）`
2. **禁止推断填充**。例如素材只给了股东名单而没有实际控制人认定，
   不得自行推断"周某为实际控制人"，必须写"实际控制人未核实"
3. **禁止用"暂无""无"替代"未核实"**。"无对外担保"和"对外担保未核实"
   是完全不同的结论——前者是事实断言，后者是信息缺口。写错会误导审批
4. **禁止把缺失当利好**。查不到负面信息 ≠ 没有负面信息
5. 每个事实性陈述后标注来源与获取时间，格式：`（来源：工商登记信息，2026-08-09）`

## 写作要求
1. **客观中立**：陈述事实与风险，不做营销式表述
2. **风险导向**：尽调的目的是发现问题，对异常指标要明确指出
3. **数据支撑**：涉及数字的结论必须引用素材中的具体数值
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
  同期经营性现金流由+1240万元转为-1580万元（来源：企业自报未经审计，2026-08-09），
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

## 关键要求

### 1. 标题编号规则（必须严格遵守）
- 一级标题：1、2、3...（如：1 企业基本情况）
- 二级标题：1.1、1.2、2.1...（如：4.1 偿债能力）
- 三级标题：1.1.1、1.1.2...
- **禁止标题重复**：每个标题必须唯一，不要在正文中重复章节标题

### 2. 引用格式规则
- 内部数据源：在事实后标注 `（来源：工商登记信息，2026-08-09）`
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
        related_facts = [f for f in state["facts"] if section_id in f.get("related_sections", [])]
        if not related_facts:
            # 如果没有特定关联，使用所有事实
            related_facts = state["facts"][:10]

        # 格式化事实
        facts_text = []
        for fact in related_facts:
            facts_text.append(f"- {fact.get('content')} (来源: {fact.get('source_name')}, 可信度: {fact.get('credibility_score')})")

        # 格式化数据点
        data_text = []
        for dp in state["data_points"][:10]:
            data_text.append(f"- {dp.get('name')}: {dp.get('value')} {dp.get('unit', '')} ({dp.get('year', 'N/A')})")

        # 格式化图表信息
        charts_info = []
        for chart in state["charts"]:
            if chart.get("section_id") == section_id:
                charts_info.append(f"- 图表: {chart.get('title')} (ID: {chart.get('id')})")

        prompt = self.SECTION_WRITING_PROMPT.format(
            query=state["query"],
            section_title=section.get("title", ""),
            section_description=section.get("description", ""),
            section_type=section.get("section_type", "mixed"),
            facts="\n".join(facts_text) if facts_text else "（暂无相关事实）",
            data_points="\n".join(data_text) if data_text else "（暂无数据点）",
            insights="\n".join([f"- {i}" for i in state["insights"][:5]]) if state["insights"] else "（暂无洞察）",
            charts_info="\n".join(charts_info) if charts_info else "（暂无图表）"
        )

        response = await self.call_llm(
            system_prompt=(
                "你是信贷机构的尽职调查分析师，撰写贷前尽调报告。"
                "只依据给定素材写作，素材中没有的信息一律标注为未核实，严禁推断填充。"
            ),
            user_prompt=prompt,
            json_mode=True,
            temperature=0.4,
            max_tokens=16000  # 拉满到最大值
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
            max_tokens=16000  # 拉满到最大值
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

        # 发送报告完成事件 - 包含完整报告内容用于前端流式显示
        self.add_message(state, "report_draft", {
            "agent": self.name,
            "content": state["final_report"],  # 完整报告内容
            "executive_summary": executive_summary,
            "conclusions": conclusions,
            "word_count": len(state["final_report"]),
            "references_count": len(state["references"])
        })

    async def _revise_report(self, state: ResearchState) -> ResearchState:
        """根据反馈修订报告"""
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
            max_tokens=16000  # 拉满到最大值
        )

        result = self.parse_json_response(response)

        if result and result.get("revised_content"):
            state["final_report"] = result["revised_content"]

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
