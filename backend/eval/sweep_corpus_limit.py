# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""条数上界扫描 —— 多送材料换来的发现，值不值那份 token

## 为什么现在才问这个问题

BC-78 之前，送多少材料由 24000 字预算决定，而那个预算是照 **qwen-max**
的 30,720 token 上限定的——生产走 deepseek-v4-flash，实测 400,000 字
仍然通过。也就是说：**这一直不是容量问题，是配置问题**，只是被
一个错误的容量常量遮住了。

现在预算放开、条数按章分配，真正的旋钮变成 `INVESTIGATION_CORPUS_LIMIT`。
它是**成本/质量取舍**，所以要拿数据说话，而不是拍一个数。

## 为什么不跑完整流水线

一轮完整尽调 3 分钟、十几次模型调用，而这里只需要看
「同一份检索结果，留存多少条 → 抽取出多少发现」。
隔离成单次抽取，同样的钱能多跑一个数量级的样本（BC-63）。

跨层挑战也能算：`judge_findings` 是**纯代码**，
拿一份已跑完的 `field_checks` 喂给它即可，不必再跑 A 层。

## 必须走生产的留存函数

自己写一套留存逻辑就会在某个维度上与生产分叉，
测出来的是一个不存在的系统——**本 session 已经撞过三次**，
最后一次（BC-78）是探针连模型都跟生产不是同一个。

用法：
    python eval/sweep_corpus_limit.py --case case01 --limits 40 80 120 --repeats 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

#: 真实的八章检索词。取自一次真实运行的 `section_query_plan`——
#: 自己编检索词会让语料构成与生产不同，那样扫出来的曲线不适用。
SECTION_QUERIES: List[Tuple[str, List[str]]] = [
    ("sec_1", ["企业基本情况 成立时间 注册资本", "统一社会信用代码 登记状态",
               "主营业务 经营范围"]),
    ("sec_2", ["股权结构 股东 持股比例", "实际控制人 控股股东"]),
    ("sec_3", ["经营状况 产能 主要客户", "收入构成 市场份额 行业地位"]),
    ("sec_4", ["营业收入 净利润 财务数据", "资产负债率 现金流",
               "应收账款 存货"]),
    ("sec_5", ["司法诉讼 被执行 失信", "行政处罚 合规"]),
    ("sec_6", ["对外投资 关联方 关联交易", "对外担保 质押"]),
    ("sec_7", ["舆情 新闻 报道", "风险事件 负面"]),
    ("sec_8", ["风险汇总 授信"]),
]


async def _retrieve_once(scope) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """检索一次，所有档位共用。分别检索会让各档材料不同——那就不是在测条数了。"""
    from service.deep_research_v2.agents.scout import DeepScout

    scout = DeepScout(
        llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=os.getenv("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        search_api_key="")
    out = []
    for section_id, queries in SECTION_QUERIES:
        merged: List[Dict[str, Any]] = []
        for q in queries:
            outcome = await scout._execute_local_search(q, kb_scope=scope)
            merged.extend(outcome.results)
        out.append((section_id, merged))
    return out


class _Shim:
    def retain(self, state, results, section_id):
        from service.deep_research_v2.agents.scout import DeepScout
        DeepScout._retain_corpus_for_investigation(self, state, results, section_id)


def build_corpus(batches, limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """用生产留存函数建语料，只把条数上界换掉。

    ⚠️ 总量预算必须跟着放开，否则它会在高档位先咬住：
    limit=80 时 80×1200 = 96,000 字 > 50,000 预算，**扫的又是预算**。
    自变量只能有一个，另一个必须让路——这里让预算跟着 limit 走，
    并在汇总里报出实际字数，让「有没有被别的东西截住」可验证。
    """
    from service.deep_research_v2.agents import scout as scout_mod

    original = scout_mod.INVESTIGATION_CORPUS_LIMIT
    original_budget = scout_mod.INVESTIGATION_INPUT_CHAR_BUDGET
    scout_mod.INVESTIGATION_CORPUS_LIMIT = limit
    scout_mod.INVESTIGATION_INPUT_CHAR_BUDGET = max(
        original_budget, limit * scout_mod.INVESTIGATION_EXCERPT_CHARS + 10000)
    try:
        state: Dict[str, Any] = {
            "due_diligence_mode": True, "raw_sources": [],
            "outline": [{"section_id": s} for s, _ in SECTION_QUERIES],
        }
        shim = _Shim()
        for section_id, results in batches:
            shim.retain(state, results, section_id)
    finally:
        scout_mod.INVESTIGATION_CORPUS_LIMIT = original
        scout_mod.INVESTIGATION_INPUT_CHAR_BUDGET = original_budget
    sections = Counter(s["section_id"] for s in state["raw_sources"])
    return state["raw_sources"], {
        "chars": sum(len(s.get("summary") or "") for s in state["raw_sources"]),
        "sections_covered": len(sections),
        "per_section": dict(sections),
    }


async def extract(wizard, sources, subject, as_of):
    from service import investigation_layer as inv

    prompt = inv.EXPLORATORY_PROMPT.format(
        subject=subject, as_of=as_of,
        sources=inv.format_sources_for_prompt(sources),
        max_findings=inv.MAX_FINDINGS)   # 已在 run() 里按需放开
    try:
        content, meta = await wizard.call_llm(
            system_prompt=("你是尽职调查分析师的助手，只负责从给定材料中"
                           "摘录可溯源的事实。你的产出不进入授信决策，"
                           "不得给出风险判断或结论性评价。"),
            user_prompt=prompt, json_mode=True, temperature=0.2,
            max_tokens=wizard.INVESTIGATION_MAX_TOKENS,
            timeout=300.0, return_meta=True)
    except Exception as exc:                                   # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    payload = wizard.parse_json_response(content)
    raw = str(content or "")
    info = {
        "prompt_tokens": (meta or {}).get("prompt_tokens"),
        "completion_tokens": (meta or {}).get("completion_tokens"),
        "finish": (meta or {}).get("finish_reason"),
        "raw_chars": len(raw),
        "unbalanced": raw.count("{") > raw.count("}"),
    }
    # ⚠️ `parse_json_response` 解析失败返回的是 **`{}`**，不是 None。
    #    写成 `if payload is None` 就永远不会触发，一次截断的响应会
    #    一路走到 ingest 产出 0 发现——**与「材料里没东西」外观相同**。
    #    这是 BC-74 的原话，而我在同一天写的这个脚本里又犯了一次。
    if not payload:
        info["error"] = (
            f"解析失败｜finish={info['finish']}｜{len(raw)} 字"
            f"｜completion_tokens={info['completion_tokens']}"
            f"｜括号{'不平' if info['unbalanced'] else '配平'}")
        return None, info
    return payload, info


def load_field_checks(case: str) -> List[Dict[str, Any]]:
    """从已跑完的观察轮里取一份 A 层核查结果，供跨层判定使用。"""
    root = BACKEND / "eval" / "runs" / "stage2_b2"
    for path in sorted(root.glob(f"*{case}*/result.json")):
        checks = (json.loads(path.read_text(encoding="utf-8"))
                  .get("final_event") or {}).get("field_checks") or []
        if checks:
            return checks
    return []


async def run(case: str, limits: List[int], repeats: int,
              max_findings: int) -> int:
    from run_stage2_observation import SUBJECTS, _resolve
    from service import investigation_layer as inv
    from service.deep_research_v2.agents.wizard import CodeWizard
    from service.cross_layer_verdict import judge_findings

    # ⚠️ 不放开发现数上限，这次扫描测的就是那个上限，不是语料量。
    #
    #    `MAX_FINDINGS = 20` 在三处生效（读取上限、准入上限、报告渲染），
    #    而批次 2 的 case01 已经有三轮**正好停在 20**——它早就饱和了。
    #    保持 20 去扫条数，各档都会顶在 20，扫出一条假的平线，
    #    然后得出「多送材料没用」这个错误结论。
    #
    #    所以扫描期间把它放开，让语料量成为唯一的自变量。
    #    这同时意味着：**生产要调条数上界，就必须一起调这个数**。
    if max_findings != inv.MAX_FINDINGS:
        print(f"⚠️ 发现数上限 {inv.MAX_FINDINGS} → {max_findings}"
              f"（否则各档都会顶在上限，测的是上限不是语料）")
        inv.MAX_FINDINGS = max_findings

    # ⚠️ 放开条数就必须放开输出预算——**这两个数是一对**。
    #
    #    第一次跑扫描时只把 MAX_FINDINGS 提到 60，`INVESTIGATION_MAX_TOKENS`
    #    还是 8000。模型照着写 60 条，输出被截断，JSON 解析失败，
    #    三档全部报 0 发现。**实际生效的上限变成了没动的那一个。**
    #
    #    输出侧实测 2.8 字/token，60 条 finding 约 9,000–12,000 字
    #    ≈ 4,300 token，加上推理模型的思考链，8000 明显不够。
    #    deepseek-v4-flash 的输出上限是 65,536，取 32,000 有充裕余量。
    #
    #    生产上同理：调 CORPUS_LIMIT 与 MAX_FINDINGS 时必须一起调它。
    need_output = max(CodeWizard.INVESTIGATION_MAX_TOKENS, max_findings * 400)
    if need_output != CodeWizard.INVESTIGATION_MAX_TOKENS:
        print(f"⚠️ 输出预算 {CodeWizard.INVESTIGATION_MAX_TOKENS} → {need_output}"
              f"（发现数上限提了，输出不跟着提就会被截断，"
              f"实际生效的上限变成没动的那一个）")
        CodeWizard.INVESTIGATION_MAX_TOKENS = need_output

    entry = SUBJECTS[case]
    params = _resolve(entry)
    batches = await _retrieve_once(params["scope"])
    retrieved = sum(len(r) for _, r in batches)
    print(f"主体：{params['subject_name']}")
    print(f"八章共检索到 {retrieved} 条（各档共用同一批结果）")

    checks = load_field_checks(case)
    print(f"跨层判定使用已有的 {len(checks)} 项 A 层核查结果"
          if checks else "⚠️ 找不到 field_checks，本次不算挑战数")
    print()

    wizard = CodeWizard(
        llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=os.getenv("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        # ⚠️ 必须显式传生产的模型。不传就撞上默认值 "qwen-max"，
        #    而那正是 BC-78 的成因——探针与生产跑的不是同一个模型。
        model=__import__("config.llm_config", fromlist=["x"])
        .get_agent_model("wizard"))
    print(f"模型：{wizard.model}（与生产一致）")
    print()

    rows: List[Dict[str, Any]] = []
    for limit in limits:
        sources, shape = build_corpus(batches, limit)
        print(f"── 条数上界 {limit}：留存 {len(sources)} 条 / {shape['chars']:,} 字"
              f"｜覆盖 {shape['sections_covered']}/8 章 {shape['per_section']}")
        for i in range(1, repeats + 1):
            payload, meta = await extract(
                wizard, sources, params["subject_name"], params["as_of"])
            if payload is None:
                print(f"   #{i} ⛔ {meta.get('error')}")
                rows.append({"limit": limit, "ok": False,
                             "why": str(meta.get("error"))[:60]})
                continue
            state = {"company_name": params["subject_name"],
                     "as_of": params["as_of"], "investigation":
                         inv.empty_investigation()}
            stats = inv.ingest_exploratory_payload(state, payload, sources)
            box = inv.get_investigation(state)
            findings = box.get("findings") or []
            verdict = judge_findings(findings, checks) if checks else {}
            ch = (verdict.get("counts") or {}).get("challenges", 0)
            dims = len({f.get("dimension") for f in findings})
            rows.append({
                "limit": limit, "ok": True, "findings": len(findings),
                "rejected": stats.get("rejected", 0), "challenges": ch,
                "dims": dims, "chars": shape["chars"],
                "sections": shape["sections_covered"],
                "prompt_tokens": meta.get("prompt_tokens") or 0,
                "completion_tokens": meta.get("completion_tokens") or 0,
            })
            print(f"   #{i} 发现 {len(findings):>3}｜拒 {stats.get('rejected', 0):>2}"
                  f"｜挑战 {ch}｜维度 {dims}｜"
                  f"prompt_tokens {meta.get('prompt_tokens'):,}")
        print()

    print("## 汇总")
    print()
    print("| 条数上界 | 覆盖章节 | 语料字数 | prompt_tokens | 发现(中位) | "
          "挑战(合计) | 每千 token 发现数 |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    base_pt = None
    for limit in limits:
        got = [r for r in rows if r["limit"] == limit and r["ok"]]
        bad = [r for r in rows if r["limit"] == limit and not r["ok"]]
        if not got:
            why = bad[0].get("why", "?") if bad else "?"
            print(f"| {limit} | — | — | — | **全部失败**：{why} | — | — |")
            continue
        pt = statistics.median(r["prompt_tokens"] for r in got)
        med = statistics.median(r["findings"] for r in got)
        base_pt = base_pt or pt
        per_k = med / (pt / 1000) if pt else 0
        print(f"| {limit} | {got[0]['sections']}/8 | {got[0]['chars']:,} | "
              f"{pt:,.0f} | {med:.0f} | "
              f"{sum(r['challenges'] for r in got)} | **{per_k:.2f}** |")
    print()
    print("> 「每千 token 发现数」是**边际收益**的粗代理：它下降说明"
          "多送的材料没有带来同比例的产出。它不衡量发现的**质量**——"
          "那要靠挑战数与维度覆盖，两者都列在左边。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="case01")
    parser.add_argument("--limits", type=int, nargs="+", default=[40, 80, 120])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-findings", type=int, default=60,
                        help="扫描期间放开发现数上限；保持 20 会测成上限本身")
    args = parser.parse_args()
    return asyncio.run(run(args.case, args.limits, args.repeats,
                           args.max_findings))


if __name__ == "__main__":
    raise SystemExit(main())
