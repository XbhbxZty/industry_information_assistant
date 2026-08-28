# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""
DeepResearch V2.0 - 数据极客 Agent (CodeWizard)

职责：
1. 数据清洗 - 统一不同来源的数据口径
2. 统计分析 - 计算关键指标（CAGR、同比等）
3. 预测建模 - 简单的趋势预测
4. 专业绘图 - 生成高质量数据可视化
"""

import uuid
import asyncio
import json
import base64
import io
import sys
from typing import Dict, Any, List, Optional
from datetime import datetime
from contextlib import redirect_stdout, redirect_stderr

from .base import BaseAgent
from ..state import ResearchState, ResearchPhase

try:
    from service import investigation_layer as inv
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service import investigation_layer as inv  # type: ignore


class CodeWizard(BaseAgent):
    """
    数据极客 - 代码与数据分析专家

    特点：
    - 唯一有权执行Python代码的Agent
    - 安全的沙箱执行环境
    - 专业的数据分析和可视化
    """

    ANALYSIS_PROMPT = """你是一位资深的数据分析师，擅长用Python进行数据处理和可视化。

## 研究问题
{query}

## 可用数据
{data_points}

## 任务
根据上述数据，生成Python代码完成以下任务：
1. 数据清洗和标准化
2. 计算关键统计指标
3. 生成专业的可视化图表

## 代码要求（必须严格遵守）

### 0. 禁止使用反斜杠续行（最重要！）
**严禁使用反斜杠 `\\` 进行代码续行**。Python 的字典、列表、函数参数天然支持跨行书写，不需要反斜杠。

✅ 正确示例：
```python
data = {{
    "Year": [2020, 2021, 2022],
    "Value": [100, 200, 300]
}}
df = pd.DataFrame(data)
```

❌ 错误示例（绝对禁止）：
```python
data = {{ \\
    "Year": ...
}}
```

### 1. 数据精简
- **只选取最关键的5-10个数据点**，不要把所有数据都写入代码
- **相同指标去重**：如果有多个年份的同一指标，只保留有代表性的几个
- **代码总长度不超过40行**
- **禁止生成重复数据**：如 [2020, 2020, 2020...] 这种重复是错误的

### 2. 数据定义方式
必须使用"列字典"格式定义数据：
```python
data = {{
    "Year": [2018, 2020, 2022, 2024],
    "Market_Size": [604.2, 1500, 2300, 3000]
}}
df = pd.DataFrame(data)
```

**禁止**使用复杂的嵌套列表 `[[...], [...]]`。

### 3. 数据清洗
创建 DataFrame 后，**必须**执行类型转换：
```python
for col in df.columns:
    if col != 'Year':
        df[col] = pd.to_numeric(df[col], errors='coerce')
df = df.dropna()
```

### 4. 环境限制
- **禁止import语句**，已预定义: pd, np, plt, sns
- **禁止plt.rcParams**，中文字体已预设

### 5. 高级图表样式（必须遵守）
生成专业、高端的商业图表，要求：
- **图表尺寸**: `plt.figure(figsize=(12, 7), dpi=200)`
- **seaborn主题**: `sns.set_theme(style='whitegrid', palette='husl')`
- **标题**: `plt.title('标题', fontsize=18, fontweight='bold', pad=20)`
- **轴标签**: `fontsize=14`
- **刻度**: `fontsize=12`
- **配色**: 使用专业配色如 `#6366f1`（靛蓝）、`#06b6d4`（青色）、`#10b981`（翡翠绿）
- **网格线**: `plt.grid(True, linestyle='--', alpha=0.3)`
- **去除边框**: `sns.despine()`
- **折线图**: `linewidth=2.5, marker='o', markersize=8`，可加面积填充 `plt.fill_between()`
- **柱状图**: 添加数值标签
- **保存**: `plt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')`

## 输出格式（严格JSON，code字段用\\n表示换行）
```json
{{
    "analysis_plan": "简要分析计划",
    "code": "sns.set_theme(style='whitegrid')\\ndata = {{'Year': [2020, 2022, 2024], 'Value': [100, 150, 200]}}\\ndf = pd.DataFrame(data)\\ndf['Value'] = pd.to_numeric(df['Value'], errors='coerce')\\nplt.figure(figsize=(12, 7), dpi=200)\\nplt.plot(df['Year'], df['Value'], linewidth=2.5, marker='o', markersize=8, color='#6366f1')\\nplt.fill_between(df['Year'], df['Value'], alpha=0.15, color='#6366f1')\\nplt.title('市场规模趋势', fontsize=18, fontweight='bold')\\nplt.xlabel('年份', fontsize=14)\\nplt.ylabel('规模（亿元）', fontsize=14)\\nplt.xticks(fontsize=12)\\nplt.yticks(fontsize=12)\\nsns.despine()\\nplt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')",
    "expected_outputs": ["图表描述"]
}}
```

注意：code 字段中的换行请使用 `\\n` 字符表示，不要使用物理换行符，也**绝对不要使用续行符 `\\`**。"""

    CHART_PROMPT = """你是专业的数据可视化专家，擅长制作高端商业图表。

## 主题: {topic}
## 图表类型: {chart_type}
## 标题: {title}

## 数据
{data}

## 代码要求（重要）

### 基础要求
1. **严禁使用反斜杠 `\\` 进行代码续行**
2. **不要写import语句**，已预导入: pd, np, plt, sns
3. 数据定义使用标准字典格式: `data = {{"col1": [...], "col2": [...]}}`

### 高级样式要求（必须遵守）
1. **图表尺寸**: `plt.figure(figsize=(12, 7), dpi=200)`
2. **使用 seaborn 主题**: `sns.set_theme(style='whitegrid', palette='husl')`
3. **标题字体**: `plt.title('标题', fontsize=18, fontweight='bold', pad=20)`
4. **坐标轴标签**: `plt.xlabel('X轴', fontsize=14)` 和 `plt.ylabel('Y轴', fontsize=14)`
5. **刻度字体**: `plt.xticks(fontsize=12)` 和 `plt.yticks(fontsize=12)`
6. **添加数据标签**: 在柱状图或折线图的数据点上显示数值
7. **配色方案**: 使用渐变色或专业配色，如 `color='#6366f1'` 或 `palette='Blues_d'`
8. **网格线**: 使用浅色虚线网格 `plt.grid(True, linestyle='--', alpha=0.3)`
9. **边框优化**: `sns.despine()` 去除上右边框
10. **保存**: `plt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white', edgecolor='none')`

### 折线图额外要求
- 线宽 2.5: `linewidth=2.5`
- 添加数据点标记: `marker='o', markersize=8`
- 添加面积填充: `plt.fill_between(x, y, alpha=0.15)`

### 柱状图额外要求
- 圆角效果（如支持）
- 添加数值标签: `for i, v in enumerate(values): plt.text(i, v + offset, str(v), ha='center', fontsize=11)`

## 输出格式（严格JSON）
```json
{{
    "code": "sns.set_theme(style='whitegrid')\\ndata = {{'Year': [2020, 2022], 'Value': [100, 200]}}\\ndf = pd.DataFrame(data)\\nplt.figure(figsize=(12,7), dpi=200)\\nplt.bar(df['Year'], df['Value'], color='#6366f1')\\nplt.title('标题', fontsize=18, fontweight='bold')\\nplt.xlabel('年份', fontsize=14)\\nplt.ylabel('数值', fontsize=14)\\nplt.xticks(fontsize=12)\\nplt.yticks(fontsize=12)\\nsns.despine()\\nplt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')",
    "chart_description": "图表说明"
}}
```

注意：code字段用 `\\n` 表示换行，**绝对不要使用续行符 `\\`**。"""

    CODE_FIX_PROMPT = """你是一位Python专家，需要修复执行失败的代码。

## 错误类型诊断

请根据错误信息判断错误类型并采取对应修复方法：

1. **如果错误是 `could not convert string to float`**：
   说明你试图将包含中文或特殊字符的列作为数值列处理。
   **修复方法**：在绘图或计算前，使用 `pd.to_numeric(df['col'], errors='coerce')` 清洗该列，并删除 NaN 值。
   不要试图直接画包含中文内容的列（除非是作为标签）。

2. **如果错误是 `SyntaxError`**：
   检查是否有多余的反斜杠或未闭合的括号。

3. **如果错误是 `KeyError`**：
   检查 DataFrame 列名是否正确，确保使用的列名与数据定义一致。

4. **如果错误是类型相关 (`TypeError`)**：
   检查数据类型是否匹配，必要时使用 `.astype()` 或 `pd.to_numeric()` 转换。

## 原始代码
{code}

## 错误信息
{error}

## 输出
{stdout}

## 要求
1. **不要写import语句**，已预导入: pd, np, plt, sns
2. 中文字体已预设
3. 使用"列字典"格式定义数据: `data = {{"col1": [...], "col2": [...]}}`
4. 创建 DataFrame 后立即转换数值列

## 输出格式
```json
{{
    "error_analysis": "错误原因分析",
    "fix_description": "具体修复说明",
    "fixed_code": "data = {{'Year': [2020, 2021], 'Value': [100, 200]}}\\ndf = pd.DataFrame(data)\\ndf['Value'] = pd.to_numeric(df['Value'], errors='coerce')\\nprint('done')"
}}
```"""

    # 词云图专用 Prompt
    WORDCLOUD_PROMPT = """你是一位数据可视化专家，擅长文本分析和词云图制作。

## 研究主题
{topic}

## 文本数据
{text_data}

## 任务
生成专业的词云图代码，展示文本中的高频关键词。

要求：
1. 使用 wordcloud 和 jieba 库
2. 中文分词处理
3. 停用词过滤（去掉"的"、"是"、"在"等常见词）
4. 专业配色方案（推荐使用渐变色）
5. 图表尺寸 (12, 8)
6. 保存图片: plt.savefig('chart.png', dpi=150, bbox_inches='tight', facecolor='white')

输出JSON：
```json
{{
    "code": "完整Python代码",
    "chart_description": "图表说明"
}}
```"""

    # 桑基图专用 Prompt（生成 ECharts 配置）
    SANKEY_PROMPT = """你是一位数据可视化专家，擅长流向图和桑基图制作。

## 研究主题
{topic}

## 流向数据
{flow_data}

## 任务
生成桑基图的 ECharts 配置，展示资金流向或产业链上下游关系。

要求：
1. 生成标准的 ECharts sankey 配置
2. 节点颜色要有区分度
3. 连线要有渐变效果
4. 包含 tooltip 显示详情

输出JSON：
```json
{{
    "echarts_option": {{ ECharts 完整配置对象 }},
    "chart_description": "图表说明"
}}
```"""

    # 关系图专用 Prompt
    NETWORK_PROMPT = """你是一位数据可视化专家，擅长关系图谱制作。

## 研究主题
{topic}

## 关系数据
{relation_data}

## 任务
生成关系图代码，展示实体之间的关联关系。

要求：
1. 使用 matplotlib 绘制网络图（或生成 ECharts graph 配置）
2. 节点大小根据重要性调整
3. 不同类型节点用不同颜色
4. 边的粗细根据关系强度调整
5. 添加节点标签
6. 保存图片: plt.savefig('chart.png', dpi=150, bbox_inches='tight', facecolor='white')

输出JSON：
```json
{{
    "code": "完整Python代码",
    "chart_description": "图表说明"
}}
```"""

    # 允许的模块白名单
    ALLOWED_MODULES = {
        'pandas', 'numpy', 'matplotlib', 'matplotlib.pyplot',
        'seaborn', 'datetime', 'math', 'statistics', 'json',
        'collections', 're',
        # 高级可视化
        'wordcloud',      # 词云图
        'jieba',          # 中文分词
    }

    # 禁止的操作（使用正则表达式匹配）
    FORBIDDEN_PATTERNS = [
        r'\bimport\s+os\b',           # import os
        r'\bimport\s+sys\b',          # import sys
        r'\bimport\s+subprocess\b',   # import subprocess
        r'\bos\.',                    # os.xxx
        r'\bsys\.',                   # sys.xxx
        r'\bsubprocess\.',            # subprocess.xxx
        r'\bopen\s*\(',               # open(
        r'\bexec\s*\(',               # exec(
        r'\beval\s*\(',               # eval(
        r'__import__',                # __import__
        r'\bimport\s+requests\b',     # import requests
        r'\brequests\.',              # requests.xxx
        r'\bimport\s+urllib\b',       # import urllib
        r'\burllib\.',                # urllib.xxx
        r'\bimport\s+socket\b',       # import socket
        r'\bsocket\.',                # socket.xxx
        r'\bimport\s+shutil\b',       # import shutil
        r'\bshutil\.',                # shutil.xxx
        r'\bimport\s+pathlib\b',      # import pathlib
        r'\bpathlib\.',               # pathlib.xxx
        r'\bimport\s+pickle\b',       # import pickle
        r'\bpickle\.',                # pickle.xxx
        r'\bimport\s+glob\b',         # import glob
        r'\bglob\.',                  # glob.xxx
        r'\bcompile\s*\(',            # compile(
        r'\b__builtins__\b',          # __builtins__
        r'\b__globals__\b',           # __globals__
        r'\b__code__\b',              # __code__
    ]

    def __init__(self, llm_api_key: str, llm_base_url: str, model: str = "qwen-max"):
        super().__init__(
            name="CodeWizard",
            role="数据极客",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """处理入口"""
        self.logger.info(f"[CodeWizard] ========== process 开始 ==========")
        self.logger.info(f"[CodeWizard] 当前 phase: {state['phase']}, data_points: {len(state['data_points'])}, outline: {len(state['outline'])}")

        if state.get("due_diligence_mode"):
            return await self._build_investigation_layer(state)

        if state["phase"] != ResearchPhase.ANALYZING.value:
            # 检查是否有需要分析的数据
            if len(state["data_points"]) >= 3:
                state["phase"] = ResearchPhase.ANALYZING.value
                self.logger.info(f"[CodeWizard] 数据点足够，设置 phase 为 ANALYZING")
            else:
                self.logger.warning(f"[CodeWizard] ⚠️ 数据点不足 ({len(state['data_points'])} < 3)，跳过分析")
                return state

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": f"开始数据分析，共有 {len(state['data_points'])} 个数据点..."
        })

        # 执行数据分析
        self.logger.info(f"[CodeWizard] 开始执行 _analyze_data...")
        await self._analyze_data(state)
        self.logger.info(f"[CodeWizard] _analyze_data 完成，当前 charts 数量: {len(state['charts'])}")

        # 生成图表
        self.logger.info(f"[CodeWizard] 开始执行 _generate_charts...")
        await self._generate_charts(state)
        self.logger.info(f"[CodeWizard] _generate_charts 完成，最终 charts 数量: {len(state['charts'])}")

        self.logger.info(f"[CodeWizard] ========== process 结束 ==========")
        return state

    # ------------------------------------------------ 调查层（B 层，阶段 1）

    #: B 层单次抽取的墙钟上界（红线 4）。挂死不能拖住整条流水线——
    #: A 层的评级此刻已经产出，B 层只是加法（计划 9.2）。
    INVESTIGATION_TIMEOUT = 120.0

    #: 探索性抽取的输出预算。**诊断函数要拿它判断截断是否可能**，
    #: 所以不能写成调用点的字面量——两处各写一个数，归因就会骗人。
    INVESTIGATION_MAX_TOKENS = 8000

    async def _build_investigation_layer(self, state: ResearchState) -> ResearchState:
        """构建调查层：确定性图表 + 探索性发现。

        ## 为什么这一步放在 visualize 节点

        `analyze` 节点（DataAnalyst）出评级，`visualize` 排在它之后且被刻意
        分开——"图表生成失败不能连累这家企业是否可授信"（BC-17）。
        B 层继承的正是这条编排：它整个失败，报告少一节，
        等级与额度照常产出（计划 9.2）。

        ## 这个方法不写 A 层任何字段

        `field_checks` / `risk_assessment` / `completeness` / `evidence_store`
        在这里只读不写；所有产出写进 `state["investigation"]`。
        隔离由 `tests/test_investigation_isolation.py` 用 AST 断言，
        不靠这段注释。
        """
        box = inv.get_investigation(state)

        # 一、确定性图表：纯代码解析已核实字段，不调用模型，因而不会失败
        charts, skipped = inv.build_deterministic_charts(state)
        box["charts"].extend(charts)
        for row in skipped:
            inv.record_failure(
                state, f"确定性图表·{row['field_name']}", row["reason"],
                kind="not_found",
            )

        # 二、探索性抽取：一次独立的模型调用，有上界、可关闭、失败不阻断
        if box.get("enabled"):
            await self._run_exploratory_pass(state)
        else:
            inv.record_failure(state, "探索性调查", "本次运行未启用调查层探索性抽取",
                               kind="disabled")

        # 三、跨层一致性判定（阶段 2）。**只观察，不闸门**：
        #     它不写 risk_assessment、不改 requires_human_review。
        #     放在这里是因为 A 层评级（analyze 节点）与 B 层发现此刻都已就绪。
        try:
            from service.cross_layer_verdict import judge_findings
        except ImportError:  # 兼容以 app 为包根的导入方式
            from app.service.cross_layer_verdict import judge_findings
        state["cross_layer_verdict"] = judge_findings(
            box.get("findings") or [], state.get("field_checks") or [])

        # 语料只服务于上面那次抽取。留在 state 里会让每次尽调的检查点
        # 随语料线性膨胀，而它对报告与审计都没有价值——原文的权威副本
        # 在知识库里，证据窗口在证据库里。
        state["raw_sources"] = []

        cross = state.get("cross_layer_verdict") or {}
        stats = {"charts_count": len(box["charts"]),
                 "findings_count": len(box["findings"]),
                 "entities_count": len(box["graph"].get("nodes") or []),
                 "challenges_count": (cross.get("counts") or {}).get("challenges", 0)}
        self.add_message(state, "investigation", {"agent": self.name, **box})
        if cross:
            self.add_message(state, "cross_layer_verdict",
                             {"agent": self.name, **cross})
        self.add_message(state, "research_step", {
            "agent": self.name, "step_type": "analyzing", "title": "调查层",
            "subtitle": (f"确定性图表 {len([c for c in box['charts'] if c['chart_class'] == inv.CHART_DETERMINISTIC])} 张，"
                         f"探索性发现 {len(box['findings'])} 条（不参与授信裁决）"),
            "status": "completed", "stats": stats,
        })
        self.logger.info(f"[CodeWizard] 调查层完成：{stats}")
        return state

    #: 输出侧的**实测**字/token 比。
    #:
    #: 2026-08-23 探针 7 次调用逐条量出来的：
    #:
    #:     1752 字 / 623 tok = 2.81      2945 字 / 1071 tok = 2.75
    #:     2960 字 / 1029 tok = 2.88     2972 字 / 1037 tok = 2.87
    #:
    #: 上一版按 1.5 估算，于是 `near_budget` 的门槛只有 7200 字，
    #: 而真实上界约 8000 × 2.8 ≈ 22400 字——**归因会把远未到顶的响应
    #: 说成被上界切断**，正是 BC-76 要消除的那类错话。
    #:
    #: ⚠️ **不要拿这个数去算输入侧。** 输入的限是另一个量纲：
    #: 实测 47054 字撞 400（`input length should be [1, 30720]`）、
    #: 23335 字通过，说明输入近似 1 字 1 token。两侧分开定
    #: （输入预算见 `scout.INVESTIGATION_INPUT_CHAR_BUDGET`）。
    OUTPUT_CHARS_PER_TOKEN = 2.8

    #: 响应长度达到输出预算的这个比例时，才可能是被 `max_tokens` 切断的。
    TRUNCATION_LENGTH_RATIO = 0.6

    def _diagnose_unparseable(self, raw: str, finish_reason: str = "") -> tuple:
        """给解析失败一个**站得住的**归因，并留下足以推翻它的证据（BC-76）。

        ## 这个函数是被自己的错误归因逼出来的

        上一版一律写「响应疑似被 max_tokens 截断」，判据只是括号不配平。
        阶段 2 观察期实测两轮：响应 1948 字与 3546 字，而输出预算是
        8000 token（约 12000 字）——**离上界差一个数量级，不可能是它**。

        更麻烦的是留痕只存了开头 120 字，而**截断的证据在结尾**：
        那条错误归因既不成立、又无法被留痕证伪。

        ## 三条互斥的归因

        括号配平 → 根本不是 JSON（模型答非所问）
        括号不平 + 接近上界 → 大概率真的是 max_tokens
        括号不平 + 远低于上界 → 响应不完整，但另有成因（提前停、网络截断…）

        第三条刻意**不猜**具体成因。写一个猜的成因，下一个人会顺着它去查。
        """
        budget_chars = self.INVESTIGATION_MAX_TOKENS * self.OUTPUT_CHARS_PER_TOKEN
        unbalanced = raw.count("{") > raw.count("}")
        near_budget = len(raw) >= budget_chars * self.TRUNCATION_LENGTH_RATIO

        if not unbalanced:
            reason = "调查层抽取的响应不是可解析的 JSON（括号配平，非截断）"
        elif finish_reason == "length":
            # **供应商自己说的**，比任何长度启发式都可靠。
            reason = "调查层抽取的响应被输出上界截断（finish_reason=length）"
        elif finish_reason and finish_reason != "length":
            # 模型自己结束了这一轮，却交出不完整的 JSON——
            # 这是指令遵循问题，不是预算问题。**不要归因到 max_tokens**。
            reason = (f"调查层抽取的响应不完整，但模型自报正常结束"
                      f"（finish_reason={finish_reason}）——属指令遵循问题，"
                      f"与输出上界无关")
        elif near_budget:
            reason = "调查层抽取的响应被输出上界截断（括号不配平且接近 max_tokens）"
        else:
            reason = ("调查层抽取的响应不完整，但**远未达输出上界**，"
                      "成因不是 max_tokens；需查模型是否提前停止或链路截断")

        # 头尾都留：截断的证据在结尾，只存开头等于留了个无法证伪的结论。
        #
        # 头尾各取 60 字是算过的：`record_failure` 会把 detail 截到 200 字，
        # 头尾各 90 字会让**结尾正好被切掉**——留痕层默默吃掉了这条修复要交付
        # 的东西，直到测试去查结尾才暴露。诊断信息要按留痕的上限来设计。
        detail = (f"响应 {len(raw)} 字 / 预算约 {budget_chars:.0f} 字；"
                  f"finish={finish_reason or '未提供'}；"
                  f"括号 {raw.count('{')}开 {raw.count('}')}闭；"
                  f"头：{raw[:50]}｜尾：{raw[-50:]}")
        return reason, detail

    async def _run_exploratory_pass(self, state: ResearchState) -> None:
        """对留存语料做一次独立的调查层抽取。

        **与 A 层的抽取是两次调用，不是一次调用多输出几个字段。**
        阶段 1 的验收标准是"A 层等级、额度、核实率逐位不变"；
        改动 A 层那次调用的输出契约就再也无法证明它没变（BC-56 量过
        这类改动对字段抽取的影响）。多花一次调用，换回可证明性。
        """
        sources = state.get("raw_sources") or []
        if not sources:
            inv.record_failure(
                state, "探索性调查",
                "本次没有留存到可供调查层使用的原文语料",
                kind="not_found",
                detail="通常意味着本轮未走本地知识库检索，或检索全部落空",
            )
            return

        prompt = inv.EXPLORATORY_PROMPT.format(
            subject=state.get("company_name") or state.get("subject_name") or "待核实主体",
            as_of=state.get("as_of") or "未设置",
            sources=inv.format_sources_for_prompt(sources),
            max_findings=inv.MAX_FINDINGS,
        )
        try:
            response, meta = await self.call_llm(
                system_prompt=(
                    "你是尽职调查分析师的助手，只负责从给定材料中摘录可溯源的事实。"
                    "你的产出不进入授信决策，不得给出风险判断或结论性评价。"
                ),
                user_prompt=prompt,
                json_mode=True,
                temperature=0.2,
                # 真实语料实测：20 条来源能让模型写出 8884 字的 JSON，
                # 4000 token 会从中间切断，于是整份解析失败（BC-74）。
                # 上界与 `MAX_FINDINGS` 是一对——提示词同时告诉模型条数上限，
                # 让"写不完"这件事在两端都被约束住。
                max_tokens=self.INVESTIGATION_MAX_TOKENS,
                timeout=self.INVESTIGATION_TIMEOUT,
                # 取回 finish_reason：响应不完整时，它是唯一能直接说出
                # "为什么"的字段。没有它，归因只能靠猜（BC-76）。
                return_meta=True,
            )
        except Exception as exc:
            # 「没查成」必须与「查了没有」分开记（BC-51）。合并成一句
            # "本节无内容"，读者就无法判断该不该补查。
            inv.record_failure(state, "探索性调查", "调查层抽取调用失败",
                               kind="error", detail=f"{type(exc).__name__}: {exc}")
            return
        finish_reason = str((meta or {}).get("finish_reason") or "")

        # ⚠️ `parse_json_response` 解析彻底失败时返回的是 **`{}`**，不是 None。
        #
        #    原来那道 `if not isinstance(payload, dict)` 守卫因此**永远不会触发**——
        #    `{}` 是 dict。于是一次截断的、解析不了的响应，一路走到
        #    `ingest_exploratory_payload({})`，产出 0 条发现、0 条拒绝、
        #    **0 条失败留痕**，外观与"材料里确实没东西"完全一致（BC-74）。
        #
        #    这正是 BC-51「查了没有 vs 没查成」在我自己写的代码里的复发，
        #    也是 BC-72「守卫存在 ≠ 守卫正确」的第二例：那行 isinstance
        #    读起来像是在挡解析失败，实际挡不住任何东西。
        payload = self.parse_json_response(response)
        raw = str(response or "").strip()
        if not payload:
            if raw:
                reason, detail = self._diagnose_unparseable(raw, finish_reason)
                inv.record_failure(state, "探索性调查", reason,
                                   kind="error", detail=detail)
            else:
                inv.record_failure(state, "探索性调查", "模型返回空响应",
                                   kind="error")
            return

        stats = inv.ingest_exploratory_payload(state, payload, sources)
        if not stats["findings"] and not stats["rejected"] and not stats["metrics"]:
            # 「查了没有」也要留痕（BC-51）。跑完一无所获与根本没跑，
            # 在报告上都是这一节空着——不写明，读者无从判断该不该补语料。
            inv.record_failure(
                state, "探索性调查",
                f"已就 {len(sources)} 条来源抽取，材料中没有清单以外的可溯源内容",
                kind="not_found")
        self.logger.info(f"[CodeWizard] 调查层探索性抽取：{stats}")

    async def _analyze_data(self, state: ResearchState) -> None:
        """分析数据"""
        if not state["data_points"]:
            self.logger.info("[CodeWizard] 没有数据点，跳过分析")
            return

        # 格式化数据点
        data_summary = []
        for dp in state["data_points"]:
            data_summary.append(f"- {dp.get('name')}: {dp.get('value')} {dp.get('unit', '')} ({dp.get('year', 'N/A')})")

        prompt = self.ANALYSIS_PROMPT.format(
            query=state["query"],
            data_points="\n".join(data_summary)
        )

        self.logger.info(f"[CodeWizard] 调用LLM生成分析代码，数据点数量: {len(state['data_points'])}")

        response = await self.call_llm(
            system_prompt="你是专业的数据分析师，擅长Python数据处理和可视化。",
            user_prompt=prompt,
            json_mode=True
        )

        # ===== 详细日志: LLM原始响应 =====
        self._save_debug_log("1_llm_response", response)
        self.logger.info(f"[CodeWizard] LLM响应长度: {len(response)}, 前200字符: {response[:200]}")

        result = self.parse_json_response(response)

        # ===== 详细日志: JSON解析结果 =====
        self._save_debug_log("2_json_parsed", str(result))
        self.logger.info(f"[CodeWizard] JSON解析结果: {type(result)}, keys: {result.keys() if isinstance(result, dict) else 'N/A'}")

        if result and result.get("code"):
            code = result["code"]

            # ===== 详细日志: 原始code字段 =====
            self._save_debug_log("3_code_raw", repr(code) if isinstance(code, str) else str(code))
            self.logger.info(f"[CodeWizard] 原始code类型: {type(code)}, 长度: {len(str(code))}")

            # 确保 code 是字符串类型
            if isinstance(code, list):
                code = '\n'.join(str(c) for c in code)
                self.logger.info(f"[CodeWizard] code是list，已转换为字符串")
            elif not isinstance(code, str):
                code = str(code)
                self.logger.info(f"[CodeWizard] code不是字符串，已转换")

            # ===== 详细日志: 清理前的code =====
            self._save_debug_log("4_code_before_clean", code)

            # 先进行基础清理（处理 \n, \[n] 等格式问题）
            cleaned_code = self._clean_code(code)

            # ===== 详细日志: 清理后的code =====
            self._save_debug_log("5_code_after_clean", cleaned_code)
            self.logger.info(f"[CodeWizard] 清理后code行数: {cleaned_code.count(chr(10)) + 1}")

            # ===== 详细日志: 语法检查 =====
            try:
                compile(cleaned_code, '<string>', 'exec')
                self.logger.info(f"[CodeWizard] ✅ 语法检查通过")
                self._save_debug_log("6_syntax_check", "PASSED")
            except SyntaxError as e:
                self.logger.error(f"[CodeWizard] ❌ 语法检查失败: {e}")
                self._save_debug_log("6_syntax_check", f"FAILED: {e}\n\nCode:\n{cleaned_code}")

            # 验证清理后的代码是否有效（过滤掉明显的垃圾代码）
            # 垃圾代码特征：太短、包含HTML片段、没有多行结构
            if len(cleaned_code) < 50 or '">\\' in cleaned_code or cleaned_code.count('\n') < 3:
                self.logger.warning(f"[CodeWizard] 检测到无效代码，跳过执行: {cleaned_code[:100]}...")
                self._save_debug_log("7_validation", f"INVALID: too short or bad format")
                return None

            # 使用清理后的代码
            code = cleaned_code

            # 发送代码事件
            self.add_message(state, "code", {
                "agent": self.name,
                "language": "python",
                "code": code,
                "purpose": result.get("analysis_plan", "数据分析")
            })

            self.logger.info(f"[CodeWizard] 开始执行代码...")

            # 执行代码（带自愈能力）
            execution_result = await self._execute_with_self_correction(
                code,
                state
            )

            # ===== 详细日志: 执行结果 =====
            self._save_debug_log("8_execution_result", str(execution_result))

            # 记录执行结果
            state["code_executions"].append({
                "id": f"exec_{uuid.uuid4().hex[:8]}",
                "code": execution_result.get("final_code", result["code"]),
                "output": execution_result.get("output", ""),
                "error": execution_result.get("error"),
                "charts": execution_result.get("charts", []),
                "retries": execution_result.get("retries", 0),
                "timestamp": datetime.now().isoformat()
            })

            # 发送执行结果
            self.add_message(state, "code_result", {
                "agent": self.name,
                "success": execution_result.get("success", False),
                "output": execution_result.get("output", "")[:500],
                "has_chart": len(execution_result.get("charts", [])) > 0,
                "retries": execution_result.get("retries", 0)
            })

            # 如果生成了图表，发送 chart SSE 事件
            charts_generated = execution_result.get("charts", [])
            if charts_generated:
                for i, chart_b64 in enumerate(charts_generated):
                    chart_entry = {
                        "id": f"chart_analysis_{uuid.uuid4().hex[:8]}",
                        "title": f"数据分析图表 {i+1}",
                        "chart_type": "generated",
                        "image_base64": chart_b64,
                        "section_id": "analysis"
                    }
                    state["charts"].append(chart_entry)

                    # 发送单个图表事件到前端
                    self.add_message(state, "chart", {
                        "agent": self.name,
                        "title": chart_entry["title"],
                        "chart_type": "generated",
                        "image_base64": chart_b64
                    })
                    self.logger.info(f"[CodeWizard] Sent chart event: {chart_entry['title']}")

    async def _execute_with_self_correction(
        self,
        code: str,
        state: ResearchState,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        带自愈能力的代码执行

        特点：
        - 首次执行失败后，将错误信息反馈给LLM修复
        - 最多重试 max_retries 次
        - 记录所有尝试和修复过程
        """
        current_code = code
        retries = 0

        while retries <= max_retries:
            # 执行代码
            result = await self._execute_code(current_code)

            if result.get("success"):
                # 执行成功
                return {
                    "success": True,
                    "output": result.get("output", ""),
                    "charts": result.get("charts", []),
                    "retries": retries,
                    "final_code": current_code
                }

            # 执行失败，尝试修复
            error = result.get("error", "Unknown error")
            stdout = result.get("output", "")

            if retries >= max_retries:
                self.logger.warning(f"Code execution failed after {max_retries} retries: {error}")
                return {
                    "success": False,
                    "error": error,
                    "output": stdout,
                    "charts": [],
                    "retries": retries,
                    "final_code": current_code
                }

            # 发送修复尝试消息
            self.add_message(state, "thought", {
                "agent": self.name,
                "content": f"代码执行失败（第{retries + 1}次），正在自动修复: {error[:100]}..."
            })

            self.logger.info(f"Attempting code self-correction (retry {retries + 1}/{max_retries})")

            # 调用LLM修复代码
            fixed_result = await self._fix_code(current_code, error, stdout)

            if fixed_result and isinstance(fixed_result, dict) and fixed_result.get("fixed_code"):
                fixed_code = fixed_result["fixed_code"]
                # 确保是字符串
                if isinstance(fixed_code, list):
                    fixed_code = '\n'.join(str(c) for c in fixed_code)
                elif not isinstance(fixed_code, str):
                    fixed_code = str(fixed_code)
                current_code = fixed_code
                self.logger.info(f"Code fixed: {fixed_result.get('fix_description', 'N/A')}")

                # 发送修复后的代码
                self.add_message(state, "code_fix", {
                    "agent": self.name,
                    "error_analysis": fixed_result.get("error_analysis", ""),
                    "fix_description": fixed_result.get("fix_description", ""),
                    "retry": retries + 1
                })
            else:
                self.logger.warning("Failed to get fixed code from LLM")
                break

            retries += 1

        return {
            "success": False,
            "error": "Max retries exceeded",
            "output": "",
            "charts": [],
            "retries": retries,
            "final_code": current_code
        }

    async def _fix_code(self, code: str, error: str, stdout: str) -> Optional[Dict]:
        """调用LLM修复代码"""
        prompt = self.CODE_FIX_PROMPT.format(
            code=code,
            error=error,
            stdout=stdout[:1000]  # 限制输出长度
        )

        try:
            response = await self.call_llm(
                system_prompt="你是Python代码调试专家，擅长分析错误并修复代码。",
                user_prompt=prompt,
                json_mode=True,
                temperature=0.2
            )
            return self.parse_json_response(response)
        except Exception as e:
            self.logger.error(f"Code fix LLM call failed: {e}")
            return None

    async def _generate_charts(self, state: ResearchState) -> None:
        """为需要图表的章节生成可视化"""
        # 找出需要图表的章节
        chart_sections = [s for s in state["outline"] if s.get("requires_chart")]

        # 如果没有明确标记需要图表的章节，使用前2个章节作为备选
        if not chart_sections and state["outline"]:
            chart_sections = state["outline"][:2]
            self.logger.info(f"[CodeWizard] 没有 requires_chart 章节，使用前2个章节生成图表")

        self.logger.info(f"[CodeWizard] 开始生成图表，需要图表的章节数: {len(chart_sections)}")

        for i, section in enumerate(chart_sections[:2]):  # 最多生成2个图表
            self.logger.info(f"[CodeWizard] 处理章节 {i+1}/{min(len(chart_sections), 2)}: '{section['title']}'")

            # 收集相关数据
            section_data = self._get_section_data(state, section["id"])
            self.logger.info(f"[CodeWizard] 章节 '{section['title']}' 数据量: {len(section_data)}")

            if not section_data:
                self.logger.warning(f"[CodeWizard] ⚠️ 章节 '{section['title']}' 没有数据，跳过")
                continue

            # 生成图表代码
            self.logger.info(f"[CodeWizard] 调用 LLM 生成图表代码...")
            chart_config = await self._generate_chart_code(
                topic=section["title"],
                data=section_data,
                chart_type="bar" if section.get("section_type") == "quantitative" else "line",
                title=f"{section['title']}分析"
            )

            if chart_config and chart_config.get("code"):
                chart_code = chart_config["code"]
                self._save_debug_log(f"chart_{section['id']}_raw", repr(chart_code))
                self.logger.info(f"[CodeWizard] ✅ 生成图表代码成功，长度: {len(chart_code)}")

                self.add_message(state, "code", {
                    "agent": self.name,
                    "language": "python",
                    "code": chart_code,
                    "purpose": f"生成图表: {section['title']}"
                })

                # 执行并获取图表
                self.logger.info(f"[CodeWizard] 执行图表代码...")
                result = await self._execute_code(chart_code)
                self._save_debug_log(f"chart_{section['id']}_result", str(result))

                if result.get("charts"):
                    self.logger.info(f"[CodeWizard] ✅ 图表执行成功，生成了 {len(result['charts'])} 个图表")
                    chart_entry = {
                        "id": f"chart_{uuid.uuid4().hex[:8]}",
                        "title": section["title"],
                        "chart_type": "generated",
                        "data": section_data,
                        "code": chart_config["code"],
                        "image_base64": result["charts"][0] if result["charts"] else None,
                        "section_id": section["id"]
                    }
                    state["charts"].append(chart_entry)
                    self.logger.info(f"[CodeWizard] 图表已添加到 state['charts']，当前总数: {len(state['charts'])}")

                    self.add_message(state, "chart", {
                        "agent": self.name,
                        "title": section["title"],
                        "chart_type": "generated",
                        "image_base64": result["charts"][0] if result["charts"] else None
                    })
                    self.logger.info(f"[CodeWizard] ✅ 已发送 chart SSE 事件: {section['title']}")
                else:
                    self.logger.warning(f"[CodeWizard] ⚠️ 图表执行失败或没有生成图表: success={result.get('success')}, error={result.get('error', 'N/A')[:100]}")
            else:
                self.logger.warning(f"[CodeWizard] ⚠️ LLM 没有返回有效的图表代码")

    def _get_section_data(self, state: ResearchState, section_id: str) -> List[Dict]:
        """获取章节相关数据"""
        related_facts = [f for f in state["facts"] if section_id in f.get("related_sections", [])]
        related_data = []

        for fact in related_facts:
            # 从facts中提取数据点
            if "data_points" in fact:
                related_data.extend(fact["data_points"])

        # 补充全局数据点
        for dp in state["data_points"][:10]:
            related_data.append(dp)

        return related_data

    async def _generate_chart_code(
        self,
        topic: str,
        data: List[Dict],
        chart_type: str,
        title: str
    ) -> Optional[Dict]:
        """生成图表代码"""
        data_str = json.dumps(data, ensure_ascii=False, indent=2)

        prompt = self.CHART_PROMPT.format(
            topic=topic,
            data=data_str,
            chart_type=chart_type,
            title=title
        )

        response = await self.call_llm(
            system_prompt="你是数据可视化专家。",
            user_prompt=prompt,
            json_mode=True
        )

        return self.parse_json_response(response)

    def _clean_code(self, code: str) -> str:
        """
        清理LLM生成的代码，修复常见格式问题

        完全重写版本：使用字符级处理来正确区分行分隔符和字符串内的 \\n
        这解决了 "unexpected character after line continuation character" 错误
        """
        import re

        # 移除markdown代码块标记
        code = re.sub(r'^```python\s*', '', code, flags=re.MULTILINE)
        code = re.sub(r'^```\s*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'^```json\s*', '', code, flags=re.MULTILINE)
        # 移除行末的 ``` (有时 LLM 会把结束标记粘在最后一行代码后面)
        code = re.sub(r'```\s*$', '', code)

        # 如果代码已经是正常的多行格式（有真正的换行符，没有转义的 \n）
        if '\n' in code and '\\n' not in code:
            lines = code.split('\n')
            cleaned_lines = [line.rstrip() for line in lines]
            return '\n'.join(cleaned_lines).strip()

        # 处理 JSON 编码导致的转义问题
        # 先处理 JSON 转义的引号 \" -> "
        code = code.replace('\\"', '"')

        # 用一个不太可能出现的占位符
        placeholder = "___NL_PLACEHOLDER___"

        # 保护字符串字面量内的 \n
        def protect_strings(text):
            result = []
            i = 0
            while i < len(text):
                # 检测 f-string, r-string 等前缀
                if i < len(text) - 1 and text[i] in 'fFrRbBuU' and text[i+1] in '"\'':
                    quote = text[i+1]
                    result.append(text[i])
                    result.append(quote)
                    i += 2
                    # 读取直到结束引号
                    while i < len(text):
                        if text[i] == '\\' and i + 1 < len(text):
                            if text[i+1] == 'n':
                                result.append(placeholder)
                                i += 2
                            elif text[i+1] == quote:
                                result.append(text[i:i+2])
                                i += 2
                            elif text[i+1] == '\\':
                                # 双反斜杠
                                result.append(text[i:i+2])
                                i += 2
                            else:
                                result.append(text[i])
                                i += 1
                        elif text[i] == quote:
                            result.append(text[i])
                            i += 1
                            break
                        else:
                            result.append(text[i])
                            i += 1
                elif text[i] in '"\'':
                    quote = text[i]
                    result.append(quote)
                    i += 1
                    # 读取直到结束引号
                    while i < len(text):
                        if text[i] == '\\' and i + 1 < len(text):
                            if text[i+1] == 'n':
                                result.append(placeholder)
                                i += 2
                            elif text[i+1] == quote:
                                result.append(text[i:i+2])
                                i += 2
                            elif text[i+1] == '\\':
                                # 双反斜杠
                                result.append(text[i:i+2])
                                i += 2
                            else:
                                result.append(text[i])
                                i += 1
                        elif text[i] == quote:
                            result.append(text[i])
                            i += 1
                            break
                        else:
                            result.append(text[i])
                            i += 1
                else:
                    result.append(text[i])
                    i += 1
            return ''.join(result)

        protected = protect_strings(code)

        # 现在处理行分隔符
        # 先处理各种异常格式（LLM 可能产生的非标准换行标记）
        import re as re_module

        # 1. LaTeX 风格: \[10pt], \\[10pt], \[12pt] 等
        protected = re_module.sub(r'\\\\?\[\d+pt\]\s*', '\n', protected)

        # 2. 中文标记: \[换行], \\[换行], [换行] 等
        protected = re_module.sub(r'\\\\?\[换行\]\s*', '\n', protected)
        protected = protected.replace('[换行]', '\n')

        # 3. \[n] 或 \\[n] 格式
        protected = protected.replace('\\\\[n]', '\n')
        protected = protected.replace('\\[n]', '\n')

        # 4. 修复注释后面粘连代码的问题
        # 例如: "# 数据准备 data = [" 应该变成 "# 数据准备\ndata = ["
        # 检测模式: # 注释文字 后面跟着 Python 关键字/变量赋值
        protected = re_module.sub(
            r'(#[^\n]*?)\s+(import |from |def |class |if |for |while |data\s*=|df\s*=|plt\.|fig\s*=|ax\s*=)',
            r'\1\n\2',
            protected
        )

        # 5. 修复多个语句粘连在一行的问题
        # 例如: "...] plt.rcParams" 应该变成 "...]\nplt.rcParams"
        # 只在特定结束符后分割，避免破坏 "fig, ax =" 这种模式
        # 匹配: ) 或 ] 或 ' 或 " 或 True/False/None 后面跟空格和新语句
        protected = re_module.sub(
            r"([\]\)'\"]|True|False|None)\s+(plt\.|fig\s*,|fig\s*=|ax\.|df\s*=|data\s*=|global_data|china_data|line\d)",
            r'\1\n\2',
            protected
        )

        # \\\\n -> \n (四重转义)
        # \\n -> \n (双重转义)
        # \n -> 换行 (单重转义 - 这是行分隔符)
        protected = protected.replace('\\\\\\\\n', '\n')
        protected = protected.replace('\\\\n', '\n')
        protected = protected.replace('\\n', '\n')

        # 6. 修复 LLM 输出 \\n\\xxx 的问题（换行后多加了反斜杠）
        # 例如: "False\\n\\data = [" 经过上面处理后变成 "False\n\data = ["
        # 需要去掉行首的反斜杠（不是转义序列的情况）
        # 匹配: 行首的反斜杠后跟变量名赋值，如 \data = [
        protected = re_module.sub(r'^\\([a-zA-Z_])', r'\1', protected, flags=re_module.MULTILINE)
        # 也处理不在行首但在空格后的情况
        protected = re_module.sub(r'(\s)\\([a-zA-Z_][a-zA-Z0-9_]*\s*=)', r'\1\2', protected)

        # 恢复字符串内的 \n
        protected = protected.replace(placeholder, '\\n')

        # 修复方括号转义
        protected = protected.replace('\\[', '[')
        protected = protected.replace('\\]', ']')

        # 修复行末的反斜杠问题和移除不需要的语句
        lines = protected.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            # 移除 import 语句（沙箱已预导入）
            if stripped.startswith('import ') or stripped.startswith('from '):
                continue
            # 移除 plt.rcParams 设置（沙箱已预设）
            if 'plt.rcParams' in stripped:
                continue
            # 移除行末的 ```
            line = re.sub(r'```\s*$', '', line)

            # ========== 核心修复：移除所有行尾的续行符（反斜杠） ==========
            # 这是"手术刀式"修复，直接把行尾的 \ 及其后的空白删掉
            # 对于 Python 字典 `data = {` 来说，后面没有 \ 也完全合法
            # （Python 支持括号内的自然换行），所以这不会破坏代码逻辑
            # 但能完美解决 "unexpected character after line continuation" 错误
            line = re.sub(r'\\\s*$', '', line)

            line = line.rstrip()
            cleaned_lines.append(line)

        result = '\n'.join(cleaned_lines).strip()
        # 最终清理：移除开头和结尾可能残留的代码块标记
        result = re.sub(r'^```\w*\s*', '', result)
        result = re.sub(r'```\s*$', '', result)
        return result.strip()

    def _save_debug_info(self, raw_code: str, cleaned_code: str, error: Exception = None):
        """保存调试信息到文件，方便排查问题"""
        import os
        from datetime import datetime

        debug_dir = "/tmp/codewizard_debug"
        os.makedirs(debug_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 保存原始代码
        has_escaped_n = '\\n' in raw_code
        has_real_newline = '\n' in raw_code
        with open(f"{debug_dir}/raw_code_{timestamp}.txt", "w") as f:
            f.write("=" * 60 + "\n")
            f.write("原始代码分析\n")
            f.write("=" * 60 + "\n")
            f.write(f"长度: {len(raw_code)} 字符\n")
            f.write(f"包含 \\n 字面量: {has_escaped_n}\n")
            f.write(f"包含真正换行: {has_real_newline}\n")
            f.write("\n--- 原始代码 (repr) ---\n")
            f.write(repr(raw_code))
            f.write("\n\n--- 原始代码 (raw) ---\n")
            f.write(raw_code)

        # 保存清理后代码
        with open(f"{debug_dir}/cleaned_code_{timestamp}.txt", "w") as f:
            f.write("=" * 60 + "\n")
            f.write("清理后代码\n")
            f.write("=" * 60 + "\n")
            f.write(f"长度: {len(cleaned_code)} 字符\n")
            f.write("\n--- 清理后代码 ---\n")
            f.write(cleaned_code)

            if error:
                f.write("\n\n" + "=" * 60 + "\n")
                f.write(f"语法错误: {error}\n")
                if hasattr(error, 'lineno') and error.lineno:
                    lines = cleaned_code.split('\n')
                    f.write(f"错误行号: {error.lineno}\n")
                    f.write("\n--- 问题代码上下文 ---\n")
                    start = max(0, error.lineno - 3)
                    end = min(len(lines), error.lineno + 2)
                    for i in range(start, end):
                        marker = ">>> " if i == error.lineno - 1 else "    "
                        f.write(f"{marker}Line {i+1}: {repr(lines[i])}\n")

        # 保存最新的调试文件路径（方便快速访问）
        with open(f"{debug_dir}/latest.txt", "w") as f:
            f.write(f"raw: {debug_dir}/raw_code_{timestamp}.txt\n")
            f.write(f"cleaned: {debug_dir}/cleaned_code_{timestamp}.txt\n")
            f.write(f"timestamp: {timestamp}\n")

        self.logger.info(f"[CodeWizard] 调试信息已保存到 {debug_dir}/")

    def _save_debug_log(self, step_name: str, content: str):
        """
        保存单步调试日志，便于追踪代码执行流程

        每次运行会创建一个带时间戳的目录，所有步骤保存在同一目录下
        """
        import os
        from datetime import datetime

        # 使用实例变量保存当前调试会话的目录
        if not hasattr(self, '_debug_session_dir'):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._debug_session_dir = f"/tmp/codewizard_debug/session_{timestamp}"
            os.makedirs(self._debug_session_dir, exist_ok=True)
            self.logger.info(f"[CodeWizard] 调试会话目录: {self._debug_session_dir}")

        # 保存步骤日志
        file_path = f"{self._debug_session_dir}/{step_name}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"=== {step_name} ===\n")
            f.write(f"时间: {datetime.now().isoformat()}\n")
            f.write(f"长度: {len(content)} 字符\n")
            f.write("=" * 60 + "\n\n")
            f.write(content)

        self.logger.debug(f"[CodeWizard] 已保存: {file_path}")

    async def _execute_code(self, code: str) -> Dict[str, Any]:
        """
        安全执行Python代码

        特点：
        - 代码清理和格式修复
        - 代码安全检查
        - 隔离执行环境
        - 捕获输出和图表
        """
        self.logger.info(f"[CodeWizard] _execute_code 开始，输入类型: {type(code)}")

        # 确保 code 是字符串类型
        if isinstance(code, list):
            code = '\n'.join(str(c) for c in code)
            self.logger.info(f"[CodeWizard] 输入是list，已转换为字符串")
        elif not isinstance(code, str):
            code = str(code)
            self.logger.info(f"[CodeWizard] 输入非字符串，已转换")

        raw_code = code  # 保存原始代码用于调试
        self._save_debug_log("exec_1_input_raw", repr(raw_code))

        # 清理代码
        code = self._clean_code(code)
        self._save_debug_log("exec_2_after_clean", code)
        self.logger.info(f"[CodeWizard] 清理后代码行数: {code.count(chr(10)) + 1}, 长度: {len(code)}")

        # 语法预检查
        syntax_error = None
        try:
            compile(code, '<string>', 'exec')
            self.logger.info(f"[CodeWizard] ✅ 语法预检查: 通过")
            self._save_debug_log("exec_3_syntax", "PASSED")
        except SyntaxError as e:
            syntax_error = e
            self.logger.error(f"[CodeWizard] ❌ 语法预检查失败: {e}")
            self._save_debug_log("exec_3_syntax", f"FAILED: {e}\n\n错误行: {e.lineno}\n\n代码:\n{code}")
            # 保存调试信息
            self._save_debug_info(raw_code, code, e)

        # 安全检查
        if not self._is_code_safe(code):
            return {
                "success": False,
                "error": "Code contains forbidden operations",
                "output": "",
                "charts": []
            }

        try:
            # 在线程池中执行代码
            result = await asyncio.to_thread(self._execute_in_sandbox, code)
            return result
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return {
                "success": False,
                "error": str(e),
                "output": "",
                "charts": []
            }

    def _is_code_safe(self, code: str) -> bool:
        """检查代码安全性（使用正则表达式）"""
        import re

        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                self.logger.warning(f"Forbidden pattern detected: {pattern}")
                return False

        return True

    def _execute_in_sandbox(self, code: str) -> Dict[str, Any]:
        """
        沙箱执行代码

        注意：这是一个简化的沙箱，生产环境应使用更安全的方案
        如 Docker 容器或专门的代码执行服务
        """
        self.logger.info(f"[CodeWizard] _execute_in_sandbox 开始执行")
        self._save_debug_log("sandbox_1_code_input", code)

        import matplotlib
        matplotlib.use('Agg')  # 非交互式后端
        import matplotlib.pyplot as plt

        # 预导入所有允许的模块
        import pandas as pd
        import numpy as np
        import seaborn as sns
        import datetime
        import math
        import statistics
        import json as json_module
        import collections
        import re as re_module

        self.logger.info(f"[CodeWizard] 沙箱环境准备完成，matplotlib backend: {matplotlib.get_backend()}")

        # 白名单基础模块
        allowed_base_modules = [
            'pandas', 'numpy', 'matplotlib', 'seaborn',
            'datetime', 'math', 'statistics', 'json', 'collections', 're'
        ]

        # 保存原始的 __import__ 函数
        import builtins
        original_import = builtins.__import__

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            """安全的 import 函数，只允许白名单模块"""
            # 检查模块是否在白名单中
            base_module = name.split('.')[0]
            if base_module in allowed_base_modules:
                # 使用真实的 import 来处理（这样可以正确处理 fromlist）
                return original_import(name, globals, locals, fromlist, level)
            raise ImportError(f"Import of '{name}' is not allowed in sandbox")

        # 准备执行环境
        exec_globals = {
            '__builtins__': {
                '__import__': safe_import,
                'print': print,
                'len': len,
                'range': range,
                'enumerate': enumerate,
                'zip': zip,
                'map': map,
                'filter': filter,
                'sorted': sorted,
                'sum': sum,
                'min': min,
                'max': max,
                'abs': abs,
                'round': round,
                'int': int,
                'float': float,
                'str': str,
                'list': list,
                'dict': dict,
                'tuple': tuple,
                'set': set,
                'bool': bool,
                'True': True,
                'False': False,
                'None': None,
                'isinstance': isinstance,
                'type': type,
                'getattr': getattr,
                'setattr': setattr,
                'hasattr': hasattr,
                'callable': callable,
                'iter': iter,
                'next': next,
                'reversed': reversed,
                'slice': slice,
                'all': all,
                'any': any,
                'chr': chr,
                'ord': ord,
                'hex': hex,
                'bin': bin,
                'oct': oct,
                'pow': pow,
                'divmod': divmod,
                'format': format,
                'repr': repr,
                'hash': hash,
                'id': id,
                'input': lambda *args: '',  # 禁用 input
                'open': None,  # 禁用 open
            },
            # 直接提供模块引用（无需import即可使用）
            'pd': pd,
            'np': np,
            'plt': plt,
            'sns': sns,
            'pandas': pd,
            'numpy': np,
            'matplotlib': matplotlib,
            # 额外的常用模块
            'datetime': datetime,
            'math': math,
            'statistics': statistics,
            'json': json_module,
            'collections': collections,
            're': re_module,
        }

        # 捕获输出
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        charts = []

        try:
            # ========== 预设高级图表样式 ==========
            # 中文字体
            chinese_fonts = [
                'Heiti TC', 'STHeiti', 'PingFang HK', 'Hiragino Sans GB',
                'SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'DejaVu Sans'
            ]
            plt.rcParams['font.sans-serif'] = chinese_fonts
            plt.rcParams['axes.unicode_minus'] = False

            # 高级默认样式
            plt.rcParams['figure.figsize'] = [12, 7]
            plt.rcParams['figure.dpi'] = 200
            plt.rcParams['font.size'] = 12
            plt.rcParams['axes.titlesize'] = 18
            plt.rcParams['axes.titleweight'] = 'bold'
            plt.rcParams['axes.labelsize'] = 14
            plt.rcParams['xtick.labelsize'] = 12
            plt.rcParams['ytick.labelsize'] = 12
            plt.rcParams['legend.fontsize'] = 12
            plt.rcParams['axes.spines.top'] = False
            plt.rcParams['axes.spines.right'] = False
            plt.rcParams['axes.grid'] = True
            plt.rcParams['grid.alpha'] = 0.3
            plt.rcParams['grid.linestyle'] = '--'

            self.logger.info(f"[CodeWizard] 开始 exec()...")
            self._save_debug_log("sandbox_2_before_exec", f"即将执行代码，长度: {len(code)} 字符")

            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                exec(code, exec_globals)

            self.logger.info(f"[CodeWizard] exec() 完成")

            # exec 之后再次强制设置字体（防止 LLM 代码里的 sns.set() 等覆盖）
            plt.rcParams['font.sans-serif'] = [
                'Heiti TC', 'STHeiti', 'PingFang HK', 'Hiragino Sans GB',
                'SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'DejaVu Sans'
            ]
            plt.rcParams['axes.unicode_minus'] = False

            # 检查是否生成了图表
            fig = plt.gcf()
            stdout_value = stdout_capture.getvalue()
            stderr_value = stderr_capture.getvalue()

            self.logger.info(f"[CodeWizard] exec() 输出: stdout={len(stdout_value)}字符, stderr={len(stderr_value)}字符")
            self._save_debug_log("sandbox_3_exec_output", f"stdout:\n{stdout_value}\n\nstderr:\n{stderr_value}")

            if fig.get_axes():
                self.logger.info(f"[CodeWizard] 检测到图表，开始捕获...")
                # 重新应用字体到当前图表的所有文本元素
                chinese_fonts = ['Heiti TC', 'STHeiti', 'PingFang HK', 'Hiragino Sans GB', 'Arial Unicode MS']
                for ax in fig.get_axes():
                    for text in ax.get_xticklabels() + ax.get_yticklabels():
                        text.set_fontfamily(chinese_fonts)
                    if ax.get_title():
                        ax.title.set_fontfamily(chinese_fonts)
                    if ax.get_xlabel():
                        ax.xaxis.label.set_fontfamily(chinese_fonts)
                    if ax.get_ylabel():
                        ax.yaxis.label.set_fontfamily(chinese_fonts)

                buf = io.BytesIO()
                fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
                buf.seek(0)
                chart_b64 = base64.b64encode(buf.read()).decode('utf-8')
                charts.append(chart_b64)
                plt.close(fig)
                self.logger.info(f"[CodeWizard] 图表捕获成功，base64长度: {len(chart_b64)}")
            else:
                self.logger.info(f"[CodeWizard] 未检测到图表")

            result = {
                "success": True,
                "output": stdout_value,
                "error": stderr_value if stderr_value else None,
                "charts": charts
            }
            self._save_debug_log("sandbox_4_result", f"success=True, charts={len(charts)}, output_len={len(stdout_value)}")
            self.logger.info(f"[CodeWizard] ✅ 沙箱执行成功，图表数: {len(charts)}")
            return result

        except Exception as e:
            plt.close('all')
            stdout_value = stdout_capture.getvalue()
            error_msg = str(e)
            self.logger.error(f"[CodeWizard] ❌ 沙箱执行失败: {error_msg}")
            self._save_debug_log("sandbox_4_error", f"error: {error_msg}\n\nstdout:\n{stdout_value}\n\ntraceback:\n{repr(e)}")
            return {
                "success": False,
                "output": stdout_value,
                "error": error_msg,
                "charts": []
            }
