"""
核查清单状态机测试

覆盖 v0.2 代码评审指出的边界：空结果、部分字段缺失、外部检索回写、
主体归属错误。这些边界只靠"完整 fixture 能算出 11/15"是覆盖不到的。

运行：
    cd backend && python -m pytest tests/test_dd_checklist.py -v
    （无 pytest 时可直接执行本文件）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import (  # noqa: E402
    CHECKLIST, CHECKLIST_BY_ID, REQUIRED_IDS,
    build_field_checks, compute_completeness, record_search_attempt,
)
from service.company_profile import fill_field_checks  # noqa: E402


def _checks():
    return build_field_checks(checked_at="2026-08-10T00:00:00")


def _by_id(checks, fid):
    return next(c for c in checks if c["field_id"] == fid)


# ---------- 清单定义本身 ----------

def test_清单规模与必查数():
    assert len(CHECKLIST) == 20
    assert len(REQUIRED_IDS) == 15


def test_每项都映射到有效章节():
    valid = {f"sec_{i}" for i in range(1, 9)}
    for item in CHECKLIST:
        assert item.section_id in valid, f"{item.field_id} 章节非法"


def test_属性型字段不得把空结果当正面结论():
    """存续企业必然具备的属性，absence_meaningful 必须为 False"""
    for fid in ("registration", "business_scope", "operating_status",
                "shareholders", "actual_controller",
                "revenue", "net_profit", "debt_ratio"):
        assert CHECKLIST_BY_ID[fid].absence_meaningful is False, fid


def test_事件型字段允许无记录结论():
    for fid in ("litigation", "enforcement", "dishonesty", "guarantee", "negative_news"):
        assert CHECKLIST_BY_ID[fid].absence_meaningful is True, fid


# ---------- 空档案 ----------

def test_空档案时核实率为零且无正面结论():
    checks = _checks()
    fill_field_checks({"name": "X", "coverage": {"queried": []}}, [], checks)
    comp = compute_completeness(checks)
    assert comp["required_verified"] == 0
    assert comp["verified_rate"] == 0.0
    assert all(c["status"] == "unverified" for c in checks)


# ---------- 关键边界：查询了但无内容 ----------

def test_属性型字段查询无内容应判未核实并提示主体存疑():
    """工商登记查了却没返回 —— 是异常信号，不是「无记录」"""
    company = {"name": "X", "coverage": {"queried": ["registration", "shareholders"]}}
    checks = _checks()
    fill_field_checks(company, [], checks)

    reg = _by_id(checks, "registration")
    assert reg["status"] == "unverified"
    assert reg["value"] is None
    assert "异常" in reg["failure_reason"] or "主体" in reg["failure_reason"]


def test_事件型字段查询无内容应判已核实无记录():
    """失信记录查了没有 —— 这是可支持授信的正面结论"""
    company = {"name": "X", "coverage": {"queried": ["dishonesty", "equity_freeze"]}}
    checks = _checks()
    fill_field_checks(company, [], checks)

    d = _by_id(checks, "dishonesty")
    assert d["status"] == "verified"
    assert d["value"] == "经查询，无相关记录"


def test_未查询与查了无记录必须区分():
    company = {"name": "X", "coverage": {
        "queried": ["dishonesty"],
        "not_queried_reason": {"guarantee": "本数据源不覆盖"},
    }}
    checks = _checks()
    fill_field_checks(company, [], checks)

    assert _by_id(checks, "dishonesty")["status"] == "verified"      # 查了，无记录
    g = _by_id(checks, "guarantee")
    assert g["status"] == "unverified"                                # 没查
    assert "不覆盖" in g["failure_reason"]


# ---------- 部分字段缺失 ----------

def test_财务期间存在但缺某指标时不得判为已核实():
    """曾有缺陷：拼接结果为空串仍被判 verified"""
    company = {
        "name": "X",
        "coverage": {"queried": ["revenue", "debt_ratio"]},
        "financials": [{"period": "2025年度", "revenue": 100.0}],  # 无 debt_ratio
    }
    checks = _checks()
    fill_field_checks(company, [], checks)

    assert _by_id(checks, "revenue")["status"] == "verified"
    dr = _by_id(checks, "debt_ratio")
    assert dr["status"] == "unverified", "缺少 debt_ratio 不应判为已核实"
    assert dr["value"] in (None, ""), "不应留下空值"


# ---------- 主体归属 ----------

def test_主体未确认的舆情不得计入已核实():
    company = {
        "name": "泰锐精密",
        "coverage": {"queried": ["negative_news"]},
        "negative_news": [{
            "title": "东莞一精密件厂被罚", "publish_date": "2025-11-08",
            "severity": "一般", "subject_confirmed": False,
        }],
    }
    checks = _checks()
    fill_field_checks(company, [], checks)

    n = _by_id(checks, "negative_news")
    assert n["status"] == "unverified", "主体归属未确认不能算已核实"
    assert "未确认" in n["failure_reason"]


def test_主体已确认的舆情计入已核实():
    company = {
        "name": "泰锐精密",
        "coverage": {"queried": ["negative_news"]},
        "negative_news": [{
            "title": "泰锐精密被罚", "publish_date": "2025-11-08",
            "severity": "一般", "subject_confirmed": True,
        }],
    }
    checks = _checks()
    fill_field_checks(company, [], checks)
    assert _by_id(checks, "negative_news")["status"] == "verified"


# ---------- 外部检索回写 ----------

def test_检索回写只记录尝试不制造已核实():
    """通用网页检索不能把未核实字段变成已核实，否则核实率会被虚高"""
    checks = _checks()
    before = compute_completeness(checks)["required_verified"]

    touched = record_search_attempt(checks, "sec_6", "web_search", checked_at="t")
    after = compute_completeness(checks)["required_verified"]

    assert touched > 0, "sec_6 应有条目被记录"
    assert after == before, "检索尝试不得改变核实数"
    assert "web_search" in _by_id(checks, "guarantee")["attempted_sources"]


def test_回写不覆盖已核实项():
    company = {"name": "X", "coverage": {"queried": ["dishonesty"]}}
    checks = _checks()
    fill_field_checks(company, [], checks)
    record_search_attempt(checks, "sec_5", "web_search")
    d = _by_id(checks, "dishonesty")
    assert d["status"] == "verified"
    assert "web_search" not in d["attempted_sources"], "已核实项不应被追加检索记录"


# ---------- 统计口径 ----------

def test_not_applicable_不计入分母():
    checks = build_field_checks(exclude_ids=["shareholders", "actual_controller"])
    comp = compute_completeness(checks)
    assert comp["required_total"] == 13, "15 必查项排除 2 项后应为 13"


def test_选查项不计入必查统计():
    company = {"name": "X", "coverage": {"queried": ["equity_freeze"]}}
    checks = _checks()
    fill_field_checks(company, [], checks)
    comp = compute_completeness(checks)
    assert comp["required_verified"] == 0, "equity_freeze 是选查项，不应计入必查已核实数"


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
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
