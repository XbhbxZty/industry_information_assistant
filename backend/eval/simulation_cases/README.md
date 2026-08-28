# 纯虚构保理尽调仿真案例

这里有四个离线保理尽调案例包，用于验证证据、冲突和未知状态的处理。所有企业、自然人、统一社会信用代码、合同/登记/文书号、地址、金额和交易均为**纯虚构测试数据**；企业信用代码以 `YFAKE` 开头并通过 18 位 USCC 校验，仅为了覆盖格式校验，绝非真实主体。

## Canonical v1 包结构与可见性

```text
<case>/
  manifest.json                         # Agent 可见；仅声明 input 五个文件
  input/                                # 唯一 Agent 可见信任区
    structured/application.json
    structured/entities.json
    structured/claims.jsonl
    structured/sources.json
    structured/materials.json
    documents/*
    site_visit/*
  oracle/expected.json                  # 仅评测器可读
```

生产加载器只接受 `manifest.json` 的 `input` 五个相对路径；根 manifest 不含 oracle 路径、预期结论或评分字段。`input/` 必须不能出现 `expected_decision`、`expected_claim_verdicts`、`known_conflicts` 或 `verdict` 等 oracle canary。评测器在先加载生产 `DueDiligenceCase` 后，才可以加载固定路径 `oracle/expected.json` 并做 case/claim/source 关联校验。

## 可见输入字段

- `manifest.json`：`schema_version`、`case_id`、标题、`simulation_only:true`、纯虚构声明、`as_of`、`cutoff_date`、业务类型、情景、主主体和五个 `input` 文件路径。所有可评分事实不得晚于 `cutoff_date`；`retrieved_at` 可以晚于它。
- `application.json`：保理申请、供应商/主借款人/付款债务人、申请金额、期限、融资用途、应收面额、预付率、合同号、付款日与交易期间。金额均为 `CNY`，期限为 `DAYS`，比例为 `PERCENT`。
- `entities.json`：`EntityBundle`。实体使用 portable ID、名称、种类、校验通过的企业 USCC、角色；`relationships` 表达合同相对方、付款债务、股东、控制和担保关系。
- `claims.jsonl`：每行是一条仅限 `claimed` 的待核验主张，含 `field_id`、主体、截至日、断言或测量值（恰好一种）以及可选材料引用；不包含真值或判定。
- `sources.json`：`SourceBundle` 的 `sources` 与一一对应的 `query_results`。每个结果都列出同主体已有 claim 的 `queried_field_ids`。`success_with_records` 需要检索时间和事实日期；`success_no_record` 需要检索时间和观察日；`timeout`、`not_queried`、`not_provided` 等未知结果必须空 records 并写 `outcome_detail`。
- `materials.json`：材料 ID、主体、类型、位于 `input/documents/` 或 `input/site_visit/` 的相对路径、来源、日期/期间和实际文件的 SHA-256。材料本体均真实存在于本仓库。

## Oracle 字段

`oracle/expected.json` 是唯一隐藏答案文件，包含 `expected_decision`、覆盖所有 input claim 的 `expected_claim_verdicts`（含 source refs 与理由）、`known_conflicts`、条件、可选 `maximum_advance` 和总理由。它不得放入 Agent 提示词、输入目录或 RAG 语料。条件和最大融资额仅可随 `conditional_approve` 出现。

## 四案预期

| Case | Agent 可见情形 | oracle 预期 |
|---|---|---|
| `case_a_consistent` | 合同、发票、交付、验收、对账、流水和登记结果基本一致 | `conditional_approve`，最高 CNY 4,000,000，放款前复查登记与付款路径 |
| `case_b_hidden_financing` | 基础贸易可见，但申请人隐瞒融资和动产抵押；登记、征信、流水反驳 | `reject` |
| `case_c_receivable_authenticity` | 合同/发票 CNY 10,000,000（1,000 万元）、发货 CNY 7,200,000（720 万元）、验收/对账 CNY 6,800,000（680 万元）；无确权且疑似重复转让 | `reject` |
| `case_d_insufficient_data` | 材料严重缺失；包含 `timeout`、`not_queried` 与 `not_provided` | `defer_manual_review`；失败与未查均是未知，绝不等同于无记录 |

直接验证：在 `backend/` 下运行 `pytest tests/test_due_diligence_simulation_cases.py -q`。该测试逐案调用生产 loader 与 oracle loader，并检查虚构残留、oracle canary、USCC 前缀和 D 案未知状态。
