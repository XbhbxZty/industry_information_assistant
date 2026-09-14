"""Offline, reproducible control-flow comparison for the experimental investigator.

This does not claim model quality: the chooser is scripted because the live LLM
may be unavailable. It verifies what differs when both strategies see the same
fixed material, and emits an auditable JSON transcript to stdout.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.investigator import InvestigationBudget, investigate


QUESTION = "分析经营现金流下降原因，区分回款恶化与备货造成的短期占款，并列出仍需补充的材料"
DOCUMENTS = {
    "经营说明": "2025 年营业收入增长 20%，经营现金流同比下降。管理层解释为提前备货，尚需核对营运资金变动。",
    "营运资金附注": "2025 年应收账款增长 45%，一年以上账龄占比上升。存货增长 5%，预付款增长 3%。应收账款变化需结合客户账期和期后回款进一步核查。",
    "期后说明": "截至 2026 年 2 月，部分主要客户已回款，但未提供逐笔回款明细和银行流水，无法确定其覆盖全部逾期应收账款。",
}


@dataclass
class Run:
    strategy: str
    actions: list[dict]
    findings: list[dict]
    missing_materials: list[str]
    elapsed_ms: float


def standard_workflow() -> Run:
    """Representative fixed-plan baseline: retrieve each planned topic once."""
    started = perf_counter()
    actions, findings = [], []
    for topic, title in (("经营现金流", "经营说明"), ("营运资金", "营运资金附注"),
                         ("期后事项", "期后说明")):
        actions.append({"action": "search_local", "query": topic, "selected": title})
        findings.append({"source": title, "quote": DOCUMENTS[title]})
    return Run("workflow", actions, findings, ["补充回款证明"],
               round((perf_counter() - started) * 1000, 2))


async def agent_workflow() -> Run:
    """Run the production bounded loop against an in-memory fixed corpus."""
    started = perf_counter()
    executed, read = [], set()
    notebook: dict = {}

    async def choose(prompt, context):
        if "最多提出两个" in prompt:
            return {"questions": []}
        observation = context.get("observation", {})
        if not executed:
            return {"action": "search_local", "arguments": {"query": "经营现金流下降 管理层解释"},
                    "reason": "先定位异常及管理层解释", "questions": ["现金流下降由回款还是备货造成？"]}
        if observation.get("sources") and "经营说明" not in read:
            return {"action": "read_source", "arguments": {"source_id": "经营说明"},
                    "reason": "阅读解释原文", "questions": ["备货解释是否与营运资金变动一致？"]}
        if "经营说明" in read and "营运资金附注" not in read:
            if not any(a.get("query") == "应收账款 账龄 存货 预付款" for a in executed):
                return {"action": "search_local", "arguments": {"query": "应收账款 账龄 存货 预付款"},
                        "reason": "用应收与存货变动检验备货解释", "questions": ["应收增长是否比存货更能解释占款？"]}
            return {"action": "read_source", "arguments": {"source_id": "营运资金附注"},
                    "reason": "比较回款恶化与备货两种解释", "questions": ["应收增长是否已在期后回收？"]}
        if "营运资金附注" in read and "期后说明" not in read:
            if not any(a.get("query") == "期后回款 银行流水" for a in executed):
                return {"action": "search_local", "arguments": {"query": "期后回款 银行流水"},
                        "reason": "验证应收是否实际回款", "questions": ["回款覆盖多少逾期应收？"]}
            return {"action": "read_source", "arguments": {"source_id": "期后说明"},
                    "reason": "核对缓释信息及覆盖限制", "questions": ["逐笔回款能否覆盖全部逾期应收？"]}
        if len(notebook.get("findings", [])) < 3:
            title = ("经营说明", "营运资金附注", "期后说明")[len(notebook.get("findings", []))]
            kinds = {"经营说明": "counter", "营运资金附注": "support", "期后说明": "gap"}
            claims = {
                "经营说明": "管理层提出提前备货这一替代解释，但仍需数据验证",
                "营运资金附注": "应收及账龄增幅高于存货和预付款，回款因素更值得追查",
                "期后说明": "部分回款提供缓释信息，但覆盖范围因缺少逐笔资料无法确定",
            }
            return {"action": "record_finding", "arguments": {"source_id": title,
                    "claim": claims[title], "quote": DOCUMENTS[title], "kind": kinds[title]},
                    "reason": "保存可追溯发现", "questions": context["questions"]}
        return {"action": "finish", "arguments": {
                "summary": "应收账款与长账龄增长比存货变化更显著；备货解释证据不足。部分期后回款构成缓释，但覆盖范围未证实。",
                "missing_materials": ["逐笔期后回款明细及银行流水，用于核对其对逾期应收账款的覆盖范围"]},
                "reason": "现有工具无法补齐逐笔回款证明", "questions": []}

    async def execute(action, args):
        executed.append({"action": action, **args})
        if action == "search_local":
            query = args["query"]
            if "应收" in query:
                titles = ["营运资金附注"]
            elif "期后" in query or "流水" in query:
                titles = ["期后说明"]
            else:
                titles = ["经营说明"]
            return {"ok": True, "progress": True, "sources": titles}
        if action == "read_source":
            title = args["source_id"]
            read.add(title)
            return {"ok": True, "progress": True, "source_id": title, "text": DOCUMENTS[title]}
        title = args["source_id"]
        assert title in read and args["quote"] in DOCUMENTS[title]
        finding = {"source": title, "claim": args["claim"], "quote": args["quote"], "kind": args["kind"]}
        notebook.setdefault("findings", []).append(finding)
        return {"ok": True, "progress": True, "finding": finding}

    await investigate(brief={"question": QUESTION}, tools={
        "search_local": "检索", "read_source": "阅读", "record_finding": "记录"},
        choose=choose, execute=execute, notebook=notebook,
        budget=InvestigationBudget(max_steps=12, max_seconds=10))
    return Run("agent", executed, notebook["findings"], notebook.get("missing_materials", []),
               round((perf_counter() - started) * 1000, 2))


def score(run: Run) -> dict:
    """Score the Agent control-flow contract, not overall report quality."""
    text = json.dumps({"actions": run.actions, "findings": run.findings,
                       "missing": run.missing_materials}, ensure_ascii=False)
    checks = {
        "adaptive_receivable_followup": "应收账款 账龄" in text,
        "tests_inventory_explanation": "检验备货解释" in text or "存货和预付款" in text,
        "keeps_mitigation_and_limit": "部分回款" in text and "覆盖范围" in text,
        "specific_missing_material": "逐笔期后回款明细" in text and "银行流水" in text,
        "all_quotes_traceable": all(f.get("quote") in DOCUMENTS.get(f.get("source"), "") for f in run.findings),
    }
    return {"metric": "agent_control_flow_contract", "passed": sum(checks.values()),
            "total": len(checks), "checks": checks}


async def main():
    runs = [standard_workflow(), await agent_workflow()]
    print(json.dumps({"test_type": "offline_scripted_control_flow", "question": QUESTION,
                      "documents": DOCUMENTS, "runs": [
                          {**run.__dict__, "score": score(run)} for run in runs],
                      "limitations": ["chooser is scripted; no live LLM quality claim",
                                      "in-memory retrieval; no Milvus recall claim"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
