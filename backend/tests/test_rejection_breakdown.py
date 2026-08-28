# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
证据闸门的卡点必须可归因

## 这一轮在钉什么

拒绝统计此前只导出 `rejection_reasons` 的计数，而且**取值被拼进了 reason
字符串**：

    f"无法从原文切出证据窗口（取值「{value}」无法在原文中逐字定位）"

于是同一类失败在统计里碎成十几个各计数 1 的条目。A 轮 20 条拒绝里有 12 条
这样各自成行——`rejection_reasons` 对最常见的那一类**完全失去了聚合能力**，
"最大的卡点是什么"这个问题根本答不了。

逐条明细（field_id、取值）留在 `state` 里没有导出，终局事件、评测器、前端
都看不到。于是 B′ 轮量出"10 条卡在字段关键词近邻检查"之后就断了：无法判断
是闸门太严还是候选本就该拒，只能靠猜——而上一次靠猜的结论是错的。

运行：cd backend && python -m pytest tests/test_rejection_breakdown.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.rag_evidence_bridge import _reject, _rejection_breakdown  # noqa: E402


def _rejections(state):
    return state["rag_evidence_rejections"]


def test_reason_stays_a_stable_category_when_details_differ():
    """同一类失败必须聚合成一条，无论取值是什么。

    这是本条的直接回归：取值拼进 reason 会让 12 条同类失败变成 12 个
    各计数 1 的条目。
    """
    state = {}
    for value in ("连带责任保证", "佛山华普气体科技有限公司", "存续", "未披露涉诉记录"):
        _reject(state, "无法从原文切出证据窗口", {"field_id": "guarantee", "value": value},
                "sec_6", f"取值「{value}」无法在原文中逐字定位")

    breakdown = _rejection_breakdown(_rejections(state))
    assert len(breakdown) == 1, "同一类别不得因取值不同而碎成多条"
    assert breakdown[0]["count"] == 4
    assert breakdown[0]["reason"] == "无法从原文切出证据窗口"


def test_breakdown_attributes_failures_to_fields_and_keeps_samples():
    """光有计数不够——要能回答是哪个字段、模型交的是什么值。"""
    state = {}
    _reject(state, "取值近邻原文没有该字段的确定性关键词，字段归属无法确认",
            {"field_id": "inventory", "value": "59835533"}, "sec_4")
    _reject(state, "取值近邻原文没有该字段的确定性关键词，字段归属无法确认",
            {"field_id": "inventory", "value": "32868257"}, "sec_4")
    _reject(state, "取值近邻原文没有该字段的确定性关键词，字段归属无法确认",
            {"field_id": "overseas_revenue", "value": "110335509"}, "sec_3")

    top = _rejection_breakdown(_rejections(state))[0]
    assert top["count"] == 3
    assert top["fields"] == {"inventory": 2, "overseas_revenue": 1}, \
        "必须能定位到字段——否则无法判断是闸门太严还是候选该拒"
    assert {sample["value"] for sample in top["samples"]} == {
        "59835533", "32868257", "110335509"}


def test_breakdown_is_ordered_by_count_so_the_top_blocker_is_first():
    state = {}
    _reject(state, "少见原因", {"field_id": "revenue", "value": "1"}, "sec_4")
    for index in range(5):
        _reject(state, "主要卡点", {"field_id": "guarantee", "value": str(index)}, "sec_6")

    breakdown = _rejection_breakdown(_rejections(state))
    assert [row["reason"] for row in breakdown] == ["主要卡点", "少见原因"]
    assert breakdown[0]["count"] == 5


def test_samples_are_capped_and_truncated():
    """这是诊断信息不是审计记录——审计走证据附录，不能在这里堆全量原文。"""
    state = {}
    for index in range(12):
        _reject(state, "同一原因", {"field_id": "revenue", "value": "值" * 300},
                "sec_4", "细节" * 300)

    row = _rejection_breakdown(_rejections(state))[0]
    assert row["count"] == 12, "计数不受样本上限影响"
    assert len(row["samples"]) == 5, "样本要封顶"
    assert len(row["samples"][0]["value"]) <= 80
    assert len(row["samples"][0]["detail"]) <= 160


def test_missing_field_id_is_labelled_not_dropped():
    """来源级失败没有 field_id，但仍必须出现在统计里。

    丢掉它们会让"拒绝总数"和"分类之和"对不上，而对不上的统计没人会信。
    """
    state = {}
    _reject(state, "来源日期晚于研究截止日", {"value": "362012554"}, "sec_4",
            "2025-07-01 > 2025-05-31")
    row = _rejection_breakdown(_rejections(state))[0]
    assert row["fields"] == {"(未指定)": 1}
    assert sum(sum(entry["fields"].values()) for entry in
               _rejection_breakdown(_rejections(state))) == len(_rejections(state)), \
        "分类之和必须等于拒绝总数"


def test_empty_input_yields_an_empty_breakdown():
    assert _rejection_breakdown([]) == []


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
