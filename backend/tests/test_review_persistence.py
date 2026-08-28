# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
人工复核结论必须落盘（BC-69）

## 这一轮在钉什么

复核人在界面上确认并**覆盖了风险等级**，日志明确记录 approved=True、
override_level=低风险、reviewer=ddtest、理由完整。前端也拿到了终稿。
但查数据库：`final_report` 仍是复核前的、`human_review` 全为 null、等级仍是中风险。

根因在 `graph.py` 终局：

    final_state["phase"] = ResearchPhase.COMPLETED.value   # 只改内存
    self.checkpoint_service.update_status(session_id, "completed")   # 只写状态位

**只调了 `update_status`，没调 `_save_checkpoint`。**

这是 BC-31/48/49/64 同形缺陷的第五次，但更隐蔽：前四次是"完全没接"，
一跑就露；这次是"接了一半"——状态位对了内容没对，功能看起来完全正常，
**可用性正常、可审计性为零**。

而这个项目的核心业务约束是"出坏账要追责"，追责要问的正是
"谁把中风险改成了低风险、依据是什么"。

运行：cd backend && python -m pytest tests/test_review_persistence.py -q
"""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

GRAPH = os.path.join(os.path.dirname(__file__), "..", "app", "service",
                     "deep_research_v2", "graph.py")


def _graph_source() -> str:
    return open(GRAPH, encoding="utf-8").read()


def _completion_block() -> str:
    """终局分支的源码：从 phase=COMPLETED 到 yield build_complete_event。"""
    text = _graph_source()
    start = text.index('final_state["phase"] = ResearchPhase.COMPLETED.value')
    end = text.index("yield build_complete_event", start)
    return text[start:end]


# ------------------------------------------------- 一、终局必须落完整状态

def test_completion_saves_full_state_not_just_status():
    """本条的直接回归：终局不能只写状态位。

    `update_status` 只改 status 一列；`final_report` 与 `state_json`
    要靠 `save_checkpoint` 才会写。只调前者 = 复核结论永久丢失。
    """
    block = _completion_block()
    assert "_save_checkpoint" in block, (
        "终局分支没有调用 _save_checkpoint —— 复核结论、覆盖后的等级、"
        "终稿都不会落盘（BC-69）"
    )
    assert "update_status" in block, "状态位仍然要写"


def test_full_state_is_saved_before_status_flips():
    """先落内容再改状态位。

    顺序反了的话，中间若崩溃，会留下一个 status=completed 但内容是旧的记录
    ——比两者都是旧的更难排查，因为它看起来像是完成了。
    """
    # ⚠️ 判顺序必须用 AST，不能用字符串 index。
    # 第一版就是拿 block.index(...) 比位置，结果命中了修复说明里那句
    # "原实现只调 update_status"——判据把注释当成了代码。
    # 本轮这已是第四次守卫误伤注释/文档（见 BADCASES 复盘表同名条目），
    # 修法永远是改判据，不是改被判的代码。
    source = _graph_source()
    lines = source.splitlines()
    start = next(i for i, l in enumerate(lines, 1)
                 if 'final_state["phase"] = ResearchPhase.COMPLETED.value' in l)
    end = next(i for i, l in enumerate(lines, 1)
               if i > start and "yield build_complete_event" in l)

    calls = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in {
            "_save_checkpoint", "update_status"
        } and start <= node.lineno <= end:
            calls.append((node.lineno, node.func.attr))

    order = [name for _, name in sorted(calls)]
    assert order[:2] == ["_save_checkpoint", "update_status"], \
        f"终局应先落盘再改状态位，实际顺序：{order}"


def test_phase_is_persisted_so_it_cannot_contradict_status():
    """`phase` 必须随状态一起落盘。

    实测残留：phase=reviewing 而 status=completed，两个字段自相矛盾——
    因为 phase 只改在内存的 final_state 里，从没写进库。
    """
    block = _completion_block()
    assert 'final_state["phase"] = ResearchPhase.COMPLETED.value' in block
    # phase 写在 state 里，由 save_checkpoint 落盘；所以断言 save 收的是 final_state
    assert "self._save_checkpoint(" in block
    assert "final_state" in block.split("self._save_checkpoint(")[1][:120], \
        "落盘的必须是 final_state（其中已含 phase=completed）"


# ------------------------------------------------- 二、高风险写入要回读

def test_review_persistence_is_verified_by_read_back():
    """复核是权限最高的人工操作，写入后必须回读校验。

    BC-69 举一反三第 3 条：越是需要追责的操作，越要在写入后回读。
    原先它恰恰是唯一没有回读校验的写入路径。
    """
    text = _graph_source()
    assert "_verify_review_persisted" in text
    block = _completion_block()
    assert "_verify_review_persisted" in block, "回读校验必须在终局路径上被调用"


def test_read_back_only_runs_when_a_review_actually_happened():
    """没有复核就不该多一次 DB 读——低频高风险路径才值得这个成本。"""
    text = _graph_source()
    fn = text[text.index("def _verify_review_persisted"):]
    fn = fn[:fn.index("\n    def ")]
    assert 'if not review.get("reviewer"):' in fn and "return" in fn, \
        "无复核人时应直接返回"


def test_read_back_failure_is_loud_but_not_fatal():
    """校验失败要大声留痕，但不抛异常。

    报告已经产出，此时中断没有意义；但"审计链断了而无人知晓"
    比"断了"更糟，所以必须 logger.error。
    """
    text = _graph_source()
    fn = text[text.index("def _verify_review_persisted"):]
    fn = fn[:fn.index("\n    def ")]
    assert "logger.error" in fn, "校验失败必须 error 级留痕"
    assert "raise" not in fn, "不得抛异常中断已完成的流程"


# ------------------------------- 三、额度必须能被验证是否跟着等级重算

def test_credit_advice_carries_the_level_it_was_based_on():
    """额度系数由等级决定（BC-49），载荷必须带上依据的等级。

    复核人覆盖等级后，若额度不跟着重算，就是一处静默不一致；
    而载荷不带等级时，这个一致性**从外部根本无法观测**。
    """
    from service.credit_advice import recommend_credit
    from config.dd_checklist import build_field_checks

    company = {"name": "测试", "credit_application": {"amount": 1000.0, "unit": "万元"},
               "financials": [{"period": "2025年度", "revenue": 10000.0,
                               "net_profit": 500.0, "operating_cash_flow": 300.0,
                               "total_assets": 8000.0, "total_liabilities": 4000.0,
                               "unit": "万元"}]}
    checks = build_field_checks()
    for level in ("低风险", "中风险", "高风险", "数据不足，无法评级"):
        rec = recommend_credit(company, checks, {"level": level, "gates_applied": []})
        assert "based_on_level" in rec, f"{level}: 额度载荷未带依据等级"
        assert rec["based_on_level"] == level, (
            f"{level}: based_on_level 是 {rec['based_on_level']}，与传入等级不符")


def test_both_branches_carry_the_level():
    """出具与不出具两条分支都要带——不出具那条同样需要可追溯。"""
    import inspect
    from service import credit_advice
    src = inspect.getsource(credit_advice.recommend_credit)
    assert src.count("based_on_level") >= 2, \
        "recommend_credit 的两条 return 分支都必须带 based_on_level"


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
