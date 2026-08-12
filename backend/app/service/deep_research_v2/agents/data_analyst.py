# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
DeepResearch V2.0 - 数据分析师 Agent (DataAnalyst)

职责：
1. 从搜索结果中提取结构化数据
2. 构建知识图谱(实体+关系)
3. 生成可视化图表配置(ECharts)
4. 识别数据趋势和洞察
"""

import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime

from .base import BaseAgent
from ..state import ResearchState, ResearchPhase

try:
    from service.risk_scorecard import (
        score as score_risk, unratable, apply_provenance_gate, PROFILE_BACKED_FIELDS,
    )
    from service.company_profile import replay_from_profile, verify_field_checks
    from service.verification import build_scoring_view
    from config.dd_checklist import compute_completeness
    from config.verification_policy import POLICY
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.risk_scorecard import (
        score as score_risk, unratable, apply_provenance_gate, PROFILE_BACKED_FIELDS,
    )
    from app.service.company_profile import replay_from_profile, verify_field_checks
    from app.service.verification import build_scoring_view
    from app.config.dd_checklist import compute_completeness
    from app.config.verification_policy import POLICY


class DataAnalyst(BaseAgent):
    """
    数据分析师 - 专注于数据提取、知识图谱和可视化

    特点：
    - 从文本中提取结构化数据点
    - 构建实体关系知识图谱
    - 生成ECharts可视化配置
    - 识别趋势和洞察
    """

    # 数据提取 Prompt
    DATA_EXTRACTION_PROMPT = """你是专业的数据分析师，擅长从文本中提取结构化数据。

## 研究主题
{query}

## 搜索结果
{search_results}

## 任务
从以上搜索结果中提取所有可量化的数据点，包括：
1. 市场规模数据（金额、单位、年份）
2. 增长率数据（百分比、时间段）
3. 市场份额数据（企业/领域、占比）
4. 排名数据（企业、产品、技术）
5. 时间序列数据（同一指标在不同年份的值）

## 输出要求
请输出JSON格式：
```json
{{
    "data_points": [
        {{
            "id": "dp_001",
            "name": "中国AI市场规模",
            "value": 5000,
            "unit": "亿元",
            "year": 2024,
            "source": "艾瑞咨询",
            "category": "market_size",
            "confidence": 0.9
        }}
    ],
    "time_series": [
        {{
            "id": "ts_001",
            "metric": "AI市场规模",
            "unit": "亿元",
            "data": [
                {{"year": 2020, "value": 3200}},
                {{"year": 2021, "value": 4100}},
                {{"year": 2024, "value": 8500}}
            ],
            "source": "艾瑞咨询"
        }}
    ],
    "distributions": [
        {{
            "id": "dist_001",
            "name": "细分领域市场份额",
            "year": 2024,
            "data": [
                {{"category": "计算机视觉", "value": 32, "unit": "%"}},
                {{"category": "自然语言处理", "value": 28, "unit": "%"}}
            ],
            "source": "IDC"
        }}
    ],
    "insights": [
        "中国AI市场规模在2024年突破5000亿元",
        "计算机视觉是最大的细分领域，占比32%"
    ]
}}
```

注意：
- 只提取有明确来源的数据
- confidence表示数据可信度(0-1)
- 如果没有找到相关数据，返回空数组"""

    # 知识图谱构建 Prompt
    KNOWLEDGE_GRAPH_PROMPT = """你是知识图谱专家，擅长从文本中提取实体和关系。

## 研究主题
{query}

## 文本内容
{content}

## 任务
从以上文本中提取实体和关系，构建知识图谱。

## 实体类型定义
- core: 核心概念（如：人工智能、大模型）
- tech: 技术（如：深度学习、计算机视觉、NLP）
- company: 企业（如：百度、阿里巴巴、华为）
- policy: 政策（如：AI发展规划、数据安全法）
- product: 产品（如：ChatGPT、文心一言）
- person: 人物（如：创始人、CEO）

## 输出要求
请输出JSON格式：
```json
{{
    "nodes": [
        {{"id": "ai", "name": "人工智能", "type": "core", "importance": 10}},
        {{"id": "baidu", "name": "百度", "type": "company", "importance": 8}},
        {{"id": "cv", "name": "计算机视觉", "type": "tech", "importance": 7}}
    ],
    "edges": [
        {{"source": "baidu", "target": "ai", "relation": "布局"}},
        {{"source": "cv", "target": "ai", "relation": "属于"}},
        {{"source": "baidu", "target": "cv", "relation": "研发"}}
    ]
}}
```

注意：
- importance范围1-10，表示节点重要性
- 核心概念(core)的importance最高
- 提取5-15个最重要的实体
- 关系要简洁，2-4个字"""

    # 图表生成 Prompt
    CHART_GENERATION_PROMPT = """你是数据可视化专家，擅长生成ECharts图表配置。

## 研究主题
{query}

## 可用数据
{data}

## 任务
根据数据生成合适的ECharts图表配置，选择最能展示数据特点的图表类型。

## 图表类型选择规则
- 时间序列数据 → line (折线图)
- 分类比较数据 → bar (柱状图)
- 占比分布数据 → pie (饼图)
- 进度/百分比 → horizontal_bar (横向进度条)
- 多维对比 → radar (雷达图)

## 设计要求
1. 配色使用简约专业色系：
   - 主色：#1677ff (蓝)
   - 辅助色：#52c41a (绿), #722ed1 (紫), #fa8c16 (橙), #eb2f96 (粉)
2. 标题简洁明了
3. 不要过多装饰，保持简约

## 输出要求
请输出JSON格式：
```json
{{
    "charts": [
        {{
            "id": "chart_001",
            "title": "中国AI市场规模",
            "subtitle": "2020-2024年市场规模（亿元）",
            "type": "line",
            "echarts_option": {{
                "grid": {{"left": "3%", "right": "4%", "bottom": "3%", "containLabel": true}},
                "xAxis": {{
                    "type": "category",
                    "data": ["2020", "2021", "2022", "2023", "2024"],
                    "axisLine": {{"lineStyle": {{"color": "#e8e8e8"}}}},
                    "axisLabel": {{"color": "#666"}}
                }},
                "yAxis": {{
                    "type": "value",
                    "axisLine": {{"show": false}},
                    "splitLine": {{"lineStyle": {{"color": "#f0f0f0"}}}}
                }},
                "series": [{{
                    "type": "line",
                    "data": [3200, 4100, 5200, 6800, 8500],
                    "smooth": true,
                    "symbol": "circle",
                    "symbolSize": 8,
                    "itemStyle": {{"color": "#1677ff"}},
                    "lineStyle": {{"width": 3}},
                    "areaStyle": {{"color": {{"type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1, "colorStops": [{{"offset": 0, "color": "rgba(22,119,255,0.2)"}}, {{"offset": 1, "color": "rgba(22,119,255,0)"}}]}}}}
                }}]
            }}
        }},
        {{
            "id": "chart_002",
            "title": "细分领域市场份额",
            "subtitle": "2024年各技术领域占比",
            "type": "horizontal_bar",
            "echarts_option": {{
                "grid": {{"left": "25%", "right": "15%", "top": "5%", "bottom": "5%"}},
                "xAxis": {{"type": "value", "show": false, "max": 100}},
                "yAxis": {{
                    "type": "category",
                    "data": ["计算机视觉", "自然语言处理", "机器学习平台", "智能语音", "其他"],
                    "axisLine": {{"show": false}},
                    "axisTick": {{"show": false}},
                    "axisLabel": {{"color": "#333", "fontSize": 13}}
                }},
                "series": [{{
                    "type": "bar",
                    "data": [
                        {{"value": 32, "itemStyle": {{"color": "#1677ff"}}}},
                        {{"value": 28, "itemStyle": {{"color": "#722ed1"}}}},
                        {{"value": 24, "itemStyle": {{"color": "#1677ff"}}}},
                        {{"value": 10, "itemStyle": {{"color": "#52c41a"}}}},
                        {{"value": 6, "itemStyle": {{"color": "#fa8c16"}}}}
                    ],
                    "barWidth": 12,
                    "label": {{
                        "show": true,
                        "position": "right",
                        "formatter": "{{c}}%",
                        "color": "#666"
                    }},
                    "backgroundStyle": {{"color": "#f5f5f5"}},
                    "showBackground": true
                }}]
            }}
        }}
    ]
}}
```"""

    def __init__(self, llm_api_key: str, llm_base_url: str, model: str = "qwen-max"):
        super().__init__(
            name="DataAnalyst",
            role="数据分析师",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """处理入口"""
        if state["phase"] == ResearchPhase.ANALYZING.value:
            return await self._analyze_data(state)
        return state

    def assess_risk(self, state: ResearchState) -> Optional[Dict[str, Any]]:
        """
        风险评分（纯规则，无 LLM）。写入 state 并推送 SSE 事件。

        ⚠️ 三条约束，都来自本项目踩过的坑：

        1. **不得被 LLM 成败门控**（BC-17）。本方法在 _analyze_data 的最前面调用，
           且不依赖任何 LLM 产物；后续数据提取/知识图谱/图表任何一步抛异常，
           评级都已经产出。确定性组件依赖不确定组件，方向是反的。

        2. **评测与生产走同一入口**（BC-15）。评分 + 事件推送收敛在这一个方法里，
           评测直接调它，测到的就是生产行为。

        3. **前置条件不满足时 fail-closed**。清单说 verified 但档案没进 state，
           score() 会把"档案里没有被执行记录"读成"未发现被执行记录"——
           一个纯粹由链路缺陷制造的正面结论。这是评分卡最危险的失效方向，
           必须落到「数据不足，无法评级」而不是低分。

        Returns: 评分结果；非尽调流程（无核查清单）返回 None
        """
        checks = state.get("field_checks") or []
        if not checks:
            # 未识别到尽调对象，退化为普通研究流程，没有清单可评——
            # 这里不做 fail-closed，因为根本不存在"授信结论"这个产物
            self.logger.info("[DataAnalyst] 无核查清单，跳过风险评分（非尽调流程）")
            return None

        # 完整度按当前清单重算：闸门的判据必须与被评分的清单同源，
        # 不能用可能已过期的 state["completeness"]
        completeness = compute_completeness(checks)
        state["completeness"] = completeness

        profile = state.get("company_profile") or {}
        if not profile:
            result = unratable(
                "结构化企业档案缺失，无法执行风险评分（清单状态无法映射到具体数值）",
                completeness,
            )
            self.logger.error("[DataAnalyst] 有核查清单但无 company_profile，评级 fail-closed")
            # 用 setdefault：从旧检查点恢复的 state 可能没有这个键，
            # 而 fail-closed 分支自己再抛异常就彻底失去意义了
            state.setdefault("errors", []).append("风险评分：company_profile 缺失，已按不可评级处理")
        else:
            evidence_store = state.get("evidence_store") or {}
            try:
                # v0.6：按 verification_origin 分发重放依据，而非一律用初始档案。
                # 结构化适配器核实的字段本就无法由初始档案重放，旧实现会把
                # 合法增量证据误判为不一致并全面 fail-closed。
                report = verify_field_checks(profile, checks, evidence_store)
                # 降级必须显式披露，不能只进日志（BC-02 的教训）
                for d in report.degradations:
                    state.setdefault("errors", []).append(
                        f"证据链降级：{d['field_id']} {d['detail']}"
                    )
                    self.logger.warning(
                        f"[DataAnalyst] 证据链降级 {d['field_id']}: {d['reason']}"
                    )
                if report.mismatches:
                    fields = "、".join(
                        f"{m['field_id']}({m['reason']})" for m in report.mismatches
                    )
                    result = unratable(
                        f"核查清单证据链不完整或与来源不一致（{fields}），不予评级",
                        completeness,
                    )
                    self.logger.error(
                        f"[DataAnalyst] 证据链校验失败，评级 fail-closed: {fields}"
                    )
                    state.setdefault("errors", []).append(
                        f"风险评分：证据链校验失败（{fields}），已按不可评级处理"
                    )
                else:
                    # 校验通过 ≠ 可以评分。评分卡读的是结构化档案，不是清单状态；
                    # 适配器证据必须先合并进这份数据，否则新增的负面证据会被
                    # 读成"未发现 XX"（BC-31）。合并不了就不予评级。
                    view, unmergeable = build_scoring_view(
                        profile, checks, evidence_store,
                        profile_backed_fields=PROFILE_BACKED_FIELDS,
                        profile_replay_fn=replay_from_profile,
                    )
                    if unmergeable:
                        fields = "、".join(m["field_id"] for m in unmergeable)
                        result = unratable(
                            f"结构化证据无法并入评分数据视图（{fields}），"
                            f"评分卡会读到旧档案并可能得出相反结论，不予评级",
                            completeness,
                        )
                        self.logger.error(
                            f"[DataAnalyst] 证据无法并入评分视图，fail-closed: {fields}"
                        )
                        state.setdefault("errors", []).append(
                            f"风险评分：证据未提供 profile_patch（{fields}），已按不可评级处理"
                        )
                    else:
                        result = score_risk(view, checks, completeness)
                    # 来源降级必须约束等级，不能只写进 errors（BC-33）
                    result = apply_provenance_gate(
                        result, report.degradations, POLICY.degraded_level_floor
                    )
            except Exception as e:
                # 打分本身出错同样不得静默：没有评级 ≠ 没有风险
                result = unratable(f"风险评分执行失败（{type(e).__name__}: {e}），不予评级", completeness)
                self.logger.error(f"[DataAnalyst] 风险评分异常，已 fail-closed: {e}", exc_info=True)
                state.setdefault("errors", []).append(f"风险评分执行失败: {e}")

        state["risk_assessment"] = result
        self.logger.info(
            f"[DataAnalyst] 风险评级：{result['level']}"
            f"（综合分 {result['composite_score']}，闸门 {len(result['gates_applied'])} 条，"
            f"人工复核 {'必须' if result['requires_human_review'] else '非强制'}）"
        )

        self.add_message(state, "risk_assessment", {
            "level": result["level"],
            "composite_score": result["composite_score"],
            # 等级往往由闸门而非分数决定，前端与下游必须能看到是哪条闸门起的作用
            "gates_applied": result["gates_applied"],
            "triggered_rules": result["triggered_rules"],
            "dimension_scores": result["dimension_scores"],
            "dimensions_excluded": result["dimensions_excluded"],
            "requires_human_review": result["requires_human_review"],
            "credit_advice": result["credit_advice"],
            "completeness": result["completeness"],
            # 来源降级必须随评级一起推给前端：只在 errors 里出现的话，
            # 只看评级卡片的复核人根本不知道这份结论建立在来源不明的数据上
            "provenance_degradations": result.get("provenance_degradations", []),
        })
        return result

    async def _analyze_data(self, state: ResearchState) -> ResearchState:
        """执行数据分析"""
        self.logger.info("Starting data analysis...")

        # 发送开始事件
        self.add_message(state, "research_step", {
            "step_id": f"step_analyze_{uuid.uuid4().hex[:8]}",
            "step_type": "analyzing",
            "title": "数据分析",
            "subtitle": "生成可视化",
            "status": "running",
            "stats": {"results_count": 0, "charts_count": 0, "entities_count": 0}
        })

        # 0. 风险评分（纯规则）。刻意放在所有 LLM 调用之前——
        #    下面任何一步失败都不能影响评级的产出（BC-17）
        self.assess_risk(state)

        # 1. 提取结构化数据
        extracted_data = await self._extract_data(state)

        # 2. 构建知识图谱
        knowledge_graph = await self._build_knowledge_graph(state)

        # 3. 生成可视化图表
        charts = await self._generate_charts(state, extracted_data)

        # 更新状态
        if knowledge_graph:
            state["knowledge_graph"] = knowledge_graph
            # 发送知识图谱事件
            self.add_message(state, "knowledge_graph", {
                "graph": knowledge_graph,
                "stats": {
                    "entities_count": len(knowledge_graph.get("nodes", [])),
                    "relations_count": len(knowledge_graph.get("edges", []))
                }
            })

        if charts:
            state["charts"].extend(charts)
            self.logger.info(f"[DataAnalyst] 生成了 {len(charts)} 个 ECharts 图表，准备发送 charts 事件")
            for i, chart in enumerate(charts):
                self.logger.info(f"[DataAnalyst] 图表 {i+1}: id={chart.get('id')}, title={chart.get('title')}, has_echarts_option={bool(chart.get('echarts_option'))}")
            # 发送图表事件
            self.add_message(state, "charts", {
                "charts": charts
            })
            self.logger.info(f"[DataAnalyst] ✅ charts 事件已发送")

        # 发送完成事件
        self.add_message(state, "research_step", {
            "step_type": "analyzing",
            "title": "数据分析",
            "subtitle": "生成可视化",
            "status": "completed",
            "stats": {
                "results_count": len(state.get("facts", [])),
                "charts_count": len(charts) if charts else 0,
                "entities_count": len(knowledge_graph.get("nodes", [])) if knowledge_graph else 0
            }
        })

        return state

    async def _extract_data(self, state: ResearchState) -> Dict[str, Any]:
        """从搜索结果中提取结构化数据"""
        self.logger.info("Extracting structured data...")

        # 收集搜索结果
        search_results_text = []
        for fact in state.get("facts", [])[:20]:
            search_results_text.append(f"- {fact.get('content', '')} (来源: {fact.get('source_name', '未知')})")

        if not search_results_text:
            self.logger.info("No facts to extract data from")
            return {"data_points": [], "time_series": [], "distributions": [], "insights": []}

        prompt = self.DATA_EXTRACTION_PROMPT.format(
            query=state["query"],
            search_results="\n".join(search_results_text)
        )

        response = await self.call_llm(
            system_prompt="你是专业的数据分析师，擅长从文本中提取结构化数据。请输出JSON格式。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.2
        )

        result = self.parse_json_response(response)

        # 更新数据点到状态
        if result.get("data_points"):
            for dp in result["data_points"]:
                state["data_points"].append(dp)

        # 更新洞察
        if result.get("insights"):
            state["insights"].extend(result["insights"])

        self.logger.info(f"Extracted {len(result.get('data_points', []))} data points, {len(result.get('time_series', []))} time series")

        return result

    async def _build_knowledge_graph(self, state: ResearchState) -> Dict[str, Any]:
        """构建知识图谱"""
        self.logger.info("Building knowledge graph...")

        # 收集内容
        content_parts = []
        for fact in state.get("facts", [])[:15]:
            content_parts.append(fact.get("content", ""))

        if not content_parts:
            self.logger.info("No content for knowledge graph")
            return {"nodes": [], "edges": []}

        prompt = self.KNOWLEDGE_GRAPH_PROMPT.format(
            query=state["query"],
            content="\n".join(content_parts)
        )

        response = await self.call_llm(
            system_prompt="你是知识图谱专家，擅长从文本中提取实体和关系。请输出JSON格式。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.2
        )

        result = self.parse_json_response(response)

        # 添加节点大小（基于importance）
        if result.get("nodes"):
            for node in result["nodes"]:
                importance = node.get("importance", 5)
                node["size"] = 20 + importance * 3  # 20-50 range

        self.logger.info(f"Built knowledge graph with {len(result.get('nodes', []))} nodes, {len(result.get('edges', []))} edges")

        return result

    async def _generate_charts(self, state: ResearchState, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成可视化图表"""
        self.logger.info("[DataAnalyst] ========== 开始生成 ECharts 可视化图表 ==========")

        # 准备数据
        data_for_charts = {
            "data_points": extracted_data.get("data_points", []),
            "time_series": extracted_data.get("time_series", []),
            "distributions": extracted_data.get("distributions", []),
            "existing_data_points": state.get("data_points", [])[:10]
        }

        # 如果没有足够数据，跳过
        total_data = (len(data_for_charts["data_points"]) +
                     len(data_for_charts["time_series"]) +
                     len(data_for_charts["distributions"]))

        self.logger.info(f"[DataAnalyst] 图表数据统计: data_points={len(data_for_charts['data_points'])}, time_series={len(data_for_charts['time_series'])}, distributions={len(data_for_charts['distributions'])}, total={total_data}")

        if total_data == 0:
            self.logger.warning("[DataAnalyst] ⚠️ 没有足够数据生成图表，跳过")
            return []

        prompt = self.CHART_GENERATION_PROMPT.format(
            query=state["query"],
            data=str(data_for_charts)
        )

        response = await self.call_llm(
            system_prompt="你是数据可视化专家，擅长生成ECharts图表配置。请输出JSON格式。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.3
        )

        result = self.parse_json_response(response)
        charts = result.get("charts", [])

        # 为每个图表添加唯一ID
        for chart in charts:
            if not chart.get("id"):
                chart["id"] = f"chart_{uuid.uuid4().hex[:8]}"

        self.logger.info(f"Generated {len(charts)} charts")

        return charts

    async def analyze_for_section(self, state: ResearchState, section_title: str) -> Dict[str, Any]:
        """为特定章节分析数据（可被其他Agent调用）"""
        self.logger.info(f"Analyzing data for section: {section_title}")

        # 收集与该章节相关的事实
        related_facts = [f for f in state.get("facts", [])
                        if section_title in str(f.get("related_sections", []))]

        if not related_facts:
            related_facts = state.get("facts", [])[:10]

        # 简化的数据提取
        search_results_text = [f"- {f.get('content', '')}" for f in related_facts]

        prompt = f"""分析以下内容，提取与"{section_title}"相关的关键数据：

{chr(10).join(search_results_text)}

输出JSON格式：
{{
    "key_metrics": [
        {{"name": "指标名", "value": "值", "unit": "单位"}}
    ],
    "trend": "上升/下降/稳定",
    "summary": "一句话总结"
}}"""

        response = await self.call_llm(
            system_prompt="你是数据分析师，提取关键数据。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.2
        )

        return self.parse_json_response(response)
