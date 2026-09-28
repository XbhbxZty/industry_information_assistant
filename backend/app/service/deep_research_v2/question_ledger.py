"""Small, run-local question ledger; evidence receipts, not a second scorecard.

The model owns decomposition and substantive answers. Code validates bounded
state transitions and actual citation/calculation membership, never financial
truth. Original questions cannot disappear during investigation or revision.
"""
from copy import deepcopy
from difflib import SequenceMatcher
import re

from .action_errors import ActionError, resolve_at, safe_reference


def _text(value, name, limit, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} 必须为{'非空' if required else ''}文本，最多{limit}字")
    return value.strip()


def calculation_reference_error(notebook, message, field_path, *, required=False):
    available = list(dict.fromkeys(
        c["id"] for c in notebook.get("calculations", [])
        if isinstance(c, dict) and isinstance(c.get("id"), str) and re.fullmatch(r"c[1-9][0-9]*", c["id"])
    ))[:12]
    return ActionError(
        "calculation_required" if required else "calculation_reference_invalid", message,
        repair={"available_calculation_ids": available,
                "instruction": "由调查员从已有底稿中选择确实支持本次判断的 ID；没有适用底稿则用已读引文调用 calculate。不能猜测、自动替换或删除错误 ID，也不能因此将问题标为 answered。",
                "actions": []}, field_path=field_path,
    )


def validate_calculation_ids(notebook, ids, message):
    if not isinstance(ids, list) or len(ids) > 6:
        raise calculation_reference_error(notebook, message, "calculation_ids")
    known = {c["id"] for c in notebook.get("calculations", [])}
    for index, cid in enumerate(ids):
        if not isinstance(cid, str) or cid not in known:
            raise calculation_reference_error(notebook, message, f"calculation_ids[{index}]")


def create_plan(notebook, arguments):
    questions = arguments.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 6:
        raise ValueError("questions 必须包含1至6个用户需要回答的核心问题")
    plan, seen = [], set()
    for index, item in enumerate(questions):
        if not isinstance(item, dict) or type(item.get("calculation_required")) is not bool:
            raise ValueError("每个问题需要question、done_when和布尔calculation_required")
        question = _text(item.get("question"), "question", 300)
        normalized = re.sub(r"\W", "", question).casefold()
        if not normalized or normalized in seen:
            raise ValueError("问题不得为空或重复")
        seen.add(normalized)
        plan.append({"id": f"p{index + 1}", "question": question,
                     "done_when": _text(item.get("done_when"), "done_when", 400),
                     "calculation_required": item["calculation_required"],
                     "status": "open", "answer": "", "limitations": "",
                     "citations": [], "calculation_ids": []})
    old = notebook.get("investigation_plan")
    if old:
        fields = ("question", "done_when", "calculation_required")
        if len(old) == len(plan) and all(all(a.get(k) == b[k] for k in fields) for a, b in zip(old, plan)):
            return {"ok": True, "progress": False, "investigation_plan": deepcopy(old)}
        raise ValueError("调查问题已建立，不可删除、替换问题或降低完成条件；请逐项address_question")
    notebook["investigation_plan"] = plan
    return {"ok": True, "progress": True, "investigation_plan": deepcopy(plan)}


def address_question(notebook, arguments, resolve_citation):
    plan = notebook.get("investigation_plan") or []
    question = next((q for q in plan if q["id"] == arguments.get("question_id")), None)
    if question is None:
        raise ValueError("请先建立计划，再使用返回的问题ID")
    status = arguments.get("status")
    if status not in ("open", "answered", "blocked"):
        raise ValueError("status只能为open、answered、blocked")
    answer = _text(arguments.get("answer", ""), "answer", 1200, status == "answered")
    limitations = _text(arguments.get("limitations", ""), "limitations", 600, status == "blocked")
    refs = arguments.get("citations", [])
    if not isinstance(refs, list) or len(refs) > 6 or (status == "answered" and not refs):
        raise ValueError("已回答的问题必须关联1至6条已读原文引文；不能仅凭摘要标记完成")
    citations = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise ValueError("引文必须指定source_id与quote_id")
        citation = resolve_at(resolve_citation, ref.get("source_id"), ref.get("quote_id"), f"citations[{index}]")
        if citation not in citations:
            citations.append(citation)
    ids = arguments.get("calculation_ids", [])
    calculations = {c["id"]: c for c in notebook.get("calculations", [])}
    validate_calculation_ids(notebook, ids, "calculation_ids必须引用已有计算底稿，最多6条")
    ids = list(dict.fromkeys(ids))
    for index, cid in enumerate(ids):
        for name, variable in calculations[cid].get("variables", {}).items():
            # Recheck scope and read membership even for an earlier workpaper.
            resolved = resolve_at(resolve_citation, variable.get("source_id"), variable.get("quote_id"),
                                  f"calculation_ids[{index}].variables.{name}")
            if resolved.get("quote") != variable.get("quote"):
                raise ActionError(
                    "citation_changed", "计算底稿引文已失效，不能继续引用旧底稿。",
                    repair={"source_id": safe_reference(variable.get("source_id")),
                            "quote_id": safe_reference(variable.get("quote_id")),
                            "instruction": "核对当前已读原文后重新调用 calculate，再由调查员选择有效底稿；不能代改旧底稿或强行宣布问题完成。",
                            "actions": []},
                    field_path=f"calculation_ids[{index}].variables.{name}",
                )
    if status == "answered" and question["calculation_required"] and not ids:
        raise calculation_reference_error(
            notebook, "该问题需要计算：先调用calculate，再关联calculation_ids；资料不足请blocked并写明缺口",
            "calculation_ids", required=True,
        )
    update = {"status": status, "answer": answer, "limitations": limitations,
              "citations": citations, "calculation_ids": ids}
    normalize = lambda s: re.sub(r"\W", "", s).casefold()
    anchors = lambda refs: {(c.get("source_id"), c.get("quote_id"), c.get("quote")) for c in refs}
    def already_recorded(previous):
        return (previous.get("id") == question["id"] and previous.get("status") == status
                and anchors(previous.get("citations", [])) == anchors(citations)
                and set(previous.get("calculation_ids", [])) == set(ids)
                and SequenceMatcher(None, normalize(previous.get("answer", "") + previous.get("limitations", "")),
                                    normalize(answer + limitations)).ratio() >= .86)
    if already_recorded(question):
        return {"ok": True, "progress": False, "question": deepcopy(question),
                "note": "同一证据上的近重复答复不计作新进展"}
    progress = not any(already_recorded(old) for old in notebook.get("question_updates", []))
    # Keep original attempts available to review; commit only after validation.
    history = notebook.setdefault("question_updates", [])
    history.append(deepcopy(question))
    del history[:-18]
    question.update(update)
    return {"ok": True, "progress": progress, "question": deepcopy(question),
            "note": "仅确认回执与引用有效；回答是否充分仍需复核，不改变核实状态或评分"}


def coverage(notebook):
    plan = notebook.get("investigation_plan") or []
    return {"total": len(plan), "answered": sum(q.get("status") == "answered" for q in plan),
            "blocked": sum(q.get("status") == "blocked" for q in plan),
            "open": sum(q.get("status") not in ("answered", "blocked") for q in plan)}
