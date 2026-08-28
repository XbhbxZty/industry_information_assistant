# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""
DeepResearch V2.0 - 深度侦探 Agent (DeepScout)

职责：
1. 全网穿透搜索 - 不只是看摘要，深入阅读原文
2. 递归搜索 - 发现新线索时自动追踪
3. 信源评级 - 评估来源可信度
4. 交叉验证 - 多源验证关键信息
"""

import uuid
import asyncio
import hashlib
import re
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime

from .base import BaseAgent, LLMCallTimeout
from ...statement_scope import build_scope_index
from ..state import ResearchState, ResearchPhase
from ...rag_evidence_bridge import collect_analysis_evidence, finalize_rag_evidence

# 展示给抽取模型的单条片段原文长度上界。
#
# 必须 ≥ 入库切片长度，否则模型看到的原文是校验器所用原文的真子集，
# 就会出现"系统要求的东西模型看不到"这种不可满足的约束（BC-57）。
# case_01 语料实测：均长 1523、最长 2059 字符。
SOURCE_EXCERPT_CHARS = 2600

# 调查层语料留存上界（红线 4）。摘录比 A 层抽取窗口短：B 层要的是
# "这份材料讲了什么"，不是逐字定位——逐字定位是 A 层证据闸门的活。
INVESTIGATION_CORPUS_LIMIT = 40
INVESTIGATION_EXCERPT_CHARS = 1200

#: 送进调查层的**正文总量**安全网（字符）。
#:
#: ⚠️ **这个数曾经是 24000，而那是按错误的模型算出来的。**
#:
#: 当时的依据是一次实测 400：
#:
#:     InternalError.Algo.InvalidParameter:
#:     Range of input length should be [1, 30720]
#:
#: 但那次探针用的是 `CodeWizard(key, url)`——**没传 model，撞上默认值
#: `"qwen-max"`**。生产走 `graph.py` 里的 `config.agents.wizard.model`，
#: 即 `deepseek-v4-flash`。两个模型的容量差一个数量级：
#:
#:     qwen-max           输入 30,720 token（≈41,000 字财报文本）
#:     deepseek-v4-flash  实测 400,000 字仍然通过
#:
#: 于是 24000 为一个生产从未遇到的限制服务，代价是八章清单里
#: 七章的语料被整体丢弃（见下面的按章分配）。
#:
#: 50000 是本来的设计意图（40 条 × 1200 字 ≈ 48,000），BC-77 之前
#: 就是这个量，在生产上跑过 15 轮没有容量问题。
#: **从此它是安全网，不是容量约束**——真正的成本闸门是条数上界。
INVESTIGATION_INPUT_CHAR_BUDGET = 50000

#: 每章至少保底几条。配额算出来比这个小时按这个给——
#: 分到 0 条与整章丢弃在结果上没有区别（BC-78 就是整章拿 0）。
MIN_SECTION_QUOTA = 2


def _corpus_key(result: Dict[str, Any]) -> tuple:
    """调查层语料的去重键：**分片粒度**（BC-75）。

    优先用 `(doc_id, chunk_index)`——那是分片的真实身份；本地检索结果
    一定带这两个字段。网页结果没有，退回 `(url, 摘要前 80 字)`：
    url 对网页已足够区分，摘要前缀兜住同一 url 多次抓取的情形。

    **不要退回 (title, url)**：那正是本条 bad case 的成因。
    """
    doc_id = result.get("doc_id")
    chunk = result.get("chunk_index")
    if doc_id is not None and chunk is not None:
        return ("chunk", str(doc_id), int(chunk))
    return ("web", str(result.get("url") or ""),
            str(result.get("summary") or result.get("snippet") or "")[:80])

# 每章期望的最少原子检索词数（BC-62）。
#
# 这是一条**下限**。`expand_local_search_queries` 原本只有 limit 上限，
# 于是"这一章只发了一次检索"这件事在任何一层都不可见——两个模型相差
# 25 条 vs 8 条查询、178 vs 80 个片段，而运行状态一律是 completed。
# 低于下限不构成故障（仍能产出证据），但必须留痕：否则下次换模型时，
# 检索广度会再一次静默腰斩。
MIN_SECTION_QUERIES = 3

# 送入抽取的检索结果条数上限。
#
# ## 为什么尽调模式要单列一个更大的值
#
# 此前两处各自写死 `[:15]`：提示词给模型看 15 条，`collect_analysis_evidence`
# 也按 15 条校验。而去重后实际保留 30 条——**中间那一半被静默丢弃**。
#
# 实测 case_01 sec_4：检索到 28 条，7 个场景字段里 **6 个**的正确材料落在
# 第 22–27 条，全部在可见窗口之外。模型不是选错了，是从没见过那些片段。
#
# 这还解释了 BC-62 修好检索广度后"片段 80→217、过闸证据零增长"那个负面
# 结果：多检索出来的内容全被这个常数挡住了，**修检索的收益被它吃掉**。
#
# 形态与 BC-57 的 `[:1600]` 完全一致——系统拿到的比给模型看的多，
# 而差额不可见。区别只是这次丢的是整条片段，不是片段的尾巴。
#
# ⚠️ 两处必须共用同一个值：只放开提示词而不放开校验，模型引用第 16 条
# 以后就会索引越界被全拒，比现在更糟。
EXTRACTION_WINDOW = 15
DUE_DILIGENCE_EXTRACTION_WINDOW = 30

# 去重后保留的检索结果上限。与上面的窗口是**耦合**的：
# 窗口小于它，差额就被静默丢弃。有断言钉住 window >= 这个值。
RETRIEVAL_KEEP_LIMIT = 30

# 规格本身就例外的章节：第 8 章「风险汇总与授信建议」不预设检索词，
# 它消费前七章的核查结果（提示词里写明"第 8 章可以只有 1 条"）。
#
# 把一条一刀切的下限套在一个规格例外上，产出的是**假告警**——B′ 轮里
# 它就报了一次。而假告警的代价不是这一条噪声：一张总是亮着的告警表，
# 读者很快就学会略过它，真正的不足也就跟着被忽略了。
# 判据要对规格校准，这一点和 BC-60 是同一件事。
QUERY_MINIMUM_EXEMPT_SECTIONS = frozenset({"sec_8"})


def expand_local_search_queries(queries: List[str], limit: int = 6) -> List[str]:
    """Split an architect's multi-topic sentence into retrieval-sized queries.

    Embedding a whole semicolon-delimited checklist as one vector overweights its
    first topic.  This helper is intentionally deterministic and is used only for
    local KB retrieval, where company context is already fixed by the collection.

    最小片段长度按中文校准（BC-60 同族）：原来的 `>= 3` 会静默丢掉
    `营收`、`存货`、`担保` 这类**两字**检索词——它们在中文财务语料里是标准
    表述，`存货` 本身就是固定清单的字段关键词。判据里的数字要对真实语言校准，
    不能按英文单词的直觉写。1 个字符仍然丢弃：那是拆分产生的碎片，不是查询。
    """
    expanded: List[str] = []
    for raw in queries:
        text = str(raw or "").strip()
        if not text:
            continue
        parts = [part.strip(" ，,、") for part in re.split(r"[；;]", text)]
        useful = [part for part in parts if len(part) >= 2]
        for query in useful or [text]:
            if query not in expanded:
                expanded.append(query)
            if len(expanded) >= limit:
                return expanded
    return expanded


class SearchOutcome:
    """
    一次检索的结果。

    ## 为什么不能直接返回 List[Dict]

    `_execute_search` 此前有五条路径返回同一个空列表：HTTP 非 200、
    业务码非 200、超时、其它异常，以及**真的一条结果都没有**。
    调用方拿到 `[]` 无从区分"这个源查了没有"和"这个源根本没查成"。

    这正是 `datasource/base.py` 的 `AdapterResult` 用 `queried` 与 `records`
    两个字段分开表达的那件事——那里的注释写明它是 v0.2 撞出来的核心设计。
    检索路径当初没享受到同一条纪律。

    后果不是少几条搜索结果，而是**检索故障会被下游读成"未发现负面信息"**：
    舆情、监管处罚这类 `absence_meaningful=True` 的字段，"搜了没有"是合法的
    正面结论，而"没搜成"绝不是。把两者混同，等于把一次 API 超时翻译成
    "该企业无负面舆情"。

    与 BC-19 / BC-31 同形：机制在别处建好了，另一个入口重新打开同一个洞。
    """

    __slots__ = ("results", "ok", "failure_reason", "provider", "query")

    def __init__(
        self,
        *,
        results: Optional[List[Dict]] = None,
        ok: bool,
        failure_reason: str = "",
        provider: str = "",
        query: str = "",
    ):
        if ok and failure_reason:
            raise ValueError("成功的检索不得携带 failure_reason")
        if not ok and not failure_reason:
            raise ValueError("失败的检索必须给出 failure_reason，否则与空结果不可区分")
        self.results = list(results or [])
        self.ok = ok
        self.failure_reason = failure_reason
        self.provider = provider
        self.query = query

    @property
    def searched_and_empty(self) -> bool:
        """查成功了但一条都没有——这是可以写进报告的正面结论。"""
        return self.ok and not self.results

    def as_failure_record(self) -> Dict[str, str]:
        """
        供 state["search_failures"] 留痕。

        对应案例包来源目录里 S009/S010 那两行：访问失败被如实记为失败，
        并明确写"只证明本次访问未能完成查询，不能据此认定不存在记录"。
        """
        return {
            "provider": self.provider,
            "query": self.query,
            "failure_reason": self.failure_reason,
            "occurred_at": datetime.now().isoformat(),
        }

    def __repr__(self) -> str:
        state = "ok" if self.ok else f"failed({self.failure_reason})"
        return f"SearchOutcome({self.provider}, {state}, {len(self.results)} results)"

# 网页文本提取库（可选依赖）
try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# 本地知识库搜索依赖
try:
    from service.milvus_service import MilvusService
    from service.embedding_service import generate_embedding
    MILVUS_AVAILABLE = True
except ImportError:
    try:
        from app.service.milvus_service import MilvusService
        from app.service.embedding_service import generate_embedding
        MILVUS_AVAILABLE = True
    except ImportError:
        MILVUS_AVAILABLE = False


class DeepScout(BaseAgent):
    """
    深度侦探 - 信息收集专家

    特点：
    - 递归搜索：发现重要线索后自动深挖
    - 长文本阅读：进入网页读取完整内容
    - 信源评级：对来源进行可信度评分
    - 并行搜索：同时执行多个搜索任务
    """

    SEARCH_ANALYSIS_PROMPT = """你是一位资深的研究分析师，擅长从搜索结果中提取关键信息，并验证研究假设。

## 研究问题
{query}

## 当前研究章节
标题: {section_title}
描述: {section_description}

## 尽调主体
{subject_name}

## 固定核查字段（field_evidence 的 field_id 只能从这里选择）
{field_catalog}

## 研究假设（需要寻找证据支持或反驳）
{hypotheses}

## 搜索结果
{search_results}

## 任务
1. 分析搜索结果，提取结构化信息
2. 寻找支持或反驳研究假设的证据
3. 如果文章引用了数据来源（如"据XX统计"），生成追溯查询

{due_diligence_instructions}

输出JSON格式：
```json
{{
    "extracted_facts": [
        {{
            "content": "提取的事实陈述（要具体、可验证）",
            "source_name": "来源名称",
            "source_url": "来源URL",
            "source_type": "official/academic/news/report/self_media",
            "credibility_score": 0.0-1.0,
            "data_points": [
                {{"name": "指标名", "value": "数值", "unit": "单位", "year": 2024}}
            ],
            "needs_verification": true或false,
            "importance": "high/medium/low",
            "related_hypothesis": "h_1或h_2或null",
            "hypothesis_support": "supports/refutes/neutral",
            "source_result_index": 1
        }}
    ],
    "field_evidence": [
        {{
            "field_id": "固定核查字段中的ID",
            "subject_name": "原文明确指向的主体",
            "period": "财务字段必填，如2024年度；其他字段可空",
            "value": "字段取值，必须逐字取自原文（可以只是数字或短语）",
            "numeric_value": "财务字段必填，只填原文数字，不做单位换算",
            "source_result_index": 1,
            "record": {{
                "case_no": "司法字段", "cause": "司法字段", "amount": "金额",
                "status": "状态", "beneficiary": "被担保方",
                "guarantee_type": "担保方式", "project": "中标项目",
                "win_date": "中标日期", "title": "舆情/处罚标题",
                "publish_date": "发布日期", "severity": "严重度", "authority": "处罚机关"
            }}
        }}
    ],
    "hypothesis_evidence": [
        {{
            "hypothesis_id": "h_1",
            "evidence_type": "supports/refutes/inconclusive",
            "evidence_summary": "证据摘要"
        }}
    ],
    "entities_discovered": [
        {{"name": "实体名", "type": "company/person/policy/technology", "relations": ["与XX相关"]}}
    ],
    "key_insights": ["从这些结果中得到的关键洞察"],
    "follow_up_queries": ["需要进一步搜索的关键词"],
    "source_tracing_queries": ["追溯原始数据源的搜索词，如'国家统计局 2024 汽车销量'"],
    "missing_info": ["仍然缺失的信息"],
    "source_quality_assessment": "对整体来源质量的评估"
}}
```

## 评分标准
- 官方来源（政府、央企）: 0.9-1.0
- 学术来源（论文、研究机构）: 0.8-0.95
- 权威媒体（央媒、财经媒体）: 0.7-0.85
- 行业报告（券商、咨询）: 0.7-0.9
- 一般新闻: 0.5-0.7
- 自媒体: 0.2-0.5

## 证据抽取硬规则

**你不需要复制原文。**引文由系统按你给出的取值从原文自动截取，
所以不要输出 exact_quote 字段——输出了也会被忽略。
你的职责是**定位**：说清引用哪一条结果、字段是什么、取值是什么。

- value / numeric_value / record 里的每个值都必须**逐字取自原文**，可以只是
  一个数字或一个短语；禁止改写、补全、换算、合并单元格或自行拼句
- extracted_facts.content 必须是原文里的一段连续文字（可以短），不得概括
- field_evidence 只记录原文明示的正面事实；“材料未提供/无法查询/尚未核实”不是已核实证据
- source_result_index 使用上方方括号编号（从1开始），不得填写URL代替
- 主体不明确、字段归属不确定时，宁可不输出该候选
- 财务字段必须给 period 与 numeric_value；**不要填单位**，单位由系统从原文
  表头解析（年报把「单位：千元」写在表头，你无须也不应转述）
- 同一表格列出多个期间时，按期间拆成多条 field_evidence，period 与
  value/numeric_value 一一对应
- 实际控制人必须有“实际控制人”的明确原文，不得由持股比例推断
- 司法、担保、中标、舆情、处罚字段必须填写 record 中对应的关键字段，
  且每个值都逐字取自原文的**同一处**记录（同一行/同一段），不得跨记录拼装

请开始分析："""

    DEEP_READ_PROMPT = """你是一位专业的文档分析师，擅长从长文本中提取关键信息。

## 研究问题
{query}

## 文档来源
URL: {url}
标题: {title}

## 文档内容
{content}

## 任务
深度阅读文档，提取与研究问题相关的所有关键信息。

输出JSON格式：
```json
{{
    "summary": "文档核心内容摘要（200字内）",
    "key_facts": [
        {{
            "content": "关键事实",
            "confidence": 0.0-1.0,
            "page_location": "大概位置描述"
        }}
    ],
    "data_tables": [
        {{
            "title": "数据表标题",
            "headers": ["列1", "列2"],
            "rows": [["值1", "值2"]]
        }}
    ],
    "quotes": ["重要原文引用"],
    "related_entities": ["提到的相关实体"],
    "publication_date": "发布日期（如果能识别）",
    "author_authority": "作者/机构权威性评估"
}}
```"""

    def __init__(
        self,
        llm_api_key: str,
        llm_base_url: str,
        search_api_key: str,
        model: str = "qwen-plus"
    ):
        super().__init__(
            name="DeepScout",
            role="深度侦探",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model
        )
        self.search_api_key = search_api_key
        self.search_cache: Dict[str, List] = {}
        # 报表口径索引缓存：{(collection, doc_id): ScopeIndex}
        # 一份文档只解析一次——分节标题不随查询变化。
        self._scope_index_cache: Dict[tuple, Any] = {}
        self.fact_fingerprints: Dict[str, str] = {}  # 事实指纹用于去重
        # 显式依赖点，便于离线评测/单元测试替换；生产默认仍使用统一
        # embedding_service。避免测试因网络状态和执行顺序产生假失败。
        self.embedding_fn = generate_embedding

        # 初始化本地知识库搜索服务
        self.milvus_service = None
        if MILVUS_AVAILABLE:
            try:
                self.milvus_service = MilvusService()
                self.logger.info("Milvus service initialized for local knowledge base search")
            except Exception as e:
                self.logger.warning(f"Failed to initialize Milvus service: {e}")

    async def process(self, state: ResearchState) -> ResearchState:
        """处理入口"""
        # 处理补充搜索阶段（审核后回退）
        if state["phase"] == ResearchPhase.RE_RESEARCHING.value:
            return await self._supplementary_research(state)

        # 正常研究阶段
        if state["phase"] not in [ResearchPhase.PLANNING.value, ResearchPhase.RESEARCHING.value]:
            return state

        # 股票行情同样是外部网络来源；封闭评测/纯本地模式不得绕过
        # search_web 开关访问聚合行情 API。
        if state.get("search_web", True):
            await self._fetch_stock_data_if_relevant(state)

        state["phase"] = ResearchPhase.RESEARCHING.value

        # 获取搜索模式配置
        search_web = state.get("search_web", True)
        search_local = state.get("search_local", False)

        # 两个搜索模式都关闭时的处理：
        # 若已有预置事实（如注入的企业档案），这是**合法的纯内部数据源模式**，
        # 必须尊重——v0.4 的评测运行依赖它保证输入与 ground truth 一致（见 BADCASES.md BC-10）。
        # 只有在既无外部检索又无任何已知事实时，才回退到网络搜索，否则无米下炊。
        if not search_web and not search_local:
            if state.get("facts"):
                self.logger.info(
                    f"仅使用内部数据源（已有 {len(state['facts'])} 条预置事实），不执行外部检索"
                )
            else:
                self.logger.warning("未选择任何搜索模式且无预置事实，回退到网络搜索")
                search_web = True
                state["search_web"] = True

        # 获取需要研究的章节
        pending_sections = [s for s in state["outline"] if s.get("status") == "pending"]

        if not pending_sections:
            self.logger.info("No pending sections to research")
            return state

        # 构建搜索模式描述
        search_mode_desc = []
        if search_web:
            search_mode_desc.append("网络搜索")
        if search_local:
            search_mode_desc.append("本地知识库")
        subtitle = " + ".join(search_mode_desc) if search_mode_desc else "深度搜索"

        # 发送 research_step 开始事件
        self.add_message(state, "research_step", {
            "step_id": f"step_searching_{uuid.uuid4().hex[:8]}",
            "step_type": "searching",
            "title": "信息检索",
            "subtitle": subtitle,
            "status": "running",
            "stats": {"sections_count": len(pending_sections), "results_count": 0},
            "search_web": search_web,
            "search_local": search_local
        })

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": f"开始{'、'.join(search_mode_desc)}，共 {len(pending_sections)} 个章节待研究..."
        })

        # 本节点只执行一次；截断到前三节会让其余章节直接进入写作，进而把
        # “没有检索”误写成“材料未披露”。对全部规划章节并行检索。
        tasks = []
        for section in pending_sections:
            tasks.append(self._research_section(state, section))

        # ⚠️ `return_exceptions=True` 是必需的，不是防御性编程（BC-55/BC-56）：
        # 裸 gather 会让**任意一章**的异常立刻上抛，另外七章的抽取结果全部
        # 丢弃。实测一次 `'list' object has no attribute 'get'` 就是这样把
        # 整轮研究打成 facts=0 的。
        #
        # 但只加隔离会让事情更糟：崩掉的章节产出 0 条证据，与"这一章确实
        # 没有材料"在下游完全无法区分——正是 BC-51 那个洞在第四个入口。
        # 所以隔离必须与 section_failures 留痕成对出现。
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for section, outcome in zip(pending_sections, outcomes):
            if isinstance(outcome, BaseException):
                self._record_section_failure(state, section, outcome)

        # LLM 只产候选。所有章节结束后统一执行原文、主体、字段、数值、日期
        # 校验并原子写入 evidence_store/field_checks，避免并发章节按完成顺序覆盖。
        evidence_counts = finalize_rag_evidence(state)
        self.add_message(state, "field_checks_updated", {
            "field_checks": state.get("field_checks", []),
            "completeness": state.get("completeness", {}),
            "rag_evidence": evidence_counts,
        })

        # 发送 research_step 完成事件
        self.add_message(state, "research_step", {
            "step_type": "searching",
            "title": "信息检索",
            "subtitle": "全网深度搜索",
            "status": "completed",
            "stats": {
                "results_count": len(state.get("facts", [])),
                "sources_count": len(set(f.get("source_url", "") for f in state.get("facts", [])))
            }
        })

        # 发送搜索结果事件供前端详情面板展示
        self._emit_search_results_event(state)

        return state

    # 说明性文字而非检索词的特征。实测 Critic 产出过
    # "（此信息无法通过公开搜索获得，必须要求企业提供）" 这类"查询"，
    # 系统照单全收拿去搜索，纯属浪费调用。
    _INVALID_QUERY_MARKERS = (
        "无法通过", "无法获得", "必须要求", "需企业提供", "需要企业提供",
        "建议要求", "不适用", "无需搜索", "N/A",
    )

    def _is_valid_search_query(self, query: str) -> bool:
        """
        判断模型产出的字符串是否是可用的检索词。

        判据基于"检索词 vs 散文"的形态差异：真实检索词是短关键词组合，
        几乎不含句读标点；模型跑偏时产出的是完整句子或整段说明。
        """
        if not query or not isinstance(query, str):
            return False
        q = query.strip()
        if len(q) < 2 or len(q) > 40:          # 中文检索词很少超过 40 字
            return False
        if any(p in q for p in "。，；！？.;!"):  # 句读标点 => 是句子不是检索词
            return False
        if q.startswith(("（", "(")) and q.endswith(("）", ")")):  # 整句括号 => 注释
            return False
        return not any(m in q for m in self._INVALID_QUERY_MARKERS)

    async def _supplementary_research(self, state: ResearchState) -> ResearchState:
        """
        补充搜索阶段 - 处理审核后发现的信息缺失

        这个方法在 Critic 发现需要补充信息时被调用
        """
        pending_queries = state.get("pending_search_queries", [])

        if not pending_queries:
            self.logger.info("No pending search queries for supplementary research")
            state["phase"] = ResearchPhase.WRITING.value
            return state

        # 补充搜索同样受搜索模式约束（见 BADCASES.md BC-11）。
        # 此前这条路径不检查开关，导致纯内部数据源模式下仍会联网检索。
        if not state.get("search_web", True) and not state.get("search_local", False):
            self.logger.info(
                f"纯内部数据源模式，跳过 {len(pending_queries)} 条补充搜索；"
                f"审核提出的信息缺口将保持未核实状态"
            )
            self.add_message(state, "thought", {
                "agent": self.name,
                "content": (
                    "当前为纯内部数据源模式，不执行外部检索。"
                    f"审核提出的 {len(pending_queries)} 项信息缺口维持未核实状态。"
                )
            })
            state["pending_search_queries"] = []
            state["phase"] = ResearchPhase.WRITING.value
            return state

        # 过滤无效检索词：模型有时会把说明性文字当作查询产出
        # （实测出现过 "（此信息无法通过公开搜索获得，必须要求企业提供）"）
        valid_queries = [q for q in pending_queries if self._is_valid_search_query(q)]
        dropped = len(pending_queries) - len(valid_queries)
        if dropped:
            self.logger.warning(f"丢弃 {dropped} 条无效补充检索词（说明性文字而非查询）")
        if not valid_queries:
            self.logger.warning("补充检索词全部无效，跳过补充搜索")
            state["pending_search_queries"] = []
            state["phase"] = ResearchPhase.WRITING.value
            return state
        pending_queries = valid_queries

        self.logger.info(f"Starting supplementary research with {len(pending_queries)} queries")

        # 发送 research_step 开始事件
        self.add_message(state, "research_step", {
            "step_id": f"step_supplementary_{uuid.uuid4().hex[:8]}",
            "step_type": "searching",
            "title": "补充搜索",
            "subtitle": "针对性信息补充",
            "status": "running",
            "stats": {"queries_count": len(pending_queries), "results_count": 0}
        })

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": f"根据审核反馈，开始补充搜索 {len(pending_queries)} 个问题..."
        })

        # 执行补充搜索
        initial_facts_count = len(state.get("facts", []))

        for query in pending_queries[:5]:  # 最多处理5个补充查询
            self.add_message(state, "action", {
                "agent": self.name,
                "tool": "supplementary_search",
                "query": query
            })

            # 执行搜索
            outcome = await self._execute_search(
                query, count=8, as_of=state.get("as_of", "") or ""
            )
            if not outcome.ok:
                self._record_search_failure(state, outcome)
            results = outcome.results

            if results:
                # 分析结果
                analysis = await self._analyze_supplementary_results(
                    state["query"],
                    query,
                    results
                )

                if analysis:
                    # 添加新事实
                    for fact in analysis.get("extracted_facts", []):
                        content = fact.get("content", "")
                        source_url = fact.get("source_url", "")

                        if not self._is_duplicate_fact(content, source_url):
                            fact_entry = {
                                "id": f"fact_{uuid.uuid4().hex[:8]}",
                                "content": content,
                                "source_url": source_url,
                                "source_name": fact.get("source_name", ""),
                                "source_type": fact.get("source_type", "news"),
                                "credibility_score": fact.get("credibility_score", 0.5),
                                "is_supplementary": True,  # 标记为补充搜索获得
                                "related_sections": []
                            }
                            state["facts"].append(fact_entry)

        # 清空待搜索列表
        state["pending_search_queries"] = []

        # 发送完成事件
        new_facts_count = len(state.get("facts", [])) - initial_facts_count
        self.add_message(state, "research_step", {
            "step_type": "searching",
            "title": "补充搜索",
            "subtitle": "针对性信息补充",
            "status": "completed",
            "stats": {
                "results_count": new_facts_count,
                "sources_count": len(set(f.get("source_url", "") for f in state.get("facts", [])[-new_facts_count:] if new_facts_count > 0))
            }
        })

        self.add_message(state, "observation", {
            "agent": self.name,
            "content": f"补充搜索完成，新增 {new_facts_count} 条事实"
        })

        # 发送更新后的搜索结果
        self._emit_search_results_event(state)

        # 继续写作阶段
        state["phase"] = ResearchPhase.WRITING.value

        return state

    async def _fetch_stock_data_if_relevant(self, state: ResearchState) -> None:
        """
        自动识别查询中的上市公司，获取实时股票数据

        当用户查询涉及上市公司时（如"茅台怎么样"），自动获取股票行情并添加到数据点
        """
        try:
            try:
                from config.stock_mapping import find_company_in_query
                from service.stock_service import get_stock_service
            except ImportError:
                from app.config.stock_mapping import find_company_in_query
                from app.service.stock_service import get_stock_service

            query = state.get("query", "")
            found_companies = find_company_in_query(query)

            if not found_companies:
                return

            stock_service = get_stock_service()

            for company_name, stock_code in found_companies[:2]:  # 最多查询2只股票
                self.logger.info(f"检测到上市公司: {company_name} ({stock_code})")

                result = await stock_service.get_stock_by_code(stock_code)

                if result.get("success"):
                    data = result["data"]

                    # 添加到 data_points
                    if "data_points" not in state:
                        state["data_points"] = []

                    state["data_points"].extend([
                        {
                            "name": f"{data['name']}当前股价",
                            "value": float(data['nowPri']) if data['nowPri'] else 0,
                            "unit": "元",
                            "source": "聚合数据股票API",
                            "source_type": "realtime"
                        },
                        {
                            "name": f"{data['name']}涨跌幅",
                            "value": data['increPer'],
                            "unit": "%",
                            "source": "聚合数据股票API",
                            "source_type": "realtime"
                        },
                        {
                            "name": f"{data['name']}今日成交量",
                            "value": data['traAmount'],
                            "unit": "手",
                            "source": "聚合数据股票API",
                            "source_type": "realtime"
                        },
                    ])

                    # 发送实时行情消息
                    self.add_message(state, "stock_quote", {
                        "agent": self.name,
                        "code": stock_code,
                        "name": data['name'],
                        "price": data['nowPri'],
                        "change": data['increase'],
                        "change_percent": data['increPer'],
                        "high": data['todayMax'],
                        "low": data['todayMin'],
                        "volume": data['traAmount'],
                        "turnover": data['traNumber'],
                        "open": data['todayStartPri'],
                        "prev_close": data['yestodEndPri']
                    })

                    self.add_message(state, "thought", {
                        "agent": self.name,
                        "content": f"已获取 {data['name']} 实时行情：¥{data['nowPri']} ({data['increPer']})"
                    })

                    self.logger.info(f"获取股票数据成功: {data['name']} ¥{data['nowPri']}")
                else:
                    self.logger.warning(f"获取股票数据失败: {stock_code} - {result.get('error')}")

        except ImportError as e:
            self.logger.warning(f"股票模块导入失败: {e}")
        except Exception as e:
            self.logger.error(f"获取股票数据异常: {e}")

    async def _analyze_supplementary_results(
        self,
        original_query: str,
        search_query: str,
        results: List[Dict]
    ) -> Optional[Dict]:
        """分析补充搜索结果"""
        results_text = []
        for r in results[:8]:
            results_text.append(f"标题: {r.get('title', 'N/A')}\n来源: {r.get('site_name', 'N/A')}\n内容: {r.get('summary', '')[:300]}")

        prompt = f"""你是一位专业的研究分析师，正在补充搜索以解决审核发现的信息缺失问题。

## 原始研究问题
{original_query}

## 补充搜索关键词
{search_query}

## 搜索结果
{chr(10).join(results_text)}

## 任务
从搜索结果中提取与"{search_query}"直接相关的关键事实和数据。

输出JSON格式：
```json
{{
    "extracted_facts": [
        {{
            "content": "提取的事实陈述",
            "source_name": "来源名称",
            "source_url": "来源URL",
            "source_type": "official/academic/news/report",
            "credibility_score": 0.0-1.0,
            "data_points": [
                {{"name": "指标名", "value": "数值", "unit": "单位"}}
            ]
        }}
    ],
    "key_findings": "本次补充搜索的关键发现"
}}
```"""

        response = await self.call_llm(
            system_prompt="你是专业的信息提取专家，擅长从搜索结果中提取结构化信息。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.2
        )

        return self.parse_json_response(response)

    def _emit_search_results_event(self, state: ResearchState) -> None:
        """发送搜索结果事件供前端展示"""
        search_results_for_ui = []
        for fact in state.get("facts", [])[-20:]:  # 取最近的20条
            search_results_for_ui.append({
                "id": fact.get("id", ""),
                "title": fact.get("content", "")[:80] + "..." if len(fact.get("content", "")) > 80 else fact.get("content", ""),
                "source": fact.get("source_name", "未知来源"),
                "url": fact.get("source_url", ""),
                "snippet": fact.get("content", "")[:200],
                "date": fact.get("date", ""),
                "isSupplementary": fact.get("is_supplementary", False)
            })

        if search_results_for_ui:
            self.add_message(state, "search_results", {
                "results": search_results_for_ui
            })

    async def _research_section(self, state: ResearchState, section: Dict) -> None:
        """研究单个章节"""
        section_id = section["id"]
        section_title = section["title"]
        search_queries = section.get("search_queries", [section_title])
        if isinstance(search_queries, str):
            search_queries = [search_queries]

        # 获取搜索模式配置
        search_web = state.get("search_web", True)
        search_local = state.get("search_local", False)
        if search_local and not search_web:
            search_queries = self._plan_section_queries(state, section, search_queries)

        self.logger.info(f"Researching section: {section_title} (web={search_web}, local={search_local})")

        self.add_message(state, "action", {
            "agent": self.name,
            "tool": "parallel_search",
            "section": section_title,
            "queries": search_queries,
            "search_web": search_web,
            "search_local": search_local
        })

        # 逐个执行搜索，每完成一个就发送事件（提升用户体验）
        all_results = []
        # 本章节是否至少有一次**查成功**的检索。清单回写只认成功——
        # 失败的检索不构成"已尝试该来源"，否则 attempted_sources 会把
        # 一串超时记成"查过了"（见 record_search_attempt 与 SearchOutcome）
        web_succeeded = False
        local_succeeded = False
        for i, query in enumerate(search_queries):
            # 网络搜索
            if search_web:
                outcome = await self._execute_search(
                    query, as_of=state.get("as_of", "") or ""
                )
                results = outcome.results
                all_results.extend(results)
                if outcome.ok:
                    web_succeeded = True
                else:
                    self._record_search_failure(state, outcome)

                # 搜索完成后立即发送原始结果（让用户看到进度）
                if results:
                    self.add_message(state, "search_progress", {
                        "agent": self.name,
                        "query": query,
                        "results_count": len(results),
                        "total_so_far": len(all_results),
                        "section": section_title,
                        "progress": f"{i + 1}/{len(search_queries)}",
                        "search_type": "web"
                    })

                    # 立即发送搜索结果供前端展示
                    search_results_for_ui = [
                        {
                            "id": f"sr_{uuid.uuid4().hex[:6]}",
                            "title": r.get("title", "")[:80],
                            "source": r.get("site_name", "未知来源"),
                            "url": r.get("url", ""),
                            "snippet": r.get("summary", "") or r.get("snippet", ""),
                            "date": r.get("date", ""),
                            "isLocal": False
                        }
                        for r in results[:5]  # 每次最多显示5条
                    ]
                    self.add_message(state, "search_results", {
                        "results": search_results_for_ui,
                        "isIncremental": True,
                        "searchType": "web"
                    })

            # 本地知识库搜索
            if search_local:
                local_outcome = await self._execute_local_search(
                    query, kb_scope=state.get("kb_scope") or []
                )
                local_results = local_outcome.results
                all_results.extend(local_results)
                if local_outcome.ok:
                    local_succeeded = True
                else:
                    self._record_search_failure(state, local_outcome)

                if local_results:
                    self.add_message(state, "search_progress", {
                        "agent": self.name,
                        "query": query,
                        "results_count": len(local_results),
                        "total_so_far": len(all_results),
                        "section": section_title,
                        "progress": f"{i + 1}/{len(search_queries)}",
                        "search_type": "local"
                    })

                    # 发送本地搜索结果
                    local_results_for_ui = [
                        {
                            "id": f"lr_{uuid.uuid4().hex[:6]}",
                            "title": r.get("title", "")[:80],
                            "source": "本地知识库",
                            "url": r.get("url", ""),
                            "snippet": r.get("summary", "") or r.get("snippet", ""),
                            "date": "",
                            "isLocal": True,
                            "score": r.get("score", 0)
                        }
                        for r in local_results[:5]
                    ]
                    self.add_message(state, "search_results", {
                        "results": local_results_for_ui,
                        "isIncremental": True,
                        "searchType": "local"
                    })

        # 回写核查清单：如实记录本章节发生过外部检索（不翻转 status，见函数注释）
        #
        # 判据是**检索是否查成功**，不是**有没有结果**。此前写的是
        # `if all_results`，两个方向都错：
        #   - 查成功但零结果 → 漏记，清单看不出这个源已经查过
        #   - 查失败         → 若恰好另一路有结果，反而被记成"查过了"
        # 而且 tag 用 `if search_web else` 三元式，两路都开时只会记 web_search。
        succeeded_tags = []
        if web_succeeded:
            succeeded_tags.append("web_search")
        if local_succeeded:
            succeeded_tags.append("local_kb")

        if succeeded_tags and state.get("field_checks"):
            try:
                try:
                    from config.dd_checklist import record_search_attempt
                except ImportError:
                    from app.config.dd_checklist import record_search_attempt
                for tag in succeeded_tags:
                    touched = record_search_attempt(
                        state["field_checks"], section_id, tag,
                        checked_at=datetime.now().isoformat()
                    )
                    if touched:
                        self.logger.info(f"[清单回写] {section_title}: {touched} 项追加 {tag} 检索记录")
            except Exception as e:  # 回写失败不应中断研究主流程
                self.logger.warning(f"[清单回写] 失败: {e}")

        if not all_results:
            self.logger.warning(f"No search results for section: {section_title}")
            return

        # Atomic queries can retrieve overlapping chunks.  Keep distinct chunks
        # and cap the context before the extraction LLM call.
        deduplicated_results = []
        seen_result_keys = set()
        for result in all_results:
            result_key = (
                result.get("kb_id"), result.get("doc_id"), result.get("chunk_index"),
                result.get("url"), result.get("summary"),
            )
            if result_key in seen_result_keys:
                continue
            seen_result_keys.add(result_key)
            deduplicated_results.append(result)
        all_results = deduplicated_results[:RETRIEVAL_KEEP_LIMIT]

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": f"搜索完成，获得 {len(all_results)} 条结果，正在分析提取关键信息..."
        })

        self._retain_corpus_for_investigation(state, all_results, section_id)

        # 分析搜索结果（传入假设以便验证）
        analysis = await self._analyze_search_results(
            state["query"],
            section,
            all_results,
            hypotheses=state.get("hypotheses", []),
            subject_name=state.get("company_name") or state.get("subject_name") or "",
            due_diligence_mode=bool(state.get("due_diligence_mode")),
            active_field_ids=[str(check.get("field_id") or "")
                              for check in (state.get("field_checks") or [])],
        )

        if analysis:
            # 提取事实（带去重）
            added_facts = 0
            duplicate_facts = 0
            for fact in analysis.get("extracted_facts", []):
                content = fact.get("content", "")
                source_url = fact.get("source_url", "")

                # 去重检查
                if self._is_duplicate_fact(content, source_url):
                    duplicate_facts += 1
                    continue

                fact_entry = {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": content,
                    "source_url": source_url,
                    "source_name": fact.get("source_name", ""),
                    "source_type": fact.get("source_type", "news"),
                    "credibility_score": fact.get("credibility_score", 0.5),
                    "extracted_at": datetime.now().isoformat(),
                    "related_sections": [section_id],
                    "verified": False,
                    "related_hypothesis": fact.get("related_hypothesis"),
                    "hypothesis_support": fact.get("hypothesis_support"),
                    "metadata": {}
                }
                state["facts"].append(fact_entry)
                added_facts += 1

                # 提取数据点
                for dp in ([] if state.get("due_diligence_mode") else fact.get("data_points", [])):
                    data_point = {
                        "id": f"dp_{uuid.uuid4().hex[:8]}",
                        "name": dp.get("name", ""),
                        "value": dp.get("value", ""),
                        "unit": dp.get("unit", ""),
                        "year": dp.get("year"),
                        "source": fact.get("source_name", ""),
                        "confidence": fact.get("credibility_score", 0.5)
                    }
                    state["data_points"].append(data_point)

            if duplicate_facts > 0:
                self.logger.info(f"Deduplicated {duplicate_facts} facts, added {added_facts}")

            # 必须放在普通事实写入之后：桥接器会找到同一条事实并在通过
            # 确定性校验后把 verified 从 False 翻为 True；失败则保持未核实。
            window = (DUE_DILIGENCE_EXTRACTION_WINDOW
                      if state.get("due_diligence_mode") else EXTRACTION_WINDOW)
            if len(all_results) > window:
                # 丢弃必须可见。此前 28 条丢 13 条毫无痕迹，直接导致
                # 6 个字段的正确材料永远进不了模型视野。
                dropped = {
                    "section_id": section_id,
                    "retrieved": len(all_results),
                    "sent_to_extraction": window,
                    "dropped": len(all_results) - window,
                }
                state.setdefault("extraction_window_drops", []).append(dropped)
                self.logger.warning(
                    f"[抽取窗口] {section_title} 检索 {len(all_results)} 条，"
                    f"只送入前 {window} 条，丢弃 {dropped['dropped']} 条"
                )
                self.add_message(state, "extraction_window_drop",
                                 {"agent": self.name, **dropped})
            collect_analysis_evidence(state, analysis, all_results[:window], section_id)

            # 更新知识图谱
            entities = ([] if state.get("due_diligence_mode")
                        else analysis.get("entities_discovered", []))
            if entities:
                self._update_knowledge_graph(state, entities)
                self.logger.info(f"Added {len(entities)} entities to knowledge graph")
                # 发送知识图谱增量更新事件
                graph = state.get("knowledge_graph", {"nodes": [], "edges": []})
                self.add_message(state, "knowledge_graph", {
                    "graph": graph,
                    "stats": {
                        "entitiesCount": len(graph.get("nodes", [])),
                        "relationsCount": len(graph.get("edges", []))
                    },
                    "isIncremental": True
                })

            # 更新假设状态
            hypothesis_evidence = ([] if state.get("due_diligence_mode")
                                   else analysis.get("hypothesis_evidence", []))
            if hypothesis_evidence:
                self._update_hypothesis_status(state, hypothesis_evidence)

            # 添加洞察
            for insight in ([] if state.get("due_diligence_mode")
                            else analysis.get("key_insights", [])):
                if insight not in state["insights"]:
                    state["insights"].append(insight)

            # 收集本次提取的数据点
            extracted_data_points = []
            for fact in ([] if state.get("due_diligence_mode")
                         else analysis.get("extracted_facts", [])):
                for dp in fact.get("data_points", []):
                    extracted_data_points.append({
                        "name": dp.get("name", ""),
                        "value": dp.get("value", ""),
                        "unit": dp.get("unit", ""),
                        "year": dp.get("year"),
                        "source": fact.get("source_name", "")
                    })

            # 发送观察结果 (包含详细数据供前端展示)
            self.add_message(state, "observation", {
                "agent": self.name,
                "section": section_title,
                "facts_count": added_facts,
                "duplicates_removed": duplicate_facts,
                "data_points_count": len(extracted_data_points),
                "insights": ([] if state.get("due_diligence_mode")
                             else analysis.get("key_insights", [])[:3]),
                "source_quality": analysis.get("source_quality_assessment", ""),
                "hypothesis_updates": len(hypothesis_evidence),
                # 新增: 原始搜索结果 (供前端详情面板展示)
                "search_results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "source": r.get("site_name", ""),
                        "snippet": r.get("summary", "") or r.get("snippet", ""),
                        "date": r.get("date", "")
                    }
                    for r in all_results[:10]  # 最多返回10条
                ],
                # 新增: 提取的事实
                "extracted_facts": [
                    {
                        "content": f.get("content", ""),
                        "source_name": f.get("source_name", ""),
                        "source_url": f.get("source_url", ""),
                        "credibility": f.get("credibility_score", 0.5)
                    }
                    for f in ([] if state.get("due_diligence_mode")
                              else analysis.get("extracted_facts", [])[:8])
                ],
                # 新增: 提取的数据点
                "data_points": extracted_data_points[:10]
            })

            # 递归搜索：信源追溯查询（优先级最高）
            source_tracing = analysis.get("source_tracing_queries", [])
            if (
                state.get("search_web", True)
                and source_tracing
                and state["iteration"] < state["max_iterations"]
            ):
                self.add_message(state, "thought", {
                    "agent": self.name,
                    "content": f"追溯原始数据源: {', '.join(source_tracing[:2])}"
                })
                # 执行信源追溯搜索
                await self._execute_deep_search(
                    state, section_id, source_tracing[:2],
                    search_type="source_tracing",
                    hypotheses=state.get("hypotheses", [])
                )

            # 递归搜索：追踪发现的新线索
            follow_up = analysis.get("follow_up_queries", [])
            if (
                state.get("search_web", True)
                and follow_up
                and state["iteration"] < state["max_iterations"]
            ):
                self.add_message(state, "thought", {
                    "agent": self.name,
                    "content": f"追踪发现的线索: {', '.join(follow_up[:2])}"
                })
                # 执行线索追踪搜索
                await self._execute_deep_search(
                    state, section_id, follow_up[:2],
                    search_type="follow_up",
                    hypotheses=state.get("hypotheses", [])
                )

        # 更新章节状态
        section["status"] = "researching"

    def _retain_corpus_for_investigation(
        self, state: ResearchState, results: List[Dict[str, Any]], section_id: str,
    ) -> None:
        """为调查层（B 层）留存一份有界的原文摘录。

        ## 为什么要单独留存

        `raw_sources` 此前是个从未被写入过的死字段，检索到的原文在 Scout
        这一步之后就消失了。B 层要在 visualize 节点做一次独立抽取，
        必须拿得到原文——**不能靠模型记得**。

        ## 为什么不顺手让 A 层那次抽取多输出几个字段

        阶段 1 的验收标准是"同一主体跑两遍，A 层的等级、额度、核实率逐位不变"。
        往 A 层抽取的输出契约里加字段会改变它的 token 预算与注意力分配
        （BC-56 量过这类改动的影响）。这里只做**纯留存**：不改提示词、
        不改调用、不改任何 A 层读取的字段，因而 A 层行为可证明未变。

        上界（红线 4）：条数与字数都截断。留一份没有上界的语料进检查点，
        等于让每次尽调的检查点随语料线性膨胀。
        """
        if not state.get("due_diligence_mode"):
            return          # B 层只在尽调模式下需要；普通研究走原有链路
        corpus = state.setdefault("raw_sources", [])

        # ⚠️ 条数必须**按章分配**，不能先到先得（BC-78）。
        #
        #    原来是 `room = 上界 - 已留存`：第一章拿满 40 条，
        #    后面七章 room<=0 直接 return。批次 2 的 case01 实测——
        #    sec_1 吃掉全部额度，sec_2~sec_8 共 166 条检索结果一条没进。
        #    **八章清单，模型只看得见一章。**
        #
        #    规则：本章额度 = 剩余额度 ÷ 剩余章节数。
        #    前面少用后面自动多分（不浪费），前面再多也拿不走后面的份额
        #    （不饿死）。不需要预知每章检索量，只需要知道共几章。
        seen_sections = state.setdefault("investigation_sections_seen", [])
        if section_id not in seen_sections:
            seen_sections.append(section_id)
        outline = state.get("outline") or []
        total_sections = len(outline)
        # ⚠️ 分母未知时**不配额**。
        #
        #    第一版写的是 `len(outline) or 8`——取不到大纲就假设八章，
        #    于是一个只有一章的运行只能拿 1/8，另外 7/8 没人来取。
        #    流式分配没法回头补：第一章让出去的额度要不回来。
        #
        #    没有分母时任何猜测都会在某一侧出错（猜大饿死单章、
        #    猜小保护不了多章），所以**宁可退回先到先得并记进留痕**，
        #    也不按一个猜出来的数去分。生产路径上大纲一定在——
        #    `_execute_deep_search` 本身就是按大纲逐章调的。
        remaining_sections = max(1, total_sections - len(seen_sections) + 1)

        global_room = INVESTIGATION_CORPUS_LIMIT - len(corpus)
        if global_room <= 0:
            # BC-75 的另一半：这条出口原先什么都不记，于是"六个章节
            # 颗粒无收"在留痕上完全看不出来。丢弃必须可见。
            state.setdefault("investigation_corpus_drops", []).append({
                "section_id": section_id,
                "retrieved": len(results),
                "retained": len(corpus),
                "retained_chars": sum(len(x.get("summary") or "")
                                      for x in corpus),
                "dropped_duplicate": 0,
                "dropped_over_limit": len(results),
                "dropped_over_budget": 0,
                "note": "条数上界已满，本章整章未进入调查层",
            })
            return
        if total_sections:
            # 本章配额 = 剩余额度 ÷ 剩余章节数，但不低于保底。
            # 同时不得挤占后面章节的保底——那才是"不饿死"的实际含义。
            reserve = max(0, total_sections - len(seen_sections)) * MIN_SECTION_QUOTA
            fair = max(MIN_SECTION_QUOTA, global_room // remaining_sections)
            room = max(1, min(fair, max(MIN_SECTION_QUOTA,
                                        global_room - reserve), global_room))
        else:
            room = global_room
        # ⚠️ 去重键必须落到**分片**粒度，不能用 (title, url)（BC-75）。
        #
        #    本地知识库的所有分片天然共享 title 与 url——它们本来就出自
        #    同一份文档。用文档级标识去重，一份 7 页的材料只会留下 1 页，
        #    而**丢弃是静默的**：从外面看就是"模型没找到东西"。
        #
        #    这个 bug 对 RAG 语料是毁灭性的、对网页检索几乎无害
        #    （网页结果的 url 天然各异），刚好绕开了最容易被注意到的路径。
        seen = {_corpus_key(item) for item in corpus}
        dropped_duplicate = 0
        dropped_over_budget = 0
        used_chars = sum(len(item.get("summary") or "") for item in corpus)
        for offset, result in enumerate(results[:room]):
            key = _corpus_key(result)
            if key in seen:
                dropped_duplicate += 1
                continue
            excerpt = str(result.get("summary") or result.get("snippet") or "")[
                :INVESTIGATION_EXCERPT_CHARS]
            if used_chars + len(excerpt) > INVESTIGATION_INPUT_CHAR_BUDGET:
                # 总量到顶。**不是跳过这一片继续找短的**——那会让送进模型的
                # 材料按长度而非相关性挑选，静默改变抽取结果的构成。
                # 用真实下标，不用 `results.index(result)`——
                # 内容相同的分片会让它取到第一次出现的位置，计数偏大。
                dropped_over_budget += len(results) - offset
                break
            seen.add(key)
            used_chars += len(excerpt)
            corpus.append({
                "title": str(result.get("title") or "")[:160],
                "url": str(result.get("url") or ""),
                "site_name": str(result.get("site_name") or ""),
                "doc_name": str(result.get("doc_name") or ""),
                "date": str(result.get("date") or ""),
                "retrieved_at": datetime.now().isoformat(),
                "section_id": section_id,
                "summary": excerpt,
            })

        # 丢弃必须可见。BC-75 之所以能藏住，就是因为去重不计数——
        # 静默丢掉 6/7 的语料，与"这份材料里没东西"在外部完全同形（BC-51）。
        overflow = max(len(results) - room, 0)
        if dropped_duplicate or overflow or dropped_over_budget:
            state.setdefault("investigation_corpus_drops", []).append({
                "section_id": section_id,
                "retrieved": len(results),
                "retained": len(corpus),
                "retained_chars": used_chars,
                # 本章分到多少、当时还剩几章——没有这两个数，
                # 就说不清"这章只留 3 条"是分配所致还是材料所致。
                "section_quota": room,
                "sections_remaining": remaining_sections,
                "sections_total": total_sections,
                "outline_available": bool(outline),
                "dropped_duplicate": dropped_duplicate,
                "dropped_over_limit": overflow,
                # 与条数超限分开记：这一条说明**该调的是总量预算或摘录长度**，
                # 而不是条数上界。合并成一个数就分不出该动哪里。
                "dropped_over_budget": dropped_over_budget,
            })

    async def _execute_deep_search(
        self,
        state: ResearchState,
        section_id: str,
        queries: List[str],
        search_type: str,
        hypotheses: List[Dict],
        depth: int = 1,
        max_depth: int = 2
    ) -> None:
        """
        执行深度递归搜索

        Args:
            state: 研究状态
            section_id: 关联章节ID
            queries: 搜索查询列表
            search_type: 搜索类型 (source_tracing/follow_up)
            hypotheses: 研究假设
            depth: 当前递归深度
            max_depth: 最大递归深度
        """
        if depth > max_depth:
            self.logger.info(f"Reached max recursion depth ({max_depth})")
            return
        if not state.get("search_web", True):
            self.logger.info(
                f"网络搜索已关闭，跳过 {len(queries)} 条深度{search_type}查询"
            )
            return

        type_labels = {
            "source_tracing": "信源追溯",
            "follow_up": "线索追踪"
        }

        self.add_message(state, "action", {
            "agent": self.name,
            "tool": f"deep_search_{search_type}",
            "queries": queries,
            "depth": depth
        })

        for query in queries:
            # 执行搜索
            outcome = await self._execute_search(
                query, count=6, as_of=state.get("as_of", "") or ""
            )
            if not outcome.ok:
                self._record_search_failure(state, outcome)
            results = outcome.results

            if not results:
                continue

            # 立即发送搜索结果供前端展示（增量）
            search_results_for_ui = [
                {
                    "id": f"sr_{uuid.uuid4().hex[:6]}",
                    "title": r.get("title", "")[:80],
                    "source": r.get("site_name", "未知来源"),
                    "url": r.get("url", ""),
                    "snippet": r.get("summary", "") or r.get("snippet", ""),
                    "date": r.get("date", "")
                }
                for r in results[:5]
            ]
            self.add_message(state, "search_results", {
                "results": search_results_for_ui,
                "isIncremental": True,
                "searchType": type_labels.get(search_type, search_type),
                "depth": depth
            })

            # 分析结果
            analysis = await self._analyze_deep_search_results(
                state["query"],
                query,
                results,
                search_type,
                hypotheses
            )

            if not analysis:
                continue

            # 提取并添加事实
            added_facts = 0
            for fact in analysis.get("extracted_facts", []):
                content = fact.get("content", "")
                source_url = fact.get("source_url", "")

                if not self._is_duplicate_fact(content, source_url):
                    fact_entry = {
                        "id": f"fact_{uuid.uuid4().hex[:8]}",
                        "content": content,
                        "source_url": source_url,
                        "source_name": fact.get("source_name", ""),
                        "source_type": fact.get("source_type", "news"),
                        "credibility_score": fact.get("credibility_score", 0.5),
                        "related_sections": [section_id],
                        "search_depth": depth,
                        "search_type": search_type
                    }
                    state["facts"].append(fact_entry)
                    added_facts += 1

                    # 更新假设证据（如果有）
                    hypothesis_support = fact.get("hypothesis_support")
                    if hypothesis_support and fact.get("related_hypothesis"):
                        h_id = fact["related_hypothesis"]
                        for h in state.get("hypotheses", []):
                            if h.get("id") == h_id:
                                if hypothesis_support == "supports":
                                    h.setdefault("evidence_for", []).append(content[:100])
                                elif hypothesis_support == "refutes":
                                    h.setdefault("evidence_against", []).append(content[:100])

            # 提取数据点
            for dp in analysis.get("data_points", []):
                state["data_points"].append({
                    "id": f"dp_{uuid.uuid4().hex[:8]}",
                    "name": dp.get("name"),
                    "value": dp.get("value"),
                    "unit": dp.get("unit", ""),
                    "year": dp.get("year"),
                    "source": dp.get("source", query),
                    "confidence": dp.get("confidence", 0.7),
                    "search_depth": depth
                })

            self.logger.info(f"Deep search ({search_type}, depth={depth}): +{added_facts} facts for query '{query[:30]}...'")

            # 如果发现更多需要追溯的线索，继续递归（但不超过max_depth）
            if depth < max_depth:
                further_tracing = analysis.get("further_tracing_queries", [])
                if further_tracing:
                    self.add_message(state, "thought", {
                        "agent": self.name,
                        "content": f"发现更深层线索 (深度{depth+1}): {', '.join(further_tracing[:2])}"
                    })
                    await self._execute_deep_search(
                        state, section_id, further_tracing[:2],
                        search_type, hypotheses,
                        depth=depth + 1, max_depth=max_depth
                    )

    async def _analyze_deep_search_results(
        self,
        original_query: str,
        search_query: str,
        results: List[Dict],
        search_type: str,
        hypotheses: List[Dict]
    ) -> Optional[Dict]:
        """分析深度搜索结果"""
        results_text = []
        for r in results[:6]:
            results_text.append(f"标题: {r.get('title', 'N/A')}\n来源: {r.get('site_name', 'N/A')}\n内容: {r.get('summary', '')[:300]}")

        hypotheses_text = ""
        if hypotheses:
            hypotheses_text = "## 研究假设\n" + "\n".join([
                f"- [{h.get('id')}] {h.get('content')}" for h in hypotheses[:3]
            ])

        search_type_desc = "追溯原始数据源" if search_type == "source_tracing" else "追踪相关线索"

        prompt = f"""你是一位专业的研究分析师，正在{search_type_desc}以获取更权威的信息。

## 原始研究问题
{original_query}

## 当前搜索关键词
{search_query}

{hypotheses_text}

## 搜索结果
{chr(10).join(results_text)}

## 任务
1. 从搜索结果中提取关键事实和数据（特别关注官方来源和权威数据）
2. 如果发现引用了其他权威来源，生成进一步追溯查询

输出JSON格式：
```json
{{
    "extracted_facts": [
        {{
            "content": "提取的事实陈述（要具体、可验证）",
            "source_name": "来源名称",
            "source_url": "来源URL",
            "source_type": "official/academic/news/report",
            "credibility_score": 0.0-1.0,
            "related_hypothesis": "h_1或null",
            "hypothesis_support": "supports/refutes/neutral"
        }}
    ],
    "data_points": [
        {{"name": "指标名", "value": "数值", "unit": "单位", "year": 2024}}
    ],
    "further_tracing_queries": ["如果发现引用了其他权威来源，建议进一步追溯的查询"],
    "source_reliability": "对本次搜索来源可靠性的评估"
}}
```"""

        response = await self.call_llm(
            system_prompt="你是专业的信息验证专家，擅长从搜索结果中提取权威信息并追溯原始来源。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.2
        )

        return self.parse_json_response(response)

    def _annotate_statement_scope(
        self, results: List[Dict], collection_name: str
    ) -> None:
        """给每条本地检索结果标上**报表口径**（合并/母公司）。

        实测缺陷：系统把母公司报表的应收账款 72,225,597 千元当成公司的
        应收账款报了出来，合并口径是 66,776,402 千元，高估 8.2%。
        逐字、主体、单位、截止日、字段关键词**全部通过**——唯一没被验证的
        是这张表属于哪份报表。对保理业务这是要害：母公司口径含对子公司的
        内部往来，合并时抵消，那部分根本不可融。

        I/O 放在这里而不是 `rag_evidence_bridge`：桥接器是纯确定性校验，
        不做外部调用。这一层负责把判定所需的事实**带到**它面前。
        """
        if not self.milvus_service or not collection_name:
            return
        for row in results:
            doc_id = str(row.get("doc_id") or "")
            if not doc_id or row.get("statement_scope"):
                continue
            key = (collection_name, doc_id)
            if key not in self._scope_index_cache:
                try:
                    chunks = self.milvus_service.get_document_chunks(collection_name, doc_id)
                    self._scope_index_cache[key] = build_scope_index(
                        (c.get("chunk_index", 0), c.get("content") or "") for c in chunks
                    )
                except Exception as e:
                    # 判不出口径不能当成"是合并"。留空 → 下游按 unknown 处理。
                    self.logger.warning(f"[报表口径] 文档 {doc_id[:12]} 索引失败: {e}")
                    self._scope_index_cache[key] = None
            index = self._scope_index_cache[key]
            if index is None:
                continue
            chunk_index = int(row.get("chunk_index") or 0)
            # 只带两样可 JSON 序列化的信息：进入本切片时的口径，以及本切片
            # 内部的分界点。检索结果要经 fixture 落盘与 SSE 推送，塞对象会炸。
            row["statement_scope"] = index.scope_at_chunk_start(chunk_index)
            marks = index.marks_inside(chunk_index)
            if marks:
                row["statement_scope_marks"] = marks

    def _plan_section_queries(
        self, state: ResearchState, section: Dict, raw_queries: List[str]
    ) -> List[str]:
        """确定本章的原子检索词，并把这件事**变得可观测**（BC-62）。

        ## 为什么需要这一层

        检索广度此前完全不可观测。`expand_local_search_queries(queries, limit=6)`
        只有上限没有下限；没有一条日志、事件或断言说过"这一章只拿到 1 条查询"。
        实测两个模型：

            deepseek-v3.2   分号连写 → 25 条查询 → 178 个片段
            deepseek-v4-flash 一个长串 → 8 条查询 → 80 个片段

        少检索 55%，而 `status` 照样 `completed`、`search_failures` 是空的。
        这是静默降级（BC-02 一族）：把不足伪装成正常。

        契约侧已改为要求数组（`architect._section_queries`），但**光改契约不够**：
        下一个模型仍可能把多个主题写成一条。所以这里做三件事——
        兜底拆分、如实记录用了兜底、低于下限时留痕。

        ⚠️ 留痕**不**降级为故障：查询偏少仍能产出证据，与"检索没查成"
        （`search_failures`）性质不同，混同会让附录里的故障表失去意义。
        """
        atomic = expand_local_search_queries(raw_queries)
        used_fallback = len(atomic) > len([q for q in raw_queries if str(q or "").strip()])
        section_id = str(section.get("id") or "")
        plan = {
            "section_id": section_id,
            "section_title": str(section.get("title") or ""),
            "planned_queries": len([q for q in raw_queries if str(q or "").strip()]),
            "atomic_queries": len(atomic),
            "min_expected": MIN_SECTION_QUERIES,
            "split_fallback_used": used_fallback,
        }
        if used_fallback:
            # 模型把多个主题连写成了一条，系统替它拆开了。这不是错误，
            # 但必须留痕：它意味着契约没有被遵守，而检索广度正依赖这次兜底。
            self.logger.warning(
                f"[检索计划] {plan['section_title']} 的检索词是连写的，"
                f"已按分号拆为 {len(atomic)} 条；提示词要求的是数组"
            )
        exempt = section_id in QUERY_MINIMUM_EXEMPT_SECTIONS
        plan["minimum_exempt"] = exempt
        if not exempt and len(atomic) < MIN_SECTION_QUERIES:
            plan["below_minimum"] = True
            state.setdefault("query_plan_shortfalls", []).append(plan)
            self.logger.warning(
                f"[检索计划] {plan['section_title']} 只有 {len(atomic)} 条原子检索词"
                f"（期望至少 {MIN_SECTION_QUERIES} 条）——本章可得材料会明显偏少"
            )
        self.add_message(state, "section_query_plan", {"agent": self.name, **plan})
        return atomic

    def _record_section_failure(
        self, state: ResearchState, section: Dict, exc: BaseException
    ) -> None:
        """
        把一次**章节级**失败写进 state 并推一条 SSE。

        与 `_record_search_failure` 严格对称，理由也一样：
        章节抽取崩了，这一章的 `facts` 是空的；"这一章没有材料"时它也是空的。
        下游（Writer / Critic / 评分卡 / 评分器）无从区分，于是一次
        `AttributeError` 或一次挂死就会以"材料未提供"的形式进入报告——
        这正是 BC-51「失败与查了没有共用一个返回值」在第四个入口的复发。

        `errors` 也要写：编排层的完成判据读它，阻断级失败不能只存在于附录里。
        """
        if isinstance(exc, asyncio.CancelledError):
            # 用户取消不是系统故障，不留痕、不降级，原样上抛给编排层。
            raise exc

        reason = f"{type(exc).__name__}: {exc}"
        record = {
            "section_id": str(section.get("id") or ""),
            "section_title": str(section.get("title") or ""),
            "failure_reason": reason[:300],
            "failure_kind": "llm_timeout" if isinstance(exc, LLMCallTimeout) else "exception",
            "occurred_at": datetime.now().isoformat(),
        }
        failures = state.setdefault("section_failures", [])
        if any(f.get("section_id") == record["section_id"] for f in failures):
            return
        failures.append(record)
        state.setdefault("errors", []).append(
            f"章节「{record['section_title']}」检索抽取失败: {record['failure_reason'][:120]}"
        )

        self.logger.error(
            f"[章节故障] {record['section_title']}: {reason}", exc_info=exc
        )
        self.add_message(state, "section_failed", {
            "agent": self.name,
            **record,
            "note": "本章节的检索抽取未能完成，其证据为空是故障所致，"
                    "不得据此认定材料未提供或不存在相关记录",
        })

    def _record_search_failure(self, state: ResearchState, outcome: SearchOutcome) -> None:
        """
        把一次检索故障写进 state 并推一条 SSE。

        ⚠️ 失败必须**向上暴露**，不能只写日志。日志没人看，而这件事会
        直接影响报告能不能写"未发现负面记录"——Writer 与 Critic 需要读到它。

        同一 (provider, query) 只记一次：重试造成的重复条目会让附录里
        三条失败看起来像三个不同的信息缺口。
        """
        record = outcome.as_failure_record()
        failures = state.setdefault("search_failures", [])
        key = (record["provider"], record["query"])
        if any((f.get("provider"), f.get("query")) == key for f in failures):
            return
        failures.append(record)

        self.logger.warning(
            f"[检索故障] {record['provider']} / {record['query'][:40]}: "
            f"{record['failure_reason']}"
        )
        self.add_message(state, "search_failed", {
            "agent": self.name,
            **record,
            "note": "本次检索未能完成，不得据此认定不存在相关记录",
        })

    async def _execute_local_search(
        self, query: str, top_k: int = 10, kb_scope: Optional[List[Dict]] = None
    ) -> SearchOutcome:
        """
        执行本地知识库搜索 - 使用 Milvus 向量检索

        ## 这个方法此前是死的

        它写死查 `collection_name="knowledge_base"`，而写入侧用的是
        `kb_<知识库名>`（`knowledge_router.py:95`）。那个集合从不存在，
        `milvus_service.search()` 第一行的 `has_collection` 检查失败就
        `return []`——**本地检索从 V2 上线至今一次都没命中过**，
        且走的是正常返回路径，前端看不出任何区别。

        ## 检索范围不由本方法决定

        `kb_scope` 必须由 `service/kb_scope.resolve_kb_scope()` 从
        PostgreSQL 解析后传入。授权信息在关系库里（`KnowledgeBase.user_id`），
        向量库 schema 里根本没有 user_id——检索层没有做授权判断的信息，
        就不该由它拼装集合名。

        与 `_execute_search` 同样返回 `SearchOutcome`：范围解析不出、
        索引丢失都是**故障**，不是"知识库里没有相关材料"。

        Args:
            query: 搜索查询
            top_k: 每个知识库返回的结果数量
            kb_scope: 允许检索的知识库，形如 kb_scope.KbScope.as_state()
        """
        def _failed(reason: str) -> SearchOutcome:
            return SearchOutcome(
                ok=False, failure_reason=reason,
                provider="local_kb", query=query,
            )

        if not self.milvus_service or not MILVUS_AVAILABLE:
            self.logger.warning("Milvus service not available for local search")
            return _failed("Milvus 服务不可用")

        scope = list(kb_scope or [])
        if not scope:
            # 没有可查的知识库 ≠ 查了没有。这里若返回空成功，报告就会
            # 出现"本地材料未发现相关内容"——而实际上一份材料都没查过。
            return _failed("未解析到可检索的知识库范围（检索范围须由服务端按用户授权解析）")

        try:
            from service.kb_scope import classify_missing_collection
        except ImportError:
            from app.service.kb_scope import classify_missing_collection

        try:
            # 生成查询向量
            query_vector = self.embedding_fn(query)
            if not query_vector:
                self.logger.error("Failed to generate embedding for query")
                return _failed("查询向量生成失败")

            self.logger.info(
                f"Executing local KB search over {len(scope)} collection(s): {query[:50]}..."
            )

            formatted_results = []
            broken = []
            for entry in scope:
                collection = entry.get("collection") or ""
                if not self.milvus_service.has_collection(collection):
                    is_broken, reason = classify_missing_collection(entry)
                    if is_broken:
                        broken.append(reason)
                    else:
                        self.logger.info(
                            f"知识库「{entry.get('kb_name')}」尚无文档，跳过"
                        )
                    continue

                results = self.milvus_service.search(
                    collection_name=collection,
                    query_vector=query_vector,
                    top_k=top_k,
                    # 集合隔离之外再按 case/KB 身份过滤。eval 集合和生产 KB
                    # 都由服务端 scope 提供该值，形成双重隔离。
                    kb_id=entry.get("kb_id") or None,
                )
                for r in results:
                    formatted_results.append({
                        'url': f"local://kb/{entry.get('kb_id', 'unknown')}/{r.get('doc_id', 'unknown')}",
                        'title': r.get('filename', 'N/A'),
                        # 保留完整入库片段供事实抽取使用。此前只给模型前 500
                        # 字，表格的数值列经常刚好被截掉，产生错误的缺失结论。
                        'summary': r.get('content', ''),
                        'snippet': r.get('content', '')[:200],
                        # 来源名带上知识库名：报告里"本地知识库"四个字
                        # 无法告诉复核人这条材料是谁提交的
                        'site_name': f"本地知识库／{entry.get('kb_name', '')}",
                        'date': '',
                        'score': r.get('score', 0),
                        'is_local': True,
                        'kb_id': entry.get('kb_id'),
                        'kb_name': entry.get('kb_name'),
                        'doc_id': r.get('doc_id'),
                        'chunk_index': r.get('chunk_index')
                    })

                # 报表口径（合并/母公司）必须在结果离开检索层之前标好：
                # 判定要回溯同文档的前序切片，只有这里还拿得到集合名。
                self._annotate_statement_scope(formatted_results, collection)

            # 索引丢失必须报故障。部分知识库查成功不足以让整次检索算成功——
            # 报告会据此写"本地材料未见相关记录"，而那部分材料根本没查过。
            if broken:
                return _failed("；".join(broken))

            # 跨知识库合并后按相似度排序，再截到 top_k。
            # 不排序就是"先解析到的知识库优先"，与相关性无关。
            formatted_results.sort(key=lambda r: r.get('score') or 0, reverse=True)
            formatted_results = formatted_results[:top_k]

            self.logger.info(
                f"Local search returned {len(formatted_results)} results for: {query[:30]}..."
            )
            return SearchOutcome(
                results=formatted_results, ok=True,
                provider="local_kb", query=query,
            )

        except Exception as e:
            self.logger.error(f"Local search error for '{query}': {e}")
            return _failed(f"{type(e).__name__}: {e}")

    def _apply_as_of_filter(self, results: List[Dict], as_of: str) -> List[Dict]:
        """
        按研究截止日过滤网页结果。

        ## 这一层只能做到"尽力而为"，且必须说出来

        结构化适配器和法定披露文件都有明确的发布日期，截止日闸门在那里能
        做扎实。通用网页检索做不到：Bocha 返回的 `datePublished` 经常为空，
        `dateLastCrawled` 是抓取时间而非发布时间，拿它当发布日会把一篇
        2019 年的旧闻判成今天发的。

        因此这里的规则是：
          - 能确认晚于截止日的 → 丢弃（这是能做准的部分）
          - 日期不明的         → **保留但打标**，并计数供报告披露

        丢弃日期不明的结果会让检索基本失效（大多数网页没有可靠日期）；
        静默保留则是假装做到了时点隔离。折中是保留 + 显式披露，
        由报告写明"N 条结果无法确认发布日期，未能施加截止日过滤"。
        """
        if not as_of:
            return results
        try:
            from service.verification import parse_iso
        except ImportError:
            from app.service.verification import parse_iso

        cutoff = parse_iso(as_of)
        if cutoff is None:
            self.logger.warning(f"[截止日] {as_of!r} 非法，跳过网页结果过滤")
            return results

        kept, dropped, undated = [], 0, 0
        for r in results:
            published = parse_iso((r.get("date") or "")[:19])
            if published is None:
                undated += 1
                r["as_of_status"] = "unknown_date"
                kept.append(r)
            elif published.date() > cutoff.date():
                dropped += 1
            else:
                r["as_of_status"] = "within_cutoff"
                kept.append(r)

        if dropped or undated:
            self.logger.info(
                f"[截止日 {as_of}] 丢弃 {dropped} 条晚于截止日的结果；"
                f"{undated} 条发布日期不明已保留并打标"
            )
        return kept

    async def _execute_search(
        self, query: str, count: int = 10, as_of: str = ""
    ) -> SearchOutcome:
        """
        执行网络搜索 - 使用 Bocha Web Search API

        返回 `SearchOutcome` 而非裸列表：调用方必须能区分"查了没有"与
        "没查成"，理由见 `SearchOutcome` 的类注释。

        `as_of` 给定时对结果施加截止日过滤——注意过滤在**客户端**做，
        不依赖检索接口的 freshness 区间语义。理由：正确性不该建立在
        未经验证的第三方参数行为上，接口若忽略或拒绝该参数，
        我们会以为已经隔离了时点而实际没有。

        ⚠️ **只缓存成功结果**。把失败也缓存下来，会让一次瞬时超时在整轮
        研究里反复复现为同一个"空结果"，且重试永远打不到真实数据源。
        """
        # 检查缓存。截止日进 key：同一查询在不同时点下的可用结果集不同，
        # 共用一份缓存会让先跑的那次决定后一次能看到什么。
        cache_key = hashlib.md5(f"{query}|{as_of}".encode()).hexdigest()
        if cache_key in self.search_cache:
            self.logger.debug(f"Cache hit for query: {query[:30]}...")
            return SearchOutcome(
                results=self.search_cache[cache_key], ok=True,
                provider="bocha_web_search", query=query,
            )

        def _failed(reason: str) -> SearchOutcome:
            return SearchOutcome(
                ok=False, failure_reason=reason,
                provider="bocha_web_search", query=query,
            )

        try:
            url = "https://api.bocha.cn/v1/web-search"
            payload = {
                "query": query,
                "summary": True,
                "count": count,
                "freshness": "noLimit"
            }
            headers = {
                'Authorization': f'Bearer {self.search_api_key}',
                'Content-Type': 'application/json'
            }

            self.logger.info(f"Executing Bocha search: {query[:50]}...")

            response = await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code != 200:
                self.logger.error(f"Bocha API error: {response.status_code} - {response.text[:200]}")
                return _failed(f"HTTP {response.status_code}")

            data = response.json()

            if data.get('code') != 200:
                self.logger.error(f"Bocha API returned error: {data.get('msg', 'Unknown error')}")
                return _failed(f"接口业务码 {data.get('code')}：{data.get('msg', '未知错误')}")

            webpages = data.get('data', {}).get('webPages', {}).get('value', [])
            self.logger.info(f"Bocha search returned {len(webpages)} results for: {query[:30]}...")

            results = []
            for item in webpages:
                if item.get('url') and (item.get('snippet') or item.get('summary')):
                    results.append({
                        'url': item.get('url'),
                        'title': item.get('name', 'N/A'),
                        'summary': item.get('summary', '') or item.get('snippet', ''),
                        'snippet': item.get('snippet', ''),
                        'site_name': item.get('siteName', 'N/A'),
                        'date': item.get('datePublished', '') or item.get('dateLastCrawled', '')
                    })

            # 截止日过滤（尽力而为，见 _apply_as_of_filter）
            results = self._apply_as_of_filter(results, as_of)

            # 缓存结果（仅成功路径）
            self.search_cache[cache_key] = results
            return SearchOutcome(
                results=results, ok=True,
                provider="bocha_web_search", query=query,
            )

        except requests.exceptions.Timeout:
            self.logger.error(f"Bocha search timeout for: {query[:30]}...")
            return _failed("请求超时（30秒）")
        except Exception as e:
            self.logger.error(f"Bocha search error for '{query}': {e}")
            return _failed(f"{type(e).__name__}: {e}")

    async def _analyze_search_results(
        self,
        query: str,
        section: Dict,
        results: List[Dict],
        hypotheses: List[Dict] = None,
        subject_name: str = "",
        due_diligence_mode: bool = False,
        active_field_ids: Optional[List[str]] = None,
    ) -> Optional[Dict]:
        """分析搜索结果

        Args:
            active_field_ids: 本次运行实际启用的清单字段（核心 + 场景扩展）。
                由调用方从 `state["field_checks"]` 传入——抽取目录必须与
                完整度、评分和报告消费的是同一份清单，否则会出现"某组字段
                被统计、被报告，却从未被提取过"的静默缺口。
        """
        if not results:
            return None

        # 格式化搜索结果
        #
        # ⚠️ 展示给模型的原文与校验器使用的原文**必须同口径**（BC-57）。
        # 此前这里截断到 1600 字符，而 rag_evidence_bridge 拿**全文**做逐字
        # 校验：实测 45.3% 的片段超过 1600 字符，落在尾部的「单位：千元」
        # 表头模型根本看不到，于是它无论怎么回答都过不了单位闸门。
        # 模型看得见的必须等于系统会校验的，否则闸门在惩罚一个不可满足的约束。
        formatted_results = []
        window = (DUE_DILIGENCE_EXTRACTION_WINDOW if due_diligence_mode
                  else EXTRACTION_WINDOW)
        for i, r in enumerate(results[:window]):
            formatted_results.append(f"""
[{i+1}] {r.get('title', 'N/A')}
URL: {r.get('url', '')}
来源: {r.get('site_name', 'N/A')}
日期: {r.get('date', 'N/A')}
是否本地文档: {bool(r.get('is_local'))}
KB ID: {r.get('kb_id', '')}
文档 ID: {r.get('doc_id', '')}
片段序号: {r.get('chunk_index', '')}
摘要/原文片段: {r.get('summary', '')[:SOURCE_EXCERPT_CHARS]}
""")

        # 格式化假设
        hypotheses_text = "无特定假设"
        if hypotheses:
            h_lines = []
            for h in hypotheses:
                status = h.get("status", "unverified")
                h_lines.append(f"- [{h.get('id')}] {h.get('content')} (状态: {status})")
            hypotheses_text = "\n".join(h_lines)

        try:
            from config.dd_checklist import CHECKLIST, CHECKLIST_BY_ID
        except ImportError:
            from app.config.dd_checklist import CHECKLIST, CHECKLIST_BY_ID
        section_id = str(section.get("id") or "")
        # ⚠️ 字段目录必须来自**本次运行实际启用的清单**，不能来自静态导入。
        #
        # 原实现遍历 `CHECKLIST`——那是 20 项核心的列表，**不含场景扩展项**。
        # 而提示词又明写"field_evidence 只能使用本章节上方列出的 field_id"，
        # 于是 BC-58 加的 14 个 factoring 字段从未出现在抽取提示词里，
        # 模型不可能提出它们。实测 case_01：14/14 场景字段零候选，
        # 而语料里应收账款减值 32 条、净额 31 条、海外收入 26 条正面命中。
        #
        # 这是 BC-49 形态的又一次复发，而且更隐蔽：场景清单接进了
        # `field_checks`、`completeness`、评分器双覆盖率和报告缺口清单——
        # **所有消费方都接上了，唯独没接产出方**。系统于是一直在精确报告
        # 一组从未被尝试过的字段"覆盖率 0/14"：数字正确，含义完全误导。
        #
        # 改为从 `active_field_ids`（即 state["field_checks"]）派生，
        # 抽取目录与完整度/评分/报告从此共用同一个事实来源（BC-52 的纪律：
        # 同一规则不得有第二处定义）。
        if active_field_ids:
            relevant_items = [
                CHECKLIST_BY_ID[field_id] for field_id in active_field_ids
                if field_id in CHECKLIST_BY_ID
                and (not due_diligence_mode
                     or CHECKLIST_BY_ID[field_id].section_id == section_id)
            ]
        else:
            # 非尽调路径与单测直接调用时的兜底：静态核心清单。
            relevant_items = [
                item for item in CHECKLIST
                if not due_diligence_mode or item.section_id == section_id
            ]
        # 风险汇总章节消费前七章的核查结果，不应再次让模型从检索片段
        # 自由抽取事实。直接返回稳定空结构，省掉一次高成本且无字段归属的调用。
        if due_diligence_mode and not relevant_items:
            return {
                "extracted_facts": [],
                "field_evidence": [],
                "hypothesis_evidence": [],
                "entities_discovered": [],
                "key_insights": [],
                "follow_up_queries": [],
                "source_tracing_queries": [],
                "missing_info": [],
                "source_quality_assessment": "固定清单无对应字段，跳过抽取",
            }
        field_catalog = "\n".join(
            f"- {item.field_id}: {item.field_name}（{item.description}）"
            for item in relevant_items
        )
        # 候选预算随本章节**实际可选字段数**伸缩，不再是一个常数。
        #
        # 原来写死 12：那是 sec_4 只有 4 个核心财务字段时标定的
        # （4 字段 × 近三期 = 12）。接入 14 项场景扩展后 sec_4 变成 14 个字段，
        # 12 条预算连"每个字段一条"都不够——模型会被迫丢掉大部分场景字段，
        # 于是刚接上的字段目录又被一个没跟着改的常数抵消掉。
        #
        # **加字段不得让一个已标定的常数继续沿用**——这正是 BC-58 记下的
        # "扩展必须是加法，不得顺手改判据"的反面：判据没跟着加，等于悄悄改了。
        #
        # 上限 42 = 14 字段 × 3 期，与 6000 token 的输出预算相容
        # （每条候选约 150 字符，42 条约 6.3K 字符）。
        candidate_budget = min(3 * max(len(relevant_items), 1), 42)
        due_diligence_instructions = ""
        if due_diligence_mode:
            due_diligence_instructions = """## 本次尽调抽取的额外输出约束
- 顶层必须是一个 JSON 对象，禁止用数组包裹整个对象
- 只填 field_evidence；extracted_facts、hypothesis_evidence、entities_discovered、key_insights、follow_up_queries、source_tracing_queries 全部输出空数组
- field_evidence 只能使用本章节上方列出的 field_id，总数最多 {candidate_budget} 条，每个 field_id 最多 3 条
- **不要输出 exact_quote，也不要输出 unit**：引文由系统从原文截取，单位由系统从表头解析
- 不写分析过程、解释、Markdown 或 JSON 之外的文字；证据不足就少输出，不得凑数""".format(
                candidate_budget=candidate_budget)
        prompt = self.SEARCH_ANALYSIS_PROMPT.format(
            query=query,
            section_title=section.get("title", ""),
            section_description=section.get("description", ""),
            subject_name=subject_name or "以研究问题中明确声明的企业为准",
            field_catalog=field_catalog,
            hypotheses=hypotheses_text,
            search_results="\n".join(formatted_results),
            due_diligence_instructions=due_diligence_instructions,
        )

        response = await self.call_llm(
            system_prompt="你是专业的研究分析师，擅长从搜索结果中提取结构化信息、验证假设并评估来源质量。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.2,
            max_tokens=6000 if due_diligence_mode else 16000,
        )

        return self.parse_json_response(response)

    async def deep_read_url(self, url: str, title: str, query: str) -> Optional[Dict]:
        """
        深度阅读网页内容

        TODO: 集成 Headless Browser（如 Playwright）实现真正的网页抓取
        目前使用简化版本
        """
        try:
            # 简化版：直接获取网页内容
            response = await asyncio.to_thread(
                requests.get,
                url,
                timeout=15,
                headers={'User-Agent': 'Mozilla/5.0'}
            )

            if response.status_code != 200:
                return None

            # 提取网页正文（去除 HTML 标签和噪音）
            content = self._extract_text_from_html(response.text, url)
            if not content or len(content) < 100:
                self.logger.warning(f"Extracted content too short for {url}")
                return None

            prompt = self.DEEP_READ_PROMPT.format(
                query=query,
                url=url,
                title=title,
                content=content
            )

            llm_response = await self.call_llm(
                system_prompt="你是专业的文档分析师。",
                user_prompt=prompt,
                json_mode=True
            )

            return self.parse_json_response(llm_response)

        except Exception as e:
            self.logger.error(f"Deep read error for {url}: {e}")
            return None

    def _extract_text_from_html(self, html: str, url: str = "", max_length: int = 12000) -> str:
        """
        从 HTML 中提取纯文本正文

        使用多种策略提取，优先级：
        1. trafilatura - 专业的网页正文提取库（效果最好）
        2. BeautifulSoup - 通用 HTML 解析（备选）
        3. 简单正则 - 最后的备选方案

        Args:
            html: 原始 HTML 内容
            url: 网页 URL（用于 trafilatura 优化）
            max_length: 最大返回长度

        Returns:
            提取的纯文本
        """
        text = ""

        # 方法 1: 使用 trafilatura（效果最好）
        if TRAFILATURA_AVAILABLE:
            try:
                text = trafilatura.extract(
                    html,
                    url=url,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,
                    favor_precision=True
                )
                if text and len(text) > 200:
                    self.logger.debug(f"Trafilatura extracted {len(text)} chars from {url}")
                    return text[:max_length]
            except Exception as e:
                self.logger.warning(f"Trafilatura extraction failed: {e}")

        # 方法 2: 使用 BeautifulSoup
        if BS4_AVAILABLE:
            try:
                soup = BeautifulSoup(html, 'lxml')

                # 移除无用标签
                for tag in soup(['script', 'style', 'nav', 'header', 'footer',
                                'aside', 'iframe', 'noscript', 'meta', 'link']):
                    tag.decompose()

                # 尝试找正文区域
                main_content = None
                for selector in ['article', 'main', '.content', '.article',
                                '#content', '#article', '.post', '.entry']:
                    main_content = soup.select_one(selector)
                    if main_content:
                        break

                if main_content:
                    text = main_content.get_text(separator='\n', strip=True)
                else:
                    # 找不到正文区域，提取 body
                    body = soup.find('body')
                    if body:
                        text = body.get_text(separator='\n', strip=True)
                    else:
                        text = soup.get_text(separator='\n', strip=True)

                if text and len(text) > 200:
                    # 清理多余空白
                    import re
                    text = re.sub(r'\n{3,}', '\n\n', text)
                    text = re.sub(r' {2,}', ' ', text)
                    self.logger.debug(f"BeautifulSoup extracted {len(text)} chars from {url}")
                    return text[:max_length]

            except Exception as e:
                self.logger.warning(f"BeautifulSoup extraction failed: {e}")

        # 方法 3: 简单正则（最后的备选）
        import re
        # 移除 script 和 style
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # 移除所有 HTML 标签
        text = re.sub(r'<[^>]+>', ' ', text)
        # 解码 HTML 实体
        text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&amp;', '&').replace('&quot;', '"')
        # 清理空白
        text = re.sub(r'\s+', ' ', text).strip()

        self.logger.debug(f"Regex extracted {len(text)} chars from {url}")
        return text[:max_length]

    def _compute_fact_fingerprint(self, content: str) -> str:
        """计算事实的语义指纹用于去重"""
        # 简化版：使用内容hash
        # TODO: 集成向量嵌入进行语义相似度比较
        import re
        # 提取数字和关键词作为指纹
        numbers = re.findall(r'\d+\.?\d*', content)
        keywords = re.findall(r'[\u4e00-\u9fa5]{2,4}', content)[:5]
        fingerprint = f"{','.join(numbers[:3])}|{','.join(keywords)}"
        return hashlib.md5(fingerprint.encode()).hexdigest()[:16]

    def _is_duplicate_fact(self, content: str, source_url: str) -> bool:
        """检查事实是否重复"""
        fingerprint = self._compute_fact_fingerprint(content)

        # 检查指纹是否已存在
        if fingerprint in self.fact_fingerprints:
            existing_url = self.fact_fingerprints[fingerprint]
            # 如果是同一个来源，不算重复（可能是更详细的版本）
            if existing_url == source_url:
                return False
            self.logger.debug(f"Duplicate fact detected: {content[:50]}...")
            return True

        # 保存指纹
        self.fact_fingerprints[fingerprint] = source_url
        return False

    def _update_knowledge_graph(self, state: ResearchState, entities: List[Dict]) -> None:
        """更新知识图谱"""
        graph = state.get("knowledge_graph", {"nodes": [], "edges": []})
        existing_nodes = {n.get("name") for n in graph["nodes"]}

        for entity in entities:
            name = entity.get("name", "")
            if not name or name in existing_nodes:
                continue

            # 添加节点
            graph["nodes"].append({
                "id": f"node_{len(graph['nodes'])}",
                "name": name,
                "type": entity.get("type", "unknown"),
                "discovered_at": datetime.now().isoformat()
            })
            existing_nodes.add(name)

            # 添加边（关系）
            for relation in entity.get("relations", []):
                # 简单解析关系
                graph["edges"].append({
                    "source": name,
                    "relation": relation,
                    "discovered_at": datetime.now().isoformat()
                })

        state["knowledge_graph"] = graph

    def _update_hypothesis_status(self, state: ResearchState, evidence: List[Dict]) -> None:
        """根据证据更新假设状态"""
        hypotheses = state.get("hypotheses", [])

        for ev in evidence:
            h_id = ev.get("hypothesis_id", "")
            ev_type = ev.get("evidence_type", "")
            ev_summary = ev.get("evidence_summary", "")

            for h in hypotheses:
                if h.get("id") == h_id:
                    if ev_type == "supports":
                        h["evidence_for"].append(ev_summary)
                        if len(h["evidence_for"]) >= 2:
                            h["status"] = "supported"
                    elif ev_type == "refutes":
                        h["evidence_against"].append(ev_summary)
                        if len(h["evidence_against"]) >= 2:
                            h["status"] = "refuted"
                    else:
                        if h["status"] == "unverified":
                            h["status"] = "partially_supported"
                    break

        state["hypotheses"] = hypotheses
