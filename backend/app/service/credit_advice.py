"""
授信额度建议（纯函数，无 LLM）—— v0.7-A

## 为什么要做

`credit_advice` 此前是每个等级一句固定话术：「可考虑授信，建议追加增信措施」。
信贷评审会拿到这句话什么也决定不了——他们要的是**建议多少钱、附什么条件**。
迁移计划 Stage 3.4 写的是"综合评分 → 等级，**附建议授信额度区间**"。

## 三条设计原则

### 1. 只用已核实的财务数据

额度建立在营收、净资产、现金流上。若这些字段本身是 `unverified`，
就**不得**拿它们算额度——那等于用未经核实的数字决定放多少钱。
这是整套核实架构在授信环节的落点。

### 2. 多口径取最小值

营收法、净资产法、现金流法各算一个，取**最小**。
取最大或取平均都会让某一个口径的乐观值主导结论；
在授信场景里保守方向是唯一安全的方向。

### 3. 算不出就不出具，而不是给一个保守的数字

与 `unratable()` 同一原则：**算不出 ≠ 可以给个小额度**。
给出一个有数字的建议，读者就会当它是经过测算的结论。
数据不足时必须明确写"不具备测算条件"。

## 对外担保为什么要扣减

对外担保是**或有负债**：被担保方违约时担保人要代偿。
这在真实风控里从授信能力中扣减，也让 v0.7-B/C 查到的担保与担保圈
真正影响一笔钱的决定，而不只是影响一个等级标签。
"""
from typing import Any, Dict, List, Optional

try:
    from service.risk_scorecard import INSUFFICIENT
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.risk_scorecard import INSUFFICIENT

# —— 各口径系数。取值参考公开的供应链金融/小微信贷业务资料 ——
REVENUE_RATIO = 0.20        # 年营收的 20%
NET_ASSET_MULTIPLE = 0.80   # 净资产的 0.8 倍
CASH_FLOW_MULTIPLE = 3.0    # 年经营性现金流净额的 3 倍

# 风险等级系数。低风险不放大——1.0 是上限，不是起点。
LEVEL_FACTOR = {"低风险": 1.0, "中风险": 0.70, "高风险": 0.40}

# 建议区间的上下浮动
RANGE_SPREAD = 0.15

# 核实率折扣：低于此值时按比例打折（总体闸门已在更低处拦截）
FULL_CONFIDENCE_RATE = 0.90


def _verified(field_checks: List[Dict], fid: str) -> bool:
    for c in field_checks or []:
        if c.get("field_id") == fid:
            return c.get("status") == "verified"
    return False


def _latest(company: Dict[str, Any]) -> Dict[str, Any]:
    fins = company.get("financials") or []
    return fins[-1] if fins else {}


def _bases(company: Dict[str, Any], field_checks: List[Dict]) -> List[Dict[str, Any]]:
    """
    各口径测算。**只使用已核实的字段**——未核实的数字不得参与额度计算。
    """
    latest = _latest(company)
    out: List[Dict[str, Any]] = []

    if _verified(field_checks, "revenue") and latest.get("revenue"):
        v = float(latest["revenue"]) * REVENUE_RATIO
        out.append({"method": "营收法", "value": v,
                    "detail": f"{latest.get('period', '最近一期')}营业收入 "
                              f"{latest['revenue']:.0f} 万元 × {REVENUE_RATIO:.0%}"})

    ta, tl = latest.get("total_assets"), latest.get("total_liabilities")
    if _verified(field_checks, "debt_ratio") and ta is not None and tl is not None:
        equity = float(ta) - float(tl)
        if equity > 0:
            out.append({"method": "净资产法", "value": equity * NET_ASSET_MULTIPLE,
                        "detail": f"净资产 {equity:.0f} 万元 × {NET_ASSET_MULTIPLE}"})

    if _verified(field_checks, "cash_flow") and latest.get("operating_cash_flow") is not None:
        ocf = float(latest["operating_cash_flow"])
        if ocf > 0:
            out.append({"method": "现金流法", "value": ocf * CASH_FLOW_MULTIPLE,
                        "detail": f"经营性现金流净额 {ocf:.0f} 万元 × {CASH_FLOW_MULTIPLE}"})
        else:
            # 现金流为负不是"这个口径算不出"，而是一个明确的负面信号，必须留痕
            out.append({"method": "现金流法", "value": 0.0,
                        "detail": f"经营性现金流净额 {ocf:.0f} 万元，为负，"
                                  f"本口径不支持任何授信额度"})
    return out


def _conditions(company: Dict[str, Any], field_checks: List[Dict],
                assessment: Dict[str, Any]) -> List[str]:
    """
    增信与放款条件。逐条对应一个已查实的风险点，不写泛泛而谈的套话。
    """
    conds: List[str] = []
    latest = _latest(company)

    guarantees = company.get("guarantee") or []
    if guarantees:
        amt = sum(float(g.get("amount") or 0) for g in guarantees)
        no_resolution = [g for g in guarantees
                         if "未" in str(g.get("board_resolution", ""))]
        conds.append(f"对外担保 {len(guarantees)} 笔合计 {amt:.0f} 万元属或有负债，"
                     f"须持续监控被担保方履约情况")
        if no_resolution:
            conds.append(f"其中 {len(no_resolution)} 笔未见股东会决议，"
                         f"须补充内部决议文件后方可放款")

    circles = company.get("guarantee_circle") or []
    if circles:
        conds.append("已查实涉入担保圈，须要求企业限期解除互保关系，"
                     "或按环上敞口全额计提风险准备")

    if (latest.get("operating_cash_flow") or 0) < 0:
        conds.append("经营性现金流为负，建议采用受托支付并对回款账户实施监管")

    dr = latest.get("debt_ratio")
    if dr is not None and float(dr) > 0.70:
        conds.append(f"资产负债率 {float(dr):.1%} 偏高，建议追加抵押物或第三方连带责任担保")

    comp = assessment.get("completeness") or {}
    if comp.get("unverified_fields"):
        conds.append(f"以下必查项尚未核实，须补齐后复评："
                     f"{'、'.join(comp['unverified_fields'][:5])}")
    if comp.get("conflicting_fields"):
        conds.append(f"存在多源冲突项（{'、'.join(comp['conflicting_fields'])}），"
                     f"须人工核实确认后方可放款")

    if assessment.get("requires_human_review"):
        conds.append("本笔评级要求人工复核，未经复核不得放款")
    return conds


def recommend_credit(
    company: Dict[str, Any],
    field_checks: List[Dict],
    assessment: Dict[str, Any],
) -> Dict[str, Any]:
    """
    产出可解释的授信额度建议。

    `recommendable=False` 时**不给任何数字**——给出一个有数字的建议，
    读者就会当它是经过测算的结论（与 `unratable()` 同一原则）。
    """
    level = assessment.get("level")
    app = company.get("credit_application") or {}
    applied = float(app["amount"]) if app.get("amount") is not None else None

    def _no(reason: str, basis: Optional[List[Dict[str, Any]]] = None,
            deductions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        # ⚠️ 已经算出来的测算口径**必须保留**。
        #
        # 初版在不出具时把 basis 清空，报告于是只说"不出具额度"而不给测算过程——
        # 复核人看不到工作底稿，无法判断这个结论是算过的还是拍的。
        # 「不出具」本身也是一个需要依据的结论。
        return {
            "recommendable": False, "reason": reason,
            "suggested_amount": None, "range_low": None, "range_high": None,
            "basis": list(basis or []), "deductions": list(deductions or []),
            "adjustments": [],
            "application_amount": applied, "application_gap": None,
            "conditions": _conditions(company, field_checks, assessment),
            "advice_text": reason,
        }

    bases = _bases(company, field_checks)

    if level == INSUFFICIENT:
        return _no("信息不足，不具备测算条件；补齐必查项后重新评估", bases)
    if level == "拒绝":
        return _no("风险等级为拒绝，不出具授信额度建议", bases)

    if not bases:
        return _no("缺少已核实的财务数据，不具备额度测算条件——"
                   "未核实的数字不得用于决定放款金额")

    # 多口径取最小：保守方向是授信场景唯一安全的方向
    base_value = min(b["value"] for b in bases)
    chosen = min(bases, key=lambda b: b["value"])
    adjustments: List[Dict[str, Any]] = [{
        "factor": "口径选取", "multiplier": 1.0,
        "detail": f"{len(bases)} 个口径取最小值，采用{chosen['method']}"}]

    amount = base_value

    factor = LEVEL_FACTOR.get(level)
    if factor is None:
        return _no(f"未知风险等级 {level!r}，不出具额度建议")
    amount *= factor
    adjustments.append({"factor": f"风险等级（{level}）", "multiplier": factor,
                        "detail": f"按 {level} 系数 {factor} 调整"})

    comp = assessment.get("completeness") or {}
    rate = comp.get("verified_rate")
    if isinstance(rate, (int, float)) and rate < FULL_CONFIDENCE_RATE:
        disc = round(float(rate) / FULL_CONFIDENCE_RATE, 3)
        amount *= disc
        adjustments.append({"factor": "核实率折扣", "multiplier": disc,
                            "detail": f"必查项核实率 {rate:.0%} 低于 "
                                      f"{FULL_CONFIDENCE_RATE:.0%}，按比例折减"})

    # 对外担保是或有负债，从授信能力中扣减
    deductions: List[Dict[str, Any]] = []
    guarantees = company.get("guarantee") or []
    if guarantees:
        amt = sum(float(g.get("amount") or 0) for g in guarantees)
        deductions.append({"item": "对外担保（或有负债）", "amount": amt,
                           "detail": f"{len(guarantees)} 笔合计 {amt:.0f} 万元，"
                                     f"被担保方违约时须代偿，全额扣减"})
        amount -= amt

    if amount <= 0:
        # ⚠️ 必须点名**真正的**约束项。
        #
        # 初版这里统一写"扣减或有负债后授信能力为零"——但实测多数情况下
        # 额度在扣减之前就已归零（现金流法为负会把取最小值的结果直接压到 0），
        # 担保扣减根本不是成因。**解释与真实成因不符**，正是这个项目
        # 一直在防的东西，只不过这次出现在我自己写的说明里。
        if base_value <= 0:
            binding = f"{chosen['method']}测算为零（{chosen['detail']}）"
        elif deductions:
            binding = (f"{chosen['method']}测算 {base_value:.0f} 万元，"
                       f"经风险调整后不足以覆盖对外担保或有负债 "
                       f"{deductions[0]['amount']:.0f} 万元")
        else:
            binding = f"{chosen['method']}测算经风险调整后归零"
        return _no(f"按已核实数据测算，授信能力为零或负数，不建议给予授信额度。"
                   f"约束项：{binding}", bases, deductions)

    amount = round(amount, 0)
    low = round(amount * (1 - RANGE_SPREAD), 0)
    high = round(amount * (1 + RANGE_SPREAD), 0)
    gap = round(applied - amount, 0) if applied is not None else None

    text = f"建议授信额度 {low:.0f}–{high:.0f} 万元（测算中值 {amount:.0f} 万元）"
    if applied is not None:
        text += (f"；申请金额 {applied:.0f} 万元，"
                 + ("**超出建议上限，建议降额**" if applied > high else "在建议区间内"))

    return {
        "recommendable": True, "reason": "",
        "suggested_amount": amount, "range_low": low, "range_high": high,
        "basis": bases, "deductions": deductions, "adjustments": adjustments,
        "application_amount": applied, "application_gap": gap,
        "conditions": _conditions(company, field_checks, assessment),
        "advice_text": text,
    }


def render_markdown(rec: Optional[Dict[str, Any]]) -> str:
    """渲染为报告可嵌入的 Markdown。与评级块一样由代码生成，不经模型改写。"""
    if not rec:
        return ""
    lines = ["**授信额度建议（规则测算，非模型判断）**", ""]
    if not rec.get("recommendable"):
        lines.append(f"- **不出具额度建议**：{rec.get('reason')}")
        if rec.get("basis"):
            lines += ["", "| 测算口径 | 金额（万元） | 依据 |", "|---|---|---|"]
            for b in rec["basis"]:
                lines.append(f"| {b['method']} | {b['value']:.0f} | {b['detail']} |")
    else:
        lines += [
            f"- **建议区间**：{rec['range_low']:.0f}–{rec['range_high']:.0f} 万元"
            f"（测算中值 {rec['suggested_amount']:.0f} 万元）",
        ]
        if rec.get("application_amount") is not None:
            gap = rec.get("application_gap") or 0
            lines.append(
                f"- **申请金额**：{rec['application_amount']:.0f} 万元"
                + (f"，超出测算中值 {gap:.0f} 万元，建议降额"
                   if gap > 0 else "，未超出测算中值"))
        lines += ["", "| 测算口径 | 金额（万元） | 依据 |", "|---|---|---|"]
        for b in rec["basis"]:
            lines.append(f"| {b['method']} | {b['value']:.0f} | {b['detail']} |")
        for a in rec["adjustments"]:
            lines.append(f"| {a['factor']} | ×{a['multiplier']} | {a['detail']} |")
        for d in rec["deductions"]:
            lines.append(f"| {d['item']} | −{d['amount']:.0f} | {d['detail']} |")

    conds = rec.get("conditions") or []
    if conds:
        lines += ["", "**放款条件与增信要求**"]
        lines += [f"- {c}" for c in conds]
    return "\n".join(lines)
