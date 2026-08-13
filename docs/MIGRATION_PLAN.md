# 场景迁移：行业信息助手 → 贷前企业尽职调查系统

> 迁移计划存档 **v3（2026-08-08，业务场景转向后重写）**
>
> 配套文档：
> - [`DESIGN_CORE_MECHANISMS.md`](DESIGN_CORE_MECHANISMS.md) —— **核查清单 / 未核实标记 / 风险评分卡的地基设计，Stage 1-2 编码前必读**
> - [`LEARNING_GUIDE.md`](LEARNING_GUIDE.md) —— 学习路线
> - [`DATA_STRUCTURES.md`](DATA_STRUCTURES.md) —— 原项目数据结构

## 修订历史

| 版本 | 变更 |
|---|---|
| v1 | Sonnet 制定，方向为"学术文献研究助手" |
| v2 | Opus 评审：核实 v1 事实断言全部成立；新增 Stage 0（安全基线）与作品集交付物；修正"删除 bidding/stock 会静默破坏 V1"的矛盾 |
| **v3** | **业务场景转向"贷前企业尽职调查"；目标岗位明确为 AI 应用/大模型应用开发；新发现 LangGraph 编排为死代码（见 0.5），项目技术叙事重构** |

---

## 〇、恢复开发环境（下次回来先看这里）

```bash
# 1. 起中间件（6 个容器）
cd D:/pythoncode/industry_information_assistant/industry_information_assistant
docker compose up -d
```
```bash
# 2. 起后端（专属环境，conda 在 E:\Anaconda3 但不在 PATH）
cd backend && F:/conda_envs/dd-assistant/python.exe app/app_main.py
```
```bash
# 3. 起前端
cd frontend && npm run dev
```

| 项 | 值 |
|---|---|
| Python 环境 | `F:\conda_envs\dd-assistant`（3.11.15，专用，勿动已有的 `agent`/`rag`） |
| 后端 | http://localhost:8000 ，API 文档 `/docs` |
| 前端 | **http://localhost:5183** （不是 README 写的 5173） |
| 测试账号 | `ddtest` / `test123456` |
| 当前分支 | `migration/due-diligence`（`main` 保持迁移前基线 `e11ea53`） |
| 执行顺序 | 以 [`ITERATION_ROADMAP.md`](ITERATION_ROADMAP.md) 为准，本文档是文件级改动清单 |

> 注意：用 PowerShell 后台启动服务时**不要**接 `| Select-Object -First N`——达到行数上限会关闭管道并杀掉进程（已踩过一次）。

---

## 一、项目定位与叙事

### 场景
面向**供应链金融 / 小额贷款 / 商业保理公司**的贷前企业尽职调查系统。业务员发起一笔授信申请，系统在 10 分钟内自动完成对借款企业的多源信息核查，产出《贷前尽职调查报告》，交由风控人员复核后进入信贷评审。

### 真实性来源（诚实前提）
本项目**没有真实甲方**，是自驱项目。这一点在 README 和面试中如实说明，不虚构委托方——履历造假的代价远高于收益，而且对 AI 应用岗来说完全没必要。

真实性靠的是**业务约束的真实**，而不是甲方的真实。以下约束来自公开的信贷业务资料，每一条都能解释为什么这么设计：

| 约束 | 业务原因 | 技术后果 |
|---|---|---|
| 报告中每个结论必须可溯源 | 出坏账要追责 | 强制事实-来源绑定 |
| 取不到的信息必须标"未核实" | 不能让模型编造"无涉诉记录" | 显式 unverified 状态机制 |
| 高风险结论必须人工确认 | 风控合规要求 | 人机协同中断/恢复 |
| 报告须导出 Word | 要提交信贷评审会 | 结构化输出 + 模板渲染 |
| 全流程留痕 | 事后审计 | 操作日志 |
| 10 分钟内出初稿 | 业务员在等着回客户 | 并行化 + 模型路由 |

### 技术叙事（面向 AI 应用开发岗）

> **一句话**：在一个不允许幻觉的业务场景里，把 LLM 的不确定性工程化地控制住。

尽调场景的容错率极低——报告里一句编造的"该企业无涉诉记录"就可能导致坏账。所以这个项目要解决的核心技术问题不是"让大模型写长报告"，而是**幻觉控制、事实溯源、可评测**。这恰好是 AI 应用岗面试的深水区，也让"没有真实甲方"这件事不再是短板：选这个场景正是为了做 Agent 工程验证。

三个可深挖的技术亮点：
1. **反幻觉工程**：来源分级 + 强制引用 + 未核实显式标记 + Critic 专项检查
2. **可评测**：模拟数据源自带 ground truth，能做真正的自动化评测（见 Stage 5.1）
3. **编排框架的取舍**：LangGraph 与实时流式输出的冲突及解决（见 0.5）

---

## 二、能力映射（为什么这个场景适配这套代码）

| 现有组件 | 尽调场景中的角色 | 是否真实业务环节 |
|---|---|---|
| Architect 规划 | 生成尽调提纲（按企业类型/授信额度定制） | ✅ 尽调报告有固定章节体系 |
| Scout 检索取证 | 工商/司法/招投标/舆情多源核查 | ✅ 尽调本质就是交叉验证 |
| **知识图谱** | **企业关联关系图谱（股权/担保/上下游）** | ✅ **担保圈风险是监管明确关注项** |
| DataAnalyst | 财务指标计算 + 风险评分卡 | ✅ 风控看数不看文字 |
| Wizard 代码执行 | 财务趋势图、同业对比图 | ✅ 评审会要图 |
| Writer | 尽调报告撰写 | ✅ |
| **Critic 审核** | **风控复核岗** | ✅ **信贷流程里真实存在的一道岗** |
| RAG 知识库 | 上传财报/征信报告/合同解析 | ✅ 甲方材料本来就是 PDF |
| Text2SQL | 财务指标查询、同业对比 | ✅ |
| checkpoint 暂停恢复 | 人工复核卡点 | ✅ |

Critic → 风控复核是最关键的一处映射：原本只是个"检查报告质量"的通用组件，在这里直接对应真实岗位。这种对应关系很难编，也最能证明你懂业务。

---

## Stage 0 — 安全与基线清理（最先执行，与场景无关）

### 0.0 🔴 建立可运行基线（v3.1 补充 —— 原计划疏漏）

**原计划为每个 Stage 都写了验证步骤，却没写"先让它跑起来"。** 已核实当前环境：

| 检查项 | 状态（2026-08-09） |
|---|---|
| Python 环境 | ❌ 无 conda、无 venv；PATH 上为 `D:\新建文件夹\python.exe` (3.12.2) |
| langgraph / pymilvus | ❌ 均未安装 |
| Docker daemon | ❌ 未运行，5 个容器全未启动 |
| git 提交 | ❌ 仓库已存在但 0 次提交 |

没有基线就无法回答"这个问题是我改坏的还是本来就坏的"，改一个 3 万行的陌生项目时这会反复咬人。

**环境定位结果**：conda 在 `E:\Anaconda3`（v26.1.1，不在 PATH），已有环境在 `F:\conda_envs\`（`agent` py3.11 已装大部分依赖 / `agent310` py3.10 空 / `rag` py3.13 / `dl-pytorch`）。为避免污染既有环境，**本项目使用专属环境 `F:\conda_envs\dd-assistant`（Python 3.11）**。

步骤：
1. `conda create -p F:\conda_envs\dd-assistant python=3.11`，`pip install -r backend/requirements.txt`（注意：用 `backend/` 下这份，不是 `backend/app/` 那份——两份内容冲突，后者锁死旧版本且缺 langgraph）
2. 启动 Docker Desktop，`docker compose up -d`，确认 5 个容器健康
3. 后端 `python app/app_main.py` + 前端 `npm run dev` 跑通，走一遍完整研究流程
4. **首次 git 提交作为"迁移前基线"**，此后每 Stage 至少一次提交
5. 记录基线数据：一次完整研究的耗时、token 消耗——这是 Stage 5.2 优化前后对比的"前"
6. **V1 vs V2 对比**（已确认要做）：同一问题分别跑两条链路，记录报告质量/耗时/token，产出"为什么需要多智能体"的一手数据，写进 README。必须在 Stage 0.4 删除 V1 之前完成

### ✅ 基线验证结果（2026-08-09 完成）

环境：`F:\conda_envs\dd-assistant`（Python 3.11.15），依赖装自 `backend/requirements.txt`。

| 项 | 结果 |
|---|---|
| Docker 六容器 | ✅ 全部 healthy |
| 后端启动 | ✅ `http://localhost:8000`，52 个端点 |
| 前端启动 | ✅ **`http://localhost:5183`**（非 README 写的 5173） |
| 认证链路 | ✅ 注册/登录/JWT 签发正常，PostgreSQL 写入正常 |
| 知识库 CRUD | ✅ 创建/列表正常 |
| Milvus 连接 | ✅ pymilvus 3.0.1 → Milvus server v2.3.3 |
| 基线提交 | ✅ `e11ea53`（main 分支，276 文件）；工作分支 `migration/due-diligence` |

实测版本：langgraph **1.2.10** / pymilvus **3.0.1** / fastapi **0.141.1** / pandas **3.0.5** / numpy **2.4.6**。

### ⚠️ 基线暴露的新问题

**pymilvus ORM API 即将失效（新增待办）**
`milvus_service.py` 全文基于 ORM-style API（`connections.connect` / `Collection` / `utility.*`），实测每次调用都抛：
```
PyMilvusDeprecationWarning: ORM-style PyMilvus API and will be removed in
PyMilvus 3.1. Use `MilvusClient` instead.
```
当前 3.0.1 仍可用，但**下个小版本就会断**。建议在 Stage 1 一并迁移到 `MilvusClient`——这也是个可写进 README 的工程化改造点。若暂不改，需在 requirements 中锁 `pymilvus>=2.3,<3.1`。

**其它**：`research_router.py:154` 使用了已废弃的 FastAPI `example` 参数（应改 `examples`），非阻塞。

### 0.1 ✅ 清除源码硬编码 API Key（已完成）
`dr_g.py:29-30` 曾把真实密钥写成 `os.getenv` 默认值，现已改为空字符串默认值。全仓库源码扫描零命中。

### 0.2 git 基线（已部分满足）
~~项目当前非 git 仓库~~ —— **更正：项目已是 git 仓库，但 0 次提交**。`.gitignore:21` 已正确忽略 `.env`，`backend/.env` 与 `frontend/.env` 均不会被追踪，密钥入库风险不存在。剩余动作：完成 0.0 后打首次基线提交。

### 0.3 移除 Elasticsearch
已验证 backend 对 `elasticsearch` 引用数为 **0**，但 docker-compose 仍启动它（占 1GB+）。从 `docker-compose.yml` 和 README 服务列表移除。

### 0.4 删除 V1 ReAct 链路
`tool_executor.py:501,566` 惰性导入 bidding/stock 服务；`research_router.py:159` 的 `version` 参数**默认值就是 v1**。V1 不是死代码而是默认可达路径，且与 V2 是同一功能两套实现。

删除清单：`react_controller.py`(951) / `tool_executor.py`(661) / `dr_g.py`(791) / `bidding_service.py`(340) / `stock_service.py`(270) / `config/stock_mapping.py`(147)、`research_router.py` 的 V1 分支与 `version` 参数、`service/__init__.py` 对应导出。约 3200 行。

> 注：**招投标数据本身在新场景里有用**（中标记录是企业真实经营能力的佐证），保留概念、在 Stage 1 以模拟数据源重建；删的是调用付费 API 的旧 service。股票行情则彻底移除——尽调对象多为中小非上市企业。

> 💡 删除前建议先跑一次 V1 对比 V2，这是理解"为什么需要多智能体"的最佳素材，也是面试可讲的内容。删完就看不到了。

### 0.5 🔴 LangGraph 编排是死代码 —— 需决策

**这是本次评审最重要的发现。**

`graph.py:203-235` 完整构建了 LangGraph 状态机（6 个节点 + 条件边），`workflow.compile()` 也执行了。但 `graph.py:349-356`：

```python
# 始终使用手写版本执行（支持实时SSE流式输出）
# LangGraph 版本会批量处理消息，无法实现实时流式输出
# if LANGGRAPH_AVAILABLE and self.graph:
#     async for event in self._run_with_langgraph(state):
#         yield event
# else:
async for event in self._run_simplified(state):
    yield event
```

**LangGraph 执行路径被整段注释掉了，运行时永远走手写的 `_run_simplified()`。** 后果：
- `_plan_node`~`_revise_node`（237-288 行）和 `_should_continue`（289 行）全是**死代码**
- 控制流被实现了两遍：声明式（死）+ 命令式（`_run_simplified` 里 650/760 行的 while 循环，活）
- **"基于 LangGraph 的多智能体编排"这句话在运行时不成立**。AI 应用岗面试官很可能要求你打开 graph 讲编排——讲到一半发现是注释掉的，是灾难性场景
- 附带问题：`langgraph` 只在 `backend/requirements.txt` 里，`backend/app/requirements.txt` 没有（两份依赖文件内容不一致）

原作者遇到的问题是真实且有价值的：**LangGraph 的 `astream` 按节点粒度产出，无法从节点内部实时流式输出**——而这个产品的体验完全依赖实时 SSE。

**✅ 方案 A 可行性已验证（2026-08-09）。** 在 `F:\conda_envs\agent` 实测 **langgraph 1.2.9**（远高于 requirements 里写的 `>=0.0.20`）：

```
get_stream_writer          OK   ← 节点内实时流式，正是绕过原作者困境的钥匙
astream(stream_mode=)      OK
astream_events             OK
interrupt / Command        OK   ← Stage 3.1 人机协同的原生原语
MemorySaver                OK
PostgresSaver              -    需额外装 langgraph-checkpoint-postgres（本项目自带检查点机制，非必需）
```

**原作者放弃 LangGraph 的理由在 1.x 已不成立。** `get_stream_writer()` 就是为"从节点内部往外推自定义事件"设计的。

**方案 A（推荐，面向 AI 应用岗）：真正修复它。**
用 `get_stream_writer()` + `stream_mode="custom"` 在保留实时 SSE 的前提下恢复真正的图执行。收益：
- 让技术叙事成立，而不是需要回避的地方
- 消除控制流的双份实现
- **这本身就是一个绝佳的面试故事**："我接手时发现编排框架被绕过了，原因是框架的批量流式语义和产品的实时性要求冲突。我用 X 机制解决了，既保留了实时性又恢复了声明式编排。" 这个故事的分量高于项目里任何一个业务功能

**方案 B：诚实地删除。**
删掉死掉的 LangGraph 代码，README 如实写"手写状态机编排 + asyncio.Queue 实时流式"。成本最低，也完全诚实，但放弃了一个高价值的技术亮点，且 AI 应用岗会觉得"没用编排框架"是个减分项。

**建议 A。** 若时间紧张可先做 B 保证不撒谎，有余力再升级到 A。

### Stage 0 验证
- 全仓库 grep `sk-` 源码无命中；`git log` 有干净基线提交
- `docker compose up -d` 起 5 个容器（无 ES）
- 后端启动正常，`/docs` 无 v1 参数
- （若选 A）研究流程走真实 LangGraph 执行且 SSE 仍实时

---

## Stage 1 — 领域建模 + 数据源适配层

### 1.1 新数据模型
新建 `backend/app/models/due_diligence.py`，替换 `models/news.py` 与 `models/industry_data.py`：

| 模型 | 表 | 关键字段 |
|---|---|---|
| `Company` | `companies` | 统一社会信用代码(唯一), 名称, 注册资本, 成立日期, 法定代表人, 注册地址, 经营范围, 企业类型, 登记状态, 参保人数 |
| `Shareholder` | `shareholders` | company_id, 股东名称, 股东类型(自然人/企业), 持股比例, 认缴出资额 |
| `CompanyRelation` | `company_relations` | source_company_id, target_company_id, 关系类型(股权/担保/供应/同一实控人), 关系强度, 数据来源 |
| `JudicialRecord` | `judicial_records` | company_id, 记录类型(涉诉/被执行/失信/股权冻结), 案号, 立案日期, 涉案金额, 案由, 当事人角色 |
| `FinancialRecord` | `financial_records` | company_id, 报告期, 营业收入, 净利润, 总资产, 总负债, 资产负债率, 经营性现金流, 数据来源(审计/自报) |
| `BiddingRecord` | `bidding_records` | company_id, 项目名称, 中标金额, 中标日期, 招标方（由原 `BiddingInfo` 改造） |
| `NegativeNews` | `negative_news` | company_id, 标题, 来源, 发布日期, 负面类型, 严重度（由原 `IndustryNews` 改造） |
| `DueDiligenceReport` | `dd_reports` | company_id, user_id, 授信金额, 状态, 风险等级, 报告正文, 风险评分明细(JSONB), 复核状态, 复核人, 复核意见 |
| `AuditLog` | `audit_logs` | user_id, action, target_type, target_id, detail(JSONB), timestamp |

同步更新 `models/__init__.py` 与 `app_main.py:27-31`。项目无 Alembic（纯 `create_all`），开发库手动 drop 重建。

### 1.2 数据源适配层（关键架构决策）

真实数据源（企查查/天眼查/中国裁判文书网）都是付费或反爬的。设计一层适配器抽象：

```
service/datasource/
├── base.py                    # DataSourceAdapter 抽象基类
├── mock/                      # 模拟数据源（默认启用）
│   ├── business_registry.py   # 工商登记
│   ├── judicial.py            # 司法涉诉
│   ├── bidding.py             # 招投标中标
│   └── public_opinion.py      # 舆情
└── real/                      # 真实适配器（按真实 API 契约写，需付费 Key）
    └── README.md              # 说明各真实数据源的接入方式
```

这一个设计同时解决三个问题：
1. **可运行性**——任何人 clone 下来无需付费 Key 就能跑通完整流程（v2 计划里"面试官跑不起来"的问题在此解决）
2. **可评测性**——模拟数据是我们生成的，**ground truth 已知**，这是 Stage 5.1 自动化评测的前提
3. **诚实且专业**——README 如实说明"真实适配器按公开 API 契约设计但未接入付费服务"，同时展示了接口设计能力

### 1.3 模拟数据生成
`scripts/seed_companies.py`：用强模型批量生成 30-50 家虚构企业的完整档案（工商+股东+司法+财务+中标+舆情+关联关系），要求：
- 覆盖不同风险画像：优质 / 一般 / 高风险（涉诉多、资产负债率高）/ 担保圈（几家企业互相担保）
- **同时产出 ground truth 标注文件**（每家企业的正确风险等级、关键风险点），供 Stage 5 评测使用
- 企业名明确虚构，避免与真实企业重名

### 1.4 Text2SQL
新场景下 Text2SQL 有了扎实的业务理由：财务指标查询、同业对比、担保圈规模统计。重写 `text2sql_service.py` 的 `SCHEMA_DEFINITION`（79-132 行，当前硬编码智慧交通三表 + 虚构示例）指向 `companies/financial_records/judicial_records`，同步更新 408/429/439 行的可视化分支。

**并接入 V2 的 DataAnalyst**（v2 计划已发现：V2 的 5 个 Agent 从不调用 text2sql，它此前只服务于孤立的 `/database` 页面）。

### 1.5 路由
`router/news_router.py` → `company_router.py`（企业档案查询）；新增 `router/dd_router.py`（尽调报告 CRUD + 发起尽调）。`database_router.py` 通用，不改。

### Stage 1 验证
后端启动建出新表；`scripts/seed_companies.py` 灌入模拟企业；Text2SQL 能回答"资产负债率超过 70% 的企业有哪些"。

---

## Stage 2 — Agent 改造与反幻觉工程（核心）

行业措辞分布已验证：architect 12 处、scout 13 处、data_analyst 14 处、writer 9 处、wizard 1 处、**critic 0 处**。

### 2.1 Architect → 尽调提纲
重写 `PLANNING_PROMPT`（32-66 行）与 199 行 system prompt。章节体系固定为尽调报告标准结构：
企业基本情况 / 股权结构与实际控制人 / 经营状况 / 财务分析 / 司法与合规风险 / 关联关系与担保圈 / 舆情扫描 / 风险评级与结论。

与原项目不同：尽调提纲**不应完全自由生成**，而是模板化 + 按授信额度/企业类型微调。这本身是个可讲的设计权衡（业务场景要求可比性，自由生成的报告无法横向对比）。

### 2.2 Scout → 多源核查 + 未核实机制
- **删除**股票钩子 `_fetch_stock_data_if_relevant`（376-462 行）及 207 行调用点
- 检索源从"通用网页搜索"改为**优先调用数据源适配层**，网页搜索降级为舆情补充
- 重写信源可信度分级（119-125 行）为尽调场景的证据等级：
  `官方登记信息(工商/司法) > 审计报告 > 企业自报 > 主流媒体 > 网络传闻`
- **🔑 新增未核实机制**：Scout 取不到的字段必须显式产出
  ```json
  {"field": "实际控制人", "status": "unverified", "reason": "工商数据未披露", "attempted_sources": ["business_registry"]}
  ```
  **严禁用推断填充。** 这是整个反幻觉设计的地基
- 实体类型改为 `company/person/judicial_case/guarantee/supply_relation`，知识图谱同步

### 2.3 DataAnalyst → 风险评分卡 + 关联图谱
- 替换"中国AI市场规模/艾瑞咨询"风格示例（44-95、179-180 行）为财务指标分析
- 新增**风险评分卡**：各维度（财务/司法/经营/关联/舆情）打分 → 综合风险等级。规则可解释，不是让 LLM 直接给分——业务上必须能解释为什么是高风险
- 关联图谱重点识别**担保圈**（环状担保关系检测）
- 接入 Text2SQL 工具调用

#### ⚠️ 评分卡接入主流程（v0.5 功能完成，质量未封板）

评分规则本身见 `service/risk_scorecard.py`（v0.5 早前已完成）。本次是接线，
涉及文件：

| 文件 | 改动 |
|---|---|
| `service/risk_scorecard.py` | 新增 `unratable()`（fail-closed 结果构造）、`render_markdown()`（报告块唯一渲染入口）、`RISK_BLOCK_MARKER`；维度闸门按预期维度全集枚举 |
| `service/company_profile.py` | `verified_profile_mismatches()` 重放清单映射，做字段级清单/档案一致性校验 |
| `service/deep_research_v2/state.py` | `ResearchState` 增 `company_profile`、`risk_assessment` 两个字段 |
| `service/deep_research_v2/graph.py` | `_load_company_profile()` 把原始档案写入 state；`research_complete` 事件抽成 `build_complete_event()` 并携带 `risk_assessment` |
| `agents/data_analyst.py` | 新增 `assess_risk()`：评分 + 写 state + 推 SSE，在 `_analyze_data` 的**所有 LLM 步骤之前**调用 |
| `agents/writer.py` | `SECTION_WRITING_PROMPT` 增 `{risk_scorecard}` 段；`_pin_risk_block()` / `_ensure_risk_block()` 两处代码层收口，最终块始终以规则引擎版本重建；`SYNTHESIS_PROMPT` 增"评级块原样保留"规则 |
| `agents/critic.py` | 扫描器与 LLM 统一审核 `final_report`；扫描器消融开关贯穿执行/注入/合并/过滤 |
| `tests/test_risk_integration.py` | 20 条**行为断言**（区别于 `test_risk_scorecard.py` 的规则正确性断言） |

关键设计与踩坑见 [`DESIGN_CORE_MECHANISMS.md`](DESIGN_CORE_MECHANISMS.md) 第四节、
[`BADCASES.md`](BADCASES.md) BC-19～BC-29；v3 消融实验见
[`backend/eval/ABLATION_V3.md`](../backend/eval/ABLATION_V3.md)。

第二套独立盲测首次运行后，完整架构出现稳定漏检与稳定误报，因此 v0.5 尚未质量
封板。具体结果及原始证据见
[`backend/eval/BLIND_V2_SEAL_REPORT.md`](../backend/eval/BLIND_V2_SEAL_REPORT.md)，
新增问题见 BC-28、BC-29。修复前不得进入“已封板”状态。

> 关联图谱与 Text2SQL 两项仍未做，属 v0.6 范围。

### 2.4 Wizard → 财务图表
替换 122 行的"市场规模趋势"示例为资产负债率趋势、同业对比。matplotlib 规范部分保留。

### 2.5 Writer → 尽调报告
- system prompt（28/33/322 行）从"投行首席分析师"改为尽调报告撰写规范
- 引用格式（79-112 行）改为尽调场景：每个事实性陈述后标注数据来源与获取时间
- **强制规则**：未核实字段必须原样渲染为"未核实（原因：…）"，禁止推断、禁止省略

### 2.6 Critic → 风控复核（改动最大的一个 Agent）
原本零行业措辞、无需改动——但在新场景下它要**升级**成真正的风控复核：
- 新增 issue_type：`unverified_as_fact`（把未核实字段当事实断言）、`unsupported_risk_conclusion`（风险结论无证据支撑）、`missing_mandatory_section`（缺必查项）
- 新增输出：**高风险结论清单**，标记需人工确认的条目（对接 Stage 3.1）
- 这是把"报告质检"变成"业务风控岗"的关键改动

### 2.7 事实溯源链路
贯穿改动：`FactRecord` 强制 `source_url + source_name + credibility_score + retrieved_at`；报告正文的每个结论携带 fact_id 引用；前端点击结论可高亮对应来源（Stage 4）。

### Stage 2 验证
对一家已知 ground truth 的模拟高风险企业发起尽调，检查：风险等级判对；未核实字段确实标为未核实而非编造；Critic 抓出人为植入的错误结论。

---

## Stage 3 — 业务约束功能（"像真的"的来源）

这些功能没人为了好玩去做，正因如此它们才是真实业务项目的标志。

### 3.1 人机协同复核
Critic 标记的高风险结论触发**暂停**，等待风控人员确认后继续。基础设施已存在：`checkpoint_service.update_status()` 已支持 `paused` 状态（但当前**无任何代码真正设置它**，是预留未用）。

若 Stage 0.5 选方案 A，可用 LangGraph 的 `interrupt` 机制实现，技术上更漂亮。

### 3.2 报告导出 Word
`python-docx` 已在依赖中。按尽调报告模板渲染，含风险评级页、图表、来源附录。

### 3.3 操作留痕
`AuditLog` 记录：谁发起了哪家企业的尽调、谁复核的、改了什么结论。审计要求，也是权限设计的前提。

### 3.4 风险等级与授信建议
综合评分 → 低/中/高/拒绝，附建议授信额度区间。规则可解释。

---

## Stage 4 — 前端改造

- `pages/news/` → `pages/companies/`（企业档案库）
- `pages/bidding/` → 删除；`components/stock-card/` → 删除
- **新增** `pages/due-diligence/`：发起尽调 → 实时过程 → 报告查看 → 复核操作
- 知识图谱组件重点适配**担保圈可视化**（环状关系高亮）
- **新增事实溯源交互**：报告结论 hover/click → 高亮来源卡片
- `store/industry.ts` → `store/business.ts`（业务线配置：供应链金融/小微信贷/商业保理，对应不同尽调侧重）
- 品牌文案（已逐一定位）：`login.tsx:76,77`、`index/index.tsx:61,63`、`frontend/.env:2` 的 `VITE_TITLE`、`package.json:2` 的 `"name": "gsk"`

---

## Stage 5 — AI 工程化（AI 应用岗的胜负手）

**这一阶段的性价比高于任何业务功能。** 绝大多数作品集项目止步于"能跑"，这里是拉开差距的地方。

### 5.1 自动化评测集 🔑
因为模拟数据是自己生成的，**ground truth 已知**——这是极少数作品集项目能做真评测的前提。

- 构造 20-30 个尽调 case，覆盖不同风险画像
- 评测指标：
  - **事实准确率**：报告中的事实性陈述与数据源的一致率
  - **幻觉率**：编造了数据源中不存在信息的比例
  - **未核实识别率**：该标未核实的字段是否正确标记（漏标 = 危险）
  - **风险等级一致性**：与 ground truth 标注的吻合度
- 输出评测报告，**在 README 里放评测结果表格**

### 5.2 成本与延迟
- 记录每次 run 的 token 消耗、各 Agent 耗时、总成本
- **模型路由**：信息抽取用便宜模型，风险推理用强模型（`llm_config.py` 已有 per-agent 模型配置，是现成的抓手）
- Scout 多源检索**并行化**（当前疑似串行，需确认）
- README 放优化前后对比数据

### 5.3 失败处理与可观测
- 工具失败、JSON 解析失败、模型拒答的降级策略
- `AgentLog` 强化为完整 trace，前端可查看每步的输入输出

---

## Stage 6 — 收尾与交付物

### 6.1 版权文件头
批量替换约 198 个源文件的
```
Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
```
为用户署名（**文本待定**）。新增根目录 `LICENSE`。

### 6.2 环境变量与依赖
- 删除 `JUHE_STOCK_API_KEY` / `BID_APP_*` / `SERPER_API_KEY`
- 保留必填：`DASHSCOPE_API_KEY`、`BOCHA_API_KEY`
- **合并两份 requirements.txt**（`backend/` 与 `backend/app/` 内容不一致，后者缺 langgraph）

### 6.3 README（面向面试官）
1. 一句话定位 + 演示 GIF
2. 业务场景与约束来源（如实说明为自驱项目）
3. 架构图 + 多智能体流程
4. **技术亮点**：反幻觉工程 / 评测结果表格 / 编排框架取舍 / 成本优化数据
5. 快速开始（强调模拟数据源开箱即用）
6. 数据来源说明（模拟 vs 真实）

### 6.4 `docs/ARCHITECTURE.md`
系统设计文档：为什么多智能体、状态机设计、幻觉控制机制、人机协同、失败处理。这份文档本身就是能力证明。

### 6.5 贡献可见性
git 历史按 Stage 分组；README 如实说明基于课程项目二次开发及自己的改造范围。

---

## 三、最小可交付路径（如果时间紧张）

完整计划体量不小。若需要先跑出一个能拿得出手的版本，按这个顺序砍：

**必须做**：Stage 0（全部）→ 1.1/1.2/1.3 → 2.1/2.2/2.5/2.6 → 4（基础页面）→ 5.1（评测）→ 6.3（README）

**可延后**：Text2SQL 接入（1.4）、Wizard 图表（2.4）、Word 导出（3.2）、留痕（3.3）、成本优化（5.2）

**理由**：反幻觉机制（2.2/2.5/2.6）+ 评测（5.1）是这个项目区别于普通作品集的全部价值所在，宁可砍业务功能也不能砍这两块。

---

## 四、待决策事项

| 决策 | 阻塞 | 建议 |
|---|---|---|
| **Python 环境方案** | Stage 0.0 | 见下方"环境说明" |
| **LangGraph：修复还是删除？** | Stage 0.5 | 方案 A（修复）。⚠️ 可行性尚未验证——langgraph 未安装，requirements 里的 `>=0.0.20` 是很老的版本号，推荐方案依赖较新版本的 `get_stream_writer()` / `stream_mode="custom"`。**装好环境后先验证实际 API 再定** |
| ~~「未核实」机制的跨层结构~~ | Stage 2.2 | ✅ 已完成 → [`DESIGN_CORE_MECHANISMS.md`](DESIGN_CORE_MECHANISMS.md) 第一部分 |
| ~~风险评分卡规则~~ | Stage 2.3 | ✅ 已完成 → [`DESIGN_CORE_MECHANISMS.md`](DESIGN_CORE_MECHANISMS.md) 第二部分 |
| 版权署名文本 | Stage 6.1 | — |
| LICENSE 选型 | Stage 6.1 | 取决于授权范围 |
| 项目正式名 | Stage 4 | — |
| 是否部署在线 Demo | Stage 6 | 模拟数据源让部署变得可行，加分明显 |

---

## 五、进度记录

- **2026-08-07**：v1 计划（学术文献方向）。无代码改动。
- **2026-08-08**：v2，Opus 评审，新增 Stage 0 与作品集交付物。无代码改动。
- **2026-08-08**：**v3，业务场景转向贷前尽调**（就业导师建议项目需体现真实业务需求）。目标岗位明确为 AI 应用开发，技术叙事重构为"不允许幻觉的场景下的 LLM 工程化"。新发现 LangGraph 编排为死代码（graph.py:349-356），列为最高优先级决策。仍无代码改动。

---

## v0.6a 进度：核实来源与结构化证据链（2026-08-11，经一轮只读复核返工）

| 项 | 状态 |
|---|---|
| 来源模型（闭集 + legacy 识别） | ✅ `service/verification.py` |
| 受信任适配器注册表 | ✅ `register_adapter()`，写入侧+重放侧双向校验 |
| 结构化证据与**原子**写入口 | ✅ `record_structured_evidence()`（返工重写） |
| 按来源分发重放 + 证据绑定校验 | ✅ `verify_evidence_chain()` |
| 评分数据视图合并 | ✅ raw→patch 注册投影器 + 字段/子路径白名单 + patch 生产映射重放 |
| 来源降级闸门 | ✅ `apply_provenance_gate()` |
| 授信口径配置 | ✅ `config/verification_policy.py` |
| `FieldCheck` 溯源字段 | ✅ `state.py` |
| `ResearchState.evidence_store` | ✅ |
| DataAnalyst 接入 + 降级约束等级 | ✅ |
| 取证时间取自档案声明 | ✅ `profile_retrieved_at()` |
| 行为断言（含端到端评级） | ✅ 53 例 |
| 真实外部适配器 | ⬜ **本轮明确不做**（注册表为空） |
| Scout 升级字段状态 | ⬜ **本轮明确不做** |
| evidence_store 终局输出 | ✅ `research_complete` 可解引用规则中的 evidence_id |
| evidence_store 检查点 | ✅ 随 `ResearchState` 通用持久化；独立迁移/恢复回归待补 |
| evidence_store 独立增量 SSE | ⬜ 待后续 |
| 证据时效性策略 | ⬜ 待后续（校验时间戳合法，未校验过期） |
| `profile_patch` 可声明替换语义 | ⬜ 待真实适配器接入时决策 |

**首版曾以 22 例全绿交付，只读复核推翻了该结论**：断言全部停在
`verify_field_checks().ok`，未走到最终评级，因而漏掉 BC-31（证据通过校验
却没进评分数据）等 5 个缺陷。详见 `BADCASES.md` BC-30～BC-35、
`DESIGN_CORE_MECHANISMS.md` 第五节、`ITERATION_ROADMAP.md` v0.6a。

第二轮复审继续暴露 BC-36～BC-40，现已由 Codex 接手修复：patch 与证据内容及
字段路径绑定、异常原子性、显式且可审计的证据替代关系、规则到 raw 的终局可追溯性。
当前专项 53/53 及既有确定性回归全绿，**仍待另一个 Agent 独立复审，不自行封板**。

---

## v0.6 进度：编排交还 LangGraph + 人机协同复核（2026-08-13）

| 项 | 状态 |
|---|---|
| 编排等价性黄金轨迹 | ✅ `tests/test_graph_equivalence.py` 11 例 |
| 重建声明式图（补 DataAnalyst / 补充搜索回环 / 档案加载 / 取消 / 检查点） | ✅ |
| 节点内实时流式（`get_stream_writer()` + `stream_mode="custom"`） | ✅ |
| 删除 `_run_simplified`，消除双份控制流 | ✅ |
| `human_review` 节点（`interrupt`） | ✅ |
| 图检查点 PostgresSaver + 异步桥接 | ✅ 见 BC-45 |
| `checkpoint_service` 首次真正设置 `paused` | ✅ |
| `POST /research/review/{session_id}` | ✅ |
| 复核结论并入评级并写回报告正文 | ✅ `apply_human_review()` |
| 真实跨进程恢复验证 | ✅ 两个独立进程实测 |
| 前端复核确认卡片 | ⬜ v1.0 范围 |
| BC-18（`guarantee_circle` 低风险可达性） | ⬜ 待决策 |

**本轮新增缺陷记录**：BC-41（声明式图与实际执行分叉）、BC-42（`Command(goto=END)`
不取代静态边）、BC-43（未声明的 state 键被丢弃）、BC-44（`interrupt` 前的副作用
执行两次）、BC-45（测试替身比真货能力强，生产路径整条失效）。

其中 **BC-45 是在 B 阶段提交之后才发现的**：23 条断言全绿，但注入的
`MemorySaver` 同步异步两套接口都实现，真正上生产的 `PostgresSaver` 只有同步一套。
详见 `BADCASES.md` BC-41～BC-45、`ITERATION_ROADMAP.md` v0.6。
