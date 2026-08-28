# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""企业档案的键名与取值词表校验（BC-80）

## 这个模块要挡住的具体缺陷

档案里写 `guarantee_records`，而评分层读的是 `guarantee`。取不到 →
空列表 → 输出「**未发现对外担保**」，relation 维度 0.0 分。

一家年报自己勾了「违规对外担保：适用」的企业，系统给出了一个**肯定的
否定结论**——不是报错，不是标未核实，是「查过了，没有」。

同理 `judicial_records` 里 `type: "破产重整"` 不在 `_score_judicial`
认识的三种类型（失信/被执行/涉诉）里，两条重整记录一个字都没进评分，
也没留任何痕迹。

**三条路径通向同一个错误输出：一句「未发现」。**

`risk_scorecard` 第 267 行的注释早就写过这件事：

> 一旦 guarantee_circle 变成 verified，无论查到什么都会记成
> "未发现担保圈"，与 BC-31 同形：**核实状态被当成了结论本身**。

那个洞为 `guarantee_circle` 修了，但「键名对不上」与「类型不认识」
两处还开着。

## 为什么是 fail-closed 而不是告警

档案带 `coverage.queried` 声明「这些项我查过了」。一旦键名对不上，
「档案里没有这个键」就会被读成「查过且无记录」——**声明的覆盖面
把一个读取失败洗成了一个阴性结论**。

在授信场景里，「没查到担保」和「有 5.4 亿违规担保但我没读懂」
之间的差别是决定性的。所以未知键**直接拒绝加载该条档案**，
而不是记一条日志了事——日志没人看，错误的授信建议有人签字。

下划线开头的键是注释性元数据（`_basis`、`_source_case`），不参与校验。
"""
from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


#: 档案的顶层键。**新增字段必须同时登记在这里**——否则它会被静默忽略，
#: 而消费方读不到时会输出「未发现」。
KNOWN_PROFILE_KEYS = {
    "company_id", "name", "credit_code", "coverage",
    "registration", "shareholders", "actual_controller",
    "external_investment", "financials",
    "judicial_records", "guarantee", "guarantee_circle",
    "bidding_records", "negative_news", "regulatory_penalty",
    "related_party", "credit_application",
}

#: `judicial_records[].type` 的词表。
#:
#: ⚠️ 不在词表里的类型会被 `_score_judicial` 的三个过滤器全部漏掉，
#: 既不计分也不留痕。实测「破产重整」就是这么消失的——
#: 而一家破产重整中的企业，judicial 维度只拿到 10 分。
KNOWN_JUDICIAL_TYPES = {"涉诉", "被执行", "失信", "股权冻结", "行政处罚"}


#: 各类记录**必需的子字段**。
#:
#: ⚠️ 顶层键对了不代表记录能读。`company_profile.py:384` 写的是
#: `g['beneficiary']`——裸下标，缺字段直接 KeyError 冒到顶层，
#: 整轮研究挂掉，错误信息只有一个 `'beneficiary'`。
#:
#: 实测：档案里把被担保方写成 `counterparty`，两轮跑到一半崩，
#: 而日志里看不出是哪个字段、哪条记录、哪份档案。
#:
#: 这与顶层键那一条是同一形状的两个粒度——**校验只做到顶层，
#: 就只能挡住顶层那一类**。
REQUIRED_RECORD_FIELDS = {
    "guarantee": ("beneficiary", "guarantee_type", "amount"),
    "judicial_records": ("type", "case_no"),
    "shareholders": ("name", "ratio"),
    "bidding_records": ("project", "amount"),
    "negative_news": ("title", "publish_date"),
}


class ProfileSchemaError(ValueError):
    """档案不符合已知词表。

    单独立类型，是为了让调用方能把它与「档案文件读不到」区分开——
    前者是数据写错了，后者是环境问题，处置完全不同。
    """


def _suggest(unknown: str, known: set) -> str:
    hit = difflib.get_close_matches(unknown, sorted(known), n=1, cutoff=0.6)
    return f"，最接近的已知键是 `{hit[0]}`" if hit else ""


def validate_profile(company: Dict[str, Any]) -> List[str]:
    """校验一条档案。返回问题列表，空列表表示通过。

    **不抛异常**，让调用方决定是拒绝加载还是记录——
    评测脚本可能需要先看看有哪些问题再决定怎么改。
    """
    problems: List[str] = []
    name = str(company.get("name") or "<未命名>")

    for key in company:
        if key.startswith("_"):
            continue                      # 注释性元数据
        if key not in KNOWN_PROFILE_KEYS:
            problems.append(
                f"[{name}] 未知的顶层键 `{key}`{_suggest(key, KNOWN_PROFILE_KEYS)}。"
                f"消费方读不到它，会把该项输出成「未发现」——"
                f"在授信场景里那是一个肯定的否定结论。")

    for i, rec in enumerate(company.get("judicial_records") or []):
        kind = str(rec.get("type") or "")
        if kind not in KNOWN_JUDICIAL_TYPES:
            problems.append(
                f"[{name}] judicial_records[{i}] 的 type=`{kind}` 不在词表内"
                f"{_suggest(kind, KNOWN_JUDICIAL_TYPES)}。"
                f"评分层的过滤器会整条漏掉它，既不计分也不留痕。")

    for key, required in REQUIRED_RECORD_FIELDS.items():
        for i, rec in enumerate(company.get(key) or []):
            missing = [f for f in required if f not in rec]
            if missing:
                near = {f: _suggest(f, set(rec)) for f in missing}
                problems.append(
                    f"[{name}] {key}[{i}] 缺必需子字段 {missing}"
                    + "".join(v for v in near.values() if v)
                    + "。消费方是裸下标读取，缺字段会让整轮研究以 KeyError 崩溃，"
                      "而日志里只看得到字段名。")

    coverage = (company.get("coverage") or {}).get("queried") or []
    for fid in coverage:
        # 声明查过、却连对应的键都没有，本身不是错——事件型字段
        # 「查了没有」就是空的。这里只挡住**键名写错**那一类，
        # 上面的未知键检查已经覆盖；此处留空是有意的。
        pass

    return problems


def validate_all(companies: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for company in companies:
        out.extend(validate_profile(company))
    return out
