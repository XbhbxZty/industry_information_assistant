"""Lossless state references for Investigator input, never a notebook rewrite.

Keep analysis and its limitations verbatim. Remove a duplicate only when its
canonical copy is visible in the same input.
This is context housekeeping, not a model-generated summary or fact checker.
"""
from copy import deepcopy


MEMORY_CONTRACT = {
    "brief.query": "用户任务；保持原文。用户陈述仍需证据支持。",
    "brief.known_facts/followup_questions": "既有抽取及补查上下文，不自动视为用户原话或已核实事实。",
    "investigation_plan/questions": "模型提出的调查任务或假设，不是用户原话，也不是原文事实。",
    "findings/investigation_plan.answer": "模型保存的分析；answered仅表示已保存答复，不表示推理已通过复核。",
    "read_evidence/cited_evidence/observation.quote_options": "已读原文及精确引用，不代表事实已独立核实。",
    "calculations": "工具计算底稿；数值执行成功不验证分类、可比性或因果。",
    "references": "*_ref指向本轮可见字段；原始行动日志仍完整保存。回取已读内容不算新调查进展。",
}


def _state_receipt(result, context):
    """Reference only exact, currently visible canonical state, not older versions."""
    result = deepcopy(result)
    if result.get("ok", True) is False:
        return result
    plan = result.get("investigation_plan")
    if plan and plan == context.get("investigation_plan"):
        result.pop("investigation_plan")
        result["investigation_plan_ref"] = "investigation_plan"
    for field, collection in (("question", "investigation_plan"),
                              ("calculation", "calculations"), ("finding", "findings")):
        item = result.get(field)
        if isinstance(item, dict) and item.get("id") and item in context.get(collection, []):
            result.pop(field)
            result[field + "_ref"] = item["id"]
    return result


def working_memory(context, notebook):
    """Pure projection used only at choose boundaries; shared exports stay intact."""
    memory = deepcopy(context)
    memory["memory_contract"] = dict(MEMORY_CONTRACT)
    can_recall = "recall_evidence" in context.get("tools", {})
    observation = context.get("observation", {})
    if "observation" in context:
        memory["observation"] = _state_receipt(observation, context)

    # Keep the newest failed arguments and repair feedback intact. Successful
    # canonical state already includes the accepted answer/formula/plan.
    for receipt in memory.get("recent_actions", []):
        original = receipt.get("result", {})
        if not isinstance(original, dict):
            continue
        result = _state_receipt(original, context)
        refs = {key: value for key, value in result.items()
                if key in ("investigation_plan_ref", "question_ref", "calculation_ref", "finding_ref")}
        if refs and original.get("ok", True):
            receipt["arguments"] = refs
        if original and original == observation:
            receipt["result"] = {"observation_ref": "observation"}
        else:
            receipt["result"] = result

    # A small index makes omission explicit, including sources outside the
    # rolling read window. It never marks reading or verification as complete.
    visible = {item["source_id"]: item for item in context.get("read_evidence", [])}
    for entry in memory.get("source_inventory", []):
        if entry.get("status") != "read_unverified":
            continue
        sid = entry["source_id"]
        source = notebook.get("sources", {}).get(sid, {})
        quotes = {qid: quote for qid, quote in source.get("quote_options", {}).items()
                  if isinstance(quote, str) and quote and
                  any(quote in text for text in source.get("read_texts", []))}
        shown = visible.get(sid, {})
        entry["memory"] = {
            "quote_count": len(quotes),
            "visible_quote_count": sum(quotes.get(qid) == quote
                                       for qid, quote in shown.get("quote_options", {}).items()),
            "in_read_evidence": sid in visible,
            "text_truncated": bool(shown.get("truncated")),
        }
        if can_recall and quotes:
            entry["memory"]["recall"] = {"action": "recall_evidence",
                                         "arguments": {"source_id": sid, "start": 0}}

    # Remove repeated citation text only if that exact ID+body is visible in
    # the input itself. Legacy raw citations and mismatches remain verbatim.
    bodies = {(item.get("source_id"), item.get("quote_id"), item.get("quote"))
              for item in context.get("cited_evidence", [])
              if item.get("quote_id") and isinstance(item.get("quote"), str)}
    for item in context.get("read_evidence", []):
        bodies.update((item.get("source_id"), qid, quote)
                      for qid, quote in item.get("quote_options", {}).items())
    for item in memory.get("investigation_plan", []) + memory.get("findings", []):
        for citation in item.get("citations", []):
            _reference_quote(citation, bodies)
    for calculation in memory.get("calculations", []):
        for variable in calculation.get("variables", {}).values():
            _reference_quote(variable, bodies)
    return memory


def _reference_quote(citation, bodies):
    key = (citation.get("source_id"), citation.get("quote_id"), citation.get("quote"))
    if key[1] and key in bodies:
        citation.pop("quote")
        citation["quote_ref"] = "cited_evidence/read_evidence"
