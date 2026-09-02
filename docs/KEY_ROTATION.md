# 开发环境密钥轮换与退役预检

D3 提供“保留旧验证 key、切换新签发 key”的最小能力，不提供密钥托管平台或自动删 key。
没有更改用户 `.env`、已有业务数据库或运行中进程。D3 本身没有新增迁移；D4a 已增加
`20260902_0003` 上下文封签与领取模型。首次使用当前预检前，数据库需按迁移说明到达当前 head。

## 两类密钥各自管理

| 用途 | keyring / active 设置 | 历史兼容 |
|---|---|---|
| 企业档案审计、legacy anchor | `COMPANY_PROFILE_AUDIT_KEYS_JSON` / `COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID` | key ID 原样保存在审计/锚点中；不覆盖旧签名 |
| 检查点图、业务状态、上下文、冻结档案快照 | `RESEARCH_CHECKPOINT_KEYS_JSON` / `RESEARCH_CHECKPOINT_ACTIVE_KEY_ID` | v1 图/业务记录保留原派生指纹；新增上下文使用独立域 |

JSON 均为 `{"key-id":"Base64 编码的原始密钥字节"}`，不能填指纹或 MAC 代替原始密钥。
新 key 至少 32 字节随机值。不要复用两类密钥，不要重复利用已使用的 ID 指代另一份材料。
检查点 alias 必须保留原名：新 v3 快照直接绑定 alias，改名会使历史无法验证。

检查点 keyring 三个新设置**完全没有出现**时，保持原有 `ADMIN_PROFILE_SNAPSHOT_HMAC_KEY`
优先、`JWT_SECRET_KEY` 备用的兼容模式。任一新设置出现（即使空字符串），都会启用严格
显式模式；配置不全或错误立即失败，不会偷偷回退 JWT。

## 从原单密钥迁移

1. 先备份数据库和受保护的密钥配置，暂停研究/复核/档案写入，升级所有相关后端进程到 D3
   代码。不要让旧代码与会产生 v3 快照的新配置混跑。
2. 找到原先实际使用的 secret：非空 `ADMIN_PROFILE_SNAPSHOT_HMAC_KEY`，否则为当时的
   `JWT_SECRET_KEY`。将其 **UTF-8 原始字节**做 Base64，作为 `checkpoint-old`。
   不能先哈希、去空格或改写原字符串。
3. 在受保护配置中放入旧、新两个 key；先保持 active 为 `checkpoint-old`，设置
   `RESEARCH_CHECKPOINT_LEGACY_KEY_ID=checkpoint-old`。该 legacy 指定只用于没有 key ID
   的旧 v2 档案快照；它允许保留过去较短的 secret，不是新 key 的强度豁免。
4. 运行下面的只读预检。通过后把 active 改为 `checkpoint-new`，保留 old 与 legacy 设置，
   再预检并重启相关进程。新图/业务签名使用新 key；新档案快照使用带 key ID 的 v3。
5. 旧图/业务签名字节与旧 v2 快照不重签，读取不更新业务版本。正常继续运行后的新状态才
   使用新签名；内层旧 v2 快照可以继续保留，因此不能只看最新 business key 就删除 old。

v2 只使用明确的 legacy key，不猜 active、不遍历尝试 MAC。若历史 v2 曾由多份不同旧
secret 签发，需要单独核对和处置，不能把无法验证的记录自动升级成“可信”。

## 预检命令

在 `backend/` 执行，使用该目录 `.env`，已有进程环境优先。先独立核对 `DATABASE_URL`
选中的目标；命令不会迁移、签名、删数据、改 key 或写配置。

```text
python app/scripts/check_key_rotation.py
python app/scripts/check_key_rotation.py --retire-audit-key audit-old
python app/scripts/check_key_rotation.py --retire-checkpoint-key checkpoint-old
```

退役参数可重复。输出只含 key ID、派生指纹、引用计数、问题代码和目标数据库名，不含
密钥、档案正文或检查点正文。退出码：`0` 为所选数据库范围内通过，`2` 为引用/配置/验签
检查阻断，`3` 为目标连接、schema 或执行失败。`preflight_passed` 不是生产授权凭证。

预检用 PostgreSQL 只读、可重复读事务，并固定 public schema，检查：

- 审计记录与迁移锚点的 key 引用及实际历史签名；旧无签名行仍由 anchor 保护。
- 检查点业务签名、图签名、上下文封签，以及 v2/v3 内层档案快照各自使用的 key。
- 未知 key、错误 key 材料、缺失配对、重复 session、验签失败均阻断。
- 当前 active 或仍被记录引用的 key 不允许退役。
- 若本库 LangGraph `checkpoints/checkpoint_blobs/checkpoint_writes` 有数据，则检查点 key
  退役一律阻断。工具不解码这类历史，更不声称最新业务检查点覆盖了全部历史引用。

## 退役与回退

有审计/锚点引用的旧 key 应长期作为 verification-only 保留；切换 active 不等于删除旧 key。
对预检允许移除的闲置 key，仍要先核对备份、其他数据库、其他进程和运行中任务。预检不
扫描这些外部对象，也不锁住后续写入；不能边持续写入边把一次报告当永久保证。

确认可退役后，在停写窗口手动移除对应 JSON 项；如果还指定了该 legacy alias，也须一并
取消 legacy 设置，再运行不带退役参数的预检并重启。存在旧 v2 快照时不能执行这一步。
若 LangGraph 阻断，保留旧 key，或另行审阅历史归档/保留方案；本工具不清除历史来“过关”。

切换 active 后出问题，可把 active 改回 old，但必须保留新旧所有验证 key 和 D3 代码。
**不要直接回退旧二进制**去读取新 key/v3 快照。真正需要代码与数据一起回退时，应恢复
变更前相匹配的代码、数据库备份和密钥配置。遗失旧 key 无法靠重算普通 SHA 或重新锚定恢复。

## 已验证与未包含

定向测试覆盖旧、新、回滚 active、缺失历史 key、实际 PostgreSQL 保存/读取与只读预检；
真实 LangGraph MemorySaver 暂停后跨密钥复核恢复也有回归保护。未完成独立进程部署、
浏览器端到端或生产 KMS 验收。D4a 已补齐 owner/status/version 可信绑定及引用统计；
最终复核原子性仍由 D4b 承接。
