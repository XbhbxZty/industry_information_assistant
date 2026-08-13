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


CHECKLIST_BY_ID: Dict[str, ChecklistItem] = {i.field_id: i for i in CHECKLIST}

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
) -> List[Dict]:
    """
    生成核查清单骨架，全部初始为 unverified。

    Args:
        exclude_ids: 不适用于该主体的项（标记为 not_applicable 而非删除，
                     保留在清单中才能解释"为什么这项没查"）
        checked_at: 生成时间（ISO 字符串）
    """
    exclude = set(exclude_ids or [])
    checks = []
    for item in CHECKLIST:
        checks.append({
            "field_id": item.field_id,
            "field_name": item.field_name,
            "category": item.category,
            "section_id": item.section_id,
            "required": item.required,
            "status": "not_applicable" if item.field_id in exclude else "unverified",
            "value": None,
            "sources": [],
            "attempted_sources": [],
            "failure_reason": "" if item.field_id in exclude else "尚未核查",
            "conflict_detail": [],
            "checked_at": checked_at,
        })
    return checks


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
    """
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
    }
