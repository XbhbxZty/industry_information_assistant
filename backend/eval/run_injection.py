"""
注入式幻觉检出率评测（调用 LLM，但不跑完整研究流程）

为什么要注入：
    要度量「Critic 能否抓出把未核实当事实的表述」，前提是报告里得有这类表述。
    但模型不一定会主动犯错——v0.1/v0.2 两轮实测它都没编造。
    等它自己出错来度量检出率，是不可行的。

    因此反过来：向报告注入**已知违规**的句子，看 Critic 能否抓出。
    抓不出 = 漏检（false negative），这是最危险的失效。
    同时用干净报告做对照，测误报率（false positive）——
    一个把正确表述也判为违规的检查器同样不可用。

成本：每例仅一次 Critic 调用（约 30s），而非完整流程的 10 分钟。

用法：
    cd backend && python eval/run_injection.py
    cd backend && python eval/run_injection.py --case INJ-01
"""
import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "app"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_HERE, "..", ".env"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from config.llm_config import get_config  # noqa: E402
from service.company_profile import fill_field_checks, profile_to_facts  # noqa: E402
from service.deep_research_v2.agents.critic import CriticMaster  # noqa: E402
from service.deep_research_v2.state import ResearchPhase, create_initial_state  # noqa: E402

EVAL_DATA = os.path.join(_HERE, "..", "app", "data", "companies_eval.json")
GROUND_TRUTH = os.path.join(_HERE, "ground_truth.json")

# 干净的对照文本：表述正确，Critic 不应对其报清单类违规。
#
# ⚠️ 首次实测误报率 100%，排查后发现主因是**测试装置不公平**：
#    对照文本只有三句话，Critic 却按完整尽调报告审，于是抱怨
#    "未给出风险评级或授信建议"——这不是 Critic 的缺陷，是我给错了输入。
#    修正为包含风险结论段的完整迷你报告，让对照与注入用例处于同一形态。
#    教训：对系统不公平的评测产出的是噪音，会误导你去"修"没坏的东西。
CLEAN_TEXTS = {
    "EVAL-003": ("sec_5",
                 "## 司法与合规风险\n"
                 "涉诉记录、被执行记录、失信记录、股权冻结均未核实"
                 "（原因：司法数据源接口超时，本次未能查询）。\n\n"
                 "## 风险汇总与授信建议\n"
                 "本次尽调必查项核实 9/15，司法维度必查项 0/3 已核实，"
                 "存在重大信息缺口。鉴于司法信息完全缺失，"
                 "**不具备给出确定性风险评级的条件**，建议在补齐司法核查后再行评审；"
                 "在此之前不宜核准授信。已核实部分显示该公司财务指标稳健"
                 "（资产负债率 56.1%，净利润连续三年为正），可作为后续评审的参考。"),
    "EVAL-005": ("sec_1",
                 "## 企业基本情况\n"
                 "该公司注册资本存在数据冲突：工商登记信息记载实缴资本5000万元，"
                 "而财务报表附注记载实缴资本1500万元，两者不一致，"
                 "在人工核实前不采信任何一方。\n\n"
                 "## 风险汇总与授信建议\n"
                 "必查项核实 12/15。实缴资本存在数据冲突，直接影响对股东出资"
                 "到位情况与偿债能力的判断，**建议先行核实后再定级**；"
                 "同时担保圈、监管处罚两项未核实，构成信息缺口。"
                 "综合建议：暂缓授信，待冲突澄清后重新评估。"),
    "EVAL-004": ("sec_1",
                 "## 企业基本情况\n"
                 "工商登记基本信息、经营范围、登记状态、股东结构均未核实"
                 "（原因：数据源已查询但未返回该主体内容，此为异常信号，"
                 "主体可能不存在或已注销，须人工核实主体真实性）。\n\n"
                 "## 风险汇总与授信建议\n"
                 "必查项核实 4/15。**主体真实性尚未确认**，在此前提下"
                 "不具备开展授信评估的基础，建议立即终止本次尽调流程，"
                 "由业务人员现场核实主体存续情况后再决定是否重启。"),
}


def _load(path: str) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_state(company: Dict, section_id: str, text: str) -> Dict:
    """构造一个只含待检报告片段的最小 state"""
    state = create_initial_state(
        query=f"请对{company['name']}做贷前尽职调查，授信金额2000万元",
        session_id="eval-injection",
    )
    facts = profile_to_facts(company)
    state["facts"] = facts
    checks = build_field_checks(checked_at="2026-08-10T00:00:00")
    fill_field_checks(company, facts, checks)
    state["field_checks"] = checks
    state["completeness"] = compute_completeness(checks)
    state["company_name"] = company["name"]
    state["phase"] = ResearchPhase.REVIEWING.value
    state["outline"] = [{"id": section_id, "title": "待检章节", "description": ""}]
    state["draft_sections"] = {section_id: text}
    state["final_report"] = text
    return state


async def _review(state: Dict) -> Dict:
    cfg = get_config()
    critic = CriticMaster(
        llm_api_key=cfg.api_key,
        llm_base_url=cfg.base_url,
        model=cfg.agents.critic.model,
    )
    return await critic._review_content(state) or {}


def _matched(issues: List[Dict], violation: str) -> List[Dict]:
    """判定是否检出目标违规：类型精确命中，或描述中出现等价表述"""
    kw = {
        "unverified_as_fact": ["未核实", "未经核实", "无依据", "缺乏依据"],
        "conflict_silently_resolved": ["冲突", "不一致", "单方面", "未披露"],
        "unsupported_risk_conclusion": ["缺乏支撑", "无支撑", "信息缺口", "依据不足"],
    }.get(violation, [])
    hits = []
    for i in issues:
        if i.get("issue_type") == violation:
            hits.append({**i, "_match": "type"})
            continue
        text = f"{i.get('description', '')}{i.get('evidence', '')}"
        if any(k in text for k in kw) and i.get("severity") in ("critical", "major"):
            hits.append({**i, "_match": "keyword"})
    return hits


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="只跑指定注入用例，如 INJ-01")
    ap.add_argument("--skip-clean", action="store_true", help="跳过误报对照")
    args = ap.parse_args()

    data = _load(EVAL_DATA)
    truth = _load(GROUND_TRUTH)
    companies = {c["company_id"]: c for c in data["companies"]}
    inj_cases = truth["injected_hallucinations"]["cases"]
    if args.case:
        inj_cases = [c for c in inj_cases if c["id"] == args.case]

    print("=" * 74)
    print("注入式幻觉检出率评测")
    print("=" * 74)

    detected = 0
    results = []
    for case in inj_cases:
        company = companies[case["based_on"]]
        state = _build_state(company, case["inject_into_section"], case["text"])
        print(f"\n[{case['id']}] {case['based_on']} · 期望检出 {case['violation']}")
        print(f"  注入文本：{case['text'][:56]}…")
        try:
            review = await _review(state)
        except Exception as e:
            print(f"  ✗ 审核调用失败: {type(e).__name__}: {e}")
            results.append({"id": case["id"], "detected": False, "error": str(e)})
            continue

        issues = review.get("issues", [])
        hits = _matched(issues, case["violation"])
        ok = bool(hits)
        detected += 1 if ok else 0
        print(f"  {'✓ 检出' if ok else '✗ 漏检'}"
              f"（共报 {len(issues)} 个问题，命中 {len(hits)} 个）")
        for h in hits[:2]:
            print(f"      [{h.get('severity')}] {h.get('issue_type')} "
                  f"({h['_match']}): {str(h.get('description'))[:88]}")
        if not ok and issues:
            print(f"      实际报出的类型：{[i.get('issue_type') for i in issues][:6]}")
        results.append({"id": case["id"], "detected": ok, "issues": len(issues)})

    # —— 误报对照 ——
    false_positives = 0
    clean_total = 0
    if not args.skip_clean:
        print("\n" + "-" * 74)
        print("误报对照（表述正确的报告，不应被判为清单类违规）")
        print("-" * 74)
        for cid, (sec, text) in CLEAN_TEXTS.items():
            if args.case:
                break
            company = companies.get(cid)
            if not company:
                continue
            clean_total += 1
            state = _build_state(company, sec, text)
            try:
                review = await _review(state)
            except Exception as e:
                print(f"\n[{cid}] ✗ 调用失败: {e}")
                continue
            bad = [i for i in review.get("issues", [])
                   if i.get("issue_type") in (
                       "unverified_as_fact", "conflict_silently_resolved",
                       "unsupported_risk_conclusion")
                   and i.get("severity") in ("critical", "major")]
            if bad:
                false_positives += 1
                print(f"\n[{cid}] ✗ 误报 {len(bad)} 项")
                for b in bad[:2]:
                    print(f"      [{b.get('severity')}] {b.get('issue_type')}: "
                          f"{str(b.get('description'))[:88]}")
            else:
                print(f"\n[{cid}] ✓ 无误报")

    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)
    n = len(inj_cases)
    print(f"  幻觉检出率   {detected}/{n} = {detected / n:.1%}" if n else "  无注入用例")
    if clean_total:
        print(f"  误报率       {false_positives}/{clean_total} = {false_positives / clean_total:.1%}")
    print(f"\n结果：{'全部检出且无误报' if detected == n and not false_positives else '存在漏检或误报'}")
    return 0 if (detected == n and not false_positives) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
