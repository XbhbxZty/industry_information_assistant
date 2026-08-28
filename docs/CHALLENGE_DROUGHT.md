# 跨层挑战稀少的成因分析

样本：30 轮，主体 ['case01', 'mock001', 'mock002']

## 一、规则一的前提：发现有没有绑上清单字段

- 发现总数：**404**
- 绑上 `field_id` 的：**24**（6%）
- `field_id` 为空的：**380**——**这些发现无论内容如何，规则一都不可能触发**

绑上的那些发现，对应字段在 A 层的核查状态：

| 状态 | 发现数 |
|---|---:|
| `unverified` | 17 |
| `verified` | 7 |

> 规则一要求该字段**已核实**。状态不是 verified 的，规则一同样不会触发——它会落进 `contradicts_unverified_field`。

## 二、判定结果的实际去向

| 去向 | 次数 | 占发现数 |
|---|---:|---:|
| `mitigating_suppressed` | 237 | 59% |
| `undecidable` | 19 | 5% |
| `challenges` | 7 | 2% |
| `rule1_contradicts_verified_field` | 4 | 1% |
| `rule2_high_aggravating_in_uncovered_dimension` | 3 | 1% |
| `contradicts_unverified_field` | 1 | 0% |
| `contradicts_unknown_field` | 0 | 0% |

## 二·五、规则二的前提逐层筛

| 条件 | 剩余发现 | 占比 |
|---|---:|---:|
| aggravating | 167 | 41% |
| + materiality=high | 82 | 20% |
| + subject_confirmed | 82 | 20% |

第四个条件是**维度未被清单覆盖**。实测各轮的 `covered_dimensions`：

- 10 轮：`['basic', 'equity', 'financial', 'judicial', 'operation']`
- 10 轮：`['basic', 'equity', 'financial', 'judicial', 'operation', 'relation']`
- 10 轮：`['basic', 'equity', 'financial', 'judicial', 'operation', 'opinion', 'relation']`

而 B 层发现用到的维度：`{'opinion': 17, 'relation': 58, 'operation': 267, 'financial': 61, 'judicial': 1}`

> **两个词表是同一套。** B 层只能用清单自己的类目给发现打标签，而规则二要求「落在清单没覆盖的维度」——**这个前提结构性地几乎不可能满足**。剩下能触发的只有个别轮次里恰好未覆盖的 `relation` / `opinion`，这与规则二实际只触发 3 次吻合。

## 三、被抑制的 mitigating 发现长什么样

⚠️ **`counts` 说被抑制了 237 条，但一条都没有留痕。**

`cross_layer_verdict` 在规则三那一支是：

```python
if judgment["direction"] == "mitigating":
    counts["mitigating_suppressed"] += 1
    continue                     # ← 不写 note
```

所以**抑制了什么根本没存下来**，只存了抑制了多少。
「没有记录」不等于「没有发生」——把仪表的缺口读成数据的结论，
正是 BC-75「丢弃必须可见」那条纪律要防的事。

**结果：规则三是否过宽，用现有数据无法判断。** 要回答它，得先让那 237 条留下痕迹。

## 四、undecidable 的都是什么

- 共 **19** 条

- [relation／aggravating] field_id=related_party｜毛坯件主要向关联方东莞市泰锐五金制品有限公司采购，2025年度采购额6,320万元，占采购总额41.2%。
- [operation／aggravating] field_id=operating_status｜截至2025年末，公司齿轮加工设计产能480万件/年，实际产量351万件，产能利用率73.1%，较2024年的84.6%下降11.5个百分点。
- [operation／aggravating] field_id=operating_status｜二期精密铸造车间于2025年6月开工，计划总投资8,600万元，截至2025年末已投入3,900万元，工程形象进度约45%；原计划2026年三季度投产，因设备到
- [financial／aggravating] field_id=cash_flow｜现金流连续两年为负，原因是主机厂账期延长与二期项目投入叠加。
- [financial／aggravating] field_id=related_party｜关联方拆借余额1,500万元，无书面协议，按需归还。
- [financial／aggravating] field_id=cash_flow｜2025年末应收账款账面余额12,840万元，周转天数由上年118天上升至147天，其中账龄1年以上部分较上年增加1,860万元。

## 判定

- **94% 的发现没有绑上任何清单字段。**
  规则一对它们**结构性地不可能触发**——不是「查了没矛盾」，是「压根没进入判定」。
  阶段 4 若在此基础上定阈值，定的是一个大半输入都够不着的探测器。

- 抑制数（237）远高于挑战数（7）。规则三是否过宽，要看上面「高重要性被抑制」那一节——**若那里为 0，说明规则三拦掉的确实都是低价值的**。

