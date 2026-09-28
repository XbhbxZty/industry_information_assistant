"""Small, citation-backed arithmetic workpapers, not a verification authority.

The caller resolves citations from the current investigation's actually-read
sources.  This module locates numeric operands in those exact quotations and
evaluates a bounded arithmetic AST with Decimal; it never evaluates Python.
Unit conversion, entity/period comparability and inference remain review work.
"""
from __future__ import annotations

import ast
import re
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from typing import Callable


MAX_EXPRESSION_LENGTH = 500
MAX_AST_NODES = 120
MAX_VARIABLES = 20
DECIMAL_PRECISION = 50
MAX_ABSOLUTE_VALUE = Decimal("1e24")
MIN_NONZERO_VALUE = Decimal("1e-24")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")
_DECIMAL = re.compile(
    r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)"
    r"(?:[eE][+-]?\d{1,3})?\Z"
)
# Deliberately consume malformed comma groups as a whole, so `1,23` cannot
# accidentally supply either the operand 1 or the operand 23.
_NUMBER_IN_QUOTE = re.compile(
    r"[+\-\u2212]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)
_TOKEN_NEIGHBOR = re.compile(r"[A-Za-z0-9_.,+\-\u2212]")
_BOUNDARY_NOTE = (
    "仅确认引文中存在输入数值并完成算术计算；未独立核实来源真实性、主体绑定、"
    "期间一致性、单位可比性或因果解释，不自动核实任何风险字段或改变评分。"
)
_CONSTANT_NOTE = "公式常量仅允许 0、1、100（百分比参数）；其他事实数值必须通过带引文的变量提供。"


def _text(value, name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{name} must be a {'nonempty ' if required else ''}string of at most {maximum} characters")
    return value


def _bounded(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("numbers must be finite")
    magnitude = value.copy_abs()
    if magnitude > MAX_ABSOLUTE_VALUE or (magnitude and magnitude < MIN_NONZERO_VALUE):
        raise ValueError("number magnitude exceeds calculation limits")
    return value


def _number(value) -> Decimal:
    if type(value) not in (str, int, float):
        raise ValueError("numeric values must be decimal strings or numbers, never booleans")
    raw = str(value).strip().replace("\u2212", "-")
    if len(raw) > 80 or not _DECIMAL.fullmatch(raw):
        raise ValueError("invalid finite decimal number")
    try:
        parsed = Decimal(raw.replace(",", ""))
    except DecimalException as exc:
        raise ValueError("invalid decimal number") from exc
    if len(parsed.as_tuple().digits) > 40:
        raise ValueError("number precision exceeds calculation limits")
    return _bounded(parsed)


def _located(value: Decimal, quote: str) -> bool:
    for match in _NUMBER_IN_QUOTE.finditer(quote):
        start, end = match.span()
        before = quote[start - 1] if start else ""
        after = quote[end] if end < len(quote) else ""
        if (before and _TOKEN_NEIGHBOR.fullmatch(before)) or (after and _TOKEN_NEIGHBOR.fullmatch(after)):
            continue
        raw = match.group().replace("\u2212", "-")
        left = quote[:start].rstrip()
        right = quote[end:].lstrip()
        # Parenthesized financial values can mean negatives or mere annotation.
        # Do not silently choose either interpretation without an explicit sign.
        if left.endswith(("(", "（")) and right.startswith((")", "）")):
            continue
        # `- 100` must not be treated as positive 100 just because of whitespace.
        if raw[0] not in "+-" and left.endswith(("-", "\u2212", "+")):
            raw = left[-1].replace("\u2212", "-") + raw
        try:
            if _number(raw) == value:
                return True
        except ValueError:
            continue
    return False


def _render(value: Decimal) -> str:
    if not value:
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def calculate_workpaper(arguments: dict, resolve_citation: Callable[[str, str], dict]) -> dict:
    """Return a serializable, unverified workpaper, or raise ValueError.

    ``resolve_citation(source_id, quote_id)`` must enforce source authorization
    and actual-read membership, and return matching IDs plus the exact ``quote``.
    Optional title/url are copied as citation display metadata.  No input or
    resolved citation is mutated and no risk/scoring state is accessed.
    """
    if not isinstance(arguments, dict) or not callable(resolve_citation):
        raise ValueError("arguments must be an object and citation resolver must be callable")
    label = _text(arguments.get("label"), "label", 200)
    expression = _text(arguments.get("expression"), "expression", MAX_EXPRESSION_LENGTH).strip()
    result_unit = _text(arguments.get("result_unit", ""), "result_unit", 80, required=False)
    supplied = arguments.get("variables")
    if not isinstance(supplied, dict) or not 1 <= len(supplied) <= MAX_VARIABLES:
        raise ValueError(f"provide between 1 and {MAX_VARIABLES} cited variables")
    limitations = _text(arguments.get("limitations", ""), "limitations", 1200, required=False)

    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError, ValueError) as exc:
        raise ValueError("invalid arithmetic expression") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError("expression complexity exceeds calculation limits")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load,
               ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)
    if any(not isinstance(node, allowed) for node in nodes):
        raise ValueError("only variables, decimal constants, parentheses and + - * / are allowed")
    used = {node.id for node in nodes if isinstance(node, ast.Name)}
    if not used or used != set(supplied):
        raise ValueError("all expression variables must have citations and all supplied variables must be used")

    variables, numbers = {}, {}
    for name, item in supplied.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(item, dict):
            raise ValueError("invalid calculation variable")
        number = _number(item.get("value"))
        sid = _text(item.get("source_id"), "source_id", 128)
        qid = _text(item.get("quote_id"), "quote_id", 128)
        unit = _text(item.get("unit", ""), "unit", 80, required=False)
        period = _text(item.get("period", ""), "period", 120, required=False)
        subject = _text(item.get("subject", ""), "subject", 200, required=False)
        try:
            citation = resolve_citation(sid, qid)
        except Exception as exc:
            raise ValueError(f"citation could not be resolved for variable {name}") from exc
        if not isinstance(citation, dict) or citation.get("source_id") != sid or citation.get("quote_id") != qid:
            raise ValueError(f"citation identity does not match variable {name}")
        quote = _text(citation.get("quote"), "quote", 5000)
        if not _located(number, quote):
            raise ValueError(f"numeric value for variable {name} was not located in its exact quote")
        variables[name] = {
            "value": _render(number), "source_id": sid, "quote_id": qid,
            "quote": quote, "unit": unit, "period": period, "subject": subject,
            "title": _text(citation.get("title") or "", "title", 500, required=False),
            "url": _text(citation.get("url") or "", "url", 2048, required=False),
        }
        numbers[name] = number

    constants = []

    def compute(node):
        if isinstance(node, ast.Expression):
            return compute(node.body)
        if isinstance(node, ast.Name):
            return numbers[node.id]
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise ValueError("only decimal numeric constants are allowed")
            raw = ast.get_source_segment(expression, node)
            value = _number(raw)
            if value not in (Decimal(0), Decimal(1), Decimal(100)):
                raise ValueError("numeric constants are limited to 0, 1 and 100; provide cited variables for facts")
            constants.append(_render(value))
            return value
        if isinstance(node, ast.UnaryOp):
            operand = compute(node.operand)
            return _bounded(-operand if isinstance(node.op, ast.USub) else operand)
        if isinstance(node, ast.BinOp):
            left, right = compute(node.left), compute(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            else:
                if not right:
                    raise ValueError("division by zero")
                value = left / right
            return _bounded(value)
        raise ValueError("unsupported arithmetic expression")

    try:
        with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
            result = compute(tree)
    except (DecimalException, RecursionError) as exc:
        raise ValueError("arithmetic failed within calculation limits") from exc
    return {
        "label": label, "expression": expression, "variables": variables,
        "result": _render(result), "result_unit": result_unit,
        "decimal_precision": DECIMAL_PRECISION, "rounding": ROUND_HALF_EVEN,
        "literal_constants": constants,
        "limitations": "\n".join(filter(None, [limitations, _BOUNDARY_NOTE, _CONSTANT_NOTE])),
        "arithmetic_status": "computed", "inference_status": "not_reviewed", "verified": False,
    }
