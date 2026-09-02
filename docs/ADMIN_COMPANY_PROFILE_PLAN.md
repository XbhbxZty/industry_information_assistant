# 管理员企业档案专项计划

> **当前状态**：3.0～3.3、3.4A、3.4B、3.4C1、3.4C2a、3.4C2b 已完成；
> 3.4D1、3.4D2a1、3.4D2a2.1、3.4D2a2a、3.4D2a2b、3.4D2a3、3.4D2b、3.4D3、3.4D4a 已完成；
> 3.4D4b1 领取/接受决定、D4b2a 原子终稿与写入保护已完成；下一步为 D4b2b HTTP/SSE 接线。
> 当前按开发/演示口径交付，不代表已可直接上线。
> 工程 Bad Case 见
> [`DEVELOPMENT_TRACE.md`](DEVELOPMENT_TRACE.md)。

## 一、目标与边界

管理员企业档案是全局、结构化、可修订的尽调输入。它不属于个人知识库；只有带
`official / authorized / audited` 字段来源的结构化值可以进入 verified 证据链。
企业补充材料只供人工阅读与受控检索，不能直接参与风险评分。

“尽调必查”是后续流程必须给出核实状态的清单语义，不是创建档案时必须填写。
企业名称是唯一创建必填项，未录入字段必须保持信息缺口。

## 二、阶段状态

| 阶段 | 交付 | 状态 | 检查点 |
|---|---|---|---|
| 3.0 | 保存专项前尽调证据/RAG 基线 | 已完成 | `bcf951b` |
| 3.1 | 四个纯虚构保理案例与隔离 oracle | 已完成 | `a48e9ff` |
| 3.2 | 仿真案例接入结构化证据与离线 RAG | 已完成 | `6d0acbb` |
| 3.3 | 管理档案前后端、来源、尽调接入、版本冻结与事件引用 | 已完成 | `dfc0277`～`998b3ac` |
| 3.4A | 数据库探索器权限和敏感表边界 | 已完成 | `91df868` |
| 3.4B | 档案 API/RBAC/脱敏/并发契约 | 已完成 | `ec69349` |
| 3.4C1 | 档案审计结构连续性与失败关闭 | 已完成 | `62ee937` |
| 3.4C2a | 独立 reviewer 授权、禁止自审、身份分离 | 已完成 | `7618d28` |
| 3.4C2b | reviewer 队列与最小复核材料包 | 已完成 | `ae2f4ac` |
| 3.4D1 | 无数据库副作用的审计链密码协议与固定向量 | 已完成 | `8460581` |
| 3.4D2a1 | 统一数据库连接权威、Alembic 环境与空库 0001 | 已完成 | `735e36e` |
| 3.4D2a2.1 | 冻结旧 schema catalog manifest 与只读 preflight | 已完成 | `dc0f788` |
| 3.4D2a2a | 审批声明、受保护目标策略与纯验证协议 | 已完成 | `5245101` |
| 3.4D2a2b | 本地事务化 adoption、锁内二次指纹与基本并发 | 已完成（开发版） | `27ad6a0` |
| 3.4D2a3 | 移除运行时建表、切换 Docker/脚本并建立启动 guard | 已完成（开发版） | `27ad6a0` |
| 3.4D2b | 审计链持久化、旧历史锚定与实际读写失败关闭 | 已完成（开发版） | `5bb6704` |
| 3.4D3 | 审计与检查点密钥轮换、保留和退役保护 | 已完成（开发版） | `f40832a` |
| 3.4D4a | 检查点 owner/status/version 可信绑定与 claim 模型 | 已完成（开发版） | `e33652c` |
| 3.4D4b | reviewer 原子领取、幂等裁决与断流恢复 | D4b1/D4b2a 已完成；D4b2b 待接线 | `fc707fb`、`42a00fe` |
| 3.5 | PostgreSQL、独立进程和浏览器端到端封板 | 待开始 | — |

### 2026-09-02 起的执行口径（用户已确认）

优先真实实现和开发环境闭环；已经完成的协议/测试保留，不重新实现。剩余工作按五批交付：
D2a2b＋D2a3（已完成）→ D2b（已完成）→ D3 最小轮换（已完成）→ D4a（已完成）＋D4b → 3.5 集中联调。
不新增线上审批中心、密钥管理平台或通用旧库修复框架；部署加固不再阻塞后续业务和 RAG。
权限、个人知识库隔离、数据不丢失、证据来源和唯一最终决定仍是基本正确性要求。

## 三、3.4C2b 契约

### 3.4C2b.1 用户能力

- 登录、注册、OAuth2 token 与 `/auth/me` 返回服务端计算的 `can_human_review`。
- 超级管理员和启动时 allowlist 中的用户为 `true`；其余用户为 `false`。
- 前端旧会话缺少该字段时按 `false` 处理；前端能力只控制入口，后端继续最终授权。

### 3.4C2b.2 专用接口

- `GET /research/reviews`：只列出待复核、非本人所有的任务。
- `GET /research/reviews/{session_id}`：返回单个最小复核材料包。
- `POST /research/review/{session_id}`：沿用 3.4C2a 的服务端身份签名和恢复路径。
- reviewer 能力不得授予 `/research/checkpoints`、full、resume、cancel 或 delete 的跨用户权限。

### 3.4C2b.3 最小材料投影

允许返回：会话标识、企业名称、创建/更新时间、冻结的 `profile_ref`、报告草稿、
规则风险等级与授信建议、闸门、完整度、未核实/冲突项、关键问题、错误摘要和可公开的
证据摘要。

禁止返回：原始 `state_json`、`ui_state_json`、Agent 内部消息/提示词、任意知识库内容、
其他用户的普通检查点及不在上述 allowlist 中的新状态字段。投影必须显式构造，不能先
返回完整对象再依赖响应过滤。

### 3.4C2b.4 前端工作台

- 只有 `can_human_review` 用户显示“风控复核”入口。
- 工作台提供待办列表、刷新、详情、报告与风险依据、批准/不通过/改判表单。
- 发起人页面继续只显示等待状态和会话标识，不出现可提交表单。
- 提交成功后刷新待办；403/404/非 paused 和流内错误必须分别呈现。

### 3.4C2b.5 验收

1. 匿名、普通用户和自审者不能读取待办或详情。
2. reviewer 只能获得显式最小投影，不能借此访问完整检查点。
3. 队列排除本人任务、非 paused 任务、无 owner 历史任务和没有有效待复核状态的任务。
4. 单个详情在返回前验证检查点完整性；损坏状态失败关闭，不静默跳过。
5. 前端生产构建和变更文件 lint 通过。
6. 定向测试、相关回归、后端全量测试通过；实际 Bad Case 写入开发追踪台账。

并发 reviewer 的原子抢占与密码学审计决定属于 3.4D；3.4C2b 不得把当前非原子提交
错误表述为已经解决。

## 四、3.4D 密码学与原子裁决计划

### 4.1 为什么必须拆分

3.4C1 的 `content_sha256` 只能发现普通损坏，拥有数据库写权限者可以修改快照并重算全部
摘要；它不是密码学审计链。另一方面，仓库虽声明 Alembic 依赖，却没有正式 revision，
应用仍在导入时执行 `create_all()`，Docker 初始化 SQL 又维护一份独立业务 schema。
`create_all()` 不会为既有库补列、约束或回填，因此不能把新完整性字段直接加进 ORM 后
宣称迁移完成。

reviewer 提交也不是一次短事务：资格读取、LangGraph 恢复、SSE 输出和最终状态写入跨越
不同连接。数据库行锁不能持有整个流生命周期，必须另有 claim/lease/CAS 协议。因此
3.4D 按“先冻结协议，再建立迁移权威，再接入生产，最后解决并发工作流”的依赖顺序推进；
每个子阶段单独提交、验证和复核，可以独立回退。

### 4.2 威胁模型与能力边界

本阶段要防御：数据库字段被离线改写、审计记录被插入/重排/跨档案复制、在可信预期 head
仍存在时发生记录缺失、旧密钥轮换后历史无法验签、owner/status/version 被单独改写，以及
两个 reviewer 对同一 paused 任务产生双重决定。

HMAC 的信任根是应用持有且数据库攻击者拿不到的独立密钥。它不能证明迁移前的历史从未被
篡改，也不能抵御同时取得数据库写权限和签名密钥的攻击者；旧记录只能被表述为“在迁移
时刻经过结构校验并锚定”。外部时间戳、KMS 权限隔离、不可否认签名和透明日志不在 3.4D
最小闭环内，后续如有合规要求再单列专项。尤其要区分“链内删记录”和“整体回滚”：如果
攻击者同时把数据库中的尾部记录、当前档案和预期 head 回滚到同一个旧的合法状态，数据库
内 HMAC 无法证明较新版本曾经存在。D1 的调用方 head 与 D2 的 append-only 数据库约束用于
发现普通截断和越权写入；抵抗具备离线回滚能力的高权限攻击者仍需要数据库外单调锚点。

### 4.3 3.4D1：纯密码协议与固定向量

本阶段只新增不依赖 SQLAlchemy、数据库或 Web 路由的密码学原语及单元测试，不改变生产
读写行为，也不得宣称生产审计链已启用。

1. 固定 `admin-company-profile-audit-hmac/v1`、`hmac-sha256`、严格 canonical JSON、
   UTC 微秒时间、完整审计快照摘要和允许的动作集合。
2. 每条 MAC 必须覆盖 format、algorithm、key id、profile/audit id、revision、动作、actor、
   修改原因、时间、业务摘要、前后完整快照摘要、前序 MAC 和链起点。
3. 建立显式 key-id 到 key-material 的独立 keyring：active key 只负责签发，历史记录按自身
   key id 验证；不得回退 JWT 密钥，不得用 active key 猜测未知记录。
4. 新档案从 genesis 开链；旧档案定义独立 legacy anchor 契约，锚定整个旧序列摘要、迁移
   截止 revision、终态快照、迁移批次和时间。D1 只定义和验证该格式，不读取或回填数据库。
5. 链验证必须拒绝字段篡改、未知版本/算法/key、断链、与可信预期 head 不符的截断、插入、
   重排、跨档案复制、revision 跳跃、非法归档后续写入和错误 legacy anchor 连接。
6. 使用稳定固定向量证明 Unicode、null、键顺序和换 key 后仍可复验；所有 MAC 比较使用
   constant-time compare。v1 数字字节采用 Python 严格 JSON 序列化并由浮点固定向量锁定；
   若未来出现非 Python 签发/验签端，必须先升级到共同实现的 JCS 等新协议版本。
7. 密码信封验证与生产完整性验证不得混为一谈。完整观测入口必须同时接收实际 before、
   after、当前档案、严格领域重建器；legacy 链还必须接收实际旧序列和终态快照。只有该入口
   能作为 D2 持久化边界，低层 MAC/链接验证不能单独接线。

验收：纯单测覆盖上述正常与对抗矩阵；原有企业档案、检查点和 reviewer 定向回归不退化；
主代理逐字段复核 canonical payload 和失败关闭分支；实际 Bad Case 写入台账。

完成证据：纯协议没有导入 ORM、配置或环境变量；49 项 D1 测试、111 项相关定向回归与
后端全量 `1011 passed / 2 skipped` 通过；Terra High 首轮发现的两个 P1 经修复复验后关闭，
未发现新增 P0/P1。该结论只表示协议可供 D2 使用，生产档案仍运行 3.4C1 的结构连续性校验。

### 4.4 3.4D2a：迁移权威与旧库准入

恢复审计确认当前存在两种不等价历史：直接 `Base.metadata.create_all()` 产生 17 张 ORM 表；
Docker 初始化先产生 6 张应用表、7 张演示表、server defaults、TIMESTAMPTZ、`idx_*` 索引和
更新时间 trigger，应用启动后再由 `create_all()` 补齐其余 ORM 表。`IF NOT EXISTS` 会保留
先创建的旧定义，因此两者不能共享一个“看起来差不多”的 stamp。

此外，数据库 URL 也有三种解释：ORM 手拼 URL、数据库探索器优先读取 `DATABASE_URL`、
LangGraph 把 ORM URL 交给 psycopg3。迁移工具在统一连接解析之前上线，可能迁移与应用不同
的数据库。因此 D2a 再拆成以下三个小检查点。

#### 4.4.1 3.4D2a1：连接权威与空库基线

1. 建立唯一连接解析器：显式 `DATABASE_URL` 优先，否则安全组合 `POSTGRES_*`；用户名、
   密码必须 URL-escape，并分别输出明确的 SQLAlchemy psycopg2 URL 与 psycopg3 conninfo。
   ORM、Alembic、数据库探索器、LangGraph 和维护脚本必须复用它。
2. 建立 import-safe Alembic 环境，只导入 `models` 以注册同一个 `Base.metadata`，绝不导入
   `app_main`；启用 type/default 差异检查。
3. 手工评审的 `0001` 明确创建当前 17 张 ORM 表及全部 PostgreSQL UUID、JSON/JSONB、
   TEXT[]、FK、unique、check 和 index。目标沿用 ORM 的客户端 UUID/时间默认值，不把旧
   Docker 的 `uuid-ossp`、server defaults 或 trigger 偷渡进权威基线。
4. Docker-only 的 7 张餐饮/股票/法律/运输演示表不属于应用 ORM schema。既有数据不删除；
   新环境若仍需要它们，后续改为显式可选 seed，不再随 PostgreSQL volume 自动创建。
5. 在真实 PostgreSQL 空库执行 upgrade、downgrade、再次 upgrade 和 `alembic check`；另用
   metadata 差异测试保证新增模型没有遗漏 revision。D2a1 期间暂不删除任何 `create_all`，
   以免在旧库准入工具完成前切断回退路径。

完成证据：ORM、Text2SQL、LangGraph 与 Alembic 已复用同一启动连接权威；手工评审的
`20260831_0001` 与 17 表 `Base.metadata` 在随机临时 PostgreSQL 数据库执行 upgrade、
`alembic check`、downgrade、re-upgrade 全生命周期无漂移。连接和迁移定向 51 项通过，
后端确定性全量 `1033 passed / 3 skipped`，真实 PostgreSQL 用例显式执行 `1 passed`；
Terra High 复核无 P0，发现的空白 URL fallback、无端口兼容和请求期目标漂移 3 个 P1 均已关闭。
本检查点没有修改三处 `create_all()`、Docker 初始化 SQL 或既有数据库；旧库不得 stamp，继续由
3.4D2a2 处理。

#### 4.4.2 3.4D2a2：旧库指纹与 adoption

恢复审计和真实 PostgreSQL 重建确认必须再拆两个检查点。直接由 `app_main`、
`init_industry_data` 或 `seed_industry_data` 触发的 `create_all` 都会先执行
`models/__init__.py`，实际注册同一组 17 表；“seed 只产生三张行业表”经独立进程和临时库
验证为误判，不进入 legacy 支持矩阵。当前真实变体为：

- **base-full**：17 张应用表精确等于当前 `Base.metadata`/`0001`，无 server default，
  `DateTime` 为 `TIMESTAMP WITHOUT TIME ZONE`。这是唯一可能直接 adoption 的变体。
- **docker-hybrid**：Docker 先建 6 张带 UUID/时间 server default、TIMESTAMPTZ、旧索引和
  4 个更新 trigger 的应用表，再由 `create_all` 补足其余 11 张，同时保留 7 张演示表和
  `uuid-ossp`。它与 `0001` 语义不等价，只能识别并拒绝；未来若要保留必须另写兼容迁移，
  不能在 adoption 中静默 ALTER 或 stamp。

##### 4.4.2.1 3.4D2a2.1：冻结 manifest 与只读 preflight

1. 在隔离的真实 PostgreSQL 捕获并提交静态 catalog manifest，不得在运行时从未来 ORM 或
   Docker SQL 动态推导。至少冻结 base-full 与 docker-hybrid 两个已知 profile；profile
   明确标记 `adoptable` 或 `known_incompatible`。
2. catalog 格式覆盖数据库/服务器身份、public relation、按序列、类型/typmod/时区、nullable、
   default、identity/generated/collation、PK/FK/UQ/CHECK、索引定义与有效性、非内部 trigger、
   RLS/policy、rewrite rule、独立类型、owner/ACL/default ACL、extension、非 extension-owned
   routine 及显式依赖边。输出 canonical digest 和脱敏逐项 diff，不输出 URL/密码或 DDL 字面量。
3. preflight 在 `REPEATABLE READ READ ONLY` 事务中运行。空库分类为 `upgrade_required`；精确
   base-full 为 `exact_adoptable`；精确 docker-hybrid 为 `known_incompatible`；已有
   `alembic_version`、缺表、单字段漂移、未知对象或权限不足分别明确分类并失败关闭。
4. unmanaged 对象只能使用精确 manifest：Docker 7 张演示表和锁定 provider 版本的 LangGraph
   4 表/3 索引；禁止表名前缀放行，禁止跨 managed/unmanaged 的 FK、trigger 或 routine 依赖。
   `uuid-ossp` 可单独报告，但只要 managed 列仍引用其 server default 或 managed trigger 仍存在，
   就属于不兼容 drift。
5. 本检查点绝不创建/修改 `alembic_version`，不执行 stamp、ALTER、DROP 或业务数据读取；只在
   结构精确命中后读取 `alembic_version` / LangGraph provider 的版本元数据行。真实 PostgreSQL
   覆盖 base-full、Docker 两变体、空库、已版本化、单字段漂移、未知表、RLS/rule/type/ACL、
   跨边界依赖与限权角色。

完成证据：六个 PostgreSQL 15 冻结 manifest 均以稳定 ID、显式 classification、仓库硬编码
SHA-256 和对象声明自校验锁定；preflight 在 `REPEATABLE READ READ ONLY` 中运行，CLI 无
stamp/DDL 参数。最终纯测与真实 PostgreSQL 定向矩阵合计 `34 passed`，阶段中后端全量基线
`1050 passed / 15 skipped`；Terra High 最终复核无 P0/P1。代码检查点为 `dc0f788`，本阶段
没有进入目标二次绑定、锁、审批或事务化 stamp；这些仍只属于 3.4D2a2.2。

##### 4.4.2.2 3.4D2a2.2：受控事务化 adoption

为缩小可回退粒度，本阶段拆为两个连续检查点：

- **3.4D2a2a** 只冻结 control-plane 契约：不可变 operator attestation、独立的受保护
  target policy、固定确认短语、服务端时钟有效期、备份/维护窗口外部引用和稳定错误码。该检查点
  不导入 Alembic command，不连接数据库，也没有 stamp/DDL 能力。
- **3.4D2a2b** 才实现 caller-owned 短事务、固定 advisory lock、表锁、锁内二次指纹、
  Alembic stamp、postverify、回滚/commit outcome unknown 和真实 PostgreSQL 并发测试。

`AdoptionApproval` 只是操作者声明，不能被描述成工具已经验证备份可恢复或外部写者全部停止。
目标信任根必须来自独立的部署配置/运维控制面；approval 只能引用 policy ID 及其 canonical
content SHA-256，不能在同一请求中自填目标身份并让工具宣称目标已经可信。

1. 受保护 target policy 独立保存 expected database/server identity；操作者声明旧 profile、
   policy ID/content digest、preflight digest、备份引用、维护窗口引用、确认时间与固定确认短语。
   工具只能校验和记录声明，不能伪称自动证明备份可恢复或所有外部写者已停止。
2. adoption 使用一个短 PostgreSQL 事务：取得项目固定的 transaction advisory lock，对现有
   managed 表加 DDL 冲突锁，在同一连接重新读取 catalog，并逐项匹配目标身份、profile 与已
   审批 digest；任何变化立即回滚。
3. 只有 exact base-full 才能用 caller-owned connection 在同一事务 stamp `20260831_0001`；
   随后验证 `alembic_version` 精确一行且等于 head 后提交。禁止 `stamp --purge`，失败只回滚，
   不运行补偿 DROP/downgrade。
4. 已在 head 的库幂等返回 `already_managed`；落后/未知/多 revision、锁竞争、目标替换、审批
   过期和 commit 结果未知均拒绝盲重试。commit 结果未知时只能重新做只读 preflight。
5. 真实 PostgreSQL 覆盖预检后漂移、错误目标库、两个 adoption 竞争、stamp 前后异常回滚、
   幂等和临时库清理身份保护。当前 D2a3 前仍有不使用共同锁的 `create_all`，因此技术锁不能
   取代真实排他维护窗口。

D2a2a 完成证据：严格 JSON 拒绝重复键、未知/缺失字段与非标准值；approval 同时绑定稳定
base-full profile、policy ID/content SHA-256 和 preflight SHA-256；服务器与数据库身份使用
冻结格式生成 canonical digest；服务端 UTC 时钟执行 15 分钟 TTL 与精确到期边界；统一纯入口
同时校验审批、目标和新鲜 `exact_adoptable` 报告。backup/window 始终标记为 operator
attestation。定向契约/预检组合 `38 passed`，后端全量 `1071 passed / 18 skipped`；代码检查点
为 `5245101`。

D2a2b 开发版仅提供本地终端命令，不提供 HTTP 入口：主机配置访问权限和数据库维护凭据构成
执行边界，policy 从 `LEGACY_ADOPTION_POLICY_FILE` 加载；不新增签名审批服务或持久审批台账。
同一 writer connection 在锁内取得 PostgreSQL 时间、目标身份与二次 preflight，再调用统一
纯验证入口。JSON 结果记录 approval ID、operator reference 和执行状态；幂等/结果未知按数据库
现状处理。固定短语和合法 JSON 本身不构成认证；backup/window 仍只是操作者声明。

#### 4.4.3 3.4D2a3：切换唯一 schema 权威

1. 最后移除 `app_main.py`、`init_industry_data.py`、`seed_industry_data.py` 三个 `create_all`；
   seed/init 只允许在数据库已处于 Alembic head 后运行。
2. 应用启动只做只读 head guard，生产/本地启动命令先显式 `alembic upgrade head`；不在
   FastAPI import 或普通请求中自动迁移。
3. Docker 不再挂载旧业务 DDL；演示 DDL/DML 移到不自动执行的显式 seed 路径。已有 volume
   绝不自动删除或重建，preflight 拒绝时由操作者选择备份迁移或新建数据库。
4. 更新快速开始、Dockerfile/脚本和运维恢复说明，并验证未迁移、落后 revision、迁移失败、
   正常 head 四种启动行为。

D2a 总验收：新装和受支持旧库走同一 head；ORM、Alembic 与 Docker 不再三处争夺 schema
权威；任何异常都不删除既有数据、不盲 stamp，也不留下半迁移状态。

第一批完成证据（`27ad6a0`）：本地 `inspect/apply`、锁内同连接 stamp/回滚/结果未知、动态
head guard、显式迁移后启动、两种行业 seed 和独立七表演示 seed 已接通。版本表固定在 public，
Alembic autogenerate 精确排除版本表与已知 unmanaged 表。相关测试汇总 `165 passed`，包含真实
PostgreSQL 新库、旧库、漂移、竞争、回滚、重复执行与 seed 后 `alembic check`；PowerShell 语法、
compose 配置和编译检查通过。未重跑后端全量，未构建/部署 Docker 镜像，未执行浏览器全流程。
测试只创建并清理随机临时库，已有业务库和运行中的容器未变更。

### 4.5 3.4D2b：生产审计链接线与历史锚定

1. migration 新增完整性版本、算法、key id、前序 MAC、当前 MAC、前后完整快照摘要，及
   每个 legacy profile 唯一的迁移锚点；模型字段只在 revision 可执行后同步。
2. 回填先在事务内复验旧结构连续性，再生成 legacy anchor；损坏历史隔离并阻断升级，
   绝不自动替其签名。新建档案直接生成 genesis。
3. 写路径在一个事务中执行“领域重建与完整观测验旧链、更新当前行、追加审计、签名、
   完整观测复验”；密钥不可用、key 未知、快照/MAC/前序/anchor 不符全部回滚。不得只调用
   D1 的低层密码信封验证器。
4. 研究快照、管理员详情/历史、修改和归档统一走密码学验证。完整性冲突返回 409，签名
   服务或配置不可用返回 503，且不返回受损档案正文。
5. PostgreSQL 追加只读/不可变约束或 trigger，应用权限不得更新或删除既有审计行；运维
   修复必须经过显式、留痕的离线流程。

验收：证明“重算普通 SHA”仍不能伪造；新链、锚定旧链、事务回滚、接口 409/503 和真实
PostgreSQL migration 全部通过。此时才能宣称生产档案审计链启用。

#### 2026-09-02 轻量第二批交付与恢复点

代码检查点 `5bb6704`，开始前检查点 `1a17253`。复用 D1 协议，不改变其签名字节与固定向量。
新增 migration `20260902_0002`、独立 keyring 配置、旧历史锚点及 PostgreSQL 防改写触发器。
新增/修改/归档在同一事务签名并复验；详情、列表、历史、材料检索和研究输入统一验链。
业务 `content_sha256` 与 `profile_ref` 协议保持不变；不进入个人知识库，不修改前端功能。

组合验证 `191 passed`（无跳过），覆盖档案 CRUD、HTTP 权限与 409/503、普通 SHA 重算篡改、
最终验签失败回滚、新链和旧链接续、真实 PostgreSQL upgrade/check/downgrade/re-upgrade、
legacy adoption/preflight、启动 guard、演示 seed 和数据库探索器隔离。之后将旧历史回滚夹具
固定为“先成功 flush 第一个锚点，再遇到损坏档案”，6 项 PostgreSQL 测试复跑通过。
`git diff --check` 通过。未跑全后端、浏览器或镜像构建；这三者不冒充已经验收。

本批只迁移随机临时数据库，测试后确认全部清理。已有业务库、用户 `.env`、运行中的容器
均未修改，因此不能声称已有业务数据已启用签名链。使用时先配置
`COMPANY_PROFILE_AUDIT_KEYS_JSON` / `COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID`，再按
[`backend/migrations/README.md`](../backend/migrations/README.md) 在线迁移；缺密钥返回 503，
损坏历史阻断升级，不自动补签。Git 回退不等于数据库回退；降级会移除签名元数据，真实库
应从审阅过的备份恢复，不能把重新锚定当作恢复原签名。

**D2b 当时的接续任务（现已完成，见 D3 交付）**：沿用已实现的审计 keyring，先做 key 引用统计/退役预检与最小
操作说明，再把研究检查点的“仅当前 secret”改成可保留历史 key 的验证路径。
不重做 D1/D2b，不建设 KMS/密钥平台；D4 原子 reviewer 及 3.5 集中联调仍未完成。

### 4.6 3.4D3：密钥轮换与退役

1. 先部署包含旧/新验证 key 的 keyring，再切换 active key；新记录使用新 key，前序 MAC
   可以跨 key 边界连接，历史记录不重签、不覆写。
2. 轮换前统计每个 key id 的历史引用；仍被保留审计或检查点引用的 key 不得删除。未知、
   缺失或被错误退役的 key 必须失败关闭。
3. 将现有检查点“只认当前 secret”的机制升级为显式历史 keyring；为 v1 记录定义兼容或
   锚定迁移，不借轮换伪造历史认证能力。
4. 记录 active key 切换、配置预检和恢复手册；日志只记录 key id，绝不记录 key material。

验收：旧 key→新 key 的连续链、只留新 key 的失败、未知 key、回滚 active key 和退役预检
均有自动化测试。

#### 2026-09-02 轻量第三批交付与恢复点

代码检查点 `f40832a`，开始前检查点 `535ec6e`。继续使用一个 Terra/high 实现子代理；
主代理完成引用统计/CLI、真实 PostgreSQL 与 LangGraph 集成、复核和收尾。

- 检查点新增显式 keyring，v1 图/业务封签保留原派生指纹与字节，按记录 key 验证历史，
  新写入采用 active；正常切换/回滚 active 不重签或覆盖历史。
- 原 v2 档案快照绑定没有 key ID，因此新增 v3；显式配置发 v3，旧 v2 只认明确 legacy key。
  未启用新配置时保持旧兼容路径；半配置或非法配置不得静默回退 JWT。
- `check_key_rotation.py` 在 PostgreSQL 只读、可重复读事务内统计审计、anchor、业务、图与
  内层快照引用，并实际复验签名。active、仍有引用、未知/错误 key 或损坏记录阻断退役。
  非空 LangGraph 历史阻断检查点 key 退役，不解码或清除历史来放行；不自动改配置/删除 key。
- 操作与回退说明见 [`KEY_ROTATION.md`](KEY_ROTATION.md)。无需新 migration，head 仍为
  `20260902_0002`。已有业务库、用户 `.env` 和运行中服务均未修改。

组合验证 `212 passed`（无跳过）：包括 7 项真实 PostgreSQL、档案跨 key 连续链与 active
回滚、旧检查点读取不改签名/业务版本、外层新 key 而内层 v2 仍引用旧 key、未知 key、CLI
脱敏/只读与退役阻断、reviewer/access 回归和真实 LangGraph MemorySaver 暂停跨 key 恢复。
最后补强非 ASCII 快照 MAC 的错误边界，相关 15 项定向复跑通过。`py_compile` 与
`git diff --check` 通过；随机临时数据库确认已清理。
未跑全后端、独立进程部署或浏览器验收，不把 MemorySaver 测试等同部署完成。

**D3 当时的接续任务（已由 D4a 完成）**：先检查 checkpoint session 唯一性、owner/status/business version 的
可信绑定和最小 claim/decision 模型，再接 D4b 短事务领取、幂等裁决及断流恢复。
保持可保存的小检查点，不重做 D1/D2/D3，也不扩大为上线审批/密钥管理平台。

### 4.7 3.4D4a：检查点可信绑定与 claim 模型

1. 迁移前审计重复 `session_id`，修复后建立唯一约束；status 设为非空受限枚举，并引入
   单调 business version。
2. 完整性上下文绑定 checkpoint id、session id、owner id、paused/status、business version
   和 business seal，owner 或状态不能再脱离封签被单独改写。
3. 新建以 checkpoint 为唯一外键的 review claim/decision 记录，至少保存 owner、reviewer、
   opaque token、basis version/seal、状态、lease、decision digest 和 accepted/finalized 时间。
4. claim 状态与研究 workflow status 分离；LangGraph checkpoint 只负责恢复，不作为谁有权
   作出最终裁决的权威源。

验收：重复 session、缺失完整性、owner/status/version 篡改、非法状态转换和 FK/唯一约束
在真实 PostgreSQL 中失败关闭。

#### 2026-09-02 D4a 交付与恢复点

代码检查点 `e33652c`，开始前为 `e5c2a1f`（工作区干净）。一个 Terra/high 子代理负责模型、
迁移及定向复核；主代理负责上下文协议、服务接线、真实图/数据库集成与收尾。

- 新增独立 context 封签域，不改 D3 的 v1 图/业务签名字节。绑定 checkpoint/session/owner/
  status/business revision/business MAC；复用原 business_revision，不再维护一份平行版本。
- `save_checkpoint`、状态转换先锁行并验旧值；进度保存保留 paused，终态不可重新改写；
  状态变化也递增版本、重签。读取缺失配对或上下文一律拒绝，不再走 unsigned legacy 分支。
- reviewer 在按 owner/status 过滤前验签，避免篡改成空 owner/本人任务后静默隐藏；图的暂停
  和最终保存失败不再发送成功完成事件。最终裁决原子性仍未完成，不能扩大这个结论。
- migration `20260902_0003`：session 唯一、status 非空检查、UUID 配对外键、版本单调触发器，
  及 checkpoint 唯一的领取/裁决记录表。旧数据先核查并验原封签，只新增 `migration_observed_v1`
  观测，不改旧 MAC/版本；损坏、重复、缺失完整性或历史 key 均使整次事务回滚。
- 领取模型已保存 owner/reviewer/token/basis/lease/decision/timestamps，并与工作流状态分开。
  **尚未接入领取接口和原子 finalization**；没有声称双 reviewer 或断流恢复已完成。

验证：主定向组合 `252 passed`、补充原档案审计迁移 `6 passed`，均无跳过；包括真实 PostgreSQL
升级/check/降级/重升、旧记录回填与失败回滚、并发保存版本不丢失、claim FK/唯一/状态形状、
真实 LangGraph MemorySaver＋PostgreSQL 暂停/复核恢复、轮换预检、权限和探索器隔离。
`py_compile` / `git diff --check` 通过。Bad Case `20260902-009`～`015` 已记入追踪台账。
未跑全后端、浏览器或独立后端进程；不把内存图测试当作部署验收。

仅创建/清理随机临时数据库，未改已有业务库、用户 `.env` 或运行中容器。实际使用前须停写、
备份并独立核对旧 checkpoint owner/status，再按迁移说明升级。新 head 为 `20260902_0003`。
Git 回退不等于数据库回退，降级会丢失 context/claim 元数据；真实数据回退应恢复匹配备份。

**D4a 当时的接续任务（短事务部分已由 D4b1 完成）**：先用现有模型完成短事务领取/接受与
真实竞争测试，再接最终事务、幂等重试及 SSE 断流恢复；不重做 D4a 或扩展管理平台。

### 4.8 3.4D4b：原子 reviewer 裁决与断流恢复

1. 领取使用短 PostgreSQL 事务锁定 checkpoint、integrity 和 claim，验证 sealed pending、
   paused、禁止自审及 basis version/seal 后写入 claim 并立即提交；不能跨 SSE 持锁。
2. 指定 session 的提交冲突使用 NOWAIT/条件更新返回 409；SKIP LOCKED 只留给未来“领取
   下一条”队列。claim lease 负责进程重启和长流期间的占有语义。
3. 最终写入另起短事务，用 token、reviewer、owner、未过期 lease、basis version/seal 做
   CAS；同一事务保存封签后的最终状态、completed 状态和不可变 decision，并 finalize claim。
4. 决定接受前断流可释放或等待 lease；接受后断流不得被第二 reviewer 接管，同一 reviewer
   依靠 idempotency key/token 重试。过期旧 token 的图输出必须在 finalization 被拒绝。
5. 只有 finalization 成功才发送 `human_review_completed`/`research_complete`；客户端断流不
   自动等同业务失败。

验收：两个真实独立数据库会话并发领取恰好一个成功；lease 接管、旧 token 拒绝、重复
POST 幂等、SSE 各断点取消、双 LangGraph 实例恢复和唯一最终决定均通过。

#### D4b 的小检查点拆分

D4b1 只实现数据库领取/接受服务，复用 0003 的模型，不新增 schema、前端或外部接口：

- 每次操作独立短事务，固定 checkpoint → integrity → claim 的 NOWAIT 锁顺序。
  用数据库锁后时钟判断 lease；验证当前数据库 reviewer 权限、禁止自审、封签的 paused/pending。
- 新领取生成服务端随机 token，绑定 owner、reviewer、business version/seal。未接受时可释放，
  或在过期后用新 token 重新领取；旧 token 不能复活旧工作。
- `accepted` 表示决定已收到，不代表批准或完成。接受时保存服务端身份、规范化决定、摘要和
  数据库时间；同人同 token 同决定重试不改写。接受后不可换 reviewer/释放，可由原人续租恢复。
- 决定校验继续调用原 `apply_human_review`，不另写评分或人工改判规则。普通摘要检查只证明
  内容一致，不等同于新的密码学签名；权限与后续持久化不可变保护仍是必要边界。
- 测试直接调用真实 PostgreSQL 服务；不把它称为 HTTP 幂等、SSE 断流恢复或唯一最终决定验收。

D4b2 再接现有提交入口与图：领取/接受前置，长流期间不持有事务；所有普通 checkpoint 写入
须遵守有效 claim 的占有语义，最终用 token/身份/lease/basis CAS 在一个事务内保存完整终稿、
completed 与 finalized 决定。接受后的原始决定不可覆写。只在最终事务成功后发送完成事件。
同时实现 HTTP 重试、已接受但未完成时的恢复，以及双图实例/断流测试。D4b1 期间现有 POST
仍走原流程，不能混合启用手工 claim 与旧写路径；只有 D4b2 接线完成后才开放新服务给界面。

#### 2026-09-02 D4b1 交付与恢复点

代码检查点 `fc707fb`，开始前为 `5042453`（工作区干净）。一个 Terra/high 子代理实现独立
服务并参与定向复核；主代理负责 PostgreSQL 测试、集成审查、修复与保存检查点。

- `ReviewClaimService` 已实现 claim/accept/release/renew；每次独立事务，返回脱离 ORM 的回执。
  两个 reviewer 竞争同一目标只有一个能领取；三个目标表的锁竞争均快速返回冲突。
- 接受前支持释放、过期后新 token 重领；接受后固定 reviewer 与原决定。同一决定重试不改
  写接受时间或内容，数据库提交成功但响应失败时也可重试确认，不把收到决定当成工作流完成。
- 复用实际数据库用户、现有 reviewer policy 和 `apply_human_review`。未授权用户在目标查询
  前拒绝；禁止自审。合法新版本允许在过期后重新领取，损坏的旧领取记录则明确拒绝而非覆盖。
- 本轮未修改 schema、checkpoint 通用写路径、图、HTTP 或前端。Alembic head 仍为
  `20260902_0003`；新服务尚未启用到实际复核入口。

验证：最终关联组合 **`163 passed`，无跳过**，涵盖领取服务、检查点上下文、复核工作台、
研究访问、人工复核和数据库探索器权限。其中新增服务集为 `54 passed`（43 项真实 PostgreSQL
测试、11 项纯测试；已包含在 163 项中），包括真实双会话竞争、锁冲突、lease/token、合法
版本漂移、损坏记录、提交前回滚与提交后响应异常重试。测试有 825 条 warnings，未在本轮
扩展为全库告警治理。`py_compile` / `git diff --check` 通过，随机临时测试数据库已确认清理。
未运行全后端、浏览器、HTTP 并发或独立进程部署验收；未改已有业务库、用户 `.env` 或容器。
Bad Case `DEV-BC-20260902-016`～`018` 已修复、验证并记录。

**D4b1 当时的接续任务**：以本检查点为基线，先接有效 claim 的普通写入保护与唯一最终事务，
再接现有 POST/图恢复和 SSE 完成事件。不重做已验证的 D4b1；全链路接线前，不对旧流程手工
调用新领取服务。为维持小检查点，本轮拆成 D4b2a 持久化闭环、D4b2b 接口与流恢复。

#### D4b2a 实现边界

- 复用 `ReviewClaimService.finalize(session_id, reviewer_id, token, final_state, ui_state=...)`。
  最终候选图状态必须验签，除了 phase/risk_assessment/final_report 外，业务投影须与已领取的
  材料严格一致；不能换证据、字段或任务。风险结果调用原 `apply_human_review`，复核时间固定
  使用 `accepted_at.isoformat()`，报告调用原 `_canonicalize_risk_block` 与 `render_markdown`。
- 同一事务保存完整状态、UI、报告、completed、递增一次的 business/context 封签与 finalized。
  同人同 token 完成态重试忽略新候选内容，只回读已验证的原结果；过期但尚未完成时须先续租。
- 普通 `save_checkpoint`/`update_status`/`delete_checkpoint` 遵守占有语义；live claim 禁写，
  accepted/finalized 不因租约过期解除保护；未接受的过期或已释放领取允许正常材料更新。
- 迁移 `20260902_0004` 只添加触发器，冻结已接受决定及终局记录，阻止删除/TRUNCATE；
  accepted→finalized 的提交时检查要求对应 checkpoint 与 revision 同步完成。旧迁移不改。
- 未改 HTTP/SSE/图执行逻辑；现有界面仍走原复核路径。新服务还不能与旧路径混用，不能把
  两个数据库会话的 finalizer 竞争测试说成双 LangGraph 实例或浏览器恢复验收。

#### 2026-09-02 D4b2a 交付与恢复点

代码检查点 `42a00fe`，开始前为 `6357295`（工作区干净）。一个 Terra/high 子代理实现
finalizer 并复核集成；主代理负责普通写入保护、迁移、真实 PostgreSQL 测试、代码审查与修复。
已保存的 D4b1 领取/接受逻辑没有重新实现；旧迁移不改，新增 head 为 `20260902_0004`。

最终关联组合 **`253 passed`，无跳过**（2022 条 warnings，未扩展为告警治理），其中新增
55 项真实 PG 测试：finalizer 36 项、普通写入/不可变保护 19 项。组合同时覆盖 D4b1、D4a、
原检查点签名持久化、迁移契约、空库升级/check/降级/重升、复核工作台、人工复核和访问隔离。
批准/拒绝/改判、两种签名模式、运行时 Lock、旧材料/旧 token、两独立数据库会话竞争、
提交前回滚/提交后响应失败重试、直接 SQL 非法终局提交失败均已通过。`py_compile` 与
`git diff --check` 通过；子代理最终只读复核未见这两项收尾修复引入明显回归。

随机临时测试数据库已确认清理；未修改现有业务库、用户 `.env` 或运行中容器。实际启动本版
前仍需停写、确认目标与备份，再显式升级到 0004；本轮不代替用户执行真实库迁移。
降回 0003 保留决定数据但移除新保护，必须匹配代码检查点；Git 回退不会自动回退数据库。
Bad Case `DEV-BC-20260902-019`～`022` 已修复、验证并记录。未跑全后端、独立进程或浏览器，
并发测试使用两个数据库会话，不是双 LangGraph 实例验收。

**D4b2b 接续要求**：在现有 POST 的流建立之前领取/接受决定；图恢复使用已保存的决定和
accepted_at，不重新接收 reviewer/时间。处理中避免普通检查点写入，终局调用 finalize，
完成事件只采用它返回的已提交结果；断流不得把 accepted 清成 failed 或转交其他 reviewer。
覆盖重复 HTTP 请求、已接受未完成恢复、各 SSE 断点、旧图输出和双图竞争后再标记 D4b 完成。

### 4.9 3.4D 开发与提交纪律

- 顺序固定为 D1 → D2a → D2b → D3 → D4a → D4b；后序发现若推翻前序协议，先回到最近
  检查点修订计划，不在未记录的情况下兼容错误格式。
- 每批遵循：短状态检查 → 可运行能力实现 → 相关测试 → 主代理复核 → Bad Case 台账与提交。
  不再每个小改动单独提交计划或重复全量回归；全量测试留给集中联调或确有必要的公共改动。
- 默认一个 Terra High 实现子代理，主代理负责集成和复核；只有独立任务才增加角色。真实迁移、
  事务和并发仍用 PostgreSQL 验证；推测性部署风险进入待办，不自动扩展当前阶段。
- 尚未接入持久化、迁移、真实 PostgreSQL 或浏览器的能力必须明确标注，不以 mock/SQLite
  测试替代生产结论。

## 五、后续阶段摘要

### 3.5

使用真实 PostgreSQL、后端独立进程和浏览器覆盖管理员建档、普通用户发起、版本冻结、
独立复核、改判留痕、归档、篡改拒绝、权限矩阵与个人知识库隔离。只有这一阶段通过，
管理员企业档案专项才可按开发/演示口径标记完成；生产部署另行验收。

## 六、RAG 后续方向

开发闭环完成后直接建立组织级企业材料库，不等待全部生产加固：材料按 `company_profile_id + material_revision`
隔离，向量索引是派生缓存；RAG 只产候选证据，继续经过主体、原文、字段、日期和来源
闸门。候选若要提升为结构化字段，必须由管理员确认并产生新的档案 revision，不能由
模型直接改写系统记录。
