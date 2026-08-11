# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
DeepResearch V2.0 - 毒舌评论家 Agent (CriticMaster)

职责：
1. 对抗式质检 - 永远不满意，找出问题
2. 逻辑漏洞检测 - 检查推理链条
3. 幻觉查杀 - 识别无来源或错误的信息
4. 偏见识别 - 发现观点偏颇
"""

import uuid
from typing import Dict, Any, List
from datetime import datetime

from .base import BaseAgent
try:
    from service.claim_scanner import scan_report, format_findings
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.claim_scanner import scan_report, format_findings
from ..state import ResearchState, ResearchPhase


class CriticMaster(BaseAgent):
    """
    毒舌评论家 - 质量守门人

    特点：
    - 对抗式思维：假设一切都有问题
    - 严格的证据要求
    - 逻辑一致性检查
    - 有权打回重写
    """

    REVIEW_PROMPT = """你是信贷机构的**风控复核岗**，负责在尽调报告进入信贷评审会之前把关。
你的职责不是润色文字，而是找出会导致错误授信决策的问题。一旦放过，后果是坏账。

## 审核原则（必须严格执行）
1. **零容忍幻觉**：任何没有明确来源的数据或事实，都是问题
2. **逻辑闭环**：论点必须有论据支撑，论据必须有来源
3. **偏见警惕**：单方面观点、情绪化表达都是问题
4. **时效性**：以上方给定的当前日期为准判断，不得依据你的训练数据截止时间
5. **完整性**：是否遗漏重要方面

## ⛔ 核查清单交叉校验（最高优先级，必须逐项执行）

下方给出本次尽调的核查清单及其**真实状态**。报告正文必须与该状态一致。
清单状态是数据源判定的结果，**报告无权推翻，也无权超出**。

{field_checks}

### 机械校验已由程序完成

{scanner_findings}

### ⛔ 分工：这两类问题**禁止**你报告

`unverified_as_fact` 与 `conflict_silently_resolved` 已由程序确定性判定完毕，
**你不得输出这两个 issue_type**。实测表明你在这两类上的判定不稳定
（同一份报告三次给出不同结论，且会误读清单状态），而程序判定零方差。
重复报告只会制造噪音并与程序结论冲突。

**你唯一需要判定的清单类问题是 `unsupported_risk_conclusion`**：
风险评级或授信建议是否建立在已核实的字段之上。
这需要通盘理解报告的论证链条，程序做不到，是你的价值所在。

典型场景：报告写"该公司资信良好、经营合规、风险可控，综合评定风险等级为低"，
未提任何具体字段名，但多项必查项未核实——这种整体性评价缺乏清单支撑。

请按此分工检出问题：

**A. `unverified_as_fact`（把未核实字段当作事实断言）**
   对每一个状态为 `未核实` 的字段，检查报告中是否出现了针对该字段的实质性结论。
   - 违规示例：涉诉记录状态为「未核实」，报告却写"该企业无重大诉讼"或"司法风险较低"
   - 注意**隐含断言**同样违规："经营合规""风险可控""资信良好"这类评价性表述，
     如果其依据的字段未核实，同样是把未核实当事实
   - 正确写法只能是"该项未核实（原因：…）"
   - **反向陷阱**：`未核实` 既不等于"无记录"，也不等于"有记录"。
     报告不得向任何一个方向倾斜
   - 严重程度：critical

**B. `conflict_silently_resolved`（把冲突数据单方面采信）**
   对每一个状态为 `数据冲突` 的字段，检查报告是否只采信了其中一个来源而未披露冲突。
   - 违规示例：注册资本状态为「数据冲突」（工商称实缴5000万，财报称1500万），
     报告却写"注册资本5000万元且已全额实缴"
   - 正确写法必须并列披露各来源取值，并指出需人工核实
   - 严重程度：critical

**C. `unsupported_risk_conclusion`（风险结论缺乏清单支撑）**
   检查报告的风险评级与授信建议，是否建立在已核实的字段之上。
   - 违规示例：多个必查项未核实，却直接给出"风险等级：低，建议核准授信"
   - 信息缺口必须在结论中体现，不得被忽略
   - 严重程度：major（若未核实项涉及司法维度，则为 critical）

**注意「已核实：经查询，无相关记录」不是未核实**——那是数据源查询后确认无记录的
正面结论，报告据此写"经查询未发现失信记录"是**正确**的，不要误报。

### 判定这三类问题时的纪律（避免误报）

误报的代价同样高：一个见谁都咬的检查器会被使用者忽略，等于没有。因此：

1. **只在报告确实越界时才报**。报告如实写"该项未核实（原因：…）"、
   如实并列披露冲突、在结论中明确体现信息缺口——这些都是**正确**处理，不得报违规。
2. **不要因为"表述可以更严谨"而报 critical**。如果你的描述里出现
   "基本正确""处理得当""但建议…"这类措辞，说明它至多是 minor。
   critical 只留给会**导致错误授信决策**的问题。
3. **不要因为报告缺少某个章节而报这三类问题**。章节缺失属于 `incomplete`，
   与清单校验无关。你收到的可能只是报告的一部分。
4. **拒绝给出确定性结论本身不是缺陷**。当必查项大量未核实时，
   报告写"不具备定级条件，建议补充核查后再评审"是**风控上正确**的做法，
   不得以"未给出风险评级"为由报 `unsupported_risk_conclusion`。
   该类型针对的是**给了结论却无支撑**，不是**没给结论**。
5. **报告引述核实率统计不是断言**。诸如"必查项核实 9/15""司法维度 0/3 已核实"
   这类表述，是在如实转述本清单的统计结果，属于正确披露信息缺口的做法，
   不得据此判定报告"对未核实项作出了断言"。真正要查的是报告有没有
   对字段的**实质内容**下结论（如"无涉诉记录""司法风险较低"）。

## 研究问题
{query}

## 研究大纲
{outline}

## 待审核内容

### 章节草稿
{draft_content}

### 引用的事实
{facts}

### 使用的数据点
{data_points}

## 任务
逐条审核上述内容，找出所有问题。先完成核查清单交叉校验，再做常规审核。

## 输出格式
```json
{{
    "overall_assessment": {{
        "quality_score": 1-10,
        "verdict": "pass/needs_revision/major_issues",
        "summary": "整体评估摘要"
    }},
    "issues": [
        {{
            "id": "issue_1",
            "target_section": "章节ID或'全局'",
            "issue_type": "unverified_as_fact/conflict_silently_resolved/unsupported_risk_conclusion/missing_source/logic_error/bias/hallucination/outdated/incomplete",
            "severity": "critical/major/minor",
            "location": "具体位置描述",
            "description": "问题详细描述",
            "evidence": "为什么这是问题的证据",
            "suggestion": "具体的修改建议",
            "requires_new_search": true或false,
            "search_query": "如果需要补充搜索，建议的关键词"
        }}
    ],
    "fact_check_results": [
        {{
            "fact_id": "事实ID",
            "status": "verified/unverified/suspicious/false",
            "reason": "判断理由"
        }}
    ],
    "missing_aspects": ["报告中遗漏的重要方面"],
    "strength_points": ["报告中做得好的地方"]
}}
```

## 严重程度说明
- critical: 必须修复，否则报告不可用（如：核心数据错误、严重幻觉）
- major: 强烈建议修复，影响报告质量（如：缺少来源、逻辑漏洞）
- minor: 建议修复，提升报告质量（如：表述不够精确）

## 评分标准（1-10分制）
- 9-10分：优秀，几乎无问题，可直接发布
- 7-8分：良好，有小问题但不影响整体质量，审核通过（verdict=pass）
- 5-6分：一般，有明显问题需要修订
- 3-4分：较差，问题较多，需要大幅修改
- 1-2分：很差，存在严重问题或大量错误

注意：quality_score >= 7 时才能设置 verdict 为 "pass"

开始你的审核："""

    FINAL_CHECK_PROMPT = """你是最终质量把关人。这是修订后的研究报告。

## 原始问题
{query}

## 之前的问题
{previous_issues}

## 修订后的内容
{revised_content}

## 任务
检查之前的问题是否已解决，是否有新问题产生。

输出JSON：
```json
{{
    "resolved_issues": ["已解决的问题ID列表"],
    "unresolved_issues": ["未解决的问题ID列表"],
    "new_issues": [{{
        "description": "新发现的问题",
        "severity": "critical/major/minor"
    }}],
    "final_verdict": "approved/needs_more_work",
    "final_score": 1-10,
    "publication_readiness": "ready/almost_ready/not_ready",
    "final_comments": "最终评语"
}}
```"""

    @staticmethod
    def _format_checklist_for_review(state: ResearchState) -> str:
        """
        渲染核查清单供交叉校验。

        与 Writer 的渲染有意不同：这里按状态分组而非按章节，
        让「哪些字段未核实」一目了然——复核岗要检查的正是报告有没有
        越过这条线，分散在各章节里反而不利于比对。
        """
        checks = state.get("field_checks") or []
        if not checks:
            return "（本次尽调无核查清单，跳过交叉校验）"

        groups = {"unverified": [], "conflicting": [], "verified_with_value": [],
                  "verified_no_record": [], "not_applicable": []}
        for c in checks:
            st = c.get("status")
            if st == "verified":
                key = ("verified_no_record" if c.get("value") == "经查询，无相关记录"
                       else "verified_with_value")
            else:
                key = st if st in groups else "unverified"
            groups[key].append(c)

        out = []

        def _tag(c):
            return "必查" if c.get("required") else "选查"

        if groups["unverified"]:
            out.append("### ⛔ 未核实字段（报告不得对这些字段给出任何实质性结论）")
            for c in groups["unverified"]:
                out.append(f"- [{_tag(c)}] {c['field_name']}｜原因：{c.get('failure_reason') or '未说明'}")
        if groups["conflicting"]:
            out.append("\n### ⚠️ 数据冲突字段（报告必须并列披露各来源，不得单方面采信）")
            for c in groups["conflicting"]:
                detail = "；".join(
                    f"{d.get('source')}={d.get('value')}" for d in (c.get("conflict_detail") or [])
                )
                out.append(f"- [{_tag(c)}] {c['field_name']}｜{detail}")
        if groups["verified_no_record"]:
            out.append("\n### ✅ 已核实·经查询无记录（正面结论，报告如此表述是正确的，不要误报）")
            out.append("- " + "、".join(c["field_name"] for c in groups["verified_no_record"]))
        if groups["verified_with_value"]:
            out.append("\n### ✅ 已核实·有具体内容")
            out.append("- " + "、".join(c["field_name"] for c in groups["verified_with_value"]))
        if groups["not_applicable"]:
            out.append("\n### ➖ 不适用于本主体")
            out.append("- " + "、".join(c["field_name"] for c in groups["not_applicable"]))

        comp = state.get("completeness") or {}
        if comp:
            out.append(
                f"\n**必查项核实率：{comp.get('required_verified')}/{comp.get('required_total')}"
                f"（{comp.get('verified_rate', 0):.0%}）**"
            )
        return "\n".join(out)

    def __init__(self, llm_api_key: str, llm_base_url: str, model: str = "qwen-max"):
        super().__init__(
            name="CriticMaster",
            role="毒舌评论家",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model
        )

    def merge_review(self, state: ResearchState, llm_result: Any) -> Dict[str, Any]:
        """
        合并确定性扫描与 LLM 审核结果。

        ⚠️ 两条设计原则，都来自实测教训：

        1. **扫描器无条件执行，不受 LLM 成败影响。**
           此前扫描代码被包在 `if review_result is not None:` 里，
           LLM 调用失败或 JSON 解析失败时，确定性检出会**全部丢失**——
           可靠的部分依赖了不可靠的部分，方向是反的。

        2. **本方法必须同时被生产流程与评测调用。**
           此前扫描与过滤只存在于 process()，而评测直接调 _review_content()，
           导致评测测的是"LLM 单独表现"，却被当成"系统整体表现"来解读，
           进而把提示词带来的改善错误归因给代码过滤。
           把合并逻辑收敛到一处，两边走同一路径，评测才对得上生产。
        """
        # —— 确定性扫描：生产中始终执行；仅显式消融时关闭 ——
        # 扫描器与 LLM 必须审核同一份文本，否则 final_report 中由整合步骤
        # 新增的断言可能只被扫描器看见，而草稿中的旧句子只被 LLM 看见。
        text = self._content_for_review(state)
        scan_issues = []
        scan_findings = (
            [] if getattr(self, "_ablate_scanner", False)
            else scan_report(state.get("field_checks") or [], text)
        )
        for f in scan_findings:
            scan_issues.append({
                "target_section": f.get("field_id", ""),
                "issue_type": f["issue_type"],
                "severity": f["severity"],
                "location": f.get("sentence", "")[:60],
                "description": f["description"],
                "evidence": f"清单状态={f['status']}；命中断言词「{f['matched_claim']}」",
                "suggestion": "改写为如实披露该项状态的表述",
                "requires_new_search": False,
                "detected_by": "scanner",
            })

        # —— LLM 结果：尽力而为，失败不影响扫描结论 ——
        # 判据要覆盖三种失败形态，不能只判 None：
        #   1) API 异常 → 调用方传入 None
        #   2) JSON 解析失败 → 可能返回 {} 或 None
        #   3) 解析出对象但缺关键字段（既无 issues 也无 overall_assessment）
        #      → 结构不可用，等同失败
        # 此前只判 isinstance(dict)，空字典会被当成有效结果，degraded 永远不置位。
        _usable = (
            isinstance(llm_result, dict)
            and ("issues" in llm_result or "overall_assessment" in llm_result)
        )
        if not _usable:
            if scan_issues:
                self.logger.warning(
                    f"[CriticMaster] LLM 审核不可用，仅保留 {len(scan_issues)} 条确定性扫描结论"
                )
            degraded = {
                "overall_assessment": {
                    "quality_score": 5.0,
                    # LLM 不可用时绝不能是 pass：审核根本没完整执行过
                    "verdict": "needs_revision",
                    "summary": "LLM 审核不可用；结论仅基于确定性扫描"
                             + ("，已发现清单越界表述" if scan_issues else "，未发现清单越界表述"),
                },
                "issues": scan_issues,
                "missing_aspects": [],
                "degraded": True,   # 显式标记降级，不得静默（BC-02 的教训）
            }
            # 降级路径同样要过扫描器闸门，否则 LLM 一挂就绕开了门控
            return self._enforce_scanner_gate(degraded, scan_issues)

        # 强制分工：这两类由扫描器独占，丢弃 LLM 的同类输出
        scanner_owned = (
            set() if getattr(self, "_ablate_scanner", False)
            else {"unverified_as_fact", "conflict_silently_resolved"}
        )
        llm_issues, dropped = [], 0
        for issue in llm_result.get("issues", []):
            if issue.get("issue_type") in scanner_owned:
                dropped += 1
                continue
            llm_issues.append(issue)
        if dropped:
            self.logger.info(f"[CriticMaster] 丢弃 {dropped} 条 LLM 输出的扫描器独占类型问题")

        llm_result["issues"] = scan_issues + llm_issues
        return self._enforce_scanner_gate(llm_result, scan_issues)

    @staticmethod
    def _content_for_review(state: ResearchState) -> str:
        """
        返回 Critic 唯一的待审核文本。

        Writer 整合/修订后，final_report 才是最终交付物，也可能包含草稿中
        从未出现的新断言，因此优先审核它；仅在最终报告尚未生成时退回章节草稿。
        确定性扫描、LLM 提示词和评测必须共用本入口。
        """
        final_report = state.get("final_report") or ""
        if final_report.strip():
            return final_report

        parts = []
        outline = state.get("outline") or []
        for section_id, content in (state.get("draft_sections") or {}).items():
            section = next((s for s in outline if s.get("id") == section_id), {})
            parts.append(f"## {section.get('title', section_id)}\n{content}")
        return "\n\n".join(parts) if parts else "（暂无内容）"

    def _agent_cfg(self):
        """取本 Agent 的模型配置；取不到时返回 None 由调用方兜底"""
        try:
            try:
                from config.llm_config import get_config
            except ImportError:
                from app.config.llm_config import get_config
            return get_config().agents.critic
        except Exception as e:
            self.logger.warning(f"[CriticMaster] 读取模型配置失败，使用兜底值: {e}")
            return None

    def _cfg_temperature(self) -> float:
        """审核是判定任务而非生成任务，温度应尽可能低以减少方差"""
        override = getattr(self, "_eval_temperature", None)   # 评测对比实验用
        if override is not None:
            return float(override)
        cfg = self._agent_cfg()
        return float(getattr(cfg, "temperature", 0.0) if cfg else 0.0)

    def _cfg_max_tokens(self) -> int:
        override = getattr(self, "_eval_max_tokens", None)
        if override is not None:
            return int(override)
        cfg = self._agent_cfg()
        return int(getattr(cfg, "max_tokens", 8000) if cfg else 8000)

    @staticmethod
    def _enforce_scanner_gate(result: Dict[str, Any], scan_issues: List[Dict]) -> Dict[str, Any]:
        """
        扫描器 critical 强制门控裁决。

        ⚠️ 修复的缺陷：此前扫描结论只是被追加进 `issues` 列表，
        却**不影响 `verdict`**。若 LLM 返回 `pass`，流程照样把状态置为 COMPLETED——
        扫描器抓到了"把未核实写成无记录"，报告仍然放行。

        这是整套确定性检查在最后一步失效：**实现了机制，但机制不约束结果。**
        确定性结论必须能否决模型的裁决，否则它只是一条日志。

        规则：
          - 存在 scanner critical → verdict 不得为 pass，quality_score 上限 3
          - 存在 scanner major    → verdict 不得为 pass，quality_score 上限 6
        （评分上限与提示词中"≥7 才可 pass"的规则保持一致，避免下游按分数放行）
        """
        crit = [i for i in scan_issues if i.get("severity") == "critical"]
        major = [i for i in scan_issues if i.get("severity") == "major"]
        if not crit and not major:
            return result

        oa = result.setdefault("overall_assessment", {})
        old_verdict = oa.get("verdict")
        old_score = oa.get("quality_score")

        if crit:
            oa["verdict"] = "major_issues"
            cap = 3.0
        else:
            if oa.get("verdict") == "pass":
                oa["verdict"] = "needs_revision"
            cap = 6.0

        try:
            score = float(old_score)
        except (TypeError, ValueError):
            score = cap
        oa["quality_score"] = min(score, cap)

        oa["scanner_gate_applied"] = {
            "critical": len(crit), "major": len(major),
            "original_verdict": old_verdict, "original_score": old_score,
            "fields": [i.get("target_section") for i in crit + major],
        }
        return result

    async def process(self, state: ResearchState) -> ResearchState:
        """处理入口"""
        self.logger.info(f"[CriticMaster] ========== process 开始 ==========")
        self.logger.info(f"[CriticMaster] phase: {state['phase']}, final_report 长度: {len(state.get('final_report', ''))}")

        if state["phase"] != ResearchPhase.REVIEWING.value:
            self.logger.info(f"[CriticMaster] phase 不是 REVIEWING，跳过")
            return state

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": "开始严格审核研究报告，准备找出所有问题..."
        })

        # 执行审核。
        # ⚠️ 必须捕获异常：call_llm 的 API 失败会直接向上抛，
        # 若不捕获，merge_review() 根本不会执行，确定性扫描结论随之丢失——
        # 这正是"可靠部分依赖不可靠部分"的另一条路径。
        self.logger.info(f"[CriticMaster] 开始调用 _review_content...")
        try:
            review_result = await self._review_content(state)
        except Exception as e:
            self.logger.error(f"[CriticMaster] LLM 审核调用失败: {type(e).__name__}: {e}")
            state.setdefault("errors", []).append(
                f"CriticMaster LLM 审核失败: {type(e).__name__}: {e}"
            )
            review_result = None
        self.logger.info(f"[CriticMaster] 审核完成，结果: {bool(review_result)}")

        review_result = self.merge_review(state, review_result)

        if review_result is not None:
            # 记录反馈
            for issue in review_result.get("issues", []):
                issue["id"] = f"issue_{uuid.uuid4().hex[:8]}"
                issue["resolved"] = False
                state["critic_feedback"].append(issue)

            # 更新质量分数。
            # 提示词声明取值 1-10，但模型不保证遵守——实测返回过 -1。
            # 下游（阈值判断、评测、前端展示）都依赖这个范围，必须校验后再落库。
            raw_score = review_result.get("overall_assessment", {}).get("quality_score", 0.0)
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                self.logger.warning(f"quality_score 非数值: {raw_score!r}，按 0 处理")
                score = 0.0
            if not (1.0 <= score <= 10.0):
                self.logger.warning(f"quality_score 越界: {score}，裁剪到 [1,10]")
                score = min(10.0, max(1.0, score))
            state["quality_score"] = score
            state["unresolved_issues"] = len([i for i in review_result.get("issues", []) if i.get("severity") in ["critical", "major"]])

            # 发送审核结果
            self.add_message(state, "review", {
                "agent": self.name,
                "verdict": review_result.get("overall_assessment", {}).get("verdict"),
                "quality_score": state["quality_score"],
                "issues_count": len(review_result.get("issues", [])),
                "critical_issues": len([i for i in review_result.get("issues", []) if i.get("severity") == "critical"]),
                "major_issues": len([i for i in review_result.get("issues", []) if i.get("severity") == "major"]),
                "summary": review_result.get("overall_assessment", {}).get("summary", ""),
                "missing_aspects": review_result.get("missing_aspects", [])
            })

            # 如果有严重问题，发送具体反馈
            critical_issues = [i for i in review_result.get("issues", []) if i.get("severity") == "critical"]
            for issue in critical_issues[:3]:  # 最多展示3个严重问题
                self.add_message(state, "critic_feedback", {
                    "agent": self.name,
                    "issue_type": issue.get("issue_type"),
                    "severity": issue.get("severity"),
                    "description": issue.get("description"),
                    "suggestion": issue.get("suggestion")
                })

            # 决定下一步 - 智能路由
            verdict = review_result.get("overall_assessment", {}).get("verdict", "needs_revision")

            if verdict == "pass":
                state["phase"] = ResearchPhase.COMPLETED.value
            elif state["iteration"] >= state["max_iterations"]:
                # 达到最大迭代次数，强制完成
                state["phase"] = ResearchPhase.COMPLETED.value
                self.add_message(state, "warning", {
                    "agent": self.name,
                    "content": "已达最大迭代次数，部分问题可能未解决"
                })
            else:
                # 智能路由：判断是需要补充搜索还是仅修改文字
                needs_new_search = self._analyze_issues_for_routing(review_result)

                if needs_new_search["should_research"]:
                    # 需要补充搜索 -> 回到研究阶段
                    state["phase"] = ResearchPhase.RE_RESEARCHING.value
                    state["pending_search_queries"] = needs_new_search["search_queries"]
                    self.add_message(state, "thought", {
                        "agent": self.name,
                        "content": f"发现信息缺失问题，需要补充搜索: {', '.join(needs_new_search['search_queries'][:3])}"
                    })
                else:
                    # 仅需要文字修改 -> 修订阶段
                    state["phase"] = ResearchPhase.REVISING.value

                state["iteration"] += 1

        return state

    def _analyze_issues_for_routing(self, review_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析问题类型，决定路由方向

        Returns:
            {
                "should_research": bool,  # 是否需要重新搜索
                "search_queries": List[str]  # 建议的搜索查询
            }
        """
        issues = review_result.get("issues", [])
        missing_aspects = review_result.get("missing_aspects", [])

        # 需要补充搜索的问题类型。
        # 注意三类清单校验问题**不在此列**：
        #   unverified_as_fact / conflict_silently_resolved —— 问题在于报告"写多了"，
        #     字段本就取不到，再搜一遍也拿不到，正确做法是改写表述而非补充检索
        #   unsupported_risk_conclusion —— 需要的是在结论中体现信息缺口，同样是改写
        # 把它们误判为"需补充检索"会导致无效的重复搜索（V1 空转的同类问题）
        research_needed_types = {"missing_source", "incomplete", "outdated"}

        search_queries = []
        research_issues_count = 0

        for issue in issues:
            issue_type = issue.get("issue_type", "")
            severity = issue.get("severity", "minor")

            # 检查是否是需要搜索的问题类型
            if issue_type in research_needed_types and severity in ["critical", "major"]:
                research_issues_count += 1

                # 收集搜索建议
                if issue.get("requires_new_search") and issue.get("search_query"):
                    search_queries.append(issue["search_query"])

        # 添加遗漏方面的搜索查询
        for aspect in missing_aspects[:3]:
            search_queries.append(aspect)

        # 决策：如果有超过30%的严重问题需要搜索，或者有明确的搜索建议，则回到搜索阶段
        total_critical_major = len([i for i in issues if i.get("severity") in ["critical", "major"]])
        should_research = (
            len(search_queries) > 0 and
            (research_issues_count > 0 or len(missing_aspects) > 0) and
            (total_critical_major == 0 or research_issues_count / max(total_critical_major, 1) > 0.3)
        )

        return {
            "should_research": should_research,
            "search_queries": list(set(search_queries))[:5]  # 去重，最多5个查询
        }

    async def _review_content(self, state: ResearchState) -> Dict[str, Any]:
        """审核内容"""
        self.logger.info(f"[CriticMaster] _review_content 开始")

        # 最终报告是实际交付物；扫描器与 LLM 共用同一文本入口。
        draft_content = self._content_for_review(state)

        self.logger.info(f"[CriticMaster] 待审核内容长度: {len(draft_content)}")

        # 准备事实列表
        facts_summary = []
        for fact in state["facts"][:20]:
            facts_summary.append(f"- [{fact.get('id')}] {fact.get('content', '')[:150]} (来源: {fact.get('source_name')}, 可信度: {fact.get('credibility_score')})")

        # 准备数据点列表
        data_summary = []
        for dp in state["data_points"][:15]:
            data_summary.append(f"- {dp.get('name')}: {dp.get('value')} {dp.get('unit', '')} (来源: {dp.get('source')})")

        # 格式化大纲
        outline_summary = []
        for section in state["outline"]:
            outline_summary.append(f"- {section.get('id')}: {section.get('title')} ({section.get('status', 'pending')})")

        # 确定性扫描：字段名与断言词共现的显式违规由程序判定，不经 LLM。
        # 实测该类判定交给模型时 12/18 对照用例结果不稳定（见 BADCASES.md BC-14）；
        # 改为程序判定后检出 7/7、误报 0/18 且完全可复现。
        # _ablate_scanner 为 True 时**完全禁用**扫描器：不执行扫描、
        # 不注入提示词、不合并结果、不做类型过滤。
        # 此前 --ablate scanner 只绕过了结果合并，扫描结论仍通过提示词
        # 到达 LLM，导致"扫描器独立贡献"根本没被测到（外部评审第①条）。
        scan_findings = (
            [] if getattr(self, "_ablate_scanner", False)
            else scan_report(state.get("field_checks") or [], draft_content)
        )
        if scan_findings:
            self.logger.info(f"[CriticMaster] 确定性扫描检出 {len(scan_findings)} 项显式违规")

        prompt = self.REVIEW_PROMPT.format(
            query=state["query"],
            outline="\n".join(outline_summary),
            draft_content=draft_content[:8000],  # 限制长度
            facts="\n".join(facts_summary) if facts_summary else "（暂无事实记录）",
            data_points="\n".join(data_summary) if data_summary else "（暂无数据点）",
            field_checks=self._format_checklist_for_review(state),
            scanner_findings=format_findings(scan_findings)
        )

        self.logger.info(f"[CriticMaster] 调用 LLM 进行审核...")
        response = await self.call_llm(
            system_prompt=(
                "你是信贷机构的风控复核岗，负责在尽调报告进入评审会前把关。"
                "你的首要任务是核查清单交叉校验：报告正文不得超出清单已核实的范围。"
                "放过一处未核实当事实的表述，可能导致错误授信与坏账。"
            ),
            user_prompt=prompt,
            json_mode=True,
            # model / temperature / max_tokens 统一由 AgentModelConfig 提供。
            # 此前三者各行其是：model 读配置、temperature 硬编码 0.2、
            # max_tokens 硬编码 16000，导致配置文件里写的值形同虚设
            # （与 BC-05 同类：配置写了但不生效）。
            temperature=self._cfg_temperature(),
            max_tokens=self._cfg_max_tokens(),
        )
        self.logger.info(f"[CriticMaster] LLM 响应长度: {len(response)}")

        result = self.parse_json_response(response)
        self.logger.info(f"[CriticMaster] JSON 解析结果: {bool(result)}, verdict: {result.get('overall_assessment', {}).get('verdict') if result else 'N/A'}")
        return result

    async def final_check(self, state: ResearchState) -> Dict[str, Any]:
        """最终检查"""
        # 收集之前的问题
        previous_issues = []
        for issue in state["critic_feedback"]:
            if not issue.get("resolved"):
                previous_issues.append(f"- [{issue.get('severity')}] {issue.get('description')}")

        prompt = self.FINAL_CHECK_PROMPT.format(
            query=state["query"],
            previous_issues="\n".join(previous_issues) if previous_issues else "无之前的问题",
            revised_content=state.get("final_report", "")[:8000]
        )

        response = await self.call_llm(
            system_prompt="你是最终质量把关人。",
            user_prompt=prompt,
            json_mode=True
        )

        return self.parse_json_response(response)
