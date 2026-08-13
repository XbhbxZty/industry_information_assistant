"""
测试替身与真实实现的契约一致性（BC-45 的通用防线）

## 这个文件为什么存在

v0.6 B 阶段 23 条断言全绿并已提交，起 Docker 做真实验证时才发现
人机协同在生产路径上**整条失效**：测试注入的 `MemorySaver` 同步异步两套
接口都实现，真正上生产的 `PostgresSaver` 只有同步一套。

> **替身比真货能力强，替身能跑通的路径，真货跑不了。**

这不是某一个替身写错了，而是一类系统性风险：只要替身的能力是真货的
**超集**，测试就会给出虚假的绿色。而超集是最容易不小心写出来的形态——
`**kwargs` 兜底、`Mock()` 万能对象、多实现一个方法，都会造成超集。

失败方向也是不对称的：
- 替身比真货**弱** → 测试失败 → 立刻发现，成本是时间
- 替身比真货**强** → 测试通过 → 上线才炸，成本是线上事故

所以这里逐个替身检查：**替身接受的调用，真货必须也接受。**

## 覆盖范围与边界

只检查**接口形状**（方法存在性 + 参数兼容性），不检查行为语义。
形状检查很便宜、不依赖任何外部服务，却能挡住 BC-45 这一整类问题；
行为语义则必须靠真实环境端到端验证（见 v0.6 的两进程恢复实验）。

运行：cd backend && python tests/test_double_contracts.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))


def _params(fn):
    """方法的形参名（去掉 self）与是否有 **kwargs 兜底"""
    sig = inspect.signature(fn)
    names, has_var_kw = [], False
    for name, p in sig.parameters.items():
        if name == "self":
            continue
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            has_var_kw = True
        elif p.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        else:
            names.append(name)
    return names, has_var_kw


def assert_double_conforms(double_cls, real_cls, methods, label):
    """
    断言 double 能接受的调用，real 也能接受。

    三条检查，每条都对应一种真实发生过或差点发生的失效：
      1. 方法必须在真货上存在      —— 否则替身在测一个不存在的接口
      2. 替身不得用 **kwargs 兜底  —— 兜底会吞掉真货不接受的参数
      3. 参数名必须一致            —— 生产按关键字调用时，名字不同即失败
    """
    for m in methods:
        fake = getattr(double_cls, m, None)
        real = getattr(real_cls, m, None)
        assert fake is not None, f"[{label}] 替身缺少 {m}"
        assert real is not None, (
            f"[{label}] 真实实现没有 {m}——替身在测一个不存在的接口"
        )

        fake_names, fake_var_kw = _params(fake)
        real_names, real_var_kw = _params(real)

        assert not (fake_var_kw and not real_var_kw), (
            f"[{label}] {m} 替身用 **kwargs 兜底而真货没有：替身成了超集，"
            f"生产多传一个真货不接受的参数也测不出来"
        )
        extra = [p for p in fake_names if p not in real_names]
        assert not extra, (
            f"[{label}] {m} 替身接受真货没有的参数 {extra}："
            f"真实{real_names} vs 替身{fake_names}"
        )


# ---------------------------------------------------------------- 检查点服务

def test_检查点服务替身与真实实现签名一致():
    """
    `save_checkpoint` 在 graph.py 里是**按关键字调用**的
    （session_id= / state= / user_id= / ui_state= / final_report=），
    参数名不一致会直接 TypeError。此前替身用 `**k` 兜底，什么都吃。
    """
    from service.checkpoint_service import CheckpointService
    from test_graph_equivalence import _build_graph

    double = type(_build_graph().checkpoint_service)
    assert_double_conforms(
        double, CheckpointService,
        ("save_checkpoint", "update_status", "load_checkpoint", "get_checkpoint_info"),
        "checkpoint_service",
    )


def test_生产按关键字调用的参数名逐一存在():
    """
    形状检查之外再钉一次调用点：graph.py 用了哪些关键字，
    真实类就必须有哪些形参。改真实类签名时这条会先炸。
    """
    from service.checkpoint_service import CheckpointService

    used = {"session_id", "state", "user_id", "ui_state", "final_report"}
    real_names, _ = _params(CheckpointService.save_checkpoint)
    missing = used - set(real_names)
    assert not missing, f"graph.py 按关键字传了 {missing}，但真实实现没有这些形参"


# ---------------------------------------------------------------- 图检查点

def test_图检查点候选实现都支持异步接口():
    """
    BC-45 本体。工厂只可能返回桥接版 PostgresSaver 或 MemorySaver，
    两者都必须实现异步接口——`astream` 只走异步。

    与 `test_human_review.py` 里那条重复是有意的：那条守的是人机协同这个功能，
    这条守的是"替身契约"这类问题。删掉任何一条，另一个视角就失去保护。
    """
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.memory import MemorySaver
    from service.deep_research_v2.graph import _make_async_bridge_saver

    for cls in (_make_async_bridge_saver(), MemorySaver):
        for m in ("aget_tuple", "aput", "aput_writes", "alist"):
            assert getattr(cls, m, None) is not getattr(BaseCheckpointSaver, m), (
                f"{cls.__name__}.{m} 未实现异步接口"
            )


def test_未桥接的PostgresSaver确实不满足契约():
    """
    ⭐ 前提断言（BC-20 的教训）：先证明这条契约**有鉴别力**。

    若不加这条，上面那条断言可能因为"所有类都碰巧满足"而永远为真——
    一条永远通过的断言和没有断言等价。
    """
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.postgres import PostgresSaver

    missing = [
        m for m in ("aget_tuple", "aput", "aput_writes", "alist")
        if getattr(PostgresSaver, m, None) is getattr(BaseCheckpointSaver, m)
    ]
    assert missing, (
        "未桥接的 PostgresSaver 现在竟然满足契约了——"
        "说明上游已修复，此时应删除 _make_async_bridge_saver 而不是留着"
    )


# ---------------------------------------------------------------- Agent 替身

def test_Agent替身保留了真实的add_message():
    """
    等价性测试替换 `process()` 但**刻意保留** `add_message` 的真实实现——
    被测的正是"消息如何流出去"。若把它也替掉，等于取消了这条测试
    （BC-34：测试替被测对象补齐前提，等于没测）。
    """
    from service.deep_research_v2.agents.base import BaseAgent
    from test_graph_equivalence import _build_graph

    g = _build_graph()
    for name in ("architect", "scout", "data_analyst", "wizard", "writer", "critic"):
        agent = getattr(g, name)
        assert type(agent).add_message is BaseAgent.add_message, (
            f"{name} 的 add_message 被替换了：SSE 流转路径将不再被测试覆盖"
        )
        assert agent.process.__name__ == "_process", f"{name} 的 process 应为替身"


def test_结构化适配器替身必须经注册表登记():
    """
    v0.6a 的替身走的是与真实适配器**完全相同**的登记手续
    （`register_adapter`），而不是绕过闭集直接构造证据。
    替身若能走捷径，那条捷径迟早会被生产代码用上。
    """
    import test_verification_chain  # noqa: F401  —— 导入时会登记替身
    from service.verification import trusted_adapters

    registered = trusted_adapters()
    assert "guarantee_registry" in registered, "测试替身必须经注册表登记"
    assert "web_search" not in registered, "通用网页检索永远不得登记为结构化适配器"


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
            print(f"  FAIL  {name}: {str(e)[:200]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
