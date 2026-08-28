"""Deterministically screen one sealed real-case Agent run against its silver label.

This is deliberately a conservative first-pass scorer.  It checks experiment
isolation, cutoff safety, decision alignment, exact/converted reference-value
coverage and source citation coverage.  It does not pretend that string matching
can replace a human or model judge for semantic contradictions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


EVAL = Path(__file__).resolve().parent
PROCESSED = EVAL / "real_cases_processed"

sys.path.insert(0, os.fspath(EVAL.parent / "app"))
from config.canonical import (  # noqa: E402
    citation_present, format_wanyuan, is_money_unit, to_wanyuan,
)
from config.dd_checklist import (  # noqa: E402
    ALL_SCENARIO_IDS, CHECKLIST_BY_ID, CORE_IDS,
)


# 银标字段名 → 系统字段 id。
#
# 这张表本身就是 BC-58 的度量：**映射不到，说明系统 schema 表达不了这条决策
# 主张**，此时"模型覆盖率低"是一个错误的归因——模型不可能产出一个不存在的
# 字段。所以评分器必须把"未映射"单独报出来，而不是混进覆盖率的分母。
#
# 命名差异是真实存在的，不是笔误：银标按会计科目全称写
# （`attributable_net_profit`），系统按清单 id 写（`net_profit`）。
_CLAIM_FIELD_TO_CHECKLIST: dict[str, str] = {
    # —— 核心二十项 ——
    "legal_name": "registration",
    "unified_social_credit_code": "registration",
    "a_share_code": "registration",
    "h_share_code": "registration",
    "revenue": "revenue",
    "attributable_net_profit": "net_profit",
    "operating_cash_flow": "cash_flow",
    "debt_asset_ratio": "debt_ratio",
    "public_execution_status": "enforcement",
    "actual_daily_related_transactions": "related_party",
    "other_litigation_amount": "litigation",
    # —— factoring 场景扩展 ——
    "accounts_receivable_gross": "accounts_receivable_gross",
    "accounts_receivable_net": "accounts_receivable_net",
    "inventory": "inventory",
    "top5_customer_share": "top5_customer_share",
    "top5_supplier_purchase_share": "top5_supplier_share",
    "cash_paid_for_fixed_intangible_longterm_assets": "capex_cash_outflow",
    "construction_in_progress": "construction_in_progress",
    "overseas_revenue": "overseas_revenue",
    "guarantee_balance": "guarantee_balance",
    "audit_opinion_2024": "audit_opinion",
    "financial_reporting_material_weakness": "internal_control_weakness",
    "material_litigation_arbitration": "material_litigation",
    "annual_report_keyword_factoring": "material_litigation",
}

# 防漂移：映射目标必须都是真实存在的清单字段。少了一个就当场炸掉，
# 而不是在跑分时静默把它算成"未映射"——那会把系统能力误报成覆盖缺口。
_UNKNOWN_TARGETS = sorted(set(_CLAIM_FIELD_TO_CHECKLIST.values()) - set(CHECKLIST_BY_ID))
if _UNKNOWN_TARGETS:
    raise RuntimeError(
        f"评分器映射到了不存在的清单字段：{_UNKNOWN_TARGETS}。"
        f"清单改名后必须同步这张表，否则覆盖率会凭空下降"
    )


def _claim_scope(claim: dict[str, Any]) -> str:
    """这条主张落在核心清单、场景扩展，还是系统根本表达不了。"""
    target = _CLAIM_FIELD_TO_CHECKLIST.get(str(claim.get("field_name") or ""))
    if target is None:
        return "unmapped"
    if target in CORE_IDS:
        return "core"
    return "scenario" if target in ALL_SCENARIO_IDS else "unmapped"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _compact(value: Any) -> str:
    return re.sub(r"[\s,，。；;：:（）()\[\]【】`*_\-]", "", str(value)).lower()


def _number_variants(value: float, unit: str) -> Iterable[str]:
    """Renderings of a silver-label value that count as the same number.

    The canonical production form comes from ``config.canonical`` -- the same
    module the production evidence adapter and report writer use.  It must not
    be re-derived here: a second implementation of one rule drifts (BC-52), and
    when the evaluator's copy drifts the failure mode is a false negative that
    blames working production code (BC-59).
    """
    if value.is_integer():
        yield str(int(value))
        yield f"{int(value):,}"
    else:
        yield str(value)
    if is_money_unit(unit):
        # 生产口径：证据链与确定性 Writer 一律输出万元。
        canonical = to_wanyuan(value, unit)
        if canonical is not None:
            yield format_wanyuan(canonical)
        # 上市公司报告常把千元的银标值折成亿元表述。接受换算后的数字，
        # 但不接受一个裸的四舍五入整数。
        yi = to_wanyuan(value, unit)
        if yi is not None:
            yi_value = yi / Decimal("10000")
            yield f"{yi_value:.2f}亿"
            yield f"{yi_value:.1f}亿"
    if "%" in unit:
        yield f"{value:g}%"


def _claim_variants(claim: dict[str, Any]) -> set[str]:
    values: list[Any] = [claim.get("value"), claim.get("value_raw")]
    variants: set[str] = set()
    for value in values:
        if value is None or str(value).lower() in {"null", "none", "—", "-"}:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            candidates = _number_variants(float(value), str(claim.get("unit") or ""))
        else:
            candidates = [str(value)]
        variants.update(_compact(item) for item in candidates if len(_compact(item)) >= 3)
    return variants


def _search_audit(events: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    result_rows: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "search_results":
            continue
        content = event.get("content") or {}
        # Scout also emits a presentation-only list after fact extraction.  Those
        # rows intentionally point at the original public document URL and are not
        # network retrieval events.  Audit only the raw local-search payloads.
        if content.get("searchType") != "local":
            continue
        result_rows.extend(content.get("results") or [])
    expected_prefix = f"local://kb/{case_id}/"
    bad_urls = [row.get("url") for row in result_rows if not str(row.get("url") or "").startswith(expected_prefix)]
    wrong_case_tags = [
        row.get("url") for row in result_rows
        if f"[case_id={case_id};" not in str(row.get("snippet") or "")
    ]
    return {
        "retrieved_result_count": len(result_rows),
        "all_result_urls_case_local": bool(result_rows) and not bad_urls,
        "all_snippets_case_tagged": bool(result_rows) and not wrong_case_tags,
        "bad_result_urls": bad_urls,
        "wrong_case_tag_count": len(wrong_case_tags),
    }


def _post_cutoff_dates(report: str, cutoff: str) -> list[str]:
    try:
        cutoff_date = date.fromisoformat(cutoff)
    except ValueError:
        return []
    candidates = set(re.findall(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", report))
    candidates.update(
        f"{year}-{month}-{day}"
        for year, month, day in re.findall(
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日", report
        )
    )
    post_cutoff = []
    for raw in candidates:
        try:
            parsed = date.fromisoformat("-".join(f"{int(part):02d}" if index else part
                                                  for index, part in enumerate(raw.split("-"))))
        except ValueError:
            continue
        if parsed > cutoff_date:
            post_cutoff.append(parsed.isoformat())
    return sorted(set(post_cutoff))


def _source_is_cited(report: str, compact_report: str, source: dict[str, Any]) -> bool:
    """Recognize the stable source ID emitted by the sealed evidence appendix.

    Sealed runs intentionally replace public URLs with ``local://`` retrieval
    URLs and may expose an uploaded filename instead of the catalog title.  The
    appendix still prints the whitelisted source identity as ``[S002]``.  The
    old scorer ignored that auditable citation and therefore reported 0% even
    when a verified evidence row named its source explicitly.

    The bracketed form is defined once in ``config.canonical`` and shared with
    the appendix renderer -- the evaluator must not carry its own idea of what a
    citation looks like, or the two drift and the drift reads as a production
    failure (BC-59).  A bare ``source_id=S002`` debug string still does not
    count: without source context it is not an auditable citation.
    """
    if citation_present(report, source.get("source_id")):
        return True
    title = str(source.get("title") or "")
    url = str(source.get("url") or source.get("source_url") or "")
    return bool(
        (title and _compact(title) in compact_report)
        or (url and url in report)
    )


def score_run(run_dir: Path) -> dict[str, Any]:
    result = _json(run_dir / "result.json")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    events = _jsonl(run_dir / "events.jsonl")
    case_id = str(result["case_id"])
    reference_dir = PROCESSED / case_id / "reference"
    assessment = _json(reference_dir / "assessment.json")
    claims = _jsonl(reference_dir / "claims.jsonl")
    sources = _jsonl(PROCESSED / case_id / "sources.jsonl")

    reasoning_ids = set(assessment.get("reasoning_claim_ids") or [])
    scoreable = [
        claim for claim in claims
        if claim.get("claim_id") in reasoning_ids
        and claim.get("as_of_eligible") is True
        and claim.get("status") == "verified"
        and _claim_variants(claim)
    ]
    compact_report = _compact(report)
    hit_ids = [
        claim["claim_id"] for claim in scoreable
        if any(variant in compact_report for variant in _claim_variants(claim))
    ]
    miss_ids = [claim["claim_id"] for claim in scoreable if claim["claim_id"] not in hit_ids]
    claim_coverage = len(hit_ids) / len(scoreable) if scoreable else 0.0

    # 双覆盖率（BC-58）。一个合成数字会同时失去两个意义：
    #   核心覆盖率   —— 主体查清了吗
    #   场景覆盖率   —— 这笔业务的决策变量齐了吗
    #   未映射数     —— 系统 schema 根本表达不了的主张数；这个数不为 0 时，
    #                  谈"模型覆盖率低"是错误归因
    by_scope: dict[str, dict[str, Any]] = {}
    hit_set = set(hit_ids)
    for claim in scoreable:
        bucket = by_scope.setdefault(_claim_scope(claim),
                                     {"total": 0, "matched": 0, "claim_ids": []})
        bucket["total"] += 1
        bucket["claim_ids"].append(claim["claim_id"])
        if claim["claim_id"] in hit_set:
            bucket["matched"] += 1
    for bucket in by_scope.values():
        bucket["coverage"] = round(bucket["matched"] / bucket["total"], 4) if bucket["total"] else 0.0
    unmapped_claim_ids = by_scope.get("unmapped", {}).get("claim_ids", [])

    source_by_id = {row["source_id"]: row for row in sources}
    expected_source_ids = {
        source_id
        for claim in scoreable
        for source_id in claim.get("source_ids") or []
        if source_id in source_by_id
    }
    cited_source_ids = []
    for source_id in sorted(expected_source_ids):
        source = source_by_id[source_id]
        if _source_is_cited(report, compact_report, source):
            cited_source_ids.append(source_id)
    citation_coverage = len(cited_source_ids) / len(expected_source_ids) if expected_source_ids else 0.0

    search = _search_audit(events, case_id)
    isolated = (
        result.get("search_web") is False
        and result.get("search_local") is True
        and len(result.get("kb_scope") or []) == 1
        and (result.get("kb_scope") or [{}])[0].get("kb_id") == case_id
        and search["all_result_urls_case_local"]
        and search["all_snippets_case_tagged"]
    )
    report_post_cutoff_dates = _post_cutoff_dates(
        report, str(result.get("research_cutoff") or "")
    )
    cutoff_safe = (
        result.get("reference_layer_read") is False
        and result.get("post_cutoff_layer_read") is False
        and str(result.get("research_cutoff") or "") == str((events[0] if events else {}).get("as_of") or "")
        and not report_post_cutoff_dates
    )

    final_event = result.get("final_event") or {}
    # 章节抽取失败的运行不可计分（BC-56）。崩掉的章节证据为空，与"该章确实
    # 没有材料"在指标上完全一致——把这种运行算进模型质量对比，会把一次
    # AttributeError 记成"这个模型覆盖率低"。这是评测有效性判据，不是扣分项。
    section_failures = list(final_event.get("section_failures") or [])
    # 回放运行冻结了规划与检索，**不是**一次完整的端到端运行。它回答的是
    # "换掉抽取模型会不会改变结果"，不是"系统能不能过"。把两者混成同一个
    # pass/fail 就是拿实验装置的成绩冒充生产成绩（BC-25 的形态）。
    fixture = result.get("retrieval_fixture") or {}
    fixture_mode = str(fixture.get("mode") or "none")
    is_ablation = fixture_mode == "replay"
    # 检索计划不足（BC-62）。**不扣分、不设闸门**——它不是质量缺陷，是实验
    # 条件缺陷：这一章只发了一次检索，那么"覆盖率低"到底是资料里没有、还是
    # 根本没去查，无法区分。跨轮次比分数前必须先看这一项是否对齐。
    query_shortfalls = list(final_event.get("query_plan_shortfalls") or [])
    window_drops = list(final_event.get("extraction_window_drops") or [])
    # 证据闸门的卡点分布。不参与打分——它回答的是"覆盖率为什么低"，
    # 而不是"这份报告好不好"。没有它，"最大卡点是哪一道闸门"只能靠猜。
    rag_summary = final_event.get("rag_evidence_summary") or {}
    rejection_breakdown = list(rag_summary.get("rejection_breakdown") or [])
    requires_review = bool((final_event.get("risk_assessment") or {}).get("requires_human_review"))
    reference_action = str(assessment.get("recommended_action") or "")
    decision_aligned = requires_review if "人工复核" in reference_action else True
    insufficient_aligned = (
        assessment.get("normalized", {}).get("transaction_readiness") != "insufficient_evidence"
        or any(term in report for term in ("信息不足", "无法支撑", "补充尽调", "未核实"))
    )
    blocking_errors = list(final_event.get("errors") or [])

    points = {
        "sealed_scope": 20.0 if isolated else 0.0,
        "cutoff_safety": 15.0 if cutoff_safe else 0.0,
        "decision_alignment": 20.0 if decision_aligned and insufficient_aligned else 0.0,
        "reference_value_coverage": round(30.0 * claim_coverage, 2),
        "citation_coverage": round(10.0 * citation_coverage, 2),
        "report_completed": 5.0 if result.get("status") == "completed" and len(report) >= 1000 else 0.0,
    }
    total = round(sum(points.values()), 2)
    gates = {
        "sealed_scope": isolated,
        "cutoff_safe": cutoff_safe,
        "decision_aligned": decision_aligned and insufficient_aligned,
        "no_blocking_review_errors": not blocking_errors,
        "no_section_extraction_failures": not section_failures,
        "reference_value_coverage_at_least_60pct": claim_coverage >= 0.60,
        # BC-58 的验收条件：全部决策主张都能映射到核心或场景字段。
        # 这一条**先于**覆盖率成立——schema 表达不了的字段，模型再强也产不出。
        "all_reference_claims_mappable": not unmapped_claim_ids,
    }
    verdict = "pass" if all(gates.values()) and total >= 75 else "fail"
    # 有章节故障时连"这一轮能不能用来评价模型"都不成立，必须比 fail 更醒目：
    # fail 意味着"跑完了但没达标"，invalid 意味着"这一轮不算数"。
    if section_failures:
        verdict = "invalid"
    # 消融运行单独一档，且优先级最高：它的分数只能与**另一次同装置的消融**
    # 相比，不能与完整运行相比，也不能被当成"系统达标了"。
    elif is_ablation:
        verdict = "ablation"
    return {
        "case_id": case_id,
        "session_id": result.get("session_id"),
        "label_grade": assessment.get("label_grade"),
        "verdict": verdict,
        "score": total,
        # 归因所需的实验条件，与分数同级呈现：分数脱离这两项没有意义。
        "agent_models": result.get("agent_models") or {},
        "retrieval_fixture": {
            "mode": fixture_mode,
            "content_sha256": str(fixture.get("content_sha256") or ""),
        },
        "full_pipeline_run": not is_ablation,
        "score_note": "确定性门槛筛查分，不替代语义事实核验或人工评审",
        "points": points,
        "gates": gates,
        "metrics": {
            "report_length": len(report),
            "scoreable_reference_claims": len(scoreable),
            "matched_reference_claims": len(hit_ids),
            "reference_value_coverage": round(claim_coverage, 4),
            "expected_reference_sources": len(expected_source_ids),
            "cited_reference_sources": len(cited_source_ids),
            "citation_coverage": round(citation_coverage, 4),
            "blocking_error_count": len(blocking_errors),
            "section_failure_count": len(section_failures),
            "retrieved_result_count": search["retrieved_result_count"],
            "query_plan_shortfall_sections": len(query_shortfalls),
            "extraction_window_dropped_results": sum(
                int(row.get("dropped") or 0) for row in window_drops),
            "rag_evidence_verified": rag_summary.get("verified", 0),
            "rag_evidence_rejected": rag_summary.get("rejected", 0),
            "top_rejection_reason": (rejection_breakdown[0]["reason"]
                                     if rejection_breakdown else ""),
            "core_claim_coverage": by_scope.get("core", {}).get("coverage", 0.0),
            "scenario_claim_coverage": by_scope.get("scenario", {}).get("coverage", 0.0),
            "unmapped_reference_claims": len(unmapped_claim_ids),
        },
        "coverage_by_scope": by_scope,
        "unmapped_claim_ids": unmapped_claim_ids,
        "section_failures": section_failures,
        "query_plan_shortfalls": query_shortfalls,
        "extraction_window_drops": window_drops,
        "rag_rejection_breakdown": rejection_breakdown,
        "matched_claim_ids": hit_ids,
        "missed_claim_ids": miss_ids,
        "cited_source_ids": cited_source_ids,
        "blocking_errors": blocking_errors,
        "report_post_cutoff_dates": report_post_cutoff_dates,
        "search_audit": search,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    score = score_run(run_dir)
    output = args.output.resolve() if args.output else run_dir / "score.json"
    output.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(score, ensure_ascii=False, indent=2))
    # 三种非 pass 各有退出码：批量脚本必须能区分"这轮不达标"（2）、
    # "这轮不算数"（3）和"这轮是消融、不参与达标判定"（4），
    # 否则一次章节故障或一次消融会被当成一次真实的质量结论。
    return {"pass": 0, "invalid": 3, "ablation": 4}.get(score["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
