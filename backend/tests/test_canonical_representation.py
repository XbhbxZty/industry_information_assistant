# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
跨层规范化只允许有一处定义（BC-59 的制度性待办）

## 这一轮在钉什么

BC-59 的两个假阴性都修完了，但**根因没修**：同一条规则仍有两份独立实现。

    金额口径   生产 `_to_wanyuan` 的 float 因子表  ×  评测 `Decimal(v)/10`
    来源引用   生产附录渲染 `[Sxxx]`               ×  评测 `_source_is_cited`

这就是 BC-52 那条纪律的原文——两处独立实现同一个规则，迟早漂移。BC-52 漂的是
写入侧与读取侧的集合名，BC-59 漂的是生产侧与评测侧的单位口径。

评测器的漂移方向尤其恶劣：它产生**假阴性**，把已经修好的生产能力重新判成
失败，诱导团队去改正确的代码。所以这组断言不测"换算对不对"，测的是
**两侧是不是同一个实现**。

## 断言分三层

1. 口径本身正确且精确（Decimal，不是浮点尾差）
2. 生产产出的形式，评测器必须能识别——用真实往返，不各自造字符串
3. 两侧不得存在第二份实现（结构性断言）

运行：cd backend && python -m pytest tests/test_canonical_representation.py -q
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.canonical import (  # noqa: E402
    CANONICAL_MONEY_UNIT, as_number, citation_present, format_source_citation,
    format_wanyuan, is_money_unit, normalize_unit, to_wanyuan,
)
from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from eval import score_real_case_run as scorer  # noqa: E402
from service.evidence_appendix import render_appendix  # noqa: E402
from service.rag_evidence_bridge import _to_wanyuan  # noqa: E402


# ------------------------------------------------------- 一、口径本身正确

def test_thousand_yuan_to_wanyuan_is_exact_not_floating_point():
    """千元→万元必须精确。

    `362012554 * 0.1` 在浮点下不精确，而这个值要参与**相等判断**
    （同期间多源冲突检测、银标比对）。一次尾差就把同一个金额判成两个值。
    """
    assert to_wanyuan(362012554, "千元") == Decimal("36201255.4")
    assert to_wanyuan("362,012,554", "千元") == Decimal("36201255.4")
    assert to_wanyuan(1234, "元") == Decimal("0.1234")
    assert to_wanyuan(1, "亿元") == Decimal("10000")
    assert to_wanyuan(5, "万元") == Decimal("5")


def test_non_money_units_are_rejected_rather_than_silently_converted():
    assert to_wanyuan(65.24, "%") is None
    assert to_wanyuan(100, "吨") is None
    assert not is_money_unit("%")
    assert to_wanyuan("不是数字", "千元") is None


def test_unit_aliases_from_real_statements_are_normalized():
    for raw, expected in (("人民币千元", "千元"), ("（千元）", "千元"),
                          ("单位", ""), ("万元", "万元"), (" 亿元 ", "亿元")):
        assert normalize_unit(raw) == expected, raw


def test_canonical_number_serialization_keeps_integers_integral():
    assert as_number(Decimal("5")) == 5
    assert isinstance(as_number(Decimal("5")), int)
    assert as_number(Decimal("36201255.4")) == 36201255.4
    assert as_number(None) is None


# ------------------------------- 二、生产产出的形式，评测器必须能识别（往返）

def test_appendix_citation_is_recognized_by_the_scorer_round_trip():
    """真实往返：附录**渲染**的引用，评测器必须**认得**。

    这是 BC-59 引用覆盖率 0% 那一条的回归。关键在于两端都用真实函数：
    左边是生产渲染器的输出，右边是评测器的判定，中间不由测试代劳。
    """
    checks = build_field_checks(checked_at="2026-08-17")
    revenue = next(c for c in checks if c["field_id"] == "revenue")
    revenue.update(status="verified", value="36201255.4万元", evidence_ids=["ev_1"],
                   sources=["ev_1"], verification_origin="structured_adapter",
                   source_adapter="rag_text_document_v1", retrieved_at="2026-08-17T00:00:00")
    store = {"ev_1": {"raw": {"sources": [{
        "source_id": "S002",
        "title": "S002_S002.pdf",          # 封闭运行里标题就是上传文件名
        "locator": "page:18",
        "url": "local://kb/case_01/doc",   # 公网 URL 已被替换
        "publication_date": "2025-03-15",
    }]}}}

    block = render_appendix(checks, store, compute_completeness(checks))
    assert scorer._source_is_cited(block, scorer._compact(block), {
        "source_id": "S002",
        "title": "宁德时代新能源科技股份有限公司2024年年度报告全文",
        "url": "https://example.invalid/annual-report.pdf",
    }), "附录渲染的来源编号，评测器必须认得——这正是 BC-59 报 0% 的那一处"


def test_bare_source_id_text_is_still_not_a_citation():
    """反面：修好假阴性不得顺手放开假阳性。"""
    assert not citation_present("内部调试字段 source_id=S002", "S002")
    assert not scorer._source_is_cited("source_id=S002", "source_id=s002",
                                       {"source_id": "S002", "title": "无", "url": "x"})
    assert citation_present("见附录 [S002] 一行", "S002")


def test_production_wanyuan_output_is_accepted_by_the_scorer():
    """生产写出的万元文本，评测器必须承认它等于银标的千元值。"""
    canonical = format_wanyuan(to_wanyuan(362012554, "千元"))
    assert canonical == f"36201255.4{CANONICAL_MONEY_UNIT}"
    variants = scorer._claim_variants({"value": 362012554, "value_raw": "362012554",
                                       "unit": "千元"})
    assert scorer._compact(canonical) in variants


def test_bridge_and_canonical_agree_on_every_money_unit():
    """生产适配器的换算与共享口径逐一致。

    `_to_wanyuan` 保留了序列化适配的职责，但数值必须来自共享定义。
    """
    for value in (0, 1, 1234, 362012554, 50744682):
        for unit in ("元", "千元", "万元", "亿元"):
            assert _to_wanyuan(value, unit) == as_number(to_wanyuan(value, unit)), \
                f"{value} {unit} 上生产与共享口径不一致"


# --------------------------------------- 三、结构性：不得存在第二份实现

def test_scorer_does_not_carry_its_own_conversion_factors():
    """评测器源码里不得再出现私有换算因子或私有引用形式。

    这是一条**结构性**断言。BC-52 的教训是"两处独立实现同一规则 = 迟早漂移"，
    而漂移无法靠数值断言提前发现——两份实现刚写完时通常是一致的。
    能提前发现的只有"这里根本不该有第二份实现"。
    """
    source = (
        os.path.join(os.path.dirname(__file__), "..", "eval", "score_real_case_run.py")
    )
    text = open(source, encoding="utf-8").read()
    body = "\n".join(
        line for line in text.splitlines()
        if not line.strip().startswith("#")
    )
    for forbidden in ('Decimal("10")', "Decimal('10')", "0.0001", "100_000",
                      'f"[{', "'[' +"):
        assert forbidden not in body, (
            f"评分器里出现了私有换算/引用实现：{forbidden!r}。"
            f"口径只能在 config.canonical 定义一次"
        )


def test_only_one_module_defines_the_money_factor_table():
    """全仓库只允许一处定义单位因子表。"""
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    offenders = []
    for root, _dirs, files in os.walk(app_dir):
        if "__pycache__" in root:
            continue
        for name in files:
            if not name.endswith(".py") or name == "canonical.py":
                continue
            path = os.path.join(root, name)
            text = open(path, encoding="utf-8").read()
            if '"千元":' in text or "'千元':" in text:
                offenders.append(os.path.relpath(path, app_dir))
    assert not offenders, f"这些模块自带单位因子表，应改为 import config.canonical：{offenders}"


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
