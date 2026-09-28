# STAGE3-live-002：审核诊断证据

## T01：两轮约 60 秒超时

只读摘录本轮后端专用日志 `logs/stage3-backend-ac080ae.err.log` 第 144–145、159–160 行。摘录不含口令、令牌、请求体或服务地址；不复制整份运行日志，不改原始日志。两组日志发生于 T01 的两轮审核；这些日志产生时，QL 尚未进入审核阶段。

```text
144: ERROR:Agent.CriticMaster:LLM call exceeded its 60s budget after 60106ms: Request timed out.
145: ERROR:Agent.CriticMaster:[CriticMaster] LLM 审核调用失败: LLMCallTimeout: CriticMaster 调用 deepseek-v4-flash 超过 60 秒上界（实际 60106ms）
159: ERROR:Agent.CriticMaster:LLM call exceeded its 60s budget after 60102ms: Request timed out.
160: ERROR:Agent.CriticMaster:[CriticMaster] LLM 审核调用失败: LLMCallTimeout: CriticMaster 调用 deepseek-v4-flash 超过 60 秒上界（实际 60102ms）
```

这解释了本轮服务端为何降级，不代表模型已经形成可用审核结果。`t01/events.jsonl` 第 49、58 行只记录审核降级，`call_meta` 为空且 `review_failure` 为 null；两种证据应分别保留，不能将服务日志原因误写成 SSE 本身已经提供的字段，也不能归因为前轮的 `review_unlocated_quote`。

## QL：两轮返回后原文定位失败，不是超时

证据来自本轮 `ql/events.jsonl`，只投影诊断字段，不修改原始事件：

| 轮次及原始行 | review_failure | duration_ms | completion_tokens | finish_reason |
|---|---|---:|---:|---|
| 第 1 轮，第 50 行 | review_unlocated_quote | 37280 | 3139 | stop |
| 第 2 轮，第 59 行 | review_unlocated_quote | 54231 | 4699 | stop |

两轮均 `degraded=true`、`analysis_review_validated=false`，`analysis_checks` 与 `question_checks` 为空。模型结束生成与协议校验成功是两回事；本次没有有效审核回执，不能称片段 ID 审核已线上通过，也不能与 T01 的模型调用超时混为同一种故障。
