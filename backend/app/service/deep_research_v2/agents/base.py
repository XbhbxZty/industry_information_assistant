# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""
DeepResearch V2.0 - Agent 基类

所有专家Agent的基类，提供通用的LLM调用、日志记录等功能。
"""

import json
import logging
import asyncio
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
from openai import OpenAI, APIConnectionError, APITimeoutError

from ..state import ResearchState, AgentLog

# LangGraph 的节点内实时流式出口（v0.6）。未安装时降级为 asyncio.Queue 路径，
# 保证 Agent 单测与不带 LangGraph 的环境仍可运行。
try:
    from langgraph.config import get_stream_writer as _get_stream_writer
except ImportError:  # pragma: no cover - 取决于运行环境是否装了 langgraph
    _get_stream_writer = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

# 单次 LLM 调用的墙钟上界（BC-56）。
#
# 此前 `call_llm` 完全没有超时概念：实测有单个 Scout 调用耗时 553 秒，
# 期间整张图没有任何产出，也没有任何一层能中止它。
#
# ⚠️ 超时**必须**下到 SDK 的 per-request `timeout`，不能用 `asyncio.wait_for`
# 包住 `asyncio.to_thread`：`to_thread` 交给线程池执行，**不可取消**——
# `wait_for` 超时后协程返回了，底下那个线程还在阻塞等 HTTP 响应。
# 每次超时泄漏一个线程，默认线程池很快被占满，后续调用连排队都排不上。
# 让 httpx 自己断连，线程才会真正结束。
DEFAULT_LLM_TIMEOUT_SECONDS = 90.0

# 客户端重试次数。SDK 默认 2 次，会把墙钟上界放大到 3×timeout；
# 显式设为 1 次，让最坏情况是**可计算的** 2×timeout + 一次退避。
LLM_MAX_RETRIES = 1


class LLMCallTimeout(RuntimeError):
    """一次 LLM 调用超出墙钟上界。

    单独立类型，是为了让调用方能把它记成**故障**而不是"这次没抽到证据"
    ——与 `SearchOutcome` 区分"没查成"和"查了没有"是同一条纪律（BC-51）。
    """

    def __init__(self, agent: str, model: str, timeout: float, elapsed_ms: int):
        super().__init__(
            f"{agent} 调用 {model} 超过 {timeout:.0f} 秒上界（实际 {elapsed_ms}ms）"
        )
        self.agent = agent
        self.model = model
        self.timeout = timeout
        self.elapsed_ms = elapsed_ms


class BaseAgent(ABC):
    """
    Agent 基类

    所有专家Agent继承此类，实现特定的 process 方法。
    """

    def __init__(
        self,
        name: str,
        role: str,
        llm_api_key: str,
        llm_base_url: str,
        model: str = "qwen-max"
    ):
        self.name = name
        self.role = role
        self.model = model
        self.client = OpenAI(
            api_key=llm_api_key,
            base_url=llm_base_url,
            max_retries=LLM_MAX_RETRIES,
        )
        # 单次调用的墙钟上界，实例属性以便按节点收紧/放宽（高频抽取该更短，
        # 低频审核可以更长）。放在实例上而不是只做 call_llm 参数，理由与
        # `as_of` 相同：漏传一处就等于那一处没有上界。
        self.llm_timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS
        self.logger = logging.getLogger(f"Agent.{name}")
        # 研究截止日（P0-3）。由各 Agent 在 process() 开头从 state 同步，
        # 空串 = 不设时点。放在实例上而非 call_llm 参数里，是为了让所有
        # 既有调用点自动获得约束——漏传一处就等于那一处没有时点隔离。
        self.as_of: str = ""

    @abstractmethod
    async def process(self, state: ResearchState) -> ResearchState:
        """
        处理状态并返回更新后的状态

        Args:
            state: 当前研究状态

        Returns:
            更新后的状态
        """
        pass

    def _with_time_anchor(self, system_prompt: str) -> str:
        """
        给 system prompt 加时间基准（见 BADCASES.md BC-03）。

        模型没有时钟，缺省会拿训练截止时间当"今天"。实测中 Critic 因此把
        一年多以前的 2025 年数据判定为"尚未发生的未来事件"并报为 critical 幻觉。

        更麻烦的是：即使正文里已出现"报告撰写于 2026年8月9日"，模型的先验仍会
        压过上下文。所以这里不只给日期，还显式给出判定规则，堵掉推理捷径。

        设了研究截止日时再加一段**时点约束**。这与上面的时间基准不是一回事：
        时间基准解决"模型以为现在是训练截止年"，时点约束解决"模型知道后来
        发生了什么，于是拿它来论证当时的判断"。后者在回溯评测里是直接的
        信息泄漏——用 7 月披露的半年报去证明 5 月的授信判断正确，
        这份评测就没有任何意义了。
        """
        today = datetime.now().strftime('%Y年%m月%d日')
        anchor = (
            "【时间基准】当前日期是 " + today + "。\n"
            "- 任何早于该日期的时间点都属于**已发生的过去**，不得判定为『未来事件』。\n"
            "- 判断数据时效性（如『是否超过两年』『是否为近期』）一律以该日期为准，"
            "不得依据你的训练数据截止时间。\n"
            "- 只有当资料中的日期**晚于**该日期时，才可质疑其真实性。\n\n"
        )
        if self.as_of:
            anchor += (
                "【研究截止日】本次判断以 " + self.as_of + " 为准。\n"
                "- 只能使用该日期**之前已经发布或已经发生**的信息。\n"
                "- 你从训练数据中知道的、该日期之后才发生的事，**一律不得引用**，"
                "也不得用来论证或推翻任何结论。\n"
                "- 若资料中出现晚于该日期的内容，指出它越过截止日，不要采信。\n"
                "- 这不是保密要求，是评测有效性要求：用事后信息倒推当时判断，"
                "会让这份报告失去全部参考价值。\n\n"
            )
        return anchor + system_prompt

    #: 各模型对单次输出的硬上限，**逐个实测**（2026-08-23）：
    #:
    #:     qwen-max           8,192   （12000 → Range of max_tokens should be [1, 8192]）
    #:     qwen-plus         32,768   （65536 → [1, 32768]）
    #:     deepseek-v4-flash ≥99,609  （二分到 99,609 仍通过）
    #:     qwen3-max         ≥65,536
    #:
    #: ⚠️ **不要把这里任何一个数提升成"模型的上限"。**
    #: 我就是这么错的：拿 `CodeWizard(...)` 不传 model 去探针，
    #: 撞上默认值 `"qwen-max"`，把 qwen-max 的 8192 当成了全局上限，
    #: 而生产跑的是 deepseek-v4-flash（`graph.py` 传 config 里的模型）。
    #: **测的和跑的不是一个模型，而两者差一个数量级。**
    MODEL_OUTPUT_CEILING = {
        "qwen-max": 8192,
        "qwen-plus": 32768,
        "qwen3-max": 65536,
        "deepseek-v4-flash": 65536,
    }

    #: 不在表里的模型按最保守处理——宁可产出短一点，不要整次调用 400。
    DEFAULT_OUTPUT_CEILING = 8192

    @property
    def max_output_tokens(self) -> int:
        """当前模型的输出上限。按实例的 `self.model` 查，不是类常量——
        同一个进程里不同 Agent 跑不同模型。"""
        return self.MODEL_OUTPUT_CEILING.get(
            self.model, self.DEFAULT_OUTPUT_CEILING)

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        temperature: float = 0.3,
        # 0 = 按当前模型的上限自动取；显式传数则以传入的为准（仍会被夹）。
        # 不写死一个数，是因为**这个默认值曾经写成 16000 并且是错的**。
        max_tokens: int = 0,
        timeout: Optional[float] = None,
        return_meta: bool = False,
    ):
        """
        调用 LLM

        Args:
            system_prompt: 系统提示
            user_prompt: 用户提示
            json_mode: 是否强制JSON输出
            temperature: 温度参数
            max_tokens: 最大token数
            timeout: 单次调用墙钟上界（秒）；缺省用 self.llm_timeout

        Returns:
            LLM 响应文本

        Raises:
            LLMCallTimeout: 超过上界。**不吞掉**——调用方必须能把它记成故障，
                否则一次挂死又会以"这一章没有证据"的形式进入报告（BC-51 同形）。
        """
        start_time = time.time()
        budget = self.llm_timeout if timeout is None else timeout

        try:
            # 超过供应商上限就夹住。**不夹的后果是整次调用颗粒无收**——
            # 一个 400 换不回任何产出，而夹到上限至少拿得到 8192 token。
            # 但夹这件事必须出声：静默截断会让报告短一截而没人知道（BC-51）。
            ceiling = self.max_output_tokens
            if not max_tokens:
                max_tokens = ceiling
            if max_tokens > ceiling:
                self.logger.warning(
                    f"max_tokens={max_tokens} 超过 {self.model} 的上限 "
                    f"{ceiling}，已夹住；该次产出可能比调用方预期的短")
                max_tokens = ceiling

            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self._with_time_anchor(system_prompt)},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                # per-request 超时交给 httpx，见模块顶部说明：
                # asyncio 侧无法取消 to_thread，只有 HTTP 层能真正断开。
                "timeout": budget,
            }

            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                **kwargs
            )

            choice = response.choices[0]
            content = choice.message.content
            duration = int((time.time() - start_time) * 1000)

            # `finish_reason` 是唯一能回答"响应为什么不完整"的字段。
            # 此前它在这里被丢掉，于是下游只能靠括号配平去猜，
            # 而猜出来的归因说了一件不成立的事（BC-76）。
            finish_reason = getattr(choice, "finish_reason", "") or ""
            usage = getattr(response, "usage", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            # 输入侧的实测读数。没有它，输入预算只能按字估，
            # 而字/token 比随内容变（散文 2.0、财报约 1.3）——
            # 按字定的预算要么撞上限（BC-77），要么白白浪费一半容量（BC-78）。
            prompt_tokens = getattr(usage, "prompt_tokens", None)

            self.logger.info(
                f"LLM call completed in {duration}ms, response length: {len(content)}"
                f", finish_reason={finish_reason or '未提供'}"
                + (f", prompt_tokens={prompt_tokens}"
                   if prompt_tokens is not None else "")
                + (f", completion_tokens={completion_tokens}"
                   if completion_tokens is not None else "")
            )

            if return_meta:
                return content, {"finish_reason": finish_reason,
                                 "completion_tokens": completion_tokens,
                                 "prompt_tokens": prompt_tokens,
                                 "duration_ms": duration}
            return content

        except (APITimeoutError, APIConnectionError) as e:
            duration = int((time.time() - start_time) * 1000)
            self.logger.error(
                f"LLM call exceeded its {budget:.0f}s budget after {duration}ms: {e}"
            )
            raise LLMCallTimeout(self.name, self.model, budget, duration) from e
        except Exception as e:
            self.logger.error(f"LLM call failed: {e}")
            raise

    def parse_json_response(self, response: str) -> Dict[str, Any]:
        """安全解析JSON响应，处理markdown代码块和格式问题"""
        import re

        def fix_escaped_newlines(s: str) -> str:
            """修复过度转义的换行符"""
            # 处理多层转义: \\\\n -> \n, \\n -> \n
            s = s.replace('\\\\\\\\n', '\n')
            s = s.replace('\\\\n', '\n')
            # 处理可能的 \\r\\n
            s = s.replace('\\\\\\\\r', '\r')
            s = s.replace('\\\\r', '\r')
            return s

        def normalize_object(value: Any) -> Optional[Dict[str, Any]]:
            """Keep the agent boundary object-shaped across model providers.

            Some OpenAI-compatible models wrap a requested JSON object in a
            one-element array even when ``response_format=json_object`` is set.
            Letting that list escape makes every caller's ``result.get(...)``
            fail and discards the entire research run.  A singleton object
            wrapper is unambiguous and safe to unwrap; any other top-level
            shape is rejected instead of being guessed at.
            """
            value = self._fix_escaped_values(value)
            if isinstance(value, dict):
                return value
            if (
                isinstance(value, list)
                and len(value) == 1
                and isinstance(value[0], dict)
            ):
                self.logger.warning(
                    "LLM returned a singleton JSON array; unwrapped its object"
                )
                return value[0]
            if isinstance(value, list):
                self.logger.warning(
                    "LLM returned an ambiguous top-level JSON array (%d items); "
                    "expected an object",
                    len(value),
                )
            return None

        def try_parse(s: str) -> Optional[Dict[str, Any]]:
            """尝试解析JSON，包含修复逻辑"""
            # 清理常见问题
            s = s.strip()
            # 移除可能的BOM
            if s.startswith('\ufeff'):
                s = s[1:]

            try:
                return normalize_object(json.loads(s))
            except json.JSONDecodeError:
                pass

            # 尝试修复常见JSON问题
            try:
                # 修复无效的JSON转义序列 \[ \] \# 等 (LLM经常产生这种错误)
                # 需要在字符串值中修复，但避免影响已转义的反斜杠
                # \\[ 是有效的(表示 \[ 字面量)，但 \[ 不是有效的JSON转义
                s = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', '', s)
                # 移除注释
                s = re.sub(r'//.*?$', '', s, flags=re.MULTILINE)
                s = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
                # 修复尾随逗号
                s = re.sub(r',(\s*[}\]])', r'\1', s)
                # 修复缺少逗号的情况（在 } 或 ] 后面缺少逗号）
                s = re.sub(r'([}\]])(\s*)([{\[])', r'\1,\2\3', s)
                # 修复没有引号的key
                s = re.sub(r'(\{|\,)\s*(\w+)\s*:', r'\1"\2":', s)
                return normalize_object(json.loads(s))
            except json.JSONDecodeError:
                pass

            return None

        # 1. 先尝试直接解析
        result = try_parse(response)
        if result is not None:
            self.logger.debug("Direct JSON parse succeeded")
            return result

        # 2. 尝试提取markdown代码块
        code_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
        match = re.search(code_block_pattern, response)
        if match:
            result = try_parse(match.group(1))
            if result is not None:
                self.logger.debug("Extracted JSON from code block")
                return result

        # 3. 尝试找到最外层的 {...}
        start = response.find('{')
        end = response.rfind('}')
        if start != -1 and end != -1 and end > start:
            result = try_parse(response[start:end+1])
            if result is not None:
                self.logger.debug("Extracted JSON from braces")
                return result

        # 4. 最后尝试用更宽松的方式解析
        try:
            # 使用 ast.literal_eval 作为备选
            import ast
            # 将 true/false/null 转换为 Python 格式
            s = response
            s = re.sub(r'\btrue\b', 'True', s)
            s = re.sub(r'\bfalse\b', 'False', s)
            s = re.sub(r'\bnull\b', 'None', s)
            start = s.find('{')
            end = s.rfind('}')
            if start != -1 and end != -1:
                result = ast.literal_eval(s[start:end+1])
                if isinstance(result, dict):
                    self.logger.debug("Parsed using ast.literal_eval")
                    return result
        except:
            pass

        self.logger.error(f"JSON parse error, could not extract valid JSON")
        self.logger.warning(f"Raw response (first 800 chars): {response[:800]}")
        return {}

    def _fix_escaped_values(self, obj: Any, key: str = None) -> Any:
        """
        递归修复字典和列表中的转义字符

        注意：对于 'code' 字段，不处理转义，因为代码中的 \n 是有意义的转义序列
        """
        if isinstance(obj, dict):
            return {k: self._fix_escaped_values(v, key=k) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._fix_escaped_values(item, key=key) for item in obj]
        elif isinstance(obj, str):
            # 对于代码字段，不进行转义处理
            # 因为代码中的 \n 应该保持为 \n（两个字符），而不是真正的换行
            if key in ('code', 'fixed_code', 'revised_content'):
                return obj

            # 对于其他字段，修复过度转义的换行符
            result = obj
            result = result.replace('\\\\n', '\n')
            result = result.replace('\\n', '\n')
            result = result.replace('\\\\r', '\r')
            result = result.replace('\\r', '\r')
            result = result.replace('\\\\t', '\t')
            result = result.replace('\\t', '\t')
            return result
        else:
            return obj

    def add_message(self, state: ResearchState, event_type: str, content: Any) -> None:
        """
        添加消息到状态并实时推送（SSE 流式输出）

        ## 为什么这里要认识 LangGraph 的 stream writer（v0.6）

        原作者放弃 LangGraph 的理由是"`astream` 按节点粒度产出，无法从节点
        内部实时流式输出"——这在 langgraph 1.x 已不成立：`get_stream_writer()`
        就是为"从节点内部往外推自定义事件"设计的，实测逐条即时送达，
        不在节点边界批处理。

        `get_stream_writer()` 是上下文局部的：在节点内返回真正的 writer，
        在节点外抛 `RuntimeError`。因此**不需要把 sink 塞进 state 再层层传递**，
        直接取即可——这也避免了把不可序列化的对象写进要落检查点的 state。

        Args:
            state: 研究状态
            event_type: 事件类型
            content: 消息内容
        """
        message = {
            "type": event_type,
            "agent": self.name,
            "timestamp": datetime.now().isoformat(),
            "content": content
        }
        state["messages"].append(message)

        # 路径一：LangGraph 节点内——直接推给 stream writer
        if _get_stream_writer is not None:
            try:
                _get_stream_writer()(message)
                return
            except Exception:
                # 不在 runnable 上下文里（如单测直接调 Agent），落到路径二
                pass

        # 路径二：手写编排的 asyncio.Queue（保留以兼容直接调用 Agent 的测试）
        queue = state.get("_message_queue")
        if queue is not None:
            try:
                queue.put_nowait(message)
            except Exception as e:
                self.logger.warning(f"Failed to push message to queue: {e}")

    def add_log(
        self,
        state: ResearchState,
        action: str,
        input_summary: str,
        output_summary: str,
        duration_ms: int,
        tokens_used: int = 0
    ) -> None:
        """添加执行日志"""
        log = {
            "timestamp": datetime.now().isoformat(),
            "agent": self.name,
            "action": action,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "duration_ms": duration_ms,
            "tokens_used": tokens_used
        }
        state["logs"].append(log)


class AgentRegistry:
    """Agent 注册表"""

    _agents: Dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, agent: BaseAgent) -> None:
        """注册Agent"""
        cls._agents[agent.name] = agent

    @classmethod
    def get(cls, name: str) -> Optional[BaseAgent]:
        """获取Agent"""
        return cls._agents.get(name)

    @classmethod
    def all(cls) -> Dict[str, BaseAgent]:
        """获取所有Agent"""
        return cls._agents.copy()
