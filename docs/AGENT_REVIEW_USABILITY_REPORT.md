# 复核可用性：反馈交接、问题闭环与验证边界

日期：2026-09-28。实现提交：`b33c0d9`。

## 结论与范围

这是已约定的第三项“复核可用性”，不是新业务模块。修复了可复现的程序交接和状态问题；没有修改调查材料、评分标准、授信公式、证据核实权限、模型选择或调用预算。不用程序替模型判断财务分析是否正确。

| 原程序问题 | 本次修改 |
|---|---|
| 普通幻觉／逻辑错误被 Critic 记录，Writer 却只处理 `analysis_quality_error` | 将能精确定位到实际报告 AI 分析字段的普通问题交给 Writer |
| 新一轮审核看不到历史问题 ID，普通问题无法逐项复核 | 提供未解决问题，要求每个 ID 的 `issue_checks` 回执；只有明确复核解决才能关闭 |
| 完全相同的问题重复追加 UUID，旧问题被挤出修订输入 | 精确相等或显式历史 ID 才合并；保留身份和出现次数，不做语义猜测合并 |
| 分析检查项转问题时丢失报告定位，检查项身份与正文位置混用 | 保留 `check_id`／`question_id` 和当前 `report_quote_id` |
| 末轮计数在 Critic 与 Graph 间不一致，可能跳过人工门或留下未复核改写 | 以当前审核轮次判断预算，末轮不再修订，按全量未解决问题执行既有人工复核限制 |
| Graph 追加交付范围说明改变全文哈希，使刚产生的 Writer 定位失效 | Critic 与 Writer 使用同一规范正文，只排除程序生成的交付状态说明 |
| 明知输入超限仍调用模型，协议／传输失败无法清楚区分 | 调用前检查报告、引用和历史问题上限；持久化失败分类，不进入无意义的文字修订循环 |

## 权限与生命周期

普通问题必须定位到实际交付报告的 AI 区块：概述、发现的 claim、逐问题答复／限制、补件说明。可用绑定当前报告版本的精确 ID，或在全文中只出现一次且完全位于可编辑行内的连续原文。重复、过期、未转义或跨越字段的定位不猜测匹配。

原始引文、来源记录、计算底稿、原问题和完成条件、规则评级及固定提示不因普通问题而获得改写权限。原有分析检查问题继续兼容；Writer 仍经原子校验更新分析字段，只能保持或降低问题完成状态，不能自行关闭审核问题。

历史内容问题按 ID 逐项复核。新报告总体高分或 `pass` 不能覆盖未明确复核的旧问题；允许一部分问题解决、另一部分保持未解决。Writer 返回“已修改”也不是解决回执。协议失败问题保留既有的保守关闭条件，旧的无 ID 检查记录继续兼容，不能假称已经完成逐 ID 复核。

问题合并只用显式 `recheck_of` 或类型／位置／原文／描述完全相同；不合并近义词。历史问题超过32项时明确按输入上限失败，不静默裁掉末尾问题。报告和引用维持原上限，严格 JSON、结束原因与精确引文校验不放松。

状态说明排除仅用于正文审核身份，不改变用户实际收到的交付限制。真正的正文变化仍使旧报告 ID 过期。耗尽轮次修复同时适用于经典同步路径，避免最后一次改写未经再审；未增加任何审核轮次。

## 离线验证

实现版本 `b33c0d9` 共29个相关测试文件：**718 passed，39条既有依赖弃用警告，142.76秒**。其中三个新增测试文件收集107项；另调整既有测试以验证失败停止与末轮人工门。不是全仓测试，不是 HTTP/UI 或真实模型质量验收。

真实运行后仅补正失败卡片文案，新增3项分类测试；7个相关文件再次回归 **212 passed，8条既有警告，6.66秒**。两次测试存在重叠，不能相加作为独立用例数；这次文案修改后没有再次真实调用模型。

覆盖普通问题 Critic→Writer 交接、实际 Graph 追加说明后的定位、冻结字段与失败原子性、重复／歧义／过期定位、历史回执遗漏／未知／重复 ID、部分关闭、重复问题计数、末轮路由、输入超限零调用、服务超时／异常分类、取消传播，以及前两阶段的行动与记忆回归。

独立只读交叉检查还用实际 `Graph.run_sync`／Critic 和内存节点桩验证经典模式：预算1次时审核1次且只写初稿，预算3次时审核3次且只修订2次；没有末轮未复核改写。该检查不调用模型或数据库。

复现联合回归（`backend` 目录）：

```powershell
python -X utf8 -m pytest tests/test_review_issue_lifecycle.py tests/test_review_writer_targets.py tests/test_review_pipeline.py tests/test_stage3_review.py tests/test_review_verdict.py tests/test_graph_equivalence.py tests/test_working_memory.py tests/test_memory_recall.py tests/test_action_selection.py tests/test_investigation_errors.py tests/test_investigator.py tests/test_agent_workbench_tools.py tests/test_question_ledger.py tests/test_calculation_tool.py tests/test_investigation_tools.py tests/test_investigation_layer.py tests/test_investigation_isolation.py tests/test_investigation_corpus_retention.py tests/test_investigation_section_fairshare.py tests/test_analysis_quality.py tests/test_critic_review.py tests/test_critic_new_issue_types.py tests/test_research_outcome.py tests/test_research_outcome_integrity.py tests/test_rag_evidence_bridge.py tests/test_statement_scope.py tests/test_risk_scorecard.py tests/test_risk_integration.py tests/test_scoring_dependencies.py -q --tb=short
```

## 冻结样本真实审核

使用 `eval/replay_agent_review.py`，输入为上一阶段的 `MEMORY-T01-001/result.json`。不重跑 Investigator，不把 ORACLE 或独立评价给审核模型，不调用数据库、Scout、检索、评分或审批，也不经过完整 Graph／HTTP／UI。用真实渲染器生成冻结调查记录的 AI 报告区块，不能把该范围当成完整尽调报告验收。

原归档 SHA-256：`5D5097D19310BFF953CF02D21CE1C3E318CC4326FB6078CE5B3A2F98C21581FE`。

审核使用项目现有阿里云百炼 `dashscope.aliyuncs.com`、`deepseek-v4-flash`；与上一阶段 Investigator 的 `qwen-plus` 分工不同，不是临时换模型。原配置 `max_iterations=1` 保持不变，因此真实测试不具备“审核→Writer修订→再次审核”的预算。该闭环只由离线测试验证，不能以一轮真实审核冒充在线闭环通过。

首次命令被出网安全检查拒绝，进程未启动、未发送请求。随后只读核实五份原文都标明虚构、5/5文件哈希与仓库夹具一致，并核实服务域名；补充这些低风险证据后，同一命令获准执行。没有换路径或降低沙箱绕过拒绝。

新归档：`backend/eval/agent_e2e_pack/runs/REVIEW-MEMORY-T01-001/`，代码版本 `b33c0d9`，保留请求参数、异常类型、输入清单和结果。

**结果：仅1次真实模型调用，61.234秒后抛出 `LLMCallTimeout`，整个定向实验63.547秒；没有返回审核正文，语义审核未完成。** 原调用参数为温度0、8000 tokens、60秒请求超时、0重试；未提高超时或再次运行。

程序保存 `review_failure_kind=transport`、`review_failure=review_provider_error:LLMCallTimeout`、`degraded=true`、`analysis_review_validated=false`，记录1条未解决的 `review_not_executed` 并要求人工复核。`phase=completed` 只表示执行停止；归档中的1分是失败保守值，不是模型对内容的实际评分。Writer 未被调用，原 notebook 与输入归档均未改变。

五份已读原文、四个调查问题均在请求中，报告／引用／已读正文均未截断。这只能证明请求输入和超时后的程序路径；没有响应不能推断模型正确、模型有推理缺陷，或错误已被修复。上一阶段的调查仍是 `stalled`，原内容失败未被“洗白”。

`report.md` 是用于送审的冻结分析正文，不含完整 Graph 交付状态说明，不是通过审核的最终报告。详细结果与完整性哈希见该目录的 `evaluation.md`。本次普通问题真实识别、Writer改写、历史问题再复核均未获得在线验证。

归档还暴露一处可确定的呈现错误：失败元数据已正确分类为传输故障，但问题卡片沿用“LLM返回不可用结构／不得进入完成态”的旧固定文案。随后仅将卡片区分为传输失败、协议失败、输入超限与未知原因，并明确执行停止不等于质检通过；不改变限制或状态机。原运行档案保留旧文案，不修改历史结果冒充修正后的在线表现。

## 停止标准

完整信息下模型仍误审／漏审，记录为这次模型表现的限制，不添加样本专用规则、不调提示重跑求通过；传输失败独立记录，不推断模型推理能力。原失败档案不覆盖、原评分不改、原预算不提高。本轮不重启应用服务。
