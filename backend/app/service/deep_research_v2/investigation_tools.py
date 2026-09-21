"""Investigation tools backed by the existing Scout and evidence bridge.

Model arguments contain source IDs, never collection names, document paths or
SQL. Local source ownership is rechecked against the request's resolved scope.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from uuid import UUID

from .investigator import investigate, InvestigationBudget
from ..rag_evidence_bridge import collect_analysis_evidence, finalize_rag_evidence


class InvestigationTools:
    def __init__(self, scout, state):
        self.scout, self.state = scout, state
        self.notebook = state.setdefault("agent_investigation", {})
        self.sources = self.notebook.setdefault("sources", {})

    def definitions(self):
        tools = {
            "read_source": '阅读已检索来源及提取证据：{"source_id":"s_...", "chunk_index":可选本地片段序号, "offset":可选网页正文偏移量}。可选择同文档其他片段；网页每次读取6000字。',
            "record_finding": '记录分析：{"claim":"判断或替代解释", "source_id":"已读来源ID", "quote_id":"阅读结果返回的引文ID", "kind":"support|counter|gap"}。优先选择quote_id，不要自行改写引文；也兼容quote原文。',
            "inspect_checks": "查看当前核查状态和证据校验反馈，参数 {}。",
        }
        if self.state.get("search_local"):
            tools["search_local"] = '检索当前用户授权资料：{"query":"具体问题"}，返回来源 ID。'
        if self.state.get("search_web"):
            tools["search_web"] = '检索公开网页：{"query":"具体问题"}，返回来源 ID。'
        return tools

    def add_source(self, row):
        identity = json.dumps([row.get("kb_id"), row.get("doc_id"), row.get("chunk_index"),
                               row.get("url"), row.get("summary")], ensure_ascii=False)
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        sid = next((key for key, value in self.sources.items() if value.get("fingerprint") == fingerprint), None)
        if sid is None:
            index = 1
            while f"s{index}" in self.sources:
                index += 1
            sid = f"s{index}"
        fresh = sid not in self.sources
        if fresh and len(self.sources) >= 40:
            return None, False
        self.sources.setdefault(sid, {**row, "summary": str(row.get("summary") or "")[:6000],
                                     "read": False, "read_texts": [], "fingerprint": fingerprint,
                                     "quote_options": {}, "read_receipt": None})
        return sid, fresh

    async def choose(self, prompt, context):
        response = await self.scout.call_llm(
            system_prompt=prompt, user_prompt=json.dumps(context, ensure_ascii=False),
            json_mode=True, temperature=0.2, max_tokens=2000, timeout=45,
        )
        return self.scout.parse_json_response(response)

    async def execute(self, action, args):
        if action not in self.definitions():
            raise ValueError("未授权的工具")
        if action.startswith("search_"):
            query = args.get("query")
            if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
                raise ValueError("查询应为 2 至 200 字的具体问题")
            if action == "search_local":
                # Existing Milvus/embedding clients are synchronous. Keep the
                # graph cancellation poll responsive while they perform I/O.
                outcome = await asyncio.to_thread(lambda: asyncio.run(
                    self.scout._execute_local_search(query, top_k=5, kb_scope=self.state.get("kb_scope") or [])
                ))
            else:
                outcome = await self.scout._execute_search(query, count=5, as_of=self.state.get("as_of") or "")
            if not outcome.ok:
                self.scout._record_search_failure(self.state, outcome)
                return {"ok": False, "progress": False, "error": outcome.failure_reason}
            previews, added = [], 0
            for row in outcome.results[:5]:
                sid, fresh = self.add_source(row)
                if sid:
                    previews.append({"source_id": sid, "title": row.get("title"),
                                     "chunk_index": row.get("chunk_index"),
                                     "snippet": str(row.get("summary") or row.get("snippet") or "")[:700]})
                    added += fresh
            return {"ok": True, "progress": bool(added), "sources": previews,
                    "source_limit_reached": len(self.sources) >= 40}
        if action == "inspect_checks":
            return {"ok": True, "progress": False, "checks": [
                {k: c.get(k) for k in ("field_id", "field_name", "status", "value", "failure_reason")}
                for c in self.state.get("field_checks", [])
            ], "evidence": self.state.get("rag_evidence_summary", {})}
        sid = args.get("source_id")
        if not isinstance(sid, str) or sid not in self.sources:
            return {"ok": False, "progress": False, "error": "来源 ID 不存在，请从返回目录选择，不要猜测", "sources": [
                {"source_id": k, "title": v.get("title"), "read": v.get("read", False)}
                for k, v in self.sources.items()]}
        source = self.sources[sid]
        if action == "record_finding":
            claim, quote = args.get("claim"), args.get("quote")
            if "quote_id" in args:
                quote = source.get("quote_options", {}).get(str(args["quote_id"]))
            kind = args.get("kind", "support")
            if not source.get("read"):
                raise ValueError("请先 read_source 阅读原文")
            if not isinstance(claim, str) or not 6 <= len(claim) <= 700:
                raise ValueError("发现应为 6 至 700 字")
            if not isinstance(quote, str) or not 6 <= len(quote) <= 1200 or not any(quote in t for t in source.get("read_texts", [])):
                return {"ok": False, "progress": False, "error": "引文不匹配，请选择此来源已有quote_id，无需重新阅读", "source_id": sid,
                        "quote_options": source.get("quote_options", {})}
            if kind not in ("support", "counter", "gap"):
                raise ValueError("发现类型必须是 support、counter 或 gap")
            finding = {"claim": claim, "quote": quote, "source_id": sid, "kind": kind,
                       "citation_status": "located", "inference_status": "not_reviewed",
                       "verified": False, "url": source.get("url"), "title": source.get("title")}
            findings = self.notebook.setdefault("findings", [])
            if any(f["claim"] == claim and f["source_id"] == sid for f in findings):
                return {"ok": True, "progress": False, "note": "发现已存在"}
            if len(findings) >= 20:
                return {"ok": False, "progress": False, "error": "发现数量已达上限，请收束调查"}
            findings.append(finding)
            self.scout.add_message(self.state, "agent_investigation", public_notebook(self.notebook))
            return {"ok": True, "progress": True, "finding": finding,
                    "note": "只确认引文存在，分析结论未自动核实，也不改变评分"}
        return await self.read_source(sid, source, args)

    async def read_source(self, sid, source, args):
        if source.get("is_local"):
            entry = next((e for e in self.state.get("kb_scope", [])
                          if str(e.get("kb_id")) == str(source.get("kb_id"))), None)
            if not entry:
                raise ValueError("来源不在当前授权知识库范围")
            # The existing Milvus method interpolates doc_id in a filter.
            # Production document IDs are UUIDs, never accept arbitrary strings.
            doc_id = str(UUID(str(source.get("doc_id"))))
            index = args.get("chunk_index", source.get("chunk_index", 0))
            if type(index) is not int or index < 0:
                raise ValueError("chunk_index 必须是非负整数")
            if index != source.get("chunk_index"):
                rows = await asyncio.to_thread(self.scout.milvus_service.get_document_chunks,
                                               entry["collection"], doc_id)
                row = next((r for r in rows if r.get("chunk_index") == index), None)
                if row is None:
                    raise ValueError("没有读到目标片段；可能不存在或读取失败")
                expanded = {**source, "chunk_index": index, "summary": row.get("content", "")}
                expanded.pop("statement_scope", None)
                expanded.pop("statement_scope_marks", None)
                self.scout._annotate_statement_scope([expanded], entry["collection"])
                sid, _ = self.add_source(expanded)
                if not sid:
                    raise ValueError("来源数量已达上限")
                source = self.sources[sid]
        if not source.get("is_local"):
            if not self.state.get("search_web"):
                raise ValueError("本次未启用网页读取")
            from .web_reader import read_public_page
            if "web_text" not in source:
                try:
                    page = await asyncio.to_thread(read_public_page, source.get("url") or "")
                except Exception as exc:
                    return {"ok": False, "progress": False, "error": str(exc)[:300],
                            "note": "搜索摘录仍可见，但正文未读到，不可当作已阅读原文"}
                source.update(web_text=page["text"], resolved_url=page["url"], truncated=page["truncated"])
            offset = args.get("offset", 0)
            if type(offset) is not int or offset < 0 or offset >= len(source["web_text"]):
                raise ValueError("offset 必须位于可读正文范围内")
            text = source["web_text"][offset:offset + 6000]
        else:
            text = source.get("summary") or source.get("snippet") or ""
        if not text:
            return {"ok": False, "progress": False, "error": "来源没有可读文本"}
        cached = source.get("read_receipt")
        if cached and cached.get("text") == text[:6000]:
            return {**cached, "progress": False, "cached": True,
                    "note": "这是已读原文缓存，没有新增证据或重新抽取。请选择引文记录发现，或finish列明未决问题。"}
        source["read"] = True
        read_texts = source.setdefault("read_texts", [])
        fresh_read = text[:6000] not in read_texts
        if fresh_read:
            read_texts.append(text[:6000])
            source["read_texts"] = read_texts[-6:]
        # IDs select exact server-observed text, never fuzzy-match model prose.
        options = source.setdefault("quote_options", {})
        for line in re.split(r"[\r\n]+|(?<=[。！？])", text[:6000]):
            line = line.strip()
            if 6 <= len(line) <= 1200 and line not in options.values() and len(options) < 40:
                options[f"q{len(options) + 1}"] = line
        section = {"id": "agent_research", "title": "自主调查", "description": self.state["query"]}
        self.scout._retain_corpus_for_investigation(self.state, [{**source, "summary": text}], section["id"])
        # Local documents alone enter the existing deterministic evidence gate.
        # Web excerpts remain exploratory material.
        feedback = {}
        if source.get("is_local"):
            before = len(self.state.get("rag_evidence_rejections", []))
            try:
                analysis = await asyncio.wait_for(self.scout._analyze_search_results(
                    self.state["query"], section, [source], hypotheses=[],
                    subject_name=self.state.get("company_name") or self.state.get("subject_name") or "",
                    due_diligence_mode=True,
                    active_field_ids=[c.get("field_id", "") for c in self.state.get("field_checks", [])],
                    all_active_fields=True,
                    extraction_timeout=20,
                ), timeout=30)
                if not isinstance(analysis, dict):
                    raise ValueError("invalid extraction result")
                collect_analysis_evidence(self.state, analysis, [source], section["id"])
            except Exception as exc:
                # Reading succeeded independently of extraction. Preserve the
                # original text, but never silently turn an extraction outage
                # into a successful empty result. Cancellation still propagates.
                feedback["error"] = f"证据抽取失败：{type(exc).__name__}；原文可读，未因此核实任何字段"
            # Final aggregation stays at the research node boundary. Return
            # candidate/rejection feedback immediately so the agent can re-read.
            feedback.update(candidate_count=len(self.state.get("rag_evidence_candidates", [])),
                            rejections=[r.get("reason") for r in self.state.get("rag_evidence_rejections", [])[before:]][:8])
            source["extraction_error"] = feedback.get("error")
        receipt = {"ok": True, "progress": fresh_read, "source_id": sid,
                "title": source.get("title"), "chunk_index": source.get("chunk_index"),
                "text": text[:6000], "evidence_feedback": feedback,
                "quote_options": options,
                "content_kind": "document_chunk" if source.get("is_local") else "web_page",
                "total_chars": len(source.get("web_text") or text),
                "truncated": source.get("truncated", False)}
        source["read_receipt"] = receipt
        return receipt

    async def run(self):
        brief = {"query": self.state["query"], "subject": self.state.get("company_name") or self.state.get("subject_name"),
                 "as_of": self.state.get("as_of"),
                 "known_facts": [{"content": f.get("content"), "source": f.get("source_name")}
                                 for f in self.state.get("facts", [])[:16]],
                 "followup_questions": self.state.get("pending_search_queries", [])[:6]}
        result = await investigate(
            brief=brief, tools=self.definitions(), choose=self.choose, execute=self.execute,
            notebook=self.notebook,
            emit=lambda event: self.scout.add_message(self.state, "research_step", event),
            budget=InvestigationBudget(max_seconds=max(0, 240 - self.notebook.get("elapsed_seconds", 0))),
        )
        counts = finalize_rag_evidence(self.state)
        self.scout.add_message(self.state, "field_checks_updated", {
            "field_checks": self.state.get("field_checks", []),
            "completeness": self.state.get("completeness", {}), "rag_evidence": counts,
        })
        self.scout.add_message(self.state, "agent_investigation", public_notebook(result))
        if result["status"] != "completed":
            warning = f"自主调查因 {result['status']} 收束；未决问题不能视为已排除风险"
            self.state.setdefault("errors", []).append(warning)
            self.scout.add_message(self.state, "warning", {"content": warning})
        self.state["pending_search_queries"] = []
        return self.state


def public_notebook(notebook):
    """Only product-facing findings and action purposes, not raw source state."""
    return {k: notebook.get(k) for k in
            ("status", "questions", "findings", "summary", "missing_materials", "elapsed_seconds")}
