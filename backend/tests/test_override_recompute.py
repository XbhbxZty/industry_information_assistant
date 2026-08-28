# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
人工覆盖等级后，额度必须跟着重算（BC-70）

## 这一轮在钉什么

BC-69 修完后跑端到端验收，8 项里 7 项通过，第 8 项失败——**而它抓到的是
另一个真缺陷**：复核人把中风险覆盖为低风险，报告里等级写低风险，
额度却仍是按中风险算出来的 144 万（低风险应为 720 万，**差 5 倍**）。

原实现：

    elif override and override != engine_level:
        out["credit_advice"] = _advice(out["level"])   # 只换了话术

`credit_advice` 是一句话，`credit_recommendation` 才是带金额的测算。
只更新前者，报告里就出现"等级低风险、额度按中风险"的内部矛盾。

BC-49 修的是"额度要在**闸门**之后算"；人工覆盖是另一条会改变等级的路径，
当时没被覆盖到——**同一条纪律的又一个入口**（BC-19→BC-31→BC-51 的老形态）。

## 附带一条方法论

这个缺陷是被 BC-69 修复时**顺手加的 `based_on_level` 字段**在第一次运行
就照出来的。那个字段的初衷只是"让额度是否跟着等级重算变得可观测"
（BC-63「先补仪表再动手」）。仪表装上，第一轮就照出了真问题。

运行：cd backend && python -m pytest tests/test_override_recompute.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service import risk_scorecard as rs  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, find_company, profile_to_facts,
)
from service.credit_advice import recommend_credit  # noqa: E402


def _assessed():
    """机器判定：中风险 + 按中风险算出的额度。"""
    company = find_company("对云岭恒晟精密机械有限公司开展应收账款保理尽职调查")
    assert company is not None, "测试档案 MOCK-002 缺失"
    checks = build_field_checks(scenario="factoring")
    fill_field_checks(company, profile_to_facts(company), checks)
    result = rs.score(company, checks, compute_completeness(checks))
    result["credit_recommendation"] = recommend_credit(company, checks, result)
    return company, checks, result


def _review(result, checks=None, view=None, **decision):
    payload = {"approved": True, "reviewer": "复核人甲", "comment": "已确认"}
    payload.update(decision)
    return rs.apply_human_review(dict(result), payload,
                                 scoring_view=view, field_checks=checks)


# ------------------------------------------------- 一、覆盖后必须重算

def test_amount_follows_the_overridden_level():
    """本条的直接回归：等级被人工调低，额度必须跟着调高。"""
    company, checks, result = _assessed()
    assert result["level"] == "中风险", "前提：机器判定为中风险"
    before = result["credit_recommendation"]["suggested_amount"]

    out = _review(result, checks, company, override_level="低风险")
    after = out["credit_recommendation"]["suggested_amount"]

    assert out["level"] == "低风险"
    assert after != before, "额度没有跟着等级重算——这正是 BC-70"
    assert after > before, "低风险的额度应高于中风险"


def test_based_on_level_matches_the_final_level():
    """额度载明的依据等级必须等于最终等级。

    这是 BC-69 修复时加的可观测字段；两者不等即为内部矛盾。
    """
    company, checks, result = _assessed()
    out = _review(result, checks, company, override_level="低风险")
    assert out["credit_recommendation"]["based_on_level"] == out["level"]


def test_recompute_uses_the_scoring_view_not_the_raw_profile():
    """必须用与初次评级同源的评分视图重算。

    用原始档案会丢掉结构化适配器查到的担保，重算出来的数字与初次评级
    不同源，等于制造第二处口径（BC-31/BC-52 同形）。
    """
    import inspect
    src = inspect.getsource(rs._recompute_credit_after_override)
    assert "scoring_view" in src
    assert "recommend_credit(scoring_view" in src, "重算必须喂 scoring_view"


# ------------------------------- 二、拿不到依据时，作废而不是留旧数字

def test_missing_view_voids_the_amount_instead_of_keeping_a_stale_one():
    """旧检查点没有 scoring_view 时，不得把旧金额留在新等级旁边。

    留着就是一个会被直接采信的错误数字——比作废危险得多。
    """
    company, checks, result = _assessed()
    stale = result["credit_recommendation"]["suggested_amount"]

    out = _review(result, override_level="低风险")          # 不传 view/checks
    advice = out["credit_recommendation"]

    assert advice["recommendable"] is False
    assert advice.get("suggested_amount") is None, "无法重算时不得给出金额"
    assert advice["based_on_level"] == "低风险"
    assert advice["superseded_amount"] == stale, "作废的旧金额仍要留痕"
    assert "无法按新等级重算" in advice["reason"]


def test_superseded_amount_is_recorded_for_audit():
    """覆盖前的金额必须留痕。

    等级调低会让额度上浮，这是一次实质的授信放大，
    必须能回答"放大了多少、谁批的"。
    """
    company, checks, result = _assessed()
    before = result["credit_recommendation"]["suggested_amount"]
    out = _review(result, checks, company, override_level="低风险")
    advice = out["credit_recommendation"]

    assert advice["superseded_amount"] == before
    assert advice["superseded_level"] == "中风险"
    assert "复核人甲" in advice["override_note"]
    assert str(int(before)) in advice["override_note"], "留痕里要写明原额度"


# ------------------------------------------------- 三、互为反面的用例

def test_no_override_leaves_the_amount_untouched():
    """没有覆盖等级就不该动额度——重算只在等级变化时发生。"""
    company, checks, result = _assessed()
    before = dict(result["credit_recommendation"])
    out = _review(result, checks, company)                  # 不传 override_level
    assert out["credit_recommendation"]["suggested_amount"] == before["suggested_amount"]
    assert "superseded_amount" not in out["credit_recommendation"], \
        "未覆盖时不应产生覆盖留痕"


def test_rejected_review_clears_the_amount_entirely():
    """复核不通过时不得留下任何金额。

    与 `unratable()` 同一原则：给出一个有数字的建议，读者就会当它是结论。
    """
    company, checks, result = _assessed()
    out = rs.apply_human_review(
        dict(result),
        {"approved": False, "reviewer": "复核人甲", "comment": "材料不足"},
        scoring_view=company, field_checks=checks,
    )
    assert out["credit_recommendation"] is None
    assert "不得出具授信建议" in out["credit_advice"]


def test_override_to_high_risk_can_also_lower_the_amount():
    """覆盖是双向的：调高风险等级同样要重算，且方向相反。"""
    company, checks, result = _assessed()
    before = result["credit_recommendation"]["suggested_amount"]
    out = _review(result, checks, company, override_level="高风险")
    advice = out["credit_recommendation"]
    assert out["level"] == "高风险"
    assert advice["recommendable"] is False or advice["suggested_amount"] < before


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn(); print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {name}: {str(e)[:170]}")
        except Exception as e:
            failed += 1; print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
