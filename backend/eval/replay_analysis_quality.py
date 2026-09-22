"""Synthetic live Critic checks, NOT an E2E test. No DB, browser or file writes.

Explicit opt-in: python backend/eval/replay_analysis_quality.py --live
Only the invented text below is sent to the configured model provider.
"""
import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

SOURCE = """虚构测试公司甲，金额单位万元，单体口径，数据未经审计。
本期经营现金流100，上期400。本期应收增加240、存货增加30；没有上期调节表。
截至测试截止日，期末长账龄余额250，收到对应长账龄90、对应短账龄40、新订单预收70，总收款200。
这些是内部台账数据，没有银行流水独立核验。"""
BAD = """据内部台账（尚未经独立核验），应收增加240占现金流同比下降300的80%，因此是同比下降主因。
期末应收已收回90；新订单预收70，长账龄未回收160，覆盖率36%。
资料不足，不能评级或审批。补件：银行流水核验回款、上期调节表核查归因。"""
GOOD = """据内部台账（尚未经独立核验），经营现金流由400降至100，下降300。
本期应收增加240、存货增加30，均占用现金；缺少上期各调节项，不能把240/300解释为同比贡献率或据此认定主因。
期末应收回款130=长账龄90+短账龄40；另有新订单预收70，全部收款200=90+40+70。
长账龄剩余160=250-90；同一债权池覆盖率36%=90/250，不能说风险已解除。
资料不足，不能评级或审批。补件：银行流水核验回款、上期调节表核查同比归因。"""


CASES = {
    "attribution_error": (SOURCE, BAD.splitlines()[0] + "\n" + "\n".join(GOOD.splitlines()[2:]), "cashflow_attribution"),
    "receipt_error": (SOURCE, "\n".join(GOOD.splitlines()[:2]) + "\n" + "\n".join(BAD.splitlines()[1:]), "receipt_reconciliation"),
    "correct_with_limits": (SOURCE, GOOD, None),
    "insufficient_disclosed": (
        "虚构测试公司甲内部台账：本期经营现金流100，上期400。收到款项200，但缺少债权对应关系、账龄、上期调节表和银行流水。金额万元，单体，未经审计。",
        "据未经独立核验的内部台账，经营现金流由400降至100，下降300。缺少调节表，不能认定同比下降主因。收到200但没有债权对应关系，不能区分期末应收回收与新订单预收，无法计算剩余长账龄及覆盖率。优先补逐笔债权勾稽及期末账龄、两期调节表、银行流水。资料不足，不能评级或审批。", None),
}


async def main(args):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from config.llm_config import get_config
    from service.deep_research_v2.agents.critic import CriticMaster
    from service.deep_research_v2.state import create_initial_state
    config = get_config()
    critic = CriticMaster(config.api_key, config.base_url, model=config.agents.critic.model)
    original_call = critic.call_llm

    calls = []
    async def bounded_call(**kwargs):
        requested_meta = kwargs.get("return_meta", False)
        # Keep the production output budget and temperature unless explicitly testing a cap.
        kwargs.update(timeout=60, max_retries=0, return_meta=True)
        if args.max_tokens:
            kwargs["max_tokens"] = args.max_tokens
        content, meta = await asyncio.wait_for(original_call(**kwargs), timeout=70)
        try:
            strict_object = isinstance(json.loads(content), dict)
        except (ValueError, TypeError):
            strict_object = False
        calls.append({"request": {k: kwargs[k] for k in ("max_tokens", "temperature", "timeout", "max_retries")},
                      "meta": meta, "strict_json_object": strict_object, "content": content})
        return (content, meta) if requested_meta else content
    critic.call_llm = bounded_call
    results = []
    for repeat, name in ((r, n) for r in range(1, args.repeats + 1) for n in args.cases):
        source, report, expected_issue = CASES[name]
        calls.clear()
        state = create_initial_state("说明现金流下降原因及回款分类、剩余长账龄、覆盖率与核查限制。",
                                     "synthetic-analysis-review", due_diligence=True, research_strategy="agent",
                                     subject_name="虚构测试公司甲", search_web=False, search_local=False,
                                     as_of="2026-02-20")
        state["final_report"] = report
        critic.as_of = state["as_of"]
        state["agent_investigation"] = {"summary": report, "findings": [], "sources": {
            "s1": {"title": "合成台账", "read": True, "read_texts": [source], "summary": source}}}
        if args.revise:
            state["agent_investigation"]["findings"] = [{"claim": report.splitlines()[0], "quote": source,
                "source_id": "s1", "title": "合成台账", "url": "synthetic://ledger", "kind": "support",
                "verified": False, "citation_status": "located", "inference_status": "not_reviewed"}]
        try:
            raw = await critic._review_content(state)
            result = critic.merge_review(state, raw)
            results.append({"case": name, "repeat": repeat, "expected_issue": expected_issue, "review": result,
                            "calls": list(calls)})
            if args.revise and expected_issue and not result.get("degraded"):
                from service.deep_research_v2.agents.writer import LeadWriter
                from service.deep_research_v2.investigation_report import append_investigation_report
                writer = LeadWriter(config.api_key, config.base_url, model=config.agents.writer.model)
                writer.as_of = state["as_of"]
                revision_calls = []
                writer_call = writer.call_llm
                async def traced_writer_call(**kwargs):
                    content, meta = await writer_call(**kwargs, return_meta=True)
                    revision_calls.append({"content": content, "meta": meta})
                    return content
                writer.call_llm = traced_writer_call
                state["critic_feedback"] = copy.deepcopy(result["issues"])
                before = copy.deepcopy(state)
                await writer._revise_agent_analysis(state)
                state["final_report"] = append_investigation_report("", state["agent_investigation"])
                calls.clear()
                followup = critic.merge_review(state, await critic._review_content(state))
                unchanged = all(state.get(k) == before.get(k) for k in ("field_checks", "risk_assessment", "critic_feedback"))
                unchanged = unchanged and state["agent_investigation"]["sources"] == before["agent_investigation"]["sources"]
                unchanged = unchanged and all(new.get(k) == old.get(k)
                    for new, old in zip(state["agent_investigation"]["findings"], before["agent_investigation"]["findings"])
                    for k in ("quote", "source_id", "verified", "citation_status"))
                results[-1]["revision"] = {"model": config.agents.writer.model, "protected_state_unchanged": unchanged,
                    "notebook": state["agent_investigation"], "review": followup, "calls": list(calls),
                    "writer_calls": revision_calls, "writer_exercised": bool(revision_calls),
                    "errors": state.get("errors", [])}
        except Exception as exc:
            results.append({"case": name, "repeat": repeat, "error": type(exc).__name__, "calls": list(calls)})
        print("CASE_JSON=" + json.dumps(results[-1], ensure_ascii=False), flush=True)
    print("RESULT_JSON=" + json.dumps({"test_type": "focused_synthetic_critic_not_e2e",
                                       "model": config.agents.critic.model, "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--cases", nargs="+", choices=tuple(CASES), default=list(CASES))
    parser.add_argument("--max-tokens", type=int, choices=(4000, 8000), default=None,
                        help="omit to retain production configuration")
    parser.add_argument("--revise", action="store_true", help="also exercise real Writer revision and Critic recheck; still not E2E")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; sends only built-in synthetic cases to the configured provider")
    asyncio.run(main(args))
