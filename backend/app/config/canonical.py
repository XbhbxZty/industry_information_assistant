# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
跨层规范化的**唯一定义点**：金额口径与来源引用形式。

## 为什么需要这个模块（BC-59）

BC-59 的现象是评测器给出两个假阴性：引用覆盖率 0%（报告里明明有 `[S002]`）、
财务字段命中 3/27（银标千元、生产万元）。两处修完了，但**根因没修**——
同一条规则仍然有两份独立实现：

    金额口径   生产 `rag_evidence_bridge._to_wanyuan`  ×  评测 `Decimal(v)/10`
    来源引用   生产 `evidence_appendix` 渲染 `[Sxxx]`   ×  评测 `_source_is_cited`

这正是 BC-52 那条纪律的原文：**两处独立实现同一个规则 = 迟早漂移**。
BC-52 是写入侧与读取侧的集合名漂了，BC-59 是生产侧与评测侧的单位口径漂了。
修复的核心不是改那个数字，是让规则只有一处定义。

评测器的假阴性尤其危险：它会把**已经修好的生产能力重新判成失败**，
诱导团队去改正确的代码。所以口径必须由生产与评测共同 import，不各写一遍。

## 为什么放在 config/

`config/` 是本项目里"代码定义的常量与判据"层（`dd_checklist` 的二十项、
`verification_policy` 的策略都在这里），且不依赖 `service/` 的重量级导入链。
评测脚本已经在 import `config.dd_checklist`；放这里两边都能拿到，
不会把 llama_index 那条导入链拖进评分器。
"""
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union


# 全系统的金额口径。报告、评分卡、证据链、评测器一律以万元表达。
CANONICAL_MONEY_UNIT = "万元"

# 单位 → 换算到万元的因子。用 Decimal 字面量而非浮点：
# `362012554 * 0.1` 在浮点下不精确，而这些数字要参与**相等判断**
# （冲突检测、银标比对）。一次尾差就会把同一个值判成两个不同的值。
_TO_WANYUAN = {
    "元": Decimal("0.0001"),
    "千元": Decimal("0.1"),
    "万元": Decimal("1"),
    "百万元": Decimal("100"),
    "亿元": Decimal("10000"),
}

# 允许出现在原文表头里的单位写法 → 规范单位。
_UNIT_ALIASES = {
    "人民币元": "元", "人民币千元": "千元", "人民币万元": "万元", "人民币亿元": "亿元",
    "rmb元": "元", "元人民币": "元",
}


def normalize_unit(unit: Any) -> str:
    """把原文里的单位写法归一到规范单位；识别不了返回空串。"""
    text = "".join(str(unit or "").split()).lower()
    for char in "（）()：:，,。":
        text = text.replace(char, "")
    if text in _TO_WANYUAN:
        return text
    return _UNIT_ALIASES.get(text, "")


def is_money_unit(unit: Any) -> bool:
    return normalize_unit(unit) in _TO_WANYUAN


def to_wanyuan(value: Union[int, float, str, Decimal], unit: Any) -> Optional[Decimal]:
    """把金额换算到万元。

    Returns:
        Decimal 结果；单位不是金额单位或数值不合法时返回 None。

    ⚠️ 返回 Decimal 而不是 float：调用方要做相等判断时必须拿到精确值。
        需要落盘/序列化时用 `as_number()`。
    """
    normalized = normalize_unit(unit)
    if normalized not in _TO_WANYUAN:
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return amount * _TO_WANYUAN[normalized]


def as_number(value: Optional[Decimal]) -> Optional[Union[int, float]]:
    """Decimal → 可 JSON 序列化的数值；整数返回 int，否则四位小数。"""
    if value is None:
        return None
    quantized = value.normalize()
    if quantized == quantized.to_integral_value():
        return int(quantized)
    return float(round(value, 4))


def format_wanyuan(value: Optional[Decimal]) -> str:
    """万元金额的规范文本形式。报告与评测器必须用同一个函数产出/识别。"""
    number = as_number(value)
    if number is None:
        return ""
    text = f"{number:.4f}".rstrip("0").rstrip(".") if isinstance(number, float) else str(number)
    return f"{text}{CANONICAL_MONEY_UNIT}"


# ------------------------------------------------------------------ 来源引用
#
# 证据附录用方括号编号标注来源。评测器据此判定"这条结论标了来源"。
# 两侧必须共用同一个形式定义，否则封闭运行里 URL 被换成 local://、标题变成
# 上传文件名之后，评测器就认不出报告里那个真实存在的引用（BC-59）。

def format_source_citation(source_id: Any) -> str:
    """来源编号的规范引用形式，如 `S002` → `[S002]`。"""
    text = str(source_id or "").strip()
    return f"[{text}]" if text else ""


def citation_present(text: str, source_id: Any) -> bool:
    """报告里是否存在对该来源的规范引用。

    要求方括号形式，**不接受**裸 `source_id=S002` 这种调试文本——
    没有来源上下文的字符串不构成一条可审计的引用。
    """
    citation = format_source_citation(source_id)
    return bool(citation) and citation in str(text or "")
