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


def _item(field_id, field_name, category, required, description, primary_source):
    return ChecklistItem(
        field_id=field_id,
        field_name=field_name,
        category=category,
        section_id=CATEGORY_TO_SECTION[category],
        required=required,
        description=description,
        primary_source=primary_source,
    )


# 完整清单：15 项必查 + 5 项选查
CHECKLIST: List[ChecklistItem] = [
    # —— 企业基本情况 ——
    _item("registration", "工商登记基本信息", "basic", True,
          "统一社会信用代码、注册资本、实缴资本、成立日期、法定代表人、注册地址、企业类型",
          "business_registry"),
    _item("business_scope", "经营范围", "basic", True,
          "登记经营范围，与实际业务是否一致", "business_registry"),
    _item("operating_status", "登记状态", "basic", True,
          "存续/在业/吊销/注销。非存续状态是一票否决级风险", "business_registry"),

    # —— 股权结构与实际控制人 ——
    _item("shareholders", "股东结构", "equity", True,
          "股东名称、类型、持股比例、认缴出资额", "business_registry"),
    _item("actual_controller", "实际控制人", "equity", True,
          "实际控制人认定及认定依据。不得由持股比例自行推断", "business_registry"),
    _item("external_investment", "对外投资", "equity", False,
          "作为股东对外投资的企业及持股情况", "business_registry"),

    # —— 经营状况 ——
    _item("bidding_record", "中标记录", "operation", False,
          "中标项目、金额、时间。是经营真实性的正面佐证", "bidding"),

    # —— 财务分析 ——
    _item("revenue", "营业收入", "financial", True,
          "近三年营业收入及变动趋势", "financial_report"),
    _item("net_profit", "净利润", "financial", True,
          "近三年净利润及变动趋势，关注增收不增利", "financial_report"),
    _item("debt_ratio", "资产负债率", "financial", True,
          "近三年资产负债率，关注上升趋势", "financial_report"),
    _item("cash_flow", "经营性现金流", "financial", False,
          "经营活动现金流净额，关注由正转负", "financial_report"),

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

    by_category: Dict[str, Dict] = {}
    for c in field_checks:
        if c.get("status") == "not_applicable":
            continue
        cat = c.get("category", "?")
        stat = by_category.setdefault(cat, {"total": 0, "verified": 0})
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
        "by_category": by_category,
    }
