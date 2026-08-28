# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
贷前尽职调查核查清单定义

设计要点（见 docs/DESIGN_CORE_MECHANISMS.md 第一部分）：

尽调的本质不是"收集到什么写什么"，而是"对一张固定清单逐项给结论"。
查不到本身就是结论——因此缺失必须显式、可计数、并能约束最终评级。

⚠️ 清单用**代码常量**定义，不由 LLM 生成。
   这个结构要被计数、被评测、与 ground truth 比对，
   引入模型变异性会让"核实率"这个指标失去意义。
   Architect 只负责裁剪（如个体工商户无股权结构），不负责创造。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ChecklistItem:
    """核查项定义（静态元数据，不含运行时状态）"""
    field_id: str
    field_name: str
    category: str          # basic|equity|financial|judicial|relation|operation|opinion
    section_id: str        # 归属的报告章节
    required: bool         # 必查项未核实会触发风险等级下限（v0.5 完整度闸门）
    description: str       # 供 Writer 理解该项要交代什么
    primary_source: str    # 主数据源，未取到时写入 attempted_sources
    data_source_status: str = "available"
    """
    该项**是否存在可用的获取路径**（BC-18）。

    available       —— 有数据源。查不到属于「信息缺口」：可能是数据源故障、
                       主体存疑，重试或换源有可能解决。
    not_implemented —— 系统尚不具备核查该项的能力，重试永远不会成功。

    ## 为什么必须区分，以及区分之后**不能**做什么

    区分的目的**不是**把能力缺失从风险中排除——借款人的担保圈敞口是未知的，
    不管未知的原因是什么。因为"我们查不了"就不计入风险，正是完整度闸门
    当初要防的那件事（查不到 ≠ 没问题）。

    区分的目的是让**闸门理由准确**。此前能力缺失伪装成「维度核实率不足」，
    导致每份报告都挂同一条闸门——一个永远亮的告警等于没有告警，
    风控人员会学会无视它，真正的信息缺口反而被淹没。

    因此 `not_implemented` 的项：
      - 仍然计入 `verified_rate` 的分母（诚实：15 项必查确实只核实了 14 项）
      - **不**计入维度核实率的分母（它不是这次没查到，不该拖累同维度其它项）
      - 触发一条**专用闸门**，明确写出是能力缺失、须线下人工核查
    """
    absence_meaningful: bool = True
    """
    数据源查询后返回空结果时，「无记录」是否构成有效结论。

    True  —— 事件型字段（涉诉、失信、对外担保、舆情…）。
             这类事件可以合法地不存在，查询返回空 = 已核实的正面结论。

    False —— 属性型字段（工商登记、股东结构、营业收入…）。
             一个存续经营的企业必然具备这些属性。查询返回空不是"没有"，
             而是**异常信号**：主体可能不存在、已注销，或数据源故障。
             绝不能表述为"经查询无相关记录"——那会把 critical 风险
             粉饰成中性结论。
    """


#: 事件型字段查询后返回空结果时写入的取值。
#:
#: **这是一个已核实的正面结论**，不是信息缺口——`absence_meaningful=True`
#: 的字段（涉诉、失信、对外担保、舆情…）可以合法地不存在，
#: 查询返回空就等于"确认没有"。
#:
#: 收成常量是因为跨层判定要靠识别这句话来判断
#: 「A 层说没有、B 层说有」这种最干净的矛盾形态。此前它在三处各写了一遍
#: 字面量，任何一处改了措辞，检测器都会**静默失效**——
#: 而失效的表现是"没检出矛盾"，与"确实没有矛盾"完全同形。
NO_RECORD_VALUE = "经查询，无相关记录"


# 维度 → 章节。与 Architect 生成的 8 章尽调提纲对齐。
CATEGORY_TO_SECTION: Dict[str, str] = {
    "basic": "sec_1",       # 企业基本情况
    "equity": "sec_2",      # 股权结构与实际控制人
    "operation": "sec_3",   # 经营状况
    "financial": "sec_4",   # 财务分析
    "judicial": "sec_5",    # 司法与合规风险
    "relation": "sec_6",    # 关联关系与对外担保
    "opinion": "sec_7",     # 舆情扫描
    # sec_8（风险汇总与授信建议）不直接对应核查项，它消费全部清单结果
}


def _item(field_id, field_name, category, required, description, primary_source,
          absence_meaningful=True, data_source_status="available"):
    return ChecklistItem(
        field_id=field_id,
        field_name=field_name,
        category=category,
        section_id=CATEGORY_TO_SECTION[category],
        required=required,
        description=description,
        primary_source=primary_source,
        data_source_status=data_source_status,
        absence_meaningful=absence_meaningful,
    )


# 完整清单：15 项必查 + 5 项选查
CHECKLIST: List[ChecklistItem] = [
    # —— 企业基本情况 ——
    _item("registration", "工商登记基本信息", "basic", True,
          "统一社会信用代码、注册资本、实缴资本、成立日期、法定代表人、注册地址、企业类型",
          "business_registry", absence_meaningful=False),
    _item("business_scope", "经营范围", "basic", True,
          "登记经营范围，与实际业务是否一致", "business_registry", absence_meaningful=False),
    _item("operating_status", "登记状态", "basic", True,
          "存续/在业/吊销/注销。非存续状态是一票否决级风险", "business_registry", absence_meaningful=False),

    # —— 股权结构与实际控制人 ——
    _item("shareholders", "股东结构", "equity", True,
          "股东名称、类型、持股比例、认缴出资额", "business_registry", absence_meaningful=False),
    _item("actual_controller", "实际控制人", "equity", True,
          "实际控制人认定及认定依据。不得由持股比例自行推断", "business_registry", absence_meaningful=False),
    _item("external_investment", "对外投资", "equity", False,
          "作为股东对外投资的企业及持股情况", "business_registry"),

    # —— 经营状况 ——
    _item("bidding_record", "中标记录", "operation", False,
          "中标项目、金额、时间。是经营真实性的正面佐证", "bidding"),

    # —— 财务分析 ——
    _item("revenue", "营业收入", "financial", True,
          "近三年营业收入及变动趋势", "financial_report", absence_meaningful=False),
    _item("net_profit", "净利润", "financial", True,
          "近三年净利润及变动趋势，关注增收不增利", "financial_report", absence_meaningful=False),
    _item("debt_ratio", "资产负债率", "financial", True,
          "近三年资产负债率，关注上升趋势", "financial_report", absence_meaningful=False),
    _item("cash_flow", "经营性现金流", "financial", False,
          "经营活动现金流净额，关注由正转负", "financial_report", absence_meaningful=False),

    # —— 司法与合规风险 ——
    _item("litigation", "涉诉记录", "judicial", True,
          "作为原告/被告的诉讼案件、案由、涉案金额、进展", "judicial"),
    _item("enforcement", "被执行记录", "judicial", True,
          "被执行案件、执行标的、执行状态。存在即为高风险信号", "judicial"),
    _item("dishonesty", "失信记录", "judicial", True,
          "失信被执行人（老赖）记录。存在即为一票否决级风险", "judicial"),
    _item("equity_freeze", "股权冻结", "judicial", False,
          "股权被司法冻结的情况", "judicial"),

    # —— 关联关系与对外担保 ——
    _item("guarantee", "对外担保", "relation", True,
          "作为担保人的对外担保、主债权金额、担保方式、内部决议程序", "business_registry"),
    # BC-18 的解除条件在 v0.7-C 达成：`service/guarantee_graph.py` 实现了
    # 从关联关系库推导互保与连环担保的能力，`graph_analysis` 适配器已登记。
    # **解除靠建能力，不靠调阈值**——这正是当初拒绝把它降为选查项的原因。
    _item("guarantee_circle", "担保圈", "relation", True,
          "是否涉入互保、连环担保。监管明确关注的系统性风险", "graph_analysis"),
    _item("related_party", "关联方交易", "relation", False,
          "关联方识别及关联交易占比、资金占用情况", "financial_report"),

    # —— 舆情扫描 ——
    _item("negative_news", "负面舆情", "opinion", True,
          "负面报道、纠纷、经营异常等公开负面信息", "public_opinion"),
    _item("regulatory_penalty", "监管处罚", "opinion", True,
          "行政处罚、监管措施及整改情况", "public_opinion"),
]


# ---------------------------------------------------------------- 场景扩展清单
#
# BC-58：固定二十项是**企业主体尽调的不可删除底座**，不是所有业务场景的全集。
#
# case_01（应收账款保理）的 29 条决策参考主张里，只有 2 条的字段名落在这二十项
# 内（都是 revenue）。剩下的——应收账款毛额/净额、存货、客户与供应商集中度、
# 资本开支、在建工程、海外收入、审计意见、内控缺陷、担保余额——系统 schema 里
# 根本没有这些字段。换模型不可能生成一个不存在的字段，确定性 Writer 也不会
# 绕过清单把它写成已核实结论：安全设计在正常工作，同时暴露了覆盖缺口。
#
# ## 为什么是代码定义而不是让模型自己加字段
#
# 每条打分规则都是 `risk_scorecard._ok(field_id)` 的硬编码查表，
# `PROFILE_BACKED_FIELDS` 上还写着"新增会读档案的规则时必须同步登记"。
# 模型临时发明的字段进不了打分、进不了完整度、进不了闸门、进不了额度——
# 它只能是报告里的装饰性文字。而 BC-58 缺的是**决策变量**，不是报告字数。
#
# 更根本的是：这 27 条不是"模型没识别出来"，是"我没写下来"。它们已知、
# 可枚举、有限。把一个已知需求外包给不可控来源，是拿架构风险换少写配置。
#
# ## 与核心清单的关系（严格分层）
#
#   核心二十项  → 进 verified_rate、进维度核实率、进闸门、进评级
#   场景扩展项  → 单独统计与呈现，**不进**上述任何一处
#
# 场景项若混进 `by_category`，会静默移动一套已经标定并测试过的闸门阈值。
# 所以扩展是**加法**：它增加系统能表达的决策变量，不改变既有判据。
SCENARIO_CHECKLISTS: Dict[str, List[ChecklistItem]] = {
    "factoring": [
        # —— 应收账款（保理的标的本身）——
        _item("accounts_receivable_gross", "应收账款账面余额", "financial", False,
              "应收账款原值/账面余额，是保理授信的标的规模", "financial_report",
              absence_meaningful=False),
        _item("accounts_receivable_net", "应收账款账面价值", "financial", False,
              "计提坏账准备后的应收账款净额", "financial_report",
              absence_meaningful=False),
        _item("accounts_receivable_aging", "应收账款账龄", "financial", False,
              "账龄分布（1年内/1-2年/2-3年/3年以上），逾期结构决定回款风险",
              "financial_report", absence_meaningful=False),
        _item("accounts_receivable_impairment", "应收账款减值", "financial", False,
              "坏账准备余额、计提比例与单项计提情况", "financial_report",
              absence_meaningful=False),
        # —— 集中度（保理最关心的对手方风险）——
        _item("top5_customer_share", "前五大客户集中度", "operation", False,
              "前五大/第一大客户销售额占营业收入比例", "financial_report",
              absence_meaningful=False),
        _item("top5_supplier_share", "前五大供应商集中度", "operation", False,
              "前五大供应商采购额占采购总额比例", "financial_report",
              absence_meaningful=False),
        # —— 经营与财务结构 ——
        _item("inventory", "存货", "financial", False,
              "存货账面价值与跌价准备，反映营运资金占用", "financial_report",
              absence_meaningful=False),
        _item("capex_cash_outflow", "资本开支", "financial", False,
              "购建固定资产、无形资产和其他长期资产支付的现金", "financial_report",
              absence_meaningful=False),
        _item("construction_in_progress", "在建工程", "financial", False,
              "在建工程规模，反映未来资本开支承诺", "financial_report",
              absence_meaningful=False),
        _item("overseas_revenue", "海外收入", "financial", False,
              "境外收入规模及占比，关联汇率与地缘风险", "financial_report",
              absence_meaningful=False),
        # —— 敞口 ——
        _item("guarantee_balance", "担保余额", "relation", False,
              "对外担保余额合计，与 guarantee 的逐笔记录互补", "financial_report"),
        # —— 审计与内控（决定财务数据本身可不可信）——
        _item("audit_opinion", "审计意见", "financial", False,
              "审计意见类型；非标意见是财务数据可信度的一票否决级信号",
              "financial_report", absence_meaningful=False),
        _item("internal_control_weakness", "内控缺陷", "financial", False,
              "财务报告内部控制重大缺陷披露情况", "financial_report"),
        # —— 交易适配性 ——
        _item("material_litigation", "重大诉讼仲裁", "judicial", False,
              "达到重大披露标准的诉讼仲裁事项，与 litigation 的逐笔记录互补",
              "financial_report"),
    ],
}

# 场景 → 扩展清单的确定性映射。由 `business_type` / `business_scenario` 决定，
# **不由模型决定字段集合**。命中不了就只用核心清单，不做模糊匹配。
_SCENARIO_ALIASES: Dict[str, str] = {
    "factoring": "factoring",
    "应收账款保理": "factoring",
    "保理": "factoring",
    "supply_chain_finance": "factoring",
    "供应链金融": "factoring",
}


def resolve_scenario(*hints: Optional[str]) -> str:
    """从业务类型/业务场景文本解析扩展清单名；解析不到返回空串。

    只做**精确别名**匹配，不做模糊包含：一个猜错的场景会给报告加上一批
    永远无法核实的字段，让"信息缺口"清单里凭空多出十几条噪声。
    """
    for hint in hints:
        key = str(hint or "").strip().lower()
        if key in _SCENARIO_ALIASES:
            return _SCENARIO_ALIASES[key]
    return ""


def scenario_checklist(scenario: str) -> List[ChecklistItem]:
    return list(SCENARIO_CHECKLISTS.get(scenario or "", ()))


def index_checklists(
    core: List[ChecklistItem],
    scenarios: Dict[str, List[ChecklistItem]],
) -> Dict[str, ChecklistItem]:
    """合并核心项与全部场景项，重名当场抛错。

    场景项也要能被证据链接纳（`rag_evidence_bridge` 用 `CHECKLIST_BY_ID` 判定
    字段是否合法），所以两者共用一张索引；但场景项不在 `CORE_IDS` 里，
    因此不会进 verified_rate / 维度核实率 / 闸门 / 评级。

    重名会让核心闸门读到场景数据——这属于必须在**定义期**失败的编程错误。
    单独成函数而不是写在模块体里，是为了让这条守卫本身可被测试：
    写在模块体里的 `raise` 只能靠 reload 触发，而 reload 会重新执行字面量，
    注入的冲突项当场被抹掉，守卫永远测不到。
    """
    index = {item.field_id: item for item in core}
    for name, items in scenarios.items():
        for item in items:
            if item.field_id in index:
                raise ValueError(
                    f"场景 {name!r} 的扩展项 {item.field_id!r} 与既有清单项重名——"
                    "重名会让核心闸门读到场景数据"
                )
            index[item.field_id] = item
    return index


# 核心清单的 id 集合。证据链、投影与评分只认这一批；
# 单独留一个常量，避免把"清单里有这个字段"和"这个字段进评级"混为一谈。
CORE_IDS = frozenset(item.field_id for item in CHECKLIST)

SCENARIO_IDS_BY_SCENARIO: Dict[str, frozenset] = {
    name: frozenset(item.field_id for item in items)
    for name, items in SCENARIO_CHECKLISTS.items()
}
ALL_SCENARIO_IDS = frozenset().union(*SCENARIO_IDS_BY_SCENARIO.values()) \
    if SCENARIO_IDS_BY_SCENARIO else frozenset()

CHECKLIST_BY_ID: Dict[str, ChecklistItem] = index_checklists(CHECKLIST, SCENARIO_CHECKLISTS)

REQUIRED_IDS = [i.field_id for i in CHECKLIST if i.required]
OPTIONAL_IDS = [i.field_id for i in CHECKLIST if not i.required]

# 系统尚不具备核查能力的项。评分卡据此施加**专用**闸门，
# 而不是让它伪装成「这次没查到」（BC-18）。
CAPABILITY_GAP_IDS = frozenset(
    i.field_id for i in CHECKLIST if i.data_source_status == "not_implemented"
)


def build_field_checks(
    exclude_ids: Optional[List[str]] = None,
    checked_at: str = "",
    scenario: str = "",
) -> List[Dict]:
    """
    生成核查清单骨架，全部初始为 unverified。

    Args:
        exclude_ids: 不适用于该主体的项（标记为 not_applicable 而非删除，
                     保留在清单中才能解释"为什么这项没查"）
        checked_at: 生成时间（ISO 字符串）
        scenario: 场景扩展清单名（见 SCENARIO_CHECKLISTS）。空串 = 只用核心项。

    每条 check 带 `scope`：`core` 或 `scenario:<名字>`。下游据此分层——
    核心项进核实率与闸门，场景项只单独统计（BC-58）。
    """
    exclude = set(exclude_ids or [])
    checks = []
    items = [(item, "core") for item in CHECKLIST]
    items += [(item, f"scenario:{scenario}") for item in scenario_checklist(scenario)]
    for item, scope in items:
        checks.append({
            "field_id": item.field_id,
            "field_name": item.field_name,
            "category": item.category,
            "section_id": item.section_id,
            "required": item.required,
            "scope": scope,
            "status": "not_applicable" if item.field_id in exclude else "unverified",
            "value": None,
            "sources": [],
            "attempted_sources": [],
            "failure_reason": "" if item.field_id in exclude else "尚未核查",
            "conflict_detail": [],
            "checked_at": checked_at,
        })
    return checks


def is_core_check(check: Dict) -> bool:
    """这条 check 是否属于核心二十项。

    判据用 `field_id in CORE_IDS`，而不是读 `scope` 字段——历史检查点里的
    check 没有 `scope`，靠字段缺省值判定会把老数据全算成场景项。
    身份要建在不可变的键上（BC-53 的同一条纪律）。
    """
    return str(check.get("field_id") or "") in CORE_IDS


def record_search_attempt(
    field_checks: List[Dict],
    section_id: str,
    source_tag: str,
    checked_at: str = "",
) -> int:
    """
    记录某章节发生过外部检索尝试（Scout 回写）。

    ⚠️ 刻意**不**翻转 status。理由：
    通用网页检索命中一篇新闻，不等于核实了"对外担保"这个字段。
    字段级核实需要定向抽取与结构化解析——那是 v0.4 数据源适配层的职责。

    此处只如实追加 attempted_sources，使清单反映"尝试过但未获字段级证据"，
    而不是让检索的存在制造"已核实"的假象。核实率因此不会被外部检索虚高。

    Returns: 被更新的条目数
    """
    n = 0
    for c in field_checks:
        if c.get("section_id") != section_id:
            continue
        if c.get("status") in ("verified", "not_applicable"):
            continue
        attempted = c.setdefault("attempted_sources", [])
        if source_tag not in attempted:
            attempted.append(source_tag)
            if checked_at:
                c["checked_at"] = checked_at
            n += 1
    return n


def compute_completeness(field_checks: List[Dict]) -> Dict:
    """
    统计核实情况。

    注意：not_applicable 不计入分母——该项对此主体本就无意义，
    不该拉低核实率，否则会误导对信息充分性的判断。

    ⚠️ 除 `scenario` 一块之外的**全部指标只统计核心二十项**（BC-58）。
    场景扩展项若混进 `by_category`，会静默移动一套已经标定并测试过的闸门
    阈值——加字段本该是加法，不该顺手改判据。
    """
    scenario_checks = [c for c in field_checks if not is_core_check(c)]
    field_checks = [c for c in field_checks if is_core_check(c)]
    required = [c for c in field_checks if c.get("required") and c.get("status") != "not_applicable"]
    verified = [c for c in required if c.get("status") == "verified"]
    unverified = [c for c in required if c.get("status") == "unverified"]
    conflicting = [c for c in field_checks if c.get("status") == "conflicting"]

    # 能力缺失项（BC-18）：系统根本查不了，重试永远不会成功。
    # 它们**仍计入 verified_rate 的分母**——15 项必查确实只核实了 14 项，
    # 这个数字必须诚实。但不计入维度核实率的分母：把"永远查不了"和
    # "这次没查到"混在一个比率里，会让同维度其它项被无辜拖累，
    # 且闸门理由永远指向错误的方向。
    capability_gaps = [
        c["field_id"] for c in field_checks
        if c.get("field_id") in CAPABILITY_GAP_IDS
        and c.get("status") not in ("verified", "not_applicable")
    ]

    by_category: Dict[str, Dict] = {}
    for c in field_checks:
        if c.get("status") == "not_applicable":
            continue
        cat = c.get("category", "?")
        stat = by_category.setdefault(cat, {"total": 0, "verified": 0, "capability_gaps": 0})
        if c.get("field_id") in capability_gaps:
            stat["capability_gaps"] += 1
            continue                      # 不进该维度的分子分母
        stat["total"] += 1
        if c.get("status") == "verified":
            stat["verified"] += 1
    for stat in by_category.values():
        stat["rate"] = round(stat["verified"] / stat["total"], 3) if stat["total"] else 0.0

    # 场景扩展覆盖率单独成块。评分器与报告分别呈现"核心主体清单覆盖率"和
    # "场景决策清单覆盖率"——两个数字回答的是不同问题：前者问主体查清了吗，
    # 后者问这笔业务的决策变量齐了吗。合成一个数会同时失去两个意义。
    scenario_applicable = [c for c in scenario_checks if c.get("status") != "not_applicable"]
    scenario_verified = [c for c in scenario_applicable if c.get("status") == "verified"]
    scenario_name = next(
        (str(c.get("scope") or "").split(":", 1)[1]
         for c in scenario_checks if str(c.get("scope") or "").startswith("scenario:")),
        "",
    )

    return {
        "required_total": len(required),
        "required_verified": len(verified),
        "verified_rate": round(len(verified) / len(required), 3) if required else 0.0,
        "unverified_fields": [c["field_id"] for c in unverified],
        "conflicting_fields": [c["field_id"] for c in conflicting],
        # 与 unverified_fields 有交集，但语义不同：前者是信息缺口（可补），
        # 这里是能力缺失（补不了，须线下核查）。消费方必须分开呈现。
        "capability_gaps": capability_gaps,
        "by_category": by_category,
        "scenario": {
            "name": scenario_name,
            "total": len(scenario_applicable),
            "verified": len(scenario_verified),
            "rate": round(len(scenario_verified) / len(scenario_applicable), 3)
                    if scenario_applicable else 0.0,
            "unverified_fields": [c["field_id"] for c in scenario_applicable
                                  if c.get("status") != "verified"],
        },
    }
