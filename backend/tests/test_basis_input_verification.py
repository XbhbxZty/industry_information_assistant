# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
额度口径只能用自己那个量的核实状态（BC-72）

## 这一轮在钉什么

`_bases()` 的说明写着「**只使用已核实的字段**——未核实的数字不得参与额度
计算」。但净资产法这么写：

    if _verified(field_checks, "debt_ratio") and ta is not None and tl is not None:
        equity = float(ta) - float(tl)

**校验的是 `debt_ratio`（一个比率），使用的是 `total_assets - total_liabilities`
（两个绝对额）。** 那两个绝对额根本不在核查清单上——不在核心二十项，
也不在保理扩展的十四项里。于是一个具体金额绕过整套核实架构，
印进了报告里最具决策相关性的那张表：

    | 净资产法 | 10080 | 净资产 12600 万元 × 0.8 |

这类错误在代码里**看起来完全正常**：一个 `_verified(...)` 守卫赫然在列。
所以判据从"调用点自己挑一个字段来验"改成"按口径查它实际用了哪些量"
（`BASIS_INPUT_FIELDS`），挑错字段这条路在结构上就走不通了。

## 为什么营收法与现金流法不需要改

它们校验 `revenue` / `cash_flow`，使用 `revenue` / `operating_cash_flow`——
**验的和用的是同一个量**。只有净资产法验的和用的不是一回事。

运行：cd backend && python -m pytest tests/test_basis_input_verification.py -q
"""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import (  # noqa: E402
    CHECKLIST_BY_ID, build_field_checks, compute_completeness,
)
from service import credit_advice as ca  # noqa: E402
from service import risk_scorecard as rs  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, find_company, profile_to_facts,
)
from service.datasource import apply_all  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _case(query: str, scenario=None):
    company = find_company(query)
    assert company is not None, f"测试档案缺失：{query}"
    checks = build_field_checks(scenario=scenario)
    fill_field_checks(company, profile_to_facts(company), checks)
    store = {}
    apply_all(company, checks, store)
    completeness = compute_completeness(checks)
    assessment = rs.score(company, checks, completeness)
    return company, checks, assessment


def _rec(query: str, scenario=None):
    company, checks, assessment = _case(query, scenario)
    return ca.recommend_credit(company, checks, assessment)


TAIRUI = "请对东莞市泰锐精密传动件有限公司做贷前尽职调查"
YUNLING = "对云岭恒晟精密机械有限公司开展应收账款保理尽职调查"


# ------------------------------------------------- 一、根因：净资产不在清单上

def test_net_assets_inputs_are_not_checklist_fields_at_all():
    """确认前提：资产总额与负债总额没有任何清单项覆盖。

    这条断言是整条 bad case 的地基。它若哪天变成 False（有人给这两个量
    建了核实路径并登记进清单），净资产法就应该重新可用——
    那时该改的是 `BASIS_INPUT_FIELDS`，不是删掉这条断言。
    """
    for field_id in ca.BASIS_INPUT_FIELDS["净资产法"]:
        assert field_id not in CHECKLIST_BY_ID, (
            f"{field_id} 现在进清单了——请同步 BASIS_INPUT_FIELDS 与本用例")


def test_net_asset_basis_is_never_computed_today():
    """净资产法不得出现在可测算口径里。

    它算出来的 10080 万元是一个绕过核实架构的具体金额，
    印在报告里最具决策相关性的那张表上。
    """
    for query, scenario in ((TAIRUI, None), (YUNLING, "factoring")):
        rec = _rec(query, scenario)
        methods = [b["method"] for b in rec.get("basis") or []]
        assert "净资产法" not in methods, f"{query}：净资产法仍在参与测算"


def test_revenue_and_cash_flow_bases_survive():
    """反面：验的和用的是同一个量的两个口径必须照常工作。

    只钉"净资产法没了"，很容易写出一个把所有口径都关掉的实现。
    """
    rec = _rec(YUNLING, "factoring")
    methods = [b["method"] for b in rec.get("basis") or []]
    assert "营收法" in methods and "现金流法" in methods


# ------------------------------------------------- 二、缺口必须可见

def test_the_missing_basis_is_recorded_with_a_reason():
    """少一个口径要写明原因，否则读者无从判断是"算出来不利"还是"根本没算"。"""
    rec = _rec(TAIRUI)
    rows = rec.get("unavailable_bases") or []
    assert [r["method"] for r in rows] == ["净资产法"]
    assert "不在核查清单内" in rows[0]["reason"]


def test_the_table_says_not_computed_rather_than_zero():
    """金额列必须写「不参与测算」。

    写 0 会被读成"这个口径算出来是零"——在授信含义上与"没算"完全相反，
    而 0 恰恰是现金流法为负时的真实取值，两者混在同一列里必然被误读。
    """
    rec = _rec(TAIRUI)
    md = ca.render_markdown(rec)
    line = next(ln for ln in md.split("\n") if ln.startswith("| 净资产法"))
    assert "不参与测算" in line
    assert "| 0 |" not in line


def test_the_gap_stays_out_of_the_drawdown_conditions():
    """不可测算的口径**不得**进放款条件。

    ## 这条用例记录了我在本次修复里的一次回退

    我最初把「净资产法无法测算」写成一条放款条件，理由是"少一道口径约束
    实际影响放多少钱"。`test_无风险点时不堆砌套话` 当场把它拦下来了——
    净资产法目前对**每一家**企业都不可测算，那条会出现在每一份报告上。

    **一个永远亮的告警等于没有告警**：风控人员会学会跳过整个条件列表，
    真正的风险点反而被淹没（BC-18 记的正是这个形态）。

    不可测算的口径属于工作底稿，位置在测算表里那一行「不参与测算」。
    """
    for query, scenario in ((TAIRUI, None), (YUNLING, "factoring")):
        rec = _rec(query, scenario)
        polluting = [c for c in rec.get("conditions") or [] if "净资产法" in c]
        assert not polluting, (
            f"{query}：恒亮的提示混进了放款条件，会淹没真正的风险点：{polluting}")


def test_a_clean_case_still_has_no_conditions_at_all():
    """反面兜底：干净主体的放款条件必须为空。

    这是上一条的反面——只钉"别出现净资产法"，仍可能塞进别的恒亮套话。
    """
    from service.credit_advice import recommend_credit  # noqa: WPS433
    from tests.test_credit_advice import (  # noqa: WPS433
        _HEALTHY, _assessment, _checks,
    )
    assert recommend_credit(_HEALTHY, _checks(), _assessment())["conditions"] == []


# ------------------------------------------------- 三、额度不得因此变大

def test_removing_the_basis_does_not_enlarge_the_amount():
    """去掉一个口径 = 少一道上限约束，额度可能因此变大。

    对现有两份档案实测：现金流法始终取最小，因此额度不变。
    这条钉住的是**当前事实**——将来若有档案让净资产法成为最小值，
    它会失败，那时必须显式决定怎么处理，而不是让额度悄悄放大。
    """
    assert _rec(YUNLING, "factoring")["suggested_amount"] == 144.0
    assert _rec(TAIRUI)["suggested_amount"] is None


def test_amount_still_comes_from_the_minimum_of_remaining_bases():
    """额度仍然取剩余口径的最小值，保守方向不变。"""
    company, checks, assessment = _case(YUNLING, "factoring")
    rec = ca.recommend_credit(company, checks, assessment)
    smallest = min(b["value"] for b in rec["basis"])
    chosen = next(a for a in rec["adjustments"] if a["factor"] == "口径选取")
    assert f"{min(rec['basis'], key=lambda b: b['value'])['method']}" in chosen["detail"]
    assert smallest == min(b["value"] for b in rec["basis"])


# ------------------------------------------------- 四、结构：别再接一半

def test_every_no_branch_carries_the_unavailable_bases():
    """`recommend_credit` 的每一条 `_no(...)` 出口都要带上不可测算口径。

    ## 这条守卫是有来历的

    写本次修复时我**连续两次漏了同一件事**：先漏了"额度归零"那条出口，
    再漏了 `_no` 内部构造放款条件的那次调用。两次都是同一形态——
    「造好了但没接到全部执行路径上」（BC-31/45/48/49/64 的第七、八次）。

    人工盘点出口靠不住，所以用 AST 逐条数：函数里有几个 `_no(...)`，
    就必须有几个带 `unavailable`。新增出口时这条会当场失败。
    """
    with open(os.path.join(APP, "service/credit_advice.py"), encoding="utf-8") as fh:
        module = ast.parse(fh.read())
    fn = next(n for n in ast.walk(module)
              if isinstance(n, ast.FunctionDef) and n.name == "recommend_credit")

    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_no"]
    assert calls, "没找到任何 _no 出口——判据本身失效了"
    missing = [ast.unparse(c)[:60] for c in calls
               if not any(k.arg == "unavailable" for k in c.keywords)]
    assert not missing, f"这些 _no 出口没带上不可测算口径：{missing}"


def test_no_basis_verifies_a_field_it_does_not_use():
    """每个口径声明的输入字段，都必须是清单里真实存在的项（或明确的空缺）。

    钉住 `BASIS_INPUT_FIELDS` 是"验哪个"与"用哪个"的**唯一**出处：
    调用点不得再自己挑字段来验——挑错就是 BC-72。
    """
    for method, fields in ca.BASIS_INPUT_FIELDS.items():
        assert fields, f"{method} 没有声明输入字段，等于没有判据"
        if method == "净资产法":
            continue          # 已知空缺，由 test_net_assets_inputs... 单独钉
        for field_id in fields:
            assert field_id in CHECKLIST_BY_ID, \
                f"{method} 声明的输入 {field_id} 不在清单里"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
