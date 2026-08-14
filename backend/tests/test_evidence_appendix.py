# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
证据溯源附录测试（v0.7-A）

## 这一轮在钉什么

迁移计划里排第一条的业务约束是「报告中每个结论必须可溯源——出坏账要追责」。
v0.6a 花三轮把溯源建起来了，但那些信息**一个字都没进过报告**：
撰写提示词要求"在句末标注来源与日期"，而传给模型的清单里根本没有这两项——
在一个以反幻觉为目的的系统里，让模型标注它拿不到的东西等于邀请它编造（BC-48）。

断言分两层：

1. **溯源信息确实到达了撰写环节与报告正文**
2. **附录是代码收口的**：模型改写/删除/复制，都由代码重建为唯一权威版本

运行：cd backend && python tests/test_evidence_appendix.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service.company_profile import fill_field_checks, profile_to_facts  # noqa: E402
from service.datasource import apply_all  # noqa: E402
from service.evidence_appendix import (  # noqa: E402
    APPENDIX_END, APPENDIX_MARKER, canonicalize_appendix, excise_appendix,
    format_provenance, render_appendix, source_label,
)
from service.risk_scorecard import RISK_BLOCK_MARKER, render_markdown, score  # noqa: E402
from service.deep_research_v2.agents.writer import LeadWriter  # noqa: E402

_EVAL = os.path.join(os.path.dirname(__file__), "..", "app", "data", "companies_eval.json")


def _companies():
    with open(_EVAL, encoding="utf-8") as f:
        return {c["company_id"]: c for c in json.load(f)["companies"]}


def _run(cid="EVAL-003"):
    """EVAL-003 同时有已核实项、适配器证据、和司法源故障造成的缺口"""
    c = _companies()[cid]
    checks = build_field_checks(checked_at="2026-08-13T00:00:00")
    fill_field_checks(c, profile_to_facts(c), checks)
    store = {}
    apply_all(c, checks, store)
    return c, checks, store, compute_completeness(checks)


def _pick(checks, fid):
    return next(x for x in checks if x["field_id"] == fid)


def _writer():
    return LeadWriter("sk-test", "http://localhost:1", "test-model")


# ---------------------------------------------------------------- 溯源内容

def test_每条已核实结论都带来源与取证时间():
    """出坏账翻开报告，必须看得到「这条结论是谁在什么时候查到的」"""
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    for c in checks:
        if c.get("status") != "verified":
            continue
        assert c["field_name"] in block, f"{c['field_name']} 未出现在附录"
    assert "初始企业档案" in block and "关联关系登记库" in block
    assert "2026-08-11" in block, "适配器证据的取证时间必须可见"


def test_内部标识翻译为业务可读名称():
    """报告读者是风控与信贷评审，relation_registry 这种内部标识对他们没有意义"""
    assert source_label("relation_registry") == "关联关系登记库"
    assert source_label("graph_analysis") == "关联关系图谱推导"
    assert source_label("initial_profile") == "初始企业档案"
    assert source_label(None) == "来源未标注"
    assert source_label("some_new_source") == "some_new_source", "未登记的标识原样透出，不静默丢弃"


def test_取证时间缺失时如实标注而非留空():
    """留空会让模型自行补一个日期——BC-48 的成因之一"""
    c = {"field_name": "x", "source_adapter": "judicial", "retrieved_at": ""}
    assert "未声明" in format_provenance(c)
    assert "来源：司法公开信息" in format_provenance(c)


def test_未声明取证时间的项被单独点名():
    _, checks, store, comp = _run()
    _pick(checks, "registration")["retrieved_at"] = ""
    block = render_appendix(checks, store, comp)
    assert "未声明取证时间" in block
    assert "工商登记基本信息" in block.split("未声明取证时间")[1]


def test_信息缺口与证据清单分开列出():
    """
    两者要求读者做的事完全不同：前者是可追溯的依据，后者是待补的工作。
    混在一张表里会让"未核实"看起来像一条记录。
    """
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    assert "尚未核实的项" in block
    assert "不得解读为「不存在」或「无记录」" in block
    gaps = block.split("尚未核实的项")[1]
    for fid in ("litigation", "enforcement", "dishonesty"):
        assert _pick(checks, fid)["field_name"] in gaps


def test_未核实项不得出现在证据清单里():
    """证据清单只列主张了事实的项；unverified 不主张任何事实"""
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    evidence_part = block.split("尚未核实的项")[0]
    for c in checks:
        if c.get("status") == "unverified":
            assert c["field_name"] not in evidence_part, \
                f"{c['field_name']} 未核实却出现在证据清单"


def test_冲突项并列披露各来源取值():
    _, checks, store, comp = _run("EVAL-005")
    reg = _pick(checks, "registration")
    assert reg["status"] == "conflicting", "前提：EVAL-005 存在多源冲突"
    block = render_appendix(checks, store, comp)
    assert "存在多源冲突" in block
    for d in reg["conflict_detail"]:
        assert str(d["source"]) in block


def test_证据编号可回查():
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    ids = [e for e in store]
    assert ids, "前提：适配器产生了证据"
    assert any(i in block for i in ids), "附录须给出证据编号供审计回查"


def test_核实率一并呈现():
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    assert "必查项核实率" in block and "73%" in block


def test_无清单时不产出附录():
    assert render_appendix([], {}, {}) == ""


# ---------------------------------------------------------------- 代码收口

def test_模型删除附录时由代码补回():
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    report = "# 尽调报告\n\n正文内容"
    out = canonicalize_appendix(report, block)
    assert APPENDIX_MARKER in out and APPENDIX_END in out
    assert "正文内容" in out


def test_模型改写附录时被替换为权威版本():
    """一张被模型改过的证据清单比没有更危险——读者会以为它是原始记录"""
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    tampered = (f"# 报告\n\n正文\n\n**{APPENDIX_MARKER}，不得由撰写环节改写）**\n\n"
                f"| 核查项 | 结论 |\n|---|---|\n| 全部 | 均已核实 |\n{APPENDIX_END}")
    out = canonicalize_appendix(tampered, block)
    assert "均已核实" not in out, "模型版本必须被切除"
    assert "尚未核实的项" in out, "权威版本必须放回"


def test_模型复制出多份附录时只保留一份():
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    dup = f"# 报告\n\n正文\n\n{block}\n\n中间段落\n\n{block}"
    out = canonicalize_appendix(dup, block)
    assert out.count(APPENDIX_MARKER) == 1


def test_附录固定置于报告末尾():
    """它是审计材料，不参与阅读流"""
    _, checks, store, comp = _run()
    block = render_appendix(checks, store, comp)
    out = canonicalize_appendix("# 报告\n\n正文\n\n## 结论\n\n结论段", block)
    assert out.index("结论段") < out.index(APPENDIX_MARKER)
    assert out.rstrip().endswith(APPENDIX_END)


def test_评级块与附录共存且互不吞噬():
    """
    两者都用"切除后重建"的收口方式，作用在同一段文本上。
    任一方的切除逻辑误伤对方，都会让另一块悄悄消失。
    """
    c, checks, store, comp = _run()
    writer = _writer()
    state = {"final_report": "# 尽调报告\n\n正文内容", "field_checks": checks,
             "evidence_store": store, "completeness": comp,
             "risk_assessment": score(c, checks, comp)}
    writer._ensure_risk_block(state)
    writer._ensure_evidence_appendix(state)
    report = state["final_report"]
    assert RISK_BLOCK_MARKER in report and APPENDIX_MARKER in report
    assert "正文内容" in report

    # 再跑一次（模拟修订后重新收口）：两块都不得重复、不得丢失
    writer._ensure_risk_block(state)
    writer._ensure_evidence_appendix(state)
    again = state["final_report"]
    assert again.count(RISK_BLOCK_MARKER) == 1, "评级块被附录逻辑复制或吞掉"
    assert again.count(APPENDIX_MARKER) == 1, "附录被评级块逻辑复制或吞掉"
    assert "正文内容" in again


def test_修订路径同样重建附录():
    """
    ⭐ BC-50：这条断言是拿一次真实端到端换来的。

    整合与修订都是 LLM 步骤、都会重写全文，因此每条路径都要重跑全部收口。
    此前 `_synthesize_report` 调了评级块与附录，`_revise_report` 只调了评级块——
    **只要 Critic 要求修订一次，证据溯源附录就从最终报告里消失。**

    真实运行验证时发现：复核卡点上的报告有附录（8416 字），
    终局报告没有（6651 字）。
    """
    c, checks, store, comp = _run()
    writer = _writer()
    state = {"final_report": "# 尽调报告\n\n正文", "field_checks": checks,
             "evidence_store": store, "completeness": comp,
             "risk_assessment": score(c, checks, comp)}
    writer._finalize_report(state)
    assert APPENDIX_MARKER in state["final_report"]

    # 模拟 LLM 修订：全文被重写，两个区块都没了
    state["final_report"] = "# 尽调报告（修订版）\n\n修订后的正文，模型没有保留任何区块"
    writer._finalize_report(state)
    assert RISK_BLOCK_MARKER in state["final_report"], "修订后评级块必须补回"
    assert APPENDIX_MARKER in state["final_report"], "修订后溯源附录同样必须补回"
    assert "修订后的正文" in state["final_report"], "正文不得被收口逻辑吃掉"


def test_收口只有一个入口():
    """
    新增收口区块时只该改一个地方。"记得同步几个调用点"这种要求迟早失效——
    上一次就失效了（BC-50）。
    """
    import inspect
    from service.deep_research_v2.agents import writer as wmod

    src = inspect.getsource(wmod.LeadWriter)
    # 除 _finalize_report 自身外，不应再有别处直接调用两个 _ensure_*
    for name in ("_ensure_risk_block", "_ensure_evidence_appendix"):
        calls = src.count(f"self.{name}(state)")
        assert calls == 1, (
            f"{name} 被直接调用 {calls} 次；应只在 _finalize_report 里调用一次，"
            f"其余路径统一走收口入口")


def test_非尽调流程不强加附录():
    writer = _writer()
    state = {"final_report": "# 行业研究报告\n\n正文", "field_checks": [],
             "evidence_store": {}, "completeness": {}}
    assert writer._ensure_evidence_appendix(state) is False
    assert APPENDIX_MARKER not in state["final_report"]


def test_报告为空时不产出附录():
    _, checks, store, comp = _run()
    state = {"final_report": "", "field_checks": checks,
             "evidence_store": store, "completeness": comp}
    assert _writer()._ensure_evidence_appendix(state) is False


# ---------------------------------------------------------------- 撰写入参

def test_清单传给模型时带上溯源():
    """
    提示词要求"在句末标注来源与日期"。此前清单里没有这两项，
    模型只能靠编造来遵守——这正是 BC-48。
    """
    _, checks, store, comp = _run()
    state = {"field_checks": checks}
    rendered = LeadWriter._format_field_checks(state, "sec_6")   # 关联关系与对外担保
    assert "来源：" in rendered and "取证时间：" in rendered
    assert "关联关系登记库" in rendered or "关联关系图谱推导" in rendered


def test_未核实项不带溯源():
    """unverified 不主张事实，给它标来源会让「有来源」失去含义"""
    _, checks, store, comp = _run()
    rendered = LeadWriter._format_field_checks({"field_checks": checks}, "sec_5")  # 司法
    assert "**未核实**" in rendered
    for line in rendered.splitlines():
        if "**未核实**" in line:
            assert "取证时间" not in line


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {str(e)[:170]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
