# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""
DeepResearch V2.0 - 状态管理模块

实现全局工作记忆（Global Working Memory），所有Agent共享此状态。
使用 TypedDict 确保类型安全，与 LangGraph 完美兼容。
"""

from typing import TypedDict, List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import hmac
import json
import os
from datetime import datetime
from enum import Enum

try:
    from service.investigation_layer import empty_investigation
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.investigation_layer import empty_investigation


ADMIN_PROFILE_SNAPSHOT_BINDING_VERSION = 2
_ADMIN_PROFILE_SNAPSHOT_BINDING_KEYS = frozenset({"version", "algorithm", "mac"})
_ADMIN_PROFILE_SNAPSHOT_HMAC_ENV = "ADMIN_PROFILE_SNAPSHOT_HMAC_KEY"
_ADMIN_PROFILE_SNAPSHOT_HMAC_CONTEXT = b"admin-profile-snapshot-binding-v2"


def _admin_profile_snapshot_binding_bytes(
    session_id: Any,
    profile: Any,
    ref: Any,
    scenario: Any,
) -> bytes:
    """Return the exact, versioned envelope protected across a resume.

    ``profile`` already carries service-issued decorations, but the outer ref
    and selected scenario are separately consumed by the graph.  They must be
    covered too; hashing profile alone lets a restored state change its
    checklist scenario without changing the payload hash.
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("管理端企业档案快照缺少合法 session_id")
    if not isinstance(scenario, str):
        raise ValueError("管理端企业档案快照场景非法")
    try:
        return json.dumps(
            {
                "version": ADMIN_PROFILE_SNAPSHOT_BINDING_VERSION,
                "session_id": session_id,
                "profile": profile,
                "ref": ref,
                "scenario": scenario,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("管理端企业档案快照绑定内容必须是纯 JSON") from exc


def _admin_profile_snapshot_hmac_key() -> bytes:
    """Derive a purpose-separated key from configured server secrets."""
    key = os.getenv(_ADMIN_PROFILE_SNAPSHOT_HMAC_ENV) or os.getenv("JWT_SECRET_KEY")
    if not key:
        raise ValueError(
            f"缺少服务端 {_ADMIN_PROFILE_SNAPSHOT_HMAC_ENV} 或 JWT_SECRET_KEY，"
            "拒绝创建或恢复管理端企业档案快照"
        )
    # The fallback deliberately reuses only the configured server secret, not
    # the JWT signing key bytes directly.  A fixed context derives a separate
    # HMAC sub-key so tokens and checkpoint bindings are different protocols.
    return hmac.new(
        key.encode("utf-8"),
        _ADMIN_PROFILE_SNAPSHOT_HMAC_CONTEXT,
        hashlib.sha256,
    ).digest()


def create_admin_profile_snapshot_binding(
    session_id: Any,
    profile: Any,
    ref: Any,
    scenario: Any,
) -> Dict[str, Any]:
    """Create the HMAC envelope persisted with a managed-profile run."""
    body = _admin_profile_snapshot_binding_bytes(session_id, profile, ref, scenario)
    return {
        "version": ADMIN_PROFILE_SNAPSHOT_BINDING_VERSION,
        "algorithm": "hmac-sha256",
        "mac": hmac.new(_admin_profile_snapshot_hmac_key(), body, hashlib.sha256).hexdigest(),
    }


def verify_admin_profile_snapshot_binding(
    binding: Any,
    session_id: Any,
    profile: Any,
    ref: Any,
    scenario: Any,
) -> None:
    """Fail closed unless the stored binding exactly covers this snapshot."""
    if not isinstance(binding, dict) or set(binding) != _ADMIN_PROFILE_SNAPSHOT_BINDING_KEYS:
        raise ValueError("管理端企业档案快照绑定缺失或形状非法")
    if binding.get("version") != ADMIN_PROFILE_SNAPSHOT_BINDING_VERSION:
        raise ValueError("管理端企业档案快照绑定版本不受支持")
    if binding.get("algorithm") != "hmac-sha256":
        raise ValueError("管理端企业档案快照绑定算法非法")
    supplied = binding.get("mac")
    if not isinstance(supplied, str) or len(supplied) != 64:
        raise ValueError("管理端企业档案快照绑定 MAC 非法")
    body = _admin_profile_snapshot_binding_bytes(session_id, profile, ref, scenario)
    expected = hmac.new(_admin_profile_snapshot_hmac_key(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise ValueError("管理端企业档案快照绑定不一致")


class ResearchPhase(str, Enum):
    """研究阶段状态机"""
    INIT = "init"                    # 初始化
    PLANNING = "planning"            # 规划阶段
    RESEARCHING = "researching"      # 深度探索阶段
    ANALYZING = "analyzing"          # 数据分析阶段
    WRITING = "writing"              # 撰写阶段
    REVIEWING = "reviewing"          # 对抗审核阶段
    RE_RESEARCHING = "re_researching"  # 补充搜索阶段（审核发现缺失信息后）
    REVISING = "revising"            # 修订阶段（仅文字修改）
    COMPLETED = "completed"          # 完成


@dataclass
class Section:
    """报告章节"""
    id: str
    title: str
    description: str
    section_type: Literal["qualitative", "quantitative", "mixed"]  # 定性/定量/混合
    status: Literal["pending", "researching", "drafted", "reviewed", "final"]
    content: str = ""
    sources: List[str] = field(default_factory=list)
    subsections: List['Section'] = field(default_factory=list)
    requires_data: bool = False
    requires_chart: bool = False


class FieldCheck(TypedDict):
    """
    核查项运行时状态（清单定义见 config/dd_checklist.py）

    这是反幻觉设计的地基：把"查没查到"从散落在正文里的措辞，
    变成显式、可计数、下游可消费的结构化数据。

    status 语义（务必区分，写错会误导授信决策）：
        verified       已核实，有值且可溯源到 sources 中的 fact
        unverified     尝试过但未取到。**不等于"不存在"**
        conflicting    多源数据不一致，本身即风险信号（v0.4 多源接入后才可能出现）
        not_applicable 该项对此类主体无意义（如个体工商户无股权结构）
    """
    field_id: str
    field_name: str
    category: str
    section_id: str
    required: bool
    status: str                      # verified|unverified|conflicting|not_applicable
    value: Any                       # verified 时的值，其余为 None
    sources: List[str]               # fact_id 列表，指向 facts 中的取证记录
    attempted_sources: List[str]     # 尝试过的数据源标识
    failure_reason: str              # unverified 时必填
    conflict_detail: List[Dict[str, Any]]  # conflicting 时填 [{source, value}]
    checked_at: str

    # —— 溯源字段（v0.6）。verified / conflicting 必须齐备，否则 fail-closed ——
    # 见 service/verification.py。设为可选是为了兼容旧检查点：
    # 缺失时走显式降级路径，而不是被当成合法来源静默通过。
    verification_origin: str         # initial_profile | structured_adapter
    evidence_ids: List[str]          # 指向 evidence_store，structured_adapter 必填
    source_adapter: str              # 产出该结论的适配器标识
    # initial_profile 的管理员快照会记录字段级来源链；静态/旧档案可缺该字段，
    # 继续按其历史 retrieved_at 重放。每项仅包含覆盖该 field_id 的来源，且
    # 保留 source_id/name/issuer/source_type/retrieved_at/as_of_date/reference/sha256。
    profile_sources: List[Dict[str, Any]]
    retrieved_at: str                # 字段来源中最新的取证时间；静态旧档案按原逻辑
    as_of_date: str                  # 字段来源中最新的事实日期；研究截止日优先使用它


@dataclass
class Fact:
    """结构化事实"""
    id: str
    content: str
    source_url: str
    source_name: str
    source_type: Literal["official", "academic", "news", "report", "self_media"]  # 来源类型
    credibility_score: float  # 可信度评分 0-1
    extracted_at: datetime
    related_sections: List[str] = field(default_factory=list)  # 关联章节ID
    verified: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DataPoint:
    """数据点"""
    id: str
    name: str
    value: Any
    unit: str
    year: Optional[int]
    source: str
    confidence: float


@dataclass
class Chart:
    """图表配置"""
    id: str
    title: str
    chart_type: Literal["line", "bar", "pie", "scatter", "table", "heatmap"]
    data: Dict[str, Any]
    code: str  # 生成图表的Python代码
    image_path: Optional[str] = None
    section_id: Optional[str] = None


@dataclass
class CriticFeedback:
    """评论家反馈"""
    id: str
    target_section: str
    # 尽调专项类型（见 service/review_verdict.py::DD_BLOCKING_ISSUE_TYPES）
    # 与通用类型并列。前者不接受 severity 降级，后者按严重度裁决。
    issue_type: Literal[
        "unverified_as_fact", "conflict_silently_resolved", "unsupported_risk_conclusion",
        "subject_attribution_error", "post_cutoff_evidence", "review_not_executed",
        "missing_source", "logic_error", "bias", "hallucination", "outdated", "incomplete",
    ]
    severity: Literal["critical", "major", "minor"]
    description: str
    suggestion: str
    resolved: bool = False


@dataclass
class AgentLog:
    """Agent执行日志"""
    timestamp: datetime
    agent: str
    action: str
    input_summary: str
    output_summary: str
    duration_ms: int
    tokens_used: int = 0


class ResearchState(TypedDict):
    """
    LangGraph 状态定义

    这是整个研究过程的全局状态，所有Agent都在读写这个状态。
    使用 TypedDict 以获得类型提示和 LangGraph 兼容性。
    """
    # 基础信息
    query: str                              # 用户原始问题
    session_id: str                         # 会话ID
    phase: str                              # 当前阶段
    iteration: int                          # 当前迭代轮次
    max_iterations: int                     # 最大迭代次数

    # 搜索模式配置
    search_web: bool                        # 是否启用网络搜索
    search_local: bool                      # 是否启用本地知识库搜索

    # 本地知识库检索范围（P0-1）——[{collection, kb_id, kb_name, document_count}]
    #
    # **必须由服务端按用户授权解析后写入**，不接受客户端传集合名。
    # Milvus schema 里没有 user_id（只有 doc_id/kb_id/filename/content/
    # chunk_index/vector），授权信息只存在于 PostgreSQL 的
    # KnowledgeBase.user_id——检索层没有做这个判断的信息。
    # 空列表且启用了本地检索时，Scout 报**故障**而非空结果：
    # 没得查 ≠ 查了没有。见 service/kb_scope.py
    kb_scope: List[Dict[str, Any]]

    # 研究截止日（P0-3）。ISO 日期，留空 = 不设时点，行为与引入本字段前一致。
    #
    # 语义是"本次判断以该日为准"：晚于该日**发布/发生**的事实不得进入判断。
    # 判据是证据的 as_of_date 而非 retrieved_at——今天跑一个截止日在去年的
    # 案子，所有证据都是今天取到的，拿取证时间去比会把每条都判成越界。
    # 见 service/verification.py::check_as_of
    #
    # 三个用途：回溯评测（案例包 S011/S012 那种后验数据不得反哺）、
    # 报告可复现、授信档案能回答"批这笔时我们看到的是什么"。
    as_of: str

    # 任务语义。尽调模式不再以 companies.json 是否命中为判据：外部文件/RAG
    # 的主体通常不在测试档案中，但仍必须先建立固定清单再开始取证。
    subject_name: str                       # 调用方声明的尽调主体（可为空）
    business_type: str                      # 保理/授信/供应链金融等业务场景
    due_diligence_mode: bool                # 是否启用固定二十项清单与证据闸门

    # 管理端企业档案快照（Stage 3）。路由层只按 ID 从受控服务读取，
    # 这里保存的是该次运行已冻结的纯 JSON，而不是可再次查询的档案 ID。
    # 检查点据此重放历史运行，避免"恢复时读到了更新后的档案"。
    provided_company_profile: Dict[str, Any]
    admin_profile_ref: Dict[str, Any]       # {id, revision, content_sha256, source}
    admin_profile_scenario: str              # 受控服务给出的场景标识；仍须由清单映射解析
    # 覆盖快照可见 payload 的运行内哈希。持久化 ref 的 content_sha256 同时
    # 覆盖未暴露的 materials，不能在图中重算；该哈希用于检查点篡改检测。
    admin_profile_payload_sha256: str
    # v2：HMAC 覆盖 session_id/profile/ref/scenario。普通 payload 哈希只覆盖
    # profile，不能防止恢复时把同一份档案改绑到另一场景或会话。
    admin_profile_snapshot_binding: Dict[str, Any]
    # LangGraph 每个节点边界的完整业务态签名。业务检查点另有 state_json
    # 外的一对一完整性记录；两套存储分别验证，不能互相充当信任源。
    checkpoint_graph_seal: Dict[str, Any]

    # 尽调对象（v0.1：来自硬编码档案；v0.4 起改由数据源适配层提供）
    company_name: str                       # 识别出的尽调对象企业名，未识别则为空
    credit_context: str                     # 授信申请背景，拼入 Architect 规划提示词

    # 核查清单（v0.2）——尽调的核心工作记忆
    field_checks: List[FieldCheck]          # 20 项固定清单及其核查状态
    completeness: Dict[str, Any]            # 核实率统计，由 compute_completeness 产出

    # 结构化企业档案（v0.5 接入评分卡时引入）
    # 风险评分卡需要的是结构化数值（负债率、被执行笔数…），不是 facts 里的自然语言，
    # 因此原始档案必须留在 state 中。缺失时评分卡走 fail-closed，不得当作"无风险"。
    company_profile: Dict[str, Any]

    # 评分卡与额度测算**实际消费**的那份数据：原始档案 + 结构化适配器补丁
    # 合并后的结果（`verification.build_scoring_view`）。
    # 人工复核覆盖等级后要按它重算额度（BC-70）；
    # 它也回答了一个此前无法回答的审计问题——评分依据的到底是哪份数据。
    scoring_view: Dict[str, Any]

    # 结构化证据库（v0.6）：{evidence_id: StructuredEvidence}
    # 由结构化适配器写入，供重放校验按 evidence_ids 回查。
    # 与 facts 的区别：facts 是给 LLM 读的自然语言，evidence 是给程序做
    # 等值比对的结构化记录——自然语言无法承担字段级重放。
    # 见 service/verification.py
    evidence_store: Dict[str, Any]
    rag_evidence_candidates: List[Dict[str, Any]]  # LLM 候选；通过确定性校验前不算证据
    rag_evidence_rejections: List[Dict[str, Any]]  # 被主体/原文/日期/字段规则拒绝的候选
    rag_evidence_summary: Dict[str, Any]            # 最终接纳/冲突/拒绝原因统计

    # 风险评分结果（v0.5）——由 DataAnalyst 阶段的纯规则评分卡产出
    # 结构见 service/risk_scorecard.py::score()。
    # ⚠️ 消费方必须同时读 level 与 gates_applied，只看 composite_score 会误判
    risk_assessment: Dict[str, Any]

    # 规划输出
    outline: List[Dict[str, Any]]           # 动态大纲 (Section序列化)
    mind_map: Dict[str, Any]                # 知识图谱/思维导图
    key_entities: List[str]                 # 关键实体
    research_questions: List[str]           # 待研究的子问题
    hypotheses: List[Dict[str, Any]]        # 研究假设（假设驱动研究）
    knowledge_graph: Dict[str, Any]         # 知识图谱 {nodes: [], edges: []}

    # 知识库
    facts: List[Dict[str, Any]]             # 结构化事实库
    data_points: List[Dict[str, Any]]       # 数据点
    raw_sources: List[Dict[str, Any]]       # 原始来源（网页内容）

    # 检索故障留痕（P0-2）——[{provider, query, failure_reason, occurred_at}]
    # 检索**失败**与检索**无结果**必须分开记录：舆情、监管处罚这类
    # absence_meaningful=True 的字段，"查了没有"是合法的正面结论，
    # "没查成"绝不是。两者混同 = 把一次 API 超时写成"未发现负面舆情"。
    # 对应案例包来源目录里 S009/S010 那两行失败记录及其免责声明。
    # 见 agents/scout.py::SearchOutcome
    search_failures: List[Dict[str, Any]]

    # 章节级抽取故障留痕（BC-56）——[{section_id, section_title,
    # failure_reason, failure_kind, occurred_at}]
    # 与 search_failures 同一条纪律，只是粒度更粗：检索**成功**但抽取环节
    # 崩溃/超时时，这一章的证据同样是空的。空证据在下游会被读成
    # "材料未提供"，所以失败必须单独留痕，并进 errors 阻断完成态。
    # 见 agents/scout.py::_record_section_failure
    section_failures: List[Dict[str, Any]]

    # 检索计划不足留痕（BC-62）——[{section_id, atomic_queries, min_expected, ...}]
    # 检索广度此前完全不可观测：模型把多个主题连写成一条时，这一章只发一次
    # 检索、可得材料腰斩，而运行状态一律 completed。这**不是故障**（仍能产出
    # 证据），所以不进 search_failures；但必须单独留痕，否则下次换模型会静默复演。
    # 见 agents/scout.py::_plan_section_queries
    query_plan_shortfalls: List[Dict[str, Any]]

    # 抽取窗口丢弃留痕——[{section_id, retrieved, sent_to_extraction, dropped}]
    # 检索到的片段多于送入抽取的条数时，差额必须可见：实测 sec_4 检索 28 条
    # 只送 15 条，6 个字段的正确材料落在被丢弃的那 13 条里，而整个过程无声无息。
    # 见 agents/scout.py::EXTRACTION_WINDOW
    extraction_window_drops: List[Dict[str, Any]]

    # 跨通道一致性无法判定留痕（BC-68）——[{field_id, reason, rag_candidates}]
    # 档案与 RAG 对同一字段各有取值，但两侧表示形态不可比（档案存渲染后的
    # 展示字符串，RAG 存文档里的单元格）。**"无法判定"既不是"一致"也不是
    # "矛盾"**：报成矛盾会误报率 100%、把真矛盾淹掉；藏起来则重犯 BC-51。
    # 见 service/rag_evidence_bridge.py::_cross_channel_conflict
    cross_channel_undecidable: List[Dict[str, Any]]

    # —— 调查层（B 层，双轨产出计划阶段 1）——
    #
    # 图表 / 知识图谱 / 调查发现。**与 A 层物理隔离**：
    # 评级、额度、完整度、证据附录一律不读这个键。
    #
    # 为什么另开一个键而不是复用下面的 `charts` / `knowledge_graph`：
    # 那两个字段的每一处消费者都是在"尽调模式下它们恒为空"的前提下写的，
    # 往里填东西等于把隔离责任摊给下游每一个调用点。见 service/investigation_layer.py
    investigation: Dict[str, Any]

    # —— 跨层一致性判定（阶段 2）——
    #
    # B 层的发现是否构成对 A 层结论的实质挑战。**纯规则、只观察**：
    # 它不写 risk_assessment、不改 requires_human_review、不改额度。
    # 单独一个键而不是塞进 investigation：它既不属于 A 层也不属于 B 层，
    # 是两者的比较器（见 service/cross_layer_verdict.py）。
    cross_layer_verdict: Dict[str, Any]

    # 调查层语料的丢弃留痕（BC-75）。与 `extraction_window_drops` 同一性质：
    # 不是故障，但"模型没找到东西"与"材料压根没送进去"必须可分辨。
    investigation_corpus_drops: List[Dict[str, Any]]

    # 调查层语料已处理过的章节（BC-78）。按章分配额度要知道"还剩几章"，
    # 否则只能先到先得——第一章拿满，后面整章丢弃且不留痕。
    investigation_sections_seen: List[str]

    # 分析输出
    charts: List[Dict[str, Any]]            # 生成的图表
    code_executions: List[Dict[str, Any]]   # 代码执行记录
    insights: List[str]                     # 数据洞察

    # 写作输出
    draft_sections: Dict[str, str]          # 章节草稿 {section_id: content}
    final_report: str                       # 最终报告
    references: List[Dict[str, Any]]        # 参考文献

    # 审核反馈
    critic_feedback: List[Dict[str, Any]]   # 评论家反馈
    unresolved_issues: int                  # 未解决问题数
    quality_score: float                    # 质量评分
    pending_search_queries: List[str]       # 待执行的补充搜索查询（审核后需要补充的）

    # 元数据
    logs: List[Dict[str, Any]]              # 执行日志
    errors: List[str]                       # 错误记录
    messages: List[Dict[str, Any]]          # Agent间消息（用于流式输出）

    # —— 运行期内部键（v0.6）——
    # ⚠️ 必须在此声明，不能只在运行时往 state 里塞。
    # LangGraph 把 TypedDict 的字段当作**通道白名单**：节点返回的未声明键
    # 会被整个丢弃。v0.6 首次把编排交给 LangGraph 时，`_cancelled` 因为
    # 没声明而无法跨节点传播——取消守卫边永远读到 None，取消后流程照常
    # 跑到底并发出终局事件（BC-43）。
    _cancelled: bool                        # 取消标志，守卫边据此路由到 END
    _user_id: str                           # 检查点归属用户
    _ui_state: Dict[str, Any]               # 前端恢复用的 UI 投影
    _checkpoint_integrity_mode: str         # managed_v1 / standard_v1；业务落库时移除


def create_initial_state(
    query: str,
    session_id: str,
    search_web: bool = True,
    search_local: bool = False,
    as_of: str = "",
    kb_scope: Optional[List[Dict[str, Any]]] = None,
    subject_name: str = "",
    business_type: str = "",
    due_diligence: Optional[bool] = None,
    investigation: Optional[bool] = None,
    provided_company_profile: Optional[Dict[str, Any]] = None,
    admin_profile_ref: Optional[Dict[str, Any]] = None,
    admin_profile_scenario: str = "",
) -> ResearchState:
    """创建初始状态

    Args:
        query: 用户查询
        session_id: 会话ID
        search_web: 是否启用网络搜索（默认True）
        search_local: 是否启用本地知识库搜索（默认False）
        as_of: 研究截止日（ISO 日期）。默认空 = 不设时点闸门
        kb_scope: 本地知识库检索范围，由服务端按用户授权解析
    """
    dd_markers = ("尽职调查", "尽调", "贷前", "授信", "保理")
    dd_mode = bool(due_diligence) if due_diligence is not None else bool(
        subject_name or business_type or any(marker in query for marker in dd_markers)
    )
    frozen_profile = deepcopy(provided_company_profile or {})
    frozen_ref = deepcopy(admin_profile_ref or {})
    frozen_scenario = (admin_profile_scenario or "").strip()
    try:
        profile_bytes = json.dumps(
            frozen_profile, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("管理端企业档案快照必须是纯 JSON") from exc
    managed_snapshot_requested = bool(frozen_profile) or bool(frozen_ref)
    binding = (
        create_admin_profile_snapshot_binding(
            session_id, frozen_profile, frozen_ref, frozen_scenario
        )
        if managed_snapshot_requested else {}
    )
    return ResearchState(
        query=query,
        session_id=session_id,
        phase=ResearchPhase.INIT.value,
        iteration=0,
        max_iterations=3,
        search_web=search_web,
        search_local=search_local,
        kb_scope=list(kb_scope or []),
        as_of=as_of,
        subject_name=subject_name.strip(),
        business_type=business_type.strip(),
        due_diligence_mode=dd_mode,
        # 不保留调用者对象引用。快照会进入检查点，后续外部档案更新不得影响
        # 本次运行或已经开始的尽调。
        provided_company_profile=frozen_profile,
        admin_profile_ref=frozen_ref,
        admin_profile_scenario=frozen_scenario,
        admin_profile_payload_sha256=hashlib.sha256(profile_bytes).hexdigest() if frozen_profile else "",
        admin_profile_snapshot_binding=binding,
        checkpoint_graph_seal={},
        company_name="",
        credit_context="",
        field_checks=[],
        completeness={},
        company_profile={},
        scoring_view={},
        evidence_store={},
        rag_evidence_candidates=[],
        rag_evidence_rejections=[],
        rag_evidence_summary={},
        risk_assessment={},
        outline=[],
        mind_map={},
        key_entities=[],
        research_questions=[],
        hypotheses=[],  # 假设驱动研究
        knowledge_graph={"nodes": [], "edges": []},  # 知识图谱
        facts=[],
        data_points=[],
        raw_sources=[],
        search_failures=[],
        section_failures=[],
        query_plan_shortfalls=[],
        extraction_window_drops=[],
        cross_channel_undecidable=[],
        # 调查层默认随尽调模式开启（计划 9.1：生产默认开、可关闭）。
        # **封闭评测必须显式传 False**——B 层是一次额外的模型调用，
        # 留着它会给每轮评测加上不可复现的时间与费用，而评测只计 A 层的分。
        cross_layer_verdict={},
        investigation_corpus_drops=[],
        investigation_sections_seen=[],
        investigation={**empty_investigation(),
                       "enabled": dd_mode if investigation is None else bool(investigation)},
        charts=[],
        code_executions=[],
        insights=[],
        draft_sections={},
        final_report="",
        references=[],
        critic_feedback=[],
        unresolved_issues=0,
        quality_score=0.0,
        pending_search_queries=[],
        logs=[],
        errors=[],
        messages=[],
        _checkpoint_integrity_mode=("managed_v1" if managed_snapshot_requested else "standard_v1"),
    )


def section_to_dict(section: Section) -> Dict[str, Any]:
    """Section 序列化"""
    return {
        "id": section.id,
        "title": section.title,
        "description": section.description,
        "section_type": section.section_type,
        "status": section.status,
        "content": section.content,
        "sources": section.sources,
        "subsections": [section_to_dict(s) for s in section.subsections],
        "requires_data": section.requires_data,
        "requires_chart": section.requires_chart
    }


def fact_to_dict(fact: Fact) -> Dict[str, Any]:
    """Fact 序列化"""
    return {
        "id": fact.id,
        "content": fact.content,
        "source_url": fact.source_url,
        "source_name": fact.source_name,
        "source_type": fact.source_type,
        "credibility_score": fact.credibility_score,
        "extracted_at": fact.extracted_at.isoformat(),
        "related_sections": fact.related_sections,
        "verified": fact.verified,
        "metadata": fact.metadata
    }
