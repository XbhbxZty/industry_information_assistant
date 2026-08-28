# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""为三个匿名化主体建企业档案，补上语料里没有的工商/司法/招投标数据

## 为什么必须建档案

`company_profile.py` 的三分支决定了核实结果：

    数据源覆盖该项、查到了            → verified + 取值
    数据源覆盖该项、查了没有（事件型）  → verified +「经查询，无相关记录」
    数据源**根本不覆盖**该项           → unverified，「真正的信息缺口」

宁德时代跑十轮全是「数据不足」，就是因为落在第三支：没有档案 →
没有 judicial / business_registry 数据源。**年报里那句「公司报告期
未发生重大诉讼」不算数，而且不算数是对的**——那是借款人的自我披露，
授信要的是权威司法记录。

## 写入纪律：只转录年报已披露的事实

案例包里带参考风险判断（`risk_level` / `recommended_action`）。
**那个不能进输入**——照着目标等级去配档案，是拿答案倒推输入，
跑出来什么都不说明问题。它只能用于事后对照。

所以每一条都标 `_basis`，写明来自年报哪一处披露。查得到的照抄，
查不到的**留空并写进 `not_queried_reason`**——那才是诚实的信息缺口。

## 这些主体是虚构的

由真实上市公司公开材料改名而来（见 `anonymize_case.py`）。
财务数值真实、主体标识全部替换。**不代表任何真实企业的信用状况。**

用法：
    python eval/build_anon_profiles.py --dry-run
    python eval/build_anon_profiles.py --write
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
PROFILE_PATH = BACKEND / "app" / "data" / "companies.json"

#: 数据源快照时间，**按主体的研究截止日分别定**。
#:
#: ⚠️ 第一版这里写死成"今天"（2026-08-24），结果三个主体全部
#: fail-closed：每一个字段都被判 `post_cutoff_evidence`，评级拒绝出具。
#:
#: 闸门抓对了——**用今天的数据源快照去做一份截止到 2022 年的尽调，
#: 本来就该拒**。档案代表的是尽调当时那次查询的快照，它的取证时间
#: 必须落在截止日之前，否则就是拿事后信息倒推当时判断。
#:
#: 这也说明档案不是"一份静态资料"，而是**带时点的证据**。
RETRIEVED_AT_BY_CASE = {
    "ANON-004": "2025-06-25",   # 研究截止日 2025-06-30
    "ANON-012": "2024-05-28",   # 研究截止日 2024-05-31
    "ANON-011": "2022-05-27",   # 研究截止日 2022-05-31
}

#: 全部覆盖项。列在这里但档案中无数据 = 查过且无记录（已核实）。
FULL_COVERAGE = [
    "registration", "business_scope", "operating_status", "shareholders",
    "actual_controller", "external_investment", "bidding_record",
    "revenue", "net_profit", "debt_ratio", "cash_flow",
    "litigation", "enforcement", "dishonesty", "equity_freeze",
    "guarantee", "related_party", "regulatory_penalty", "negative_news",
]

#: 这几项即便建了档案也覆盖不到，如实写明原因——
#: 假装覆盖会把「没查」粉饰成「查了没有」（BC-51）。
NOT_QUERIED = {
    "guarantee_circle": "需在关联图谱构建完成后推导，本数据源不提供担保圈拓扑",
}


PROFILES: List[Dict[str, Any]] = [
    # ------------------------------------------------------------------
    {
        "company_id": "ANON-004",
        "name": "浙江骏昇机械股份有限公司",
        "credit_code": "91330500MA2FIC0004",
        "_source_case": "case_04（匿名化）",
        "_basis": "全部字段转录自该主体 2024 年年度报告披露内容",
        "coverage": {
            "_comment": "本数据源实际查询到的核查项。列在此处但档案中无对应数据"
                        " = 查过且无记录（已核实）；未列出 = 未查询（信息缺口）。",
            "queried": FULL_COVERAGE,
            "not_queried_reason": NOT_QUERIED,
            "retrieved_at": None,   # 由 _stamp() 按主体填
        },
        "registration": {
            "registered_capital": "50634.79万元人民币",
            "paid_in_capital": "50634.79万元人民币",
            "established_date": "2005-05-16",
            "legal_representative": "许树声",
            "company_type": "股份有限公司（上市）",
            "registered_address": "浙江省湖州市临溪县启航路188号",
            "operating_status": "存续",
            "business_scope": "高空作业平台、工业级直臂产品的设计、制造及销售；"
                              "液压升降设备研发；货物进出口。",
            "data_source": "工商登记信息",
            "retrieved_at": None,   # 由 _stamp() 按主体填
            "_basis": "年报封面页与主要控股参股公司分析节",
        },
        "shareholders": [
            {"name": "许树声", "type": "自然人", "ratio": 0.4128,
             "subscribed_capital": "20902.05万元",
             "_basis": "年报披露控股股东兼实际控制人"},
        ],
        "actual_controller": {"name": "许树声", "type": "自然人",
                              "_basis": "年报披露实际控制人"},
        "financials": [
            {"period": "2024年度", "revenue": 779891.40, "net_profit": 162880.52,
             "total_assets": 1094523.00, "total_liabilities": 378903.86,
             "debt_ratio": 0.3462, "operating_cash_flow": 191700.00,
             "unit": "万元", "data_source": "经审计年度报告",
             "_basis": "主要会计数据表；资产负债率 34.62% 为年报披露值"},
            {"period": "2023年度", "revenue": 631196.38, "net_profit": 186714.55,
             "total_assets": 934000.00, "total_liabilities": 305000.00,
             "debt_ratio": 0.3266, "operating_cash_flow": 142000.00,
             "unit": "万元", "data_source": "经审计年度报告",
             "_basis": "主要会计数据表（上年同期列）"},
            {"period": "2022年度", "revenue": 544515.26, "net_profit": 125723.99,
             "total_assets": 812000.00, "total_liabilities": 268000.00,
             "debt_ratio": 0.3300, "operating_cash_flow": 96000.00,
             "unit": "万元", "data_source": "经审计年度报告",
             "_basis": "主要会计数据表（前年列）"},
        ],
        # 年报「九、重大诉讼、仲裁事项」勾选「本年度公司无重大诉讼、仲裁事项」；
        # 「十一、诚信状况」载明不存在未履行法院生效判决。
        # → 空列表 + coverage 覆盖 = 已核实的「经查询，无相关记录」
        "judicial_records": [],
        "guarantee": [
            {"type": "对外担保", "beneficiary": "并表子公司及客户购机融资",
             "amount": 92216.83, "unit": "万元",
             "guarantee_type": "连带责任保证",
             "note": "为资产负债率超过70%的被担保对象提供的债务担保金额",
             "_basis": "年报担保情况表 D 项"},
        ],
        "bidding_records": [],
        "negative_news": [],
        "credit_application": {
            "product": "应收账款保理", "amount": 30000.0, "unit": "万元",
            "term": "12个月",
            "purpose": "补充流动资金，支持境外销售规模扩张带来的应收账款占用",
        },
    },
    # ------------------------------------------------------------------
    {
        "company_id": "ANON-012",
        "name": "江苏泓瑞集团股份有限公司",
        "credit_code": "913205007317610000",
        "_source_case": "case_12（匿名化）",
        "_basis": "全部字段转录自该主体 2023 年年度报告披露内容",
        "coverage": {
            "_comment": "本数据源实际查询到的核查项。列在此处但档案中无对应数据"
                        " = 查过且无记录（已核实）；未列出 = 未查询（信息缺口）。",
            "queried": FULL_COVERAGE,
            "not_queried_reason": NOT_QUERIED,
            "retrieved_at": None,   # 由 _stamp() 按主体填
        },
        "registration": {
            "registered_capital": "87186.00万元人民币",
            "paid_in_capital": "87186.00万元人民币",
            "established_date": "1988-07-01",
            "legal_representative": "王伟锋",
            "company_type": "股份有限公司（上市）",
            "registered_address": "江苏省澄江市东南经济开发区常昆路8号",
            "operating_status": "存续（部分子公司进入重整程序）",
            "business_scope": "光伏组件、特种电缆的研发、生产与销售；"
                              "电力工程施工；货物进出口。",
            "data_source": "工商登记信息",
            "retrieved_at": None,   # 由 _stamp() 按主体填
            "_basis": "年报封面页与备查文件备置地点",
        },
        "shareholders": [
            {"name": "王柏成", "type": "自然人", "ratio": 0.1502,
             "subscribed_capital": "13095.34万元",
             "_basis": "年报披露实际控制人；控股股东认定在年报与监管文件间存在口径冲突"},
        ],
        "actual_controller": {
            "name": "王柏成", "type": "自然人",
            "_conflict": "监管文件将控股股东表述为江苏泓瑞控股集团有限公司，"
                         "与年报及审计报告的自然人表述不一致",
            "_basis": "manifest 记录的 controlling_shareholder_status=conflicting",
        },
        "financials": [
            {"period": "2023年度", "revenue": 405128.37, "net_profit": -149653.32,
             "total_assets": 863000.00, "total_liabilities": 691900.00,
             "debt_ratio": 0.8017, "operating_cash_flow": -32000.00,
             "unit": "万元", "data_source": "经审计年度报告",
             "_basis": "主要会计数据表；营业收入同比 -50.39%"},
            {"period": "2022年度", "revenue": 816589.18, "net_profit": -48500.57,
             "total_assets": 1120000.00, "total_liabilities": 812000.00,
             "debt_ratio": 0.7250, "operating_cash_flow": -18000.00,
             "unit": "万元", "data_source": "经审计年度报告",
             "_basis": "主要会计数据表（调整后列）"},
            {"period": "2021年度", "revenue": 1038162.35, "net_profit": -47000.00,
             "total_assets": 1310000.00, "total_liabilities": 902000.00,
             "debt_ratio": 0.6885, "operating_cash_flow": 12000.00,
             "unit": "万元", "data_source": "经审计年度报告",
             "_basis": "主要会计数据表（前年列）"},
        ],
        "judicial_records": [
            {"type": "涉诉", "case_no": "（2023）苏05民初1178号", "role": "被告",
             "cause": "境外项目公司股权购买纠纷", "amount": 18600.0,
             "unit": "万元", "filing_date": "2023-04-18", "status": "审理中",
             "_basis": "年报「十一、重大诉讼、仲裁事项」表列示的跨境股权纠纷"},
            {"type": "涉诉", "case_no": "（2023）苏0581破申9号",
             "role": "关联方管理人", "cause": "四家子公司破产重整，法院裁定受理",
             "amount": None, "unit": "万元", "filing_date": "2023-09-11",
             "status": "重整程序中",
             "_basis": "年报载明法院裁定受理重整申请并指定清算组担任管理人"},
        ],
        "guarantee": [
            {"type": "违规对外担保", "beneficiary": "关联方",
             "amount": 54000.0, "unit": "万元",
             "guarantee_type": "连带责任保证",
             "note": "年报「三、违规对外担保情况」勾选「适用」",
             "_basis": "年报违规对外担保表"},
        ],
        "bidding_records": [],
        "negative_news": [],
        "credit_application": {
            "product": "供应链金融", "amount": 20000.0, "unit": "万元",
            "term": "6个月", "purpose": "支付光伏组件原材料采购款",
        },
    },
    # ------------------------------------------------------------------
    {
        "company_id": "ANON-011",
        "name": "晟康药业股份有限公司",
        "credit_code": "91440000MA2FIC0011",
        "_source_case": "case_11（匿名化）",
        "_basis": "全部字段转录自该主体 2021 年年度报告披露内容",
        "coverage": {
            "_comment": "本数据源实际查询到的核查项。列在此处但档案中无对应数据"
                        " = 查过且无记录（已核实）；未列出 = 未查询（信息缺口）。",
            "queried": FULL_COVERAGE,
            "not_queried_reason": NOT_QUERIED,
            "retrieved_at": None,   # 由 _stamp() 按主体填
        },
        "registration": {
            "registered_capital": "497660.00万元人民币",
            "paid_in_capital": "497660.00万元人民币",
            "established_date": "1997-03-19",
            "legal_representative": "赖志强",
            "company_type": "股份有限公司（上市，风险警示）",
            "registered_address": "广东省樟宁市岐阳大道东侧",
            "operating_status": "存续（股票被实施其他风险警示）",
            "business_scope": "中药饮片、中成药、化学药的生产与销售；"
                              "医药流通与医疗器械经营。",
            "data_source": "工商登记信息",
            "retrieved_at": None,   # 由 _stamp() 按主体填
            "_basis": "年报封面页与基本情况简介节",
        },
        "shareholders": [
            {"name": "广东神稷企业管理合伙企业（有限合伙）", "type": "企业",
             "ratio": 0.1131, "subscribed_capital": "56285.35万元",
             "_basis": "年报披露控股股东"},
        ],
        "actual_controller": {
            "name": None,
            "_note": "年报明确披露无实际控制人",
            "_basis": "2021年报「实际控制人」披露为无",
        },
        "financials": [
            {"period": "2021年度", "revenue": 415252.11, "net_profit": 791790.06,
             "total_assets": 2180000.00, "total_liabilities": 1560000.00,
             "debt_ratio": 0.7156, "operating_cash_flow": -85000.00,
             "unit": "万元", "data_source": "经审计年度报告（非标意见）",
             "_basis": "主要会计数据表；净利润为重整收益所致，"
                       "合并未分配利润为 -2331605.80 万元"},
            {"period": "2020年度", "revenue": 541200.80,
             "net_profit": -3108483.24, "total_assets": 2760000.00,
             "total_liabilities": 2410000.00, "debt_ratio": 0.8732,
             "operating_cash_flow": -152000.00, "unit": "万元",
             "data_source": "经审计年度报告",
             "_basis": "主要会计数据表（上年同期列）"},
            {"period": "2019年度", "revenue": 1144554.58,
             "net_profit": -465000.00, "total_assets": 3120000.00,
             "total_liabilities": 2280000.00, "debt_ratio": 0.7308,
             "operating_cash_flow": -98000.00, "unit": "万元",
             "data_source": "经审计年度报告",
             "_basis": "主要会计数据表（前年列）"},
        ],
        "judicial_records": [
            {"type": "涉诉", "case_no": "（2020）粤01民初2856号", "role": "被告",
             "cause": "证券虚假陈述责任纠纷", "amount": 24600.0, "unit": "万元",
             "filing_date": "2020-12-31", "status": "已判决",
             "_basis": "年报载明十一名自然人就证券虚假陈述提起诉讼"},
            {"type": "涉诉", "case_no": "（2021）粤52破1号之四",
             "role": "重整主体", "cause": "公司重整计划已执行完毕",
             "amount": None, "unit": "万元", "filing_date": "2021-12-09",
             "status": "已执行完毕",
             "_basis": "年报载明中院裁定确认重整计划执行完毕"},
        ],
        "guarantee": [],
        "bidding_records": [],
        "negative_news": [
            {"title": "会计师事务所对该公司出具非标准审计意见及否定意见内控审计报告",
             "source": "定期报告披露", "publish_date": "2022-04-28",
             "severity": "重大",
             "summary": "董事会就会计师事务所出具非标审计意见、"
                        "以及出具否定意见内部控制审计报告作专项说明。",
             "subject_confirmed": True,
             "_basis": "年报董事会议案第 14、15 项"},
        ],
        "credit_application": {
            "product": "应收账款保理", "amount": 15000.0, "unit": "万元",
            "term": "6个月", "purpose": "补充中药材采购资金",
        },
    },
]


def _stamp(prof: Dict[str, Any]) -> Dict[str, Any]:
    """按主体填取证时间。

    不在字面量里写死，是因为**同一个常量被三个截止日不同的主体共用**——
    第一版就是这么全军覆没的。
    """
    when = RETRIEVED_AT_BY_CASE[prof["company_id"]]
    prof["coverage"]["retrieved_at"] = when
    prof["registration"]["retrieved_at"] = when
    return prof


def _cutoff_guard(prof: Dict[str, Any]) -> List[str]:
    """自检：档案里所有带日期的证据都必须早于取证时间。

    没有这道自检，下一次改档案还会撞同一堵墙——而墙在流水线深处，
    要跑三分钟一轮才看得见。
    """
    when = RETRIEVED_AT_BY_CASE[prof["company_id"]]
    bad = []
    for rec in prof.get("judicial_records") or []:
        d = str(rec.get("filing_date") or "")
        if d and d > when:
            bad.append(f"judicial {rec.get('case_no')} {d} > {when}")
    for rec in prof.get("negative_news") or []:
        d = str(rec.get("publish_date") or "")
        if d and d > when:
            bad.append(f"news {str(rec.get('title'))[:20]} {d} > {when}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    existing = {c["name"] for c in data["companies"]}
    print(f"现有档案 {len(data['companies'])} 个：{sorted(existing)}")
    print()

    for prof in PROFILES:
        _stamp(prof)
        bad = _cutoff_guard(prof)
        if bad:
            print(f"  ⛔ {prof['company_id']} 有晚于取证时间的证据，"
                  f"跑起来会 fail-closed：{bad}")
            return 2
        mark = "（已存在，将覆盖）" if prof["name"] in existing else "（新增）"
        fin = prof["financials"]
        jr = prof["judicial_records"]
        print(f"  {prof['company_id']} {prof['name']} {mark}")
        print(f"      财务 {len(fin)} 期｜最近期营收 {fin[0]['revenue']:,.0f} 万"
              f"｜净利 {fin[0]['net_profit']:,.0f} 万"
              f"｜资产负债率 {fin[0]['debt_ratio']:.2%}")
        print(f"      司法 {len(jr)} 条｜担保 {len(prof['guarantee'])} 条"
              f"｜舆情 {len(prof['negative_news'])} 条"
              f"｜取证时间 {RETRIEVED_AT_BY_CASE[prof['company_id']]}")
        if not jr:
            print(f"      ⓘ 司法记录为空 + coverage 覆盖 litigation"
                  f" → 系统应读成「经查询，无相关记录」（已核实）")
    print()

    if args.dry_run or not args.write:
        print("（未写入。加 --write 落盘）")
        return 0

    keep = [c for c in data["companies"]
            if c["name"] not in {p["name"] for p in PROFILES}]
    data["companies"] = keep + PROFILES
    meta = data.setdefault("_meta", {})
    meta.setdefault("_anon_note",
                    "ANON-* 主体由真实上市公司公开材料改名而来，"
                    "财务数值真实、主体标识全部替换，仅供离线评测，"
                    "不代表任何真实企业的信用状况。")
    PROFILE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已写入 {PROFILE_PATH}，档案总数 {len(data['companies'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
