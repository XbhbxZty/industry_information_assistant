"""Analysis review protocol, not a text scanner or a factual verification engine."""
from copy import deepcopy


CHECKS = ("question_coverage", "period_basis", "cashflow_attribution", "receipt_reconciliation")

ANALYSIS_RULES = """
量化分析纪律（与核查清单的独立核实状态分开）：
1. 回答用户逐项问题。每个计算写出来源、主体、期间、单位、公式和口径限制。
2. 同比增长使用同口径两期数；上期为零或负数时说明百分比含义限制。
3. 本期利润到经营现金流的调节表只解释本期现金流，不是同比下降的贡献表。
   缺少上期各调节项时，不得用本期营运资金变动除以现金流同比差额声称贡献率或主因。
   可以比较本期各调节项方向和金额，并明确同比归因未能完成；应付增加通常是现金来源。
4. 回款逐笔按对应债权分类：期末应收回款（其中长账龄、短账龄）、新订单预收、
   其他或无法归类；长账龄回款是期末应收回款的子集，不得重复求和或互相替代。
   核对总收款=互斥分类之和；长账龄未收回余额=期末长账龄余额-对应回款，
   覆盖率分子分母必须属于同一债权池。没有对应关系不能擅自分类。
5. 引文可定位仅证明文本存在，不证明公式正确、分类正确或因果推断成立。
"""

AUDIT_PROTOCOL = """
在原审核 JSON 中增加 analysis_checks 数组，必须各输出一次以下 id：
question_coverage、period_basis、cashflow_attribution、receipt_reconciliation。
每项格式：{"id":"...","status":"supported|issue|not_applicable",
"reason":"逐项复核依据；涉及数字须展示独立重算的公式、分类合计与限制",
"report_quote":"报告中对应的逐字片段；issue 时必填"}。
不适用须说明为什么当前问题和报告均不涉及该项，不能把缺少必要资料当作不适用。
报告明确披露不能归因、分类不明等限制时，可以 supported，不得为保守而制造问题。
评级保守不免除分析正确性检查。发现量化错误须标 issue，即使普通 issues 数组已列出。
不要输出内部思维过程，仅提供可审计的计算结果及证据依据。
"""


def enforce_analysis_review(result, report):
    """Require an explicit audit receipt; never infer semantic correctness in code.

    Missing/invalid receipts mean incomplete review, not a proven financial error.
    Model-identified errors must survive severity downgrades and iteration exhaustion.
    """
    result = deepcopy(result)
    issues = result.get("issues")
    result["issues"] = [i for i in issues if isinstance(i, dict)] if isinstance(issues, list) else []
    checks = result.get("analysis_checks")
    checks = checks if isinstance(checks, list) else []
    for key in CHECKS:
        rows = [c for c in checks if isinstance(c, dict) and c.get("id") == key]
        row = rows[0] if len(rows) == 1 else {}
        status, reason, quote = row.get("status"), row.get("reason"), row.get("report_quote")
        valid = status in ("supported", "issue", "not_applicable") and isinstance(reason, str) and bool(reason.strip())
        if status == "issue":
            valid = valid and isinstance(quote, str) and bool(quote.strip()) and quote in report
        if valid and status != "issue":
            continue
        result["issues"].append({
            "target_section": "全局", "location": key,
            "issue_type": "analysis_quality_error" if valid else "review_not_executed",
            "severity": "major", "description": reason if valid else f"分析复核项 {key} 缺失或格式无效，尚未完成复核",
            "evidence": quote if valid else "缺少有效的结构化复核回执",
            "suggestion": "修正对应分析并重新复核" if valid else "重新执行分析复核；不得视为已通过",
            "requires_new_search": False, "detected_by": "llm" if valid else "protocol",
        })
        if not valid:
            result["degraded"] = True
    return result
