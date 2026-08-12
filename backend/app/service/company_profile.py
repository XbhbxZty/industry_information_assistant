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
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from config.dd_checklist import CHECKLIST_BY_ID
    from config.verification_policy import POLICY
    from service.verification import (
        parse_iso, stamp_initial_profile_origin, verify_evidence_chain,
    )
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.config.dd_checklist import CHECKLIST_BY_ID
    from app.config.verification_policy import POLICY
    from app.service.verification import (
        parse_iso, stamp_initial_profile_origin, verify_evidence_chain,
    )

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
    # 数据源返回部分字段是常态（不同接口口径不同、部分指标未披露），
    # 因此逐项按存在与否拼接，缺哪项就不写哪项——不得因缺一个字段而整条链路崩溃，
    # 更不得用 0 或占位符填充造成虚假数据。
    for fin in company.get("financials", []):
        u = fin.get("unit", "万元")
        audited = "审计" in (fin.get("data_source", "") + fin.get("audit_status", ""))
        stype = "report" if audited else "self_reported"
        parts = []
        for label, key, is_ratio in (
            ("营业收入", "revenue", False),
            ("净利润", "net_profit", False),
            ("总资产", "total_assets", False),
            ("总负债", "total_liabilities", False),
            ("资产负债率", "debt_ratio", True),
            ("应收账款", "accounts_receivable", False),
            ("经营性现金流净额", "operating_cash_flow", False),
        ):
            if fin.get(key) is None:
                continue
            parts.append(f"{label} {fin[key]:.1%}" if is_ratio else f"{label} {fin[key]}{u}")
        if not parts:
            continue
        audit_note = fin.get("audit_status") or ("已审计" if audited else "未注明审计状态")
        facts.append(_fact(
            f"{name}{fin.get('period', '（期间未载明）')}财务数据：" + "，".join(parts)
            + f"。（{audit_note}）",
            fin.get("data_source", "财务报表"), stype, "financial"))

    # —— 司法 ——
    for jr in company.get("judicial_records", []):
        role = f"，身份为{jr['role']}" if jr.get("role") else ""
        facts.append(_fact(
            f"{name}{jr.get('type', '司法')}记录：案号 {jr.get('case_no', '（未载明）')}{role}，"
            f"案由/事由 {jr.get('cause', '（未提供）')}，"
            f"涉案金额 {jr.get('amount', '（未载明）')}{jr.get('unit', '万元')}，"
            f"立案日期 {jr.get('filing_date', '（未载明）')}，"
            f"当前状态：{jr.get('status', '（未载明）')}。",
            "司法公开信息", "official", "judicial"))

    # —— 中标 ——
    for br in company.get("bidding_records", []):
        facts.append(_fact(
            f"{name}中标记录：{br.get('project', '（项目未载明）')}，"
            f"中标金额 {br.get('amount', '（未载明）')}{br.get('unit', '万元')}，"
            f"中标日期 {br.get('win_date', '（未载明）')}。",
            "招投标公开信息", "official", "operation"))

    # —— 舆情 ——
    for nn in company.get("negative_news", []):
        facts.append(_fact(
            f"负面舆情（{nn['severity']}）：{nn['title']}（{nn['publish_date']}，来源：{nn['source']}）。"
            f"{nn.get('summary','')}",
            nn.get("source", "公开报道"), "news", "opinion"))

    logger.info(f"[company_profile] {name} 生成 {len(facts)} 条事实")
    return facts


# field_id → 档案中承载该项数据的键。用于取**证据自身声明的获取时间**。
# 不在表内的项（如 guarantee_circle 需图谱推导）没有档案载体，取不到时间。
_FIELD_PROFILE_KEY: Dict[str, str] = {
    "registration": "registration", "business_scope": "registration",
    "operating_status": "registration",
    "shareholders": "shareholders", "actual_controller": "actual_controller",
    "external_investment": "external_investment",
    "bidding_record": "bidding_records",
    "revenue": "financials", "net_profit": "financials",
    "debt_ratio": "financials", "cash_flow": "financials",
    "litigation": "judicial_records", "enforcement": "judicial_records",
    "dishonesty": "judicial_records", "equity_freeze": "judicial_records",
    "guarantee": "guarantee", "related_party": "related_party",
    "negative_news": "negative_news", "regulatory_penalty": "regulatory_penalty",
}


def _latest_ts(node: Any) -> Optional[str]:
    """从档案节点（dict 或 list）里取最新的 retrieved_at。"""
    if isinstance(node, dict):
        node = [node]
    if not isinstance(node, list):
        return None
    stamps = [x.get("retrieved_at") for x in node
              if isinstance(x, dict) and parse_iso(x.get("retrieved_at"))]
    return max(stamps) if stamps else None


def profile_retrieved_at(company: Dict[str, Any]) -> Dict[str, str]:
    """
    解析档案中每个核查项的**取证时间**。

    ⚠️ 绝不能退化成 `datetime.now()`。那记录的是程序读取档案的时刻，
       把它当成证据获取时间，就是拿运行时间冒充取证时间——报告会显示
       "本次核查于今日完成"，而数据可能是三个月前抓的（BC-35）。

    取不到时留空：由重放校验记为降级并触发来源闸门，不伪造。
    """
    # 数据源级兜底：coverage/顶层声明的整批抓取时间
    fallback = (
        (company.get("coverage") or {}).get("retrieved_at")
        or company.get("retrieved_at")
        or company.get("as_of")
    )
    if parse_iso(fallback) is None:
        fallback = None

    # 必须遍历**整张清单**，不能只遍历有档案载体的字段：
    # guarantee_circle 这类靠推导得出、没有对应档案键的项会整个缺席，
    # 于是拿不到快照时间而被误记为"取证时间不明"。与 BC-21 同形——
    # 从"已有数据"出发遍历，最缺数据的那一项反而漏掉。
    out: Dict[str, str] = {}
    for fid in CHECKLIST_BY_ID:
        key = _FIELD_PROFILE_KEY.get(fid)
        # 事件型字段查到空列表时没有记录可取时间，退到数据源级声明
        ts = _latest_ts(company.get(key)) if key else None
        out[fid] = ts or fallback or ""

    # 冲突项：以各来源声明的取证时间为准
    for fid, entries in (company.get("multi_source") or {}).items():
        if isinstance(entries, list):
            ts = _latest_ts(entries)
            if ts:
                out[fid] = ts
    return out


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

    ac = company.get("actual_controller") or {}
    if ac.get("name"):
        # 认定依据必须一并给出——尽调中"谁是实控人"和"凭什么这么认定"同等重要
        resolved["actual_controller"] = (
            f"{ac['name']}（认定依据：{ac.get('basis', '未说明')}）", "equity")

    if company.get("external_investment"):
        resolved["external_investment"] = (
            "；".join(f"{e['name']} 持股{e['ratio']:.2%}" for e in company["external_investment"]),
            "equity")

    if company.get("guarantee"):
        resolved["guarantee"] = (
            "；".join(
                f"为{g['beneficiary']}提供{g['guarantee_type']}{g['amount']}{g.get('unit', '万元')}"
                f"（{g.get('period', '期限未载明')}，{g.get('board_resolution', '内部决议情况未载明')}）"
                for g in company["guarantee"]
            ),
            "relation")

    if company.get("related_party"):
        resolved["related_party"] = (
            "；".join(f"{r['name']}（{r['relation']}）" for r in company["related_party"]),
            "relation")

    penalties = [p for p in (company.get("regulatory_penalty") or [])
                 if p.get("subject_confirmed") is True]
    if penalties:
        resolved["regulatory_penalty"] = (
            "；".join(
                f"{p['title']}（{p.get('authority', '处罚机关未载明')}，{p['publish_date']}"
                + (f"，罚款{p['amount']}{p.get('unit', '万元')}" if p.get("amount") else "") + "）"
                for p in penalties
            ),
            "opinion")

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
    # 与上面的 _fin_series 保持一致：拼不出内容就不要写进 resolved，
    # 否则空串会被判为 verified（值为空）——曾出现过此缺陷
    _dr = "；".join(f"{f['period']} {f['debt_ratio']:.1%}" for f in fins if "debt_ratio" in f)
    if _dr:
        resolved["debt_ratio"] = (_dr, "financial")

    for fid, kind in (("litigation", "涉诉"), ("enforcement", "被执行"),
                      ("dishonesty", "失信"), ("equity_freeze", "股权冻结")):
        v = _judicial(kind)
        if v:
            resolved[fid] = (v, "judicial")

    # 舆情：必须区分「已确认属于本主体」与「疑似相关但主体未确认」。
    # 公开报道常以"某地一企业"指代而不点名，把这类报道直接归属给尽调对象，
    # 与 BC-10（同名主体混淆）是同一类错误——只是方向相反。
    news = company.get("negative_news") or []
    confirmed = [n for n in news if n.get("subject_confirmed") is True]
    unconfirmed = [n for n in news if n.get("subject_confirmed") is not True]
    if confirmed:
        resolved["negative_news"] = (
            "；".join(f"{n['title']}（{n['publish_date']}，{n['severity']}）" for n in confirmed),
            "opinion")
    # unconfirmed 的线索不进 resolved：它不能作为"已核实的本主体舆情"，
    # 但会在下方写入 pending_attribution，让报告披露"检索到疑似相关但主体未确认"
    pending_attribution = {
        "negative_news": [
            f"{n['title']}（{n['publish_date']}）" for n in unconfirmed
        ]
    } if unconfirmed else {}

    # 数据源覆盖范围：区分「查了但无记录」与「未查询」。
    # 这两者在尽调中的业务含义完全不同——前者是可支持授信的正面结论，
    # 后者是必须补查的信息缺口。混为一谈会直接误导审批。
    coverage = company.get("coverage") or {}
    queried = set(coverage.get("queried") or [])
    not_queried_reason = coverage.get("not_queried_reason") or {}

    # 多源冲突：同一核查项存在多个数据源且取值不一致。
    # 尽调中这类矛盾（如工商登记实缴 5000万 vs 财报附注实缴 1500万）是核心风险线索，
    # 绝不能单方面采信其一——那等于替审批人做了没有依据的判断。
    multi_source = company.get("multi_source") or {}
    conflicts: Dict[str, List[Dict[str, Any]]] = {}
    for fid, entries in multi_source.items():
        if fid.startswith("_") or not isinstance(entries, list):
            continue
        distinct = {e.get("value") for e in entries if e.get("value") is not None}
        if len(distinct) > 1:
            conflicts[fid] = [
                {"source": e.get("source", "未知来源"),
                 "value": e.get("value"),
                 "retrieved_at": e.get("retrieved_at", "")}
                for e in entries
            ]

    # 逐项落状态
    for chk in field_checks:
        if chk["status"] == "not_applicable":
            continue
        fid = chk["field_id"]
        item = CHECKLIST_BY_ID.get(fid)
        source_tag = item.primary_source if item else "company_profile"
        chk["attempted_sources"] = [source_tag] if fid in queried else []
        chk["checked_at"] = now

        if fid in conflicts:
            # 冲突优先于一切：即便某个来源给出了完整取值，也不得据此判为已核实
            chk["status"] = "conflicting"
            chk["value"] = None
            chk["conflict_detail"] = conflicts[fid]
            chk["attempted_sources"] = [c["source"] for c in conflicts[fid]]
            chk["failure_reason"] = (
                "多个数据源取值不一致，需人工核实后方可采信："
                + "；".join(f"{c['source']}={c['value']}" for c in conflicts[fid])
            )
        elif fid in resolved:
            # 查到了具体内容
            value, cat = resolved[fid]
            chk["status"] = "verified"
            chk["value"] = value
            chk["sources"] = facts_by_cat.get(cat, [])
            chk["failure_reason"] = ""

        elif fid in queried:
            # 查询已执行但无内容。「无记录」是否构成有效结论，取决于字段类型。
            if item and not item.absence_meaningful:
                # 属性型字段（工商登记/股东/营收…）：存续企业必然具备。
                # 查询返回空不是"没有"，而是主体存疑或数据源异常，
                # 绝不能粉饰成"经查询无相关记录"。
                chk["status"] = "unverified"
                chk["value"] = None
                chk["failure_reason"] = (
                    f"数据源 {source_tag} 已查询但未返回该项内容。"
                    f"此为必备属性，缺失属异常信号（主体可能不存在/已注销，或数据源故障），"
                    f"须人工核实主体真实性"
                )
            else:
                # 事件型字段（涉诉/失信/担保/舆情…）：可以合法地不存在
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

        # 主体归属未确认的线索：不能算已核实，但必须在报告中披露
        if fid in pending_attribution and pending_attribution[fid]:
            leads = "；".join(pending_attribution[fid])
            if chk["status"] == "verified" and chk["value"] == "经查询，无相关记录":
                chk["status"] = "unverified"
                chk["value"] = None
            chk["failure_reason"] = (
                (chk["failure_reason"] + "；" if chk["failure_reason"] else "")
                + f"另检索到疑似相关线索但主体归属未确认，需人工核对：{leads}"
            )

    # 打上来源标记：本函数产出的每一条 verified/conflicting 都来自初始档案。
    # 必须在这里标，而不是交给调用方——漏标一次，该清单在重放校验里
    # 就会被当成"来源不明的旧检查点"，走降级路径。
    #
    # 时间戳取档案自身声明的取证时间（见 profile_retrieved_at），不是 now。
    undated = stamp_initial_profile_origin(field_checks, profile_retrieved_at(company))
    if undated:
        logger.warning(
            f"[company_profile] {company.get('name')} 有 {len(undated)} 项未声明取证时间，"
            f"将记为证据链降级：{undated}"
        )

    return field_checks


def verified_profile_mismatches(
    company: Dict[str, Any],
    field_checks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    校验「清单已核实」能否由当前结构化档案重新推出。

    评分卡同时消费 company_profile 与 field_checks。两者若来自不同时间的
    检查点，可能出现清单仍是 verified、档案字段已经缺失的情况；此时空列表
    会被评分器误读成「已查询且无记录」。这里复用 fill_field_checks 作为唯一
    字段映射口径，重放一次填充并比较状态和值，避免另写第二套路径规则。
    """
    report = verify_evidence_chain(
        company, field_checks, evidence_store=None,
        profile_replay_fn=replay_from_profile,
    )
    return report.mismatches


def replay_from_profile(
    company: Dict[str, Any],
    field_checks: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    用当前结构化档案重放一遍清单填充，返回 {field_id: 预测出的 check}。

    复用 `fill_field_checks` 作为**唯一**字段映射口径——另写一套路径规则
    必然会与填充逻辑漂移，届时"不一致"到底是数据问题还是两套规则不同步
    就无从分辨。
    """
    expected = deepcopy(field_checks)
    for check in expected:
        if check.get("status") == "not_applicable":
            continue
        check["status"] = "unverified"
        check["value"] = None
        check["sources"] = []
        check["attempted_sources"] = []
        check["failure_reason"] = "尚未核查"
        check["conflict_detail"] = []

    fill_field_checks(company, profile_to_facts(company), expected)
    return {c.get("field_id"): c for c in expected}


def verify_field_checks(
    company: Dict[str, Any],
    field_checks: List[Dict[str, Any]],
    evidence_store: Optional[Dict[str, Dict]] = None,
    *,
    allow_legacy_profile_replay: Optional[bool] = None,
):
    """
    完整的证据链校验入口（v0.6）。

    与 `verified_profile_mismatches()` 的区别：后者只返回 mismatches，
    丢掉了 degradations——而旧检查点的降级必须被调用方看到并披露，
    不能只在日志里一闪而过。新代码一律用本函数。

    `allow_legacy_profile_replay` 不传时取 `config.verification_policy.POLICY`，
    而不是就地写一个默认值：这是授信口径开关，必须在统一配置里可见（BC-33）。
    """
    legacy = (POLICY.allow_legacy_profile_replay
              if allow_legacy_profile_replay is None else allow_legacy_profile_replay)
    return verify_evidence_chain(
        company, field_checks, evidence_store,
        profile_replay_fn=replay_from_profile,
        allow_legacy_profile_replay=legacy,
    )


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
