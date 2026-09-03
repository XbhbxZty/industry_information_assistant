# 开发 Bad Case 与解决方案追踪

本台账记录开发过程中**实际遇到或通过代码审计实际确认**的 Bad Case，并把现象、根因、修复、回归证据和 Git 提交串成一条可复核链路。

它与 [`BADCASES.md`](BADCASES.md) 分工如下：

- `BADCASES.md`：产品、算法、数据与端到端评测的案例库，也允许记录明确标记的预判。
- `DEVELOPMENT_TRACE.md`：按阶段和时间记录工程实施中已确认的问题；不把假设风险写成已发生事实。

## 执行规则

从阶段 3.4C2a 之后，每个开发阶段都执行以下规则：

1. 发现问题时先登记临时条目；确认根因后再把状态改为 `已关闭`、`已缓解`、`延期` 或 `误判`。
2. 每条记录使用稳定编号 `DEV-BC-YYYYMMDD-NNN`，后续提交、测试和阶段汇报都引用该编号。
3. 条目必须包含：阶段、发现方式、可观察现象、影响、根因、解决办法、回归保护、验证结果、提交和遗留风险。
4. 只有“代码已修改 + 回归保护已落地 + 验证通过 + commit 可定位”才能标记为 `已关闭`。
5. 发现但本阶段不修的问题标记为 `延期`，写明原因和承接阶段；不得静默遗忘。
6. 误判也保留，写明推翻它的证据，避免以后重复调查同一条错误假设。
7. 阶段结束即使没有新增问题，也要在阶段汇报中写明“新增 Bad Case：无”。
8. 不记录令牌、密钥、环境变量原值、个人信息或未脱敏业务数据。

## 阶段索引

| 日期 | 阶段 | 状态 | Bad Case | 阶段提交 | 验证摘要 |
|---|---|---|---|---|---|
| 2026-08-30 | 3.4C1 档案审计连续性 | 已完成 | DEV-BC-20260830-001 ～ 003 | `62ee937` | 档案审计、事务回滚与 HTTP 契约回归通过 |
| 2026-08-31 | 3.4C2a 独立人工复核授权 | 已完成 | DEV-BC-20260831-001 ～ 004 | `7618d28` | 定向 21 项、相关 122 项、后端全量 951 passed / 2 skipped；前端构建与变更文件 lint 通过 |
| 2026-08-31 | 3.4C2b reviewer 工作台 | 已完成 | DEV-BC-20260831-005 ～ 013 | `ae2f4ac` | 定向 42 项、后端全量 962 passed / 2 skipped；前端构建、变更文件 lint 与 diff check 通过 |
| 2026-08-31 | 3.4D1 审计链密码协议 | 已完成 | DEV-BC-20260831-014 ～ 020 | `8460581` | 协议 49 项、相关定向 111 项、后端全量 1011 passed / 2 skipped；Terra High 复验无 P0/P1 |
| 2026-09-01 | 3.4D2a1 连接权威与空库基线 | 已完成 | DEV-BC-20260901-001 ～ 005 | `735e36e` | 真实 PostgreSQL upgrade/check/downgrade/re-upgrade 通过；后端全量 1033 passed / 3 skipped；Terra High 复核无 P0，3 个 P1 已关闭 |
| 2026-09-01 | 3.4D2a2.1 冻结指纹与只读预检 | 已完成 | DEV-BC-20260901-006 ～ 010 | `dc0f788` | 最终纯测+真实 PostgreSQL 定向 34 passed；阶段全量基线 1050 passed / 15 skipped；Terra High 最终无 P0/P1 |
| 2026-09-02 | 3.4D4a 检查点上下文与领取模型 | 已完成（开发版） | DEV-BC-20260902-009 ～ 015 | `e33652c` | 主定向组合 252 passed，补充档案审计迁移 6 passed；含真实 PG 与图恢复，无跳过 |
| 2026-09-02 | 3.4D4b1 领取与接受决定短事务 | 已完成（开发版，未接 HTTP） | DEV-BC-20260902-016 ～ 018 | `fc707fb` | 关联组合 163 passed，无跳过；含新增服务 54 项，其中 43 项真实 PG；临时库已清理 |
| 2026-09-02 | 3.4D4b2a 原子终稿与写入保护 | 已完成（开发版，未接 HTTP/SSE） | DEV-BC-20260902-019 ～ 022 | `42a00fe` | 关联组合 253 passed，无跳过；含新增 55 项真实 PG；迁移往返及临时库清理通过 |
| 2026-09-02 | 3.4D4b2b HTTP/SSE 与图恢复 | 已完成（开发版） | DEV-BC-20260902-023 ～ 027 | `0805b4a` | 关联 343 passed，无跳过；含新增 35 项真实 PG；前端行为 5 项、构建/lint 通过，旧类型债单列延期 |
| 2026-09-02 | 3.5 无浏览器开发功能验收 | 已通过（开发版） | DEV-BC-20260902-028 ～ 030 | `f4c19c7` | 后端关联 102 passed，无跳过；含真实独立进程/登录/PG图/SSE断流重启；前端8项、构建/lint通过，浏览器交互待补 |

> 前两阶段是从已提交代码、测试和阶段验证结果做的基线回填；3.4C2b 起均在问题处理当期登记。

## Bad Case 明细

### DEV-BC-20260830-001：审计记录“约定不可变”，但读取链路不验证连续性

- **阶段 / 状态**：3.4C1 / 已关闭
- **发现方式**：安全代码审计，并由篡改数据库记录的回归测试复现
- **现象**：删除审计记录、修改前序快照、破坏当前档案与最新快照的一致性后，旧实现仍可能返回历史或继续把当前档案送入尽调。
- **影响**：档案修订链无法作为可靠审计证据；被改写的企业档案可能继续影响授信分析。
- **根因**：旧实现只校验当前内容的 `content_sha256`，没有验证修订唯一性、前后快照衔接、操作者归属、当前行与最新审计快照一致性；“append-only”仅是代码约定，不是运行时契约。
- **解决办法**：新增结构化审计连续性校验；在更新、归档、历史读取和尽调快照读取前失败关闭；补充 `(profile_id, revision)` 唯一约束以及操作者、原因、修订号的数据库约束；审计写入失败时整笔事务回滚。
- **回归保护**：`test_audit_continuity_failure_blocks_history_and_research_snapshot`、`test_missing_audit_or_current_row_drift_blocks_profile_snapshot`、`test_fresh_schema_enforces_one_attributed_audit_per_revision`、`test_audit_failure_rolls_back_profile_update`。
- **验证结果**：上述定向回归与阶段相关测试集通过；篡改后的历史读取和尽调快照读取均失败关闭。
- **提交**：`62ee937`
- **遗留风险**：当前是结构连续性与内容一致性校验，不是密码学防篡改链；哈希链、密钥与迁移由阶段 3.4D 承接。

### DEV-BC-20260830-002：合法 JSON 可以伪装成非法领域快照并重新封装哈希

- **阶段 / 状态**：3.4C1 / 已关闭
- **发现方式**：对历史快照进行对抗性形状变异
- **现象**：把 `revision` 从整数改成字符串、把 `scenario_data` 从对象改成数组、把 `materials` 从数组改成对象后，仅重新计算 JSON 哈希并不能证明数据仍满足企业档案契约。
- **影响**：哈希值正确会制造“数据完整”的假象，下游可能在错误类型上静默降级或产生错误结论。
- **根因**：内容哈希只能证明字节内容未变化，不能证明字段集合、字段类型和领域规范正确。
- **解决办法**：校验快照精确字段集合和严格运行时类型；通过权威的 `prepare_company_profile_content` 重新构建规范化领域数据并比对投影与哈希；JSON 序列化拒绝 `NaN` 等非标准数值。
- **回归保护**：参数化测试 `test_historical_snapshot_domain_shape_cannot_be_resealed` 覆盖 `string_revision`、`list_scenario`、`dict_materials`。
- **验证结果**：三种领域形状变异即使重新计算内容哈希也全部被拒绝。
- **提交**：`62ee937`
- **遗留风险**：密码学真实性仍由 DEV-BC-20260830-001 所述的 3.4D 承接。

### DEV-BC-20260830-003：更新信用代码冲突可能越过异常映射并污染事务

- **阶段 / 状态**：3.4C1 / 已关闭
- **发现方式**：HTTP 更新路径冲突用例
- **现象**：把企业乙的统一社会信用代码更新为企业甲已占用的值时，`UPDATE` 可能在进入旧有 `try` 块之前抛出 `IntegrityError`，无法稳定返回业务冲突，且会话需要显式回滚后才能继续使用。
- **影响**：客户端可能收到 500；失败更新可能让数据库会话停留在失败事务状态。
- **根因**：数据库写操作与异常捕获边界错位，只包住了后续 `flush/commit`。
- **解决办法**：把条件更新、审计追加和提交纳入同一异常边界；所有完整性错误统一回滚并映射为 HTTP 409；保持原档案内容和修订号不变。
- **回归保护**：`test_duplicate_credit_code_update_is_409_and_rolls_back`。
- **验证结果**：冲突请求返回 409；企业乙仍保留原信用代码且 `revision` 保持为 1。
- **提交**：`62ee937`
- **遗留风险**：无已知遗留；后续数据库迁移仍需在 PostgreSQL 上验证约束名称与错误映射。

### DEV-BC-20260831-001：检查点所有者可以复核自己的尽调结论

- **阶段 / 状态**：3.4C2a / 已关闭
- **发现方式**：人工复核授权矩阵审计
- **现象**：旧端点复用了检查点读取权限；普通所有者可以向自己的暂停会话提交复核，超级管理员也可以复核自己发起的会话。
- **影响**：发起人与复核人未分离，人工复核不能构成独立的风险控制环节。
- **根因**：把“可以访问检查点”错误等同为“可以作出复核决定”，缺少专用 reviewer 能力和禁止自审规则。
- **解决办法**：新增进程启动时冻结的 reviewer allowlist，保留超级管理员应急复核能力；复核端点使用专用依赖；无论普通 reviewer 还是超级管理员均禁止自审；无归属历史检查点失败关闭。
- **回归保护**：`test_non_reviewer_is_rejected_before_checkpoint_lookup`、`test_any_authorized_role_is_forbidden_from_self_review`、`test_review_target_helper_requires_owner_and_separation_for_all_roles`、`test_reviewer_review_target_boundaries`。
- **验证结果**：未授权用户在读取检查点前即返回 403；所有授权角色自审均返回 403；合法的非所有者 reviewer 可以提交。
- **提交**：`7618d28`
- **遗留风险**：allowlist 是阶段性授权载体；持久化 reviewer 角色、管理界面和最小复核工作区由后续阶段承接。

### DEV-BC-20260831-002：跨用户复核时把复核人当成检查点所有者

- **阶段 / 状态**：3.4C2a / 已关闭
- **发现方式**：复核服务调用参数审计
- **现象**：旧端点把 `current_user.id` 同时作为复核身份和恢复图的 `user_id`；超级管理员跨用户复核时，这个值不是检查点所有者。
- **影响**：恢复后的状态归属可能漂移，或因所有权校验产生不可预测失败；审计上也混淆“谁拥有会话”和“谁做了决定”。
- **根因**：一个参数承载了两个不同身份语义。
- **解决办法**：恢复图始终传入检查点所有者 ID；复核人的用户名和不可变 ID 只写入服务器生成的 decision 元数据，客户端不能指定复核人。
- **回归保护**：`test_authorized_nonowner_reviewer_submits_server_identity_and_owner_id`、`test_复核请求不接受客户端伪造复核人`。
- **验证结果**：服务调用捕获值显示 graph `user_id` 为所有者，decision 中的 `reviewer_id` 为登录复核人；伪造 reviewer 字段被 schema 拒绝。
- **提交**：`7618d28`
- **遗留风险**：复核决定的密码学封存与跨修订链关联仍由 3.4D 承接。

### DEV-BC-20260831-003：所有者页面仍展示可提交的人工复核表单

- **阶段 / 状态**：3.4C2a / 已关闭
- **发现方式**：前后端权限契约复核
- **现象**：后端开始区分独立 reviewer 后，所有者使用的尽调页面仍显示批准、否决和覆盖等级的提交控件。
- **影响**：界面向无权用户暗示其可以复核，造成权限模型误解和稳定的 403 失败路径。
- **根因**：前端交互能力没有随服务端授权边界同步收紧。
- **解决办法**：`ReviewCard` 默认 `canSubmit=false`；所有者页面只显示等待独立风控复核的状态和可复制会话 ID；表单保留给后续专用 reviewer 工作区显式启用。
- **回归保护**：前端生产构建和变更文件 lint 通过；后端继续作为最终授权边界。
- **验证结果**：阶段前端生产构建与变更文件 lint 通过；所有者调用复核接口仍受服务端 403 契约保护。
- **提交**：`7618d28`
- **遗留风险**：专用 reviewer 队列和最小复核材料包由 3.4C2b 承接。

### DEV-BC-20260831-004：SSE 测试替身使用转义文本，无法证明真实帧边界

- **阶段 / 状态**：3.4C2a / 已关闭
- **发现方式**：开发期间复核新增 SSE 回归装置
- **现象**：测试替身一度返回字面量 `\\n`，而不是真实换行符；用例即使通过，也不能证明错误事件以合法的 `data: ...\n\n` 帧结束。
- **影响**：绿色测试可能掩盖浏览器无法正确消费复核错误事件的问题。
- **根因**：测试夹具的字符串表示与线上 SSE 协议帧不一致，断言只看内容而没有验证帧首尾。
- **解决办法**：替身改用真实换行；断言响应以 `data: ` 开始、以双换行结束，并包含结构化错误类型和错误内容。
- **回归保护**：`test_review_stream_failure_remains_a_framed_sse_error`。
- **验证结果**：真实双换行帧、结构化 `error` 类型和异常内容断言均通过。
- **提交**：`7618d28`
- **遗留风险**：无已知遗留；3.5 端到端阶段仍需用真实浏览器流再次验证。

### DEV-BC-20260831-005：复核材料可能信任未封签的行级报告

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：检查点完整性边界审计
- **现象**：`research_checkpoints.final_report` 不在业务 HMAC 内；若最小材料包直接读取该列，行级报告被改写后仍可能展示给复核人。
- **影响**：复核人可能签署一份与已封签业务状态不一致的报告。
- **根因**：关系表便捷列和封签 `state_json` 存在两份报告来源，信任源未明确。
- **解决办法**：详情包只从完整性校验后的 `state_json.final_report` 取报告；行级 `final_report` 不进入 reviewer 投影。
- **回归保护**：`test_packet_is_a_handbuilt_minimum_projection_and_uses_sealed_report` 同时放入不同的行级报告并断言其不泄漏。
- **验证结果**：定向测试和后端全量通过；最小投影只返回封签报告。
- **提交**：`ae2f4ac`
- **遗留风险**：无已知遗留。

### DEV-BC-20260831-006：未封签 status 被篡改后可把待办伪装成空队列

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：Terra High 子代理对抗性代码审查
- **现象**：队列最初以 `status='paused'` 预过滤；把仍处于封签待复核状态的行改成 `completed/failed`，任务会静默消失。
- **影响**：受损待办会被表现成“没有任务”，复核遗漏无法与真实空队列区分。
- **根因**：把未纳入 HMAC 的运维状态当成可信候选索引。
- **解决办法**：队列同时检查封签状态提示；封签仍待复核但行状态不是 paused 时返回完整性冲突，不再静默跳过。
- **回归保护**：`test_detail_forbids_self_review_and_nonpaused_or_nonpending_targets` 覆盖详情和队列两条路径的 status 不一致。
- **验证结果**：状态不一致返回 409；真实非待办仍正常排除。
- **提交**：`ae2f4ac`
- **遗留风险**：status 的密码学绑定和可信待办索引由 3.4D 正式设计承接。

### DEV-BC-20260831-007：测试使用了错误的 triggered_rules 形状

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：主代理把新增测试夹具与真实风险计分器输出逐字段对照
- **现象**：首版测试把 `triggered_rules` 写成字符串列表，但真实计分器产出对象列表；真实待办会被投影器当成非法状态拒绝。
- **影响**：测试全绿但 reviewer 无法打开正常尽调任务。
- **根因**：新接口测试自行构造了简化载荷，没有复用或核对生产 scorecard 契约。
- **解决办法**：测试改用真实对象形状；服务只投影可读 `detail`，不暴露内部 score、field 和 evidence 关联。
- **回归保护**：`test_packet_is_a_handbuilt_minimum_projection_and_uses_sealed_report` 断言规则摘要可读且内部字段不泄漏。
- **验证结果**：真实形状定向回归和全量测试通过。
- **提交**：`ae2f4ac`
- **遗留风险**：后续 scorecard 改契约时仍需同步显式 reviewer schema，不能自动透传。

### DEV-BC-20260831-008：严格读取可被直接 POST 提交绕过

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：Terra High 子代理对抗性代码审查
- **现象**：GET 工作台会拒绝 legacy/损坏检查点，但首版 POST 仍通过宽松的 `get_checkpoint_info()` 判断 owner 和 paused；有 reviewer 权限者可跳过页面直接提交。
- **影响**：缺少完整性证明或状态受损的会话可能进入图恢复，读取与写入信任边界不一致。
- **根因**：新增严格读服务没有成为提交前置条件，旧提交端点仍沿用 3.4C2a 的元数据检查。
- **解决办法**：`authorize_submission` 复用详情的完整性、owner、自审、pending 和 paused 校验并返回 owner；POST 不再用通用检查点读取器作授权决定。
- **回归保护**：`test_submission_authorization_uses_the_same_strict_pending_reader`、`test_direct_review_post_cannot_bypass_strict_integrity_reader`。
- **验证结果**：legacy/损坏会话在流开始前返回 409，研究服务未被调用。
- **提交**：`ae2f4ac`
- **遗留风险**：读取资格与最终写入之间仍非原子，见 DEV-BC-20260831-013。

### DEV-BC-20260831-009：SSE 提前结束被页面显示为复核成功

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：主代理流协议审计，并由子代理独立确认
- **现象**：首版只拦截 `error` 事件；只收到 `research_resumed + [DONE]` 或连接提前 EOF 时，也会显示“已完成后续流程”。
- **影响**：复核人可能误以为结论已经持久化并生效。
- **根因**：把传输结束等同为业务终局，没有遵守后端“只有 `research_complete` 才算生效”的契约。
- **解决办法**：消费器必须观察到 `research_complete` 才成功；任何无终局 EOF 都失败关闭并要求刷新核对，错误时清空陈旧队列。
- **回归保护**：前端生产 TypeScript 构建、变更文件 lint 和终局分支代码审计通过；3.5 再做真实浏览器断流验证。
- **验证结果**：构建与 lint 通过，代码路径不再把无终局流 resolve 为成功。
- **提交**：`ae2f4ac`
- **遗留风险**：真实代理/网络中断的浏览器级复现由 3.5 承接。

### DEV-BC-20260831-010：切换任务会复用上一任务的复核表单

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：Terra High 子代理 React 状态生命周期审查
- **现象**：`ReviewCard` 的 Form 和批准状态只在首次挂载初始化；从任务 A 切到任务 B 时组件未重建，A 的否决、意见或改判可能残留。
- **影响**：复核人可能把上一企业的决定误提交给下一企业。
- **根因**：列表详情切换只替换 props，没有用业务身份隔离组件本地状态。
- **解决办法**：以 `detail.session_id` 作为 `ReviewCard` key，每个任务独立挂载表单状态。
- **回归保护**：前端生产构建和变更文件 lint 通过；3.5 浏览器矩阵包含 A/B 任务切换。
- **验证结果**：组件身份现在与 session 绑定，构建与 lint 通过。
- **提交**：`ae2f4ac`
- **遗留风险**：浏览器交互回放由 3.5 承接。

### DEV-BC-20260831-011：OAuth token 入口会给禁用 reviewer 返回能力声明

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：Terra High 子代理认证入口一致性审查
- **现象**：`/auth/login` 拒绝禁用用户，但 `/auth/token` 曾直接签发 token，并在响应中给 allowlisted/superuser 返回 `can_human_review=true`；随后业务 API 才 403。
- **影响**：同一账号在两个登录入口得到矛盾结果，前端短暂展示无效的高权限入口。
- **根因**：OAuth2 兼容入口缺少与 JSON 登录相同的 active 检查。
- **解决办法**：token 入口在签发前统一拒绝 inactive 用户。
- **回归保护**：`test_oauth_token_login_rejects_inactive_reviewer_before_issuing_token` 断言 403 且 token 生成函数未调用。
- **验证结果**：定向和全量测试通过。
- **提交**：`ae2f4ac`
- **遗留风险**：无已知遗留。

### DEV-BC-20260831-012：跨用户报告以原始 HTML 注入 reviewer 页面

- **阶段 / 状态**：3.4C2b / 已关闭
- **发现方式**：主代理跨用户内容信任边界审计
- **现象**：既有 Markdown 组件使用 `dangerouslySetInnerHTML` 且允许原始 HTML；新工作台会把普通用户触发生成的报告展示给高权限 reviewer。
- **影响**：恶意报告内容可形成存储型 DOM 注入面，并跨越普通用户到 reviewer 的权限边界。
- **根因**：同用户报告展示组件被直接复用于跨用户审核，没有重新分类内容可信度。
- **解决办法**：Markdown 新增显式 `safe` 模式；复核页启用后转义原始 HTML、拒绝非 HTTP(S) 链接、严格限制 data image 类型并转义属性。
- **回归保护**：安全分支通过 TypeScript 生产构建和 ESLint；3.5 浏览器安全用例加入原始 HTML、事件属性和 `javascript:` 链接。
- **验证结果**：构建与 lint 通过，复核页已使用安全模式。
- **提交**：`ae2f4ac`
- **遗留风险**：既有同用户 Markdown 页面保持原行为以避免本阶段扩大重构；全局内容安全策略需另立专项。

### DEV-BC-20260831-013：owner 归属和并发复核尚未形成原子密码学裁决

- **阶段 / 状态**：3.4C2b / 延期至 3.4D
- **发现方式**：主代理持久化模型与提交时序审计
- **现象**：当前业务 HMAC 覆盖 `state_json`，但关系行 `user_id` 尚未纳入密码学绑定；两个 reviewer 也可能在同一 paused 状态上同时通过资格检查。
- **影响**：具备数据库改写能力的攻击者可能改变 owner 归属；并发提交可能产生双重决定或后写覆盖。
- **根因**：3.4C2b 只建立最小工作台和失败关闭读路径，尚无正式迁移后的可信 owner binding 与原子 compare-and-set/claim。
- **解决办法**：本阶段不伪称已解决；3.4D 将 owner、状态/版本和复核决定纳入版本化审计封签，并用 PostgreSQL 行锁或条件更新实现单一裁决。
- **回归保护**：当前测试明确覆盖禁止自审、直接 POST 防绕过和损坏检查点拒绝；3.4D 增加 owner 篡改与双 reviewer 并发测试。
- **验证结果**：当前非并发授权路径通过；原子并发与 owner 密码学绑定尚未实施。
- **提交**：延期项，无完成提交；发现基线为 `ae2f4ac`
- **遗留风险**：即本条全部风险；由 3.4D 承接，不得在 3.4C2b 完成声明中省略。

### DEV-BC-20260831-014：声明了 Alembic 依赖但没有任何正式迁移权威

- **阶段 / 状态**：3.4D 恢复审计 / 处理中（3.4D2a1 已完成，3.4D2a2～a3 承接余项）
- **发现方式**：主代理与 Terra High 子代理对仓库启动、依赖和数据库初始化路径做只读审计
- **现象**：`requirements.txt` 声明 Alembic，但仓库没有 `alembic.ini`、`env.py` 或 revision；应用导入时执行 `Base.metadata.create_all()`，Docker 初始化 SQL 又维护一份业务 schema。
- **影响**：给 ORM 增加完整性字段不会修改既有数据库；新装、Docker 和现有环境可能拥有不同表结构，回填或约束也没有可审计执行路径。
- **根因**：当前项目仍处于启动时建表和手写初始化 SQL 并存的阶段，没有建立 schema 的唯一变更权威。
- **解决办法**：D2a1 已建立手工评审的 17 表 `0001`、import-safe Alembic 环境和真实 PostgreSQL 空库生命周期测试；D2a2 再冻结旧库指纹与 adoption 准入，D2a3 最后移除三处运行时 `create_all()` 并切换 Docker/启动 guard。
- **回归保护**：D2a1 已有 metadata 表集合、单一 head、禁止迁移导入 `app_main`/调用 `create_all`、empty upgrade、`alembic check`、downgrade/re-upgrade；D2a2 继续增加 legacy preflight、drift rejection 和异常回滚测试。
- **验证结果**：D2a1 在随机临时 PostgreSQL 数据库完成 upgrade/check/downgrade/re-upgrade，`0001` 与当前 `Base.metadata` 无漂移；运行时建表和旧 Docker DDL 按阶段边界仍未切除。
- **提交**：发现基线 `bbc6e0e`；D2a1 缓解提交 `735e36e`
- **遗留风险**：旧库尚不能安全 stamp/adopt，三处 `create_all()` 与 Docker 旧 DDL 要到 D2a3 才退出；D2a 完成前不得宣称唯一 schema 权威已在所有启动路径生效。

### DEV-BC-20260831-015：普通 SHA 可以随被篡改历史一起重新计算

- **阶段 / 状态**：3.4D1 → 3.4D2b / 已关闭（开发版代码；已有库待显式迁移）
- **发现方式**：现有 3.4C1 审计实现与篡改测试复核
- **现象**：当前 `content_sha256` 和快照连续性不含秘密；拥有数据库写权限者可以改写领域快照、重算公开 SHA，并同步修改后继 before/current 行。
- **影响**：3.4C1 可以发现意外损坏和未完整重算的篡改，但不能证明记录由持有独立审计密钥的应用签发。
- **根因**：内容摘要用于一致性校验，不具备消息认证能力；模型尚无 key id、前序 MAC 或审计 MAC。
- **解决办法**：D1 冻结版本化 HMAC 记录、完整快照摘要、显式 keyring、legacy anchor 和完整观测验证协议；D2b 已增加迁移，并将其接入实际档案读写边界。
- **回归保护**：`test_mutating_any_persisted_audit_field_fails_closed`、`test_native_chain_crosses_key_rotation_without_resigning_history`、`test_observed_chain_binds_actual_snapshots_domain_semantics_and_current_row`。
- **验证结果**：D1 当时完成纯协议 49 项测试；D2b 已接实际服务并通过 191 项定向组合测试。`test_recomputed_plain_hashes_and_metadata_forgery_cannot_pass` 证明重算业务及完整快照 SHA、同步修改当前行后，旧结构检查虽通过，HMAC 边界仍拒绝。真实 PostgreSQL 测试验证签名写入、旧链锚定和回滚。
- **提交**：协议 `8460581`；实际接线 `5bb6704`
- **遗留风险**：已有业务库未自动迁移；需配置独立密钥并在线升级 0002。HMAC 不证明锚定前历史真实性，也不抵抗签名密钥泄露或整库与 head 一起回滚。

### DEV-BC-20260831-016：检查点 key id 只能识别当前 secret，轮换会拒绝全部旧记录

- **阶段 / 状态**：3.4D 恢复审计 → 3.4D3 / 已关闭（开发版）
- **发现方式**：主代理与 Terra High 子代理审计 `checkpoint_integrity.py`
- **现象**：检查点 key id 从当前环境 secret 派生，验签也只重算当前 key id；切换 secret 后没有按记录 key id 查找历史验证 key 的路径。
- **影响**：正常密钥轮换会让所有旧检查点不可恢复；若为兼容而回退 active/JWT key，又会形成降级验证风险。
- **根因**：现有检查点封签是单 key 配置，不是版本化 verifier keyring。
- **解决办法**：D3 新增显式 checkpoint keyring，按持久化派生指纹验旧/新 key；v1 原签名字节不变。无 key ID 的 v2 快照只认明确 legacy key，新快照用带 key ID 的 v3；统计业务、图、内层快照等所有本工具可读取的引用，阻止误退役。
- **回归保护**：`test_old_checkpoint_restores_unchanged_after_rotation_and_new_writes_use_active`、`test_inventory_counts_inner_snapshot_and_blocks_retirement_without_writes`、`test_real_langgraph_review_resumes_across_key_rotation`。
- **验证结果**：D3 组合 212 项通过，含真实 PostgreSQL 7 项和真实 LangGraph MemorySaver；最后定向复跑 15 项通过。未启用新配置时保留原兼容路径，显式模式不得 fallback。
- **提交**：发现基线 `bbc6e0e`；完成 `f40832a`
- **遗留风险**：需按 KEY_ROTATION.md 保留旧 key 并显式部署；备份/其他数据库/运行中任务未纳入本库统计，非空 LangGraph 历史保守阻断退役，不宣称全局无引用。

### DEV-BC-20260831-017：数据库内 HMAC 链不能单独证明整个合法后缀曾经存在

- **阶段 / 状态**：3.4D1 / 已缓解
- **发现方式**：主代理对“拒绝截断”验收语句做密码学反证
- **现象**：若攻击者同时把尾部审计、当前档案和数据库内预期 head 回滚到同一个旧的合法版本，剩余 HMAC 仍然有效，验证器无法仅从当前数据库证明更新版本曾经存在。
- **影响**：把 HMAC 前序链表述成对高权限离线整库回滚也安全，会制造超过实际能力的审计承诺。
- **根因**：前序链证明当前所见记录没有被中间改写；没有数据库外单调状态时，旧的合法前缀与“从未产生后缀”不可区分。
- **解决办法**：D1 强制调用方提供 expected head，检测 head 未同步回滚时的截断；D2b 增加 append-only 数据库约束并修正文档威胁模型。抵抗高权限离线回滚需后续外部时间戳、透明日志或 KMS 单调锚点。
- **回归保护**：`test_chain_rejects_truncation_reorder_insertion_and_cross_profile_copy` 验证可信 head 下的截断拒绝；代码和计划均显式写出整体回滚边界。
- **验证结果**：普通缺记录、重排和复制测试通过；高权限同步回滚未被错误标记为已解决。
- **提交**：协议缓解 `8460581`；边界文档由本阶段收尾提交记录
- **遗留风险**：3.4D 不包含数据库外单调锚定；有该合规威胁时必须另立专项。

### DEV-BC-20260831-018：只验 MAC 的低层函数会接受“归档动作 + 生效快照”

- **阶段 / 状态**：3.4D1 / 已关闭
- **发现方式**：Terra High 子代理对抗复核并运行最小反例
- **现象**：首版 `verify_audit_chain()` 只检查摘要和 MAC 链；一条由合法 key 签发、`action=archived` 但实际 after snapshot 仍为 `status=active` 的记录可以通过低层链验证。
- **影响**：若 D2 误把低层密码信封校验直接当作生产完整性校验，会比 3.4C1 现有领域约束更弱。
- **根因**：MAC 只认证调用方给出的摘要和元数据，低层函数没有接收实际 before/after/current 快照，也无法运行领域重建。
- **解决办法**：新增唯一持久化候选入口 `verify_observed_audit_chain()`，强制实际快照、当前行和领域 validator，逐条绑定 profile/revision/content/action/status；低层函数 docstring 明确不得单独用于持久化边界。
- **回归保护**：`test_crypto_only_chain_cannot_replace_observed_action_and_status_validation` 保留低层反例，并证明完整观测入口拒绝它。
- **验证结果**：子代理复验确认原反例在完整观测入口失败，未发现新增 P0/P1；49 项 D1 测试通过。
- **提交**：`8460581`
- **遗留风险**：D2b 必须接完整观测入口并继续复用 3.4C1 的严格领域重建器和 actor/current-row 关系校验。

### DEV-BC-20260831-019：legacy anchor 验签不等于复验它所声明的旧历史

- **阶段 / 状态**：3.4D1 / 已关闭
- **发现方式**：Terra High 子代理对抗复核
- **现象**：首版主链入口会验证 anchor MAC 和首条后缀链接，但不强制调用方提供实际 legacy rows 和 terminal snapshot；只调用该入口可能遗漏迁移后旧数据漂移。
- **影响**：锚点本身有效会被误解成数据库中的旧历史仍与锚定时完全相同。
- **根因**：密码锚点记录验证与被锚定对象的观测比对被设计为两个可选调用，存在漏调旁路。
- **解决办法**：完整观测入口在 legacy 分支强制同时接收实际旧序列和终态快照，内部先执行 anchor observation，再验证领域语义、后继链和当前行；缺任一观测立即失败。
- **回归保护**：`test_legacy_anchor_supports_anchor_only_state_and_new_key_continuation`、`test_legacy_anchor_observation_rejects_changed_history_and_terminal_snapshot` 及缺失观测断言。
- **验证结果**：子代理复验确认 legacy 分支没有可选漏调路径，未发现新增 P0/P1。
- **提交**：`8460581`
- **遗留风险**：D2b 回填仍需在单事务中先运行现有旧结构连续性校验；D1 不会自行读取数据库。

### DEV-BC-20260831-020：v1 canonical JSON 的浮点格式是 Python 运行时协议

- **阶段 / 状态**：3.4D1 / 已缓解
- **发现方式**：Terra High 子代理跨实现 canonicalization 审计
- **现象**：标准库 `json.dumps` 对有限 float 有确定的 Python 输出，但它不是 RFC 8785；例如 `-0.0`、`1.0` 和指数格式需要非 Python 实现逐字节仿真。
- **影响**：未来若由 JavaScript、Java 或数据库函数直接签发同一 v1 payload，数值文本差异可能造成同一业务数据验签失败。
- **根因**：现有企业档案允许数值字段，不能简单禁用 float；本阶段也没有引入跨语言 JCS 依赖。
- **解决办法**：v1 明确限定为 Python 签发/验签协议，并为负零、小数和指数增加固定字节向量；任何非 Python 消费端必须先定义共同实现的新版本，不能悄悄复用 v1 名称。
- **回归保护**：`test_v1_float_rendering_is_explicitly_locked_for_python_consumers` 和 Unicode/null 固定向量。
- **验证结果**：当前单一 Python 后端固定向量通过；跨语言一致性未宣称已验证。
- **提交**：`8460581`
- **遗留风险**：引入非 Python 签发/验签端时需升级到 JCS 等明确规范，并为旧 v1 保留只读验证器。

### DEV-BC-20260901-001：同一进程中的数据库消费者可能连接不同目标

- **阶段 / 状态**：3.4D2a1 / 已关闭
- **发现方式**：主代理与 Terra High 子代理对 ORM、Text2SQL、LangGraph 和迁移入口做连接审计
- **现象**：ORM 忽略显式 `DATABASE_URL` 并手拼 `POSTGRES_*`，Text2SQL 优先读取 `DATABASE_URL`，LangGraph 又把 ORM URL 直接交给 psycopg3；请求期重新读取环境还可能偏离启动时已冻结的 ORM 目标。
- **影响**：应用读写、数据库探索、检查点和迁移可能落到不同数据库；看似成功的迁移不能证明正在服务的库已升级。
- **根因**：连接配置在多个调用点重复解释，并混淆 SQLAlchemy psycopg2 URL 与 psycopg3 conninfo。
- **解决办法**：新增 import-safe 单一解析器，统一输出等价的 SQLAlchemy psycopg2 URL 和 psycopg3 conninfo；ORM 在启动时解析一次，Text2SQL 复用同一 `DATABASE_URL` 快照，LangGraph 使用对应 `PSYCOPG_CONNINFO`，Alembic 复用同一解析器。
- **回归保护**：`test_database_url_takes_priority_and_normalizes_both_driver_forms`、`test_component_values_are_percent_encoded_for_both_consumers` 及数据库探索器权限回归。
- **验证结果**：连接定向测试、真实 PostgreSQL 迁移测试与后端全量通过；对抗复核确认请求期环境漂移路径已关闭。
- **提交**：`735e36e`
- **遗留风险**：已经导入并创建的 engine 不支持进程内热切换数据库；配置改变必须重启进程，这是有意的单一启动快照语义。

### DEV-BC-20260901-002：空白 DATABASE_URL 会静默回退到另一组连接变量

- **阶段 / 状态**：3.4D2a1 / 已关闭
- **发现方式**：Terra High 子代理对抗复核并以空白环境变量复现
- **现象**：首版解析器在 `DATABASE_URL` 存在但值为空白时，静默改用 `POSTGRES_*`，可以成功生成一个指向其他主机的 URL。
- **影响**：显式配置错误不报错，迁移或服务可能误连到操作者没有选择的数据库。
- **根因**：把“变量缺失”和“变量存在但非法”合并成同一 fallback 分支。
- **解决办法**：只有键完全缺失时才允许组件变量 fallback；只要 `DATABASE_URL` 存在就必须是非空合法 PostgreSQL URL，否则失败关闭。
- **回归保护**：`test_present_but_blank_database_url_never_falls_back_to_components`，并参数化覆盖空字符串与纯空白。
- **验证结果**：空白显式配置稳定抛出 `DatabaseUrlConfigurationError`，不会生成 fallback URL；全量回归通过。
- **提交**：`735e36e`
- **遗留风险**：无已知遗留。

### DEV-BC-20260901-003：合法的无端口 PostgreSQL URL 被连接权威拒绝

- **阶段 / 状态**：3.4D2a1 / 已关闭
- **发现方式**：Terra High 子代理兼容性审计与最小 URL 复现
- **现象**：首版要求显式 URL 必须包含端口，`postgresql://user:password@host/database` 虽符合 PostgreSQL 常规默认端口语义却被拒绝。
- **影响**：现有标准部署配置升级后可能在启动阶段无故失败，形成兼容性退化。
- **根因**：输入规范化把“未指定端口”错误地当成“非法端口”，没有应用 PostgreSQL 默认值 5432。
- **解决办法**：未指定端口时规范化为 5432；端口 0、超界和语法错误仍失败关闭。
- **回归保护**：`test_explicit_url_without_port_uses_postgresql_default` 及非法端口参数化测试。
- **验证结果**：无端口 URL 同时生成带 5432 的 psycopg2/psycopg3 形式；非法端口仍全部拒绝。
- **提交**：`735e36e`
- **遗留风险**：无已知遗留。

### DEV-BC-20260901-004：迁移静态测试会把注释误判成启动入口导入

- **阶段 / 状态**：3.4D2a1 / 已关闭
- **发现方式**：首次运行新增迁移契约测试
- **现象**：测试用字符串包含关系寻找 `app_main`，因此 `env.py` 中解释“不得导入 app_main”的注释反而触发失败；另一个 import-safe 子进程因未加入 `backend/app` 路径而报模块不存在。
- **影响**：测试失败反映的是装置缺陷而不是产品契约，可能诱导开发者删除有价值的安全说明，且无法真正证明导入边界。
- **根因**：静态断言没有解析 Python 语法树，子进程也没有复现项目实际模块搜索路径。
- **解决办法**：使用 AST 只检查真实 import 与 `create_all` 调用节点；子进程显式加入应用模块路径后验证只导入 URL resolver 不会构造 engine。
- **回归保护**：`test_migration_runtime_never_imports_fastapi_startup_or_calls_create_all`、`test_importing_url_resolver_does_not_construct_the_application_engine`。
- **验证结果**：修正后静态契约测试通过，并保留 `env.py` 的边界说明注释。
- **提交**：`735e36e`
- **遗留风险**：AST 测试只约束直接导入/调用；真实 PostgreSQL 生命周期和 `alembic check` 继续作为运行时互补证据。

### DEV-BC-20260901-005：pytest 把需外部凭据的异步演示脚本当成确定性测试

- **阶段 / 状态**：3.4D2a1 / 已关闭
- **发现方式**：从 `backend/` 首次执行无路径参数的全量 `pytest -q`
- **现象**：pytest 递归收集 `app/scripts/test_deep_research_v2.py` 中两个供 `asyncio.run()` 手工执行的异步函数，并因未使用 async 测试插件报 2 个失败；其余 1030 项当时已通过。
- **影响**：默认全量命令无法作为稳定检查点，且容易把外部 API 演示脚本的装置问题误报为业务回归。
- **根因**：仓库没有冻结确定性测试根，而手工脚本文件名和函数名符合 pytest 默认发现模式。
- **解决办法**：新增 `backend/pytest.ini`，把确定性测试根固定为 `backend/tests`，并注册真实 PostgreSQL 集成标记；手工脚本仍按其文档通过 `python -m scripts.test_deep_research_v2` 显式执行。
- **回归保护**：从 `backend/` 直接执行 `python -m pytest -q` 必须只收集确定性测试树。
- **验证结果**：默认全量命令最终 `1033 passed / 3 skipped`；真实 PostgreSQL 用例另行显式执行 `1 passed`。
- **提交**：`735e36e`
- **遗留风险**：手工外部 API 脚本仍不是 CI 测试；若要自动化，需另行提供测试凭据、async 插件和隔离的网络验收环境。

### DEV-BC-20260901-006：seed 脚本只会产生三张行业表的判断是误判

- **阶段 / 状态**：3.4D2a2.1 / 误判
- **发现方式**：独立 Python 进程和真实 PostgreSQL 临时库重建
- **现象**：初步审计曾把 `seed_industry_data` 当成三表 legacy 变体；实际导入 `models.industry_data` 会先执行 `models/__init__.py`，最终向同一 `Base.metadata` 注册 17 张表。
- **影响**：若保留错误三表 profile，会增加不存在的 adoption 分支并削弱支持矩阵。
- **根因**：只阅读 seed 文件的直接模型引用，没有验证 Python 包导入副作用后的完整 metadata。
- **解决办法**：删除三表假设；支持矩阵只保留经临时库复现的 base-full 与 Docker 变体。
- **回归保护**：真实 PG `Base.metadata.create_all()` 用例断言表集合精确等于 17 张 `APPLICATION_TABLES`。
- **验证结果**：独立进程与临时库结果一致；计划已在 `95b9443` 更正，定向矩阵通过。
- **提交**：计划更正 `95b9443`；代码检查点 `dc0f788`
- **遗留风险**：Python 包导入仍有副作用，正式移除运行时 `create_all()` 由 3.4D2a3 承接。

### DEV-BC-20260901-007：fake catalog 测试漏掉驱动对百分号的参数解释

- **阶段 / 状态**：3.4D2a2.1 / 已关闭
- **发现方式**：首次在真实 psycopg2/PostgreSQL 运行 catalog 查询
- **现象**：schema 过滤 SQL 中的百分号被 psycopg2 当作参数格式符，真实库抛出 `TypeError`，而 fake connection 测试全部通过。
- **影响**：只读预检无法在真实目标运行，纯替身绿色结果形成假安全感。
- **根因**：`exec_driver_sql` 仍经过 DBAPI 参数规则；fake 只按 SQL 标记返回行，没有执行驱动解析。
- **解决办法**：改用不含百分号占位歧义的 PostgreSQL 正则；把真实 PostgreSQL 变体矩阵设为阶段验收证据。
- **回归保护**：`test_legacy_schema_preflight_postgres.py` 的所有场景都执行完整 catalog SQL。
- **验证结果**：最终纯测和真实定向合计 34 项通过。
- **提交**：`dc0f788`
- **遗留风险**：新增 catalog SQL 不能只靠 fake 测试验收，必须继续保留真实 PG 套件。

### DEV-BC-20260901-008：Unicode 传输和自洽摘要不足以冻结稳定 manifest ID

- **阶段 / 状态**：3.4D2a2.1 / 已关闭
- **发现方式**：Windows patch/console 边界复验与 manifest 篡改测试
- **现象**：首轮含中文 default 的 JSON 在传输中出现替换字符，内存摘要与落盘内容不一致；只校验文件自带摘要时，也可同时替换 catalog 和摘要而沿用旧 ID。
- **影响**：profile 可能因传输损坏不可加载，或在稳定 ID 下被静默换成另一套结构。
- **根因**：可移植字节编码未冻结，摘要信任根仍位于被校验文件内部。
- **解决办法**：manifest 统一使用 ASCII JSON Unicode escape；稳定 ID 绑定代码内硬编码 SHA-256，并校验声明对象集合与 catalog 投影完全一致。
- **回归保护**：ASCII transport、embedded digest、frozen digest rebinding、声明不一致测试。
- **验证结果**：六个真实 PG manifest 重新捕获并通过双重摘要和声明校验。
- **提交**：`dc0f788`
- **遗留风险**：更新 profile 必须使用新 ID 和人工评审，不能原地替换旧 ID。

### DEV-BC-20260901-009：首版指纹遗漏可改变写入与授权语义的数据库对象

- **阶段 / 状态**：3.4D2a2.1 / 已关闭
- **发现方式**：两路 Terra High 对抗复核并用真实 PostgreSQL 反例验证
- **现象**：首版没有覆盖 rewrite rule、独立 enum/domain、ACL/owner/default ACL 和显式 routine/rewrite 依赖；在精确 17 表上增加这些对象可能仍接近 `exact_adoptable`。
- **影响**：rule 可改写 INSERT，PUBLIC grant 可暴露敏感表，未知类型/跨 managed-unmanaged 依赖会让 stamp 后语义并不等价。
- **根因**：早期 catalog 过度聚焦列、约束、索引与 trigger，没有把安全/授权和独立对象作为 schema 语义。
- **解决办法**：补齐 RLS/policy/rule/type、owner/ACL、routine 安全属性和依赖边；默认拒绝未知全局对象，并显式拒绝跨边界 FK/trigger。
- **回归保护**：纯测与真实 PG 覆盖 rule、PUBLIC ACL、default ACL、enum/domain、SQL routine 依赖、跨边界 FK/trigger、RLS 和 event trigger。
- **验证结果**：新增反例均返回 `schema_drift`，零 stamp；Terra High 最终复核无 P0/P1。
- **提交**：`dc0f788`
- **遗留风险**：PL/pgSQL 动态 SQL 不保证进入 `pg_depend`；因此任何未被精确 manifest 覆盖的 routine/rule/trigger 仍直接拒绝，而不是推断为无依赖。

### DEV-BC-20260901-010：错误输出和测试失败可能回显敏感连接或 DDL 字面量

- **阶段 / 状态**：3.4D2a2.1 / 已关闭
- **发现方式**：限权角色集成测试首次失败及最终输出审计
- **现象**：pytest fixture dataclass 的默认 repr 会在失败上下文显示带凭据连接串；CLI 若原样序列化 diff，也可能输出函数、rule 或 default 中内嵌的敏感字面量；Alembic 日志还一度污染 JSON 断言。
- **影响**：CI/操作日志可能泄漏凭据或数据库定义中的秘密，并破坏机器可读错误协议。
- **根因**：内部诊断对象与面向操作者的安全输出没有分层，测试 capture 未清空前序日志。
- **解决办法**：连接字段 `repr=False`；CLI 不回显 DBAPI 文本，DDL/表达式值只返回 redacted 标记和 SHA-256；限权用例在调用 CLI 前清空前序日志。
- **回归保护**：单元和真实限权角色测试验证稳定 `legacy_preflight_permission_denied`、无 URL；报告测试验证 `CREATE RULE` 不出现在 JSON。
- **验证结果**：限权角色真实用例与最终 34 项定向矩阵通过，随机角色按 name/OID/cluster identity 清理。
- **提交**：`dc0f788`
- **遗留风险**：测试进程被硬杀时可能遗留随机前缀角色，后续测试运维可增加孤儿扫描；不影响生产预检只读路径。

### DEV-BC-20260901-011：稳定 policy ID 未绑定内容且目标身份缺少规范化定义

- **阶段 / 状态**：3.4D2a2a / 已关闭
- **发现方式**：主代理对初版纯契约的提交前审计
- **现象**：初版 approval 只引用 `target_policy_id`，同一 ID 下替换 server/database 目标内容仍可通过；数据库目标还使用自由文本 binding，profile 写成未冻结的 `base-full`。
- **影响**：部署策略可在审批后被静默重绑定，调用方也可能用不同字符串规范描述同一或不同目标，削弱错库保护。
- **根因**：稳定标识符被误当成不可变内容凭证，且 D2a2.1 的冻结 profile 与 PostgreSQL 物理/逻辑身份尚未形成同一 canonical 协议。
- **解决办法**：approval 同时绑定 policy ID 与 canonical policy SHA-256；复用 `BASE_MANIFEST_ID`；分别冻结包含 cluster system identifier/address/port/version 和 database name/OID/owner 的身份摘要格式。
- **回归保护**：`test_policy_and_runtime_target_mismatch_fail_closed`、`test_canonical_target_identity_changes_when_any_bound_value_changes` 及固定 Unicode/hash 向量。
- **验证结果**：策略内容在相同 ID 下变化、任一目标身份字段变化和歧义输入均失败关闭；定向组合测试通过。
- **提交**：`5245101`
- **遗留风险**：D2a2b 必须从受保护部署配置加载 policy，并在锁内从 PostgreSQL 重新计算身份；客户端传入同结构 JSON 不能成为信任根。

### DEV-BC-20260901-012：非 UTC 服务端时钟会在校验前被静默归一化

- **阶段 / 状态**：3.4D2a2a / 已关闭
- **发现方式**：主代理时间边界审计
- **现象**：初版先调用 `astimezone(UTC)` 再检查 offset，因此 `UTC+08:00` 等 aware datetime 会被接受，尽管接口契约要求执行层显式提供 UTC 时钟。
- **影响**：调用方违反可信时钟协议时不会失败，时间来源/单位错误更难在 adoption 前暴露。
- **根因**：规范化发生在输入前置条件验证之前。
- **解决办法**：先要求 `server_now` timezone-aware 且原始 UTC offset 精确为零，再做 UTC 归一化；`now == expires_at` 明确判定过期。
- **回归保护**：`test_expiry_future_and_ttl_use_supplied_server_clock_only` 覆盖非 UTC aware、未来确认、TTL 超限和到期相等边界。
- **验证结果**：非 UTC 时钟稳定返回 `invalid_timestamp`，精确到期返回 `approval_expired`；定向组合测试通过。
- **提交**：`5245101`
- **遗留风险**：D2a2b 必须使用同一 writer connection 读取 PostgreSQL 时间，不能把客户端时间转成 UTC 后冒充服务端时钟。

### DEV-BC-20260901-013：审批中的 preflight digest 曾是未参与门禁的孤立字段

- **阶段 / 状态**：3.4D2a2a / 已关闭
- **发现方式**：Terra High 子代理最终对抗复核
- **现象**：候选实现只验证 `expected_preflight_sha256` 的 64 位格式，没有纯入口将它与 D2a2.1 的当前报告、`exact_adoptable` 状态和 base-full profile 同时比较。
- **影响**：未来执行器若遗漏手工拼接校验，任意形状合法的 digest 可能通过审批验证，锁内二次指纹成为可选步骤。
- **根因**：审批、目标和 preflight 各自有局部 validator，但缺少不可跳过的组合协议。
- **解决办法**：新增 `validate_approved_preflight` 和统一 `validate_adoption_contract`；要求 fresh report 为 `EXACT_ADOPTABLE`、冻结 profile、无 unmanaged package/差异，且 snapshot/preflight digest 合法、审批摘要精确相等。
- **回归保护**：`test_approved_preflight_requires_fresh_exact_report_and_digest`、`test_approved_preflight_rejects_untyped_or_malformed_report`、`test_composite_contract_applies_approval_target_and_preflight_gates`。
- **验证结果**：漂移状态、profile 不符、摘要变化、unmanaged package、差异和错误目标均失败关闭；阶段全量 `1071 passed / 18 skipped`。
- **提交**：`5245101`
- **遗留风险**：纯函数不能证明 report 来源；D2a2b 必须只传入取得锁后由同一 writer connection 重采集的报告，并单独强制认证、受保护 policy 来源和 approval replay 语义。

### DEV-BC-20260902-001：固定 public 版本表后 Alembic check 误报删除版本表

- **阶段 / 状态**：轻量第一批 D2a2b＋D2a3 / 已关闭
- **发现方式**：真实 PostgreSQL upgrade 后执行 `command.check`。
- **现象 / 根因**：`version_table_schema="public"` 与 `include_schemas=False` 的默认 schema 反射不一致，产生 `remove_table(alembic_version)` 差异。
- **解决办法**：autogenerate 精确排除版本表与已知七张演示表、LangGraph 表，不使用前缀放行；未知表仍可报告差异。
- **回归保护 / 验证**：`test_real_head_guard_rejects_unmigrated_stale_and_accepts_upgrade`、`test_demo_seed_requires_head_then_creates_once_without_migration_drift` 及迁移全生命周期真实测试通过；本批合计 `165 passed`。
- **提交**：`27ad6a0`
- **遗留风险**：这些 unmanaged 表不由 ORM/Alembic 迁移；其结构正确性仍由各自维护路径负责。

### DEV-BC-20260902-002：新演示 seed 的连接配置与写入 schema 没有对齐

- **阶段 / 状态**：轻量第一批 D2a3 / 已关闭
- **发现方式**：主审代码检查。
- **现象 / 根因**：首版直接调用 import-safe URL resolver，未加载 `backend/.env`；存在性检查固定 public，但未限定的 DDL/DML 又依赖会话 search_path，读写目标可能不一致。
- **解决办法**：CLI 在解析 URL 前加载 `.env`，保留进程环境优先；同一 seed 事务内设置 public search_path、检查 head 和已有表，再创建/插入。已有任一演示表直接拒绝。
- **回归保护 / 验证**：`test_demo_cli_loads_dotenv_before_resolving_target` 验证顺序；真实 PG 以非默认 search_path 验证建表、重复拒绝和数据计数不变，通过。
- **提交**：`27ad6a0`
- **遗留风险**：演示 seed 是显式一次性命令，不做已有演示数据的升级或合并。

### DEV-BC-20260902-003：扩大 Docker 构建目录后可能把本地环境凭据打入镜像

- **阶段 / 状态**：轻量第一批 D2a3 / 已关闭
- **发现方式**：主审 Dockerfile 构建范围检查，未执行镜像构建。
- **现象 / 根因**：为包含 Alembic，构建上下文改为 backend/，而 `COPY . /app/` 在缺少 `.dockerignore` 时也会包含本地 `.env`。
- **解决办法**：新增 backend/.dockerignore，排除 `.env`、环境变体和缓存，仅保留示例配置；文档明确使用运行时配置。
- **回归保护 / 验证**：启动配置静态测试检查 `.env` 排除项，compose 配置检查通过；本阶段不宣称已完成镜像部署验收。
- **提交**：`27ad6a0`
- **遗留风险**：镜像实际构建和部署仍待后续验收。

### DEV-BC-20260902-004：新增 ORM 字段后旧库夹具不能再跟随当前模型建表

- **阶段 / 状态**：轻量第二批 D2b / 已关闭（测试夹具）
- **发现方式**：主审旧库冻结契约，随后真实 PostgreSQL 关联回归。
- **现象 / 根因**：旧 preflight 夹具使用当前 `Base.metadata.create_all` 重建 0001；增加锚点和签名列后不再代表旧库。首版替换为完整 baseline upgrade，又使 Docker 混合库的两个测试因已有 `users` 表报 `DuplicateTable`。
- **解决办法**：测试夹具从冻结 0001 revision 构建；混合库仅在测试内拦截建表/索引操作、跳过已有表，复现旧 create_all 的行为。生产迁移不使用这种跳过机制，原冻结 manifest 不变。
- **回归保护 / 验证**：`test_docker_legacy_variants_are_known_incompatible_and_never_stamped`、`test_real_managed_unmanaged_fk_and_trigger_edges_are_explicitly_rejected`；关联组合 82 项通过，最终定向组合 191 项通过。
- **提交**：`5bb6704`
- **遗留风险**：adoption/preflight 固定面向 0001 旧库；已升级 0002 的库应用 Alembic current/check 与 head guard，不应再走旧库 adoption。

### DEV-BC-20260902-005：旧回滚测试通过详情服务读取损坏档案，与新验链契约冲突

- **阶段 / 状态**：轻量第二批 D2b / 已关闭（测试断言失配）
- **发现方式**：第一次定向测试出现 1 failed / 80 passed。
- **现象 / 根因**：测试故意破坏审计后，仍调用 `get_company_profile` 读取 revision 以证明更新已回滚；现在详情与研究入口统一失败关闭，该读取正确抛出完整性异常。
- **解决办法**：回滚落盘状态改用测试内 ORM 查询核对，同时断言详情服务必须拒绝损坏档案；不放宽实际服务校验。
- **回归保护 / 验证**：`test_audit_continuity_failure_blocks_history_and_research_snapshot` 及所有消费边界 409/503 测试通过；最终定向组合 191 项通过。
- **提交**：`5bb6704`
- **遗留风险**：无已知遗留。

### DEV-BC-20260902-006：密钥配置对象默认 repr 会包含原始密钥

- **阶段 / 状态**：轻量第三批 D3 / 已关闭
- **发现方式**：主代理复核新增 `CheckpointKeyring` dataclass。
- **现象 / 根因**：候选实现 `_keys` 使用 dataclass 默认 repr，调试打印或失败上下文可能包含原始 bytes。未观察到用户真实密钥泄露。
- **解决办法**：`_keys` 使用 `field(repr=False, compare=False)`；报告显式投影 key ID/计数，不序列化 keyring。
- **回归保护 / 验证**：`test_keyring_repr_does_not_expose_key_material` 断言 repr 不含原始或 Base64 secret；CLI 脱敏与错误输出测试通过。
- **提交**：`f40832a`
- **遗留风险**：密钥仍需由受保护环境提供，不应主动打印 `key_material()` 返回值。

### DEV-BC-20260902-007：隐式数字解析让非规范指纹和版本越过形状检查

- **阶段 / 状态**：轻量第三批 D3 / 已关闭
- **发现方式**：主代理与 Terra/high 对签名字段边界的复核。
- **现象 / 根因**：`int(value, 16)` 接受全角数字，之后 `compare_digest` 会抛 TypeError 而非完整性异常；快照 MAC 仅检查长度也有同类问题。v3 的 `version == 3` 还会接受 `3.0`，因为 JSON 类型差异没有显式验证。
- **解决办法**：指纹和快照 MAC 限定 ASCII 小写十六进制；快照版本必须为精确 int，再按 v2/v3 验证形状。
- **回归保护 / 验证**：全角 key ID 测试、`test_explicit_snapshot_bindings_are_v3_and_bind_alias` 中的浮点版本断言、`test_snapshot_non_ascii_mac_is_a_validation_error_not_compare_digest_type_error` 均通过；最终相关 15 项通过。
- **提交**：`f40832a`
- **遗留风险**：无已知遗留。

### DEV-BC-20260902-008：持久化缺失 key_id 会误用底层函数的兼容默认值

- **阶段 / 状态**：轻量第三批 D3 / 已关闭
- **发现方式**：主代理新增反例，先复现 `DID NOT RAISE`，修复后同一测试通过。
- **现象 / 根因**：为兼容旧直接调用，底层 `verify_business_state_seal(key_id=None)` 使用 active；服务把损坏的持久化 `None` 原样传入后，会在当前 MAC 仍有效时接受缺失 key ID。数据库非空约束能挡正常写入，但服务边界不应依赖此默认值。
- **解决办法**：持久化入口先要求非空字符串 key ID，然后才调用按记录指纹的验证器；底层省略参数的旧直接调用仍保留。
- **回归保护 / 验证**：`test_persistence_never_treats_missing_stored_key_id_as_use_active` 从失败变为通过，并纳入 212 项组合及最终定向复跑。
- **提交**：`f40832a`
- **遗留风险**：owner/status/version 的完整可信绑定仍由 D4a 承接，本修复不代表该项已完成。

### DEV-BC-20260902-009：研究内容已封签，但归属和工作流状态未绑定

- **阶段 / 状态**：D4a / 已关闭
- **发现方式**：恢复审计与上下文篡改测试。
- **现象 / 根因 / 影响**：旧业务 MAC 只绑定 state/session/revision；owner/status 可单独改写，缺失 Integrity 还会被当作可读取的旧记录。保存旧记录会重新赋予完整性元数据，可能掩盖损坏。
- **解决办法**：保留 v1 签名字节，新增独立上下文域，绑定 checkpoint/session/owner/status/revision/business MAC。所有持久化读取先验三层封签；缺失配对不再兼容。写入先锁定并验旧值，再递增版本、同时重签。旧迁移仅生成明确标识的观测封签。
- **回归保护 / 验证**：`test_context_binds_every_identity_and_basis_field`、`test_real_corruption_never_passes_read_or_save`、真实 PG 迁移失败回滚与并发版本测试通过。
- **提交**：`e33652c`
- **遗留风险**：迁移观测不能证明迁移前 owner/status 的真实性；迁移前仍需独立核对归属与备份。

### DEV-BC-20260902-010：在验证前过滤 owner 会隐藏被篡改的待复核任务

- **阶段 / 状态**：D4a / 已关闭
- **发现方式**：主审 reviewer 队列读取顺序。
- **现象 / 根因 / 影响**：队列先排除空 owner 和本人任务，再验证完整性；将 owner 改成 NULL 或 reviewer 自身时，损坏任务可能静默消失。
- **解决办法**：先验证上下文，再按可信 owner/status 筛选；详情同样先验证归属上下文。
- **回归保护 / 验证**：`test_forged_owner_cannot_hide_a_task_from_the_review_queue` 两种篡改均抛完整性冲突；真实 PostgreSQL 图暂停后 reviewer 授权通过。
- **提交**：`e33652c`
- **遗留风险**：小规模开发队列逐条验证；本批不建设分页索引或后台队列系统。

### DEV-BC-20260902-011：持久化返回失败时图仍可能发送完成事件

- **阶段 / 状态**：D4a / 已关闭
- **发现方式**：主审图终局调用与注入保存/状态转换失败的回归测试。
- **现象 / 根因 / 影响**：调用方忽略 `_save_checkpoint`、`update_status` 的返回值；新增严格校验拒绝写入后，SSE 仍可能显示研究完成。
- **解决办法**：暂停和最终完成检查写入结果；失败进入 error，不发 `research_complete`。保存进度保留 paused，不静默重开待复核。
- **回归保护 / 验证**：`test_graph_does_not_emit_completion_when_persistence_rejects`、真实图＋PG 暂停/恢复/回读通过。替身的成功状态更新明确返回 True，与服务契约一致。
- **提交**：`e33652c`
- **遗留风险**：`human_review_completed` 的发送时点、终局同事务、竞争和断流恢复仍由 D4b 处理；本修复不代表唯一裁决完成。

### DEV-BC-20260902-012：唯一约束误落到无对应列的 ORM 表

- **阶段 / 状态**：D4a / 已关闭
- **发现方式**：共享目录集成测试 collection 报 `ConstraintColumnNotFoundError`。
- **现象 / 根因 / 影响**：子代理中间补丁把 token 唯一约束放进 ResearchCheckpoint，导致整个 models 包无法导入。
- **解决办法**：将约束移至 ResearchReviewClaim；实现完成后再执行模型导入、静态合约与实际 `alembic check`。
- **回归保护 / 验证**：静态 metadata/head 与真实 PG upgrade/check/downgrade/re-upgrade 通过。
- **提交**：`e33652c`
- **遗留风险**：子代理写入期间读取可看到中间状态；最终提交前必须集成复验。

### DEV-BC-20260902-013：迁移日志配置关闭已有应用安全日志器

- **阶段 / 状态**：D4a / 已关闭
- **发现方式**：真实 PG 图测试后紧接权限测试，组合结果 1 failed / 62 passed。
- **现象 / 根因 / 影响**：Alembic `fileConfig` 默认禁用已有日志器，使 reviewer 非法配置告警不再出现在 caplog；同进程调用也会失去该安全告警。
- **解决办法**：显式 `disable_existing_loggers=False`，不关闭调用方已初始化的日志器。
- **回归保护 / 验证**：`test_migration_keeps_existing_application_loggers_enabled` 与原告警脱敏测试通过。
- **提交**：`e33652c`
- **遗留风险**：不改变日志后端或部署配置，未建设日志平台。

### DEV-BC-20260902-014：JSON null 不等于领取状态要求的 SQL NULL

- **阶段 / 状态**：D4a / 已关闭
- **发现方式**：主审后补充 `decision_json=None` 的真实 PG 创建反例，先复现 CHECK 失败。
- **现象 / 根因 / 影响**：默认 JSONB 将 Python None 写成 JSON 字面量 null，不能满足未接受裁决时的 `decision_json IS NULL`；显式传空决定反而无法领取。
- **解决办法**：领取记录的 `decision_json` 使用 `JSONB(none_as_null=True)`；其他业务 JSON 格式不变。
- **回归保护**：`test_database_constraints_and_claim_skeleton` 显式传 None 并验证约束、禁止自审、外键、唯一目标和保留删除保护。
- **验证结果**：修复后纳入最终 252 项组合通过，无跳过。
- **提交**：`e33652c`
- **遗留风险**：领取/裁决服务尚未开放，后续 D4b 使用该模型，不另建平行状态。

### DEV-BC-20260902-015：旧迁移测试把阶段 revision 当成永远不变的 head

- **阶段 / 状态**：D4a / 已关闭（测试合约修正）
- **发现方式**：主审后续 head 对原迁移测试的影响，未将未经执行的失败记为测试结果。
- **现象 / 根因 / 影响**：D2b 成功测试停在冻结 0002 后直接用当前 ORM 做 `alembic check`；新增 0003 后已不再处于当前 head，不能作为无漂移证明。
- **解决办法**：保留 D2b 0002 目标及原失败回滚断言；成功路径继续升到当前 head，再检查 metadata 一致性。
- **回归保护 / 验证**：`test_company_profile_audit_migration_postgres.py` 6 项真实 PG 测试通过。
- **提交**：`e33652c`
- **遗留风险**：后续增加 migration 时仍需区分冻结历史 revision 与动态应用 head。

### DEV-BC-20260902-016：领取服务的首版授权顺序遗漏自审和目标信息边界

- **阶段 / 状态**：D4b1 / 已关闭
- **发现方式**：主代理审查子代理首版，随后补真实 PostgreSQL 用例。
- **现象 / 根因 / 影响**：首版先锁目标再查询 reviewer 权限，且没有显式比较 owner/reviewer；无权限调用可能根据 NotFound/锁冲突区分目标，自审只能依赖数据库 CHECK 阻止，错误语义也会变成 Unavailable。
- **解决办法**：在查询或锁定目标之前按实际数据库用户和冻结 policy 授权；验证完整性后显式禁止自审，不将客户端标志作为管理员权限依据。
- **回归保护 / 验证**：`test_role_and_self_review_checked_from_database_on_every_call`、`test_unauthorized_actor_cannot_probe_missing_or_busy_checkpoint`、数据库实际超级用户路径通过；纳入新增服务 54 项测试。
- **提交**：`fc707fb`
- **遗留风险**：新服务尚未连接 HTTP，不能将服务级通过视为接口接线验收。

### DEV-BC-20260902-017：过期领取仍绑定旧版本，导致无法重领有效的新材料

- **阶段 / 状态**：D4b1 / 已关闭
- **发现方式**：主代理审查分支顺序与独立 PostgreSQL 版本漂移用例。
- **现象 / 根因 / 影响**：首版在所有分支之前都要求 claim basis 与当前 checkpoint 完全相同；合法进度保存递增版本后，未接受且已过期的 claim 也永远无法重新领取。
- **解决办法**：live/accepted 继续要求 basis 精确匹配；expired/released 先核对旧 owner 与记录形状，再允许无旧 token 的新领取绑定当前已验证的版本，生成新 token。已接受的决定不进入接管分支。
- **回归保护 / 验证**：`test_changed_sealed_basis_blocks_live_claim_but_expiry_allows_new_basis`、`test_expired_claim_rotates_token_and_never_revives_old_work`、接受后不得换人用例均通过。
- **提交**：`fc707fb`
- **遗留风险**：接线时仍须阻止普通 checkpoint 写路径改动有效 claim 的基础材料；该保护随 D4b2 最终事务一起实现。

### DEV-BC-20260902-018：非 ASCII 摘要被误报为服务故障，重领还可能覆盖损坏记录

- **阶段 / 状态**：D4b1 / 已关闭
- **发现方式**：主代理与子代理定向复核，真实 PG 反例先出现 5 failed，修复后新增服务 54 项全部通过。
- **现象 / 根因 / 影响**：直接比较非 ASCII 的 basis_seal/decision_digest 会触发 TypeError，被上层误报 Unavailable；过期分支仅核对 owner/token 时，会把损坏的旧 basis 覆盖成新值。坏 token 则被归类为普通版本冲突。
- **解决办法**：持久化 token、正整数 basis version 和 ASCII 十六进制摘要先检查形状；损坏统一为 IntegrityError。只允许合法但过期的 basis 被重绑；非法 UTF-8 决定也作为输入错误拒绝。
- **回归保护 / 验证**：`test_malformed_persisted_claim_cannot_be_returned_or_auto_repaired` 四种路径和 `test_non_ascii_decision_digest_is_integrity_failure_not_database_outage` 从失败转为通过；损坏值保持原样，不自动修复。
- **提交**：`fc707fb`
- **遗留风险**：decision_digest 是内容摘要，不是 HMAC；不能凭该测试宣称抵御具有任意数据库改写权限的攻击者。数据库决定不可变保护与最终落盘由 D4b2 承接。

### DEV-BC-20260902-019：普通检查点写入可绕过已领取材料，终局状态缺少提交联动

- **阶段 / 状态**：D4b2a / 已关闭
- **发现方式**：主代理恢复旧保存/图收尾路径，子代理独立复核新增数据库触发器。
- **现象 / 根因 / 影响**：D4b1 尚未接线时，普通保存可在 live/accepted claim 期间改动 basis；旧图把保存终稿与 completed 分成两次提交。本轮触发器首版虽冻结决定，却仍允许单独 SQL 将 accepted 标成 finalized，留下 paused checkpoint 与终局 claim 不一致。
- **解决办法**：普通写入按 CP→integrity→claim 检查有效占有；新增 finalizer 同事务保存终稿/封签/状态/领取终局。数据库另用延迟至提交的约束触发器核对 completed 与 basis+1，不依赖 flush 顺序，不增加反向行锁；禁止删除和 TRUNCATE 已保留的决定。
- **回归保护 / 验证**：`test_normal_writes_cannot_bypass_effective_review_claim`、保存与首次领取竞争、`test_two_finalizers_only_commit_once_and_loser_can_retry`、提交异常回滚/恢复与 `test_raw_finalization_without_completed_checkpoint_is_rejected_at_commit` 均通过，纳入最终 253 项组合。
- **提交**：`42a00fe`
- **遗留风险**：HTTP/SSE 仍未接入新服务，由 D4b2b 承接；触发器不等同整个数据库防篡改，数据库所有者仍可禁用保护或伪造其他表。

### DEV-BC-20260902-020：终局重试漏掉自审语义与独立报告列一致性

- **阶段 / 状态**：D4b2a / 已关闭
- **发现方式**：主代理对 finalizer 首版进行代码复核，补 PostgreSQL 回归；未将审查发现误记为已执行失败。
- **现象 / 根因 / 影响**：首版先核 claim identity，所有者重试被当成 token 冲突；完成态只验 state_json，不核独立 final_report 列，后者被改后可能随幂等结果返回。finalized_at 顺序与批准改判后的等级关系也缺少显式核对。
- **解决办法**：先验检查点再显式禁止自审；完成态回读必须核对原决定、复核时间、完成时间、版本和报告列与封签报告的一致性，忽略新传入候选。
- **回归保护 / 验证**：`test_finalization_rechecks_reviewer_and_token_even_for_retry`、`test_finalized_retry_never_returns_tampered_report_column`、批准/拒绝/改判及完成态重试路径均通过，纳入最终 253 项组合。
- **提交**：`42a00fe`
- **遗留风险**：本轮只核新 finalizer 回执；最终界面与 SSE 还须使用回执中的已提交结果，不能继续发送未经落盘的候选报告。

### DEV-BC-20260902-021：新增保护使旧测试造数失效，JSON SQL 字面量又被识别为绑定参数

- **阶段 / 状态**：D4b2a / 已关闭（测试夹具）
- **发现方式**：第一轮真实 PostgreSQL 组合实际出现 `2 failed, 76 passed`。
- **现象 / 根因 / 影响**：旧 D4b1 测试借普通 update_status 构造 live claim 的非 pending 目标，新增 guard 正确拒绝使夹具先失败；新测试的 JSON 字面量 `:false` 被 SQLAlchemy text 解析为未提供的绑定参数，尚未进入数据库触发器。
- **解决办法**：非 pending 夹具先让未接受领取过期；合法但越界重签测试显式在测试事务中注入，损坏已接受摘要的测试仅在随机临时库内临时禁用具名触发器并同事务恢复。JSON 变异改用 `jsonb_build_object`，实际触发数据库约束。
- **回归保护 / 验证**：保留 D4b1 原拒绝断言，新增 guard/immutable 测试与原 context PG 测试一起通过最终 253 项组合；没有弱化生产保护以迁就旧夹具。
- **提交**：`42a00fe`
- **遗留风险**：特权破坏夹具只适用于已验证身份的随机临时库，不能复制到业务维护流程。

### DEV-BC-20260902-022：整份深拷贝把不应持久化的运行时锁也当成业务状态

- **阶段 / 状态**：D4b2a / 已关闭
- **发现方式**：主代理补不可复制锁用例，实际出现 `1 failed, 1 passed`；子代理独立复核也指出同一边界。
- **现象 / 根因 / 影响**：finalizer 先 deepcopy 整份 final_state，`_message_queue` 等私有运行时字段若含锁/Future，会在清理前抛 TypeError；原先 object() 测试可复制，未暴露问题，正常图输出可能被拒。
- **解决办法**：先对原始图状态验签，再剔除签名/存储契约之外的顶层私有字段并深拷贝业务字段；清理后再次验签，既不复制运行时资源，也不把损坏业务内容“洗成”有效内容。
- **回归保护 / 验证**：`test_runtime_fields_are_not_persisted_and_omitted_ui_preserves_previous_ui` 同时覆盖普通 object 和不可复制 Lock，修复后纳入 158 项初回归和 253 项最终关联组合通过；原无签名/错误材料/严格 JSON 类型反例继续保留。
- **提交**：`42a00fe`
- **遗留风险**：未声称真实活跃 LangGraph/SSE 恢复已验收，该接线仍在 D4b2b。

### DEV-BC-20260902-023：旧复核入口在最终事务提交前就发出完成事件

- **阶段 / 状态**：D4b2b / 已关闭
- **发现方式**：主代理与 Terra/high 对 HTTP、图节点和数据库服务逐层代码审计，补真实 PG 故障注入测试。
- **现象 / 根因 / 影响**：旧 POST 未使用已完成的 claim/finalizer；人审节点直接发送 human_review_completed，后续落盘失败仍可能让前端误判完成，重复请求也没有持久化的唯一决定。
- **解决办法**：流建立前在一个短事务中领取并接受决定；图恢复只读已接受身份/决定/时间，完成事件仅来自 finalizer 已提交回执；过滤节点提前发送的两类完成事件。失败/关闭流不得释放 accepted 或写 failed，异常消息不带决定和数据库明细。
- **回归保护 / 验证**：35 项真实 PG HTTP/图定向通过，包含提交前回滚、提交后响应丢失、双 HTTP reviewer、双编译图实例、两个事件断点与同决定重试；已纳入最终 343 passed 组合，无跳过。
- **提交**：`0805b4a`
- **遗留风险**：真实浏览器网络中断和独立后端进程由 3.5 承接；当前图竞争为同进程两实例，不是多进程验收。

### DEV-BC-20260902-024：双检查点的暂停基准与合法人审后状态不能混为一谈

- **阶段 / 状态**：D4b2b / 已关闭
- **发现方式**：主代理两轮代码复核确认，未将审查发现写成已发生的业务事故。
- **现象 / 根因 / 影响**：旧首次 interrupt 只改 paused，业务检查点可能仍是 writer 状态；新增恢复首版又把已执行完人审的图与复核前材料直接比较，会误拒合法的提交失败重试。反过来，单凭 next 为空直接恢复又会忽略损坏图。
- **解决办法**：首次暂停先保存完整已封签状态；待执行图严格匹配冻结基准，已执行图先验原签名，再严格匹配由冻结材料＋原决定＋固定时间推导的人审输出。只有真正缺失的图才直接做确定性的最后一步，不重新研究。
- **回归保护 / 验证**：标准/管理档案主流程、实际 before_commit 失败恢复、无图恢复和未完成/已完成图的坏签名/合法签名但内容漂移四个反例，均在 35 项定向与最终 343 项组合中通过。
- **提交**：`0805b4a`
- **遗留风险**：不会自动修复历史双库漂移；旧材料不一致明确拒绝，不能降低验签要求绕过。

### DEV-BC-20260902-025：普通 409 复核冲突被前端误报为完整性损坏

- **阶段 / 状态**：D4b2b / 已关闭
- **发现方式**：主代理接线后的前端错误分支代码审计。
- **现象 / 根因 / 影响**：工作台固定把全部 409 显示成完整性校验失败；fetch 的 stream 响应模式又把流建立前的 JSON 错误作为流返回，直接读 data.detail 取不到后端原因。正常占用或已接受其他决定会被误报为材料损坏。
- **解决办法**：仅对失败 HTTP 响应解析 JSON，成功 SSE 保持原流；区分冲突、无效决定、授权失败，使用服务端安全说明与中性兜底；断流明确允许原决定重试。
- **回归保护 / 验证**：后端错误状态/安全响应测试、前端 API 响应五项 Node 行为测试、变更文件 ESLint 与生产构建通过；浏览器交互验证未执行。
- **提交**：`0805b4a`
- **遗留风险**：3.5 补浏览器错误提示/断流矩阵；既有大包构建警告不在本阶段治理。

### DEV-BC-20260902-026：旧源码守卫截到了新原子分支的首个 yield

- **阶段 / 状态**：D4b2b / 已关闭（测试判据）
- **发现方式**：关联初回归实际出现 4 failed、133 passed，四项均为 test_review_persistence 的片段提取误判。
- **现象 / 根因 / 影响**：旧测试从完成 phase 的赋值截到第一个完成 yield；新增 claimed 分支提前返回后，选中的片段不再包含仍存在的 legacy/offline 普通保存与回读路径。
- **解决办法**：用 AST 选定 _drive 主 try 的直接完成块，不把嵌套 claimed 分支的 yield 当作该块终点；保留原保存顺序、phase 和回读断言。新原子路径由真实 PG 行为用例单独约束。
- **回归保护 / 验证**：修正后原 test_review_persistence 八项全部通过；最终关联组合 343 passed，无跳过。
- **提交**：`0805b4a`
- **遗留风险**：无已知遗留。

### DEV-BC-20260902-027：Vite 构建通过不代表全前端类型检查通过

- **阶段 / 状态**：D4b2b / 已缓解，旧模块类型债延期
- **发现方式**：补跑独立 TypeScript 编译检查；通过内存 compiler host 读取 `c266b98` 中本轮两份前端文件，保持依赖/配置一致做基线对照，不改工作树。
- **现象 / 根因 / 影响**：npm run build 成功，但独立类型检查有 25 项错误；Vite 构建不执行这一完整检查。复核页 critical_issues 的 nullable 字段与卡片输入类型不一致，另外 24 项来自原有图表、聊天等模块。
- **解决办法**：只规范化当前复核页的 nullable 字段；不为本阶段扩展全前端类型治理，不声称全量 TypeScript 通过。
- **回归保护 / 验证**：同依赖 `c266b98` 基线 25 项，当前 24 项，新增诊断 0；本轮复核页该错误消除。另有前端 API 响应五项 Node 行为测试通过，覆盖成功流保留、409/422 JSON 解码、代理非 JSON 与网络错误。
- **提交**：`0805b4a`
- **遗留风险**：其余 24 项旧类型诊断单列待办；3.5 已按用户确认改为无浏览器开发验收，当前对照新增诊断 0；浏览器交互另补，不自动扩展为全面重构。

### DEV-BC-20260902-028：档案资源状态被通用响应拦截器误判为接口失败

- **阶段 / 状态**：3.5 / 已关闭
- **发现方式**：主代理恢复前端请求链，Terra/high 补行为测试；修复前实际复现 active/archived 被转换为 ResponseError。
- **现象 / 根因 / 影响**：档案详情、创建、修改、归档返回资源本身，顶层 status 是 active/archived；通用 servicePlugin 却把它当作必须等于 success 的 API envelope，成功操作在前端变成失败。HTTP 错误也可能被替换而丢失原异常语义。
- **解决办法**：仅四个返回档案资源的 API 显式声明 responseStatusIsResourceState；保留其他接口的 envelope 行为与原 HTTP 403/409/422 拒绝。
- **回归保护**：新增 company-profile-response.test.mjs，复用真实请求模块及拦截器源码；与原 review-response.test.mjs 组合。
- **验证结果**：最终 8 项 Node、构建与定向 lint 通过；主审独立 tsc 曾发现本轮 unknown 缩窄新增 3 项诊断，修正后同依赖内存基线对照为 `f81cfef` 24 项、当前 24 项、新增 0，不以构建通过代替类型检查。
- **提交**：`f4c19c7`
- **遗留风险**：未执行浏览器表单/按钮/页面提示验证；旧类型债仍按 027 延期。

### DEV-BC-20260902-029：隔离测试入口静态编译通过，但模块与依赖替换不等于实际生效

- **阶段 / 状态**：3.5 / 已关闭（测试设施）
- **发现方式**：代码审计发现原 Depends 捕获问题；实际 Uvicorn 启动失败暴露路由模块导入问题；子代理只读复核发现父进程目标检查迟于建库。
- **现象 / 根因 / 影响**：只替换模块函数不能改变路由注册时已捕获的 Depends；from router import research_router 又取到 APIRouter，访问工厂函数直接 AttributeError。子入口限制随机本地库，但父 pytest 共享 fixture 会先按任意管理员 URL 建临时库，限制执行得太晚。
- **解决办法**：显式导入 router.research_router 模块，dependency_overrides 覆盖原 callable；v2 保留运行时工厂替换。父进程 autouse 守卫在建库前限制本地 PG 且拒绝 query 覆盖；子入口也独立检查环境、禁 dotenv/Python 非本地连接，并拒绝 MemorySaver 回退。日志还显示先构造真实 Scout 再替换会连接已有 Milvus；构造函数只建立连接，未读写集合，现改为构造前替换 Scout，避免依赖真实向量服务。
- **回归保护**：test_e2e_entry_contract.py 检查隔离环境拒绝、真实路由 Depends；实际进程测试断言 PG checkpoints 表有对应 thread_id。
- **验证结果**：最终入口合同 11 项、真实独立进程 4 项均通过，已含在关联 102 passed 中；最终进程日志确认使用 PostgresSaver，未再出现 Milvus 连接。
- **提交**：`f4c19c7`；修改前入口已保存为 `f81cfef`。
- **遗留风险**：入口仅属于 tests，不是生产安全策略；测试不调用真实模型或外部搜索。

### DEV-BC-20260902-030：新 HTTP 验收夹具在客户端、来源和故障注入 SQL 处失败

- **阶段 / 状态**：3.5 / 已关闭（测试夹具）
- **发现方式**：独立进程测试实际失败，先后为 Cannot open a client instance more than once、缺少受信来源的 422，以及 jsonb_set(json, ...) 类型错误；修正前最后一轮为 3 passed / 1 failed。
- **现象 / 根因 / 影响**：登录 helper 在已发送请求后把开启的 httpx.Client 再交给 with；合成档案漏填应收账款、经营状态和经营范围的字段级来源；篡改夹具又把档案 JSON 列当成 JSONB。失败发生在测试设施/故障注入阶段，不能归因于业务恢复失败。
- **解决办法**：登录 helper 统一用 contextmanager 管理 Client 生命周期；补全合成来源及覆盖字段；仅故障注入 SQL 显式将 JSON 转 JSONB，不改 schema、不放宽生产来源闸门。
- **回归保护**：同一独立进程套件继续走真实登录、创建、修改和尽调，保留稀疏档案可保存及材料不能变成结构化证据的断言。
- **验证结果**：修复后四项进程验收全部通过，已纳入关联 102 passed、无跳过；篡改 API 均返回 409，未弱化原断言。
- **提交**：`f4c19c7`
- **遗留风险**：合成来源仅供测试，不能代表真实企业事实已核实。

## 2026-09-03：学习手册样章检查点

- **范围**：用户确认先交付完整目录与一个详细样章，供本人读懂项目；没有提前扩写全部章节，没有修改业务代码。
- **产物**：`docs/project-handbook/index.html`；十二章目录、七步档案生命周期、五组数据结构、二十个词条、三十七处内嵌源码。模板、生成脚本、验证脚本与阅读说明一并保存。
- **代码基线**：`f5f8d17`；源码只从这一已提交版本的明确文件清单读取，不自动跟随 HEAD，不读取企业资料、环境文件或数据库。
- **分工**：主代理撰写、集成与复核；两个 Terra/high 子代理分别只读核对前端/API 与后端快照/恢复，随后审阅对应样章文字。
- **本次验证**：11 组文档检查通过，包括链接、显式标签闭合、SVG 数值、部分 CSS 维度约束、离线依赖、脚本语法、元素定位、搜索与版本示意逻辑、JSON 解析和源码生成一致性。未做浏览器视觉或真实 DOM 交互验收；没有重新执行历史的 102 项后端/8 项前端业务验收。
- **文档提交**：`bdae58c`；Windows 换行兼容维护：`e1f0708`。
- **下次入口**：先收集用户对样章深度、词条和源码跳转方式的反馈，再按目录逐章展开；不要把“目录已有”当作“全书已完成”。

### DEV-BC-20260903-031：文档引用定位不唯一，流程图含非法尺寸

- **阶段 / 状态**：学习手册样章 / 已关闭（文档生成与静态检查）
- **发现方式**：生成脚本实际失败，提示 SSE 服务构造锚点匹配 4 处；验证脚本实际拒绝 SVG `height=" sixty"`；只读复核另发现不存在的恢复函数名及冗余非法 CSS 维度。
- **现象 / 根因 / 影响**：凭通用语句定位会选错同文件内的调用；非法尺寸使流程框渲染不可靠。源码片段和图解可能产生“看似可信、实际指错”的学习依据。
- **解决办法**：采用唯一上下文锚点和真实恢复函数名；引用必须恰好匹配一处，否则停止生成；修正 SVG 数值，删除非法 CSS，保留固定提交、原行号和文件摘要。
- **回归保护 / 验证**：最终 37 处源码生成成功，文档 11 组检查通过；标签、链接、数值与生成一致性有断言。图形排版未通过浏览器实测，不能由静态检查替代。
- **提交**：`bdae58c`
- **遗留风险**：源码片段是局部摘录，未来修改基线必须重新核对文字与引用范围。

### DEV-BC-20260903-032：讲解概括过度，把前端约束和不同完整性机制混在一起

- **阶段 / 状态**：学习手册样章 / 已关闭（文档事实复核）
- **发现方式**：两个 Terra/high 子代理只读复核，主代理逐项对照实际实现。
- **现象 / 根因 / 影响**：初稿称服务端会校验信用代码格式；将审计/图封签合并讲解，遗漏完整审计快照摘要；“材料绑定”可能误导为恢复时会读取 materials 正文。这些是文档误述，不是本次发现并修复的业务事故。
- **解决办法**：明确服务端仅去空白/转大写，前端的格式规则不是 API 保证；区分 Pydantic 外层结构与领域字典校验；展开六项摘要/绑定/封签机制；补充归档终态、完整状态字段名、UI 实际搜索模式及历史测试依据范围。
- **回归保护 / 验证**：以固定基线代码二次核对，相关函数直接内嵌溯源；样章与术语、失败矩阵同步修正。没有为使文字成立而改动业务代码。
- **提交**：`bdae58c`
- **遗留风险**：当前服务端信用代码规范化不等于格式或校验位验证；如需增加该能力，应另定业务变更范围。

### DEV-BC-20260903-033：Windows 换行转换会使文档可复现性比较误报

- **阶段 / 状态**：学习手册样章 / 已关闭（生成器兼容性）
- **发现方式**：Git 提示检出时 LF 将转为 CRLF；内存模拟确认相同 HTML 换行转换后字节比较不同、规范化后相同。
- **现象 / 根因 / 影响**：生成器读取模板原始换行，并对成品原始字符串比较；Windows 新检出的文本可能因换行风格被误判过期。
- **解决办法**：模板读取与成品比较均只规范化 CRLF 为 LF；仍逐字比较全部其余内容，不放宽源码/正文一致性。
- **回归保护 / 验证**：内存 LF/CRLF 对照通过；修改后完整 11 组文档检查通过。未声称执行了新的 Windows Git 工作树检出测试。
- **提交**：`e1f0708`
- **遗留风险**：无已知遗留；视觉与真实 DOM 验证仍未执行。

## 新条目模板

```markdown
### DEV-BC-YYYYMMDD-NNN：一句话描述可观察问题

- **阶段 / 状态**：阶段号 / 已确认、处理中、已关闭、已缓解、延期或误判
- **发现方式**：测试、日志、代码审计、人工操作、子代理复核等
- **现象**：只写已观察到的行为和复现条件
- **影响**：用户、业务、数据、安全或开发流程影响
- **根因**：已验证的机制；未知时明确写“尚未确认”
- **解决办法**：代码、数据、流程或测试如何改变
- **回归保护**：测试名、断言、探针或人工验证步骤
- **验证结果**：实际执行结果；失败或未执行时不得写“已关闭”
- **提交**：commit hash；未提交时写“待提交”
- **遗留风险**：剩余边界、延期原因和承接阶段；没有则写“无已知遗留”
```
