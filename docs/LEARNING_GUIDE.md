# 项目学习路线图

> 用途：在动手迁移之前，按这条路线把项目吃透。配合 `docs/DATA_STRUCTURES.md`（数据结构总览）和 `docs/MIGRATION_PLAN.md`（迁移计划）一起看。
>
> 核心方法：**不要按目录逐个文件读，而是跟着一次真实请求走完整条链路。** 读代码的顺序应该等于数据流动的顺序。

## 项目一句话概括

给定一个研究问题，由 5 个 LLM 智能体（规划→检索→分析→写作→审核）协作，边搜索边取证，最终流式产出一份带图表、知识图谱和引用来源的深度研究报告。

## 规模概览（帮你判断该在哪里花时间）

| 部分 | 行数 | 重要性 |
|---|---|---|
| `service/deep_research_v2/`（V2 多智能体，**心脏**） | ~6000 | ⭐⭐⭐⭐⭐ |
| ├─ `agents/scout.py`（检索取证，最大的 Agent） | 1392 | ⭐⭐⭐⭐⭐ |
| ├─ `agents/wizard.py`（代码执行+绘图） | 1304 | ⭐⭐⭐⭐ |
| ├─ `graph.py`（LangGraph 状态机编排） | 806 | ⭐⭐⭐⭐⭐ |
| ├─ `agents/data_analyst.py` | 481 | ⭐⭐⭐⭐ |
| ├─ `agents/writer.py` / `critic.py` / `architect.py` | 491/356/353 | ⭐⭐⭐⭐ |
| ├─ `agents/base.py`（所有 Agent 的基类，含 SSE 封装） | 304 | ⭐⭐⭐⭐⭐ |
| └─ `state.py`（全局共享状态定义） | 237 | ⭐⭐⭐⭐⭐ |
| V1 遗留（`react_controller`+`tool_executor`+`dr_g`） | ~2400 | ⭐（计划中将删除，**别花时间精读**） |
| 知识库 RAG（milvus/docmind/embedding/retrieval） | ~1000 | ⭐⭐⭐⭐ |
| 外围（采集/股票/招投标/Text2SQL） | ~1700 | ⭐⭐ |

**时间分配建议：60% 花在 V2 多智能体，25% 花在 RAG 和 SSE 链路，15% 扫一遍外围。V1 只需要知道它存在且是历史包袱。**

---

## Step 0：先跑起来（半天）

没跑起来之前读代码事倍功半——你需要能对着真实的 SSE 输出和数据库表理解代码。

```bash
docker compose up -d
```

起来 6 个容器：PostgreSQL、Redis、Milvus、MinIO、etcd、Elasticsearch。

> 💡 **第一个可以自己验证的发现**：其中 Elasticsearch 后端代码里一次都没引用过（可以自己 grep `elasticsearch` 验证）。这是历史遗留，迁移计划里会删掉它。发现这类"文档说有、实际没用"的差异，是熟悉项目的重要一环。

然后按 `READMED.md` 配 `.env`（必填 `DASHSCOPE_API_KEY` 和 `BOCHA_API_KEY`）、起后端、起前端。

打开 `http://localhost:8000/docs` —— FastAPI 自动生成的接口文档，这是你的项目地图。

**验证你已完成 Step 0**：能注册登录、能发一条普通对话、能发起一次深度研究并看到右侧面板动起来。

---

## Step 1：SSE 流式链路（这是全项目的骨架）

整个项目的用户体验建立在 SSE（Server-Sent Events）上。先搞懂这条最短的链路，后面所有复杂功能都是它的加强版。

**阅读顺序：**
1. `frontend/src/api/session.ts` — 前端如何发起流式请求
2. `backend/app/router/chat_router.py` — 后端如何返回 `StreamingResponse`
3. `backend/app/service/chat_service.py` — 事件如何一条条 yield 出来
4. `frontend/src/pages/chat/index.tsx` — 前端如何解析 `data: {...}\n\n` 并增量渲染

**要带着回答的问题：**
- 一个 SSE 事件的 JSON 长什么样？（对照 `DATA_STRUCTURES.md` §7）
- 前端怎么区分"这是思考过程"和"这是正文"？
- 中途取消是怎么实现的？（提示：Redis 里的 `research:cancel:{session_id}` 标志位）

---

## Step 2：深度研究 V2 —— 项目的心脏（花最多时间）

这是迁移改动最大的部分，也是面试时最值得讲的部分。

**阅读顺序（严格按这个顺序，不要跳）：**

1. **`deep_research_v2/state.py`** — 先读状态定义。`ResearchState` 是所有 Agent 共享的"全局工作记忆"，理解它 = 理解整个系统在传递什么。重点看 `ResearchPhase` 枚举的 9 个阶段。

2. **`deep_research_v2/agents/base.py`** — 所有 Agent 的基类。重点看 `add_message()`：它把所有 Agent 输出统一包装成 `{type, agent, timestamp, content}` 信封再推给 SSE。这个设计是前后端解耦的关键。

3. **`deep_research_v2/graph.py`** — 编排层。这是最值得精读的一个文件，也**藏着整个项目最大的坑**。

   ⚠️ **读这个文件时请特别注意 349-356 行**：LangGraph 的执行路径被整段注释掉了，运行时永远走手写的 `_run_simplified()`。也就是说：
   - 203-235 行构建的 LangGraph 状态机、237-289 行的 `_plan_node`~`_should_continue`，**全是死代码**
   - 真正在跑的控制流是 `_run_simplified()` 里 650/760 行附近的 while 循环
   - 原作者注释里写了原因："LangGraph 版本会批量处理消息，无法实现实时流式输出"

   **请务必把两套实现对照着读**：先读死掉的声明式版本（好理解，看清意图），再读活着的命令式版本（看清实际行为）。重点搞明白：
   - `reviewing` 之后怎么决定走 `completed` / `re_researching` / `revising`（自我修正循环）——这段逻辑在两个版本里各写了一遍
   - 实时 SSE 是怎么用 `asyncio.Queue` 实现的，为什么它跟 LangGraph 的 `astream` 语义冲突
   - checkpoint 在什么时机存

   这个坑是迁移计划里的最高优先级决策项（见 `MIGRATION_PLAN.md` Stage 0.5），也是最有价值的面试素材，值得花时间吃透。

4. **5 个 Agent，按流程顺序读**：
   - `architect.py`（353行）— 把问题拆成研究大纲 + 假设
   - `scout.py`（1392行，最大）— 按大纲检索、抽取事实和数据点、增量构建知识图谱。**注意 376 行的 `_fetch_stock_data_if_relevant`**：自动识别问题里的上市公司并拉股票行情——这是行业场景的强绑定，迁移时会删掉
   - `data_analyst.py`（481行）— 从事实中提炼结构化数据、生成 ECharts 配置
   - `wizard.py`（1304行）— 生成并**执行 Python 代码**画图，带失败重试。这块工程量最大
   - `writer.py`（491行）— 逐章节写作 + 组装最终报告
   - `critic.py`（356行）— 审核报告质量、打分、提出问题触发返工

5. **`deep_research_v2/service.py`** — 对外封装层，看它怎么被 router 调用

**要带着回答的问题：**
- 一次研究从开始到结束，`ResearchState` 里的字段是按什么顺序被填满的？
- Critic 打分低会发生什么？循环最多转几次？（看 `ResearchConfig.max_iterations`）
- 为什么 `state.py` 里定义了 `Fact`、`Chart` 这些 dataclass，但实际代码里传的是字典？（提示：JSON 序列化 / checkpoint 存储）
- 断线之后怎么恢复研究？（`checkpoint_service.py` + `research_checkpoints` 表）

---

## Step 3：知识库 RAG 链路

**阅读顺序：**
1. `router/knowledge_router.py` — 上传接口
2. `service/docmind_service.py` — 调阿里云 DocMind 解析 PDF/Word
3. `service/embedding_service.py` — 调 DashScope 生成 1024 维向量
4. `service/milvus_service.py` — 建 collection、插入、向量检索（`IVF_FLAT` + 余弦相似度）
5. `service/retrieval_service.py` — 检索结果如何回流给 Agent

**要带着回答的问题：**
- 一个 PDF 从上传到能被检索，经过了几步？中间态存在哪？
- Milvus 的 collection 是怎么命名的？（`kb_<知识库名>`——想想这个设计有什么隐患）
- PostgreSQL 里的 `documents` 表和 Milvus 里的切片是怎么对应上的？

---

## Step 4：外围功能（快速扫过即可，各 30 分钟）

这些是"行业场景"的具体体现，也是迁移时替换或删除的主要对象。理解**它们的模式**比理解细节更重要。

| 模块 | 看什么 |
|---|---|
| `service/news_collection_service.py` | "遍历关键词→搜索→查重→落库"的采集编排模式（迁移时会照搬这个模式换成论文采集） |
| `service/text2sql_service.py` | 自然语言转 SQL。**重点看 79 行的 `SCHEMA_DEFINITION`**：表结构和示例数据是硬编码在 prompt 里的 |
| `service/memory_service.py` | 跨会话长期记忆，存 Milvus |
| `config/industry_config.py` + `frontend/src/store/industry.ts` | 行业配置，前后端各维护一份（这是已知的契约风险） |
| `service/scheduler_service.py` | APScheduler 定时任务 |

---

## Step 5：架构反思（最重要的一步，直接决定面试能讲多深）

读 **`docs/DATA_STRUCTURES.md` 第 10 节**——它列了 12 条"同名异构与契约风险"，是原作者自己承认的技术债。

然后自己带着批判视角回答：

1. **为什么会有两套会话系统？**（Redis 旧版 + PostgreSQL 新版并存）这反映了什么样的演进过程？
2. **为什么会有 V1 和 V2 两套研究架构？** V1（`react_controller.py`，ReAct 循环）和 V2（多智能体）解决同一个问题。V2 相比 V1 好在哪？为什么 V1 没被删掉？
2b. **编排框架为什么被绕过了？**（graph.py:349-356）实时流式输出和 LangGraph 的批量语义为什么冲突？如果是你，会怎么解决？——这是全项目最值得想清楚的一个问题
3. **`ChartConfig` 为什么有 5 个不同版本？** 这种类型碎片化是怎么产生的？
4. **前后端的行业配置为什么要人工同步两份？** 有什么更好的做法？

> 这一步的产出直接是你的面试素材。"我接手了一个有技术债的项目，识别出 X 个架构问题，重构了其中 Y 个"——这比"我做了一个 AI 助手"有说服力得多。

---

## 学习完成的自检清单

- [ ] 能不看代码画出一次深度研究的完整流程图（从 HTTP 请求到最终报告）
- [ ] 能说清 `ResearchState` 里至少 10 个字段分别由谁写、被谁读
- [ ] 能指出 Critic 触发返工时，状态机会走哪条边、哪些字段会被重置
- [ ] 能说清一个 PDF 从上传到被 Agent 检索到的完整路径
- [ ] 能列举至少 3 个你认为写得不好、准备在迁移时改掉的地方
- [ ] 能回答："这个项目里哪些代码是通用的 AI 工程能力，哪些是行业场景的硬编码？"（这个问题的答案就是迁移计划的依据）

---

## 学完之后

回到迁移窗口，把 `docs/MIGRATION_PLAN.md` 重新读一遍。那时候你会发现计划里的每一条改动你都能自己判断合不合理——那就说明学习阶段真正完成了。

迁移方向已定为**贷前企业尽职调查系统**（供应链金融/小贷场景），目标岗位为 AI 应用开发。届时最需要你拍板的是：**LangGraph 死代码是修复还是删除**（`MIGRATION_PLAN.md` Stage 0.5）——建议在学习阶段就把 graph.py 这个坑想清楚，回来直接能定。

其余待定项：版权署名文本、LICENSE 选型、项目正式名。
