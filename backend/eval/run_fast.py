# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
快速层评测：核查清单判定规则的正确性（不调用 LLM）

为什么要分快速层和慢速层：
    一次完整尽调约 10 分钟 + API 费用，20 个用例根本无法迭代。
    而 v0.2 代码评审发现的 5 个缺陷**全部出在判定规则里，没有一个出在模型输出**。
    快速层正好覆盖出问题最多的地方，秒级完成，每次改动都能跑。

指标：
    字段状态准确率   —— 每个核查项的状态是否与 ground truth 一致
    未核实识别率     —— 真正取不到的字段是否被正确标为 unverified（漏标最危险）
    无记录识别率     —— 「查了但无记录」是否被正确判为已核实的正面结论
    异常识别率       —— 属性型字段的空结果是否被标记为主体存疑

用法：
    cd backend && python eval/run_fast.py
    cd backend && python eval/run_fast.py --case EVAL-003
"""
import argparse
import json
import os
import sys
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service.company_profile import fill_field_checks, profile_to_facts  # noqa: E402
from service.company_profile import replay_from_profile, verify_field_checks  # noqa: E402
from service.datasource import apply_all  # noqa: E402
from service.risk_scorecard import PROFILE_BACKED_FIELDS, score as score_risk  # noqa: E402
from service.verification import build_scoring_view  # noqa: E402

EVAL_DATA = os.path.join(_HERE, "..", "app", "data", "companies_eval.json")
GROUND_TRUTH = os.path.join(_HERE, "ground_truth.json")


def _load(path: str) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_case(company: Dict, expected: Dict) -> Dict:
    """对单个企业跑清单填充，与 ground truth 比对"""
    checks = build_field_checks(checked_at="2026-08-10T00:00:00")
    facts = profile_to_facts(company)
    fill_field_checks(company, facts, checks)
    # 走与生产同一条链路：档案填充 → 数据源适配层 → 证据链校验 → 评分视图。
    # 评测若只跑档案填充，测的就不是生产行为（BC-15 的教训）。
    evidence_store: Dict = {}
    apply_all(company, checks, evidence_store)
    comp = compute_completeness(checks)
    by_id = {c["field_id"]: c for c in checks}

    # —— 字段状态准确率 ——
    exp_status = expected.get("expected_status", {})
    status_hits, status_misses = 0, []
    for fid, want in exp_status.items():
        got = by_id.get(fid, {}).get("status")
        if got == want:
            status_hits += 1
        else:
            status_misses.append({"field": fid, "expected": want, "actual": got})

    # —— 「查了但无记录」是否被判为正面结论 ——
    no_rec_expected = expected.get("expected_no_record_fields", [])
    no_rec_hits, no_rec_misses = 0, []
    for fid in no_rec_expected:
        c = by_id.get(fid, {})
        if c.get("status") == "verified" and c.get("value") == "经查询，无相关记录":
            no_rec_hits += 1
        else:
            no_rec_misses.append({
                "field": fid, "status": c.get("status"), "value": c.get("value")
            })

    # —— 属性型空结果是否标记为主体存疑 ——
    anomaly = expected.get("expected_anomaly_fields") or {}
    anomaly_fields = anomaly.get("fields", [])
    anomaly_kw = anomaly.get("reason_must_contain", "")
    anomaly_hits, anomaly_misses = 0, []
    for fid in anomaly_fields:
        c = by_id.get(fid, {})
        reason = c.get("failure_reason") or ""
        if c.get("status") == "unverified" and anomaly_kw in reason:
            anomaly_hits += 1
        else:
            anomaly_misses.append({"field": fid, "status": c.get("status"), "reason": reason[:60]})

    # —— 风险等级与闸门理由（v0.7 新增）——
    #
    # 只断言等级是不够的：等级可能对了但理由是错的。BC-18 就是这个形态——
    # 等级正确（中风险），理由却说成"这次核实率不足"，实为"系统根本查不了"。
    # 因此同时断言**决定性闸门必须出现**与**特定闸门绝不能出现**。
    #
    # 判据用 gate_kinds（机器可读）而非中文措辞：靠中文子串判断闸门，
    # 就是扫描器同款的开集脆弱性（BC-47）。
    chain = verify_field_checks(company, checks, evidence_store)
    view, unmergeable = build_scoring_view(
        company, checks, evidence_store,
        profile_backed_fields=PROFILE_BACKED_FIELDS,
        profile_replay_fn=replay_from_profile,
    )
    chain_ok = chain.ok and not unmergeable
    assessment = score_risk(view, checks, comp)
    got_level = assessment["level"]
    want_level = expected.get("expected_risk_level")
    level_ok = (want_level is None) or (got_level == want_level)

    kinds = set(assessment.get("gate_kinds") or [])
    need = expected.get("expected_gate_kinds_include") or []
    forbid = expected.get("expected_gate_kinds_exclude") or []
    gate_missing = [k for k in need if k not in kinds]
    gate_forbidden = [k for k in forbid if k in kinds]
    gate_ok = not gate_missing and not gate_forbidden

    # —— 完整度统计是否吻合 ——
    exp_comp = expected.get("expected_completeness", {})
    comp_ok = (
        comp["required_verified"] == exp_comp.get("required_verified")
        and comp["required_total"] == exp_comp.get("required_total")
    )

    return {
        "case_id": expected["case_id"],
        "scenario": expected["scenario"],
        "status_total": len(exp_status),
        "status_hits": status_hits,
        "status_misses": status_misses,
        "no_record_total": len(no_rec_expected),
        "no_record_hits": no_rec_hits,
        "no_record_misses": no_rec_misses,
        "anomaly_total": len(anomaly_fields),
        "anomaly_hits": anomaly_hits,
        "anomaly_misses": anomaly_misses,
        "chain_ok": chain_ok,
        "chain_problems": [m["reason"] for m in chain.mismatches] + [u["reason"] for u in unmergeable],
        "evidence_count": len(evidence_store),
        "level_ok": level_ok,
        "level_actual": got_level,
        "level_expected": want_level,
        "gate_ok": gate_ok,
        "gate_missing": gate_missing,
        "gate_forbidden": gate_forbidden,
        "gate_kinds_actual": sorted(kinds),
        "completeness_ok": comp_ok,
        "completeness_actual": f"{comp['required_verified']}/{comp['required_total']}",
        "completeness_expected": f"{exp_comp.get('required_verified')}/{exp_comp.get('required_total')}",
        "by_category": comp["by_category"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="只跑指定用例，如 EVAL-003")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印每个字段的实际状态")
    args = ap.parse_args()

    data = _load(EVAL_DATA)
    truth = _load(GROUND_TRUTH)
    companies = {c["company_id"]: c for c in data["companies"]}

    cases = truth["cases"]
    if args.case:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            print(f"未找到用例 {args.case}")
            return 2

    results: List[Dict] = []
    for exp in cases:
        company = companies.get(exp["case_id"])
        if not company:
            print(f"⚠ 数据文件中缺少 {exp['case_id']}")
            continue
        results.append(run_case(company, exp))

    # —— 输出 ——
    print("=" * 74)
    print("快速层评测：核查清单判定规则")
    print("=" * 74)

    tot_s = tot_sh = tot_n = tot_nh = tot_a = tot_ah = 0
    comp_ok_n = level_ok_n = gate_ok_n = chain_ok_n = 0
    for r in results:
        tot_s += r["status_total"]; tot_sh += r["status_hits"]
        tot_n += r["no_record_total"]; tot_nh += r["no_record_hits"]
        tot_a += r["anomaly_total"]; tot_ah += r["anomaly_hits"]
        comp_ok_n += 1 if r["completeness_ok"] else 0
        chain_ok_n += 1 if r["chain_ok"] else 0
        level_ok_n += 1 if r["level_ok"] else 0
        gate_ok_n += 1 if r["gate_ok"] else 0

        flag = "PASS" if (r["status_hits"] == r["status_total"]
                          and r["no_record_hits"] == r["no_record_total"]
                          and r["anomaly_hits"] == r["anomaly_total"]
                          and r["completeness_ok"]
                          and r["level_ok"] and r["gate_ok"]
                          and r["chain_ok"]) else "FAIL"
        print(f"\n[{flag}] {r['case_id']}  {r['scenario']}")
        print(f"       字段状态 {r['status_hits']}/{r['status_total']}"
              f" | 无记录识别 {r['no_record_hits']}/{r['no_record_total']}"
              f" | 异常识别 {r['anomaly_hits']}/{r['anomaly_total']}"
              f" | 核实率 {r['completeness_actual']}"
              f" (期望 {r['completeness_expected']}) {'OK' if r['completeness_ok'] else '✗'}")
        for m in r["status_misses"]:
            print(f"         ✗ {m['field']}: 期望 {m['expected']}，实际 {m['actual']}")
        for m in r["no_record_misses"]:
            print(f"         ✗ 无记录项 {m['field']}: status={m['status']} value={m['value']!r}")
        print(f"       证据 {r['evidence_count']} 条 | 证据链 {'OK' if r['chain_ok'] else '✗ ' + str(r['chain_problems'])}")
        print(f"       风险等级 {r['level_actual']}"
              f" (期望 {r['level_expected']}) {'OK' if r['level_ok'] else '✗'}"
              f" | 闸门 {'OK' if r['gate_ok'] else '✗'} {r['gate_kinds_actual']}")
        for m in r["anomaly_misses"]:
            print(f"         ✗ 异常项 {m['field']}: status={m['status']} reason={m['reason']!r}")
        if r["gate_missing"]:
            print(f"         ✗ 缺少决定性闸门: {r['gate_missing']}")
        if r["gate_forbidden"]:
            print(f"         ✗ 触发了不该触发的闸门: {r['gate_forbidden']}")

    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)

    def pct(a, b):
        return f"{a}/{b} = {a / b:.1%}" if b else f"{a}/{b} = n/a"

    print(f"  字段状态准确率     {pct(tot_sh, tot_s)}")
    print(f"  无记录识别率       {pct(tot_nh, tot_n)}")
    print(f"  主体异常识别率     {pct(tot_ah, tot_a)}")
    print(f"  核实率统计吻合     {pct(comp_ok_n, len(results))}")
    print(f"  风险等级一致性     {pct(level_ok_n, len(results))}")
    print(f"  闸门理由正确性     {pct(gate_ok_n, len(results))}")
    print(f"  证据链完整性       {pct(chain_ok_n, len(results))}")

    # ⚠️ 判定必须覆盖**全部**已呈现的指标。
    #
    # 此前 all_pass 只看字段状态/无记录/主体异常/核实率四项，等级一致性、
    # 闸门理由、证据链完整性三项**印在报告里却不参与判定**——装置可以一边
    # 显示「风险等级一致性 80%」一边宣布「全部通过」。
    #
    # 与 BC-12 同形（评测装置产出误导性结论），且 chain_ok 是 v0.7-B
    # 新加的指标，加了却没纳入判定——一个不参与判定的指标等于没有指标。
    all_pass = (tot_sh == tot_s and tot_nh == tot_n
                and tot_ah == tot_a and comp_ok_n == len(results)
                and level_ok_n == len(results) and gate_ok_n == len(results)
                and chain_ok_n == len(results))
    print(f"\n结果：{'全部通过' if all_pass else '存在未通过项'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
