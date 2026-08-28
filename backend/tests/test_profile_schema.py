# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""档案键名与取值词表必须被校验，否则「读不到」会被洗成「查过且没有」

## 这一轮在钉什么

档案里写 `guarantee_records`，评分层读 `guarantee`。取不到 → 空列表 →
输出「**未发现对外担保**」，relation 维度 0.0。

一家年报自己勾了「违规对外担保：适用」、有 5.4 亿违规担保的企业，
系统给出的是一个**肯定的否定结论**——不是报错，不是标未核实。

同理 `type: "破产重整"` 不在 `_score_judicial` 认识的三种类型里，
两条重整记录既不计分也不留痕：一家破产重整中的企业，
judicial 维度只拿到 10 分。

`risk_scorecard` 第 267 行的注释早写过这件事（"核实状态被当成了结论本身"），
那个洞为 `guarantee_circle` 修过，但这两处一直开着。

运行：cd backend && python -m pytest tests/test_profile_schema.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from service.profile_schema import (  # noqa: E402
    KNOWN_JUDICIAL_TYPES, KNOWN_PROFILE_KEYS, validate_all, validate_profile,
)

DATA = Path(__file__).resolve().parents[1] / "app" / "data" / "companies.json"


def _minimal(**over: Any) -> Dict[str, Any]:
    base = {"company_id": "T-1", "name": "测试主体有限公司",
            "credit_code": "91000000TEST0001", "coverage": {"queried": []},
            "registration": {}, "financials": [], "judicial_records": []}
    base.update(over)
    return base


# ------------------------------------------------- 直接回归


def test_未知顶层键必须被喊出来():
    """**本条的直接回归。** `guarantee_records` 不是 `guarantee`。"""
    problems = validate_profile(_minimal(guarantee_records=[{"amount": 54000}]))
    assert problems, (
        "未知键没有被喊出来——消费方读不到它，会把该项输出成「未发现」")
    assert "guarantee_records" in problems[0]


def test_未知司法类型必须被喊出来():
    bad = _minimal(judicial_records=[{"type": "破产重整", "case_no": "x"}])
    problems = validate_profile(bad)
    assert problems and "破产重整" in problems[0]
    assert "不留痕" in problems[0] or "漏掉" in problems[0], (
        "提示里要说清后果：整条被漏掉、既不计分也不留痕")


def test_提示要给出最接近的已知键():
    """光说「未知」不够——下一个人得知道该改成什么。"""
    problems = validate_profile(_minimal(judical_records=[]))
    assert problems
    assert "judicial_records" in problems[0], f"没有给出建议：{problems[0]}"


# ------------------------------------------------- 反面：不能过度报警


def test_下划线开头的元数据不算未知键():
    """`_basis`、`_source_case` 是注释性元数据。

    把它们报成未知键，校验就变成恒亮的灯——真问题跟着被忽略
    （本项目已因此栽过：BC-78 的通用词误报、BC-77 的等长夹具）。
    """
    assert validate_profile(_minimal(_basis="来自年报", _source_case="case_04")) == []


def test_合法档案不得报警():
    assert validate_profile(_minimal(
        guarantee=[{"beneficiary": "某子公司", "guarantee_type": "连带责任保证",
                    "amount": 100}],
        judicial_records=[{"type": "涉诉", "case_no": "x"}])) == []


def test_词表里的每种司法类型都放行():
    for kind in KNOWN_JUDICIAL_TYPES:
        assert validate_profile(
            _minimal(judicial_records=[{"type": kind, "case_no": "（2024）测民初1号"}])
        ) == [], f"{kind} 被误报"


def test_词表里的每个顶层键都放行():
    for key in KNOWN_PROFILE_KEYS:
        assert validate_profile(_minimal(**{key: [] if key.endswith("s") else {}})) == [], \
            f"{key} 被误报"


# ------------------------------------------------- 生产档案


def test_生产档案全部通过校验():
    """五份在用档案不得有未知键或未知类型。

    这条会在有人新增字段却忘了登记词表时立刻变红——
    而那正是本条 bad case 的成因。
    """
    data = json.loads(DATA.read_text(encoding="utf-8"))
    problems = validate_all(data["companies"])
    assert not problems, "生产档案校验失败：\n" + "\n".join(problems)


def test_加载器会拒绝带未知键的档案(tmp_path, monkeypatch):
    """校验失败必须**拒绝加载**，不是记条日志。

    日志没人看，错误的授信建议有人签字。
    """
    import service.company_profile as cp

    bad = {"companies": [_minimal(name="坏档案有限公司",
                                  guarantee_records=[{"amount": 1}]),
                         _minimal(name="好档案有限公司")]}
    f = tmp_path / "companies.json"
    f.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cp, "_DATA_FILE", f)

    names = [c["name"] for c in cp.list_companies()]
    assert "坏档案有限公司" not in names, "带未知键的档案被放行了"
    assert "好档案有限公司" in names, "不能因为一条坏档案把好的也丢掉"


# ------------------------------------------------- 夹具自检


def test_夹具里的未知键确实不在词表内():
    """先证明这组用例有鉴别力。"""
    assert "guarantee_records" not in KNOWN_PROFILE_KEYS
    assert "破产重整" not in KNOWN_JUDICIAL_TYPES
    assert "guarantee" in KNOWN_PROFILE_KEYS
    assert "涉诉" in KNOWN_JUDICIAL_TYPES


def test_记录缺必需子字段必须被喊出来():
    """**这条是崩溃那次的直接回归。**

    档案把被担保方写成 `counterparty`，而 `company_profile.py:384`
    是裸下标 `g['beneficiary']`——KeyError 冒到顶层，整轮研究挂掉，
    日志里只有一个字段名，看不出是哪份档案、哪条记录。

    顶层键校验挡不住它：`guarantee` 这个键名是对的，错的是记录内部。
    **校验做到哪一层，就只挡得住哪一层。**
    """
    problems = validate_profile(_minimal(
        guarantee=[{"counterparty": "关联方", "guarantee_type": "连带责任保证",
                    "amount": 54000}]))
    assert problems, "缺 beneficiary 没有被喊出来，跑起来会 KeyError 崩溃"
    assert "beneficiary" in problems[0]
    assert "counterparty" not in KNOWN_PROFILE_KEYS      # 夹具自检


def test_子字段校验覆盖每一类记录():
    """词表里声明了必需子字段的每一类，都要真的被检查到。"""
    from service.profile_schema import REQUIRED_RECORD_FIELDS
    for key, required in REQUIRED_RECORD_FIELDS.items():
        problems = validate_profile(_minimal(**{key: [{}]}))
        assert problems, f"{key} 的空记录没有被喊出来"
        assert required[0] in problems[0],             f"{key} 的提示没写清缺哪个字段：{problems[0]}"
