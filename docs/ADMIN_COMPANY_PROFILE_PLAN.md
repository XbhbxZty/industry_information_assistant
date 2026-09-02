# 管理员企业档案专项计划

> **当前状态**：3.0～3.3、3.4A、3.4B、3.4C1、3.4C2a、3.4C2b 已完成；
> 3.4D1、3.4D2a1、3.4D2a2.1、3.4D2a2a 已完成；下一可开发单元为 3.4D2a2b。
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
| 3.4D2a2b | 锁内二次指纹、事务化 adoption 与并发封板 | 待开始 | — |
| 3.4D2a3 | 移除运行时建表、切换 Docker/脚本并建立启动 guard | 待开始 | — |
| 3.4D2b | 审计链持久化、旧历史锚定与生产失败关闭 | 待开始 | — |
| 3.4D3 | 审计与检查点密钥轮换、保留和退役保护 | 待开始 | — |
| 3.4D4a | 检查点 owner/status/version 可信绑定与 claim 模型 | 待开始 | — |
| 3.4D4b | reviewer 原子领取、幂等裁决与断流恢复 | 待开始 | — |
| 3.5 | PostgreSQL、独立进程和浏览器端到端封板 | 待开始 | — |

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

D2a2b 的执行入口不得接收客户端提供的 target policy 或 preflight report；必须从受保护部署
配置加载 policy，由已认证的 maintenance principal 发起，在锁内同一 writer connection 取得
PostgreSQL 时间、目标身份与二次 preflight，再调用统一纯验证入口。approval ID 必须进入可审计、
可判断重放/结果未知的执行结果；固定短语和合法 JSON 本身都不构成授权。

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

### 4.9 3.4D 开发与提交纪律

- 顺序固定为 D1 → D2a → D2b → D3 → D4a → D4b；后序发现若推翻前序协议，先回到最近
  检查点修订计划，不在未记录的情况下兼容错误格式。
- 每个子阶段遵循：恢复/只读审计 → 计划提交 → 小粒度实现 → 定向测试 → Terra High 对抗
  复核 → 主代理逐文件复核 → 全量测试 → Bad Case 台账 → 完成提交。
- 尚未接入持久化、迁移、真实 PostgreSQL 或浏览器的能力必须明确标注，不以 mock/SQLite
  测试替代生产结论。

## 五、后续阶段摘要

### 3.5

使用真实 PostgreSQL、后端独立进程和浏览器覆盖管理员建档、普通用户发起、版本冻结、
独立复核、改判留痕、归档、篡改拒绝、权限矩阵与个人知识库隔离。只有这一阶段通过，
管理员企业档案专项才可标记完成。

## 六、RAG 后续方向

专项封板后再建立组织级企业材料库：材料按 `company_profile_id + material_revision`
隔离，向量索引是派生缓存；RAG 只产候选证据，继续经过主体、原文、字段、日期和来源
闸门。候选若要提升为结构化字段，必须由管理员确认并产生新的档案 revision，不能由
模型直接改写系统记录。
