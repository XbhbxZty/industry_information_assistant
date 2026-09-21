"""Two synthetic live Critic checks, NOT an E2E test. No DB, browser or file writes.

Explicit opt-in: python backend/eval/replay_analysis_quality.py --live
Only the invented text below is sent to the configured model provider.
"""
import argparse
import asyncio
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


async def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from config.llm_config import get_config
    from service.deep_research_v2.agents.critic import CriticMaster
    from service.deep_research_v2.state import create_initial_state
    config = get_config()
    critic = CriticMaster(config.api_key, config.base_url, model=config.agents.critic.model)
    original_call = critic.call_llm

    async def bounded_call(**kwargs):
        kwargs.update(timeout=45, max_retries=0, max_tokens=4000)
        return await asyncio.wait_for(original_call(**kwargs), timeout=55)
    critic.call_llm = bounded_call
    results = []
    for name, report in (("incorrect_attribution_and_receipts", BAD), ("correct_with_limits", GOOD)):
        state = create_initial_state("说明现金流下降原因及回款分类、剩余长账龄、覆盖率与核查限制。",
                                     "synthetic-analysis-review", due_diligence=True, research_strategy="agent",
                                     subject_name="虚构测试公司甲", search_web=False, search_local=False,
                                     as_of="2026-02-20")
        state["final_report"] = report
        state["agent_investigation"] = {"summary": report, "findings": [], "sources": {
            "s1": {"title": "合成台账", "read": True, "read_texts": [SOURCE], "summary": SOURCE}}}
        try:
            raw = await critic._review_content(state)
            result = critic.merge_review(state, raw)
            results.append({"case": name, "review": result})
        except Exception as exc:
            results.append({"case": name, "error": type(exc).__name__})
    print("RESULT_JSON=" + json.dumps({"test_type": "focused_synthetic_critic_not_e2e",
                                       "model": config.agents.critic.model, "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; sends only built-in synthetic cases to the configured provider")
    asyncio.run(main())
