# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
证据链校验策略（v0.6a 复核后新增）

## 为什么单独成文件

「旧检查点缺来源时是否兜底重放」是一个**授信决策级别的开关**：
开着，一份来源未迁移的检查点就能继续产出自动评级。这种开关藏在函数默认参数
里，等于让链路中任意一层的调用者不经意间决定风控口径——v0.6a 首版就是
`allow_legacy_profile_replay: bool = True` 藏在 `verify_evidence_chain()` 的
签名里，复核时才发现全 verified 的旧检查点可以一路走到「低风险」（BC-33）。

配置集中在这里，才能一眼看出当前口径，也才能在测试里显式切换而不是
靠传参绕过。

## 默认取严的理由

尽调系统的失败方向不对称：
- 误判为「不予评级」→ 多一次人工复核，成本是时间
- 误判为「低风险」  → 可能直接放款，成本是本金

所以任何"拿不准"都必须倒向前者。
"""
import os


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class VerificationPolicy:
    """证据链校验口径。决策路径读这里，不读函数默认值。"""

    def __init__(self) -> None:
        # 旧检查点缺 verification_origin 时，是否允许按初始档案重放兜底。
        #
        # False（默认）：不予采信 → 决策路径直接 fail-closed。
        # True：仅供**显式迁移工具**与非决策读取使用。即便置 True，降级也必然
        #       记入 degradations，并由 `apply_provenance_gate()` 强制
        #       「不得自动落到低风险 + 强制人工复核」。
        self.allow_legacy_profile_replay: bool = _env_bool(
            "DD_ALLOW_LEGACY_PROFILE_REPLAY", False
        )

        # 存在来源降级时，自动评级允许达到的最优等级。
        # 设为「中风险」而非「低风险」：来源不明的核实不足以支撑最宽松的结论。
        self.degraded_level_floor: str = os.getenv(
            "DD_DEGRADED_LEVEL_FLOOR", "中风险"
        )

        # —— 人机协同复核卡点（v0.6）——
        #
        # `requires_human_review` 为真时，流程在 `human_review` 节点中断，
        # 等风控人员确认后才继续。合规要求，也是出坏账追责的前提。
        #
        # 置 False 只应用于**离线批量评测**：评测跑几十家企业，没人在旁边点确认，
        # 中断会让整批任务挂死。生产环境关掉它等于取消了复核这道岗。
        self.require_human_review_gate: bool = _env_bool(
            "DD_REQUIRE_HUMAN_REVIEW_GATE", True
        )


POLICY = VerificationPolicy()
