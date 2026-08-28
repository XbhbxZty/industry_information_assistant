# Copyright © 2026 XbhbxZty
"""这份语料理论上最多能核实哪些清单字段（离线分析，不调用任何模型）。

## 为什么需要它

case_01 的参考主张命中率停在 22.2%，而三轮实验证明它既不是抽取能力问题
（换模型无增益），也不是检索广度问题（检索量翻 2.7 倍、过闸证据零增长）。
新仪表给出的拒绝归因指向第三种可能：**清单里有一批字段，在这类语料上
根本没有数据源**——中标、失信、被执行、股权冻结的来源是工商登记与司法
公开网，而本案例的语料是 8 份年报类文档（案例包里 S009/S010 两条正是
403 失败的那两个官方源）。

模型没有源可查就去别的表里找形状相似的值填，闸门逐条挡住。这时候
"覆盖率低"必须归因到**数据源缺失**，而不是模型能力——否则就会去调
一个本来正确的闸门（BC-18 的纪律：能力缺失不能伪装成"这次没查到"）。

## 这个分析测的是什么，不测什么

判据是**必要条件**，不是充分条件：

* 字段的确定性关键词在语料里出现过吗（0 次 = 确定无源）
* 完整度门槛要求的要素在语料里出现过吗（如工商登记要 6 选 5、
  股东结构要"股东名称"+名册措辞、财务要三个期间）

关键词出现**不等于**该字段能核实——还需要取值、主体、期间同时成立。
所以结论是一个**上限**：低于它的部分才谈得上抽取能力问题。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))

from config.dd_checklist import ALL_SCENARIO_IDS, CHECKLIST_BY_ID, CORE_IDS  # noqa: E402
from service.rag_evidence_bridge import _FIELD_TERMS, _FINANCIAL_KEYS, _verbatim_compact  # noqa: E402

CORPUS = BACKEND / "eval" / "real_cases_processed"

# 完整度门槛额外要求的措辞（与 `_field_is_complete_enough` 对齐）。
# 这里重复列出是刻意的：分析要能独立回答"门槛本身可不可达"，
# 而不是调用那个只接受已通过候选的函数。
GATE_TERMS = {
    "registration": ("统一社会信用代码", "注册资本", "成立", "法定代表人", "注册地址", "企业类型"),
    "shareholders": ("股东名称", "前十名股东", "前10名股东", "全部股东", "股东总数"),
}


def _chunks(case_id: str) -> list[str]:
    path = CORPUS / case_id / "corpus" / "chunks.jsonl"
    return [json.loads(line)["content"]
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# 否定式披露的措辞。年报里「经核查，XXX 不属于失信被执行人」「报告期内未发生
# 因环境问题受到行政处罚」这类句子**含有关键词，但不是证据**——它是发行人
# 声明"没有"，而 `field_evidence` 只收原文明示的正面事实。
#
# 不把它们剔掉，这张上限表就会把"年报里写了一句没有"算成"有数据源"，
# 进而把一个无源字段的覆盖缺口误报成模型抽取能力不足——正是本分析要防的
# 那种错误归因。
_ABSENCE_MARKERS = ("不属于", "未发生", "未达到", "不适用", "未受到", "不存在",
                    "未出现", "无重大", "未披露", "没有发生")

# 关键词在语料中出现的**上下文半径**：判断这次命中是正面记录还是否定式声明。
_CONTEXT_RADIUS = 40


def _hits(texts: list[str], term: str) -> int:
    needle = _verbatim_compact(term)
    return sum(1 for text in texts if needle in _verbatim_compact(text))


def _positive_hits(texts: list[str], term: str) -> int:
    """命中里有几次**不是**否定式披露。

    子串匹配无法回答"这次命中是不是我要的意思"——`operating_status` 的
    「存续」在本语料里 12 次全部来自「整个存续期预期信用损失」，是金融工具
    减值的会计政策，与工商登记状态毫无关系。这个函数只能滤掉否定式声明，
    **滤不掉这种同形异义**，所以结论仍是上限而非结论。
    """
    positive = 0
    for text in texts:
        for match in re.finditer(re.escape(term), text):
            window = text[max(0, match.start() - _CONTEXT_RADIUS):
                          match.end() + _CONTEXT_RADIUS]
            if not any(marker in window for marker in _ABSENCE_MARKERS):
                positive += 1
                break
    return positive


def _period_labels(texts: list[str]) -> set[str]:
    """语料里出现过的年度标签——财务字段要求近三期。"""
    labels: set[str] = set()
    for text in texts:
        labels.update(re.findall(r"(20\d{2})\s*年", text))
    return labels


def analyse(case_id: str) -> dict:
    texts = _chunks(case_id)
    periods = _period_labels(texts)
    rows = []
    for field_id in list(CORE_IDS) + list(ALL_SCENARIO_IDS):
        terms = _FIELD_TERMS.get(field_id, ())
        term_hits = {term: _hits(texts, term) for term in terms}
        positive_hits = {term: _positive_hits(texts, term) for term in terms}
        best = max(term_hits.values()) if term_hits else 0
        best_positive = max(positive_hits.values()) if positive_hits else 0

        blockers = []
        if best == 0:
            blockers.append("确定性关键词在语料中出现 0 次")
        elif best_positive == 0:
            blockers.append(
                f"{best} 次命中全部落在否定式披露里（「未发生」「不属于」等），"
                f"不是可引用的正面事实"
            )
        if field_id in GATE_TERMS:
            gate_hits = {term: _hits(texts, term) for term in GATE_TERMS[field_id]}
            if field_id == "registration":
                covered = sum(1 for count in gate_hits.values() if count)
                if covered < 5:
                    blockers.append(f"工商要素仅 {covered}/6 出现，完整度门槛要求 5")
            if field_id == "shareholders":
                if not gate_hits.get("股东名称"):
                    blockers.append("门槛要求的「股东名称」未出现")
                if not any(gate_hits.get(t) for t in
                           ("前十名股东", "前10名股东", "全部股东", "股东总数")):
                    blockers.append("门槛要求的名册措辞未出现")
        # ⚠️ 刻意不按"年度标签数"判财务字段的三期门槛。实测这个正则在本语料上
        # 抓出 2002–2040 共 33 个标签（债券到期、折旧年限、质保期），
        # 恒定通过、零区分度。一个永远为真的判据不是判据，写出来只会让
        # 这张表看起来比实际更有依据——留着它比没有更糟（BC-60 的教训）。
        if field_id in _FINANCIAL_KEYS:
            pass
        if field_id in {"external_investment", "related_party"}:
            blockers.append("完整度判据对列表型字段硬返回 False（需全量表格解析）")

        rows.append({
            "field_id": field_id,
            "field_name": CHECKLIST_BY_ID[field_id].field_name,
            "scope": "core" if field_id in CORE_IDS else "scenario",
            "best_term_hits": best,
            "best_positive_hits": best_positive,
            "top_terms": dict(sorted(term_hits.items(), key=lambda kv: -kv[1])[:3]),
            "blockers": blockers,
            "reachable": not blockers,
        })
    return {"case_id": case_id, "chunks": len(texts),
            "year_labels": sorted(periods), "fields": rows}


def main() -> int:
    case_id = sys.argv[1] if len(sys.argv) > 1 else "case_01"
    report = analyse(case_id)
    out = CORPUS / case_id / "corpus_ceiling.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rows = report["fields"]
    by_scope = Counter(row["scope"] for row in rows)
    ok = Counter(row["scope"] for row in rows if row["reachable"])
    print(f"{case_id}: {report['chunks']} 片段，年度标签 {report['year_labels']}\n")
    for scope in ("core", "scenario"):
        print(f"=== {scope} 可达 {ok[scope]}/{by_scope[scope]} ===")
        for row in rows:
            if row["scope"] != scope:
                continue
            mark = "可达" if row["reachable"] else "不可达"
            print(f"  [{mark}] {row['field_id']:26} 命中 {row['best_term_hits']:>4}"
                  f"（正面 {row['best_positive_hits']:>4}）")
            for blocker in row["blockers"]:
                print(f"           ↳ {blocker}")
        print()
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
