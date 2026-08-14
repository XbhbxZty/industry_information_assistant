# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
担保圈图谱推导测试（v0.7-C）—— BC-18 的解除条件

## 这一轮在钉什么

BC-18 的结论是：系统若确实无法核查某个必查项，它本来就不该出具低风险结论；
「低风险不可达」不是缺陷，是正确行为。当时刻意拒绝了两条捷径——
降为选查项、把它排除出维度分母——并把解除条件写死为**建成图谱推导能力**。

本文件断言那个能力真的建起来了，以及**它没有顺手放宽任何安全约束**。

## 最关键的一条：未发现 ≠ 不存在

图谱推导的"未发现担保圈"只和图的完整性一样强。若担保对手方根本不在
关联关系库里，我们无从知道它是否反向担保——此时**不得**表述为"未发现"，
那是把"查不到"说成"没问题"。这条有独立断言。

运行：cd backend && python tests/test_guarantee_graph.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import (  # noqa: E402
    CAPABILITY_GAP_IDS, build_field_checks, compute_completeness,
)
from service.company_profile import (  # noqa: E402
    fill_field_checks, profile_to_facts, replay_from_profile, verify_field_checks,
)
from service.datasource import apply_all  # noqa: E402
from service.datasource.mock.guarantee_circle import GuaranteeCircleAdapter  # noqa: E402
from service.guarantee_graph import (  # noqa: E402
    CIRCLE_CHAIN, CIRCLE_MUTUAL, GuaranteeGraph, detect_guarantee_circles,
    profile_records,
)
from service.risk_scorecard import PROFILE_BACKED_FIELDS, score  # noqa: E402
from service.verification import build_scoring_view  # noqa: E402

_EVAL = os.path.join(os.path.dirname(__file__), "..", "app", "data", "companies_eval.json")


def _companies():
    with open(_EVAL, encoding="utf-8") as f:
        return {c["company_id"]: c for c in json.load(f)["companies"]}


def _g(spec):
    """spec: {企业名: [(对手方, 金额), ...]}，所有出现的企业都视为已登记"""
    records = {}
    for i, (name, outs) in enumerate(spec.items()):
        records[f"CODE{i}"] = {
            "company_name": name,
            "guarantee": [{"beneficiary": b, "amount": a, "unit": "万元",
                           "guarantee_type": "连带责任保证"} for b, a in outs],
        }
    return GuaranteeGraph.from_records(records)


def _run(company):
    checks = build_field_checks(checked_at="2026-08-13T00:00:00")
    fill_field_checks(company, profile_to_facts(company), checks)
    store = {}
    apply_all(company, checks, store)
    return checks, store, compute_completeness(checks)


def _pick(checks, fid):
    return next(c for c in checks if c["field_id"] == fid)


# ---------------------------------------------------------------- 纯图算法

def test_无对外担保则确定性不涉入():
    """
    担保圈要求主体自身是担保人，没有出边就不可能成环。
    这个结论**不依赖图的完整性**——即便对手方数据全缺也成立。
    """
    r = detect_guarantee_circles("甲", _g({"甲": [], "乙": [("甲", 100)]}))
    assert not r.has_circle
    assert r.traversal_complete, "无出边时结论是确定性的，与对手方数据无关"
    assert "未发现" in r.describe()


def test_互保识别为2环():
    r = detect_guarantee_circles("甲", _g({"甲": [("乙", 500)], "乙": [("甲", 300)]}))
    assert len(r.circles) == 1
    assert r.circles[0]["kind"] == CIRCLE_MUTUAL
    assert r.circles[0]["path"] == ["甲", "乙"]
    assert r.circles[0]["total_amount"] == 800


def test_连环担保识别为3环及以上():
    r = detect_guarantee_circles("甲", _g({
        "甲": [("乙", 100)], "乙": [("丙", 200)], "丙": [("甲", 300)]}))
    assert len(r.circles) == 1
    assert r.circles[0]["kind"] == CIRCLE_CHAIN
    assert r.circles[0]["path"] == ["甲", "乙", "丙"]
    assert r.circles[0]["total_amount"] == 600


def test_不经过本主体的环不算本主体涉入():
    """乙丙互保与甲无关；把它算进甲的担保圈是无中生有"""
    r = detect_guarantee_circles("甲", _g({
        "甲": [("乙", 100)], "乙": [("丙", 200)], "丙": [("乙", 300)]}))
    assert not r.has_circle
    assert r.traversal_complete


def test_多条环全部列出():
    r = detect_guarantee_circles("甲", _g({
        "甲": [("乙", 100), ("丙", 100)], "乙": [("甲", 200)], "丙": [("甲", 300)]}))
    assert len(r.circles) == 2


# -------------------------------------------- ⭐ 未发现 ≠ 不存在

def test_对手方不在库中时不得判定未发现():
    """
    ⭐ 本轮最重要的断言。甲为乙担保，但乙根本不在关联关系库里——
    我们无从知道乙是否反向担保。此时下"未发现担保圈"的结论，
    就是把"查不到"说成"没问题"。
    """
    graph = GuaranteeGraph.from_records({
        "C1": {"company_name": "甲", "guarantee": [
            {"beneficiary": "乙", "amount": 500, "unit": "万元"}]}})
    r = detect_guarantee_circles("甲", graph)
    assert not r.has_circle
    assert not r.traversal_complete, "遍历未闭合"
    assert r.unknown_counterparties == ["乙"]
    assert r.describe() == "", "无法判定时不得给出任何结论文本"


def test_遍历不完整时适配器落成未查询而非已核实():
    """无法判定必须表现为信息缺口，不能是一条正面结论"""
    c = _companies()["EVAL-004"]           # 主体不在关联关系库中
    checks, store, _ = _run(c)
    gc = _pick(checks, "guarantee_circle")
    assert gc["status"] == "unverified"
    assert gc["value"] is None
    assert not any(e["field_id"] == "guarantee_circle" for e in store.values())


def test_发现环时不受未展开分支影响():
    """已经找到环就是确定性结论，不因为别的分支没走完而降级"""
    graph = GuaranteeGraph.from_records({
        "C1": {"company_name": "甲", "guarantee": [
            {"beneficiary": "乙", "amount": 100}, {"beneficiary": "丁", "amount": 50}]},
        "C2": {"company_name": "乙", "guarantee": [{"beneficiary": "甲", "amount": 200}]},
    })  # 丁 不在库中
    r = detect_guarantee_circles("甲", graph)
    assert r.has_circle
    assert r.traversal_complete, "环已确证，未展开的分支不影响该结论"


# ---------------------------------------------------------------- 适配器

def test_担保圈证据取值可被生产映射重放():
    """
    措辞必须与 `fill_field_checks` 逐字一致，否则 patch 一致性校验判为漂移。
    这条断言把两处措辞钉在一起，改一处就会红。
    """
    c = _companies()["EVAL-002"]
    checks, store, comp = _run(c)
    report = verify_field_checks(c, checks, store)
    assert report.ok, report.mismatches
    _, unmergeable = build_scoring_view(
        c, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
        profile_replay_fn=replay_from_profile)
    assert not unmergeable, unmergeable


def test_无环时用系统标准措辞():
    """自造一句更好听的措辞会与重放结果漂移（v0.7-B 踩过的坑）"""
    c = _companies()["EVAL-001"]
    checks, _, _ = _run(c)
    assert _pick(checks, "guarantee_circle")["value"] == "经查询，无相关记录"


def test_推导过程完整留痕供人工复核():
    """
    这个适配器不是查询而是推导，`raw` 必须能还原推导过程——
    尤其是**没能展开的分支**：复核人得看出这次推导有多完整。
    """
    c = _companies()["EVAL-002"]
    _, store, _ = _run(c)
    ev = next(e for e in store.values() if e["field_id"] == "guarantee_circle")
    raw = ev["raw"]
    for key in ("subject", "circles", "circle_detail", "traversal_complete",
                "unknown_counterparties", "depth_truncated", "visited_nodes",
                "knowledge_graph"):
        assert key in raw, f"推导记录缺少 {key}"
    assert raw["circle_detail"][0]["edges"], "必须能还原环上每一条担保边"


def test_导出知识图谱并标出环上节点():
    """知识图谱组件第一次有真实业务目的——此前只是通用实体图"""
    c = _companies()["EVAL-002"]
    _, store, _ = _run(c)
    kg = next(e for e in store.values()
              if e["field_id"] == "guarantee_circle")["raw"]["knowledge_graph"]
    assert kg["nodes"] and kg["edges"]
    assert any(n["is_subject"] for n in kg["nodes"])
    assert sum(1 for n in kg["nodes"] if n["on_circle"]) == 3, "3 环应有 3 个节点在环上"
    assert any(e["on_circle"] for e in kg["edges"])


# ------------------------------------------- ⭐ BC-18 解除，但不放宽安全

def test_能力缺失清单已清空():
    assert CAPABILITY_GAP_IDS == frozenset(), \
        f"担保圈能力已上线，不应再有 not_implemented 项：{CAPABILITY_GAP_IDS}"


def test_低风险首次可达且靠的是建能力():
    """
    ⭐ BC-18 的解除验收。EVAL-001 此前唯一的闸门是能力缺失，
    图谱推导上线后该闸门消失 → 低风险。

    **解除靠建能力，不靠调阈值**：断言阈值本身没被动过。
    """
    from service.risk_scorecard import MIN_CATEGORY_RATE, MIN_OVERALL_RATE
    assert (MIN_OVERALL_RATE, MIN_CATEGORY_RATE) == (0.60, 0.50), \
        "解除低风险不可达不得靠调阈值——这正是 BC-18 拒绝过的那条路"

    c = _companies()["EVAL-001"]
    checks, store, comp = _run(c)
    view, unmergeable = build_scoring_view(
        c, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
        profile_replay_fn=replay_from_profile)
    assert not unmergeable
    r = score(view, checks, comp)
    assert comp["verified_rate"] == 1.0, f"必查项应全部核实：{comp['unverified_fields']}"
    assert r["level"] == "低风险", f"实际 {r['level']}，闸门 {r['gates_applied']}"
    assert not r["gates_applied"], f"优质企业不应触发任何闸门：{r['gates_applied']}"


def test_担保圈检出必须加重风险而非放过():
    """新增负面证据只能让结论更保守——BC-31 的方向性要求"""
    c = _companies()["EVAL-002"]
    checks, store, comp = _run(c)
    view, _ = build_scoring_view(
        c, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
        profile_replay_fn=replay_from_profile)
    r = score(view, checks, comp)
    rules = [x for x in r["triggered_rules"] if x["field_id"] == "guarantee_circle"]
    assert rules and rules[0]["score"] > 0, f"检出连环担保必须扣分：{rules}"
    assert "担保圈" in rules[0]["detail"]
    assert all("未发现担保圈" not in x["detail"] for x in r["triggered_rules"])


def test_能力上线不得削弱任何企业的等级():
    """安全性不因"能查了"而下降：只有 EVAL-001 因闸门消失而下调，且它本就无不良"""
    expected = {"EVAL-001": "低风险", "EVAL-002": "拒绝", "EVAL-003": "中风险",
                "EVAL-004": "数据不足，无法评级", "EVAL-005": "高风险"}
    for cid, c in _companies().items():
        checks, store, comp = _run(c)
        view, _ = build_scoring_view(
            c, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
            profile_replay_fn=replay_from_profile)
        assert score(view, checks, comp)["level"] == expected[cid], cid


def test_profile_records投影结构供评分卡消费():
    r = detect_guarantee_circles("甲", _g({"甲": [("乙", 500)], "乙": [("甲", 300)]}))
    recs = profile_records(r)
    assert len(recs) == 1
    assert recs[0]["kind"] == CIRCLE_MUTUAL and recs[0]["hops"] == 2
    assert recs[0]["total_amount"] == 800


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
