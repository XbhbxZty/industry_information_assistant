# Agent-first 第一阶段交付记录

日期：2026-09-28。范围：冻结失败基线、统一评估入口、修复经营维度依赖、分离交付状态。

本阶段的目标是让系统诚实表达已经完成和尚未完成的工作，不把规则评分成功误当成自主调查成功。未改数据库连接、未迁移或清空数据、未放宽证据真实性校验，也未执行真实贷款审批。

## 1. 固定验收基线

沿用已经入库 Git 的真实失败产物，不覆盖原始报告或重新给历史实验打分：

- `backend/eval/agent_e2e_pack/runs/T01-agent-003/`，归档提交 `bb001e1`：调查 stalled，正文分析存在遗漏，系统高质检分不等于有效的逐项复核回执。
- `backend/eval/agent_e2e_pack/runs/QL-approve-agent-001/`，归档提交 `3de5933`：规则低风险、必查 15/15、末轮质检 10 分，但调查 stalled，历史重大问题仍未解决。

新增回归直接读取这些归档：T01 显示调查停滞、质检未确认、依据不足；QL 显示质检待修订、受限报告，且不擅自改写其原规则评级。此处验证的是交付语义，不是重新运行模型或证明原分析已纠正。

## 2. 共用评估入口

新增 `backend/app/service/risk_evaluation.py::evaluate_risk`，生产 DataAnalyst 和 `backend/eval/run_fast.py` 都调用它：

1. 按当前清单重算完整度。
2. 校验档案、证据来源、回放一致性和截止日。
3. 合并同源评分视图；无法合并则不予评级。
4. 执行规则评分及来源降级约束。
5. 使用同一评分视图、最终等级计算额度建议。

返回评级、完整度、评分视图、分阶段诊断。评测不再绕过生产前置条件直接调用底层评分卡。正常拒评和执行异常分别记录，异常不能靠“刚好也得到依据不足”蒙混为评测通过。

失败重评显式清空 `scoring_view`：LangGraph 合并节点更新时，仅删除 Python 字典键不能清除旧检查点的通道值。测试使用真实 StateGraph + MemorySaver 验证不复用旧评分输入。

运行状态只保存评估的 `status/stage` 摘要，用于区分运行故障和资料缺口；公开交付状态不携带原始证据、工具回执或诊断堆栈。

## 3. 修复经营维度的错误依赖

经营评分依赖已经核实的 `operating_status`，不再因选查项 `bidding_record` 未查询而排除整个经营维度。登记状态未核实、冲突或缺失时仍受约束，中标记录不能替代登记状态。

EVAL-005 的期望等级相应调整为中风险，原因已经写入 `ground_truth.json`。同输入对照修复前后评分器：

| 项目 | 修复前 | 修复后 |
| --- | --- | --- |
| 综合分 | 19.4 | 17.5 |
| 经营维度 | 被选查缺口错误排除 | 按已核实登记状态参与评分 |
| 上调原因 | 错误维度门槛，再叠加实缴冲突 | 仅实缴冲突 |
| 规则等级 | 高风险 | 中风险 |
| 人工复核 | 必须 | 仍必须 |

没有改冲突事实、评分阈值或其他四个案例的期望。此变更不是认定冲突已经解决，也不是允许自动授信。

## 4. 明确交付状态

新增 `research_outcome` v1 投影，随草稿、人工复核事件、终局/取消事件及检查点 UI 投影输出；前端严格校验后分别展示：

| 状态 | 含义 |
| --- | --- |
| execution_status | 执行中、已结束、等待人工复核、取消或存在执行异常 |
| investigation_status | 自主调查完成、停滞、时间/步骤上限等；普通研究不适用 |
| quality_status | 已保存复核通过、待修订，或尚未取得有效复核回执 |
| report_status | 暂无报告、受限报告、部分材料报告、草稿或就绪 |
| rating_status | 未评估、依据不足或已生成既有规则评级 |
| credit_status | 既有规则下额度测算可用或不可用，不是放款权限 |

关键规则：

- 末轮高分不能清除仍为 `resolved != True` 的历史重大/阻断问题。
- agent 调查未正常完成、质量复核不通过/缺失、执行节点或评估器出错时，已有报告作受限交付。
- 如实披露材料不足、没有其他调查/质检问题的报告可以标为部分材料报告，不将缺少资料等同运行失败。
- Critic 保存结构化 `quality_review`，`unresolved_issues` 统计历史未解决问题，而不只统计末轮新问题。
- 受限/部分交付说明写进报告正文且幂等；不只在前端加一条容易消失的提示。
- `research_complete` 仍表示编排执行结束，不再单独承载“质量通过”的含义。
- 图表节点在取消时统一设置取消标记，守卫不再向后推进；取消事件明确更新执行状态，避免前端保留旧的执行中标签。

边界没有扩大：`rating_status=available` 只是已有规则产物存在，不是新的评级就绪政策。本阶段未将质量状态反向写成新的财务风险门槛，也未改人工复核是否必须的现有政策。历史问题的完整销项工作流属于后续阶段；已有分析专项问题仍只能按原有完整复核规则关闭。

限制说明在节点签名前生成；`quality_review`、异常记录和报告正文纳入原检查点完整性保护。人工通过、调整等级或拒绝都不自动改变这些调查依据。已签名的历史报告不批量重写；旧数据没有有效回执时不凭高分推断已通过质检。

## 5. 验证及限制

- 后端主回归：361 项全部通过，覆盖共用评估、证据闸门、经营评分、真实 Critic 处理、状态投影、历史失败产物、签名检查点和人工复核恢复；不是全仓库/全部数据库集成测试。
- 最后补入取消状态接线后，重跑 `test_research_outcome.py` 与 `test_graph_equivalence.py`，34 项全部通过（与主回归部分重叠，不累加计数）。

- 快速规则评测：5/5 案例通过，字段状态 86/86；评级、门槛、证据链、执行状态全部通过。
- 前端 Node 测试：22/22 通过，含真实 hook 的 SSE 状态消费回归。
- 前端 `npm run build`：成功。
- 前端 `npx tsc --noEmit -p tsconfig.app.json`：仍有 24 项既存错误。用 TypeScript CompilerHost 虚拟还原修改前文件作只读对照，前后诊断的文件、位置、错误码和消息完全一致；本次前端文件没有新增错误。不把构建成功等同于全量类型检查通过。

后端复现命令（在 `backend` 目录运行，PowerShell）：

```powershell
$stage1Tests = @(
  'test_risk_evaluation', 'test_risk_integration', 'test_risk_scorecard',
  'test_scoring_dependencies', 'test_verification_chain', 'test_credit_advice',
  'test_override_recompute', 'test_research_outcome', 'test_research_outcome_integrity',
  'test_graph_equivalence', 'test_analysis_quality', 'test_critic_review',
  'test_critic_new_issue_types', 'test_final_event_layer_labels',
  'test_profile_ref_event_contract', 'test_human_review', 'test_review_persistence',
  'test_review_verdict', 'test_review_claim_service',
  'test_checkpoint_integrity_persistence', 'test_checkpoint_context'
) | ForEach-Object { "tests/$_.py" }
python -m pytest $stage1Tests -q --disable-warnings
python -X utf8 eval/run_fast.py
```

前端复现命令（在 `frontend` 目录运行）：

```powershell
node --test tests/*.test.mjs
npm run build
npx tsc --noEmit -p tsconfig.app.json
```

本轮为离线代码/协议回归；封签和暂停恢复测试使用真实图执行及 MemorySaver，不写业务数据库。未进行新的在线模型端到端实验、浏览器视觉验收或 PostgreSQL 集成测试，因此不能宣称 T01/QL 的分析质量已经提高或所有线上链路通过。

## 6. 留给下一阶段

后续重点是实际调查效果：跨片段继续阅读、区分“未读到”与“资料不存在”、引文工具契约、近重复发现的进展判定，以及量化计算/归因与针对真实错误的修订。新 readiness 政策、补件续查和更完整的问题销项不在本阶段实现。

本轮保留工作树原有数据库、Docker、启动脚本、企业档案和用户材料改动，按明确文件清单提交，未混入阶段提交。
