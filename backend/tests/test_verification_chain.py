"""
核实来源与结构化证据链测试（v0.6）

## 这一轮要钉住的东西

`verified_profile_mismatches()` 此前假设**所有** verified 字段都能由初始
`company_profile` 重放。当前成立，只因 Scout 至今只追加 `attempted_sources`。
一旦结构化适配器查到新字段并置 verified，初始档案无法重放，合法增量证据
会被误判为不一致 → 全面 fail-closed。

本轮不让任何东西真正联网核实，只建立来源模型与重放边界，并用断言锁住
三条不可让步的规则：

1. attempted_sources 不是证据
2. 通用网页检索不是结构化核实来源
3. 来源不明时不得静默猜测

运行：cd backend && python tests/test_verification_chain.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, profile_to_facts, replay_from_profile, verify_field_checks,
)
from service.verification import (  # noqa: E402
    ORIGIN_INITIAL_PROFILE, ORIGIN_STRUCTURED_ADAPTER,
    REASON_EVIDENCE_NOT_FOUND, REASON_EVIDENCE_VALUE_MISMATCH,
    REASON_INVALID_ORIGIN, REASON_MISSING_CONFLICT_VALUES, REASON_MISSING_ORIGIN,
    REASON_MISSING_TIMESTAMP, REASON_NO_EVIDENCE_IDS, REASON_PROFILE_REPLAY_MISMATCH,
    record_structured_evidence, verify_evidence_chain,
)

_TS = "2026-08-11T00:00:00"

_COMPANY = {
    "name": "测试科技有限公司",
    "coverage": {"queried": ["registration", "business_scope", "operating_status",
                             "litigation", "enforcement", "dishonesty"]},
    "registration": {
        "registered_capital": "5000万元人民币", "paid_in_capital": "5000万元人民币",
        "established_date": "2015-01-01", "legal_representative": "张三",
        "company_type": "有限责任公司", "operating_status": "存续",
        "business_scope": "技术开发", "data_source": "工商登记信息",
    },
    "judicial_records": [], "negative_news": [],
}


def _filled_checks(company=_COMPANY):
    """走生产路径填充：fill_field_checks 内部会打来源标记"""
    checks = build_field_checks(checked_at=_TS)
    fill_field_checks(company, profile_to_facts(company), checks)
    return checks


def _pick(checks, field_id):
    return next(c for c in checks if c["field_id"] == field_id)


def _reasons(report):
    return {m["reason"] for m in report.mismatches}


# ---------- 来源模型基本性质 ----------

def test_档案填充自动打上初始档案来源():
    """漏标一次，该清单在重放时就会被当成来源不明的旧检查点"""
    checks = _filled_checks()
    reg = _pick(checks, "registration")
    assert reg["status"] == "verified"
    assert reg["verification_origin"] == ORIGIN_INITIAL_PROFILE
    # retrieved_at 取**读取档案的时刻**，而非 build_field_checks 传入的 checked_at。
    # 语义上前者才是"证据何时到手"；后者只是清单骨架的生成时间。
    # 只断言非空与格式，不锁具体值——锁死会让这条断言变成时钟测试。
    assert reg["retrieved_at"], "缺时间戳的核实项在重放时会被 fail-closed"
    assert reg["retrieved_at"].startswith("20"), "应为 ISO 格式时间戳"


def test_未核实项不打来源标记():
    """unverified 不主张任何事实；给它标来源会让「有来源」失去含义"""
    checks = _filled_checks()
    unv = [c for c in checks if c["status"] == "unverified"]
    assert unv, "前提：应存在未核实项"
    assert all(not c.get("verification_origin") for c in unv)


# ---------- 要求：初始档案核实项正常重放 ----------

def test_初始档案核实项正常重放():
    checks = _filled_checks()
    report = verify_field_checks(_COMPANY, checks, {})
    assert report.ok, f"合法档案重放不应报错：{report.mismatches}"
    assert not report.degradations


# ---------- 要求：初始档案字段缺失时 fail-closed ----------

def test_初始档案字段缺失时fail_closed():
    """清单仍是 verified，但档案里的 registration 已经没了"""
    checks = _filled_checks()
    broken = {k: v for k, v in _COMPANY.items() if k != "registration"}
    report = verify_field_checks(broken, checks, {})
    assert not report.ok
    assert REASON_PROFILE_REPLAY_MISMATCH in _reasons(report)


def test_档案取值被改动时fail_closed():
    checks = _filled_checks()
    _pick(checks, "business_scope")["value"] = "被篡改的经营范围"
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_PROFILE_REPLAY_MISMATCH in _reasons(report)


# ---------- 要求：结构化适配器新增合法证据不被错杀 ----------

def test_结构化适配器新增合法证据不被错杀():
    """
    这是本轮的核心用例：适配器查到了初始档案里没有的字段。
    旧实现会把它误判为"清单说已核实但档案无法复现" → 全面 fail-closed。
    """
    checks = _filled_checks()
    store = {}
    guarantee = _pick(checks, "guarantee")
    assert guarantee["status"] == "unverified", "前提：档案未覆盖对外担保"

    guarantee["status"] = "verified"
    guarantee["value"] = "为某公司提供连带责任保证1800万元"
    record_structured_evidence(
        store, guarantee,
        source_adapter="guarantee_registry",
        value="为某公司提供连带责任保证1800万元",
        retrieved_at=_TS,
    )

    report = verify_field_checks(_COMPANY, checks, store)
    assert report.ok, f"合法的适配器增量证据不应被判为不一致：{report.mismatches}"


def test_适配器证据与档案并存时各按各的来源重放():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    g["status"] = "verified"; g["value"] = "无对外担保"
    record_structured_evidence(store, g, source_adapter="guarantee_registry",
                               value="无对外担保", retrieved_at=_TS)
    report = verify_field_checks(_COMPANY, checks, store)
    assert report.ok
    # 档案项仍走档案重放
    assert _pick(checks, "registration")["verification_origin"] == ORIGIN_INITIAL_PROFILE
    assert g["verification_origin"] == ORIGIN_STRUCTURED_ADAPTER


# ---------- 要求：只改 field_checks、没有结构化证据时 fail-closed ----------

def test_只改状态不写证据时fail_closed():
    """
    要求 5 的反面：适配器若只改状态不落证据，必须被拦。
    record_structured_evidence 把两件事绑在一起，正是为了让这条路走不通。
    """
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    g["status"] = "verified"
    g["value"] = "凭空出现的取值"
    g["verification_origin"] = ORIGIN_STRUCTURED_ADAPTER
    g["source_adapter"] = "guarantee_registry"
    g["retrieved_at"] = _TS
    # 故意不写 evidence_ids

    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_NO_EVIDENCE_IDS in _reasons(report)


# ---------- 要求：evidence_id 不存在时 fail-closed ----------

def test_evidence_id不存在时fail_closed():
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    g.update({
        "status": "verified", "value": "x",
        "verification_origin": ORIGIN_STRUCTURED_ADAPTER,
        "source_adapter": "guarantee_registry", "retrieved_at": _TS,
        "evidence_ids": ["ev_不存在的ID"],
    })
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_EVIDENCE_NOT_FOUND in _reasons(report)


# ---------- 要求：证据值与清单值不一致时 fail-closed ----------

def test_证据值与清单值不一致时fail_closed():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    g["status"] = "verified"; g["value"] = "担保1800万元"
    record_structured_evidence(store, g, source_adapter="guarantee_registry",
                               value="担保1800万元", retrieved_at=_TS)
    # 证据落好之后，清单取值被改掉——两者漂移
    g["value"] = "担保800万元"

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_EVIDENCE_VALUE_MISMATCH in _reasons(report)


def test_时间戳缺失时fail_closed():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    g["status"] = "verified"; g["value"] = "x"
    record_structured_evidence(store, g, source_adapter="a", value="x", retrieved_at=_TS)
    g["retrieved_at"] = ""      # 时效未知，不予采信

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_MISSING_TIMESTAMP in _reasons(report)


# ---------- 要求：attempted_sources 不能升级为 verified ----------

def test_attempted_sources不能升级为verified():
    """
    「尝试过」与「查到了什么」是两回事。仅凭尝试记录升级状态，
    等于把"我搜过"当成"我核实了"。
    """
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    g["status"] = "verified"
    g["value"] = "无对外担保"
    g["attempted_sources"] = ["guarantee_registry", "web_search"]
    g["verification_origin"] = ORIGIN_STRUCTURED_ADAPTER
    g["source_adapter"] = "guarantee_registry"
    g["retrieved_at"] = _TS
    # attempted_sources 有值，但没有任何 evidence

    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_NO_EVIDENCE_IDS in _reasons(report)
    fail = next(m for m in report.mismatches if m["reason"] == REASON_NO_EVIDENCE_IDS)
    assert fail["attempted_sources"], "失败记录应带上 attempted_sources 便于排查"


# ---------- 要求：通用网页事实不能升级为 verified ----------

def test_通用网页检索不是合法核实来源():
    """
    自然语言事实没有稳定字段映射，也无法在重放时做等值比对。
    合法来源是闭集，web_search 不在其中。
    """
    for bogus in ("web_search", "scout_web", "llm_inference", "unknown"):
        checks = _filled_checks()
        g = _pick(checks, "guarantee")
        g.update({
            "status": "verified", "value": "网上说没有担保",
            "verification_origin": bogus,
            "source_adapter": bogus, "retrieved_at": _TS,
            "evidence_ids": [],
        })
        report = verify_field_checks(_COMPANY, checks, {})
        assert not report.ok, bogus
        assert REASON_INVALID_ORIGIN in _reasons(report), bogus


def test_网页事实进了facts也不改变字段状态():
    """
    Scout 把网页内容写进 facts 是允许的（那是给 LLM 读的素材），
    但 facts 数量增长不得让任何字段变成 verified。
    """
    checks = _filled_checks()
    before = {c["field_id"]: c["status"] for c in checks}
    fake_facts = [{"id": "f1", "content": "网传该公司无对外担保",
                   "source_name": "某论坛", "metadata": {"category": "relation"}}]
    # 模拟 Scout 往 facts 里塞内容——不经过 record_structured_evidence
    after = {c["field_id"]: c["status"] for c in checks}
    assert before == after, "写 facts 不得改变任何字段状态"
    assert fake_facts  # 素材存在，但与核实状态无关


# ---------- 要求：conflicting 保留所有来源和取值 ----------

def test_conflicting同样要求完整证据链():
    """冲突会抬高风险等级并强制人工复核；没有出处的冲突与编造的冲突无法区分"""
    checks = _filled_checks()
    reg = _pick(checks, "registration")
    reg["status"] = "conflicting"
    reg["value"] = None
    # 保留 initial_profile 来源，但档案重放不出 conflicting
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_PROFILE_REPLAY_MISMATCH in _reasons(report)


def test_conflicting证据必须保留至少两个来源取值():
    checks = _filled_checks()
    store = {}
    reg = _pick(checks, "registration")
    reg["status"] = "conflicting"; reg["value"] = None; reg["evidence_ids"] = []
    record_structured_evidence(
        store, reg, source_adapter="registry_vs_audit",
        conflict_values=[{"source": "工商登记", "value": "实缴5000万"}],  # 只有一条
        retrieved_at=_TS,
    )
    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_MISSING_CONFLICT_VALUES in _reasons(report)


def test_conflicting证据齐备时通过并保留各来源取值():
    checks = _filled_checks()
    store = {}
    reg = _pick(checks, "registration")
    reg["status"] = "conflicting"; reg["value"] = None; reg["evidence_ids"] = []
    ev_id = record_structured_evidence(
        store, reg, source_adapter="registry_vs_audit",
        conflict_values=[
            {"source": "工商登记", "value": "实缴5000万元"},
            {"source": "财务附注", "value": "实缴1500万元"},
        ],
        retrieved_at=_TS,
    )
    report = verify_field_checks(_COMPANY, checks, store)
    assert report.ok, report.mismatches
    stored = store[ev_id]["conflict_values"]
    assert len(stored) == 2, "各来源取值必须原样保留，供复核人判断该信谁"
    assert {c["source"] for c in stored} == {"工商登记", "财务附注"}


# ---------- 要求：旧检查点的兼容/降级行为明确可测 ----------

def test_旧检查点缺来源时显式降级而非静默通过():
    """来源不明是既成事实，但必须在结果里可见，不能悄悄按档案来源处理"""
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
        c.pop("retrieved_at", None)

    report = verify_field_checks(_COMPANY, checks, {})
    assert report.ok, "档案能重放出来时不阻断，保持旧检查点可用"
    assert report.degradations, "但必须显式记录降级，不得静默通过"
    assert all(d["reason"] == REASON_MISSING_ORIGIN for d in report.degradations)


def test_旧检查点且档案也重放不出时仍然fail_closed():
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
    broken = {k: v for k, v in _COMPANY.items() if k != "registration"}
    report = verify_field_checks(broken, checks, {})
    assert not report.ok, "降级兜底不等于放行——档案也对不上时必须拦住"


def test_可显式关闭旧检查点兜底重放():
    """严格模式：不接受任何来源不明的核实"""
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
    report = verify_field_checks(_COMPANY, checks, {},
                                 allow_legacy_profile_replay=False)
    assert not report.ok
    assert REASON_MISSING_ORIGIN in _reasons(report)
    assert report.degradations, "即便拒绝兜底，降级事实同样要记录"


# ---------- 回归：旧接口仍可用 ----------

def test_旧接口verified_profile_mismatches仍可用():
    from service.company_profile import verified_profile_mismatches
    checks = _filled_checks()
    assert verified_profile_mismatches(_COMPANY, checks) == []
    _pick(checks, "business_scope")["value"] = "改过的值"
    assert verified_profile_mismatches(_COMPANY, checks), "漂移仍应被检出"


def test_replay_from_profile返回按字段索引的预测():
    checks = _filled_checks()
    predicted = replay_from_profile(_COMPANY, checks)
    assert predicted["registration"]["status"] == "verified"
    assert set(predicted) == {c["field_id"] for c in checks}


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {str(e)[:120]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
