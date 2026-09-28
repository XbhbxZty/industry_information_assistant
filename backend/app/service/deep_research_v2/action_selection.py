"""Small, state-derived affordances, not a second planner or tool executor."""


def selectable_tools(notebook, tools):
    if notebook.get("plan_required") and not notebook.get("investigation_plan"):
        return {k: v for k, v in tools.items() if k == "plan_investigation"}
    return {k: v for k, v in tools.items()
            if k != "plan_investigation" or not notebook.get("investigation_plan")}


def action_options(notebook, tools, inventory, observation):
    """Recommend fresh pages without forbidding explicit evidence revisits.

    Use coverage receipts, including next_offset=0, not the weaker read flag.
    Expanded chunks have their own source IDs: continue those IDs rather than
    repeatedly starting their first page through an older chunk's read_next.
    All actual execution still rechecks scope inside InvestigationTools.
    """
    sources = notebook.get("sources", {})
    candidates, targets = [], set()

    def identity(sid, source, chunk=None):
        return ((source.get("kb_id"), source.get("doc_id"),
                 source.get("chunk_index") if chunk is None else chunk)
                if source.get("is_local") else (sid,))

    known = {identity(sid, s): (sid, s) for sid, s in sources.items()}

    def add(action, arguments, target, purpose):
        if action in tools and target not in targets and len(candidates) < 40:
            targets.add(target)
            candidates.append({"action": action, "arguments": arguments, "purpose": purpose})

    # Continue partial pages before offering entirely unread sources.
    for continuation in (True, False):
        for sid, source in list(sources.items())[:40]:
            offset = (source.get("read_receipt") or {}).get("next_offset")
            partial = source.get("read") and offset is not None
            if bool(partial) != continuation or (not partial and source.get("read")):
                continue
            args = {"source_id": sid}
            if partial:
                args["offset"] = offset
            if "read_source" in tools:
                add("read_source", args, identity(sid, source),
                    "continue_page" if partial else "unread_source")
            elif partial:
                add("read_next", {"source_id": sid}, identity(sid, source), "continue_page")

    for item in inventory.get("source_inventory", [])[:40]:
        sid = item["source_id"]
        source = sources.get(sid, {})
        index = item.get("navigation", {}).get("next_chunk_index")
        if not source.get("is_local") or index is None or item.get("next_offset") is not None:
            continue
        target = identity(sid, source, index)
        if target in known:
            # The exact ID/page is already offered above, or fully read.
            continue
        if "read_next" in tools:
            add("read_next", {"source_id": sid}, target, "unread_chunk")
        else:
            add("read_source", {"source_id": sid, "chunk_index": index}, target, "unread_chunk")

    recommended = [k for k in tools if k not in ("read_source", "read_next")]
    for item in candidates:
        if item["action"] not in recommended:
            recommended.append(item["action"])
    repair = observation.get("repair") or {}
    return {
        "instruction": "候选是建议，不是固定流程；按待查问题选择。可自行构造 tools 允许的其他行动；必要的原文重访允许但不算新进展。",
        "read_candidates": candidates,
        "open_question_ids": [q["id"] for q in notebook.get("investigation_plan", [])[:6]
                              if q.get("status") == "open"],
        "available_calculation_ids": [c["id"] for c in notebook.get("calculations", [])[:12]],
        "repair_actions": [a for a in repair.get("actions", [])[:6] if a.get("action") in tools],
        "next_options": recommended + ([] if notebook.get("plan_required") and not notebook.get("investigation_plan")
                                        else ["finish"]),
    }
