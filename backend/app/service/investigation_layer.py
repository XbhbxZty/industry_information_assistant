# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
调查层（B 层）—— 双轨产出计划阶段 1

## 这一层要解决的问题

尽调模式为了反幻觉，把图表、知识图谱、趋势分析整块关掉了，产出是一份
只有表格的报告。但**「不能给授信结论」和「报告没价值」是两件事**
（`docs/DUAL_TRACK_PLAN.md` 第一节）。行业地位、客户结构、上下游议价能力
永远不会进二十项清单，却恰恰是信贷经理判断的依据。

本模块把这些能力接回来，同时**一道闸门都不松**。办法是物理隔离：

    A 层（裁决）  field_checks / risk_assessment / completeness / evidence_store
    B 层（调查）  state["investigation"]   ← 本模块唯一的写入位置

A 层代码从不读 `state["investigation"]`，B 层代码从不写 A 层任何字段。
**隔离靠数据结构，不靠"记得别写"**——后者迟早失效，BC-50 就是那样失效的
（两条路径各自记得调用收口，其中一条忘了，附录整块消失）。

因此这里不复用 `state["charts"]` / `state["knowledge_graph"]`：那两个字段的
每一个消费者都是在"尽调模式下它们恒为空"的前提下写的，往里填东西等于
把隔离的责任摊给下游每一处调用点。新开字段，下游默认看不见。

## 图表为什么必须分类标注

**一张图比一句话权威得多。** 读者会核对正文措辞，但不会去核对趋势图的脚注。
所以类别写进数据结构、由 `make_chart()` 强制，而不是写在注释里：

| 类型 | 数据来源 | 生成方 | 标记 |
|---|---|---|---|
| 确定性 | A 层已核实字段 | 纯代码解析 | 标注字段 + evidence_id |
| 探索性 | B 层调查材料 | LLM | 强制带徽标 + 来源 + 未核实声明 |

确定性图表解析的就是证据附录引用的那个取值字符串，**不做二次换算**。
一旦允许换算，同一个数就有了两个口径（BC-31 的形态）。

## 三条硬约束，都来自已实测的失效

1. **解析不出来就不画。** 三个区间解析出两个，画出来的是一条会被当作
   完整趋势看的折线。与 `unratable()` 同一原则：给出一个有数字的产物，
   读者就会当它是结论。
2. **单位不一致就不画。** 万元与亿元混在一条折线上，图形本身就是错的，
   而且错得看不出来。
3. **B 层发现必须过主体确认。** 复用 A 层的 `_subject_aliases` 判据——
   同名公司的材料被写成本主体风险，BC-04 / BC-12 / BC-14 都栽过。

## 失败必须留痕

B 层查不到东西，报告就少一节，A 层评级与额度照常产出（计划 9.2）。
但**少的那一节要写明原因**——是"查了没有"还是"没查成"。
沿用 `search_failures` / `section_failures` 的纪律（BC-51）：
不留痕的话，读者会以为这一节本来就不存在。
"""
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple


def _verified_negative_kind() -> str:
    """真矛盾的种类标识，**从 `cross_layer_verdict` 取**，不在这里另写一份。

    两处各写一个字面量就是「一条规则有两份实现」（BC-52/BC-59 的形态），
    而这里的分叉会让"豁免去重"悄悄失效——最有价值的那类发现被自己的
    另一道闸门吃掉，且只留一条"未通过准入判定"。
    """
    try:
        from service.cross_layer_verdict import CONTRADICTION_VERIFIED_NEGATIVE
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.service.cross_layer_verdict import (  # type: ignore
            CONTRADICTION_VERIFIED_NEGATIVE,
        )
    return CONTRADICTION_VERIFIED_NEGATIVE


def detect_contradiction(finding: Dict[str, Any],
                        field_checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """惰性取跨层冲突识别器（阶段 2 路 B）。惰性理由同 `_subject_predicates`。"""
    try:
        from service.cross_layer_verdict import detect_contradiction as _d
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.service.cross_layer_verdict import (  # type: ignore
            detect_contradiction as _d,
        )
    return _d(finding, field_checks)


def normalize_judgment(raw: Dict[str, Any]) -> Dict[str, str]:
    """惰性取跨层判定的取值收敛器（阶段 2）。

    惰性同 `_subject_predicates`：`state.py` 要用本模块的
    `empty_investigation()`，顶层导入会把整条链拉进状态模块的导入期。
    """
    try:
        from service.cross_layer_verdict import normalize_judgment as _n
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.service.cross_layer_verdict import normalize_judgment as _n  # type: ignore
    return _n(raw)


def _subject_predicates():
    """惰性取 A 层的主体确认判据。

    惰性是必需的：`state.py` 要用本模块的 `empty_investigation()` 初始化
    状态，而顶层导入 `rag_evidence_bridge` 会把 company_profile /
    risk_scorecard / verification 整条链拉进状态模块的导入期。

    **判据必须复用而不是照抄一份**——两份实现迟早会分叉，
    而分叉之后 B 层的主体确认会比 A 层松，那正是红线 3 要防的。
    """
    try:
        from service.rag_evidence_bridge import (  # noqa: WPS433
            _document_identity_zone, _subject_aliases,
        )
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.service.rag_evidence_bridge import (  # type: ignore # noqa: WPS433
            _document_identity_zone, _subject_aliases,
        )
    return _document_identity_zone, _subject_aliases


# ---------------------------------------------------------------- 常量

LAYER_INVESTIGATION = "investigation"

CHART_DETERMINISTIC = "deterministic"
CHART_EXPLORATORY = "exploratory"

#: 探索性产物的视觉标记。红线 2 要求它出现在标题里而不只是脚注——
#: 脚注在缩略图、导出图片、截图转发时都会丢失，标题不会。
EXPLORATORY_BADGE = "〔探索性·未经核实〕"

DETERMINISTIC_NOTE = "数据解析自上文已核实字段，与证据溯源附录同源，未做任何换算。"
EXPLORATORY_NOTE = (
    "本图由调查层生成，数据**未通过证据闸门核验**，"
    "不参与风险评级与授信额度，仅供人工参考。"
)

#: 章节锚点。与评级块、证据附录同一套收敛策略：模型改写过的版本一律切除，
#: 放回代码生成的权威版本（BC-50）。
SECTION_MARKER = "调查层补充（不参与授信裁决"
SECTION_END = "<!-- /investigation-layer -->"

# 红线 4：B 层要有上界。这些不是性能考虑——一份带三十张图的尽调报告，
# 读者会放弃逐张核对来源，等于所有标注都失效。
MAX_EXPLORATORY_CHARTS = 4
MAX_FINDINGS = 20
MAX_GRAPH_NODES = 40
MAX_GRAPH_EDGES = 60
MAX_CLAIM_CHARS = 220

#: 可绘制趋势的 A 层字段。**代码常量而非模型选择**——与 `CHECKLIST` 同一理由：
#: 这些图要被断言、被比对，引入变异性就没法回答"两次跑出的图为什么不一样"。
TREND_FIELDS: List[Tuple[str, str]] = [
    ("revenue", "line"),
    ("net_profit", "line"),
    ("debt_ratio", "line"),
    ("cash_flow", "bar"),
    ("accounts_receivable_gross", "line"),
    ("accounts_receivable_net", "line"),
]

#: `2023年度 41250.0万元` / `2025年度 63.3%` / `2024 1180 万元`
_SEGMENT = re.compile(
    r"^\s*(?P<period>[^\s]{2,12}?)\s+"
    r"(?P<num>-?[\d,]+(?:\.\d+)?)\s*"
    r"(?P<unit>亿元|万元|千元|百万元|元|%|)\s*$"
)
_SEPARATORS = re.compile(r"[；;]")


# ---------------------------------------------------------------- 状态容器

def empty_investigation() -> Dict[str, Any]:
    """B 层状态的空壳。

    `enabled=False` 与"跑了但没结果"是不同的两件事，必须能分辨——
    否则读者看到空章节，无从判断是关掉了还是查空了（BC-51 同形）。
    """
    return {
        "enabled": False,
        "charts": [],
        "graph": {"nodes": [], "edges": []},
        "findings": [],
        "failures": [],
    }


def get_investigation(state: Dict[str, Any]) -> Dict[str, Any]:
    """取（必要时初始化）B 层容器。"""
    box = state.get("investigation")
    if not isinstance(box, dict):
        box = empty_investigation()
        state["investigation"] = box
    for key, default in empty_investigation().items():
        box.setdefault(key, default)
    return box


def record_failure(state: Dict[str, Any], stage: str, reason: str,
                   kind: str = "not_found", detail: str = "") -> None:
    """记一条 B 层失败。

    `kind` 区分「查了没有」(`not_found`) 与「没查成」(`error`)。
    这个区分是 BC-51 的全部内容：合并成一句"本节无内容"，
    读者就无法判断该不该补查。
    """
    get_investigation(state)["failures"].append({
        "stage": stage,
        "kind": kind if kind in ("not_found", "error", "disabled") else "error",
        "reason": reason,
        "detail": str(detail or "")[:200],
    })


# ---------------------------------------------------------------- 图表构造

def make_chart(
    chart_class: str,
    chart_type: str,
    title: str,
    *,
    provenance: Dict[str, Any],
    series: Optional[List[Dict[str, Any]]] = None,
    graph: Optional[Dict[str, Any]] = None,
    unit: str = "",
    subtitle: str = "",
) -> Dict[str, Any]:
    """构造一张 B 层图表。

    ⚠️ 分类与来源是**必填**，缺失直接抛错而不是给默认值。

    给默认值等于让"忘了标注"静默通过——而红线 2 保护的恰恰是这个：
    一张没标来源的探索性图表，与一张确定性图表在视觉上毫无区别。
    """
    if chart_class not in (CHART_DETERMINISTIC, CHART_EXPLORATORY):
        raise ValueError(f"未知的图表类别：{chart_class!r}")
    if not provenance:
        raise ValueError(f"图表「{title}」缺少来源标注，不得生成")
    if chart_class == CHART_DETERMINISTIC and not provenance.get("field_id"):
        raise ValueError(f"确定性图表「{title}」必须载明所依据的清单字段")
    if chart_class == CHART_EXPLORATORY and not provenance.get("sources"):
        raise ValueError(f"探索性图表「{title}」必须载明来源")

    exploratory = chart_class == CHART_EXPLORATORY
    return {
        "id": f"bchart_{uuid.uuid4().hex[:8]}",
        "layer": LAYER_INVESTIGATION,
        "chart_class": chart_class,
        "chart_type": chart_type,
        # 徽标进标题：脚注会在截图、导出、缩略图里丢失，标题不会
        "title": f"{EXPLORATORY_BADGE}{title}" if exploratory else title,
        "plain_title": title,
        "subtitle": subtitle,
        "unit": unit,
        "series": list(series or []),
        "graph": graph or {},
        "provenance": provenance,
        "note": EXPLORATORY_NOTE if exploratory else DETERMINISTIC_NOTE,
    }


# ---------------------------------------------------------------- 确定性解析

def parse_period_series(value: str) -> Dict[str, Any]:
    """把已核实字段的取值串解析成时间序列。

    输入形如 `2023年度 41250.0万元；2024年度 46800.0万元`——
    这正是 `company_profile._fin_series()` 写进 `check["value"]`、
    并被证据附录逐字引用的那个字符串。**解析而不换算**，
    保证图上的数与附录里的数逐位相同。

    Returns: {points, unit, unparsed, refusal}
             `refusal` 非空表示不得绘制，且已说明原因。
    """
    text = str(value or "").strip()
    if not text:
        return {"points": [], "unit": "", "unparsed": [], "refusal": "取值为空"}

    points: List[Dict[str, Any]] = []
    unparsed: List[str] = []
    units: List[str] = []
    for raw in _SEPARATORS.split(text):
        segment = raw.strip()
        if not segment:
            continue
        m = _SEGMENT.match(segment)
        if not m:
            unparsed.append(segment[:40])
            continue
        try:
            number = float(m.group("num").replace(",", ""))
        except ValueError:
            unparsed.append(segment[:40])
            continue
        # `text` 保留原文写法。`41250.0` 被渲染成 `41250` 只是"更好看"，
        # 但它让图上的数与附录里的数不再逐字相同——BC-59 记的正是这种
        # 同一个数在两处有两种写法的漂移。数值给图表用，原文给正文用。
        points.append({"period": m.group("period"), "value": number,
                       "text": m.group("num")})
        units.append(m.group("unit"))

    if unparsed:
        # 约束 1：部分解析出的趋势图会被当作完整趋势看。
        return {"points": [], "unit": "", "unparsed": unparsed,
                "refusal": f"{len(unparsed)} 个区间无法确定性解析"}
    distinct = {u for u in units if u}
    if len(distinct) > 1:
        # 约束 2：单位混用时图形本身就是错的，且错得看不出来。
        return {"points": [], "unit": "", "unparsed": [],
                "refusal": f"同一序列出现多个单位（{'、'.join(sorted(distinct))}）"}
    if len(points) < 2:
        return {"points": [], "unit": "", "unparsed": [],
                "refusal": f"只有 {len(points)} 个区间，不构成趋势"}
    return {"points": points, "unit": (units[0] if units else ""),
            "unparsed": [], "refusal": ""}


def _check_by_id(checks: List[Dict[str, Any]], field_id: str) -> Optional[Dict[str, Any]]:
    for check in checks or []:
        if str(check.get("field_id")) == field_id:
            return check
    return None


def build_trend_charts(state: Dict[str, Any]) -> Tuple[List[Dict[str, Any]],
                                                       List[Dict[str, Any]]]:
    """从 A 层已核实的财务字段解析趋势图。

    只读 `field_checks`，不读原始档案——档案里可能有清单没采信的取值，
    用它画图就制造了第二处口径（BC-31 / BC-52 同形）。

    Returns: (charts, skipped)  `skipped` 逐条说明为什么没画。
    """
    checks = state.get("field_checks") or []
    charts: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for field_id, chart_type in TREND_FIELDS:
        check = _check_by_id(checks, field_id)
        if check is None:
            continue                       # 本场景没有这一项，不是失败
        name = check.get("field_name") or field_id
        if check.get("status") != "verified":
            skipped.append({"field_id": field_id, "field_name": name,
                            "reason": f"该字段状态为{check.get('status') or 'unverified'}，"
                                      f"未核实的取值不得绘图"})
            continue
        parsed = parse_period_series(check.get("value"))
        if parsed["refusal"]:
            skipped.append({"field_id": field_id, "field_name": name,
                            "reason": parsed["refusal"]})
            continue
        charts.append(make_chart(
            CHART_DETERMINISTIC, chart_type, f"{name}趋势",
            unit=parsed["unit"],
            series=parsed["points"],
            provenance={
                "field_id": field_id,
                "field_name": name,
                "source_value": str(check.get("value") or ""),
                "evidence_ids": list(check.get("evidence_ids") or []),
                "source_adapter": check.get("source_adapter") or "",
                "as_of_date": check.get("as_of_date") or "",
            },
        ))
    return charts, skipped


def build_completeness_chart(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """核查完整度按维度构成图。

    这张图画的是**信息缺口本身**——它是唯一一张"没查到"比"查到"更该被
    看见的图。放在 B 层是因为它不参与裁决（闸门早已按 completeness 算过），
    但它让读者一眼看出缺口集中在哪个维度。
    """
    by_category = (state.get("completeness") or {}).get("by_category") or {}
    if not by_category:
        return None
    series = []
    for category, row in by_category.items():
        total = int(row.get("total") or 0)
        verified = int(row.get("verified") or 0)
        if total <= 0:
            continue
        series.append({"period": category, "value": verified,
                       "total": total, "missing": max(total - verified, 0)})
    if not series:
        return None
    return make_chart(
        CHART_DETERMINISTIC, "stacked_bar", "各维度核查完整度",
        unit="项",
        series=series,
        subtitle="深色为已核实项，浅色为信息缺口",
        provenance={
            "field_id": "__completeness__",
            "field_name": "核查完整度",
            "source_value": "由 field_checks 逐项统计",
            "evidence_ids": [],
        },
    )


def build_guarantee_graph_chart(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """担保关系图谱——确定性图谱的第一个实例。

    数据来自 `graph_analysis` 适配器落在证据库里的推导记录，不是模型输出。
    评审会问"凭什么说它涉入担保圈"，答案是这张图上的每条边都有金额与期间，
    并挂在一个 evidence_id 上（`guarantee_graph.py` 的设计前提）。

    ⚠️ 遍历不完整时该字段本就落成 `unverified`，此处也就取不到证据——
    这是对的：一张缺边的担保图会被读成"就这些关系"。
    """
    store = state.get("evidence_store") or {}
    for evidence in store.values():
        if not isinstance(evidence, dict) or not evidence.get("active"):
            continue
        if evidence.get("field_id") != "guarantee_circle":
            continue
        raw = evidence.get("raw") or {}
        graph = raw.get("knowledge_graph") or {}
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if not nodes:
            continue
        circles = raw.get("circle_detail") or []
        return make_chart(
            CHART_DETERMINISTIC, "graph", "对外担保关系图谱",
            graph={"nodes": nodes[:MAX_GRAPH_NODES], "edges": edges[:MAX_GRAPH_EDGES]},
            subtitle=(f"检出 {len(circles)} 个担保环" if circles else "未检出担保环"),
            provenance={
                "field_id": "guarantee_circle",
                "field_name": "担保圈",
                "source_value": str(raw.get("describe") or ""),
                "evidence_ids": [evidence.get("evidence_id", "")],
                "source_adapter": evidence.get("source_adapter") or "",
                "traversal_complete": bool(raw.get("traversal_complete")),
            },
        )
    return None


def build_deterministic_charts(state: Dict[str, Any]) -> Tuple[List[Dict[str, Any]],
                                                               List[Dict[str, Any]]]:
    """全部确定性图表。纯代码，不调用 LLM，因而不会失败也不需要超时。"""
    charts, skipped = build_trend_charts(state)
    for builder in (build_completeness_chart, build_guarantee_graph_chart):
        chart = builder(state)
        if chart is not None:
            charts.append(chart)
    return charts, skipped


# ---------------------------------------------------------------- 探索性接纳

#: 从**描述**里拆出来的碎词的最短长度。
#:
#: 描述是一句话，`re.split` 拆出来的东西没经过挑选，会出现「客户」这种
#: 两字通用词——而客户结构恰恰是清单覆盖不到、最该由调查层补的东西。
#: 字段名不受此限（见下）：那是清单作者精挑过的术语，短也精确。
MIN_TERM_CHARS = 4

#: 去掉主体名与清单术语之后，还剩多少属于这条陈述自己的信息。
#: 少于这个数就说明它整条都在讲那个清单字段。
MIN_DISTINCTIVE_CHARS = 16

#: 复述判定的片段长度与命中比例。5 字连续片段有 60% 落在**同一条**已核实
#: 取值里，就认定它在复述那个取值。跨条目拼出来的命中不算——
#: 那会把两个无关字段的尾首相接误判成复述。
SHINGLE = 5
SHINGLE_HIT_RATIO = 0.6

#: 复述判定前先抹掉的连接词与标点。逐个列出而不是用笼统的 \W——
#: \W 会把数字与字母也当成边界，而信用代码、金额恰恰是最该保留的特征。
#: 空白与引号用 chr() 写，避免转义序列在编辑链路上被二次展开。
_NOISE_CHARS = ("的是为，。；：、,.;:（）()[]「」"
                + chr(8220) + chr(8221) + chr(39) + chr(34)
                + chr(32) + chr(9) + chr(10))
_NOISE = re.compile("[" + re.escape(_NOISE_CHARS) + "]+")

_VOCABULARY: Optional[List[str]] = None


def _checklist_vocabulary() -> List[str]:
    """清单术语表，**从清单定义派生**而不是手写一份。

    手写的那份会与清单分叉：新增字段时没人记得回来补。这里取每一项的
    `field_name` 与 `description`——描述里逐项列着子项
    （`registration` 的描述就写着"统一社会信用代码、注册资本、实缴资本、
    成立日期、法定代表人、注册地址、企业类型"），正是模型最爱复述的那些。
    """
    global _VOCABULARY
    if _VOCABULARY is not None:
        return _VOCABULARY
    try:
        from config.dd_checklist import CHECKLIST, SCENARIO_CHECKLISTS
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.config.dd_checklist import (  # type: ignore
            CHECKLIST, SCENARIO_CHECKLISTS,
        )
    items = list(CHECKLIST)
    for extension in SCENARIO_CHECKLISTS.values():
        items.extend(extension)
    terms = set()
    for item in items:
        # 字段名**无条件收录**：`净利润`『担保圈』`存货` 都短于门槛，
        # 但它们是清单作者精挑过的术语，漏掉就等于判据对这些字段静默失效。
        # 过严的风险由「去掉术语后还剩多少自有信息」那一关兜住，不靠长度。
        if item.field_name:
            terms.add(item.field_name.strip())
        for token in re.split(r"[、，,。；;：:（）()/]", item.description or ""):
            token = token.strip()
            if len(token) >= MIN_TERM_CHARS:
                terms.add(token)
    _VOCABULARY = sorted((t for t in terms if t), key=len, reverse=True)
    return _VOCABULARY


def _verified_values(field_checks: List[Dict[str, Any]]) -> List[str]:
    return [_NOISE.sub("", str(c.get("value") or ""))
            for c in field_checks or []
            if c.get("status") == "verified" and c.get("value")]


def restates_checklist_field(
    claim: str,
    field_checks: List[Dict[str, Any]],
    subject: str,
) -> str:
    """这条陈述是不是在重复清单已经处理过的东西？返回原因，空串表示不是。

    ## 为什么必须是代码判据

    提示词里原本写着「不要重复清单字段……写了也会被丢弃」。
    **那是一句承诺，而代码里没有任何地方执行它**——2026-08-22 带语料的
    真实运行里，模型交出的 6 条发现全部是清单字段：法定代表人、注册地址、
    成立日期、注册资本、登记状态、统一社会信用代码。

    后果不是"多了几条废话"，而是**同一份报告里同一批事实出现两次、
    挂着相反的可信度标签**：信用代码在 A 层标「已核实」，在 B 层标
    「未经核实」。这比没有 B 层更糟——它让读者怀疑 A 层的核实算不算数。

    ## 两条判据，分别针对两种复述形态

    1. **复述取值**：陈述的连续片段大量落在某一条已核实取值里
       （"法定代表人郑允升" 整个出现在 registration 的取值中）
    2. **整条在讲那个字段**：陈述里出现清单术语，去掉主体名与该术语后
       几乎不剩自己的信息（"登记状态为存续" 去掉后只剩"存续"）

    两条都不命中才放行。**「主要客户为三家整车厂，合计占营业收入约六成」
    虽然提到了"营业收入"，但去掉之后仍有大量自有信息，因此放行**——
    这正是调查层该补的东西，判据不能把它一起拦掉。
    """
    body = _NOISE.sub("", (claim or "").replace(subject or "\0", ""))
    if len(body) < MIN_TERM_CHARS:
        return "陈述去掉主体名后几乎没有内容"

    # 判据一：复述某一条已核实取值
    grams = [body[i:i + SHINGLE] for i in range(len(body) - SHINGLE + 1)]
    if grams:
        for value in _verified_values(field_checks):
            hits = sum(1 for g in grams if g in value)
            if hits / len(grams) >= SHINGLE_HIT_RATIO:
                return "复述已核实字段的取值，清单里已有权威表述"

    # 判据二：整条都在讲某个清单字段
    for term in _checklist_vocabulary():
        if term in body:
            remainder = body.replace(term, "")
            if len(remainder) < MIN_DISTINCTIVE_CHARS:
                return f"整条陈述都在讲清单字段「{term}」，该项结论以清单为准"
    return ""


def _source_index(raw: Any) -> Optional[int]:
    try:
        return int(raw) - 1
    except (TypeError, ValueError):
        return None


def admit_finding(
    raw: Dict[str, Any],
    sources: List[Dict[str, Any]],
    subject: str,
    as_of: str,
    field_checks: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """B 层发现的准入判定。**代码判，不采信模型自报。**

    模型会自报 `subject_confirmed: true`——那正是不能采信它的理由。
    这里复用 A 层的同一判据（`_document_identity_zone` + `_subject_aliases`），
    对**来源文档**做确认，而不是对模型的声明做确认（红线 3）。

    Returns: (finding, reject_reason)  两者恰有其一非空。
    """
    claim = str(raw.get("claim") or "").strip()
    if len(claim) < 8:
        return None, "陈述过短，无法核对"

    # 冲突识别由**代码**做，且必须排在去重闸门之前（阶段 2 路 B）。
    #
    # 顺序不能反：BC-73 的去重闸门会丢掉"讲清单字段"的发现，
    # 而**推翻清单字段的发现恰好也讲清单字段**——两者表面同形。
    # 先判冲突、再判重复，才分得开"复述清单"与"推翻清单"。
    contradiction: Dict[str, Any] = {}
    if field_checks is not None:
        contradiction = detect_contradiction(
            {**raw, "claim": claim}, field_checks) or {}

    # 清单已经处理过的东西不得在调查层再说一遍（BC-73）。
    # `field_checks` 缺省时跳过：关系边走的是另一条语义，不做复述判定。
    #
    # ⚠️ 只有**真矛盾**才豁免去重，`undecidable` 不行。
    #    「我比不了」不是放行复述的理由——第一版让它也豁免，
    #    于是「登记状态为存续」这种纯复述被放了进来，等于 BC-73 白修。
    #    （测试当场拦下：`test_a_plain_restatement_is_still_dropped`。）
    exempt = contradiction.get("kind") == _verified_negative_kind()
    if field_checks is not None and not exempt:
        duplicate = restates_checklist_field(claim, field_checks, subject)
        if duplicate:
            return None, duplicate

    index = _source_index(raw.get("source_result_index"))
    if index is None or not (0 <= index < len(sources)):
        # 红线：B 层每条断言强制带来源。指不到来源的断言就是无源之谈。
        return None, "未指向可解析的来源"
    source = sources[index]

    if subject:
        identity_zone, aliases_of = _subject_predicates()
        identity = identity_zone(source, str(source.get("summary") or ""))
        if not any(alias in identity for alias in aliases_of(subject)):
            return None, "来源文档无法确认属于当前尽调主体"

    published = str(source.get("date") or "")[:10]
    cutoff = str(as_of or "")[:10]
    if published and cutoff and published > cutoff:
        return None, f"来源日期晚于研究截止日（{published} > {cutoff}）"

    # 阶段 2：模型自报的判定字段原样带上，**收敛与判定都不在这里做**。
    # 准入只回答"这条能不能进报告"，一致性判定是 `cross_layer_verdict` 的活；
    # 两件事混在一个函数里，以后就分不清某条被丢是因为没来源还是因为判定。
    judgment = normalize_judgment(raw)
    return {
        "claim": claim[:MAX_CLAIM_CHARS],
        "dimension": judgment["dimension"],
        "direction": judgment["direction"],
        "materiality": judgment["materiality"],
        "source": {
            "title": str(source.get("title") or "")[:120],
            "url": str(source.get("url") or ""),
            "publisher": str(source.get("site_name") or source.get("doc_name") or "")[:80],
            "published_at": published,
            "retrieved_at": str(source.get("retrieved_at") or ""),
        },
        # 由代码判定后写入，不是模型自报的那一个
        "subject_confirmed": True,
        "verified": False,
        # 模型只说"这条讲的是哪个字段"；是否构成推翻由 `contradiction` 载明，
        # 而它是代码算出来的（BC-29 同一条分工）。
        "field_id": str(raw.get("field_id") or "").strip(),
        "contradiction": contradiction,
    }, ""


def build_graph_from_relations(
    relations: List[Dict[str, Any]],
    subject: str,
    sources: Optional[List[Dict[str, Any]]] = None,
    as_of: str = "",
) -> Tuple[Dict[str, Any], int]:
    """把模型给出的实体关系收敛成有界图谱。**每条边必须带来源。**

    ## 为什么边也要过准入

    隔离要求 2 是"B 层每条断言强制带来源与获取时间"。一条关系边就是一条
    断言——"A 是 B 的客户"与写成文字的同一句话没有区别，而画成图之后它
    **更容易被采信**（红线 2 的同一理由）。原实现让边免检，是个漏洞。

    因此边走与 `admit_finding` 相同的三道判据：来源可解析、来源文档属于
    本主体、发布日期不晚于研究截止日。`sources` 缺省时退回只做形状校验——
    仅供单测构造，生产路径一律传入。

    ## 一个已知弱点，写在这里而不是藏着

    不做实体消歧。`guarantee_graph` 里已写明按名称对齐会合并同名主体
    （BC-10 同类风险）。探索性图谱有同样的弱点，这也正是它只能是
    探索性的原因之一。

    Returns: (graph, rejected)
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    rejected = 0
    for rel in relations or []:
        if not isinstance(rel, dict):
            rejected += 1
            continue
        src = str(rel.get("source") or "").strip()
        dst = str(rel.get("target") or "").strip()
        if not src or not dst or src == dst:
            rejected += 1
            continue

        origin: Dict[str, Any] = {}
        if sources is not None:
            admitted, _ = admit_finding(
                {"claim": f"{src} 与 {dst} 存在{rel.get('relation') or '关联'}关系",
                 "source_result_index": rel.get("source_result_index")},
                sources, subject, as_of)
            if admitted is None:
                rejected += 1
                continue
            origin = admitted["source"]

        for name in (src, dst):
            nodes.setdefault(name, {
                "id": name, "label": name, "type": "entity",
                "is_subject": bool(subject) and name == subject,
                "exploratory": True,
            })
        edges.append({
            "source": src, "target": dst,
            "relation": str(rel.get("relation") or "关联")[:20],
            "exploratory": True,
            "origin": origin,
        })
        if len(edges) >= MAX_GRAPH_EDGES or len(nodes) >= MAX_GRAPH_NODES:
            break
    return ({"nodes": list(nodes.values())[:MAX_GRAPH_NODES],
             "edges": edges[:MAX_GRAPH_EDGES]}, rejected)


# ---------------------------------------------------------------- 章节渲染

def _fmt_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f"{number:g}"


def _render_chart(chart: Dict[str, Any]) -> List[str]:
    lines = [f"**{chart['title']}**"]
    if chart.get("subtitle"):
        lines.append(f"　{chart['subtitle']}")
    if chart.get("chart_type") == "graph":
        graph = chart.get("graph") or {}
        lines.append(f"　节点 {len(graph.get('nodes') or [])} 个，"
                     f"关系 {len(graph.get('edges') or [])} 条")
        for edge in (graph.get("edges") or [])[:8]:
            amount = edge.get("amount")
            tail = (f"，{_fmt_number(amount)}{edge.get('unit') or ''}"
                    if amount not in (None, "") else "")
            lines.append(f"　- {edge.get('source')} —{edge.get('relation')}→ "
                         f"{edge.get('target')}{tail}")
    elif chart.get("chart_type") == "stacked_bar":
        # 构成图不是时间序列。用箭头连起来会被读成趋势——
        # 图表类型决定读法，渲染必须跟着类型走。
        lines.append("　" + "　｜　".join(
            f"{p.get('period')} {_fmt_number(p.get('value'))}/{_fmt_number(p.get('total'))}"
            for p in (chart.get("series") or [])[:12]
        ))
    else:
        unit = chart.get("unit") or ""
        rendered = "　→　".join(
            f"{p.get('period')} {p.get('text') or _fmt_number(p.get('value'))}{unit}"
            for p in (chart.get("series") or [])[:12]
        )
        if rendered:
            lines.append(f"　{rendered}")
    provenance = chart.get("provenance") or {}
    if chart.get("chart_class") == CHART_DETERMINISTIC:
        evidence = "、".join(provenance.get("evidence_ids") or []) or "—"
        lines.append(f"　依据字段：{provenance.get('field_name')}"
                     f"（{provenance.get('field_id')}）；证据编号：{evidence}")
    else:
        sources = provenance.get("sources") or []
        lines.append("　来源：" + "；".join(
            f"{s.get('title') or s.get('url') or '未标注'}"
            f"（{s.get('published_at') or '日期未标注'}）" for s in sources[:3]))
    return lines


_FAILURE_KIND_TEXT = {
    "not_found": "查了没有",
    "error": "没查成",
    "disabled": "本次未启用",
}


def render_investigation_section(state: Dict[str, Any]) -> str:
    """渲染 B 层章节。空内容时返回空串，由调用方决定是否插入。

    章节整体带锚点，供 `_canonicalize_investigation_section` 收敛——
    与评级块、证据附录同一策略：模型改写过的版本一律切除（BC-50）。
    """
    box = state.get("investigation")
    if not isinstance(box, dict):
        return ""
    charts = box.get("charts") or []
    findings = box.get("findings") or []
    failures = box.get("failures") or []
    if not (charts or findings or failures):
        return ""

    lines = [
        f"**{SECTION_MARKER}，由系统生成）**",
        "",
        "> 本章**不进入**风险评级、授信额度与证据溯源附录，也不构成授信依据。",
        "> 确定性内容解析自上文已核实字段，取值与证据附录同源；"
        "带 " + EXPLORATORY_BADGE + " 的内容来自尚未通过证据闸门的调查材料。",
    ]

    deterministic = [c for c in charts if c.get("chart_class") == CHART_DETERMINISTIC]
    exploratory = [c for c in charts if c.get("chart_class") == CHART_EXPLORATORY]

    if deterministic:
        lines += ["", "### 确定性图表（数据取自已核实字段）", ""]
        for chart in deterministic:
            lines += _render_chart(chart) + [""]

    if exploratory or findings:
        lines += ["", "### 探索性调查发现（未经核实，不作为授信依据）", ""]
        for chart in exploratory:
            lines += _render_chart(chart) + [""]
        graph = box.get("graph") or {}
        if graph.get("edges"):
            lines.append(f"{EXPLORATORY_BADGE}**实体关系图谱**："
                         f"节点 {len(graph.get('nodes') or [])} 个，"
                         f"关系 {len(graph['edges'])} 条")
            for edge in graph["edges"][:8]:
                origin = edge.get("origin") or {}
                where = origin.get("publisher") or origin.get("title") or "未标注"
                lines.append(f"　- {edge.get('source')} —{edge.get('relation')}→ "
                             f"{edge.get('target')}（来源：{where}）")
            lines.append("")

        for finding in findings[:MAX_FINDINGS]:
            source = finding.get("source") or {}
            lines.append(
                f"- {EXPLORATORY_BADGE}{finding.get('claim')}"
                f"（来源：{source.get('publisher') or source.get('title') or '未标注'}；"
                f"发布日期：{source.get('published_at') or '未标注'}；"
                f"获取时间：{source.get('retrieved_at') or '未标注'}）"
            )
        if findings:
            lines.append("")

    lines += _render_cross_layer_verdict(state)

    if failures:
        # 少一节必须写明原因，且区分"查了没有"与"没查成"（BC-51）。
        lines += ["", "### 本层未能产出的部分", ""]
        for failure in failures[:MAX_FINDINGS]:
            kind = _FAILURE_KIND_TEXT.get(failure.get("kind"), "没查成")
            detail = f"；{failure['detail']}" if failure.get("detail") else ""
            lines.append(f"- {failure.get('stage')}：{kind}——{failure.get('reason')}{detail}")

    lines += ["", SECTION_END]
    return "\n".join(lines)


def _render_cross_layer_verdict(state: Dict[str, Any]) -> List[str]:
    """渲染跨层一致性判定（阶段 2）。

    放在调查层章节内，是因为它评的是调查层的发现；
    但措辞必须让读者一眼看出**它不改变授信结论**——
    一个写在报告里的"存在实质挑战"，很容易被读成"这笔不能放"。
    """
    verdict = state.get("cross_layer_verdict")
    if not verdict:
        return []
    try:
        from service.cross_layer_verdict import render_markdown
    except ImportError:  # 兼容以 app 为包根的导入方式
        from app.service.cross_layer_verdict import render_markdown  # type: ignore
    return render_markdown(verdict)


def excise_investigation_section(text: str) -> str:
    """切除正文中的 B 层章节，保留其余内容。

    与 `_excise_risk_block` 同一 fail-closed 策略：只找到起始锚点时
    不保留残块——一段被截断的"未经核实"声明比整块丢失危险得多，
    读者会看不到那句声明却看得到内容。
    """
    start = text.find(SECTION_MARKER)
    if start < 0:
        return text.strip()
    line_start = text.rfind("\n", 0, start) + 1
    head = text[:line_start].rstrip()
    end = text.find(SECTION_END, start)
    tail = text[end + len(SECTION_END):].strip() if end >= 0 else ""
    return "\n\n".join(p for p in (head, tail) if p)


def canonicalize_investigation_section(text: str, block: str) -> str:
    """把正文中的 B 层章节收敛为一份系统生成的原文。

    位置固定在正文末尾、证据附录之前——附录由 `canonicalize_appendix`
    另行置底，两者互不干扰，最终顺序恒为 A 层 → B 层 → 附录。
    """
    body = excise_investigation_section(text)
    while SECTION_MARKER in body:
        stripped = excise_investigation_section(body)
        if stripped == body:
            break
        body = stripped
    if not block:
        return body
    return "\n\n".join(p for p in (body.rstrip(), "---", block) if p)


# ---------------------------------------------------------------- 探索性摄入

#: B 层抽取提示词。**与 A 层的抽取提示词完全分开，是一次独立的模型调用。**
#:
#: 为什么不合并进 Scout 那一次调用（省一次费用、省一半延迟）：
#: 阶段 1 的验收标准是"同一主体跑两遍，A 层的等级、额度、核实率逐位不变"。
#: 往 A 层那次调用的输出契约里加字段，会改变它的注意力分配与 token 预算——
#: BC-56 实测过这类改动对字段抽取的影响。合并省下的那次调用，
#: 代价是**再也无法证明 A 层没被动过**。
EXPLORATORY_PROMPT = """你在为一份贷前尽职调查报告补充**调查层**内容。

## 你的产出不进入授信决策

风险等级、授信额度、核查清单已由规则引擎另行确定，与你无关，你也无权改动。
你补充的是清单覆盖不到、但信贷经理关心的东西：行业地位、客户与供应商结构、
产能与在建项目、上下游议价能力、经营事件。

## 三条硬性要求

1. **每条陈述必须给出 source_result_index**，指向下方材料的编号。
   指不出来源的陈述会被系统丢弃，不要写。
2. **只写材料里明确写着的内容。** 不要综合、不要推断、不要给出
   "经营稳健""前景良好"这类判断性结论——那是评级的事，不是你的事。
3. **不要重复清单字段。** 工商登记、股东、营收、净利润、资产负债率、
   涉诉、担保这些已由清单处理——**系统会自动丢弃这类条目**，
   写了不会进报告，只会浪费你的输出。

尽调主体：{subject}
研究截止日：{as_of}

## 材料

{sources}

## 输出 JSON

```json
{{
  "findings": [
    {{"claim": "材料中明确写着的一条事实陈述",
      "dimension": "operation|relation|opinion|financial|judicial",
      "direction": "aggravating（对授信不利）| mitigating（对授信有利）",
      "materiality": "high|medium|low",
      "field_id": "这条讲的是哪个清单字段的 id；与清单无关则填 null",
      "source_result_index": 1}}
  ],
  "relations": [
    {{"source": "实体A", "target": "实体B",
      "relation": "客户|供应商|子公司|参股|合作", "source_result_index": 1}}
  ],
  "metrics": [
    {{"name": "指标名（不得是清单已有字段）", "unit": "万元",
      "source_result_index": 1,
      "points": [{{"period": "2024年度", "value": 1234.5}}]}}
  ]
}}
```

**findings 最多写 {max_findings} 条**，挑最有信息量的写——
超出的部分不会进报告，而且过长的输出会被截断，导致整份作废。

三个数组都可以为空。**材料里没有就交空数组**，这不是失败。

## 关于 direction / materiality / field_id

这三个字段供系统做一致性判定用，**你的填写不是最终判定**——
系统会用规则复核，正面发现也不会因为你标了 high 就改变授信结论。
所以请如实标注，不必为了让某条显得重要而拔高。

`field_id` 只回答一件事：**这条陈述讲的是哪个清单字段**。
你不需要判断它和清单的结论一致还是矛盾——那由系统比对，不由你判断。
如实填即可；与二十项清单都无关的内容填 null。

可用的字段 id：registration、business_scope、operating_status、shareholders、
actual_controller、external_investment、bidding_record、revenue、net_profit、
debt_ratio、cash_flow、litigation、enforcement、dishonesty、equity_freeze、
guarantee、guarantee_circle、related_party、negative_news、regulatory_penalty。"""


def format_sources_for_prompt(sources: List[Dict[str, Any]]) -> str:
    """把留存的来源摘录编号后喂给模型。编号即 `source_result_index`。"""
    blocks = []
    for i, source in enumerate(sources, start=1):
        blocks.append(
            f"[{i}] 标题：{source.get('title') or '(无标题)'}\n"
            f"    来源：{source.get('site_name') or source.get('doc_name') or '未标注'}"
            f"　日期：{source.get('date') or '未标注'}\n"
            f"    正文：{str(source.get('summary') or '')[:1200]}"
        )
    return "\n\n".join(blocks)


def ingest_exploratory_payload(
    state: Dict[str, Any],
    payload: Dict[str, Any],
    sources: List[Dict[str, Any]],
) -> Dict[str, int]:
    """把模型输出经准入判定后写入 B 层。**纯函数，可脱离 LLM 单测。**

    准入失败不是错误：材料里确实没有可写的东西是常态。但被拒的条数要
    记下来——全部被拒和模型返回空数组是两回事，前者说明判据或提示词有问题。

    Returns: {"findings": n, "rejected": n, "metrics": n, "relations": n}
    """
    box = get_investigation(state)
    subject = str(state.get("company_name") or state.get("subject_name") or "").strip()
    as_of = str(state.get("as_of") or "")

    admitted, rejected = 0, 0
    reject_reasons: Dict[str, int] = {}
    for raw in (payload.get("findings") or [])[:MAX_FINDINGS * 2]:
        if not isinstance(raw, dict):
            continue
        finding, reason = admit_finding(raw, sources, subject, as_of,
                                        field_checks=state.get("field_checks") or [])
        if finding is None:
            rejected += 1
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
            continue
        box["findings"].append(finding)
        admitted += 1
        if admitted >= MAX_FINDINGS:
            break

    metrics = 0
    for raw in (payload.get("metrics") or []):
        if not isinstance(raw, dict) or metrics >= MAX_EXPLORATORY_CHARTS:
            break
        points = [
            {"period": str(p.get("period") or ""), "value": float(p.get("value"))}
            for p in (raw.get("points") or [])
            if isinstance(p, dict) and _is_number(p.get("value"))
        ]
        if len(points) < 2:
            continue        # 与确定性图表同一门槛：不足两点不构成趋势
        index = _source_index(raw.get("source_result_index"))
        if index is None or not (0 <= index < len(sources)):
            rejected += 1
            reject_reasons["指标未指向可解析的来源"] = \
                reject_reasons.get("指标未指向可解析的来源", 0) + 1
            continue
        source = sources[index]
        box["charts"].append(make_chart(
            CHART_EXPLORATORY, "line", str(raw.get("name") or "未命名指标")[:40],
            unit=str(raw.get("unit") or "")[:8],
            series=points,
            provenance={"sources": [{
                "title": str(source.get("title") or "")[:120],
                "url": str(source.get("url") or ""),
                "published_at": str(source.get("date") or "")[:10],
                "retrieved_at": str(source.get("retrieved_at") or ""),
            }]},
        ))
        metrics += 1

    graph, graph_rejected = build_graph_from_relations(
        payload.get("relations") or [], subject, sources, as_of)
    rejected += graph_rejected
    if graph_rejected:
        reject_reasons["关系边未通过来源/主体判据"] = graph_rejected
    if graph["nodes"]:
        box["graph"] = graph

    if rejected:
        detail = "；".join(f"{reason}×{count}"
                           for reason, count in sorted(reject_reasons.items(),
                                                       key=lambda kv: -kv[1])[:4])
        record_failure(state, "探索性发现准入",
                       f"{rejected} 条候选未通过准入判定", kind="not_found",
                       detail=detail)
    return {"findings": admitted, "rejected": rejected,
            "metrics": metrics, "relations": len(graph["edges"])}


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
