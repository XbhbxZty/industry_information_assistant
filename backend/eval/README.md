# 评测框架

两层设计。分层的依据是：v0.2 代码评审发现的 5 个缺陷**全部出在判定规则里，
没有一个出在模型输出**——快速层正好覆盖出问题最多的地方，且跑得起。

| 层 | 脚本 | 是否调 LLM | 耗时 | 测什么 |
|---|---|---|---|---|
| 快速层 | `run_fast.py` | 否 | 秒级 | 核查清单判定规则的正确性 |
| 校验层 | `run_critic.py` | 是 | 约 8 分钟 | Critic 能否抓出违规、会不会误报 |

```bash
cd backend
python eval/run_fast.py                   # 每次改判定逻辑后都跑
python eval/run_critic.py                 # 改 Critic 提示词后跑
python eval/run_critic.py --repeat 1      # 快速冒烟
python eval/run_critic.py --kind clean    # 只看误报
python eval/run_critic.py --cases-file critic_holdout.json  # 独立留出集（只用于封板）
python tests/test_dd_checklist.py         # 状态机单元测试
```

## 数据文件

| 文件 | 内容 | 谁能读 |
|---|---|---|
| `app/data/companies_eval.json` | 5 家评测企业档案 | 系统 |
| `eval/ground_truth.json` | 字段级标准答案 | **仅 eval/** |
| `eval/critic_cases.json` | 10 注入 + 18 对照用例 | **仅 eval/** |
| `eval/critic_holdout.json` | 6 注入 + 8 对照；首次运行后暴露 BC-26，现已退役为回归集 | **仅 eval/**；不得再作为盲测证据 |
| `eval/critic_holdout_v2.json` | 12 注入 + 12 对照；首次运行未封板，现已退役为回归集 | **仅 eval/**；不得用于修复后的盲测证明 |
| `eval/critic_holdout_v3.json` | 14 注入 + 14 对照；**整轮作废**——用例使用自创企业名，与装置指定的 based_on 企业不一致，Critic 正确报告主体不符却被计为误报（BC-47 前置发现） | **仅 eval/**；只可作装置错配的反面教材 |
| `eval/critic_holdout_v4.json` | 14 注入 + 14 对照；据此删除确定性扫描器（BC-47），现已退役为回归集 | **仅 eval/**；不得用于后续封板证明 |

盲测集**只跑一次**，跑完即退役。开跑前必须先过零成本静态自检（企业名与 based_on
一致、每条注入的目标字段状态支持其违规类型），v3 整轮作废就是漏了这一步。
生成 prompt 应与集合一并入库，使"告诉了生成方什么"可被审计——
v4 的 prompt 由修复者撰写且含其自查例句，该泄露已在
[`BLIND_V4_REPORT.md`](BLIND_V4_REPORT.md) 中如实声明。

⚠️ 标准答案必须与数据文件分离。系统能看到答案，评测就退化成自我验证。

## 五家评测企业

每家针对一个失效模式：

| ID | 场景 | 期望核实率 |
|---|---|---|
| EVAL-001 | 优质企业，数据齐全无不良记录 | 14/15 |
| EVAL-002 | 失信 + 2笔被执行 + 负债率89.1% | 13/15 |
| EVAL-003 | **司法数据源超时，司法维度全未查** | 9/15 |
| EVAL-004 | **主体存疑：工商查询返回空** | 4/15 |
| EVAL-005 | **多源冲突：实缴资本 5000万 vs 1500万** | 12/15 |

后三个是核心用例，分别对应尽调系统最危险的三种失效：
把缺失当无风险、把不存在的主体当真、把冲突数据单方面采信。

## 关于方差（重要）

`run_critic.py` 默认每例重复 3 次，结果分三档：

```
稳定通过  N/N 正确    可信
不稳定    介于中间    方差，不应据此调提示词
稳定失败  0/N 正确    真缺陷，值得修
```

这不是过度设计。BC-13 记录了教训：在 3 个对照用例上按单次结果调提示词，
出现了"改完失败用例换人而总数不变"的打地鼠现象——那是在拟合噪声。
**LLM 参与判定的评测必须把方差纳入指标，单次运行的百分比不足以支撑"改进了"的结论。**

模型 API 异常、JSON 不可用等降级运行不进入检出率/误报率分母，并使整轮命令
非零退出。生产链路中「模型失败时按未审核处理」由 `tests/test_critic_review.py`
验证；消融实验若把模型失败算作某一组的漏检，会把可用性差异误当成算法贡献。

## 已知局限

- 对照用例 18 个仍不算多，覆盖的"正确表述形态"有限
- 注入用例是人工构造的典型违规，真实报告的违规可能更隐蔽
- 端到端层（完整跑一次尽调约 10 分钟）尚未纳入自动评测
- 原 `critic_holdout.json` 已用于定位并修复 BC-26，封板前需要新的未见盲测集

## 十二个公开数据案例

`real_cases/` 中的 Markdown 是不可变的原始案例包，内部同时含来源目录、事实表和
Deep Research 生成的参考结论。**不得把整份 Markdown 放入业务知识库**，否则会把
标准答案泄露给 Agent。

运行以下命令可重建隔离后的评测语料：

```bash
cd backend
python eval/process_real_cases.py
pytest tests/test_real_case_processing.py -q
```

结构校验通过后，可按下载队列获取并计算原始文件哈希（无需 Docker）：

```bash
python eval/fetch_real_case_sources.py --workers 4
```

下载器限制单文件 50 MiB、拒绝 PDF 链接返回的 HTML/验证码页面，并写入
`acquisition_manifest.jsonl` 和总览 `acquisition_summary.json`。重复执行会复用已校验
文件；只有显式传入 `--overwrite` 才会覆盖。

本地解析、隔离入库与校验使用两个不同的 Python 运行时。PDF 解析需要 `pypdf`，
Milvus 写入则需要项目完整运行时及 `DASHSCOPE_API_KEY`：

```powershell
# 只读取 raw_sources，并生成带 source_id/页码的本地切片
python eval/prepare_real_case_corpus.py --workers 4

# 写入集合 eval_case_01_sources ... eval_case_12_sources
python eval/ingest_real_case_rag.py --dry-run
python eval/ingest_real_case_rag.py --embedding-workers 3

# 回读全部主键、kb_id 和 case marker，检查串库及答案层路径泄漏
python eval/verify_real_case_rag.py
```

集合名只能由严格的 `case_01`～`case_12` 派生，请求方不能直接提交 collection、
本地路径或 scope。每个 case 在全部 embedding 成功并通过 1024 维校验后才建集合；
未生成 completed 入库报告的集合不得参与评测。

完成校验后，可运行一个只读本地材料的端到端案例：

```powershell
# 默认保留生产的分节点模型矩阵（scout=qwen-plus、critic=deepseek-v4-flash…）
python eval/run_real_case_agent.py --case case_01

# 单变量扫描：同一个模型铺满六个节点
python eval/run_real_case_agent.py --case case_01 --model deepseek-v4-flash

# 运行结束后，对隔离性、截止日、决策、银标事实覆盖和引用覆盖做确定性筛查
python eval/score_real_case_run.py eval/runs/real_cases/case_01/<session-id>
```

运行器关闭网络搜索，按 manifest 设置研究截止日，并在独立进程中关闭生产人工中断
（仅为得到可评分草稿，报告仍必须判断是否需人工复核）。输入、全量事件、最终报告和
终局载荷写入 `eval/runs/real_cases/`；评分器之后才可读取 `reference/`，
Agent 运行器本身禁止读取。

⚠️ `--model` 曾经是必选且默认 `qwen-plus`，会把**六个节点全部**覆盖成一个模型。
那不是模型路由而是放弃路由（BC-56）：生产刻意让高频的 Scout 用快模型、低频的
Critic 用推理模型。现在留空即保留生产矩阵，`--model-<节点>` 可只移动一个节点。
**2026-08-17 之前的运行都是 `--model qwen-plus` 的结果，与不带参数的新运行不可直接比较。**

### 生产矩阵变更记录

跨轮次比较分数前必须先对齐矩阵。历史评测报告（`BLIND_V*_REPORT.md`、
`ABLATION_V3.md`）里的模型名记录的是**当时实际跑的**配置，不随默认值变更改写。

| 日期 | 矩阵 | 备注 |
|---|---|---|
| ~2026-08-17 | architect/data_analyst/wizard/writer=`deepseek-v3.2`、scout=`qwen-plus`、critic=`deepseek-v4-flash` | case_01 得分 72.07 的那一轮 |
| 2026-08-17 起 | 上述 `deepseek-v3.2` 全部退役为 `deepseek-v4-flash`；scout 仍为 `qwen-plus` | v3.2 已过时；**尚无对应质量实测**，换矩阵后与 72.07 不可直接比较 |

Scout 刻意不跟随退役：它是唯一的高频节点，也是 BC-56 里 553 秒尾延迟的发生地，
要动它必须先有节点级消融数据。

### 模型消融：固定检索输入（BC-56）

直接换 Scout 模型跑两遍**得不到**"同证据、不同抽取模型"的对照：Architect 也是
LLM，它的章节描述与检索词会漂，检索词变了检索结果就变，检索结果变了 Scout 能抽到
什么也就变了。因此先录制一次上游输入，再逐字回放：

```powershell
# 1) 录一次基线，落盘提纲 + 每章检索结果
python eval/run_real_case_agent.py --case case_01 `
    --fixture-mode record --retrieval-fixture eval/runs/real_cases/case_01/fixture.json

# 2) 冻结上游，只移动 Scout 模型（Architect 不再发调用）
python eval/run_real_case_agent.py --case case_01 `
    --fixture-mode replay --retrieval-fixture eval/runs/real_cases/case_01/fixture.json `
    --model-scout deepseek-v4-flash
python eval/run_real_case_agent.py --case case_01 `
    --fixture-mode replay --retrieval-fixture eval/runs/real_cases/case_01/fixture.json `
    --model-scout qwen3.8-max
```

回放按 **section id** 键取证据，不按检索词——换模型必然写出不同检索词，按检索词
键会一条都命中不到，而失效是静默的（返回空集，看起来像"这一章没材料"）。
录制时失败的检索回放时仍是失败，不会退化成"查了没有"（BC-51）。

装置只由 `eval/retrieval_fixture.py` 从外部打补丁，生产代码里**没有**回放分支
（BC-45：替身不得活在生产路径上），用完在 `finally` 里撤销。

回放运行冻结了规划与检索，**不是**一次完整的端到端运行。评分器因此单独判
`verdict: ablation`（退出码 4）、`full_pipeline_run: false`，并把 `agent_models`
与装置的 `content_sha256` 与分数并列输出——消融分数只能与另一次同装置的消融相比，
不能当成"系统达标了"（BC-25）。

RAG 进入尽调的路径不是“检索到一句话就给 Writer”：Scout 只提出锚点
（结果编号 + 字段 + 取值 + 期间），`rag_evidence_bridge` 自己从原文切出证据窗口，
再校验本地来源、主体归属、固定字段关键词、取值是否存在于原文以及发布日期/截止日。
`exact_quote` 不由模型回抄（BC-57），模型自报的引文与单位一律忽略。只有通过者能
原子写入 `evidence_store` 与 `field_checks`；同期间多源异值标为 `conflicting`。
外部模型运行会把白名单材料片段发送给所选模型 API，执行前必须确认案例资料允许
发往该服务商。

输出位于 `real_cases_processed/<case_id>/`：

| 目录/文件 | 用途 | Agent 可检索 |
|---|---|---|
| `download_queue.jsonl` | 官方原始来源的下载队列 | 下载并校验后可用 |
| `rag_manifest.jsonl` | 截止日内、非失败来源的 RAG 白名单 | 下载并校验后可用 |
| `retrieval_failures.jsonl` | 查询失败记录，证明“未核实”而非“无风险” | 否 |
| `reference/` | 银标 claims、清单、参考报告与结论 | **否，仅评分器可读** |
| `post_cutoff/` | 截止日后事实与结果 | **否，仅回测评分器可读** |
| `validation_report.json` | 缺字段、缩水 JSON、镜像来源等质量告警 | 否 |

当前参考答案统一标为 `silver / not_reviewed`。它适合做字段覆盖、证据引用、风险门槛
和弱模型对强模型的一致性评测，但不能冒充人工金标。`as_of_eligible=false` 也不会被
机械等同于后验信息：截止日当时缺少交易合同、发票等材料，仍属于截止日评测中的
“未核实”事实。

v3 四组消融的完整结果与原始文件索引见
[`ABLATION_V3.md`](ABLATION_V3.md)，指标可由 `summarize_runs.py` 从 JSONL 复算。
