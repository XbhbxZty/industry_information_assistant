"""Citation location and bounded arithmetic never imply verified risk evidence."""
import copy
import json
import sys
from decimal import ROUND_DOWN, localcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.calculation_tool import calculate_workpaper


def request(expression="current - previous", **overrides):
    result = {
        "label": "两期应收账款变化", "expression": expression,
        "variables": {
            "current": {"value": "1,250.50", "source_id": "s1", "quote_id": "q1",
                        "unit": "万元", "period": "2025", "subject": "样例公司"},
            "previous": {"value": "1000.00", "source_id": "s1", "quote_id": "q2",
                         "unit": "万元", "period": "2024"},
        },
        "result_unit": "万元", "limitations": "未经审计；两期分类口径尚待确认。",
    }
    result.update(overrides)
    return result


def citations():
    return {
        ("s1", "q1"): {"source_id": "s1", "quote_id": "q1", "quote": "2025年末应收账款为1,250.50万元。",
                       "title": "财务报告", "url": "https://example.test/report"},
        ("s1", "q2"): {"source_id": "s1", "quote_id": "q2", "quote": "2024年末应收账款为1,000.00万元。",
                       "title": "财务报告", "url": "https://example.test/report"},
    }


def run(arguments):
    entries = citations()
    return calculate_workpaper(arguments, lambda sid, qid: entries[(sid, qid)])


def single(value="100", quote="账面金额为100万元。", expression="amount", **overrides):
    args = request(expression, variables={"amount": {"value": value, "source_id": "s1", "quote_id": "q1"}})
    args.update(overrides)
    return calculate_workpaper(args, lambda sid, qid: {"source_id": sid, "quote_id": qid, "quote": quote})


def test_two_period_delta_has_exact_citations_and_unreviewed_boundary():
    args = request()
    entries = citations()
    before = copy.deepcopy((args, entries))
    result = calculate_workpaper(args, lambda sid, qid: entries[(sid, qid)])
    assert result["result"] == "250.5"
    assert result["result_unit"] == "万元"
    assert result["variables"]["current"]["value"] == "1250.5"
    assert result["variables"]["current"]["quote"] == entries[("s1", "q1")]["quote"]
    assert result["variables"]["current"]["subject"] == "样例公司"
    assert result["variables"]["previous"]["period"] == "2024"
    assert result["arithmetic_status"] == "computed"
    assert result["inference_status"] == "not_reviewed"
    assert result["verified"] is False
    assert "单位可比性" in result["limitations"]
    assert "未经审计" in result["limitations"]
    assert "评分" in result["limitations"]
    assert (args, entries) == before
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


@pytest.mark.parametrize("expression, expected, constants", [
    ("(current - previous) / previous * 100", "25.05", ["100"]),
    ("current + previous", "2250.5", []),
    ("-(previous - current) / +previous", "0.2505", []),
    ("current / previous - 1", "0.2505", ["1"]),
])
def test_percentage_classification_sum_and_unary_arithmetic(expression, expected, constants):
    result = run(request(expression))
    assert result["result"] == expected
    assert result["literal_constants"] == constants


def test_different_period_units_are_metadata_not_implicit_conversions():
    args = request()
    args["variables"]["previous"].update(unit="元", subject="另一公司")
    result = run(args)
    assert result["result"] == "250.5"
    assert result["verified"] is False
    assert result["variables"]["previous"]["unit"] == "元"
    assert result["variables"]["previous"]["subject"] == "另一公司"
    assert "主体绑定" in result["limitations"]


@pytest.mark.parametrize("value, quote, expected", [
    ("1234.50", "总计1,234.50万元。", "1234.5"),
    ("-1234.50", "净利润−1,234.50万元。", "-1234.5"),
    ("-100", "净利润 - 100 万元。", "-100"),
    ("+100", "本期变动+100.00万元。", "100"),
    (".50", "份额为0.50。", "0.5"),
    (0, "本期余额为0万元。", "0"),
    (1.25, "比例为1.25%。", "1.25"),
    ("1e3", "金额为1,000元。", "1000"),
])
def test_numeric_location_respects_whole_values(value, quote, expected):
    assert single(value, quote)["result"] == expected


@pytest.mark.parametrize("value, quote", [
    ("25", "金额为125万元。"),
    ("25", "金额为1.25万元。"),
    ("100", "本期净额为-100万元。"),
    ("100", "本期净额为− 100万元。"),
    ("1000", "本期净额为（1,000）万元。"),
    ("23", "未经清洗数据1,23万元。"),
    ("1", "未经清洗数据1,23万元。"),
    ("12", "凭证编码A12B。"),
    ("24", "截至2024年。"),
    ("100", "金额100.5元。"),
    ("5", "比例1.5%。"),
    ("999", "本期未提供金额。"),
])
def test_substrings_malformed_grouping_and_sign_changes_cannot_supply_operand(value, quote):
    with pytest.raises(ValueError, match="not located"):
        single(value, quote)


def test_worded_subtraction_keeps_source_operand_sign_and_explains_repair():
    quote = "本期减费用80万元。"
    with pytest.raises(ValueError, match="value须与该原文数字及其符号一致"):
        single("-80", quote, expression="amount")
    assert single("80", quote, expression="-amount")["result"] == "-80"
    with pytest.raises(ValueError, match="expression须用variables的键名"):
        single("80", quote, expression="0")


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo bad') + amount", "amount.__class__", "amount[0]",
    "amount ** 100", "amount // 1", "amount % 1", "[amount for _ in range(1)]",
    "lambda: amount", "amount if True else 0", "amount < 100", "abs(amount)",
])
def test_code_and_non_arithmetic_ast_are_rejected_before_resolving(expression):
    def never_resolve(*_):
        pytest.fail("invalid AST must be rejected before citation resolver")
    args = request(expression, variables={"amount": {"value": "100", "source_id": "s1", "quote_id": "q1"}})
    with pytest.raises(ValueError, match="only variables"):
        calculate_workpaper(args, never_resolve)


@pytest.mark.parametrize("expression", ["amount + True", "amount + '100'", "amount + 2", "amount + 999999",
                                        "amount * 0.01", "amount + 0x64", "amount + 1_00"])
def test_boolean_string_and_uncited_fact_constants_are_rejected(expression):
    with pytest.raises(ValueError):
        single(expression=expression)


@pytest.mark.parametrize("value", [True, None, [], {}, float("nan"), float("inf"), float("-inf"),
                                    "NaN", "Infinity", "1e25", "1e-25", "9" * 81, "1,00"])
def test_nonfinite_nondecimal_and_unbounded_inputs_fail_closed(value):
    with pytest.raises(ValueError):
        single(value)


@pytest.mark.parametrize("expression", ["amount / 0", "amount / (amount - amount)"])
def test_division_by_zero(expression):
    with pytest.raises(ValueError, match="division by zero"):
        single(expression=expression)


def test_intermediate_overflow_cannot_be_hidden_by_subsequent_cancellation():
    with pytest.raises(ValueError, match="magnitude"):
        single("1e24", "规模1e24元。", "amount * 100 / 100")


def test_missing_citation_and_not_yet_read_resolver_fail_closed():
    for exception in (KeyError("no such citation"), PermissionError("unread source")):
        def unresolved(*_, error=exception):
            raise error
        with pytest.raises(ValueError, match="citation could not be resolved"):
            calculate_workpaper(request(), unresolved)


@pytest.mark.parametrize("replacement", [None, {}, {"source_id": "foreign", "quote_id": "q1", "quote": "100"},
                                         {"source_id": "s1", "quote_id": "q1", "quote": None}])
def test_resolver_identity_and_payload_must_match(replacement):
    with pytest.raises(ValueError):
        calculate_workpaper(request(), lambda *_: replacement)


@pytest.mark.parametrize("expression, variables", [
    ("100 + 1", {}),
    ("100 + 1", {"amount": {"value": "100", "source_id": "s1", "quote_id": "q1"}}),
    ("unknown + current", request()["variables"]),
    ("current", request()["variables"]),
])
def test_every_used_operand_is_declared_and_unused_citations_cannot_launder_constants(expression, variables):
    with pytest.raises(ValueError):
        run(request(expression, variables=variables))


def test_expression_length_node_count_variable_count_are_bounded():
    for expression in ("amount" + " " * 501, " + ".join(["amount"] * 45), "(" * 220 + "amount" + ")" * 220):
        with pytest.raises(ValueError):
            single(expression=expression)
    args = request(" + ".join(f"a{i}" for i in range(21)), variables={
        f"a{i}": {"value": "100", "source_id": "s1", "quote_id": "q1"} for i in range(21)
    })
    with pytest.raises(ValueError, match="between 1 and 20"):
        run(args)


def test_decimal_arithmetic_is_not_binary_float_arithmetic():
    args = request("first + second", variables={
        "first": {"value": 0.1, "source_id": "s1", "quote_id": "q1"},
        "second": {"value": 0.2, "source_id": "s1", "quote_id": "q1"},
    })
    result = calculate_workpaper(args, lambda sid, qid: {
        "source_id": sid, "quote_id": qid, "quote": "分类甲0.1万元，分类乙0.2万元。",
    })
    assert result["result"] == "0.3"


def test_repeating_decimal_has_explicit_reproducible_precision():
    args = request("first / second", variables={
        "first": {"value": "2", "source_id": "s1", "quote_id": "q1"},
        "second": {"value": "3", "source_id": "s1", "quote_id": "q1"},
    })
    def resolver(sid, qid):
        return {"source_id": sid, "quote_id": qid, "quote": "分类甲2万元，分类乙3万元。"}
    expected = calculate_workpaper(args, resolver)
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_DOWN
        context.Emax = 2
        observed = calculate_workpaper(args, resolver)
    assert observed == expected
    assert observed["result"] == "0." + "6" * 49 + "7"
    assert observed["decimal_precision"] == 50
    assert observed["rounding"] == "ROUND_HALF_EVEN"


@pytest.mark.parametrize("overrides", [
    {"limitations": ["not a string"]}, {"label": ""}, {"expression": ""},
    {"variables": []}, {"result_unit": None},
])
def test_malformed_metadata_is_rejected(overrides):
    with pytest.raises(ValueError):
        run(request(**overrides))
