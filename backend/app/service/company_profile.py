"""
企业档案加载（v0.1 最小闭环）

从硬编码 JSON 读取企业档案，转换为 ResearchState 可消费的 facts 记录。

⚠️ 这是 v0.1 的临时实现，将在 v0.4 被 service/datasource/ 适配层取代。
   当前刻意保持简单：不查库、不做适配、不处理并发。

设计约束（重要）：
    只输出档案中**确实存在**的字段。档案里没有的信息（如实际控制人、
    对外担保）绝不在此处补齐或推断——观察下游 Agent 如何处理这些缺口，
    正是 v0.1 的实验目的。
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from config.dd_checklist import CHECKLIST_BY_ID
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.config.dd_checklist import CHECKLIST_BY_ID

logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "companies.json"

# 来源类型 → 可信度。尽调场景的证据等级：官方登记 > 审计 > 企业自报 > 媒体
_CREDIBILITY = {
    "official": 0.95,   # 工商登记、司法记录
    "report": 0.85,     # 审计报告
    "self_reported": 0.60,  # 企业自报未审计
    "news": 0.50,       # 媒体舆情
}


def _load_raw() -> Dict[str, Any]:
    try:
        with open(_DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"[company_profile] 档案文件不存在: {_DATA_FILE}")
        return {"companies": []}
    except json.JSONDecodeError as e:
        logger.error(f"[company_profile] 档案 JSON 解析失败: {e}")
        return {"companies": []}


def list_companies() -> List[Dict[str, Any]]:
    return _load_raw().get("companies", [])


def find_company(query: str) -> Optional[Dict[str, Any]]:
    """
    从查询文本中识别企业。

    v0.1 用朴素子串匹配即可：先试全称，再试去掉行政区划/后缀的简称。
    """
    if not query:
        return None

    companies = list_companies()
    for c in companies:
        if c["name"] in query:
            logger.info(f"[company_profile] 全称命中: {c['name']}")
            return c

    # 简称匹配：剥离常见前后缀后取核心字号
    for c in companies:
        core = c["name"]
        for prefix in ("东莞市", "深圳市", "广州市", "上海市", "北京市"):
            core = core.replace(prefix, "")
        for suffix in ("有限公司", "股份有限公司", "有限责任公司", "科技", "集团"):
            core = core.replace(suffix, "")
        if core and len(core) >= 2 and core in query:
            logger.info(f"[company_profile] 简称命中: {core} -> {c['name']}")
            return c

    logger.info(f"[company_profile] 未匹配到企业: {query[:40]}")
    return None


# 语义分类 → 尽调提纲固定 8 章的 section id
# 注意 sec_6（关联关系与对外担保）刻意没有任何来源映射：档案未提供对外担保数据，
# 该章将拿到 0 条事实。观察 Writer 是写"未核实"还是自行编造，是 v0.1 的核心实验。
_SECTION_MAP = {
    "basic": "sec_1",      # 企业基本情况
    "equity": "sec_2",     # 股权结构与实际控制人
    "operation": "sec_3",  # 经营状况
    "financial": "sec_4",  # 财务分析
    "judicial": "sec_5",   # 司法与合规风险
    "opinion": "sec_7",    # 舆情扫描
}


def _fact(content: str, source_name: str, source_type: str, category: str) -> Dict[str, Any]:
    return {
        "id": f"fact_{uuid.uuid4().hex[:8]}",
        "content": content,
        "source_url": "",                       # 内部数据源无 URL
        "source_name": source_name,
        "source_type": source_type,
        "credibility_score": _CREDIBILITY.get(source_type, 0.5),
        "related_sections": [_SECTION_MAP.get(category, "sec_1")],
        "verified": True,                       # 来自结构化数据源，非模型生成
        "metadata": {"origin": "company_profile", "category": category},
    }


def profile_to_facts(company: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把企业档案摊平成 facts 列表。只输出档案中实际存在的字段。"""
    facts: List[Dict[str, Any]] = []
    name = company["name"]

    # —— 工商登记 ——
    reg = company.get("registration")
    if reg:
        src = reg.get("data_source", "工商登记信息")
        facts.append(_fact(
            f"{name}，统一社会信用代码 {company.get('credit_code','（未提供）')}，"
            f"{reg.get('company_type','')}，成立于 {reg.get('established_date','')}，"
            f"注册资本 {reg.get('registered_capital','')}，实缴资本 {reg.get('paid_in_capital','')}，"
            f"法定代表人 {reg.get('legal_representative','')}，登记状态：{reg.get('operating_status','')}。"
            f"注册地址：{reg.get('registered_address','')}。",
            src, "official", "basic"))

        if reg.get("business_scope"):
            facts.append(_fact(f"{name}经营范围：{reg['business_scope']}", src, "official", "basic"))

        si = reg.get("social_insurance_count")
        if si:
            trend = "、".join(f"{y}年 {n} 人" for y, n in sorted(si.items()))
            facts.append(_fact(f"{name}参保人数变化：{trend}。", src, "official", "operation"))

    # —— 股东结构 ——
    for sh in company.get("shareholders", []):
        facts.append(_fact(
            f"{name}股东 {sh['name']}（{sh['type']}）持股 {sh['ratio']:.2%}，"
            f"认缴出资 {sh.get('subscribed_capital','（未提供）')}。",
            "工商登记信息", "official", "equity"))

    # —— 财务 ——
    for fin in company.get("financials", []):
        u = fin.get("unit", "万元")
        stype = "report" if "审计" in fin.get("data_source", "") else "self_reported"
        facts.append(_fact(
            f"{name}{fin['period']}财务数据：营业收入 {fin['revenue']}{u}，"
            f"净利润 {fin['net_profit']}{u}，总资产 {fin['total_assets']}{u}，"
            f"总负债 {fin['total_liabilities']}{u}，资产负债率 {fin['debt_ratio']:.1%}，"
            f"应收账款 {fin['accounts_receivable']}{u}，"
            f"经营性现金流净额 {fin['operating_cash_flow']}{u}。",
            fin.get("data_source", "财务报表"), stype, "financial"))

    # —— 司法 ——
    for jr in company.get("judicial_records", []):
        facts.append(_fact(
            f"{name}{jr['type']}记录：案号 {jr['case_no']}，身份为{jr['role']}，"
            f"案由/事由 {jr.get('cause','（未提供）')}，涉案金额 {jr['amount']}{jr.get('unit','万元')}，"
            f"立案日期 {jr['filing_date']}，当前状态：{jr['status']}。",
            "司法公开信息", "official", "judicial"))

    # —— 中标 ——
    for br in company.get("bidding_records", []):
        facts.append(_fact(
            f"{name}中标记录：{br['project']}，中标金额 {br['amount']}{br.get('unit','万元')}，"
            f"中标日期 {br['win_date']}。",
            "招投标公开信息", "official", "operation"))

    # —— 舆情 ——
    for nn in company.get("negative_news", []):
        facts.append(_fact(
            f"负面舆情（{nn['severity']}）：{nn['title']}（{nn['publish_date']}，来源：{nn['source']}）。"
            f"{nn.get('summary','')}",
            nn.get("source", "公开报道"), "news", "opinion"))

    logger.info(f"[company_profile] {name} 生成 {len(facts)} 条事实")
    return facts


def fill_field_checks(
    company: Dict[str, Any],
    facts: List[Dict[str, Any]],
    field_checks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    用档案内容填充核查清单状态（v0.2 核心）。

    档案里有的项标 verified 并挂上对应 fact_id；没有的项保持 unverified
    并写明 failure_reason 与尝试过的数据源。

    ⛔ 只依据档案**实际存在**的字段判定，绝不推断。
       例如档案给了股东名单但没有实际控制人认定，
       actual_controller 必须保持 unverified——这正是要观测的关键行为。

    注：清单填充归属数据源层而非 Scout。纯内部数据源模式下 Scout 不做检索，
    由此处填充；v0.4 换成适配层后，各适配器各自填充自己负责的项。
    """
    now = datetime.now().isoformat()
    # 建立 category -> fact_id 索引，便于把清单项挂到取证记录上
    facts_by_cat: Dict[str, List[str]] = {}
    for f in facts:
        cat = (f.get("metadata") or {}).get("category")
        if cat:
            facts_by_cat.setdefault(cat, []).append(f["id"])

    # 档案中各字段的取值路径与摘要方式
    reg = company.get("registration") or {}
    fins = company.get("financials") or []
    jrs = company.get("judicial_records") or []

    def _fin_series(key: str, unit: str = "万元") -> Optional[str]:
        if not fins:
            return None
        parts = [f"{f['period']} {f[key]}{unit}" for f in fins if key in f]
        return "；".join(parts) if parts else None

    def _judicial(kind: str) -> Optional[str]:
        hits = [r for r in jrs if r.get("type") == kind]
        if not hits:
            return None
        return "；".join(
            f"{r['case_no']}（{r.get('cause') or kind}，{r['amount']}{r.get('unit','万元')}，{r['status']}）"
            for r in hits
        )

    # field_id -> (取值, 归属 category)
    resolved: Dict[str, tuple] = {}

    if reg:
        resolved["registration"] = (
            f"信用代码 {company.get('credit_code')}；注册资本 {reg.get('registered_capital')}；"
            f"实缴 {reg.get('paid_in_capital')}；成立 {reg.get('established_date')}；"
            f"法定代表人 {reg.get('legal_representative')}；类型 {reg.get('company_type')}",
            "basic")
        if reg.get("business_scope"):
            resolved["business_scope"] = (reg["business_scope"], "basic")
        if reg.get("operating_status"):
            resolved["operating_status"] = (reg["operating_status"], "basic")

    if company.get("shareholders"):
        resolved["shareholders"] = (
            "；".join(f"{s['name']}（{s['type']}）{s['ratio']:.2%}" for s in company["shareholders"]),
            "equity")

    if company.get("bidding_records"):
        resolved["bidding_record"] = (
            "；".join(f"{b['project']} {b['amount']}{b.get('unit','万元')}（{b['win_date']}）"
                     for b in company["bidding_records"]),
            "operation")

    for fid, key in (("revenue", "revenue"), ("net_profit", "net_profit"),
                     ("cash_flow", "operating_cash_flow")):
        v = _fin_series(key)
        if v:
            resolved[fid] = (v, "financial")
    if fins:
        resolved["debt_ratio"] = (
            "；".join(f"{f['period']} {f['debt_ratio']:.1%}" for f in fins if "debt_ratio" in f),
            "financial")

    for fid, kind in (("litigation", "涉诉"), ("enforcement", "被执行"),
                      ("dishonesty", "失信"), ("equity_freeze", "股权冻结")):
        v = _judicial(kind)
        if v:
            resolved[fid] = (v, "judicial")

    if company.get("negative_news"):
        resolved["negative_news"] = (
            "；".join(f"{n['title']}（{n['publish_date']}，{n['severity']}）"
                     for n in company["negative_news"]),
            "opinion")

    # 数据源覆盖范围：区分「查了但无记录」与「未查询」。
    # 这两者在尽调中的业务含义完全不同——前者是可支持授信的正面结论，
    # 后者是必须补查的信息缺口。混为一谈会直接误导审批。
    coverage = company.get("coverage") or {}
    queried = set(coverage.get("queried") or [])
    not_queried_reason = coverage.get("not_queried_reason") or {}

    # 逐项落状态
    for chk in field_checks:
        if chk["status"] == "not_applicable":
            continue
        fid = chk["field_id"]
        item = CHECKLIST_BY_ID.get(fid)
        source_tag = item.primary_source if item else "company_profile"
        chk["attempted_sources"] = [source_tag] if fid in queried else []
        chk["checked_at"] = now

        if fid in resolved:
            # 查到了具体内容
            value, cat = resolved[fid]
            chk["status"] = "verified"
            chk["value"] = value
            chk["sources"] = facts_by_cat.get(cat, [])
            chk["failure_reason"] = ""
        elif fid in queried:
            # 查询已执行但无记录 —— 这是已核实的正面结论，不是缺口
            chk["status"] = "verified"
            chk["value"] = "经查询，无相关记录"
            chk["sources"] = []
            chk["failure_reason"] = ""
        else:
            # 该数据源根本不覆盖此项 —— 真正的信息缺口
            chk["status"] = "unverified"
            chk["value"] = None
            chk["failure_reason"] = not_queried_reason.get(
                fid, f"数据源 {source_tag} 未覆盖该项"
            )

    return field_checks


def build_credit_context(company: Dict[str, Any]) -> str:
    """授信申请背景，用于拼进 Architect 的规划提示词。"""
    app = company.get("credit_application")
    if not app:
        return f"授信申请信息未提供。尽调对象：{company['name']}。"
    return (
        f"尽调对象：{company['name']}\n"
        f"授信产品：{app['product']}\n"
        f"申请金额：{app['amount']}{app.get('unit','万元')}\n"
        f"期限：{app.get('term','（未提供）')}\n"
        f"资金用途：{app.get('purpose','（未提供）')}"
    )
