# 跨层一致性观察汇总（阶段 2）

> 本汇总只呈现数字与对应建议，**结论由人做**——「停下来先改设计」是架构判断，不该由统计脚本代劳。

- 已判定轮次：**15**（预设 30，中期检视点 10）
- 存在实质挑战的轮次：**5**（33%）
- 不一致类型数：**2**

## 按规则

| 规则 | 次数 |
|---|---:|
| rule2_high_aggravating_in_uncovered_dimension | 3 |
| rule1_contradicts_verified_field | 2 |

## 按维度

| 维度 | 次数 |
|---|---:|
| relation | 4 |
| financial | 1 |

## 不计入不一致的口径差异

> 这几类看起来像矛盾，其实不是。把它们算进不一致率，阶段 4 的决策就会建立在一个虚高的数上。

| 类型 | 次数 |
|---|---:|
| mitigating_suppressed | 105 |

## 样本（每条都能指认依据）

- **[stage2-case01-r2-c89d5491]** 向联营企业采购商品163.56亿元，向联营企业销售商品53.90亿元。
　规则：rule2_high_aggravating_in_uncovered_dimension｜依据：高重要性负面发现落在 A 层未覆盖的「relation」维度（该维度没有任何已核实项）
- **[stage2-case01-r4-2c8817fb]** 公司前五名客户中第一名销售额为54,173,399千元，占年度销售总额14.96%，且公司与该客户签订了重大销售合同，合同未约定总金额，以订单方式确定。
　规则：rule2_high_aggravating_in_uncovered_dimension｜依据：高重要性负面发现落在 A 层未覆盖的「relation」维度（该维度没有任何已核实项）
- **[stage2-case01-r5-a78fab0a]** 前五名客户合计销售金额占年度销售总额37.03%，其中第一名占14.96%。
　规则：rule2_high_aggravating_in_uncovered_dimension｜依据：高重要性负面发现落在 A 层未覆盖的「relation」维度（该维度没有任何已核实项）
- **[stage2-mock001-r3-d570a8ec]** 公司持有惠州泰锐传动科技有限公司30%股权，出资额900万元，按权益法核算，2025年度确认投资损失116万元。
　规则：rule1_contradicts_verified_field｜依据：清单对「external_investment」的已核实结论是「经查询，无相关记录」，而调查层在材料中发现了实质内容
- **[stage2-mock001-r5-b6ba3817]** 公司持有惠州泰锐传动科技有限公司30%股权，出资额900万元，按权益法核算，2025年度确认投资损失116万元。
　规则：rule1_contradicts_verified_field｜依据：清单对「external_investment」的已核实结论是「经查询，无相关记录」，而调查层在材料中发现了实质内容

## 中期检视建议（计划 9.3）

- 不一致类型 2 种（≤3）——若最近 3 轮无新类型，**可提前停**，直接进阶段 4