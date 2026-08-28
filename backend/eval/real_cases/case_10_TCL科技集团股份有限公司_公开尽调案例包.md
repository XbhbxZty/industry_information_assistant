# case_10：TCL科技集团股份有限公司公开尽调案例包

> **研究对象**：TCL科技集团股份有限公司  
> **统一社会信用代码**：91441300195971850Y  
> **业务场景**：合同签于“TCL集团”时期、申请融资时主体已经叫“TCL科技”；或发票债务人为 TCL 华星/TCL 中环等，而申请人错误地把母集团信用直接作为付款保证。  
> **研究截止日**：2024-12-31  
> **资料检索日期**：2026-08-14  
> **案例编号**：case_10  
> **案例难度**：困难  

---

## 一、案例摘要

| 字段 | 内容 |
|---|---|
| case_id | `case_10` |
| 企业主体 | TCL科技集团股份有限公司 |
| 统一社会信用代码 | `91441300195971850Y` |
| 上市代码 | `000100.SZ` |
| 业务场景 | 历史合同仍使用“TCL集团”旧称；或发票债务人为 TCL 华星、TCL 中环等独立法人，而融资申请人将 TCL 科技集团信用直接视为付款保证 |
| 研究截止日 | `2024-12-31` |
| 案例设定检索日 | `2026-08-14` |
| 本次来源实际复核日 | `2026-08-16` |
| 案例难度 | 困难 |
| 主要风险 | ①同一法人历史名称与现名被错误拆分；②旧债券简称/全称继续保留“TCL集团”造成字符串匹配误判；③TCL华星、TCL中环、TCL科技集团财务有限公司、TCL实业体系均需按独立法人解析；④集团合并信用被错误迁移给实际发票债务人；⑤应收账款保理、转让及关联保理安排增加权利链和重复融资核验要求；⑥截至2024Q3，TCL中环自身经营结果明显弱于TCL科技合并口径 |
| 关键缺失信息 | 具体合同、发票、订单、送货/验收、应收确权文件；实际债务人完整名称及USCC；是否已保理/转让/质押；付款历史；所谓母集团保证文件及授权；银行流水、纳税、企业征信；完整的法院执行/失信及市场监管实时查询快照 |
| 是否足以形成参考结论 | **是，但只能形成“实体识别及交易可融资性”的参考判断；不足以形成具体融资额度或认定某笔应收账款真实、有效、未重复融资。** |

最核心的主体事实已经能够闭环：现行章程把历史名称连续记载为“TCL集团有限公司→广东TCL集团股份有限公司→TCL集团股份有限公司→TCL科技集团股份有限公司”；而“16TCL03”付息公告明确说明，发行人更名并不修改既有债券名称。由此，旧称不能仅凭字符串被拆成另一个发行主体。

反方向同样成立：TCL中环、TCL华星以及TCL科技集团财务有限公司均以独立公司身份出现在法定披露中，不能因为名称中包含“TCL”便与上市母集团合并。尤其是TCL中环，截至2024年前三季度其自身净利润约为-64.78亿元，而TCL科技同期归母净利润仍为正，债务人信用差异具有实际意义。

---

## 二、来源目录

来源等级：

- `L1`：政府、监管、法定公示机关；
- `L2`：交易所、巨潮资讯等法定证券披露；
- `L3`：年报、审计报告、债券文件等原始专业文件；
- `L4`：公司官网；
- `L5`：新闻、第三方材料。

| source_id | 来源标题 | 发布机构 | 等级 | 发布日期 | 事件日期/期间 | 直接URL | 格式 | 页码/章节 | 访问状态 | 可下载 | 支持调查项目 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S01 | TCL科技集团股份有限公司章程（2024年1月修订） | TCL科技集团股份有限公司／巨潮资讯 | L2 | 2024-01-27 | 2024-01 | https://static.cninfo.com.cn/finalpage/2024-01-27/1219012834.PDF | PDF | p.3历史沿革；p.5第4、6条 | 成功 | 是 | 现名、曾用名、上市主体、注册资本 |
| S02 | TCL科技集团股份有限公司“16TCL03”2020年付息公告 | TCL科技集团股份有限公司／巨潮资讯 | L2/L3 | 2020-07-04 | 2020-07-07付息 | https://static.cninfo.com.cn/finalpage/2020-07-04/1208002409.PDF | PDF | p.1 | 成功 | 是 | 债券历史名称、更名后债券名称延续 |
| S03 | TCL科技集团股份有限公司2022年年度报告摘要 | TCL科技集团股份有限公司／巨潮资讯 | L2/L3 | 2023-03-31 | 2022-12-31 | https://static.cninfo.com.cn/finalpage/2023-03-31/1216280928.PDF | PDF | p.4-5财务指标；p.7-8债券 | 成功 | 是 | 2021-2022财务、历史债券名称、偿债指标 |
| S04 | TCL科技集团股份有限公司审计报告及财务报表（2023年度） | 大华会计师事务所（特殊普通合伙）；TCL科技披露 | L3 | 2024-04-30 | 2023-12-31；审计报告日2024-04-28 | https://static.cninfo.com.cn/finalpage/2024-04-30/1219922669.PDF | PDF | 审计报告p.2、关键审计事项p.5、合并报表、财务报表附注“应收账款” | 成功 | 是 | 审计意见、资产负债、利润、现金流、应收账款、保理、关联交易 |
| S05 | 关于开展应收账款保理业务暨关联交易的公告 | TCL科技集团股份有限公司／巨潮资讯 | L2 | 2023-03-31 | 2023年度保理安排 | https://static.cninfo.com.cn/finalpage/2023-03-31/1216280915.PDF | PDF | p.1-4 | 成功 | 是 | 关联保理、供应商应收转让、额度、追索/无追索 |
| S06 | 关于2023年日常关联交易执行情况的报告 | TCL科技集团股份有限公司／巨潮资讯 | L2 | 2024-04-30 | 2023年度；董事会审议2024-04-28 | https://static.cninfo.com.cn/finalpage/2024-04-30/1219922673.PDF | PDF | p.1-2及关联方章节 | 成功 | 是 | 日常关联交易规模、关联主体 |
| S07 | 关于TCL科技集团财务有限公司2024年半年度风险持续评估报告 | TCL科技集团股份有限公司／巨潮资讯 | L2 | 2024-08-27 | 2024-06-30 | https://static.cninfo.com.cn/finalpage/2024-08-27/1220986561.PDF | PDF | p.1-4 | 成功 | 是 | 财务公司主体、牌照、股权、监管指标、关联金融服务 |
| S08 | TCL科技集团股份有限公司2024年第三季度报告 | TCL科技集团股份有限公司／巨潮资讯 | L2 | 2024-10-30 | 2024-09-30 | https://static.cninfo.com.cn/finalpage/2024-10-30/1221556084.PDF | PDF | p.3-15 | 成功 | 是 | 最新截止日前财务、TCL华星/TCL中环定义、股东、TCL中环经营情况 |
| S09 | TCL中环新能源科技股份有限公司2024年半年度财务报告（未经审计） | TCL中环新能源科技股份有限公司／巨潮资讯 | L2/L3 | 2024-08-24 | 2024-06-30 | https://static.cninfo.com.cn/finalpage/2024-08-24/1220967323.PDF | PDF | p.1、公司基本情况/历史沿革章节 | 成功 | 是 | TCL中环独立代码、USCC、控制关系、应收账款 |
| S10 | 光明区2024年度概念验证中心、中小试基地认定资助项目拟资助单位名单（2家） | 深圳市光明区科技创新局 | L1 | **2025-08-28** | 2024年度项目 | https://www.szgm.gov.cn/gmkjcxj/attachment/1/1620/1620143/12350634.xls | XLS | 第3行 | 成功 | 是 | **后验**核对TCL华星完整法人名称及USCC；不得进入截止日风险判断 |
| S11 | 深圳市隆利科技股份有限公司2023年年度报告 | 深圳市隆利科技股份有限公司／巨潮资讯 | L3 | 2024-04-26 | 2023-12-31 | https://static.cninfo.com.cn/finalpage/2024-04-26/1219823606.PDF | PDF | 财务附注，约PDF p.150 | 成功 | 是 | “简单汇”无追索权金单保理的外部交易样本 |
| S12 | TCL中环关于控股子公司拟以增资扩股方式收购鑫芯半导体科技有限公司股权暨关联交易的公告 | TCL中环新能源科技股份有限公司／巨潮资讯 | L2 | 2023-01-20 | 2023-01-19 | https://static.cninfo.com.cn/finalpage/2023-01-20/1215665346.PDF | PDF | p.2-3 | 成功 | 是 | TCL科技USCC、成立日期、法定代表人、无实际控制人等身份交叉验证 |
| P01 | 2024 Annual Report of TCL Technology Group Corporation (Summary) | TCL科技集团股份有限公司／巨潮资讯 | L2/L3 | **2025-05-20** | 2024-12-31 | https://static.cninfo.com.cn/finalpage/2025-05-20/1223588693.PDF | PDF | 主要财务指标及经营回顾 | 成功 | 是 | **后验**2024全年业绩、TCL中环全年结果 |

> 注意：S10、P01虽然描述部分2024年度事实，但其公开日期晚于2024-12-31，因此统一标记 `as_of_eligible=false`，仅用于回测。

---

## 三、原子事实表

### A. 主体与实体解析

| claim_id | category | field_name | value | unit | period | subject_name | subject_identifier | status | source_ids | source_location | confidence | conflict_note | as_of_eligible |
|---|---|---|---|---|---|---|---|---|---|---|---:|---|---|
| C001 | basic | current_name | TCL科技集团股份有限公司 | — | 2024-12-31 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S01 | p.5第4条 | 1.00 | 无 | true |
| C002 | basic | unified_social_credit_code | 91441300195971850Y | — | 截止日前 | TCL科技集团股份有限公司 | 同左 | verified | S12 | p.2-3 | 0.99 | 无 | true |
| C003 | basic | establishment_date | 1982-03-11 | — | — | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S12 | p.2-3 | 0.99 | 无 | true |
| C004 | basic | registered_capital | 18,779,080,767 | 元 | 2024-01 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S01 | p.5第6条 | 0.99 | 历史文件中的注册资本较低属于时点差异 | true |
| C005 | basic | stock_code | 000100 | — | 截止日前 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S01 | p.3第3条 | 1.00 | 无 | true |
| C006 | basic | legal_representative | 李东生 | — | 截止日前已披露 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S12 | p.2-3 | 0.98 | 未以截止日市场监管实时快照二次复核 | true |
| C007 | basic | historical_name_chain | TCL集团有限公司→广东TCL集团股份有限公司→TCL集团股份有限公司→TCL科技集团股份有限公司 | — | 历史至2024 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S01 | p.3第2条 | 1.00 | 属于同一法人连续名称变化 | true |
| C008 | bond | bond_name_after_issuer_rename | 发行人更名不涉及既有债券名称修改 | — | 2020 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S02 | p.1 | 1.00 | 因此旧“TCL集团”债券名称不能单独证明另有发行人 | true |
| C009 | bond | historical_bond_display_name | 2022年披露的19TCL01/02/03仍保留“TCL集团股份有限公司”历史全称 | — | 2022 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S03 | 债券章节 | 0.99 | 与C008一致，不构成主体冲突 | true |
| C010 | equity | actual_controller | 无实际控制人 | — | 截止日前 | TCL科技集团股份有限公司 | 91441300195971850Y | verified | S09,S12 | 控制关系/关联交易主体介绍 | 0.98 | 无 | true |
| C011 | equity | largest_shareholder_group | 李东生及宁波九天联成一致行动关系，合计持股6.74%，为第一大股东 | % | 2024-09-30 | TCL科技集团股份有限公司 | 000100 | verified | S08 | 股东章节 | 0.99 | “第一大股东”不等于“实际控制人” | true |
| C012 | relation | tze_stock_code | 002129 | — | 2024 | TCL中环新能源科技股份有限公司 | 911200001034137808 | verified | S09 | 公司基本情况 | 1.00 | 与000100为不同上市法人 | true |
| C013 | relation | tze_uscc | 911200001034137808 | — | 2024 | TCL中环新能源科技股份有限公司 | 002129 | verified | S09 | 公司基本情况/沿革 | 0.99 | 无 | true |
| C014 | relation | tze_control_relationship | TCL科技间接控制TCL中环 | — | 2024 | TCL中环新能源科技股份有限公司 | 002129 | verified | S09 | 历史沿革/控制关系 | 0.99 | 控制关系不改变法人独立性 | true |
| C015 | relation | csot_entity | TCL华星光电技术有限公司作为独立公司主体列示 | — | 2024 | TCL华星光电技术有限公司 | 截止日前本证据包未使用后验USCC | verified | S07,S08 | 定义及财务公司股权章节 | 0.98 | 不得与母集团按“TCL”字符串合并 | true |
| C016 | relation | finance_company_uscc | 91441300717867103C | — | 2024-06-30 | TCL科技集团财务有限公司 | 同左 | verified | S07 | p.1 | 1.00 | 无 | true |
| C017 | relation | finance_company_license | L0066H344130001 | — | 2024-06-30 | TCL科技集团财务有限公司 | 91441300717867103C | verified | S07 | p.1 | 1.00 | 无 | true |
| C018 | relation | finance_company_ownership | TCL科技持股82%，TCL华星持股18% | % | 2024-06-30 | TCL科技集团财务有限公司 | 91441300717867103C | verified | S07 | p.1 | 1.00 | 股权关系进一步证明TCL华星与母公司是不同法人主体 | true |
| C019 | relation | tcl_industries_related_party | TCL实业及其保理子公司因李东生在相关企业任职关系被认定为关联法人 | — | 2023 | TCL实业体系 | — | verified | S05 | p.1-3 | 0.98 | “关联法人”不等于“TCL科技自身” | true |
| C054 | basic | registration_status | null | — | 2024-12-31 | TCL科技集团股份有限公司 | 91441300195971850Y | unverified | S01 | 章程仅记载公司永久存续，未取得截止日市场监管实时登记快照 | 0.35 | 不能据章程替代工商登记状态查询 | true |

### B. 保理、应收账款及关联交易

| claim_id | category | field_name | value | unit | period | subject_name | subject_identifier | status | source_ids | source_location | confidence | conflict_note | as_of_eligible |
|---|---|---:|---:|---|---|---|---|---|---|---|---:|---|---|
| C020 | scf | annual_factoring_cap | 不超过4,000,000,000 | 元 | 2023年度安排 | TCL科技及控股子公司相关保理 | 000100 | verified | S05 | p.1 | 0.99 | 为业务额度，不代表期末实际余额 | true |
| C021 | scf | supplier_receivable_transfer_structure | 上游供应商或取得相关应收权益的企业，可将其对TCL科技或其控股子公司的应收账款权益转让给关联保理公司 | — | 2023 | 相关供应商/债务人 | — | verified | S05 | p.1 | 0.99 | 必须逐笔识别“公司”还是具体控股子公司为债务人 | true |
| C022 | scf | factoring_recourse_type | 公告允许根据具体合同采用有追索权或无追索权保理 | — | 2023 | TCL科技及相关方 | — | verified | S05 | p.4 | 0.98 | 具体交易类型仍须看合同 | true |
| C023 | scf | simplehui_external_example | 隆利科技通过TCL商业保理（深圳）有限公司“简单汇”开展无追索权金单保理并终止确认13,628,943.15元应收款项融资 | 元 | 2023 | 深圳市隆利科技股份有限公司 | — | verified | S11 | 财务附注 | 0.99 | 仅证明该平台/模式存在，不证明本案某张发票已融资 | true |
| C036 | financial | accounts_receivable_net | 22,003,651 | 千元 | 2023-12-31 | TCL科技集团股份有限公司合并口径 | 000100 | verified | S04 | 应收账款附注 | 0.99 | 合并口径不等于某个子公司单体应收 | true |
| C037 | financial | accounts_receivable_within_1_year | 94.18 | % | 2023-12-31 | TCL科技集团股份有限公司合并口径 | 000100 | verified | S04 | 应收账款账龄附注 | 0.99 | 无 | true |
| C038 | financial | top5_accounts_receivable_ratio | 45.30 | % | 2023-12-31 | TCL科技集团股份有限公司合并口径 | 000100 | verified | S04 | 应收账款前五名附注 | 0.99 | 无 | true |
| C039 | scf | derecognized_ar_via_discount_factoring | 7,223,995 | 千元 | 2023 | TCL科技集团股份有限公司合并口径 | 000100 | verified | S04 | 应收账款终止确认附注 | 0.99 | 仅为合并层面已终止确认金额 | true |
| C040 | relation | related_party_transactions_kam | 约36,100,000,000 | 元 | 2023 | TCL科技集团股份有限公司 | 000100 | verified | S04 | 关键审计事项“关联方关系及交易” | 0.98 | 与C041统计口径不同 | true |
| C041 | relation | specified_daily_related_transactions_actual | 21,294,450,000 | 元 | 2023 | TCL科技集团股份有限公司 | 000100 | verified | S06 | p.1-2 | 0.99 | 仅特定日常关联交易口径，不能与审计KAM约361亿元直接比较 | true |

### C. 经营与财务

| claim_id | category | field_name | value | unit | period | subject_name | status | source_ids | source_location | confidence | conflict_note | as_of_eligible |
|---|---|---:|---:|---|---|---|---|---|---|---:|---|---|
| C024 | audit | audit_opinion | 财务报表在所有重大方面按企业会计准则编制并公允反映相关财务状况和经营成果 | — | 2023 | TCL科技 | verified | S04 | 审计报告p.2 | 1.00 | 标准无保留表述；不等于对未来偿债提供保证 | true |
| C025 | financial | revenue | 163,657,700,477 | 元 | 2021 | TCL科技合并 | verified | S03 | 主要财务数据 | 0.99 | 调整后口径 | true |
| C026 | financial | net_profit_attributable_parent | 10,064,253,118 | 元 | 2021 | TCL科技合并 | verified | S03 | 主要财务数据 | 0.99 | — | true |
| C027 | financial | operating_cash_flow | 32,878,450,437 | 元 | 2021 | TCL科技合并 | verified | S03 | 主要财务数据 | 0.99 | — | true |
| C028 | financial | revenue | 166,552,785,829 | 元 | 2022 | TCL科技合并 | verified | S03 | 主要财务数据 | 1.00 | — | true |
| C029 | financial | net_profit_attributable_parent | 261,319,451 | 元 | 2022 | TCL科技合并 | verified | S03 | 主要财务数据 | 1.00 | 较2021大幅下降 | true |
| C030 | financial | operating_cash_flow | 18,426,376,609 | 元 | 2022 | TCL科技合并 | verified | S03 | 主要财务数据 | 1.00 | — | true |
| C031 | financial | revenue | 174,366,657 | 千元 | 2023 | TCL科技合并 | verified | S04 | 合并利润表 | 1.00 | — | true |
| C032 | financial | net_profit_attributable_parent | 2,214,934 | 千元 | 2023 | TCL科技合并 | verified | S04 | 合并利润表 | 1.00 | — | true |
| C033 | financial | operating_cash_flow | 25,314,756 | 千元 | 2023 | TCL科技合并 | verified | S04 | 合并现金流量表 | 1.00 | — | true |
| C034 | financial | total_assets | 382,859,086 | 千元 | 2023-12-31 | TCL科技合并 | verified | S04 | 合并资产负债表 | 1.00 | — | true |
| C035 | financial | total_liabilities | 237,593,113 | 千元 | 2023-12-31 | TCL科技合并 | verified | S04 | 合并资产负债表 | 1.00 | — | true |
| C042 | financial | revenue | 123,028,497,947 | 元 | 2024-01-01至09-30 | TCL科技合并 | verified | S08 | 主要财务数据 | 1.00 | 未经审计 | true |
| C043 | financial | consolidated_net_profit | -1,829,006,223 | 元 | 2024-01-01至09-30 | TCL科技合并 | verified | S08 | 合并利润表 | 1.00 | 与C044口径不同，不冲突 | true |
| C044 | financial | net_profit_attributable_parent | 1,525,319,763 | 元 | 2024-01-01至09-30 | TCL科技合并 | verified | S08 | 主要财务数据/利润表 | 1.00 | 少数股东损益导致其可与合并净利润方向不同 | true |
| C045 | financial | operating_cash_flow | 22,000,714,536 | 元 | 2024-01-01至09-30 | TCL科技合并 | verified | S08 | 现金流量表 | 1.00 | 未经审计 | true |
| C046 | financial | total_assets | 393,795,228,854 | 元 | 2024-09-30 | TCL科技合并 | verified | S08 | 资产负债表 | 1.00 | 未经审计 | true |
| C047 | financial | total_liabilities | 257,182,269,426 | 元 | 2024-09-30 | TCL科技合并 | verified | S08 | 资产负债表 | 1.00 | 未经审计 | true |
| C048 | financial | accounts_receivable | 23,482,521,853 | 元 | 2024-09-30 | TCL科技合并 | verified | S08 | 资产负债表 | 1.00 | 未经审计 | true |
| C049 | financial | short_term_borrowings | 11,346,551,303 | 元 | 2024-09-30 | TCL科技合并 | verified | S08 | 资产负债表 | 1.00 | 未经审计 | true |
| C050 | subsidiary_financial | revenue | 22,582,000,000 | 元 | 2024-01-01至09-30 | TCL中环 | verified | S08 | 经营情况章节 | 0.98 | 公告采用亿元/百万元概述口径 | true |
| C051 | subsidiary_financial | net_profit | -6,478,000,000 | 元 | 2024-01-01至09-30 | TCL中环 | verified | S08 | 经营情况章节 | 0.98 | 属于TCL中环，不得直接归属TCL科技母公司 | true |
| C052 | finance_company | capital_adequacy_ratio | 30.75 | % | 2024-06-30 | TCL科技集团财务有限公司 | verified | S07 | 监管指标章节 | 0.99 | 为公司披露的持续风险评估结果 | true |
| C053 | finance_company | nonperforming_loan_ratio | 0 | % | 2024-06-30 | TCL科技集团财务有限公司 | verified | S07 | 资产质量章节 | 0.99 | 不得外推为集团所有应收账款“零信用风险” | true |

---

## 四、尽调检查清单

| 检查项目 | 状态 | 结论 | 证据ID | 冲突 | 缺失材料 | 是否触发人工复核 | 是否触发硬性风险门槛 |
|---|---|---|---|---|---|---|---|
| 当前法人名称、USCC、上市代码 | verified | `TCL科技集团股份有限公司 / 91441300195971850Y / 000100`可相互关联 | S01,S12 | 无 | 截止日工商实时快照 | 是，交易时须重核 | **是：合同/发票主体不一致即触发** |
| 曾用名连续性 | verified | 历史“TCL集团”等名称属于同一法人连续更名 | S01 | 无 | 无重大缺口 | 否 | 否 |
| 历史债券名称连续性 | verified | 发行人更名后既有债券名称可以继续保留旧称 | S02,S03 | 无 | 无 | 否 | 否 |
| 登记状态 | unverified | 章程载明公司永久存续，但未取得2024-12-31市场监管实时状态快照 | S01 | 无 | 官方登记状态查询结果 | 是 | 视实际查询结果 |
| 法定代表人 | verified | 已披露李东生 | S12 | 无 | 截止日市场监管快照 | 是 | 否 |
| 实际控制人 | verified | 披露口径为“无实际控制人” | S09,S12 | 无 | — | 否 | 否 |
| 第一大股东与实控人区分 | verified | 李东生及一致行动方为第一大股东组合，不应自动写成实际控制人 | S08,S09 | 无 | — | 否 | 否 |
| TCL中环主体识别 | verified | `002129 / 911200001034137808`，为不同上市法人，由TCL科技间接控制 | S09 | 无 | 交易时最新工商信息 | **是** | **是：发票债务人为TCL中环时不得替换成000100** |
| TCL华星主体识别 | verified | 截止日前公告明确其为独立公司主体 | S07,S08 | 无 | 交易时须取得其完整USCC；本包所取得政府USCC材料为截止日后公开 | **是** | **是** |
| TCL科技集团财务有限公司主体识别 | verified | 独立持牌财务公司，USCC及金融许可证明确 | S07 | 无 | 具体结算/承兑/贷款合同 | 是 | 视交易结构 |
| TCL实业/关联保理主体识别 | verified | 属关联法人体系，不等同于TCL科技上市公司本体 | S05 | 无 | 具体保理合同及权利链 | **是** | **是** |
| 2021-2023财务趋势 | verified | 收入稳定增长，但盈利波动大；经营现金流连续为正 | S03,S04 | 无实质冲突 | 2024全年报告在截止日尚未公开 | 是 | 否 |
| 2024截止日前最新财务 | verified | 可取得2024Q3数据，但未经审计 | S08 | 无 | 2024全年经审计数据在截止日不可得 | 是 | 否 |
| 审计意见 | verified | 2023年审计报告为标准公允反映表述 | S04 | 无 | 2024年度审计报告截止日不可得 | 否 | 非硬门槛 |
| 应收账款规模、账龄、集中度 | verified | 2023净额220.04亿元，94.18%一年以内，前五名占45.30% | S04 | 无 | 本案具体债权账龄及付款方 | 是 | 具体债权层面是 |
| 公开保理/转让安排 | verified | 存在集团及供应商应收账款保理、转让机制 | S05,S04 | 无 | 某笔债权是否已保理/转让/质押 | **是** | **是** |
| “简单汇”平台外部使用证据 | verified | 其他上市公司披露存在无追索权金单保理实践 | S11 | 无 | 本案是否使用该平台 | 是 | 否；仅作线索 |
| 具体合同真实性及签约主体 | unverified | 无公开资料可证明本案合同 | — | — | 合同原件、签章、授权 | **是** | **是** |
| 发票债务人 | unverified | 未提供本案发票，不能判断为TCL科技、TCL华星或TCL中环 | — | — | 发票原件/电子底账、购方USCC | **是** | **是** |
| 交付、物流、验收 | unverified | 公共信息不能验证本案基础交易履行 | — | — | PO、送货单、签收单、验收单、物流 | **是** | **是** |
| 应收确权 | unverified | 无本案债务人确权资料 | — | — | 对账单、确权书、付款承诺 | **是** | **是** |
| 是否已质押/转让/重复融资 | unverified | 集团存在保理业务并不能说明本笔债权状态 | S05,S04 | 无 | 动产融资登记、保理台账、转让通知、平台状态 | **是** | **是** |
| 所谓TCL科技母集团付款保证 | unverified | 集团关系及合并报表本身不构成某一子公司债务的合同保证证据 | — | — | 保证合同、董事会/授权文件、金额期限范围 | **是** | **是：若授信依赖母集团保证** |
| 重大诉讼/仲裁完整情况 | unverified | 本次未取得能够代表完整法定查询范围的截止日清单 | — | — | 法院、交易所及企业案件明细核验 | **是** | 发现重大事项后判断 |
| 被执行/失信/限制消费/终本 | unverified | 未形成可审计的官方全量查询快照，不能写“无记录” | — | — | 中国执行信息公开网等官方查询快照 | **是** | 发现记录后判断 |
| 行政处罚/经营异常 | unverified | 未取得截止日市场监管/信用中国完整主体查询结果 | — | — | 官方主体查询记录 | **是** | 发现重大事项后判断 |
| 监管处分/公开谴责 | unverified | 本次材料不足以形成完整否定性结论 | — | — | 深交所/证监会完整纪律处分查询 | **是** | 发现重大事项后判断 |
| 债券违约/贷款逾期总体结论 | unverified | 个别债券正常付息公告及2022偿付率不能证明所有债务从未违约 | S02,S03 | 无 | 全量债券受托报告、债务逾期披露、银行征信 | **是** | 是 |
| 银行流水/税务/企业征信 | unverified | 非公开数据，未获取且不得虚构 | — | — | 授权后私域资料 | **是** | 按机构政策 |
| 截止日控制 | verified | S10、P01全部排除于2024-12-31当时的风险判断 | S10,P01 | 无 | — | 否 | 是，评测规则层面 |

---

## 五、参考报告

### 5.1 经证据验证的事实

TCL科技集团股份有限公司的历史名称具有明确连续性。章程记载其名称经历“TCL集团有限公司”“广东TCL集团股份有限公司”“TCL集团股份有限公司”直至“TCL科技集团股份有限公司”的变化，因此这些名称在相应历史时期指向同一法人主体，而非单纯根据名称字符串建立多个企业实体。【C007；S01】

这一连续性也体现在债券披露中。“16TCL03”2020年付息公告明确说明，发行人更名“不涉及债券名称的修改”；2022年年度报告仍列有保留“TCL集团股份有限公司”历史名称的存量债券。【C008-C009；S02-S03】

因此，本案例中若一份历史合同写“TCL集团股份有限公司”，不能仅因为申请融资时公司已更名“TCL科技集团股份有限公司”就把它拆成两个债务主体。主体核对的主键应至少包括统一社会信用代码、公司历史沿革、合同签署时点及法定更名证据，而不是名称字符串。

另一方面，“TCL”品牌下并非所有公司都是同一法人。TCL中环新能源科技股份有限公司具有独立股票代码`002129`和统一社会信用代码`911200001034137808`，公开文件将其描述为由TCL科技间接控制的上市公司。【C012-C014；S09】

TCL华星光电技术有限公司也作为独立企业在TCL科技披露中被单独定义；TCL科技集团财务有限公司具有独立USCC `91441300717867103C`和金融许可证`L0066H344130001`，其股权结构为TCL科技82%、TCL华星18%。【C015-C018；S07-S08】

因此，名称中都含“TCL”只表示集团或品牌关联线索，不产生法人合并效果。

截至2023年末，TCL科技合并应收账款净额约220.04亿元，其中一年以内占94.18%，前五名应收账款占45.30%；2023年度因贴现、保理等终止确认的应收账款约72.24亿元。【C036-C039；S04】

2023年公司还披露关联保理安排：公司及控股子公司可开展应收账款保理，上游供应商或取得相关应收权益的企业亦可将其对“公司或其控股子公司”的应收账款权益转让给TCL实业体系保理公司，年度循环额度上限40亿元，具体交易可采用有追索权或无追索权模式。【C020-C022；S05】

这种措辞本身说明保理债权中的付款义务主体可能是“TCL科技”也可能是“某个控股子公司”，两者必须逐笔区分。

在财务层面，TCL科技2021、2022、2023年合并营业收入分别约1636.58亿元、1665.53亿元和1743.67亿元；归母净利润分别约100.64亿元、2.61亿元和22.15亿元，盈利波动明显，而经营现金流分别约328.78亿元、184.26亿元和253.15亿元，均为正。【C025-C033；S03-S04】

2023年末总资产约3828.59亿元，总负债约2375.93亿元。【C034-C035；S04】

截至2024年前三季度，TCL科技合并口径实现营业收入约1230.28亿元、归母净利润约15.25亿元、经营现金流约220.01亿元；截至9月末总资产约3937.95亿元、总负债约2571.82亿元、应收账款约234.83亿元、短期借款约113.47亿元。【C042、C044-C049；S08】

但同一期间TCL中环收入约225.82亿元、净利润约-64.78亿元。【C050-C051；S08】

该差异是本案例最重要的付款主体风险证据之一：**母集团合并信用表现不能自动代替实际发票债务人的单体信用表现。**

### 5.2 从事实推导出的分析

第一，历史“TCL集团”合同不能通过名称字符串直接判定为另一个法人。若USCC、历史沿革及签约主体能够连续对应，应当归并到同一法律实体。【基于C001-C009】

第二，这一规则不能反过来变成“所有TCL公司都归并”。TCL中环、TCL华星、TCL科技集团财务有限公司等均存在独立法人识别证据。Agent若只按品牌名称做模糊匹配，很容易出现两种相反错误：

```text
同一法人被拆分：

TCL集团股份有限公司 → 被误识别为A
TCL科技集团股份有限公司 → 被误识别为B
```

以及：

```text
不同法人被合并：

TCL科技集团股份有限公司
TCL华星光电技术有限公司
TCL中环新能源科技股份有限公司
→ 被错误归为同一个“TCL集团债务人”
```

第三，“TCL科技控制TCL中环”不能推出“TCL科技当然对TCL中环的采购应付款承担保证责任”。控制关系、合并报表和品牌关系均不是保证合同本身。若融资方案依赖母公司信用增级，应单独取得并验证保证文件、保证人授权、保证范围、金额、期限和生效条件。

第四，TCL中环2024年前三季度约64.78亿元净亏损不能归属于TCL科技母公司本体，也不能据此宣称TCL科技“违约”；它的意义是证明子公司的债务人风险可能与母集团合并财务表现显著不同。【C044、C050-C051；S08】

第五，2023年存在大规模应收账款贴现/保理终止确认及公开的关联保理安排，意味着交易审查不能只确认“应收账款存在”，还必须确认该笔权利此前是否已经转让、保理、质押或者进入金单/供应链平台。【C020-C023、C039】

第六，约361亿元关联方交易被审计师列为关键审计事项，应理解为主体映射和关联交易核验的重要性较高，而不是违规事实。【C040；S04】

### 5.3 未经验证的假设

以下命题均不得作为本案例事实：

- “本案发票的购方就是TCL科技集团股份有限公司”；
- “TCL华星/TCL中环应付款由TCL科技无条件兜底”；
- “历史合同上的TCL集团一定等同于当前TCL科技”——仍需结合合同主体及识别码核对；
- “本案应收账款没有做过保理、质押或转让”；
- “本案基础交易已经真实交付并验收”；
- “TCL商业保理‘简单汇’上的公开案例意味着本案也使用该平台”；
- “财务公司不良贷款率为0，因此所有TCL体系供应商应收均安全”；
- “未检索到司法风险就等于没有司法风险”。

### 5.4 数据缺口

最关键的缺口不是上市公司财务数据，而是交易级证据：

合同及补充协议、发票及购方USCC、订单、送货单、物流、签收和验收、双方对账单、应收账款确权、付款历史、付款账户、保理及转让通知、动产融资登记、金单/平台融资状态、保证合同及公司授权材料均未提供。

此外，本次公开数据包未形成截至2024-12-31的市场监管登记状态、法院执行/失信/限制消费/终本、全部监管纪律处分以及全部金融债务逾期事项的可审计官方查询快照，因此相应项目均维持 `unverified`，不转换为“无风险”。

### 5.5 截止日之后的后验结果

2025年5月20日公开的2024年年度报告摘要显示，TCL科技2024全年营业收入约1648.23亿元、归母净利润约15.64亿元、经营现金流约295.27亿元；这些结果在2024-12-31尚未公开，因此不进入当时的授信判断。【P001-P003；P01】

同一后验材料显示，TCL中环2024全年归属于其股东的净利润约为-98.2亿元。该结果与截止日前已经观察到的2024前三季度大额亏损方向一致，但只能用于回测，不能倒灌成为2024-12-31时点已知事实。【P004；P01】

2025年8月深圳市光明区科技创新局公开名单进一步给出TCL华星光电技术有限公司USCC `91440300697136927G`。因为材料公开时间晚于截止日，本案例仅将其作为后验静态身份交叉验证。【P005；S10】

---

## 六、参考风险判断

```yaml
risk_level: 高

risk_scope: >
  这里的“高”指本案例给定业务场景下的
  “债务人实体识别、应收账款权利完整性和错误使用母集团信用”的交易风险，
  不等同于认定“TCL科技集团股份有限公司主体信用等级为高风险”。

hard_gate_triggers:
  - 合同、发票中的债务人无法与完整法人名称及USCC唯一匹配
  - 发票债务人为TCL华星、TCL中环等子公司，却将TCL科技自动登记为债务人
  - 授信依赖“TCL科技母集团保证”，但无法提供有效保证合同和授权
  - 无法排除应收账款此前已保理、转让、质押或重复融资
  - 合同、发票、交付/验收、对账/确权之间不能形成一致证据链
  - 历史“TCL集团”名称仅凭字符串匹配而未核验法人连续性

key_risk_factors:
  - 同一主体历史名称变化与历史债券旧称并存，易产生实体拆分错误
  - TCL华星、TCL中环等属于不同法人，易发生反向实体合并错误
  - TCL中环2024前三季度自身经营结果显著弱于TCL科技合并口径
  - 2023年存在较大规模的应收账款保理/贴现终止确认
  - 存在供应商将对公司或控股子公司的应收权益转让给关联保理公司的机制
  - 关联交易规模较大且被列为关键审计事项
  - 本案没有交易级原始凭证，无法验证债权真实性、唯一性和可转让性

mitigating_factors:
  - TCL科技历史名称连续性有高等级法定披露证据，可以可靠消除“旧称即不同主体”的错误
  - 2023年度取得标准公允反映的审计意见
  - 2021-2023经营现金流持续为正
  - 2024前三季度TCL科技合并经营现金流仍为正
  - 主要集团法人之间存在较充分公开披露，可通过USCC、证券代码和控制关系进行实体解析

missing_critical_evidence:
  - 本案合同、发票及实际付款义务人USCC
  - 订单、物流、签收、验收和对账
  - 应收账款确权
  - 保理、转让、质押和平台融资状态
  - 动产融资统一登记等权利负担查询
  - 母集团保证合同及有效公司授权
  - 债务人历史实际回款
  - 银行流水、税务及企业征信
  - 截止日官方司法、执行、市场监管和纪律处分完整查询快照

recommended_action: 暂缓
credit_amount: null
confidence: 0.89

reasoning_claim_ids:
  - C007
  - C008
  - C009
  - C014
  - C015
  - C020
  - C021
  - C039
  - C044
  - C050
  - C051
```

> 风险判断说明：这里的“暂缓”并非直接拒绝，而是因为本案例最核心的债务人、保证关系和应收权利状态均属于尚未提供的交易级证据。该判断仅用于离线尽调Agent评测，不构成真实金融审批意见。

---

## 七、机器可读 JSON

```json
{
  "case": {
    "case_id": "case_10",
    "difficulty": "困难",
    "business_scenario": "合同签于“TCL集团”时期但融资时主体已更名为“TCL科技”，或发票债务人为TCL华星/TCL中环等独立法人而申请人错误使用母集团信用作为付款保证",
    "research_cutoff_date": "2024-12-31",
    "declared_retrieval_date": "2026-08-14",
    "actual_source_verification_date": "2026-08-16",
    "assessment_purpose": "公开资料离线尽调Agent评测"
  },
  "subject": {
    "name": "TCL科技集团股份有限公司",
    "uscc": "91441300195971850Y",
    "stock_code": "000100",
    "stock_exchange": "深圳证券交易所",
    "historical_names": [
      "TCL集团有限公司",
      "广东TCL集团股份有限公司",
      "TCL集团股份有限公司",
      "TCL科技集团股份有限公司"
    ],
    "actual_controller": "无实际控制人",
    "registration_status": {
      "status": "unverified",
      "note": "未取得截至2024-12-31的市场监管机关实时登记状态快照"
    }
  },
  "sources": [
    {
      "source_id": "S01",
      "title": "TCL科技集团股份有限公司章程（2024年1月修订）",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2",
      "publication_date": "2024-01-27",
      "event_date": "2024-01",
      "url": "https://static.cninfo.com.cn/finalpage/2024-01-27/1219012834.PDF",
      "file_type": "PDF",
      "location": "p.3历史沿革；p.5第4、6条",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S02",
      "title": "TCL科技集团股份有限公司“16TCL03”2020年付息公告",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2/L3",
      "publication_date": "2020-07-04",
      "event_date": "2020-07-07",
      "url": "https://static.cninfo.com.cn/finalpage/2020-07-04/1208002409.PDF",
      "file_type": "PDF",
      "location": "p.1",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S03",
      "title": "TCL科技集团股份有限公司2022年年度报告摘要",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2/L3",
      "publication_date": "2023-03-31",
      "event_date": "2022-12-31",
      "url": "https://static.cninfo.com.cn/finalpage/2023-03-31/1216280928.PDF",
      "file_type": "PDF",
      "location": "p.4-5主要财务指标；p.7-8债券",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S04",
      "title": "TCL科技集团股份有限公司审计报告及财务报表（2023年1月1日至2023年12月31日止）",
      "publisher": "大华会计师事务所（特殊普通合伙）／TCL科技披露",
      "level": "L3",
      "publication_date": "2024-04-30",
      "event_date": "2023-12-31",
      "url": "https://static.cninfo.com.cn/finalpage/2024-04-30/1219922669.PDF",
      "file_type": "PDF",
      "location": "审计报告、关键审计事项、合并财务报表、应收账款附注",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S05",
      "title": "TCL科技集团股份有限公司关于开展应收账款保理业务暨关联交易的公告",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2",
      "publication_date": "2023-03-31",
      "event_date": "2023年度",
      "url": "https://static.cninfo.com.cn/finalpage/2023-03-31/1216280915.PDF",
      "file_type": "PDF",
      "location": "p.1-4",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S06",
      "title": "TCL科技集团股份有限公司关于2023年日常关联交易执行情况的报告",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2",
      "publication_date": "2024-04-30",
      "event_date": "2023年度",
      "url": "https://static.cninfo.com.cn/finalpage/2024-04-30/1219922673.PDF",
      "file_type": "PDF",
      "location": "p.1-2及关联方章节",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S07",
      "title": "TCL科技集团股份有限公司关于TCL科技集团财务有限公司2024年半年度风险持续评估报告",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2",
      "publication_date": "2024-08-27",
      "event_date": "2024-06-30",
      "url": "https://static.cninfo.com.cn/finalpage/2024-08-27/1220986561.PDF",
      "file_type": "PDF",
      "location": "p.1-4",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S08",
      "title": "TCL科技集团股份有限公司2024年第三季度报告",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2",
      "publication_date": "2024-10-30",
      "event_date": "2024-09-30",
      "url": "https://static.cninfo.com.cn/finalpage/2024-10-30/1221556084.PDF",
      "file_type": "PDF",
      "location": "p.3-15",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S09",
      "title": "TCL中环新能源科技股份有限公司2024年半年度财务报告（未经审计）",
      "publisher": "TCL中环新能源科技股份有限公司／巨潮资讯",
      "level": "L2/L3",
      "publication_date": "2024-08-24",
      "event_date": "2024-06-30",
      "url": "https://static.cninfo.com.cn/finalpage/2024-08-24/1220967323.PDF",
      "file_type": "PDF",
      "location": "p.1、公司基本情况及历史沿革",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S10",
      "title": "光明区2024年度概念验证中心、中小试基地认定资助项目拟资助单位名单（2家）",
      "publisher": "深圳市光明区科技创新局",
      "level": "L1",
      "publication_date": "2025-08-28",
      "event_date": "2024年度",
      "url": "https://www.szgm.gov.cn/gmkjcxj/attachment/1/1620/1620143/12350634.xls",
      "file_type": "XLS",
      "location": "第3行",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": false
    },
    {
      "source_id": "S11",
      "title": "深圳市隆利科技股份有限公司2023年年度报告",
      "publisher": "深圳市隆利科技股份有限公司／巨潮资讯",
      "level": "L3",
      "publication_date": "2024-04-26",
      "event_date": "2023-12-31",
      "url": "https://static.cninfo.com.cn/finalpage/2024-04-26/1219823606.PDF",
      "file_type": "PDF",
      "location": "财务附注，约PDF p.150",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "S12",
      "title": "TCL中环新能源科技股份有限公司关于控股子公司拟以增资扩股方式收购鑫芯半导体科技有限公司股权暨关联交易的公告",
      "publisher": "TCL中环新能源科技股份有限公司／巨潮资讯",
      "level": "L2",
      "publication_date": "2023-01-20",
      "event_date": "2023-01-19",
      "url": "https://static.cninfo.com.cn/finalpage/2023-01-20/1215665346.PDF",
      "file_type": "PDF",
      "location": "p.2-3",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": true
    },
    {
      "source_id": "P01",
      "title": "2024 Annual Report of TCL Technology Group Corporation (Summary)",
      "publisher": "TCL科技集团股份有限公司／巨潮资讯",
      "level": "L2/L3",
      "publication_date": "2025-05-20",
      "event_date": "2024-12-31",
      "url": "https://static.cninfo.com.cn/finalpage/2025-05-20/1223588693.PDF",
      "file_type": "PDF",
      "location": "主要财务指标、经营回顾",
      "access_status": "success",
      "downloadable": true,
      "as_of_eligible": false
    }
  ],
  "claims": [
    {
      "claim_id": "C001",
      "category": "basic",
      "field_name": "current_name",
      "value": "TCL科技集团股份有限公司",
      "unit": null,
      "period": "2024-12-31",
      "subject_name": "TCL科技集团股份有限公司",
      "subject_identifier": "91441300195971850Y",
      "status": "verified",
      "source_ids": ["S01"],
      "source_location": "p.5第4条",
      "confidence": 1.0,
      "conflict_note": null,
      "as_of_eligible": true
    },
    {
      "claim_id": "C007",
      "category": "basic",
      "field_name": "historical_name_chain",
      "value": [
        "TCL集团有限公司",
        "广东TCL集团股份有限公司",
        "TCL集团股份有限公司",
        "TCL科技集团股份有限公司"
      ],
      "unit": null,
      "period": "historical_to_2024",
      "subject_name": "TCL科技集团股份有限公司",
      "subject_identifier": "91441300195971850Y",
      "status": "verified",
      "source_ids": ["S01"],
      "source_location": "p.3第2条",
      "confidence": 1.0,
      "conflict_note": "连续更名而非多个法人",
      "as_of_eligible": true
    },
    {
      "claim_id": "C008",
      "category": "bond",
      "field_name": "bond_name_after_issuer_rename",
      "value": "发行人更名不涉及既有债券名称修改",
      "unit": null,
      "period": "2020",
      "subject_name": "TCL科技集团股份有限公司",
      "subject_identifier": "91441300195971850Y",
      "status": "verified",
      "source_ids": ["S02"],
      "source_location": "p.1",
      "confidence": 1.0,
      "conflict_note": null,
      "as_of_eligible": true
    },
    {
      "claim_id": "C014",
      "category": "relation",
      "field_name": "control_relationship",
      "value": "TCL科技间接控制TCL中环",
      "unit": null,
      "period": "2024",
      "subject_name": "TCL中环新能源科技股份有限公司",
      "subject_identifier": "002129",
      "status": "verified",
      "source_ids": ["S09"],
      "source_location": "历史沿革/控制关系",
      "confidence": 0.99,
      "conflict_note": "控制关系不改变法人独立性",
      "as_of_eligible": true
    },
    {
      "claim_id": "C020",
      "category": "scf",
      "field_name": "annual_factoring_cap",
      "value": 4000000000,
      "unit": "CNY",
      "period": "2023",
      "subject_name": "TCL科技及控股子公司相关保理",
      "subject_identifier": "000100",
      "status": "verified",
      "source_ids": ["S05"],
      "source_location": "p.1",
      "confidence": 0.99,
      "conflict_note": "额度不等于实际期末余额",
      "as_of_eligible": true
    },
    {
      "claim_id": "C021",
      "category": "scf",
      "field_name": "supplier_receivable_transfer_structure",
      "value": "供应商或取得相关应收权益的企业可以将其对TCL科技或其控股子公司的应收账款权益转让给关联保理公司",
      "unit": null,
      "period": "2023",
      "subject_name": "相关供应商/债务人",
      "subject_identifier": null,
      "status": "verified",
      "source_ids": ["S05"],
      "source_location": "p.1",
      "confidence": 0.99,
      "conflict_note": "实际债务人必须逐笔识别",
      "as_of_eligible": true
    },
    {
      "claim_id": "C039",
      "category": "scf",
      "field_name": "derecognized_ar_via_discount_factoring",
      "value": 7223995,
      "unit": "CNY_thousand",
      "period": "2023",
      "subject_name": "TCL科技集团股份有限公司合并口径",
      "subject_identifier": "000100",
      "status": "verified",
      "source_ids": ["S04"],
      "source_location": "应收账款终止确认附注",
      "confidence": 0.99,
      "conflict_note": null,
      "as_of_eligible": true
    },
    {
      "claim_id": "C044",
      "category": "financial",
      "field_name": "net_profit_attributable_parent",
      "value": 1525319763,
      "unit": "CNY",
      "period": "2024-01-01/2024-09-30",
      "subject_name": "TCL科技集团股份有限公司合并口径",
      "subject_identifier": "000100",
      "status": "verified",
      "source_ids": ["S08"],
      "source_location": "主要财务数据/合并利润表",
      "confidence": 1.0,
      "conflict_note": "与合并净利润统计口径不同",
      "as_of_eligible": true
    },
    {
      "claim_id": "C051",
      "category": "subsidiary_financial",
      "field_name": "net_profit",
      "value": -6478000000,
      "unit": "CNY_approx",
      "period": "2024-01-01/2024-09-30",
      "subject_name": "TCL中环新能源科技股份有限公司",
      "subject_identifier": "002129",
      "status": "verified",
      "source_ids": ["S08"],
      "source_location": "经营情况章节",
      "confidence": 0.98,
      "conflict_note": "属于TCL中环，不自动归属母公司本体",
      "as_of_eligible": true
    },
    {
      "claim_id": "C054",
      "category": "basic",
      "field_name": "registration_status",
      "value": null,
      "unit": null,
      "period": "2024-12-31",
      "subject_name": "TCL科技集团股份有限公司",
      "subject_identifier": "91441300195971850Y",
      "status": "unverified",
      "source_ids": ["S01"],
      "source_location": "章程仅记载永久存续",
      "confidence": 0.35,
      "conflict_note": "未取得市场监管实时登记状态快照",
      "as_of_eligible": true
    }
  ],
  "checklist": [
    {
      "item": "主体名称、USCC和上市代码一致性",
      "status": "verified",
      "conclusion": "可以建立同一上市法人映射",
      "evidence_ids": ["S01", "S12"],
      "conflict": null,
      "missing_material": ["交易时最新工商信息"],
      "manual_review": true,
      "hard_gate": true
    },
    {
      "item": "曾用名连续性",
      "status": "verified",
      "conclusion": "历史TCL集团名称与当前TCL科技属于连续更名",
      "evidence_ids": ["S01"],
      "conflict": null,
      "missing_material": [],
      "manual_review": false,
      "hard_gate": false
    },
    {
      "item": "历史债券旧称",
      "status": "verified",
      "conclusion": "发行人更名后既有债券名称可继续保留旧称",
      "evidence_ids": ["S02", "S03"],
      "conflict": null,
      "missing_material": [],
      "manual_review": false,
      "hard_gate": false
    },
    {
      "item": "TCL中环法人独立性",
      "status": "verified",
      "conclusion": "002129为独立上市法人，不能替换为000100",
      "evidence_ids": ["S09"],
      "conflict": null,
      "missing_material": ["具体交易时工商和合同主体"],
      "manual_review": true,
      "hard_gate": true
    },
    {
      "item": "TCL华星法人独立性",
      "status": "verified",
      "conclusion": "截止日前公告将TCL华星作为独立公司主体列示",
      "evidence_ids": ["S07", "S08"],
      "conflict": null,
      "missing_material": ["截止日前交易级完整USCC证明"],
      "manual_review": true,
      "hard_gate": true
    },
    {
      "item": "应收账款保理和转让",
      "status": "verified",
      "conclusion": "公开披露存在保理及供应商应收权益转让机制",
      "evidence_ids": ["S04", "S05"],
      "conflict": null,
      "missing_material": ["本笔债权保理、质押、转让和登记状态"],
      "manual_review": true,
      "hard_gate": true
    },
    {
      "item": "合同、发票、物流、验收、确权",
      "status": "unverified",
      "conclusion": "公开资料无法验证具体基础交易",
      "evidence_ids": [],
      "conflict": null,
      "missing_material": [
        "合同",
        "发票",
        "订单",
        "物流及签收",
        "验收",
        "对账及确权"
      ],
      "manual_review": true,
      "hard_gate": true
    },
    {
      "item": "母集团付款保证",
      "status": "unverified",
      "conclusion": "未发现本案具体保证文件，不能以集团控制关系替代保证",
      "evidence_ids": [],
      "conflict": null,
      "missing_material": [
        "保证合同",
        "公司授权",
        "保证范围、金额及期限"
      ],
      "manual_review": true,
      "hard_gate": true
    },
    {
      "item": "司法执行及失信完整情况",
      "status": "unverified",
      "conclusion": "本证据包未形成完整法定查询快照，不作无风险结论",
      "evidence_ids": [],
      "conflict": null,
      "missing_material": ["官方法院及执行信息查询结果"],
      "manual_review": true,
      "hard_gate": false
    },
    {
      "item": "银行流水、税务及企业征信",
      "status": "unverified",
      "conclusion": "属于未取得的私域资料",
      "evidence_ids": [],
      "conflict": null,
      "missing_material": ["授权后的私域数据"],
      "manual_review": true,
      "hard_gate": false
    }
  ],
  "reference_assessment": {
    "risk_level": "高",
    "risk_scope": "实体解析、应收账款可融资性及错误使用母集团信用的交易风险，不等同于TCL科技主体信用评级",
    "hard_gate_triggers": [
      "合同或发票债务人无法与完整法人名称及USCC唯一匹配",
      "将TCL华星或TCL中环债务自动归入TCL科技",
      "依赖母集团保证但无有效保证合同及授权",
      "无法排除应收账款已保理、转让、质押或重复融资",
      "基础交易证据链无法闭环",
      "历史名称仅按字符串匹配而未核验法人连续性"
    ],
    "key_risk_factors": [
      "历史名称连续变化与债券旧称并存",
      "集团内存在多个独立TCL法人",
      "TCL中环2024年前三季度经营结果与母集团合并口径明显分化",
      "公开披露存在较大规模应收账款保理及终止确认",
      "存在供应商对公司或控股子公司的应收权益转让机制",
      "关联交易主体及交易规模较复杂",
      "缺少交易级原始凭证"
    ],
    "mitigating_factors": [
      "历史名称连续性有法定披露证据",
      "2023年度取得标准公允反映的审计意见",
      "2021至2023年度经营现金流均为正",
      "2024年前三季度合并经营现金流为正",
      "主要集团法人可通过证券代码、USCC和控制关系进行区分"
    ],
    "missing_critical_evidence": [
      "合同及实际债务人USCC",
      "发票",
      "订单、物流、签收和验收",
      "应收账款确权",
      "保理、转让、质押及供应链平台状态",
      "动产融资登记查询",
      "母集团保证合同及授权",
      "历史回款",
      "银行流水",
      "纳税资料",
      "企业征信",
      "完整司法执行和监管查询快照"
    ],
    "recommended_action": "暂缓",
    "credit_amount": null,
    "confidence": 0.89,
    "reasoning_claim_ids": [
      "C007",
      "C008",
      "C009",
      "C014",
      "C015",
      "C020",
      "C021",
      "C039",
      "C044",
      "C050",
      "C051"
    ]
  },
  "post_cutoff_outcomes": [
    {
      "claim_id": "P001",
      "field_name": "2024_full_year_revenue",
      "subject_name": "TCL科技集团股份有限公司",
      "value": 164822832863,
      "unit": "CNY",
      "status": "verified",
      "source_ids": ["P01"],
      "publication_date": "2025-05-20",
      "as_of_eligible": false
    },
    {
      "claim_id": "P002",
      "field_name": "2024_full_year_net_profit_attributable_parent",
      "subject_name": "TCL科技集团股份有限公司",
      "value": 1564109407,
      "unit": "CNY",
      "status": "verified",
      "source_ids": ["P01"],
      "publication_date": "2025-05-20",
      "as_of_eligible": false
    },
    {
      "claim_id": "P003",
      "field_name": "2024_full_year_operating_cash_flow",
      "subject_name": "TCL科技集团股份有限公司",
      "value": 29526569404,
      "unit": "CNY",
      "status": "verified",
      "source_ids": ["P01"],
      "publication_date": "2025-05-20",
      "as_of_eligible": false
    },
    {
      "claim_id": "P004",
      "field_name": "2024_full_year_net_profit_attributable_shareholders",
      "subject_name": "TCL中环新能源科技股份有限公司",
      "value": -9820000000,
      "unit": "CNY_approx",
      "status": "verified",
      "source_ids": ["P01"],
      "publication_date": "2025-05-20",
      "as_of_eligible": false
    },
    {
      "claim_id": "P005",
      "field_name": "unified_social_credit_code",
      "subject_name": "TCL华星光电技术有限公司",
      "value": "91440300697136927G",
      "unit": null,
      "status": "verified",
      "source_ids": ["S10"],
      "publication_date": "2025-08-28",
      "as_of_eligible": false
    }
  ],
  "limitations": [
    "未取得截至2024-12-31的市场监管实时登记状态快照",
    "未形成法院执行、失信、限制消费、终本案件的完整官方查询快照，因此未作无记录结论",
    "未形成行政处罚、经营异常和全部监管纪律处分的完整官方查询快照",
    "未获得合同、发票、订单、物流、验收、确权及历史回款等交易私域资料",
    "未获得银行流水、税务数据及企业征信",
    "未验证本案具体应收账款是否已保理、质押、转让或重复融资",
    "未验证任何具体母集团保证文件",
    "2024年度全年年报于截止日之后公开，只作为后验结果",
    "TCL华星USCC的政府交叉验证来源于截止日后公开材料，因此未用于截止日风险判断",
    "所有授信判断仅供离线研究和Agent评测，不构成真实金融审批"
  ],
  "self_check": {
    "major_conclusion_without_source": false,
    "related_party_risk_misattributed_to_subject": false,
    "post_cutoff_information_used_in_as_of_assessment": false,
    "no_record_converted_to_no_risk": false,
    "unvisited_or_nonexistent_link_cited": false,
    "inference_presented_as_verified_fact": false
  }
}
```

---

## 八、自检

- 重大结论是否均有来源：是。
- 是否把关联方风险错误归属给主体：否。
- 是否使用研究截止日之后的信息参与当时风险判断：否；S10、P01仅列入后验结果。
- 是否把“未发现记录”写成“没有风险”：否。
- 是否把无法验证的合同、发票、确权、保证、司法全量查询写成已验证事实：否。
- 是否把推断写成事实：否；主体信用与交易风险、集团信用与子公司债务人信用均已分开。
- 是否存在实体边界混淆：已重点修正。旧“TCL集团”与现“TCL科技”按同一法人连续性处理；TCL华星、TCL中环、财务公司、关联保理公司按独立法人处理。

