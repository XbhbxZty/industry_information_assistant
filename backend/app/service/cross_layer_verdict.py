# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
跨层一致性判定（纯规则，无 LLM）—— 双轨产出计划阶段 2

## 这一层回答什么

A 层出等级，B 层出发现，**这里判断那些发现是否构成对 A 层的实质挑战**。

计划第四节明确否掉了"两层各自打分再比对"的方案：A 层结构上只有抬升下限
的闸门、几乎没有降低等级的路径，而 B 层从自由调查出发倾向给低风险。
硬要它们输出同一个等级，**比出来的差异大部分是口径差异而非判断差异**，
结果就是每次都要人工复核。所以第一版做「主判 + 反证」。

## 判定权在代码，不在模型

与 `review_verdict.py`（BC-29 把 Critic 的裁决权收回代码）同一条纪律：
模型负责把非结构化信息转成结构化字段，**规则负责判断**。

第一版曾让模型自报 `contradicts_field`（"这条与清单矛盾吗"）。
实测三个主体 48 条发现，**它一次都没填过**——因为提示词从来没告诉它
清单里已核实的取值是什么，我让模型做了一件它拿不到输入的判断。

现在的分工是：

    模型  回答"这条讲的是哪个字段"（`field_id`）—— 容易，且可校验
    代码  回答"这构成对清单结论的推翻吗"        —— 判定

`direction` / `materiality` 仍由模型自报，但只是输入，
还要先过一道收敛（见 `normalize_judgment`）。

## 三条规则

1. **代码判定**该发现推翻了一条已核实结论 → 不一致
   （最强信号：清单说"经查询，无相关记录"，材料里却有一条）
2. `materiality=high` + `subject_confirmed` + `direction=aggravating`，
   且落在 A 层**未覆盖**的维度 → 不一致
3. **`mitigating` 永远不单独触发**

第 3 条是红线。否则搜到几条正面报道就能把高风险降下来，
那是 BC-P3「数据缺失判低风险」的变体。

## 一个必须区分的边界

`field_id` 指向一个**未核实**字段时**不算不一致**——
清单在那一项上没有结论，也就没有可被推翻的东西。那是 B 层在填空缺，
与"清单说 A、调查说 B"完全不同。混为一谈会让不一致率虚高，
而阶段 2 的全部目的就是**测准这个率**。

## 阶段 2 只观察，不闸门

判定结果写进状态与报告，**不触发人工复核、不改变等级与额度**。
这条由 `tests/test_cross_layer_verdict.py` 的结构断言把守——
写在注释里的约束迟早会被后来的改动突破。
"""
from typing import Any, Dict, List, Optional

#: 模型自报的取值只在这些集合内有效，其余一律收敛为 UNKNOWN。
DIRECTIONS = ("aggravating", "mitigating")
MATERIALITY = ("high", "medium", "low")

#: **未知就是未知**，不得默认成任何一端。
#:
#: 默认成 `aggravating` 会让缺字段的候选凭空触发不一致；默认成 `mitigating`
#: 会让真正的负面发现静默失效。两个方向都是拿"没说"冒充"说了"，
#: 而这个项目对这类替换的立场一贯明确（`unratable()` / BC-51）。
UNKNOWN = "unknown"

#: 发现所属维度，与清单的 category 同名，便于直接对齐。
DIMENSIONS = ("financial", "judicial", "operation", "relation", "opinion")

RULE_CONTRADICTS_VERIFIED = "rule1_contradicts_verified_field"
RULE_HIGH_AGGRAVATING_UNCOVERED = "rule2_high_aggravating_in_uncovered_dimension"

#: 不构成不一致、但必须单独计数的两种情形。
#: 把它们混进"一致"会丢掉阶段 2 最想看的东西：**不一致率里有多少是口径差异**。
NOTE_FILLS_GAP = "contradicts_unverified_field"      # 指向未核实项 = 填空缺
NOTE_INVALID_FIELD = "contradicts_unknown_field"      # 指向不存在的字段


def _checklist_ids() -> set:
    try:
        from config.dd_checklist import CHECKLIST_BY_ID
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.config.dd_checklist import CHECKLIST_BY_ID  # type: ignore
    return set(CHECKLIST_BY_ID)


def normalize_judgment(raw: Dict[str, Any]) -> Dict[str, str]:
    """把模型自报的判定字段收敛成可判定的取值。

    非法值一律落到 `UNKNOWN`，而不是就近纠正到某个合法值——
    "模型写了个我们不认识的词"和"模型说它是高风险"是两回事。
    """
    direction = str(raw.get("direction") or "").strip().lower()
    materiality = str(raw.get("materiality") or "").strip().lower()
    dimension = str(raw.get("dimension") or "").strip().lower()
    return {
        "direction": direction if direction in DIRECTIONS else UNKNOWN,
        "materiality": materiality if materiality in MATERIALITY else UNKNOWN,
        "dimension": dimension if dimension in DIMENSIONS else UNKNOWN,
    }


# ---------------------------------------------------------------- 冲突识别

#: 代码能判定的冲突形态。
CONTRADICTION_VERIFIED_NEGATIVE = "verified_negative_contradicted"

#: 两边都有内容、但代码比不了。**必须单独计数**——
#: 把它算进"一致"会低报，算进"不一致"会高报，而阶段 2 的全部目的是测准。
#: 与 `cross_channel_undecidable` 同一条纪律。
CONTRADICTION_UNDECIDABLE = "undecidable"

#: 一条发现要构成对否定结论的推翻，至少得有这么多自有内容。
#: 太短的陈述（"有一笔"）无法支撑"清单说没有、这里说有"这个断言。
MIN_CONTRADICTION_CHARS = 12

#: 被规则三抑制的发现，也要留痕（不只是计数）。
#:
#: 两批 30 轮实测：抑制 237 条、挑战 7 条，34 : 1。而抑制的**内容**
#: 一条都没存下来，于是「规则三是不是拦掉了真信号」这个问题
#: 用现有数据根本无法回答——静默丢弃与「本来就没有」外观相同（BC-75）。
NOTE_MITIGATING_SUPPRESSED = "mitigating_suppressed"

#: `notes` 的条数上界。它直接进检查点，无上界会让检查点随发现数线性膨胀
#: （与语料留存那条「红线 4」同一条纪律）。
#:
#: ⚠️ 上界只截断 `notes`，**不影响 `counts`**——计数必须始终准确，
#: 否则截断会悄悄改变统计结论。超出的条数单独记在
#: `notes_truncated` 里：那说明该调这个上界，与「抑制了多少」是两件事。
MAX_NOTES = 120


def is_verified_negative(check: Dict[str, Any]) -> bool:
    """该核查项是不是一条**已核实的否定结论**。

    `absence_meaningful=True` 的字段（涉诉、失信、对外担保、舆情…）
    可以合法地不存在，查询返回空就是"确认没有"——那是一个**正面结论**，
    也是唯一一种代码能机械判定被推翻的结论：
    「清单说没有」与「材料里有一条」之间不存在解释空间。
    """
    if check.get("status") != "verified":
        return False
    try:
        from config.dd_checklist import CHECKLIST_BY_ID, NO_RECORD_VALUE
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.config.dd_checklist import (  # type: ignore
            CHECKLIST_BY_ID, NO_RECORD_VALUE,
        )
    item = CHECKLIST_BY_ID.get(str(check.get("field_id") or ""))
    if item is None or not item.absence_meaningful:
        return False
    return str(check.get("value") or "").strip() == NO_RECORD_VALUE


def detect_contradiction(
    finding: Dict[str, Any],
    field_checks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """**由代码判断**这条发现是否推翻了清单结论。

    ## 为什么把这件事从模型手里拿回来

    第一版让模型自报 `contradicts_field`。2026-08-23 三主体实测：
    三个主体、48 条发现，**模型一次都没填过这个字段**——
    哪怕语料里埋了两处刻意的矛盾。

    原因很简单：**提示词从来没告诉模型清单里已核实的取值是什么。**
    它看到"持有某公司 30% 股权"，无从知道档案里 `external_investment`
    写着"经查询，无相关记录"。我让模型做一件它拿不到输入的判断。

    补输入（把已核实取值喂进提示词）是另一条路，但那等于把冲突判定
    交给模型的自觉——本文档 81 条 bad case 的贯穿结论正是不能这么做，
    而 BC-29 记的就是"把裁决权从模型收回代码"。

    ## 现在的分工

        模型  回答"这条讲的是哪个字段"          —— 容易，且可校验
        代码  回答"这构成对清单结论的推翻吗"    —— 判定

    ## v1 只判一种形态，其余显式记为无法判定

    **已核实的否定结论 vs 有实质内容的发现**：完全机械可判。

    数值分歧（清单说一笔 1200 万、材料说两笔合计 4000 万）**不判**——
    口径、期间、笔数都可能不同，naive 的数值比对会造出假矛盾。
    这类记为 `undecidable` 并单独计数，而不是假装没看见：
    系统说"这里两边都有内容但我比不了"，比说"没有矛盾"诚实。

    Returns: {} 表示不构成；否则 {kind, field_id, a_layer_value, why}
    """
    field_id = str(finding.get("field_id") or "").strip()
    if not field_id or field_id.lower() in ("null", "none"):
        return {}
    if field_id not in _checklist_ids():
        # 模型报了一个不存在的字段。**不能凭空造出一条对清单的挑战**，
        # 但要计数——它说明提示词里的字段清单没被遵守。
        return {"kind": NOTE_INVALID_FIELD, "field_id": field_id,
                "a_layer_value": None,
                "why": f"「{field_id}」不在核查清单内，已忽略该归属"}
    check = next((c for c in field_checks or []
                  if str(c.get("field_id")) == field_id), None)
    if check is None or check.get("status") != "verified":
        # 清单在这一项上没有结论，就没有可被推翻的东西——**那是填空缺**。
        # 单独计数：不一致率里有多少是口径差异，是阶段 2 最想知道的事。
        return {"kind": NOTE_FILLS_GAP, "field_id": field_id,
                "a_layer_value": None,
                "why": f"清单对「{field_id}」尚无已核实结论，"
                       f"本条是在填补信息空缺，不构成矛盾"}

    claim = str(finding.get("claim") or "").strip()
    if is_verified_negative(check):
        if len(claim) < MIN_CONTRADICTION_CHARS:
            return {}
        return {
            "kind": CONTRADICTION_VERIFIED_NEGATIVE,
            "field_id": field_id,
            "a_layer_value": check.get("value"),
            "why": f"清单对「{field_id}」的已核实结论是"
                   f"「{check.get('value')}」，而调查层在材料中发现了实质内容",
        }

    return {
        "kind": CONTRADICTION_UNDECIDABLE,
        "field_id": field_id,
        "a_layer_value": check.get("value"),
        "why": f"清单与调查层对「{field_id}」都有内容，"
               f"但取值形态不可机械比对（口径/期间/笔数可能不同），需人工核对",
    }


def covered_dimensions(field_checks: List[Dict[str, Any]]) -> set:
    """A 层已经"覆盖"了哪些维度。

    定义为**该维度下至少有一项已核实**。取这个宽松定义是刻意的：
    规则 2 要抓的是"B 层在 A 层完全没看见的地方发现了重要负面事实"，
    而不是"A 层查得不够全"——后者由完整度闸门管，不该在这里重复计一次。

    这个定义会随数据被复盘：阶段 2 的观察记录里带着它，
    若发现规则 2 几乎不触发，第一个该检查的就是这里。
    """
    return {str(c.get("category") or "") for c in field_checks or []
            if c.get("status") == "verified"}


def judge_findings(
    findings: List[Dict[str, Any]],
    field_checks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """按三条规则判定 B 层发现是否挑战了 A 层结论。

    Returns 一个可直接导出的记录：`challenges` 里每条都指认了触发的规则、
    具体发现、以及被挑战的 A 层字段与取值——阶段 2 的验收要求
    「每条都能指认具体依据」。
    """
    covered = covered_dimensions(field_checks)
    challenges: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []
    counts = {"findings": len(findings or []), "challenges": 0,
              RULE_CONTRADICTS_VERIFIED: 0, RULE_HIGH_AGGRAVATING_UNCOVERED: 0,
              CONTRADICTION_UNDECIDABLE: 0,
              NOTE_FILLS_GAP: 0, NOTE_INVALID_FIELD: 0,
              "mitigating_suppressed": 0}

    for finding in findings or []:
        judgment = normalize_judgment(finding)
        # 冲突由**准入时的代码判定**写在发现上（路 B）；这里只消费结论，
        # 不再读模型自报的 `contradicts_field`。
        contradiction = finding.get("contradiction") or {}
        claim = str(finding.get("claim") or "")[:200]
        source = finding.get("source") or {}
        base = {"claim": claim, "dimension": judgment["dimension"],
                "direction": judgment["direction"],
                "materiality": judgment["materiality"],
                "source": source}

        kind = contradiction.get("kind")
        if kind in (CONTRADICTION_UNDECIDABLE, NOTE_FILLS_GAP, NOTE_INVALID_FIELD):
            counts[kind] += 1
            notes.append({**base, "note": kind,
                          "field_id": contradiction.get("field_id"),
                          "a_layer_value": contradiction.get("a_layer_value"),
                          "why": contradiction.get("why")})

        # —— 规则 1：推翻了清单的已核实结论 ——
        if kind == CONTRADICTION_VERIFIED_NEGATIVE:
            counts[RULE_CONTRADICTS_VERIFIED] += 1
            challenges.append({
                **base, "rule": RULE_CONTRADICTS_VERIFIED,
                "field_id": contradiction.get("field_id"),
                "a_layer_value": contradiction.get("a_layer_value"),
                "why": contradiction.get("why"),
            })
            continue

        # —— 规则 3：mitigating 永远不单独触发 ——
        if judgment["direction"] == "mitigating":
            counts["mitigating_suppressed"] += 1
            # 抑制了什么必须存下来。只存「抑制了多少」的话，
            # 「规则三是否过宽」这个问题永远无法复核（BC-75 同形）。
            notes.append({**base, "note": NOTE_MITIGATING_SUPPRESSED,
                          "field_id": finding.get("field_id") or "",
                          "a_layer_value": None,
                          "why": "利好方向的发现不单独构成对 A 层结论的挑战"})
            continue

        # —— 规则 2：高重要性负面发现落在未覆盖维度 ——
        if (judgment["materiality"] == "high"
                and judgment["direction"] == "aggravating"
                and bool(finding.get("subject_confirmed"))
                and judgment["dimension"] not in covered
                and judgment["dimension"] != UNKNOWN):
            counts[RULE_HIGH_AGGRAVATING_UNCOVERED] += 1
            challenges.append({
                **base, "rule": RULE_HIGH_AGGRAVATING_UNCOVERED,
                "field_id": "", "a_layer_value": None,
                "why": f"高重要性负面发现落在 A 层未覆盖的「{judgment['dimension']}」"
                       f"维度（该维度没有任何已核实项）",
            })

    counts["challenges"] = len(challenges)
    return {
        "verdict": "challenged" if challenges else "consistent",
        # ⚠️ 阶段 2 只观察。这个标志由代码写死为 False，
        #    是为了让"要不要升级成闸门"成为一次显式的决定，而不是某次改动的副作用。
        "triggers_human_review": False,
        "challenges": challenges,
        # ⚠️ 截断只作用于 `notes`，`counts` 始终是全量——
        #    让截断改变统计数字，是比不截断更糟的事。
        "notes": notes[:MAX_NOTES],
        "notes_truncated": max(0, len(notes) - MAX_NOTES),
        "counts": counts,
        "covered_dimensions": sorted(covered),
    }


# ---------------------------------------------------------------- 报告渲染

SECTION_TITLE = "跨层一致性判定（观察期，不触发闸门）"


def render_markdown(verdict: Optional[Dict[str, Any]]) -> List[str]:
    """渲染为报告可嵌入的行。空判定返回空列表。"""
    if not verdict or not verdict.get("counts"):
        return []
    counts = verdict["counts"]
    lines = [
        "", f"### {SECTION_TITLE}", "",
        "> 本判定**不改变风险等级与授信额度，也不触发人工复核**。"
        "它记录调查层的发现是否构成对清单结论的实质挑战，用于积累校准数据。",
        "",
        f"- 判定结果：**{'存在实质挑战' if verdict['verdict'] == 'challenged' else '未发现实质挑战'}**"
        f"（{counts['challenges']}/{counts['findings']} 条发现构成挑战）",
    ]
    if counts.get(NOTE_FILLS_GAP):
        lines.append(
            f"- 其中 {counts[NOTE_FILLS_GAP]} 条指向**未核实**字段——"
            f"那是填补信息空缺，不构成矛盾")
    if counts.get(NOTE_INVALID_FIELD):
        lines.append(
            f"- 其中 {counts[NOTE_INVALID_FIELD]} 条指向了清单里不存在的字段，已忽略该指向")
    if counts.get("mitigating_suppressed"):
        lines.append(
            f"- {counts['mitigating_suppressed']} 条正面发现按规则不单独触发挑战")

    for item in verdict.get("challenges") or []:
        lines.append("")
        lines.append(f"**挑战**：{item['claim']}")
        lines.append(f"　依据：{item['why']}")
        source = item.get("source") or {}
        lines.append(f"　来源：{source.get('publisher') or source.get('title') or '未标注'}"
                     f"（{source.get('published_at') or '日期未标注'}）")
    return lines
