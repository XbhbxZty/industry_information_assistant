# REVIEW-MEMORY-T01-001：一次冻结样本真实审核

结论：**真实审核因服务调用超时未执行完成；程序正确保存未审核和人工复核限制。没有审核正文，不能评判模型的语义复核能力，也没有完成在线修订闭环。**

## 测试边界

- 类型：`focused_live_agent_review_not_e2e`。输入为 `MEMORY-T01-001/result.json`，保留上一阶段的失败 notebook。
- 代码：`b33c0d9f643d0f0c121c6dd2ebc64e790c08394f`；工作区有既有用户改动，`checkout_dirty=true`。
- 五份合成材料文件哈希与 `uploads/base` 一致，全部明确标注虚构；不含真实借款人财务资料。既有调查的 query、as_of、来源和模型答案未改。
- 项目当前 Critic 模型 `deepseek-v4-flash`，服务 `dashscope.aliyuncs.com`；原 Investigator 是 `qwen-plus`，本轮未换配置。
- 保持 `max_iterations=1`、temperature=0、max_tokens=8000、timeout=60、max_retries=0，外层70秒保护。没有为跑通增加轮次或延长超时。
- 不调用数据库、Scout、搜索、Investigator、风险评分或审批，不运行完整 Graph／HTTP／UI，不把 ORACLE 或本评价作为模型输入。
- 原始报告由真实渲染器从冻结 notebook 生成。`report.md` 仅是送审分析正文，不包含完整交付状态说明，不是已过审的用户报告。

首次启动请求被安全检查拒绝，进程没有启动。只读检查合成标记、五份原文哈希及服务地址后，提供这些证据的同一命令获准执行；没有改路径或绕过权限。下面的1次计数是实际模型调用数，不把被拒绝启动算成模型运行。

## 实际结果

| 指标 | 结果 |
|---|---|
| 实际审核／模型调用 | 1轮／1次 |
| 调用耗时／实验耗时 | 61.234秒／63.547秒 |
| 模型返回 | 无；异常类型 `LLMCallTimeout` |
| 故障分类 | `transport` |
| 故障代码 | `review_provider_error:LLMCallTimeout` |
| 审核有效／降级 | `analysis_review_validated=false`／`degraded=true` |
| 程序状态 | `completed`：执行结束，不代表审核通过 |
| 未解决问题 | 1条 `review_not_executed` |
| 人工复核 | `requires_human_review=true` |
| Writer调用／修订 | 均未发生 |
| 原调查状态 | 保留 `stalled` |

归档 `quality_review.score=1` 是失败兜底值，不是模型内容评分；`major_issues` 来自未审核阻断项，不表示模型已经识别了样本中的具体财务错误。既有问题的通用描述含“不可用结构”，实际失败类型应以新增的 `review_failure_kind`／`review_failure` 和调用异常记录为准。

该文案错误在运行后以程序修改修正，212项相关离线测试通过。此目录仍保存当时的原始结果，未改写旧卡片，也没有再次请求模型来制造修正后的“成功运行”。

请求中有五份已读原文、四个问题，报告、绑定引用和各份已读正文均未截断。请求未带 ORACLE；没有响应，因此无法确认模型是否正确理解这些信息。普通问题实际识别、Writer修复、历史问题逐ID再复核，本次均 `not_exercised`，不能用离线测试替代在线通过。

失败后没有再次运行，没有提高预算，也没有修改模型、提示词或样本求成功。不把传输故障称为模型推理缺陷，不把保守停止称为报告内容达标。上阶段错误内容和失败评价全部保留。

## 完整性

运行前后输入文件 SHA-256 相同：

`5D5097D19310BFF953CF02D21CE1C3E318CC4326FB6078CE5B3A2F98C21581FE`

结果中的 notebook 与输入中的 notebook 深度比较相同；没有调查状态、答案、引用或计算变更。

新归档文件 SHA-256（本地原始字节）：

| 文件 | SHA-256 |
|---|---|
| calls.json | `4e2ac2c43dacccac4fc7c345c7806b96dff3da5255c7db4172bca90cbe0a7f72` |
| input_manifest.json | `d142a821e7531a2284ab4185c621f993da7d323ef87a6b0d7bd127cf84280ccb` |
| report.md | `c7b8231ab8a32a6b378c42e49677e9bff0702fea9c84acbf9d1cd2d5e5e3ad0e` |
| result.json | `34fa665d9e81a8972b240aa271fd153063b2ea607b8ba914b49082b962ee037a` |

常见API key、Bearer和凭据字段正则扫描未命中；仅为归档前检查，不等于完整安全审计。调用记录不保存认证头或服务异常正文。
