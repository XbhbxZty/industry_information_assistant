# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
DeepResearch V2.0 - LangGraph 工作流

实现多智能体协作的状态机图：
Plan -> Research -> Analyze -> Write -> Review -> (Revise) -> Complete

使用 LangGraph 实现循环和条件分支。
"""

import logging
import asyncio
from typing import Dict, Any, List, Literal, Optional, AsyncGenerator
from datetime import datetime

# 导入取消检查函数
try:
    from router.research_router import is_research_cancelled, clear_cancel_flag
except ImportError:
    try:
        from app.router.research_router import is_research_cancelled, clear_cancel_flag
    except ImportError:
        # 兼容直接运行脚本的情况
        def is_research_cancelled(session_id: str) -> bool:
            return False
        def clear_cancel_flag(session_id: str):
            pass

# LangGraph 导入 - 如果没有安装则使用简化版本
try:
    from langgraph.graph import StateGraph, END
    from langgraph.types import Command, interrupt
    from langgraph.config import get_stream_writer
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    logging.error(
        "LangGraph 未安装。v0.6 起编排完全由 LangGraph 承担，"
        "手写编排已删除——请安装 langgraph 后再运行。"
    )

from .state import ResearchState, ResearchPhase, create_initial_state
from .agents import ChiefArchitect, DeepScout, CodeWizard, CriticMaster, LeadWriter, DataAnalyst
from .agents.writer import _canonicalize_risk_block

try:
    from service.risk_scorecard import apply_human_review, needs_human_review, render_markdown
    from config.verification_policy import POLICY
except ImportError:
    from app.service.risk_scorecard import (
        apply_human_review, needs_human_review, render_markdown,
    )
    from app.config.verification_policy import POLICY

# 导入检查点服务
try:
    from service.checkpoint_service import get_checkpoint_service
except ImportError:
    try:
        from app.service.checkpoint_service import get_checkpoint_service
    except ImportError:
        # 兼容直接运行脚本的情况
        def get_checkpoint_service():
            return None

# 导入配置
try:
    from config.llm_config import get_config
except ImportError:
    try:
        from app.config.llm_config import get_config
    except ImportError:
        # 兼容直接运行脚本的情况
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from config.llm_config import get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("DeepResearchGraph")


def build_complete_event(state: Dict[str, Any], references: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    构造 research_complete 事件。

    独立成函数是为了能被断言：终局事件必须携带风险评级——
    评级只推在中途的流式事件里是不够的，调用方若只等最终结果就会拿不到，
    而"拿不到评级"在这个业务里不能表现为"没有风险"。
    """
    return {
        "type": "research_complete",
        "final_report": state.get("final_report", ""),
        "quality_score": state.get("quality_score", 0.0),
        "facts_count": len(state.get("facts", [])),
        "charts_count": len(state.get("charts", [])),
        "iterations": state.get("iteration", 0),
        "references": references,
        # 核查清单结果（v0.2）：核实率是该轮唯一的可验收产出
        "completeness": state.get("completeness", {}),
        "field_checks": state.get("field_checks", []),
        # 风险评级（v0.5）：level 与 gates_applied 必须同时给出，
        # 只给 composite_score 会让下游得出与等级相反的结论
        "risk_assessment": state.get("risk_assessment", {}),
        # field_checks / triggered_rules 里的 evidence_id 必须能在同一个终局载荷中
        # 解引用；只发 ID 不发证据库，会形成不可审计的悬空引用（BC-39）。
        "evidence_store": state.get("evidence_store", {}),
        # 证据链降级与执行错误（v0.6a 复核补充）：终局事件此前不带这些，
        # 调用方只等最终结果就看不到"这份评级建立在来源不明的数据上"（BC-33）
        "errors": state.get("errors", []),
    }


# ---------------------------------------------------------------- 图检查点
#
# 与本项目自带的 `checkpoint_service` **职责不重叠**，两者都需要：
#
#   LangGraph checkpointer  → 图执行到哪个节点、中断在哪、resume 从哪继续
#   本项目 checkpoint_service → 业务状态与 UI 状态，供前端刷新后重建界面
#
# 必须持久化而非 MemorySaver：`DeepResearchV2Service()` 是**每次请求新建**的，
# 进程内内存检查点跨请求必然失效——风控人员几小时后来点"确认"时，
# 那个中断点已经不存在了。这不是偏好，是 resume 能否工作的硬约束。
_CHECKPOINTER = None
_CHECKPOINTER_POOL = None


def _make_async_bridge_saver():
    """
    构造带异步桥接的 PostgresSaver 类。

    ## 为什么需要桥接（BC-45）

    同步 `PostgresSaver` **没有实现异步接口**——`aget_tuple` 直接抛
    `NotImplementedError`，而 `astream` 只走异步接口。直接拿它编译图，
    人机协同在生产路径上完全不工作。

    为什么不用 `AsyncPostgresSaver`：psycopg 的异步模式在 Windows 上要求
    `WindowsSelectorEventLoopPolicy`，而改全局事件循环策略会牵动整个应用
    （Selector 循环不支持子进程）。桥接只影响检查点这一处，代价是每次
    读写多一次线程切换——检查点操作短且不频繁，这个代价可以接受。
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    class _AsyncBridgePostgresSaver(PostgresSaver):
        async def aget_tuple(self, config):
            return await asyncio.to_thread(self.get_tuple, config)

        async def aput(self, config, checkpoint, metadata, new_versions):
            return await asyncio.to_thread(
                self.put, config, checkpoint, metadata, new_versions)

        async def aput_writes(self, config, writes, task_id, task_path=""):
            return await asyncio.to_thread(
                self.put_writes, config, writes, task_id, task_path)

        async def adelete_thread(self, thread_id):
            return await asyncio.to_thread(self.delete_thread, thread_id)

        async def alist(self, config, *, filter=None, before=None, limit=None):
            items = await asyncio.to_thread(
                lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
            for item in items:
                yield item

    return _AsyncBridgePostgresSaver


def _get_graph_checkpointer():
    """惰性构造全局共享的图检查点存储。失败时降级为进程内存储并大声告警。"""
    global _CHECKPOINTER, _CHECKPOINTER_POOL
    if _CHECKPOINTER is not None:
        return _CHECKPOINTER

    try:
        from psycopg_pool import ConnectionPool
        try:
            from core.database import DATABASE_URL
        except ImportError:
            from app.core.database import DATABASE_URL

        _CHECKPOINTER_POOL = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=5, open=True, timeout=10,
            # autocommit + 关闭 prepare 是 PostgresSaver 的要求
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        saver = _make_async_bridge_saver()(_CHECKPOINTER_POOL)
        saver.setup()
        _CHECKPOINTER = saver
        logger.info("[Graph] 图检查点使用 PostgresSaver（人工复核可跨请求恢复）")
    except Exception as e:
        from langgraph.checkpoint.memory import MemorySaver
        _CHECKPOINTER = MemorySaver()
        logger.error(
            f"[Graph] PostgresSaver 不可用（{e}），降级为 MemorySaver。"
            f"⚠️ 人工复核中断将无法跨请求恢复——生产环境必须修复此项"
        )
    return _CHECKPOINTER


def reset_graph_checkpointer(checkpointer=None) -> None:
    """替换图检查点存储。供测试注入 MemorySaver，避免依赖数据库。"""
    global _CHECKPOINTER
    _CHECKPOINTER = checkpointer


class DeepResearchGraph:
    """
    DeepResearch V2.0 工作流图

    实现完整的多智能体协作流程：
    1. Plan (ChiefArchitect) - 分析问题，生成研究大纲
    2. Research (DeepScout) - 并行深度搜索
    3. Analyze (CodeWizard) - 数据分析和可视化
    4. Write (LeadWriter) - 撰写报告
    5. Review (CriticMaster) - 对抗式审核
    6. Revise (LeadWriter) - 修订（如果需要）
    """

    def __init__(
        self,
        llm_api_key: str = None,
        llm_base_url: str = None,
        search_api_key: str = None,
        model: str = None,
        max_iterations: int = None
    ):
        """
        初始化工作流

        所有参数都可从配置文件读取，传入的参数会覆盖配置
        """
        # 获取配置
        config = get_config()

        # 使用传入参数或配置默认值
        self.llm_api_key = llm_api_key or config.api_key
        self.llm_base_url = llm_base_url or config.base_url
        self.search_api_key = search_api_key or config.search_api_key
        self.model = model or config.default_model
        self.max_iterations = max_iterations or config.research.max_iterations

        # 初始化各个 Agent（使用各自配置的模型）
        self.architect = ChiefArchitect(
            self.llm_api_key, self.llm_base_url,
            config.agents.architect.model
        )
        self.scout = DeepScout(
            self.llm_api_key, self.llm_base_url, self.search_api_key,
            config.agents.scout.model
        )
        self.data_analyst = DataAnalyst(
            self.llm_api_key, self.llm_base_url,
            config.agents.data_analyst.model
        )
        self.wizard = CodeWizard(
            self.llm_api_key, self.llm_base_url,
            config.agents.wizard.model
        )
        self.critic = CriticMaster(
            self.llm_api_key, self.llm_base_url,
            config.agents.critic.model
        )
        self.writer = LeadWriter(
            self.llm_api_key, self.llm_base_url,
            config.agents.writer.model
        )

        logger.info(f"DeepResearchGraph initialized with models:")
        logger.info(f"  - Architect: {config.agents.architect.model}")
        logger.info(f"  - Scout: {config.agents.scout.model}")
        logger.info(f"  - DataAnalyst: {config.agents.data_analyst.model}")
        logger.info(f"  - Wizard: {config.agents.wizard.model}")
        logger.info(f"  - Critic: {config.agents.critic.model}")
        logger.info(f"  - Writer: {config.agents.writer.model}")

        # 检查点服务
        self.checkpoint_service = get_checkpoint_service()

        # 构建图
        if LANGGRAPH_AVAILABLE:
            self.graph = self._build_langgraph()
        else:
            self.graph = None

    def _save_checkpoint(
        self,
        state: Dict[str, Any],
        user_id: str = None,
        ui_state: Dict[str, Any] = None
    ) -> bool:
        """保存检查点（包含后端状态和 UI 状态）"""
        if not self.checkpoint_service:
            return False

        session_id = state.get("session_id", "")
        if not session_id:
            return False

        try:
            checkpoint_id = self.checkpoint_service.save_checkpoint(
                session_id=session_id,
                state=state,
                user_id=user_id,
                ui_state=ui_state,
                final_report=state.get("final_report")
            )
            if checkpoint_id:
                logger.info(f"Checkpoint saved: {checkpoint_id}")
                return True
        except Exception as e:
            logger.warning(f"Failed to save checkpoint: {e}")

        return False

    def _load_checkpoint(self, session_id: str) -> Dict[str, Any]:
        """加载检查点"""
        if not self.checkpoint_service:
            return None

        try:
            state = self.checkpoint_service.load_checkpoint(session_id)
            if state:
                logger.info(f"Checkpoint loaded for session: {session_id}")
                return state
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")

        return None

    def get_checkpoint_info(self, session_id: str) -> Dict[str, Any]:
        """获取检查点信息"""
        if not self.checkpoint_service:
            return None
        return self.checkpoint_service.get_checkpoint_info(session_id)

    def _build_langgraph(self):
        """
        构建 LangGraph 状态图。

        ## 这张图为什么要重建而不是"取消注释"（v0.6）

        原先的图是个**半成品，而且已经和实际执行分叉**：analyze 节点只调
        CodeWizard 不调 DataAnalyst，没有补充搜索回环，没有取消与检查点。
        实测跑一遍只产出 7 个事件、`risk_assessment` 为空——照它恢复执行会
        静默丢掉整个 v0.5 + v0.6a 的风险评级链路（BC-41）。

        声明式定义和实际行为分叉，而分叉本身没有任何机制能发现——这与
        BC-15 / BC-31 是同一形态：两套实现只有一套被执行，另一套无人验证。
        所以 `tests/test_graph_equivalence.py` 先于本次改造存在，
        它对旧路径录了黄金轨迹，本图必须逐事件复现。

        ## 结构

            plan → research → analyze → visualize → write → review
                                                              │
                          ┌───────────────────────────────────┤
                          ↓                 ↓                 ↓
                    re_research         revise            (complete)
                          ↓                 ↓                 ↓
                       rewrite ─────────→ review            END

        取消不走静态边：任意节点在入口发现取消标志时返回
        `Command(goto=END)` 直接终止——它是异常出口，不该污染主干拓扑。
        """
        workflow = StateGraph(ResearchState)

        workflow.add_node("plan", self._plan_node)
        workflow.add_node("research", self._research_node)
        # analyze / visualize 必须是两个节点：DataAnalyst 产出风险评级（纯规则），
        # CodeWizard 产出图表（依赖 LLM）。合成一个节点会让评级被 LLM 成败门控。
        workflow.add_node("analyze", self._analyze_node)
        workflow.add_node("visualize", self._visualize_node)
        workflow.add_node("write", self._write_node)
        workflow.add_node("review", self._review_node)
        workflow.add_node("re_research", self._re_research_node)
        workflow.add_node("rewrite", self._rewrite_node)
        workflow.add_node("revise", self._revise_node)

        workflow.set_entry_point("plan")

        # 主干边全部带取消守卫。
        #
        # ⚠️ 不能用 `Command(goto=END)` 做取消出口：实测当节点同时声明了静态边时，
        # goto 不会取代静态边，两条路都会走——取消后流程照常推进到底，
        # 终局事件照发（BC-42）。守卫条件边是唯一可靠且可读的写法。
        for src, dst in (("plan", "research"), ("research", "analyze"),
                         ("analyze", "visualize"), ("visualize", "write"),
                         ("write", "review")):
            workflow.add_conditional_edges(src, self._guard(dst), [dst, END])

        # 审核后的三种走向。原图只有 revise / complete 两种，
        # 漏掉了"信息不足需补充检索"这条实际存在的路径。
        workflow.add_node("human_review", self._human_review_node)

        workflow.add_conditional_edges(
            "review",
            self._route_after_review,
            {
                "re_research": "re_research",
                "revise": "revise",
                # 审核回环结束后一律经过复核卡点。是否真的中断由节点自己判断——
                # 路由函数不该重复实现"要不要人工复核"这条规则。
                "complete": "human_review",
            },
        )
        workflow.add_edge("human_review", END)

        workflow.add_conditional_edges("re_research", self._guard("rewrite"), ["rewrite", END])
        workflow.add_conditional_edges("rewrite", self._guard("review"), ["review", END])
        workflow.add_conditional_edges("revise", self._guard("review"), ["review", END])

        return workflow.compile(checkpointer=_get_graph_checkpointer())

    @staticmethod
    def _guard(next_node: str):
        """取消守卫：正常走 next_node，已取消则直接结束"""
        def _route(state: ResearchState) -> str:
            return END if state.get("_cancelled") else next_node
        return _route

    # ------------------------------------------------------------ 节点公共部分

    def _cancelled(self, state: ResearchState) -> bool:
        session_id = state.get("session_id", "")
        return bool(session_id and is_research_cancelled(session_id))

    @staticmethod
    def _emit(event: Dict[str, Any]) -> None:
        """
        往 SSE 流里推一条编排级事件（phase / checkpoint_saved / cancelled）。

        Agent 级事件由 `BaseAgent.add_message()` 自己推，两者走同一个
        stream writer，因此前端看到的顺序就是真实的执行顺序。
        """
        try:
            get_stream_writer()(event)
        except Exception:      # 不在 runnable 上下文（如直接单测节点函数）
            pass

    async def _run_agent(self, agent, state: ResearchState) -> bool:
        """
        执行一个 Agent，期间保持取消响应。

        Returns: True 正常完成；False 被取消

        与手写版本的区别：消息不再经 `asyncio.Queue` 中转——`add_message()`
        直接推给 stream writer，所以这里的轮询**只负责取消**。
        仍然必须轮询而不是直接 await：Scout 一轮可能跑几百秒，
        只在节点边界响应取消等于取消按钮在最需要的时候失灵。
        """
        logger.info(f"[Graph] node agent start: {agent.name}")
        task = asyncio.create_task(agent.process(state))
        while not task.done():
            if self._cancelled(state):
                logger.info(f"[Graph] cancelled during agent: {agent.name}")
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                self._mark_cancelled(state)
                return False
            await asyncio.sleep(0.1)
        try:
            await task
        except Exception as e:
            # Agent 自身异常不终止编排：确定性产出（评级/清单）必须能活到终局
            logger.error(f"[Graph] agent {agent.name} error: {e}", exc_info=True)
            state.setdefault("errors", []).append(f"{agent.name} 执行失败: {e}")
        # 消息已经流出去了，state 里不必再留一份（检查点会因此显著变小）
        state["messages"] = []
        return True

    def _mark_cancelled(self, state: ResearchState) -> None:
        """
        置取消标志并推事件。

        只推一次：`_cancelled` 已置位时直接返回。守卫边保证后续节点不会执行，
        但节点内部可能在 `_enter` 与 `_run_agent` 两处都发现取消。
        """
        if state.get("_cancelled"):
            return
        state["_cancelled"] = True
        self._emit({"type": "research_cancelled", "message": "研究已取消"})

    def _enter(self, state: ResearchState, phase: str, label: str) -> bool:
        """节点入口：取消检查 + 推 phase 事件。返回 False 表示应当立即终止"""
        if self._cancelled(state) or state.get("_cancelled"):
            self._mark_cancelled(state)
            return False
        self._emit({"type": "phase", "phase": phase, "content": label})
        return True

    # ------------------------------------------------------------ 节点

    async def _plan_node(self, state: ResearchState):
        state = dict(state)
        if not self._enter(state, "planning", "开始规划研究..."):
            return state
        state["phase"] = ResearchPhase.INIT.value
        if not await self._run_agent(self.architect, state):
            return state
        self._save_step_checkpoint(state, {
            "type": "planning", "status": "completed",
            "stats": {"sections": len(state.get("outline", []))},
        })
        return state

    async def _research_node(self, state: ResearchState):
        state = dict(state)
        if not self._enter(state, "researching", "开始深度搜索..."):
            return state
        state["phase"] = ResearchPhase.RESEARCHING.value
        if not await self._run_agent(self.scout, state):
            return state
        self._save_step_checkpoint(state, {
            "type": "researching", "status": "completed",
            "stats": {"facts": len(state.get("facts", [])),
                      "sources": len(state.get("references", []))},
        })
        return state

    async def _analyze_node(self, state: ResearchState):
        """
        风险评级节点（DataAnalyst，纯规则）。

        刻意与 visualize 分开且排在它前面：评级不得被任何 LLM 步骤门控——
        图表生成失败不能连累"这家企业是否可授信"这个结论（BC-17）。
        """
        state = dict(state)
        if not self._enter(state, "analyzing", "开始数据分析..."):
            return state
        state["phase"] = ResearchPhase.ANALYZING.value
        if not await self._run_agent(self.data_analyst, state):
            return state
        return state

    async def _visualize_node(self, state: ResearchState):
        state = dict(state)
        if self._cancelled(state):
            self._emit({"type": "research_cancelled", "message": "研究已取消"})
            return state
        if not await self._run_agent(self.wizard, state):
            return state
        self._save_step_checkpoint(state, {
            "type": "analyzing", "status": "completed",
            "stats": {"charts": len(state.get("charts", []))},
        })
        return state

    async def _write_node(self, state: ResearchState):
        state = dict(state)
        if not self._enter(state, "writing", "开始撰写报告..."):
            return state
        state["phase"] = ResearchPhase.WRITING.value
        if not await self._run_agent(self.writer, state):
            return state
        self._save_step_checkpoint(state, {
            "type": "writing", "status": "completed",
            "stats": {"report_length": len(state.get("final_report", ""))},
        })
        return state

    async def _review_node(self, state: ResearchState):
        state = dict(state)
        label = f"审核中（第 {state.get('iteration', 0) + 1} 轮）..."
        if not self._enter(state, "reviewing", label):
            return state
        state["phase"] = ResearchPhase.REVIEWING.value
        if not await self._run_agent(self.critic, state):
            return state
        return state

    async def _re_research_node(self, state: ResearchState):
        state = dict(state)
        if not self._enter(state, "re_researching", "根据审核反馈补充搜索..."):
            return state
        state["phase"] = ResearchPhase.RE_RESEARCHING.value
        if not await self._run_agent(self.scout, state):
            return state
        return state

    async def _rewrite_node(self, state: ResearchState):
        state = dict(state)
        if not self._enter(state, "rewriting", "基于新信息重新撰写..."):
            return state
        state["phase"] = ResearchPhase.WRITING.value
        if not await self._run_agent(self.writer, state):
            return state
        return state

    async def _revise_node(self, state: ResearchState):
        state = dict(state)
        if not self._enter(state, "revising", "根据反馈修订报告..."):
            return state
        state["phase"] = ResearchPhase.REVISING.value
        if not await self._run_agent(self.writer, state):
            return state
        return state

    async def _human_review_node(self, state: ResearchState):
        """
        风控复核卡点（v0.6 人机协同）。

        ## 为什么这一步必须存在

        业务约束：高风险结论不得全自动放行。这既是信贷合规要求，也是出坏账
        追责的前提——报告上必须有人签字。`checkpoint_service` 从原项目起就
        支持 `paused` 状态，但**在此之前没有任何一行代码设置过它**。

        ## 为什么它倒逼了 LangGraph 的恢复

        "暂停 → 等人确认 → 从断点继续"最干净的实现是 LangGraph 原生
        `interrupt()`。而 v0.6 之前图执行是死代码，所以这条业务需求
        直接倒逼了 A 阶段的编排重建——不是先重构再找用途。

        ## ⚠️ interrupt 之前不得有不可重复的副作用

        实测：恢复时**本节点会从头重跑**，`interrupt()` 这次直接返回复核结论
        而不再抛出。因此中断点之前的任何写操作都会执行两次。
        本节点在 `interrupt()` 之前只读不写；"已暂停"的 SSE 事件也不在这里推，
        而是由 `_run_with_langgraph` 检测到 `__interrupt__` 时推一次（BC-44）。
        """
        state = dict(state)
        assessment = state.get("risk_assessment") or {}

        if not POLICY.require_human_review_gate:
            logger.warning("[Graph] 人工复核卡点已被配置关闭（仅应用于离线评测）")
            return state
        if not needs_human_review(assessment):
            logger.info("[Graph] 评级未要求人工复核，直接完成")
            return state

        decision = interrupt(self._review_request(state, assessment))

        # —— 以下只在恢复后执行 ——
        logger.info(f"[Graph] 收到复核结论: {decision}")
        try:
            state["risk_assessment"] = apply_human_review(assessment, decision or {})
        except ValueError as e:
            # 结论不合法（如未署名）不得静默放行：宁可停在未复核状态
            logger.error(f"[Graph] 复核结论非法: {e}")
            state.setdefault("errors", []).append(f"人工复核结论非法，未采纳: {e}")
            return state

        self._sync_risk_block(state)
        self._emit({
            "type": "human_review_completed",
            "session_id": state.get("session_id", ""),
            "human_review": state["risk_assessment"]["human_review"],
            "level": state["risk_assessment"]["level"],
            "gates_applied": state["risk_assessment"]["gates_applied"],
            "credit_advice": state["risk_assessment"]["credit_advice"],
        })
        return state

    @staticmethod
    def _review_request(state: ResearchState, assessment: Dict[str, Any]) -> Dict[str, Any]:
        """
        交给复核人的材料。

        必须同时给出等级**与闸门**：等级往往由闸门而非分数决定，
        只给分数会让复核人得出与等级相反的结论（见 risk_scorecard 3.1）。
        """
        comp = state.get("completeness") or {}
        critical = [
            f for f in (state.get("critic_feedback") or [])
            if (f.get("severity") if isinstance(f, dict) else None) == "critical"
        ]
        return {
            "type": "human_review_required",
            "session_id": state.get("session_id", ""),
            "company_name": state.get("company_name", ""),
            "level": assessment.get("level"),
            "composite_score": assessment.get("composite_score"),
            "credit_advice": assessment.get("credit_advice"),
            "gates_applied": assessment.get("gates_applied") or [],
            "verified_rate": comp.get("verified_rate"),
            "unverified_fields": comp.get("unverified_fields") or [],
            "conflicting_fields": comp.get("conflicting_fields") or [],
            "critical_issues": critical,
            "errors": state.get("errors") or [],
        }

    def _sync_risk_block(self, state: ResearchState) -> None:
        """
        复核结论回写报告正文。

        复核人签的是这份报告，复核结果就必须出现在这份报告里——
        只存在事件载荷里，导出的 Word 交到评审会时就看不到谁批的。
        """
        report = state.get("final_report") or ""
        if not report:
            return
        try:
            state["final_report"] = _canonicalize_risk_block(
                report, render_markdown(state["risk_assessment"])
            )
        except Exception as e:
            logger.error(f"[Graph] 复核结论回写报告失败: {e}", exc_info=True)
            state.setdefault("errors", []).append(f"复核结论未能写入报告正文: {e}")

    def _route_after_review(
        self, state: ResearchState
    ) -> Literal["re_research", "revise", "complete"]:
        """
        审核后的走向。

        判据是 Critic 写回的 `phase`，不是 `unresolved_issues` ——
        Critic 已经区分了"信息不足要补检索"与"只需改文字"，
        编排层再自己推断一遍就会与它打架。

        ⚠️ 轮次上限必须在这里兜底：Critic 可以永远要求修订。
        """
        if state.get("_cancelled"):
            return "complete"
        if state.get("iteration", 0) >= state.get("max_iterations", 3):
            logger.info("[Graph] 达到最大迭代轮次，结束审核回环")
            return "complete"
        phase = state.get("phase")
        if phase == ResearchPhase.RE_RESEARCHING.value:
            return "re_research"
        if phase == ResearchPhase.REVISING.value:
            return "revise"
        return "complete"

    async def run(
        self,
        query: str,
        session_id: str,
        resume: bool = False,
        user_id: str = None,
        search_web: bool = True,
        search_local: bool = False
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        执行研究流程（流式输出）

        Args:
            query: 用户问题
            session_id: 会话ID
            resume: 是否从检查点恢复
            user_id: 用户ID（用于检查点）
            search_web: 是否启用网络搜索（默认True）
            search_local: 是否启用本地知识库搜索（默认False）

        Yields:
            SSE 事件字典
        """
        # 尝试从检查点恢复
        state = None
        if resume and session_id:
            state = self._load_checkpoint(session_id)
            if state:
                yield {
                    "type": "research_resumed",
                    "phase": state.get("phase", ""),
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat()
                }

        # 如果没有检查点，创建初始状态
        if not state:
            state = create_initial_state(
                query, session_id,
                search_web=search_web,
                search_local=search_local
            )
            state["max_iterations"] = self.max_iterations

            # 注入尽调对象档案（v0.1：硬编码 JSON；v0.4 起改由 datasource 适配层提供）
            company = self._load_company_profile(query, state)

            yield {
                "type": "research_start",
                "query": query,
                "session_id": session_id,
                "search_web": search_web,
                "search_local": search_local,
                "company_name": state.get("company_name", ""),
                "timestamp": datetime.now().isoformat()
            }

            if company:
                yield {
                    "type": "company_profile_loaded",
                    "company_name": state["company_name"],
                    "facts_count": len(state["facts"]),
                    "timestamp": datetime.now().isoformat()
                }
                # 核查清单状态：前端展示核实率，评测据此计算未核实识别率
                yield {
                    "type": "field_checks_updated",
                    "field_checks": state["field_checks"],
                    "completeness": state["completeness"],
                    "timestamp": datetime.now().isoformat()
                }

        # 存储 user_id 用于检查点
        state["_user_id"] = user_id

        async for event in self._run_with_langgraph(state):
            yield event

    def _load_company_profile(self, query: str, state: ResearchState) -> Optional[Dict[str, Any]]:
        """
        识别尽调对象并把档案注入 state。

        v0.1 临时实现：读硬编码 JSON。v0.4 将替换为 service/datasource/ 适配层。
        识别不到企业时不报错——允许退化为普通研究流程。
        """
        try:
            from service.company_profile import (
                find_company, profile_to_facts, build_credit_context, fill_field_checks
            )
            from config.dd_checklist import build_field_checks, compute_completeness
        except ImportError:
            try:
                from app.service.company_profile import (
                    find_company, profile_to_facts, build_credit_context, fill_field_checks
                )
                from app.config.dd_checklist import build_field_checks, compute_completeness
            except ImportError:
                logger.warning("[graph] company_profile 模块不可用，跳过档案注入")
                return None

        company = find_company(query)
        if not company:
            logger.info("[graph] 未识别到尽调对象，按普通研究流程执行")
            return None

        state["company_name"] = company["name"]
        state["credit_context"] = build_credit_context(company)
        # 原始档案要留在 state 里：风险评分卡消费的是结构化数值（负债率、被执行笔数…），
        # facts 里的自然语言无法还原这些字段
        state["company_profile"] = company

        facts = profile_to_facts(company)
        state["facts"].extend(facts)

        # 核查清单：生成骨架 → 用档案填充 → 统计核实率
        checks = build_field_checks(checked_at=datetime.now().isoformat())
        fill_field_checks(company, facts, checks)
        before = compute_completeness(checks)

        # 结构化数据源适配层（v0.7-B）。
        #
        # 顺序在档案填充**之后**：适配器只补档案没覆盖的项，不覆盖已核实结论——
        # 覆盖需要显式的证据替代授权，"我后跑"不构成替代理由（BC-40）。
        #
        # 这也是 v0.6a 那套证据链第一次处理非替身适配器：证据经受信任注册表
        # 登记、带 profile_patch 投影、走同一条重放校验（BC-45 的教训在架构层）。
        state["evidence_store"] = state.get("evidence_store") or {}
        try:
            from service.datasource import apply_all
        except ImportError:
            from app.service.datasource import apply_all
        try:
            applied = apply_all(company, checks, state["evidence_store"])
            for adapter_id, fields in applied.items():
                got = sorted(f for f, r in fields.items() if r == "verified")
                if got:
                    logger.info(f"[graph] 适配器 {adapter_id} 补充核实：{got}")
        except Exception as e:
            # 适配器失败不得中断尽调，但必须显式披露——静默失败会让核实率
            # 悄悄退回档案水平，而报告读者无从知道少查了哪些源
            logger.error(f"[graph] 数据源适配层执行失败: {e}", exc_info=True)
            state.setdefault("errors", []).append(f"数据源适配层执行失败，本次仅使用初始档案: {e}")

        state["field_checks"] = checks
        state["completeness"] = compute_completeness(checks)

        comp = state["completeness"]
        logger.info(
            f"[graph] 已注入尽调对象 {company['name']}：事实 {len(facts)} 条，"
            f"必查项核实 {before['required_verified']}→{comp['required_verified']}"
            f"/{comp['required_total']} ({comp['verified_rate']:.0%})，"
            f"未核实：{comp['unverified_fields']}"
        )
        return company

    # ------------------------------------------------------- 检查点与 UI 状态

    def _build_ui_state(self, state: ResearchState) -> Dict[str, Any]:
        """
        从后端 state 投影出前端恢复所需的 UI 状态。

        与 LangGraph 自身的检查点职责不同，两者不重叠：
          - LangGraph checkpointer → **图执行到哪一步**（用于 interrupt 后 resume）
          - 本项目 checkpoint_service → **业务状态与 UI 状态**（用于刷新页面后重建界面）
        """
        ui = state.setdefault("_ui_state", {
            "research_steps": [], "search_results": [], "charts": [],
            "knowledge_graph": None, "streaming_report": "", "references": [],
        })

        if state.get("charts"):
            ui["charts"] = state["charts"]
        if state.get("final_report"):
            ui["streaming_report"] = state["final_report"]

        kg = state.get("knowledge_graph") or {}
        if kg.get("nodes") or kg.get("edges"):
            ui["knowledge_graph"] = kg
        elif not ui.get("knowledge_graph"):
            ui["knowledge_graph"] = {"nodes": [], "edges": []}

        facts = state.get("facts", [])
        if facts:
            ui["search_results"] = [{
                "id": f.get("id", ""),
                "title": f.get("source_name") or (f.get("content", "")[:50] + "..."
                                                  if len(f.get("content", "")) > 50
                                                  else f.get("content", "")),
                "source": f.get("source_type", "web"),
                "url": f.get("source_url", ""),
                "snippet": (f.get("content") or "")[:200],
                "date": f.get("timestamp", ""),
            } for f in facts]

        ui["references"] = self._ui_references(state)
        return ui

    def _ui_references(self, state: ResearchState) -> List[Dict[str, Any]]:
        """把 references 补成前端要的形状（title/link 必有值）"""
        facts = state.get("facts", [])
        out = []
        for idx, ref in enumerate(state.get("references", [])):
            fact = next((f for f in facts if f.get("source_url") == ref.get("url")), None)
            title = ref.get("source") or ref.get("marker") or ""
            if not title and fact:
                content = fact.get("content", "")
                title = content[:50] + "..." if len(content) > 50 else content
            out.append({
                "id": ref.get("id", idx + 1),
                "title": title or f"来源 {idx + 1}",
                "link": ref.get("url", ""),
                "content": (fact.get("content", "")[:200] if fact else ""),
                "source": "web",
            })
        return out

    def _save_step_checkpoint(self, state: ResearchState, step_info: Dict[str, Any]) -> None:
        """
        阶段结束时落一次检查点，并推 `checkpoint_saved` 事件。

        每个阶段都要落：中断后能从哪一步恢复，取决于最后一次成功保存在哪。
        """
        ui = self._build_ui_state(state)
        steps = ui["research_steps"]
        existing = next((s for s in steps if s.get("type") == step_info.get("type")), None)
        if existing:
            existing.update(step_info)
        else:
            steps.append(step_info)

        if self._save_checkpoint(state, state.get("_user_id"), ui):
            self._emit({
                "type": "checkpoint_saved",
                "phase": state.get("phase", ""),
                "session_id": state.get("session_id", ""),
            })
        else:
            logger.error(f"[检查点保存失败] session_id={state.get('session_id')}")

    # ------------------------------------------------------------- 图执行

    async def _run_with_langgraph(
        self, state: ResearchState
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        用 LangGraph 执行整条研究流程，同时保持实时 SSE。

        ## 原作者放弃 LangGraph 的理由，以及它为什么已经不成立

        `graph.py` 里原本写着"LangGraph 版本会批量处理消息，无法实现实时流式
        输出"。在当时的版本上这是真的：`astream` 默认按**节点粒度**产出，
        一个 Scout 节点跑几百秒，期间前端一个字都收不到。

        langgraph 1.x 提供了 `get_stream_writer()` + `stream_mode="custom"`：
        节点内部每调一次 writer，事件就立刻从 `astream` 出来。实测逐条即时
        送达，不在节点边界积压。于是"声明式编排"和"实时流式"不再互斥——
        这正是当年被迫二选一的那个点。

        `stream_mode=["custom", "values"]` 同时要两种流：
          - custom → Agent 与编排推出来的 SSE 事件，逐条转发
          - values → 每个节点后的完整 state，用来拿最终状态构造终局事件
        """
        if not (LANGGRAPH_AVAILABLE and self.graph):
            yield {"type": "error", "content": "LangGraph 不可用，无法执行研究流程"}
            return

        session_id = state.get("session_id", "")
        if session_id:
            clear_cancel_flag(session_id)
        state["_cancelled"] = False

        async for event in self._drive(state, session_id):
            yield event

    async def resume_review(
        self,
        session_id: str,
        decision: Dict[str, Any],
        user_id: str = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        风控人员提交复核结论后，从中断点继续执行。

        `Command(resume=...)` 让 `interrupt()` 直接返回该结论——被中断的节点
        会**从头重跑**，所以它在中断点之前必须是只读的（见 `_human_review_node`）。

        线程标识用 `session_id`：一次尽调 = 一个 thread，恢复才能找回断点。
        """
        if not (LANGGRAPH_AVAILABLE and self.graph):
            yield {"type": "error", "content": "LangGraph 不可用，无法恢复复核"}
            return

        config = {"configurable": {"thread_id": session_id}}
        snapshot = await self.graph.aget_state(config)
        if not (snapshot and snapshot.next):
            yield {"type": "error",
                   "content": f"会话 {session_id} 没有待复核的中断点（可能已完成或从未暂停）"}
            return

        logger.info(f"[Graph] 恢复复核: session={session_id}, 断点={snapshot.next}")
        yield {
            "type": "research_resumed",
            "session_id": session_id,
            "reason": "human_review",
            "timestamp": datetime.now().isoformat(),
        }
        async for event in self._drive(Command(resume=decision), session_id,
                                       user_id=user_id):
            yield event

    async def _drive(
        self,
        payload: Any,
        session_id: str,
        user_id: str = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        驱动一次图执行（首次运行或恢复），把三种收尾情况分开处理：
        中断待复核 / 被取消 / 正常完成。

        三者必须严格互斥：**中断和取消都不得产出 `research_complete`**，
        否则调用方会以为拿到了完整结论——而"暂停等复核"恰恰意味着
        这份结论还没有效力。
        """
        config = {"configurable": {"thread_id": session_id}}
        final_state: Dict[str, Any] = payload if isinstance(payload, dict) else {}
        interrupted = None

        try:
            async for mode, chunk in self.graph.astream(
                payload, config, stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    yield chunk
                elif isinstance(chunk, dict):
                    if chunk.get("__interrupt__"):
                        interrupted = chunk["__interrupt__"][0]
                    else:
                        final_state = chunk

            if interrupted is not None:
                # 暂停：写 paused 状态，推出复核请求，**不发终局事件**
                if self.checkpoint_service and session_id:
                    self.checkpoint_service.update_status(session_id, "paused")
                payload_out = dict(getattr(interrupted, "value", {}) or {})
                payload_out.setdefault("type", "human_review_required")
                payload_out["session_id"] = session_id
                logger.info(f"[Graph] 已暂停等待人工复核: session={session_id}")
                yield payload_out
                return

            if final_state.get("_cancelled"):
                logger.info(f"[Graph] 研究已取消: {session_id}")
                return

            final_state["phase"] = ResearchPhase.COMPLETED.value
            if self.checkpoint_service and session_id:
                self.checkpoint_service.update_status(session_id, "completed")

            logger.info(
                f"[Graph] ===== 研究完成 ===== facts={len(final_state.get('facts', []))}, "
                f"charts={len(final_state.get('charts', []))}, "
                f"iterations={final_state.get('iteration', 0)}, "
                f"报告长度={len(final_state.get('final_report', ''))}"
            )
            yield build_complete_event(final_state, self._ui_references(final_state))

        except Exception as e:
            logger.error(f"[Graph] LangGraph execution error: {e}", exc_info=True)
            if self.checkpoint_service and session_id:
                self.checkpoint_service.update_status(session_id, "failed", str(e))
            yield {"type": "error", "content": str(e)}

    async def run_sync(self, query: str, session_id: str) -> ResearchState:
        """
        同步执行（返回最终状态）

        用于不需要流式输出的场景
        """
        state = create_initial_state(query, session_id)
        state["max_iterations"] = self.max_iterations

        # 依次执行各阶段
        state = await self.architect.process(state)
        state = await self.scout.process(state)
        state = await self.data_analyst.process(state)
        state = await self.wizard.process(state)
        state = await self.writer.process(state)

        # 审核修订循环（支持智能路由）
        while state["iteration"] < state["max_iterations"]:
            state = await self.critic.process(state)

            if state["phase"] == ResearchPhase.COMPLETED.value:
                break

            # 智能路由：需要补充搜索
            if state["phase"] == ResearchPhase.RE_RESEARCHING.value:
                state = await self.scout.process(state)
                state["phase"] = ResearchPhase.WRITING.value
                state = await self.writer.process(state)

            # 仅需要文字修订
            elif state["phase"] == ResearchPhase.REVISING.value:
                state = await self.writer.process(state)
            else:
                break

        return state


def create_research_graph(
    llm_api_key: str = None,
    llm_base_url: str = None,
    search_api_key: str = None,
    model: str = None
) -> DeepResearchGraph:
    """
    工厂函数：创建 DeepResearch 工作流图

    所有参数都是可选的，会从配置文件读取默认值

    Args:
        llm_api_key: LLM API 密钥（可选，默认从配置读取）
        llm_base_url: LLM API 基础 URL（可选，默认从配置读取）
        search_api_key: 搜索 API 密钥（可选，默认从配置读取）
        model: 默认模型名称（可选，默认从配置读取）

    Returns:
        DeepResearchGraph 实例
    """
    return DeepResearchGraph(
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        search_api_key=search_api_key,
        model=model
    )
