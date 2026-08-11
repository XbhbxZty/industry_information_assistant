"""
Critic 清单交叉校验评测（含方差分离）

设计要点（BC-13 的直接产物）：

1. **每例重复运行 N 次取多数**
   LLM 判定不是确定性的。单次运行的百分比无法区分"真缺陷"与"随机波动"，
   而在小样本上按单次结果调提示词，会出现"失败用例换人而总数不变"的打地鼠现象。
   本运行器把结果分成三档：
       稳定通过  N/N 次正确   —— 可信
       不稳定    介于两者之间 —— 方差，不应据此改提示词
       稳定失败  0/N 次正确   —— 真缺陷，值得修

2. **样本量**
   注入 10 例 + 对照 18 例。对照数量多于注入是有意的：
   误报比漏检更容易被忽视，而一个见谁都咬的检查器等于没有。

3. **并发执行**
   28 例 × 3 次 = 84 次调用，串行约 40 分钟。并发后约 8 分钟。

用法：
    python eval/run_critic.py                  # 全量，每例 3 次
    python eval/run_critic.py --repeat 1       # 快速冒烟
    python eval/run_critic.py --only INJ-05
    python eval/run_critic.py --kind clean     # 只跑对照
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from typing import Dict, List, Optional

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
CASES = os.path.join(_HERE, "critic_cases.json")

CHECKLIST_ISSUE_TYPES = {
    "unverified_as_fact",
    "conflict_silently_resolved",
    "unsupported_risk_conclusion",
}

# 描述中出现这些词也算命中目标违规（模型可能用等价表述但选了别的 type）
EQUIV_KEYWORDS = {
    "unverified_as_fact": ["未核实", "未经核实", "当作事实", "越权断言"],
    "conflict_silently_resolved": ["冲突", "不一致", "单方面采信", "未披露"],
    "unsupported_risk_conclusion": ["缺乏支撑", "无支撑", "信息缺口", "依据不足", "缺乏依据"],
}


def _load(path: str) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_state(company: Dict, section_id: str, text: str) -> Dict:
    """
    构造待审 state。

    ⚠️ 关键：清单必须**与被审文本的范围匹配**。

    生产环境中 Critic 拿到的是完整 8 章报告 + 完整 20 项清单，两者范围一致。
    但评测用例是单章节文本，若仍配完整清单，Critic 会合理地发现
    "报告没覆盖担保圈、监管处罚"并据此报违规——那是评测装置制造的错配，
    不是 Critic 的缺陷。首次实测就因此产生大量误报（与 BC-12 同类）。

    因此这里只注入本章节对应的核查项，外加 sec_8（风险汇总）——
    因为风险结论天然要消费全部清单，测 unsupported_risk_conclusion 时需要它。
    """
    state = create_initial_state(
        query=f"请对{company['name']}做贷前尽职调查，授信金额2000万元",
        session_id="eval-critic",
    )
    facts = profile_to_facts(company)
    state["facts"] = facts
    all_checks = build_field_checks(checked_at="2026-08-10T00:00:00")
    fill_field_checks(company, facts, all_checks)

    if section_id == "sec_8":
        # 风险汇总章节：结论要基于全部清单，保留完整视图
        scoped = all_checks
    else:
        scoped = [c for c in all_checks if c.get("section_id") == section_id]

    state["field_checks"] = scoped
    # 完整度统计始终按全量计算——报告引述"核实 9/15"时应能对上
    state["completeness"] = compute_completeness(all_checks)
    state["company_name"] = company["name"]
    state["phase"] = ResearchPhase.REVIEWING.value
    state["outline"] = [{"id": section_id, "title": "待检章节", "description": ""}]
    state["draft_sections"] = {section_id: text}
    state["final_report"] = text
    return state


# 由 --model / --temperature 覆盖，None 表示用 llm_config 的默认值
_MODEL_OVERRIDE: Optional[str] = None
_TEMP_OVERRIDE: Optional[float] = None


def _make_critic() -> CriticMaster:
    cfg = get_config()
    critic = CriticMaster(
        llm_api_key=cfg.api_key,
        llm_base_url=cfg.base_url,
        model=_MODEL_OVERRIDE or cfg.agents.critic.model,
    )
    if _TEMP_OVERRIDE is not None:
        # BaseAgent.call_llm 的 temperature 由调用方传入，
        # 这里通过实例属性让 _review_content 用上覆盖值
        critic._eval_temperature = _TEMP_OVERRIDE
    return critic


def _hit_injection(issues: List[Dict], violation: str) -> bool:
    """注入用例：是否检出目标违规"""
    kw = EQUIV_KEYWORDS.get(violation, [])
    for i in issues:
        if i.get("issue_type") == violation:
            return True
        if i.get("severity") not in ("critical", "major"):
            continue
        text = f"{i.get('description', '')}{i.get('evidence', '')}"
        if any(k in text for k in kw):
            return True
    return False


def _false_positive(issues: List[Dict]) -> List[Dict]:
    """对照用例：是否被误报清单类违规（仅计 critical/major）"""
    return [
        i for i in issues
        if i.get("issue_type") in CHECKLIST_ISSUE_TYPES
        and i.get("severity") in ("critical", "major")
    ]


async def _run_once(sem: asyncio.Semaphore, company: Dict, case: Dict, kind: str) -> Dict:
    async with sem:
        state = _build_state(company, case["section"], case["text"])
        critic = _make_critic()
        # 走与生产完全相同的路径：确定性扫描 + LLM + 强制分工过滤。
        # 此前这里直接调 _review_content()，只测到 LLM 单独表现，
        # 却被当作系统整体表现解读，导致归因错误。
        try:
            llm_result = await critic._review_content(state)
        except Exception as e:
            llm_result = None
            _ = e
        try:
            review = critic.merge_review(state, llm_result) or {}
        except Exception as e:
            return {"ok": None, "error": f"{type(e).__name__}: {e}", "issues": []}
        issues = review.get("issues", [])
        if kind == "injection":
            return {"ok": _hit_injection(issues, case["violation"]), "issues": issues}
        fps = _false_positive(issues)
        return {"ok": not fps, "issues": issues, "fps": fps}


async def run_case(sem, company, case, kind, repeat) -> Dict:
    runs = await asyncio.gather(*[_run_once(sem, company, case, kind) for _ in range(repeat)])
    oks = [r["ok"] for r in runs if r["ok"] is not None]
    errs = [r.get("error") for r in runs if r.get("error")]
    n_ok = sum(1 for o in oks if o)
    n = len(oks)
    if n == 0:
        verdict = "ERROR"
    elif n_ok == n:
        verdict = "STABLE_PASS"
    elif n_ok == 0:
        verdict = "STABLE_FAIL"
    else:
        verdict = "UNSTABLE"
    sample_fps = next((r.get("fps") for r in runs if r.get("fps")), [])
    return {
        "id": case["id"], "kind": kind, "verdict": verdict,
        "n_ok": n_ok, "n": n, "errors": errs,
        "note": case.get("why") or case.get("exercises", ""),
        "sample_fps": sample_fps,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3, help="每例重复次数（默认3，用于分离方差）")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--only", help="只跑指定用例 id")
    ap.add_argument("--kind", choices=["injection", "clean"], help="只跑某一类")
    ap.add_argument("--model", help="覆盖 Critic 模型，用于模型对比实验")
    ap.add_argument("--temperature", type=float, help="覆盖温度，用于方差实验")
    ap.add_argument("--label", default="", help="本次实验的标签，打印在标题里")
    args = ap.parse_args()

    global _MODEL_OVERRIDE, _TEMP_OVERRIDE
    _MODEL_OVERRIDE = args.model
    _TEMP_OVERRIDE = args.temperature

    data = _load(EVAL_DATA)
    cases = _load(CASES)
    companies = {c["company_id"]: c for c in data["companies"]}

    todo = []
    if args.kind != "clean":
        todo += [(c, "injection") for c in cases["injection_cases"]]
    if args.kind != "injection":
        todo += [(c, "clean") for c in cases["clean_cases"]]
    if args.only:
        todo = [(c, k) for c, k in todo if c["id"] == args.only]
    if not todo:
        print("无匹配用例")
        return 2

    print("=" * 78)
    from config.llm_config import get_config as _gc
    _m = _MODEL_OVERRIDE or _gc().agents.critic.model
    _t = _TEMP_OVERRIDE if _TEMP_OVERRIDE is not None else 0.0
    print(f"Critic 清单交叉校验评测 ｜ {len(todo)} 例 × {args.repeat} 次 = "
          f"{len(todo) * args.repeat} 次调用，并发 {args.concurrency}")
    print(f"模型 {_m} ｜ 温度 {_t}" + (f" ｜ {args.label}" if args.label else ""))
    print("=" * 78)

    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*[
        run_case(sem, companies[c["based_on"]], c, k, args.repeat) for c, k in todo
    ])

    for kind, title in (("injection", "注入组（测检出率，应全部检出）"),
                        ("clean", "对照组（测误报率，应全部无误报）")):
        rs = [r for r in results if r["kind"] == kind]
        if not rs:
            continue
        print(f"\n{'-' * 78}\n{title}\n{'-' * 78}")
        for r in sorted(rs, key=lambda x: x["id"]):
            mark = {"STABLE_PASS": "✓", "STABLE_FAIL": "✗", "UNSTABLE": "~", "ERROR": "!"}[r["verdict"]]
            print(f"  {mark} {r['id']}  {r['n_ok']}/{r['n']}  {r['note'][:52]}")
            if r["verdict"] in ("STABLE_FAIL", "UNSTABLE") and r["sample_fps"]:
                for f in r["sample_fps"][:1]:
                    print(f"        误报 [{f.get('severity')}] {f.get('issue_type')}: "
                          f"{str(f.get('description'))[:70]}")
            for e in r["errors"][:1]:
                print(f"        错误: {e[:70]}")

    print("\n" + "=" * 78)
    print("汇总（按稳定性分档）")
    print("=" * 78)

    for kind, label in (("injection", "检出"), ("clean", "无误报")):
        rs = [r for r in results if r["kind"] == kind]
        if not rs:
            continue
        c = Counter(r["verdict"] for r in rs)
        total = len(rs)
        print(f"\n{label}（{total} 例）")
        print(f"  稳定正确 {c['STABLE_PASS']:>2}/{total}  ({c['STABLE_PASS'] / total:.1%})")
        print(f"  不稳定   {c['UNSTABLE']:>2}/{total}  ← 方差，不应据此调提示词")
        print(f"  稳定失败 {c['STABLE_FAIL']:>2}/{total}  ← 真缺陷，值得修")
        if c["ERROR"]:
            print(f"  调用错误 {c['ERROR']:>2}/{total}")
        # 传统口径（所有运行合并），便于与单次运行的历史数字对比
        tot_ok = sum(r["n_ok"] for r in rs)
        tot_n = sum(r["n"] for r in rs)
        if tot_n:
            print(f"  逐次口径 {tot_ok}/{tot_n} = {tot_ok / tot_n:.1%}")

    inj = [r for r in results if r["kind"] == "injection"]
    cln = [r for r in results if r["kind"] == "clean"]
    stable_fail = [r["id"] for r in results if r["verdict"] == "STABLE_FAIL"]
    unstable = [r["id"] for r in results if r["verdict"] == "UNSTABLE"]

    print("\n" + "-" * 78)
    if stable_fail:
        print(f"需要修的稳定缺陷：{', '.join(stable_fail)}")
    else:
        print("无稳定缺陷")
    if unstable:
        print(f"存在方差的用例（先不动，样本再大些再看）：{', '.join(unstable)}")
    return 0 if not stable_fail else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
