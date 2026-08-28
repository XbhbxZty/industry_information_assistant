# case_12：江苏中利集团股份有限公司历史授信尽调案例包

> 研究截止日：2024-05-31。以下风险判断只使用截至该日已经公开的信息。2024-05-31 之后的信息单列为 `post_cutoff_outcomes`，不参与历史授信判断。  
> 案例设定的资料检索日期为 2026-08-14；关键原始链接于 2026-08-16 再次进行了可访问性复核。

## 输出一：案例摘要

| 字段 | 内容 |
|---|---|
| case_id | `case_12` |
| 企业主体 | 江苏中利集团股份有限公司 |
| 统一社会信用代码 | `913205007317618904` |
| 上市代码 | 深交所 `002309` |
| 曾用名 | 中利科技集团股份有限公司 |
| 业务场景 | 电缆、光伏供应链供应商授信 |
| 研究截止日 | 2024-05-31 |
| 案例难度 | 高难 |
| 主要风险 | 持续经营重大不确定性；负净资产；大额逾期短期借款；银行账户冻结及流动性紧张；债权人申请重整且仅处于预重整；历史关联方非经营性资金占用与违规担保；内部控制审计否定意见；证监会行政处罚事先告知程序；较大担保敞口；应收账款长账龄及高坏账准备 |
| 关键缺失信息 | 最新工商登记状态的独立工商公示复核；全国法院被执行/失信/限消完整结果；具体供应链合同、发票、物流、确权、回款；银行流水、企业征信、纳税；应收账款是否已质押/转让/保理以及重复融资情况；主要客户、供应商实名 |
| 是否足以形成参考结论 | **是，可以形成“高风险、暂缓新增授信并人工复核”的研究参考判断；不足以形成真实授信审批或额度。** |

本案例最重要的标签不能写成“2023 年保留意见”。2023 年财务报表审计的正式意见是**无保留意见**；审计报告另外设置“与持续经营相关的重大不确定性”段和“强调事项”段。审计报告分别明确说明这些段落“不影响已经发表的审计意见”。与此同时，2023 年内部控制审计为**否定意见**；2022 年财务报表审计则确实是**保留意见**。这三个字段必须分开建模。

审计准则逻辑在该报告中也直接体现：当持续经营存在重大不确定性、但相关披露充分时，可以在无保留意见之外增加持续经营重大不确定性段；若相关披露不充分，才应发表非无保留意见。因此，“无保留意见”与“持续经营重大不确定性”并不矛盾。

---

## 输出二：来源目录

来源等级定义：`L1`＝政府/法院/监管机关；`L2`＝巨潮资讯等法定披露平台原始文件；`L3`＝公司公告或年报原件的公开镜像。

| source_id | 来源标题 | 发布机构 | 等级 | 发布日期 | 事件/基准日期 | 直接URL | 格式 | 页码/章节 | 访问状态 | 可下载 | 支持项目 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S001 | 江苏中利集团股份有限公司2023年年度报告全文 | 江苏中利集团/巨潮资讯 | L2 | 2024-04-24 | 2023-12-31/2023年度 | https://static.cninfo.com.cn/finalpage/2024-04-24/1219775289.PDF | PDF | p.2、8-10、23-24、64-85、91-108、129、160-161、186-188、235-240 | 成功 | 是 | A/B/C/D/E |
| S002 | 董事会审计委员会对会计师事务所2023年度履职情况评估及履行监督职责情况的报告 | 江苏中利集团/巨潮资讯 | L2 | 2024-04-24 | 2023年度审计 | https://static.cninfo.com.cn/finalpage/2024-04-24/1219775253.PDF | PDF | p.2 | 成功 | 是 | B/C |
| S003 | 江苏中利集团股份有限公司2022年年度报告摘要 | 江苏中利集团；新浪财经镜像 | L3 | 2023-04-22 | 2022年度 | https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2023/2023-4/2023-04-22/9047515.PDF | PDF | p.1 | 成功 | 是 | A/B |
| S004 | 江苏中利集团股份有限公司2024年第一季度报告 | 江苏中利集团；新浪财经镜像 | L3 | 2024-04-30 | 2024-03-31/2024Q1 | https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2024/2024-4/2024-04-30/10155807.PDF | PDF | p.1-4 | 成功 | 是 | B/E |
| S005 | 江苏证监局关于对江苏中利集团股份有限公司、江苏中利控股集团有限公司、王柏兴采取责令改正措施的决定〔2024〕88号 | 江苏证监局 | L1 | 2024-05-09 | 2018-2023；余额截至2023-12-31 | https://www.csrc.gov.cn/jiangsu/c103893/c7479954/content.shtml | HTML | 正文 | 成功 | 网页 | A/C |
| S006 | 关于收到中国证券监督管理委员会《行政处罚及市场禁入事先告知书》的公告（2024-050） | 江苏中利集团；新浪财经镜像 | L3 | 2024-05-14 | 事先告知书收到日2024-05-13 | https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2024/2024-5/2024-05-14/10204667.PDF | PDF | p.1-8 | 成功 | 是 | C |
| S007 | 关于被债权人申请重整及预重整的进展公告（2024-046） | 江苏中利集团；新浪公告镜像 | L3 | 2024-04-30 | 公告签署2024-04-29 | https://gu.sina.cn/bd/hq/notice.php?annid=20197876&source=bdquote | PDF | p.1-4 | 成功 | 是 | C/E |
| S008 | 江苏中利集团股份有限公司章程（2018年12月） | 江苏中利集团/巨潮资讯 | L2 | 2018-12-11 | 章程当期 | https://static.cninfo.com.cn/finalpage/2018-12-11/1205662102.PDF | PDF | p.4-5 | 成功 | 是 | A |
| S009 | 关于募集资金2017年半年度存放与使用情况的专项报告 | 江苏中利集团/巨潮资讯 | L2 | 2017-08-25 | 2017H1 | https://static.cninfo.com.cn/finalpage/2017-08-25/1203863524.PDF | PDF | p.1 | 成功 | 是 | A |
| S010 | 关于子公司涉及仲裁的公告（2024-057） | 江苏中利集团；腾讯财经镜像 | L3 | 2024-05-31 | 公告签署2024-05-30 | https://file.finance.qq.com/finance/hs/pdf/2024/05/31/1220211159.PDF | PDF | p.1-2 | 成功 | 是 | C |
| S011 | 国家企业信用信息公示系统 | 国家市场监督管理总局 | L1 | — | 查询日 | https://www.gsxt.gov.cn/index.html | HTML | — | **失败：403 Forbidden** | 否 | A |
| S012 | 中国执行信息公开网 | 最高人民法院 | L1 | — | 查询日 | https://zxgk.court.gov.cn/ | HTML | — | **失败：403 Forbidden** | 否 | C |
| S013 | 全国企业破产重整案件信息网 | 最高人民法院 | L1 | — | 查询日 | https://pccz.court.gov.cn/ | HTML | 首页/公开案件检索入口 | 首页成功；未完成主体全量结果查询 | 否 | C |
| S014 | 关于重整进展暨在继续推动重整程序中完成资金占用整改及风险提示的公告（2024-125） | 江苏中利集团/巨潮资讯 | L2 | 2024-11-23 | 2024-11-08以后 | https://static.cninfo.com.cn/finalpage/2024-11-23/1221815778.PDF | PDF | p.1-3 | 成功 | 是 | 后验 |
| S015 | 关于股东权益变动暨控股股东和实际控制人拟发生变更的提示性公告（2024-137） | 江苏中利集团/巨潮资讯 | L2 | 2024-12-18 | 2024-12-11以后 | https://static.cninfo.com.cn/finalpage/2024-12-18/1222049190.PDF | PDF | p.1-3 | 成功 | 是 | 后验 |

S001 对企业名称、股票代码、法定代表人及信用代码进行了直接披露；章程进一步确认股份公司于 2007-07-26 发起设立、2007-08-06 登记，信用代码为 `913205007317618904`、注册资本为 87,178.7068 万元。

S009 明确记录“中利科技集团股份有限公司（现已更名为江苏中利集团股份有限公司）”，因此曾用名能够由原始披露交叉验证。

S011、S012 的失败不能转换成“未发现工商异常”“未发现执行案件”。本轮访问分别得到 403，因此对应字段只能标记为 `unverified`。

---

## 输出三：原子事实表

金额均保持原披露口径；`confidence` 取 0-1。

| claim_id | category | field_name | value | unit | period | subject_name | identifier | status | source_ids | source_location | confidence | conflict_note | as_of_eligible |
|---|---|---|---:|---|---|---|---|---|---|---|---:|---|---|
| C001 | basic | legal_name | 江苏中利集团股份有限公司 | — | 截止日 | 江苏中利集团股份有限公司 | 913205007317618904 | verified | S001,S008 | S001 p8；S008 p4 | 1.00 | — | true |
| C002 | basic | former_name | 中利科技集团股份有限公司 | — | 2017年前 | 同上 | 同上 | verified | S009 | p1 | 1.00 | — | true |
| C003 | basic | unified_social_credit_code | 913205007317618904 | — | 截止日 | 同上 | 同上 | verified | S001,S008 | S001 p8/p130；S008 p4 | 1.00 | — | true |
| C004 | basic | stock_code | 002309 | — | 截止日 | 同上 | 002309 | verified | S001 | p8 | 1.00 | — | true |
| C005 | basic | listing_exchange | 深圳证券交易所 | — | 截止日 | 同上 | 002309 | verified | S001,S008 | S001 p8；S008 p4 | 1.00 | — | true |
| C006 | basic | joint_stock_company_establishment_date | 2007-07-26 | date | — | 同上 | 同上 | verified | S008 | p4 第2条 | 0.99 | 工商公示独立复核因S011失败未完成 | true |
| C007 | basic | registration_date | 2007-08-06 | date | — | 同上 | 同上 | verified | S008 | p4 第2条 | 0.99 | 同上 | true |
| C008 | basic | registered_capital | 871787068 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001,S008 | S001 p130；S008 p4 | 1.00 | — | true |
| C009 | basic | legal_representative | 王伟峰 | — | 2023年报时点 | 同上 | 同上 | verified | S001 | p8/p130 | 1.00 | — | true |
| C010 | operation | principal_business | 光伏业务、特种线缆业务 | — | 2023 | 同上 | 同上 | verified | S001,S003 | 年报管理层讨论；S003 p1-2 | 0.99 | — | true |
| C011 | equity | actual_controller | 王柏兴 | — | 2023-12-31 | 同上 | 同上 | verified | S001 | p236 | 0.99 | 江苏证监局亦认定王柏兴为实际控制人 | true |
| C012 | equity | controlling_shareholder_label | 年报/审计文本称王柏兴；江苏证监局〔2024〕88号称江苏中利控股集团有限公司 | — | 截止日 | 同上 | 同上 | conflicting | S001,S005 | S001 p100/p236；S005正文 | 1.00 | **主体关系标签冲突，应原样保留，不能自行消除** | true |
| C013 | equity | parent_company | 无母公司 | — | 2023-12-31 | 同上 | 同上 | verified | S001 | p236 | 0.99 | 不等于“无控股股东” | true |
| C014 | financial | revenue | 4051283736.03 | CNY | 2023 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C015 | financial | revenue | 8165891813.89 | CNY | 2022 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C016 | financial | revenue | 10381623530.23 | CNY | 2021 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C017 | financial | attributable_net_profit | -1496533239.05 | CNY | 2023 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C018 | financial | attributable_net_profit_adjusted | -475578383.85 | CNY | 2022 | 同上 | 同上 | verified | S001 | p9 | 1.00 | 调整前为-485005704.37 | true |
| C019 | financial | attributable_net_profit | -4188446098.15 | CNY | 2021 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C020 | financial | operating_cash_flow | 289661826.87 | CNY | 2023 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C021 | financial | operating_cash_flow | 387179858.18 | CNY | 2022 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C022 | financial | operating_cash_flow | 1094079372.44 | CNY | 2021 | 同上 | 同上 | verified | S001 | p9 | 1.00 | — | true |
| C023 | financial | total_assets | 7712405206.77 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p108 | 1.00 | — | true |
| C024 | financial | total_liabilities | 8276843748.05 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p109 | 1.00 | — | true |
| C025 | financial | attributable_equity | -558303262.46 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p109 | 1.00 | — | true |
| C026 | financial | accounts_receivable_net | 944281300.16 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p107 | 1.00 | — | true |
| C027 | financial | accounts_receivable_gross | 1965181412.99 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p161 | 1.00 | — | true |
| C028 | financial | accounts_receivable_aging_over_3y | 1024153384.82 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p161 | 1.00 | 占应收账款账面余额约52.1%，为计算值 | true |
| C029 | financial | inventory | 883888101.21 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p107 | 1.00 | — | true |
| C030 | financial | short_term_borrowings | 2910605047.91 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p108/p187 | 1.00 | — | true |
| C031 | financial | overdue_short_term_borrowings | 2611107668.96 | CNY | 2023-12-31 | 同上 | 同上 | verified | S001 | p187 | 1.00 | 审计报告取整为261110.77万元 | true |
| C032 | relation | disclosed_guarantee_balance | 2130855200 | CNY | 2023-12-31 | 江苏中利集团合并口径 | 002309 | verified | S001 | p84-85 | 0.99 | **为披露的公司担保总额口径，不能自动等同于上市母公司全部直接债务** | true |
| C033 | audit | financial_statement_audit_opinion | 无保留意见，附持续经营重大不确定性段与强调事项段 | — | 2023 | 同上 | 同上 | verified | S001,S002 | S001 p99-101；S002 p2 | 1.00 | 不是保留意见 | true |
| C034 | audit | going_concern_material_uncertainty | 存在 | — | 2023审计报告 | 同上 | 同上 | verified | S001 | p100 | 1.00 | 审计师明确称“不影响已经发表的审计意见” | true |
| C035 | relation | non_operating_fund_occupation_excl_guarantee | 1692979200 | CNY | 2023-12-31 | 江苏中利集团 | 002309 | verified | S001 | p100 | 1.00 | 实控人及关联方占用 | true |
| C036 | relation | provision_for_irregular_related_guarantees | 112212700 | CNY | 2023-12-31 | 江苏中利集团 | 002309 | verified | S001 | p100 | 1.00 | 不应与全部正常担保余额混淆 | true |
| C037 | audit | internal_control_audit_opinion | 否定意见 | — | 2023 | 同上 | 同上 | verified | S001,S002 | S001重要提示；S002 p2 | 1.00 | 与财务报表审计是不同审计业务 | true |
| C038 | audit | prior_year_fs_audit_opinion | 保留意见 | — | 2022 | 同上 | 同上 | verified | S003,S002 | S003 p1；S002 p2 | 1.00 | 2023已转为无保留意见 | true |
| C039 | judicial | restructuring_application | 债权人江苏欣意装饰工程有限公司申请对公司重整及预重整 | — | 2023-01-18 | 同上 | 同上 | verified | S001,S007 | S007 p1 | 1.00 | 申请理由披露为不能清偿到期债务且明显缺乏清偿能力但具有重整价值 | true |
| C040 | judicial | restructuring_status_at_cutoff | 处于预重整，上市主体尚未进入正式重整 | — | 2024-04-29至截止日 | 同上 | 同上 | verified | S007 | p3-4 | 0.99 | 四家子公司已进入正式重整，**不能归属为上市主体已正式重整** | true |
| C041 | regulation | rectification_order_fund_occupation | 18.05亿元，并要求收到决定书后六个月内归还 | CNY | 2024-05-09 | 同上及相关方 | 同上 | verified | S005 | 正文 | 1.00 | 监管口径16.93亿元占用+1.12亿元违规担保 | true |
| C042 | regulation | csrc_prior_notice_stage | 已收到《行政处罚及市场禁入事先告知书》，处于拟处罚程序 | — | 2024-05-13 | 同上 | 同上 | verified | S006 | p1 | 1.00 | **截止日不是最终行政处罚决定** | true |
| C043 | regulation | alleged_historical_false_disclosure | 事先告知书拟认定专网通信业务存在虚增营业收入、利润总额等信息披露违法事实 | — | 涉2016-2020等历史年度 | 同上 | 同上 | verified | S006 | p1起 | 0.98 | verified的是“事先告知书载明拟认定该事实”；不得在截止日表述成最终生效处罚认定 | true |
| C044 | financial | q1_revenue | 840308434.86 | CNY | 2024Q1 | 同上 | 同上 | verified | S004 | p2 | 1.00 | 未经审计 | true |
| C045 | financial | q1_attributable_net_profit | -186871532.37 | CNY | 2024Q1 | 同上 | 同上 | verified | S004 | p2 | 1.00 | 未经审计 | true |
| C046 | financial | q1_operating_cash_flow | -45994366.29 | CNY | 2024Q1 | 同上 | 同上 | verified | S004 | p2 | 1.00 | 未经审计 | true |
| C047 | financial | q1_attributable_equity | -717656631.62 | CNY | 2024-03-31 | 同上 | 同上 | verified | S004 | p2 | 1.00 | 未经审计 | true |
| C048 | operation | top5_customer_concentration | 24.47 | % | 2023 | 同上 | 同上 | verified | S001 | p24 | 1.00 | 客户实名未披露 | true |
| C049 | operation | top5_supplier_concentration | 46.28 | % | 2023 | 同上 | 同上 | verified | S001 | p24 | 1.00 | 第一大供应商22.99%；实名未披露 | true |
| C050 | judicial | subsidiary_arbitration | 苏州腾晖光伏被申请仲裁，主张金额12446857.64元 | CNY | 2024-05-30/31 | 苏州腾晖光伏技术有限公司 | — | verified | S010 | p1 | 1.00 | **被申请人为全资子公司，不得直接写成上市母公司被申请仲裁** | true |
| C051 | basic | registry_operating_status | 无法由国家企业信用信息公示系统独立复核 | — | 查询时点 | 江苏中利集团 | 913205007317618904 | unverified | S011 | 403 | 1.00 | 不是no_record_found | true |
| C052 | judicial | enforcement_default_limit_consumption | 官方执行公开网本轮无法完成查询 | — | 截止日核查 | 江苏中利集团 | 同上 | unverified | S012 | 403 | 1.00 | 不得推断“无被执行/失信/限消” | true |
| C053 | supply_chain | ar_factoring_pledge_duplicate_financing | 未获得足够公开证据确认具体应收账款是否已转让、质押、保理或重复融资 | — | 截止日 | 同上 | 同上 | unverified | S001 | 公开财报范围 | 0.95 | 年报未形成交易级完整证明 | true |
| C054 | supply_chain | contract_invoice_logistics_confirmation_payment | 未取得具体交易私域材料 | — | 截止日 | 具体供应链交易 | — | unverified | — | — | 1.00 | 公开资料无法完成交易真实性闭环 | true |
| C055 | post_cutoff | formal_restructuring_acceptance | 苏州中院于2024-11-08裁定受理上市主体重整 | — | 2024-11-08 | 江苏中利集团 | 002309 | verified | S014,S015 | S014 p1；S015 p1 | 1.00 | 后验信息 | **false** |
| C056 | post_cutoff | restructuring_plan_approval | 法院于2024-12-11批准重整计划并终止重整程序 | — | 2024-12-11 | 江苏中利集团 | 002309 | verified | S015 | p1 | 1.00 | 后验信息 | **false** |

2023 年收入从 2022 年的 81.66 亿元下降至 40.51 亿元，归母净亏损扩大至约 14.97 亿元；2023 年末归母净资产为 -5.58 亿元。

2023 年末总资产约 77.12 亿元、总负债约 82.77 亿元，按两项披露值计算，负债/资产约 **107.32%**；短期借款约 29.11 亿元，其中已逾期未偿还短期借款约 26.11 亿元。

应收账款账面余额约 19.65 亿元，其中三年以上约 10.24 亿元，约占账面余额 52.1%；资产负债表净额仅约 9.44 亿元，反映坏账准备规模较高。

公司披露 2023 年末实际担保余额合计约 21.31 亿元，但这是年度报告的公司担保汇总口径，应保持母公司、对子公司担保及其他担保的边界，不能把整个数额直接解释成某一单笔融资的直接债务。

---

## 输出四：尽调检查清单

“硬性风险门槛”仅为该评测案例的参考规则，不代表任何真实银行、保理公司或其他金融机构内部授信政策。

| 检查项目 | 状态 | 结论 | 证据ID | 冲突 | 缺失材料 | 人工复核 | 硬性风险门槛 |
|---|---|---|---|---|---|---|---|
| 企业名称/信用代码/上市代码 | verified | 三者可匹配至同一上市主体 | C001-C005 | 无 | 工商实时档案 | 否 | 否 |
| 曾用名 | verified | 原名中利科技集团股份有限公司 | C002 | 无 | — | 否 | 否 |
| 成立/注册资本 | verified | 章程可验证 | C006-C008 | 工商公示未独立复核 | 最新营业执照/GSXT | 是 | 否 |
| 登记状态/经营异常 | unverified | GSXT访问失败，不能写“正常且无异常” | C051 | 无 | GSXT正式查询结果 | 是 | 否 |
| 法定代表人 | verified | 王伟峰 | C009 | 无 | — | 否 | 否 |
| 控股股东/实际控制人 | conflicting | 实控人王柏兴明确；“控股股东”标签在年报与江苏证监局文件间存在冲突 | C011-C013 | **有** | 历史股权和监管口径解释 | **是** | 否 |
| 母子公司边界 | verified | 上市公司称无母公司；腾晖等为子公司 | C013,C050 | 无 | 最新合并范围 | 是 | 否 |
| 最近三年收入 | verified | 103.82亿→81.66亿→40.51亿，明显下降 | C014-C016 | 无 | — | 否 | 是 |
| 最近三年利润 | verified | 三年连续归母亏损，2023亏损约14.97亿元 | C017-C019 | 无 | — | 否 | **是** |
| 最近三年经营现金流 | verified | 仍有正流入但逐年下降；2024Q1转负 | C020-C022,C046 | 无 | 月度现金流 | 是 | 是 |
| 资产负债情况 | verified | 2023年末负债大于资产，归母净资产为负 | C023-C025 | 无 | 最新时点财务报表 | 是 | **是** |
| 短期借款/逾期 | verified | 短借29.11亿元，其中约26.11亿元已逾期 | C030-C031 | 无 | 银行借款清单、展期协议 | **是** | **是** |
| 应收账款余额及账龄 | verified | 长账龄显著，三年以上超过账面余额一半 | C026-C028 | 无 | 客户明细、回款流水、函证 | **是** | 是 |
| 存货 | verified | 2023年末8.84亿元 | C029 | 无 | 库龄、盘点资料 | 是 | 否 |
| 对外/集团担保 | verified | 披露实际担保余额21.31亿元 | C032 | 口径需拆分 | 全量担保合同及被担保债务状态 | **是** | 是 |
| 非经营性资金占用 | verified | 截至2023年末审计口径16.93亿元左右，不含违规担保 | C035,C041 | 金额单位存在四舍五入 | 后续清偿证明 | **是** | **是** |
| 违规担保 | verified | 审计强调事项对应预计负债1.122亿元左右 | C036,C041 | 无实质金额冲突 | 原担保合同、司法状态 | **是** | **是** |
| 2023财报审计意见 | verified | **无保留意见+持续经营重大不确定性段+强调事项段** | C033-C034 | 无 | — | 是 | **是（因持续经营事项，而非因“保留意见”）** |
| 2023内部控制审计 | verified | 否定意见 | C037 | 无 | 整改闭环材料 | **是** | **是** |
| 2022财报审计意见 | verified | 保留意见 | C038 | 无 | 保留事项消除专项说明完整底稿 | 是 | 是 |
| 债权人重整申请 | verified | 2023-01-18已申请 | C039 | 无 | 法院完整卷宗 | **是** | **是** |
| 截止日重整阶段 | verified | **仅预重整；上市主体未正式进入重整程序** | C040 | 无 | 截止日后续法院裁定不得前置使用 | **是** | **是** |
| 证监局责令改正 | verified | 18.05亿元占用资金须限期整改 | C041 | 控股股东称谓有冲突 | 整改进度 | **是** | **是** |
| 证监会调查/处罚程序 | verified | 截止日仅到《事先告知书》阶段 | C042-C043 | 无 | 最终处罚决定当时尚不可得 | 是 | **是** |
| 重大诉讼仲裁 | verified/partial | 年报列示大量诉讼仲裁；截止日另有腾晖子公司仲裁 | S001,C050 | 主体边界需拆分 | 法院/仲裁机构全量案号 | **是** | 是 |
| 被执行/失信/限消/终本 | unverified | 官方网站访问受阻 | C052 | 无 | 法院官方完整检索 | **是** | 否，因证据不足不能触发 |
| 客户集中度 | verified | 前五客户24.47% | C048 | 无 | 客户实名及回款质量 | 是 | 否 |
| 供应商集中度 | verified | 前五供应商46.28%，第一名22.99% | C049 | 无 | 供应商实名 | 是 | 是 |
| 应收账款质押/转让/保理 | unverified | 无法形成交易级结论 | C053 | 无 | 动产融资登记、保理通知、质押合同 | **是** | 否 |
| 重复融资 | unverified | 无公开资料足以排除 | C053 | 无 | 全量融资登记与应收账款台账 | **是** | 否 |
| 合同/发票/物流/确权 | unverified | 公开信息不能验证具体拟融资交易 | C054 | 无 | 原件及交易对手确权 | **是** | 否 |
| 银行流水/企业征信/纳税 | unverified | 非公开资料缺失 | — | 无 | 私域材料 | **是** | 否 |

---

## 输出五：离线评测参考报告

### 1. 经证据验证的事实

江苏中利集团股份有限公司统一社会信用代码为 `913205007317618904`，股票代码 `002309`，在深圳证券交易所上市；历史名称为“中利科技集团股份有限公司”。公司主营业务包括特种线缆及光伏业务。[S001][S008][S009]

2021、2022、2023 年营业收入分别约为 103.82 亿元、81.66 亿元和 40.51 亿元；同期归母净利润分别约为 -41.88 亿元、调整后 -4.76 亿元和 -14.97 亿元。2023 年经营现金流净额仍为正的约 2.90 亿元，但明显低于 2021 年约 10.94 亿元。[S001]

截至 2023-12-31，公司总资产约 77.12 亿元，总负债约 82.77 亿元，归属于母公司的所有者权益为 -5.58 亿元；短期借款约 29.11 亿元，其中约 26.11 亿元处于逾期未偿还状态。[S001]

审计报告进一步披露，公司流动负债高于流动资产约 30.14 亿元，部分银行账户被司法冻结，可用资金约 3.24 亿元，融资能力下降，流动性风险较高。审计师据此认定存在可能导致对公司持续经营能力产生重大疑虑的重大不确定性。[S001]

2023 年财务报表正式审计意见为**无保留意见**，同时包含“与持续经营相关的重大不确定性”段以及“强调事项”段。[S001][S002] 审计报告对持续经营事项写明其“不影响已经发表的审计意见”；强调事项段同样明确“不影响已发表的审计意见”。[S001]

这一结构不能识别成保留意见。报告说明，如果持续经营重大不确定性的相关披露不充分，审计师才应发表“非无保留意见”；因此本案应建模为：

```text
2023_FS_opinion = unqualified
going_concern_material_uncertainty = true
emphasis_of_matter = true
```

而不是：

```text
2023_FS_opinion = qualified
```

另一个独立字段是内部控制审计。苏亚金诚认为公司于 2023-12-31 未能在所有重大方面保持有效的财务报告内部控制，因此内部控制审计报告为**否定意见**。[S002]

历史上，2022 年财务报表审计确实为**保留意见**，因此审计意见版本树应保留为：

```text
2022财务报表：保留意见
→ 2023财务报表：无保留意见 + 持续经营重大不确定性段 + 强调事项段
→ 2023内部控制：否定意见
```

[S002][S003]

截至 2023-12-31，审计报告披露实际控制人及其关联方非经营性占用公司资金约 16.93 亿元，不含担保；对相关违规担保计提预计负债约 1.12 亿元，二者合计约 18.05 亿元。[S001]

2024-05-09，江苏证监局正式采取责令改正监管措施，其文件亦载明截至 2023-12-31 非经营性资金占用余额约 18.05 亿元，其中预付供应商货款等形成约 16.93 亿元、违规担保形成约 1.12 亿元，并要求收到决定书之日起六个月内归还。[S005]

需要保留一个实体解析冲突：2023 年报财务附注明确“本公司无母公司，本公司的实际控制人是王柏兴”，审计报告又称王柏兴为“控股股东”；江苏证监局〔2024〕88号却将江苏中利控股集团有限公司称为“控股股东”，将王柏兴称为实际控制人。[S001][S005] 因此，“控股股东是谁”这一标签应为 `conflicting`，而不是由评测 Agent 自行选择一个来源覆盖另一个来源。

2023-01-18，债权人江苏欣意装饰工程有限公司以公司不能清偿到期债务且明显缺乏清偿能力、但具有重整价值为由申请公司重整及预重整。法院随后启动公司预重整程序并指定临时管理人。[S001][S007]

截至 2024-04-29 的最新月度进展，公司重整层报仍在江苏省层面推进，公告明确说明预重整属于正式受理重整之前的程序，并称公司能否正式进入重整仍存在不确定性。因此在 2024-05-31 的历史截面，正确事实是**“上市主体处于预重整”**，而不是“已进入正式重整”。[S007]

2024-05-13，公司收到证监会《行政处罚及市场禁入事先告知书》。该文件属于拟处罚和陈述申辩程序，截止日不能错误记录成已经收到最终行政处罚决定。事先告知书载明拟认定的历史违法事实包括专网通信业务涉嫌虚增营业收入、利润总额等。[S006]

2024 年第一季度未经审计营业收入约 8.40 亿元，归母净亏损约 1.87 亿元，经营现金流净额为 -0.46 亿元，2024-03-31 归母所有者权益进一步下降至约 -7.18 亿元。公司自身解释营业收入下降的重要原因之一是运营资金紧张、产能无法完全释放、经营业务开展受限。[S004]

截至研究截止日当天披露的一项仲裁中，被申请人为**全资子公司苏州腾晖光伏技术有限公司**，主张金额约 1244.69 万元，且当时尚未开庭。该事项不能直接写成“江苏中利集团股份有限公司被申请仲裁1244.69万元”。[S010]

### 2. 从事实推导出的分析

第一，企业在截止日处于显著偿债压力状态。负债已经超过资产，归母净资产为负；短期借款中绝大部分已经逾期，且审计师同时披露银行账户冻结、融资能力下降和较大的流动负债缺口。由 C023-C025、C030-C031、C034 可以支持“偿债能力和持续经营风险高”的分析，但这属于风险推导，不应改写成“企业已经破产”。

第二，2023 年的“无保留意见”不能作为低风险缓释信号单独使用。它只说明审计师认为财务报表在重大方面按准则公允列报，并不意味着企业不存在重大流动性、持续经营、资金占用或内控风险。尤其本报告自身同时出现持续经营重大不确定性段、强调事项段以及另一独立审计业务中的内部控制否定意见。[C033-C037]

第三，2023 年应收账款账面余额约 19.65 亿元，其中三年以上约 10.24 亿元。按披露数计算，三年以上余额约占 52.1%，且应收账款净额仅约 9.44 亿元，说明信用减值已对资产质量产生显著影响。[C026-C028] 对基于应收账款的保理业务，不能仅依据资产负债表“应收账款9.44亿元”判断可融资基础充足。

第四，前五大客户仅占销售额约 24.47%，单纯从该指标看不存在极端客户集中；但前五大供应商采购占比达到 46.28%，其中第一大供应商约占 22.99%。由于年报隐藏了对手方实名，无法进一步判断拟授信供应商是否就是披露的大额交易对手，也无法排除关联关系、账期异常或重复融资。[C048-C049]

第五，预重整意味着公司已经存在非常严重的债务风险信号，但预重整与正式重整存在明确程序边界。在截止日将公司状态写成“已被法院裁定进入重整”属于使用后验事实污染历史判断。[C039-C040]

### 3. 未经验证的假设

以下假设均不得提升为事实：

- 拟融资应收账款真实存在且对应合同、发票、物流完全一致；
- 应收账款尚未向其他金融机构转让、质押、保理；
- 核心债务人已完成无条件确权；
- 不存在回购、抵销、质量争议、商业折让或关联交易安排；
- 截止日不存在未披露的执行、失信或限制消费记录；
- 银行借款已获得展期或其他债权人同意延期；
- 预重整一定能够进入正式重整并成功完成；
- 控股股东/关联方一定能够完成资金占用清偿。

### 4. 数据缺口

S011 国家企业信用信息公示系统本轮直接访问返回 403，因此无法独立核实研究时点最新登记状态和经营异常情况。

S012 中国执行信息公开网同样返回 403，因此被执行、失信、限制消费、终本案件不能给出 `no_record_found`，只能标记 `unverified`。

全国企业破产重整案件信息网首页可访问，但本轮没有完成对主体、历史案号及所有关联子公司的全量官方检索，因此法院程序主要以公司法定信息披露为依据，不能声称完成了破产网站全库核验。

公开资料还不能验证具体拟融资交易的合同、订单、发票、物流单据、验收单、确权函、银行回款；银行流水、纳税、企业征信同样属于本案例缺失材料。对这些字段不能以“未发现异常”代替证据。

### 5. 截止日之后的后验结果——不得用于当时评级

2024-11-08，苏州市中级人民法院才正式裁定受理江苏中利集团股份有限公司重整；同一公告还披露，公司未能在江苏证监局要求的六个月期限内清收 18.05 亿元被占用资金。[S014] 该事实证明 2024-05-31 时把公司写成“已经正式重整”是时间穿越。

2024-12-11，法院批准公司重整计划并终止重整程序；后续公告还披露拟发生控股股东和实际控制人变更。[S015] 这些均属于后验结果，不得作为 2024-05-31 历史授信的缓释因素。

---

## 输出六：参考风险判断

```yaml
risk_level: 高

hard_gate_triggers:
  - 持续经营存在重大不确定性（C034）
  - 2023年末归母净资产为负（C025）
  - 短期借款大额逾期约26.11亿元（C031）
  - 债权人已经提出重整申请且上市主体处于预重整（C039,C040）
  - 实际控制人及关联方大额非经营性资金占用（C035,C041）
  - 存在违规担保预计负债（C036）
  - 2023年度内部控制审计为否定意见（C037）
  - 2024年一季度继续亏损且净资产进一步恶化（C045,C047）
  - 已收到证监会行政处罚及市场禁入事先告知书（C042,C043）

key_risk_factors:
  - 债务集中到期和流动性压力
  - 资产负债率实质超过100%
  - 经营收入大幅下降
  - 连续亏损
  - 应收账款长账龄和高减值风险
  - 关联方治理及资金占用风险
  - 担保及或有负债风险
  - 内部控制失效
  - 重整结果存在重大不确定性
  - 具体供应链交易无法由公开信息完成真实性与唯一性验证

mitigating_factors:
  - 2023年财务报表正式审计意见为无保留意见，但该因素不能抵消持续经营重大不确定性
  - 2023年经营活动现金流净额仍为正
  - 公司仍持续开展电缆及光伏经营业务
  - 截止日仍在推进预重整和引入投资人，但结果不确定

missing_critical_evidence:
  - 具体合同、发票、物流、验收及债务人确权
  - 应收账款质押/转让/保理及重复融资登记
  - 最新银行流水、借款展期协议和企业征信
  - 纳税资料
  - 法院执行、失信、限消完整官方检索
  - 最新工商登记和经营异常官方查询
  - 全量担保合同、诉讼仲裁清单及最新状态

recommended_action: 暂缓
secondary_action: 人工复核
credit_amount: null
confidence: 0.96

reasoning_claim_ids:
  - C017
  - C023
  - C024
  - C025
  - C028
  - C031
  - C033
  - C034
  - C035
  - C036
  - C037
  - C039
  - C040
  - C041
  - C042
  - C045
  - C047
  - C053
  - C054
```

这里的“暂缓”是**离线评测参考意见**，不是任何金融机构实际审批结果。尤其不能因为 2023 年财务报表形式上为无保留意见，就把持续经营重大不确定性、负净资产、逾期债务、资金占用和预重整状态覆盖掉。

---

## 自检结果

| 自检项 | 结果 |
|---|---|
| 是否存在重大结论无来源 | 未发现；分析性结论明确与事实分开 |
| 是否把关联方/子公司风险自动归属于上市主体 | 已修正；S010仲裁明确归属腾晖光伏，担保采用合并披露口径说明 |
| 是否使用2024-05-31之后信息参与历史判断 | 未使用；S014、S015全部标记为后验 |
| 是否把未发现记录写成没有风险 | 未发生；工商和执行查询失败均为`unverified` |
| 是否引用未访问或不存在链接 | 来源目录均为本轮实际尝试访问；S011/S012明确记录403失败 |
| 是否将预重整写成正式重整 | 未发生；截止日状态为预重整 |
| 是否将强调事项段识别成保留意见 | 未发生；2023 FS明确为无保留意见 |
| 是否混淆财报审计与内控审计 | 未发生；分别记录“无保留”与“否定意见” |
| 是否把《行政处罚事先告知书》写成最终处罚决定 | 未发生 |
| 是否消除控股股东来源冲突 | 未消除；明确保留为`conflicting` |
| 是否把推断写成事实 | 资产负债率、应收账款长账龄占比等均标明为计算/分析结果 |

---

## 输出七：机器可读 JSON

```json
{
  "case": {
    "case_id": "case_12",
    "difficulty": "高难",
    "business_scenario": "电缆、光伏供应链供应商授信",
    "research_cutoff": "2024-05-31",
    "configured_retrieval_date": "2026-08-14",
    "key_benchmark_issue": "正确区分2023年度财务报表无保留意见、持续经营相关重大不确定性段、强调事项段以及内部控制否定意见，不得把强调事项或持续经营段识别成保留意见"
  },
  "subject": {
    "name": "江苏中利集团股份有限公司",
    "former_name": "中利科技集团股份有限公司",
    "unified_social_credit_code": "913205007317618904",
    "stock_code": "002309",
    "exchange": "深圳证券交易所",
    "legal_representative": "王伟峰",
    "actual_controller": "王柏兴",
    "controlling_shareholder_status": "conflicting",
    "controlling_shareholder_conflict": {
      "annual_report_and_audit_wording": "王柏兴",
      "jiangsu_csrc_2024_88_wording": "江苏中利控股集团有限公司"
    }
  },
  "sources": [
    {
      "source_id": "S001",
      "title": "江苏中利集团股份有限公司2023年年度报告全文",
      "publisher": "江苏中利集团股份有限公司/巨潮资讯网",
      "level": "L2",
      "publication_date": "2024-04-24",
      "url": "https://static.cninfo.com.cn/finalpage/2024-04-24/1219775289.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S002",
      "title": "董事会审计委员会对会计师事务所2023年度履职情况评估及履行监督职责情况的报告",
      "publisher": "江苏中利集团股份有限公司/巨潮资讯网",
      "level": "L2",
      "publication_date": "2024-04-24",
      "url": "https://static.cninfo.com.cn/finalpage/2024-04-24/1219775253.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S003",
      "title": "江苏中利集团股份有限公司2022年年度报告摘要",
      "publisher": "江苏中利集团股份有限公司",
      "level": "L3",
      "publication_date": "2023-04-22",
      "url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2023/2023-4/2023-04-22/9047515.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S004",
      "title": "江苏中利集团股份有限公司2024年第一季度报告",
      "publisher": "江苏中利集团股份有限公司",
      "level": "L3",
      "publication_date": "2024-04-30",
      "url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2024/2024-4/2024-04-30/10155807.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S005",
      "title": "江苏证监局关于对江苏中利集团股份有限公司、江苏中利控股集团有限公司、王柏兴采取责令改正措施的决定",
      "publisher": "江苏证监局",
      "level": "L1",
      "publication_date": "2024-05-09",
      "url": "https://www.csrc.gov.cn/jiangsu/c103893/c7479954/content.shtml",
      "format": "HTML",
      "access_status": "success"
    },
    {
      "source_id": "S006",
      "title": "关于收到中国证券监督管理委员会《行政处罚及市场禁入事先告知书》的公告",
      "publisher": "江苏中利集团股份有限公司",
      "level": "L3",
      "publication_date": "2024-05-14",
      "event_date": "2024-05-13",
      "url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2024/2024-5/2024-05-14/10204667.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S007",
      "title": "关于被债权人申请重整及预重整的进展公告",
      "publisher": "江苏中利集团股份有限公司",
      "level": "L3",
      "publication_date": "2024-04-30",
      "event_date": "2024-04-29",
      "url": "https://gu.sina.cn/bd/hq/notice.php?annid=20197876&source=bdquote",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S008",
      "title": "江苏中利集团股份有限公司章程",
      "publisher": "江苏中利集团股份有限公司/巨潮资讯网",
      "level": "L2",
      "publication_date": "2018-12-11",
      "url": "https://static.cninfo.com.cn/finalpage/2018-12-11/1205662102.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S009",
      "title": "关于募集资金2017年半年度存放与使用情况的专项报告",
      "publisher": "江苏中利集团股份有限公司/巨潮资讯网",
      "level": "L2",
      "publication_date": "2017-08-25",
      "url": "https://static.cninfo.com.cn/finalpage/2017-08-25/1203863524.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S010",
      "title": "关于子公司涉及仲裁的公告",
      "publisher": "江苏中利集团股份有限公司",
      "level": "L3",
      "publication_date": "2024-05-31",
      "event_date": "2024-05-30",
      "url": "https://file.finance.qq.com/finance/hs/pdf/2024/05/31/1220211159.PDF",
      "format": "PDF",
      "access_status": "success"
    },
    {
      "source_id": "S011",
      "title": "国家企业信用信息公示系统",
      "publisher": "国家市场监督管理总局",
      "level": "L1",
      "url": "https://www.gsxt.gov.cn/index.html",
      "format": "HTML",
      "access_status": "failed_403"
    },
    {
      "source_id": "S012",
      "title": "中国执行信息公开网",
      "publisher": "最高人民法院",
      "level": "L1",
      "url": "https://zxgk.court.gov.cn/",
      "format": "HTML",
      "access_status": "failed_403"
    },
    {
      "source_id": "S013",
      "title": "全国企业破产重整案件信息网",
      "publisher": "最高人民法院",
      "level": "L1",
      "url": "https://pccz.court.gov.cn/",
      "format": "HTML",
      "access_status": "homepage_accessible_subject_search_incomplete"
    },
    {
      "source_id": "S014",
      "title": "关于重整进展暨在继续推动重整程序中完成资金占用整改及风险提示的公告",
      "publisher": "江苏中利集团股份有限公司/巨潮资讯网",
      "level": "L2",
      "publication_date": "2024-11-23",
      "url": "https://static.cninfo.com.cn/finalpage/2024-11-23/1221815778.PDF",
      "format": "PDF",
      "access_status": "success",
      "as_of_eligible": false
    },
    {
      "source_id": "S015",
      "title": "关于股东权益变动暨控股股东和实际控制人拟发生变更的提示性公告",
      "publisher": "江苏中利集团股份有限公司/巨潮资讯网",
      "level": "L2",
      "publication_date": "2024-12-18",
      "url": "https://static.cninfo.com.cn/finalpage/2024-12-18/1222049190.PDF",
      "format": "PDF",
      "access_status": "success",
      "as_of_eligible": false
    }
  ],
  "claims": [
    {"claim_id":"C001","field":"legal_name","value":"江苏中利集团股份有限公司","status":"verified","source_ids":["S001","S008"],"as_of_eligible":true},
    {"claim_id":"C002","field":"former_name","value":"中利科技集团股份有限公司","status":"verified","source_ids":["S009"],"as_of_eligible":true},
    {"claim_id":"C003","field":"unified_social_credit_code","value":"913205007317618904","status":"verified","source_ids":["S001","S008"],"as_of_eligible":true},
    {"claim_id":"C006","field":"establishment_date","value":"2007-07-26","status":"verified","source_ids":["S008"],"as_of_eligible":true},
    {"claim_id":"C008","field":"registered_capital","value":871787068,"unit":"CNY","status":"verified","source_ids":["S001","S008"],"as_of_eligible":true},
    {"claim_id":"C011","field":"actual_controller","value":"王柏兴","status":"verified","source_ids":["S001","S005"],"as_of_eligible":true},
    {"claim_id":"C012","field":"controlling_shareholder_label","value":{"S001":"王柏兴","S005":"江苏中利控股集团有限公司"},"status":"conflicting","source_ids":["S001","S005"],"as_of_eligible":true},
    {"claim_id":"C014","field":"revenue_2023","value":4051283736.03,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C015","field":"revenue_2022","value":8165891813.89,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C016","field":"revenue_2021","value":10381623530.23,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C017","field":"attributable_net_profit_2023","value":-1496533239.05,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C018","field":"attributable_net_profit_2022_adjusted","value":-475578383.85,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C019","field":"attributable_net_profit_2021","value":-4188446098.15,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C020","field":"operating_cash_flow_2023","value":289661826.87,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C021","field":"operating_cash_flow_2022","value":387179858.18,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C022","field":"operating_cash_flow_2021","value":1094079372.44,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C023","field":"total_assets_2023","value":7712405206.77,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C024","field":"total_liabilities_2023","value":8276843748.05,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C025","field":"attributable_equity_2023","value":-558303262.46,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C026","field":"accounts_receivable_net","value":944281300.16,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C027","field":"accounts_receivable_gross","value":1965181412.99,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C028","field":"accounts_receivable_over_3_years","value":1024153384.82,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C030","field":"short_term_borrowings","value":2910605047.91,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C031","field":"overdue_short_term_borrowings","value":2611107668.96,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C032","field":"disclosed_guarantee_balance","value":2130855200,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C033","field":"financial_statement_audit_opinion_2023","value":"无保留意见，附持续经营相关重大不确定性段及强调事项段","status":"verified","source_ids":["S001","S002"],"as_of_eligible":true},
    {"claim_id":"C034","field":"going_concern_material_uncertainty","value":true,"status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C035","field":"non_operating_fund_occupation_excl_guarantee","value":1692979200,"unit":"CNY","status":"verified","source_ids":["S001","S005"],"as_of_eligible":true},
    {"claim_id":"C036","field":"irregular_guarantee_provision","value":112212700,"unit":"CNY","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C037","field":"internal_control_audit_opinion_2023","value":"否定意见","status":"verified","source_ids":["S001","S002"],"as_of_eligible":true},
    {"claim_id":"C038","field":"financial_statement_audit_opinion_2022","value":"保留意见","status":"verified","source_ids":["S003","S002"],"as_of_eligible":true},
    {"claim_id":"C039","field":"restructuring_application","value":"债权人于2023-01-18提出重整及预重整申请","status":"verified","source_ids":["S001","S007"],"as_of_eligible":true},
    {"claim_id":"C040","field":"restructuring_status_at_cutoff","value":"预重整，上市主体尚未进入正式重整","status":"verified","source_ids":["S007"],"as_of_eligible":true},
    {"claim_id":"C041","field":"regulatory_rectification","value":"责令整改18.05亿元资金占用相关事项","status":"verified","source_ids":["S005"],"as_of_eligible":true},
    {"claim_id":"C042","field":"csrc_procedure_stage","value":"行政处罚及市场禁入事先告知书阶段，非最终处罚决定","status":"verified","source_ids":["S006"],"as_of_eligible":true},
    {"claim_id":"C044","field":"q1_2024_revenue","value":840308434.86,"unit":"CNY","status":"verified","source_ids":["S004"],"as_of_eligible":true},
    {"claim_id":"C045","field":"q1_2024_net_profit","value":-186871532.37,"unit":"CNY","status":"verified","source_ids":["S004"],"as_of_eligible":true},
    {"claim_id":"C046","field":"q1_2024_operating_cash_flow","value":-45994366.29,"unit":"CNY","status":"verified","source_ids":["S004"],"as_of_eligible":true},
    {"claim_id":"C047","field":"q1_2024_attributable_equity","value":-717656631.62,"unit":"CNY","status":"verified","source_ids":["S004"],"as_of_eligible":true},
    {"claim_id":"C048","field":"top5_customer_concentration","value":24.47,"unit":"percent","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C049","field":"top5_supplier_concentration","value":46.28,"unit":"percent","status":"verified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C050","field":"subsidiary_arbitration","value":"苏州腾晖光伏技术有限公司被申请仲裁12446857.64元","status":"verified","source_ids":["S010"],"as_of_eligible":true},
    {"claim_id":"C051","field":"registry_operating_status","value":null,"status":"unverified","source_ids":["S011"],"as_of_eligible":true},
    {"claim_id":"C052","field":"enforcement_default_limit_consumption","value":null,"status":"unverified","source_ids":["S012"],"as_of_eligible":true},
    {"claim_id":"C053","field":"ar_factoring_pledge_duplicate_financing","value":null,"status":"unverified","source_ids":["S001"],"as_of_eligible":true},
    {"claim_id":"C054","field":"private_transaction_evidence","value":null,"status":"unverified","source_ids":[],"as_of_eligible":true},
    {"claim_id":"C055","field":"formal_restructuring_acceptance","value":"2024-11-08","status":"verified","source_ids":["S014","S015"],"as_of_eligible":false},
    {"claim_id":"C056","field":"restructuring_plan_approval","value":"2024-12-11","status":"verified","source_ids":["S015"],"as_of_eligible":false}
  ],
  "checklist": [
    {"item":"主体身份匹配","status":"verified","evidence":["C001","C003","C004"],"manual_review":false,"hard_gate":false},
    {"item":"工商登记状态","status":"unverified","evidence":["C051"],"manual_review":true,"hard_gate":false},
    {"item":"控股股东与实际控制人","status":"conflicting","evidence":["C011","C012"],"manual_review":true,"hard_gate":false},
    {"item":"近三年经营财务","status":"verified","evidence":["C014","C015","C016","C017","C018","C019","C020","C021","C022"],"manual_review":true,"hard_gate":true},
    {"item":"偿债能力","status":"verified","evidence":["C023","C024","C025","C030","C031"],"manual_review":true,"hard_gate":true},
    {"item":"应收账款质量","status":"verified","evidence":["C026","C027","C028"],"manual_review":true,"hard_gate":true},
    {"item":"审计意见","status":"verified","evidence":["C033","C034","C037","C038"],"manual_review":true,"hard_gate":true},
    {"item":"资金占用和违规担保","status":"verified","evidence":["C035","C036","C041"],"manual_review":true,"hard_gate":true},
    {"item":"重整程序","status":"verified","evidence":["C039","C040"],"manual_review":true,"hard_gate":true},
    {"item":"证券监管程序","status":"verified","evidence":["C042","C043"],"manual_review":true,"hard_gate":true},
    {"item":"法院执行失信限消","status":"unverified","evidence":["C052"],"manual_review":true,"hard_gate":false},
    {"item":"应收账款质押转让保理及重复融资","status":"unverified","evidence":["C053"],"manual_review":true,"hard_gate":false},
    {"item":"交易真实性材料","status":"unverified","evidence":["C054"],"manual_review":true,"hard_gate":false}
  ],
  "reference_assessment": {
    "risk_level": "高",
    "hard_gate_triggers": [
      "持续经营重大不确定性",
      "负净资产",
      "大额短期借款逾期",
      "债权人申请重整且处于预重整",
      "大额非经营性资金占用",
      "违规担保",
      "内部控制审计否定意见",
      "证监会行政处罚事先告知程序",
      "2024Q1继续亏损及净资产恶化"
    ],
    "key_risk_factors": [
      "流动性和偿债风险",
      "连续亏损及收入下降",
      "应收账款长账龄及减值风险",
      "关联方治理风险",
      "担保和或有负债风险",
      "重整结果不确定",
      "交易级公开证据不足"
    ],
    "mitigating_factors": [
      "2023年度财务报表正式意见仍为无保留意见",
      "2023年度经营现金流净额仍为正",
      "电缆及光伏主营业务仍在经营",
      "截止日仍在推进预重整"
    ],
    "missing_critical_evidence": [
      "合同发票物流验收确权及回款",
      "应收账款质押转让保理和重复融资登记",
      "银行流水及企业征信",
      "纳税资料",
      "法院执行失信限消完整结果",
      "最新工商公示结果",
      "全量担保和诉讼仲裁最新状态"
    ],
    "recommended_action": "暂缓",
    "credit_amount": null,
    "confidence": 0.96,
    "reasoning_claim_ids": [
      "C017",
      "C023",
      "C024",
      "C025",
      "C028",
      "C031",
      "C033",
      "C034",
      "C035",
      "C036",
      "C037",
      "C039",
      "C040",
      "C041",
      "C042",
      "C045",
      "C047",
      "C053",
      "C054"
    ],
    "usage_note": "仅供离线评测研究，不代表真实金融审批结果"
  },
  "post_cutoff_outcomes": [
    {
      "event_date": "2024-11-08",
      "event": "苏州市中级人民法院正式裁定受理江苏中利集团股份有限公司重整",
      "source_ids": ["S014","S015"],
      "usable_for_cutoff_assessment": false
    },
    {
      "event_date": "2024-11",
      "event": "公司披露未能在江苏证监局责令改正期限内清收18.05亿元被占用资金",
      "source_ids": ["S014"],
      "usable_for_cutoff_assessment": false
    },
    {
      "event_date": "2024-12-11",
      "event": "法院批准江苏中利集团股份有限公司重整计划并终止重整程序",
      "source_ids": ["S015"],
      "usable_for_cutoff_assessment": false
    }
  ],
  "limitations": [
    "国家企业信用信息公示系统本轮访问返回403，登记状态和经营异常未完成独立官方复核",
    "中国执行信息公开网本轮访问返回403，被执行、失信、限消和终本情况不得认定为无记录",
    "全国企业破产重整案件信息网未完成针对主体和全部关联公司的全量结果检索",
    "无法通过公开资料验证拟融资交易的合同、发票、物流、验收、确权及回款",
    "无法取得银行流水、企业征信和纳税等私域数据",
    "无法从年报确认具体应收账款是否已质押、转让、保理或重复融资",
    "主要客户和供应商在年报中匿名披露，无法进行实名交易对手穿透",
    "控股股东标签在2023年报/审计文本与江苏证监局2024年第88号决定之间存在冲突，已保留冲突而未自行消除"
  ]
}
```
