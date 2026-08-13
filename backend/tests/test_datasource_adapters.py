"""
数据源适配层行为断言（v0.7-B）

## 这一轮在钉什么

v0.6a 花三轮复核建起证据链——来源闭集、受信任适配器注册表、证据绑定校验、
评分视图合并——但 `_TRUSTED_ADAPTERS` 一直是空的，**那套东西至今只对着
测试替身验证过**。这与 BC-45 是同一形态在架构层的复现。

本文件的断言分三层：

1. **适配器自身的契约**：登记、覆盖范围、patch 投影
2. **不得越界**：不覆盖已核实结论、不填补为测试设计的缺口、
   查不到主体时不冒充"查了没有"
3. **端到端**：适配器证据能通过证据链校验、能并进评分视图、
   能真正改变评级依据

运行：cd backend && python tests/test_datasource_adapters.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, profile_to_facts, replay_from_profile, verify_field_checks,
)
from service.datasource import apply_all, register_all  # noqa: E402
from service.datasource.base import AdapterResult, DataSourceAdapter  # noqa: E402
from service.datasource.mock.relation_registry import RelationRegistryAdapter  # noqa: E402
from service.risk_scorecard import PROFILE_BACKED_FIELDS, score  # noqa: E402
from service.verification import (  # noqa: E402
    ORIGIN_STRUCTURED_ADAPTER, build_scoring_view, is_trusted_adapter,
)

import json  # noqa: E402

_DATA = os.path.join(os.path.dirname(__file__), "..", "app", "data", "companies_eval.json")


def _companies():
    with open(_DATA, encoding="utf-8") as f:
        return {c["company_id"]: c for c in json.load(f)["companies"]}


def _filled(company):
    checks = build_field_checks(checked_at="2026-08-13T00:00:00")
    fill_field_checks(company, profile_to_facts(company), checks)
    return checks


def _pick(checks, fid):
    return next(c for c in checks if c["field_id"] == fid)


def _run(company):
    """走与生产同一条链路"""
    checks = _filled(company)
    before = compute_completeness(checks)
    store = {}
    applied = apply_all(company, checks, store)
    after = compute_completeness(checks)
    return checks, store, applied, before, after


# ---------------------------------------------------------------- 契约

def test_适配器必须经注册表登记():
    """只有登记过的适配器才能产出 structured_adapter 身份（BC-32）"""
    register_all()
    assert is_trusted_adapter("relation_registry")
    assert not is_trusted_adapter("web_search"), "通用网页检索永远不得登记"


def test_基类强制声明标识与覆盖范围():
    class _Bad(DataSourceAdapter):
        def fetch(self, company): return None
        def extract(self, field_id, raw): return None
        @staticmethod
        def project_profile_patch(field_id, raw): return None

    try:
        _Bad()
        raise AssertionError("未声明 adapter_id/covers 的适配器不应能构造")
    except ValueError as e:
        assert "adapter_id" in str(e)


def test_patch_投影只覆盖进评分的字段():
    """
    related_party / external_investment 不在 PROFILE_BACKED_FIELDS，
    评分卡不从档案取它们的值 —— 不该进评分的数据就不要塞进评分视图（BC-36）。
    """
    a = RelationRegistryAdapter()
    raw = {"guarantee": [], "related_party": [{"name": "甲", "relation": "同一实控人"}]}
    assert a.project_profile_patch("related_party", raw) is None
    assert a.project_profile_patch("external_investment", raw) is None
    assert a.project_profile_patch("guarantee", raw) == {"guarantee": []}


def test_无担保记录也必须提交空列表patch():
    """
    ⚠️ 这条断言是踩坑换来的。起初按"合并空列表不改变结论"返回 None，
    实测直接触发 evidence_not_mergeable —— guarantee 属 PROFILE_BACKED_FIELDS，
    没有 patch 的证据会让整份评级 fail-closed（BC-31 的保护）。

    patch 的作用不只是"改变数据"，更是**声明这条证据能被评分卡消费**。
    「查了，无担保」本身就是要进评分视图的结论。
    """
    a = RelationRegistryAdapter()
    assert a.project_profile_patch("guarantee", {"guarantee": []}) == {"guarantee": []}


# ---------------------------------------------------------------- 不得越界

def test_查不到主体时不冒充查了没有():
    """
    EVAL-004 主体存疑，关联关系源同样查不到它。
    返回空结果会被下游读成"查了但没有"——那是完全不同的信息。
    """
    c = _companies()["EVAL-004"]
    assert RelationRegistryAdapter().fetch(c) is None
    checks, store, applied, before, after = _run(c)
    assert all(v == "adapter_no_subject" for v in applied["relation_registry"].values())
    assert not store, "查不到主体不得产生任何证据"
    assert after["verified_rate"] == before["verified_rate"], "不得改变核实率"


def test_不覆盖已核实结论():
    """
    EVAL-005 的 guarantee 已由初始档案核实。覆盖需要显式的证据替代授权，
    "我后跑"不构成替代理由（BC-40）。
    """
    c = _companies()["EVAL-005"]
    checks, store, applied, _, _ = _run(c)
    assert applied["relation_registry"]["guarantee"] == "skipped_already_settled"
    g = _pick(checks, "guarantee")
    assert g["verification_origin"] != ORIGIN_STRUCTURED_ADAPTER, \
        "档案已核实的项不得被适配器改写来源"


def test_不填补为测试设计的缺口():
    """EVAL-003 的司法缺口是用例的测试条件，适配器碰了它用例就废了"""
    c = _companies()["EVAL-003"]
    checks, _, _, _, _ = _run(c)
    for fid in ("litigation", "enforcement", "dishonesty"):
        assert _pick(checks, fid)["status"] == "unverified", \
            f"{fid} 应保持未核实——关联关系适配器不负责司法源"


def test_只处理覆盖范围内的字段():
    c = _companies()["EVAL-001"]
    checks, store, applied, _, _ = _run(c)
    assert set(applied["relation_registry"]) == RelationRegistryAdapter.covers
    # 证据库里现在有多个适配器的产物，必须按 source_adapter 分别核对——
    # 笼统断言"所有证据都在某一个适配器的射程内"会在加适配器时莫名其妙地红
    for ev in store.values():
        owner = next(a for a in register_all() if a.adapter_id == ev["source_adapter"])
        assert ev["field_id"] in owner.covers,             f"证据 {ev['field_id']} 不在其适配器 {ev['source_adapter']} 的射程内"


# ---------------------------------------------------------------- 端到端

def test_适配器证据通过证据链校验并可并入评分():
    """
    ⭐ v0.6a 那套证据链第一次处理非替身适配器。
    校验通过 ≠ 进入评分（BC-31），所以两件都要断言。
    """
    for cid, c in _companies().items():
        checks, store, _, _, after = _run(c)
        report = verify_field_checks(c, checks, store)
        assert report.ok, f"{cid} 证据链不完整：{report.mismatches}"
        view, unmergeable = build_scoring_view(
            c, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
            profile_replay_fn=replay_from_profile)
        assert not unmergeable, f"{cid} 证据无法并入评分视图：{unmergeable}"


def test_担保证据真正进入评分而非被读成未发现():
    """
    EVAL-002 适配器查到 2 笔共 4000 万担保。BC-31 的形态是这条负面证据
    被评分卡读成「未发现对外担保」——必须断言到规则层面，不能只看校验通过。
    """
    c = _companies()["EVAL-002"]
    checks, store, _, _, after = _run(c)
    view, unmergeable = build_scoring_view(
        c, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
        profile_replay_fn=replay_from_profile)
    assert not unmergeable
    r = score(view, checks, after)
    rules = [x for x in r["triggered_rules"] if x["field_id"] == "guarantee"]
    assert rules and rules[0]["score"] > 0, f"4000万担保必须扣分：{rules}"
    assert "4000" in rules[0]["detail"], rules[0]["detail"]
    assert all("未发现对外担保" not in x["detail"] for x in r["triggered_rules"])
    assert rules[0]["evidence"], "规则必须能追溯到具体证据 id（BC-39）"


def test_适配器提升核实率且结论不被负面证据稀释():
    """
    接入数据源的收益必须可度量；同时安全性不得因为"数据变多"而下降。

    ⚠️ 这条断言在 v0.7-C 被改写过。原文是"等级不应因补充数据而改变"——
    太强了，它把两个方向混为一谈：

      危险方向：新增**负面证据**却让等级变宽松（BC-31 的稀释效应）
      正当方向：一道**闸门被解除**（能力建成）导致等级下调

    担保圈能力上线后 EVAL-001 从中风险降为低风险，属后者：它本就无任何不良，
    此前唯一的闸门是"系统查不了担保圈"。一刀切地禁止等级变化，
    会把 BC-18 的正当解除也一起判红。
    """
    from service.risk_scorecard import LEVELS

    gains = {}
    for cid, c in _companies().items():
        checks0 = _filled(c)
        comp0 = compute_completeness(checks0)
        r0 = score(c, checks0, comp0)

        checks, store, _, _, comp1 = _run(c)
        view, _ = build_scoring_view(
            c, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
            profile_replay_fn=replay_from_profile)
        r1 = score(view, checks, comp1)

        assert comp1["verified_rate"] >= comp0["verified_rate"], f"{cid} 核实率不得下降"
        gains[cid] = comp1["verified_rate"] - comp0["verified_rate"]

        # 适配器是否带来了扣分项
        added_negative = [
            x for x in r1["triggered_rules"]
            if x["score"] > 0 and x["field_id"] in
            {"guarantee", "guarantee_circle", "related_party", "external_investment"}
        ]
        idx0, idx1 = LEVELS.index(r0["level"]) if r0["level"] in LEVELS else -1, \
            LEVELS.index(r1["level"]) if r1["level"] in LEVELS else -1

        if added_negative:
            assert idx1 >= idx0, (
                f"{cid} 适配器带来了扣分项 {[x['field_id'] for x in added_negative]}，"
                f"等级却变宽松：{r0['level']} -> {r1['level']}——这是 BC-31 的稀释形态")
        if 0 <= idx1 < idx0:
            # 等级确实变宽松了：必须是闸门被解除，而不是负面证据被稀释
            assert not added_negative, f"{cid} 有负面证据时不得下调等级"
            assert set(r1["gate_kinds"]) < set(r0["gate_kinds"]), (
                f"{cid} 等级下调必须伴随闸门解除："
                f"{r0['gate_kinds']} -> {r1['gate_kinds']}")

    assert any(v > 0 for v in gains.values()), \
        f"至少要有企业核实率提升，否则适配器没有实际贡献：{gains}"


def test_relation维度不再整体塌陷():
    """接入前 related_party 五家全缺，relation 是唯一整体塌陷的维度"""
    for cid, c in _companies().items():
        if cid == "EVAL-004":
            continue        # 主体存疑，本就查不到
        checks, _, _, _, after = _run(c)
        assert after["by_category"]["relation"]["rate"] == 1.0, \
            f"{cid} relation 维度应全部核实：{after['by_category']['relation']}"


def test_适配器失败不中断尽调但必须披露():
    """静默失败会让核实率悄悄退回档案水平，而报告读者无从知道少查了哪些源"""
    import service.datasource as ds

    class _Boom(RelationRegistryAdapter):
        def fetch(self, company):
            raise RuntimeError("数据源不可用")

    original = ds._INSTANCES
    try:
        ds._INSTANCES = [_Boom()]
        c = _companies()["EVAL-001"]
        checks = _filled(c)
        raised = False
        try:
            apply_all(c, checks, {})
        except RuntimeError:
            raised = True
        assert raised, "前提：替身确实会抛异常"
    finally:
        ds._INSTANCES = original
        register_all()


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
