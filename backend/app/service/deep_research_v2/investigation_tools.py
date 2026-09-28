"""Investigation tools backed by the existing Scout and evidence bridge.

Model arguments contain source IDs, never collection names, document paths or
SQL. Local source ownership is rechecked against the request's resolved scope.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from difflib import SequenceMatcher
from time import monotonic
from uuid import UUID

from .investigator import investigate, InvestigationBudget
from ..rag_evidence_bridge import collect_analysis_evidence, finalize_rag_evidence


class InvestigationTools:
    def __init__(self, scout, state):
        self.scout, self.state = scout, state
        self.notebook = state.setdefault("agent_investigation", {})
        self.sources = self.notebook.setdefault("sources", {})
        self._chunks = {}

    def definitions(self):
        tools = {
            "read_source": '阅读原文，不自动抽取或核实字段：{"source_id":"s1", "chunk_index":可选片段序号, "offset":可选正文偏移量}。返回可引用原文及后续片段导航；每次最多6000字。',
            "read_next": '继续阅读同一材料的下一未读片段：{"source_id":"s1"}；不要把只读一片当作全文已读。',
            "record_finding": '记录分析：{"claim":"判断或替代解释", "citations":[{"source_id":"s1","quote_id":"q1"},{"source_id":"s2","quote_id":"q3"}], "kind":"support|counter|gap", "calculation_ids":["c1"]}。最多6条精确引文；也兼容单source_id配quote_id或quote_ids。',
            "calculate": '用已读引文计算工作底稿：{"label":"计算用途", "expression":"(current-prior)/prior*100", "variables":{"current":{"value":"120","source_id":"s1","quote_id":"q1","unit":"万元","period":"本期"},"prior":{"value":"100","source_id":"s1","quote_id":"q2","unit":"万元","period":"上期"}},"result_unit":"%","limitations":"主体、期间及口径限制"}。仅四则运算；不证明分类或因果成立，结果可用calculation_ids引用。',
            "inspect_checks": "查看当前核查状态和证据校验反馈，参数 {}。",
        }
        if self.state.get("search_local"):
            tools["list_materials"] = '列出本次授权的材料目录、索引及阅读状态，参数 {}。无需语义搜索命中也可阅读；目录不是已经读完的证据。'
            tools["extract_evidence"] = '对已读本地片段尝试字段证据抽取：{"source_id":"s1"}。仅需要核查清单字段时使用，仍须原证据闸门校验；失败不等于没有材料。'
            tools["search_local"] = '检索当前用户授权资料：{"query":"具体问题"}，返回来源 ID。'
        if self.state.get("search_web"):
            tools["search_web"] = '检索公开网页：{"query":"具体问题"}，返回来源 ID。'
        return tools

    def add_source(self, row):
        identity = json.dumps([row.get("kb_id"), row.get("doc_id"), row.get("chunk_index"),
                               row.get("url"), None if row.get("is_local") else row.get("summary")], ensure_ascii=False)
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
                                     "read_ranges": [], "read_complete": False,
                                     "quote_options": {}, "read_receipt": None,
                                     "extraction_receipt": None, "extraction_key": None})
        if not fresh and not self.sources[sid].get("read") and row.get("summary"):
            self.sources[sid]["summary"] = str(row["summary"])[:6000]
        return sid, fresh

    def _scope(self, source):
        if not source.get("is_local"):
            if not self.state.get("search_web"):
                raise ValueError("本次未启用网页读取")
            return None
        if not self.state.get("search_local"):
            raise ValueError("本次未启用本地读取")
        entry = next((e for e in self.state.get("kb_scope", [])
                      if str(e.get("kb_id")) == str(source.get("kb_id"))), None)
        if not entry:
            raise ValueError("来源不在当前授权知识库范围")
        UUID(str(source.get("doc_id")))
        return entry

    def resolve_citation(self, source_id, quote_id):
        if not isinstance(source_id, str) or source_id not in self.sources:
            raise ValueError("来源 ID 不存在")
        source = self.sources[source_id]
        self._scope(source)
        if not source.get("read"):
            raise ValueError("请先 read_source 阅读原文")
        quote = source.get("quote_options", {}).get(quote_id) if isinstance(quote_id, str) else None
        if not isinstance(quote, str) or not any(quote in t for t in source.get("read_texts", [])):
            raise ValueError("引文不匹配，请选择该已读来源的 quote_id")
        return {"source_id": source_id, "quote_id": quote_id, "quote": quote,
                "title": source.get("title"), "url": source.get("url")}

    async def list_materials(self):
        from .material_catalog import load_material_catalog
        try:
            catalog = await asyncio.wait_for(asyncio.to_thread(load_material_catalog,
                self.state.get("kb_scope") or [], self.state.get("_user_id")), timeout=15)
        except Exception as exc:
            self.notebook["catalog_status"] = "unavailable"
            return {"ok": False, "progress": False, "error": f"材料目录读取失败：{type(exc).__name__}",
                    "note": "不能据此认定没有材料；可对授权范围继续检索。"}
        documents, added = [], 0
        for row in catalog["documents"]:
            entry = next((e for e in self.state.get("kb_scope", []) if str(e.get("kb_id")) == row["kb_id"]), None)
            if entry is None:
                raise ValueError("目录返回范围外材料")
            source = {**row, "is_local": True, "chunk_index": 0, "summary": "",
                      "url": f"local://kb/{row['kb_id']}/{row['doc_id']}",
                      "kb_name": entry.get("kb_name"), "site_name": f"本地知识库／{entry.get('kb_name', '')}"}
            sid, fresh = self.add_source(source)
            if sid:
                documents.append({**row, "source_id": sid})
                added += fresh
        self.notebook.update(documents=documents, catalog_status="available",
                             catalog_truncated=catalog["truncated"] or len(documents) < len(catalog["documents"]))
        self._publish()
        return {"ok": True, "progress": bool(added), "documents": documents,
                "truncated": self.notebook["catalog_truncated"],
                "note": "目录只证明材料已登记；pending/failed 是处理状态，不是材料不存在。请自主选择相关材料阅读。"}

    def _publish(self):
        self.scout.add_message(self.state, "agent_investigation", public_notebook(self.notebook))

    async def choose(self, prompt, context):
        response = await self.scout.call_llm(
            system_prompt=prompt, user_prompt=json.dumps(context, ensure_ascii=False),
            json_mode=True, temperature=0.2, max_tokens=2000, timeout=45,
        )
        return self.scout.parse_json_response(response)

    async def execute(self, action, args):
        if action not in self.definitions():
            raise ValueError("未授权的工具")
        if action == "list_materials":
            return await self.list_materials()
        if action == "calculate":
            from .calculation_tool import calculate_workpaper
            receipt = calculate_workpaper(args, self.resolve_citation)
            calculations = self.notebook.setdefault("calculations", [])
            for previous in calculations:
                if all(previous.get(k) == receipt.get(k) for k in ("expression", "variables", "result_unit")):
                    return {"ok": True, "progress": False, "calculation": previous, "note": "相同底稿已计算"}
            if len(calculations) >= 12:
                raise ValueError("计算底稿数量已达上限")
            receipt["id"] = f"c{len(calculations) + 1}"
            calculations.append(receipt)
            self._publish()
            return {"ok": True, "progress": True, "calculation": receipt}
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
        if action == "record_finding":
            return self.record_finding(args)
        sid = args.get("source_id")
        if not isinstance(sid, str) or sid not in self.sources:
            return {"ok": False, "progress": False, "error": "来源 ID 不存在，请从返回目录选择，不要猜测", "sources": [
                {"source_id": k, "title": v.get("title"), "read": v.get("read", False)}
                for k, v in self.sources.items()]}
        source = self.sources[sid]
        if action == "extract_evidence":
            self._scope(source)
            if not source.get("is_local") or not source.get("read"):
                raise ValueError("仅可抽取已读本地片段")
            extraction_key = hashlib.sha256(json.dumps([
                source["read_receipt"].get("offset", 0), source["read_receipt"]["text"]
            ], ensure_ascii=False).encode()).hexdigest()
            if source.get("extraction_key") == extraction_key:
                return {"ok": True, "progress": False, "cached": True,
                        "evidence_feedback": source["extraction_receipt"]}
            feedback = await self.extract_evidence(source)
            source["extraction_key"] = extraction_key
            return {"ok": "error" not in feedback, "progress": bool(feedback.get("new_candidates")),
                    "evidence_feedback": feedback}
        if action == "read_next":
            if source.get("is_local"):
                rows = await self.document_chunks(source)
                offset = (source.get("read_receipt") or {}).get("next_offset")
                unread = [r["chunk_index"] for r in rows if not self._chunk_read(source, r["chunk_index"])]
                if offset is not None:
                    args = {"chunk_index": source["chunk_index"], "offset": offset}
                elif not unread:
                    return {"ok": True, "progress": False, "note": "当前可取得片段已读完；不代表已独立核实。"}
                else:
                    args = {"chunk_index": next((i for i in unread if i > source.get("chunk_index", -1)), unread[0])}
            else:
                args = {"offset": (source.get("read_receipt") or {}).get("next_offset", 0)}
                if args["offset"] is None:
                    return {"ok": True, "progress": False, "note": "已读完当前可取得网页正文。"}
        return await self.read_source(sid, source, args)

    def record_finding(self, args):
        claim, kind = args.get("claim"), args.get("kind", "support")
        if not isinstance(claim, str) or not 6 <= len(claim) <= 700:
            raise ValueError("发现应为 6 至 700 字")
        if kind not in ("support", "counter", "gap"):
            raise ValueError("发现类型必须是 support、counter 或 gap")
        refs = args.get("citations")
        if refs is None:
            ids = args.get("quote_ids", args.get("quote_id"))
            # Explicit multi-ID shorthand is split, never fuzzy matched.
            if isinstance(ids, str):
                ids = [q.strip() for q in ids.split(",")]
            if ids is not None:
                if not isinstance(ids, list):
                    raise ValueError("quote_ids 必须为数组")
                refs = [{"source_id": args.get("source_id"), "quote_id": q} for q in ids]
            else:
                sid, quote = args.get("source_id"), args.get("quote")
                source = self.sources.get(sid) if isinstance(sid, str) else None
                if not source:
                    raise ValueError("来源 ID 不存在")
                self._scope(source)
                if not source.get("read"):
                    raise ValueError("请先 read_source 阅读原文")
                if not isinstance(quote, str) or not 6 <= len(quote) <= 1200 or not any(quote in t for t in source.get("read_texts", [])):
                    return {"ok": False, "progress": False, "error": "引文不匹配，请选择已读来源的quote_id"}
                refs = [{"source_id": sid, "quote": quote}]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 6:
            raise ValueError("citations 必须含 1 至 6 条引文")
        citations = []
        try:
            for ref in refs:
                if not isinstance(ref, dict):
                    raise ValueError("引文应为对象")
                if "quote_id" in ref:
                    citation = self.resolve_citation(ref.get("source_id"), ref["quote_id"])
                elif args.get("citations") is None and args.get("quote"):
                    citation = {**ref, "title": source.get("title"), "url": source.get("url")}
                else:
                    raise ValueError("多引文请使用各来源的 quote_id")
                if citation not in citations:
                    citations.append(citation)
        except ValueError as exc:
            return {"ok": False, "progress": False, "error": str(exc),
                    "note": "使用已读引文 ID；可以修改参数，不必重复读取。"}
        calculation_ids = args.get("calculation_ids", [])
        if not isinstance(calculation_ids, list) or len(calculation_ids) > 6 or any(
            not isinstance(c, str) or c not in {p["id"] for p in self.notebook.get("calculations", [])}
            for c in calculation_ids
        ):
            raise ValueError("calculation_ids 必须引用已有计算底稿")
        first = citations[0]
        finding = {"claim": claim, "kind": kind, "citations": citations,
                   "calculation_ids": list(dict.fromkeys(calculation_ids)),
                   "quote": first["quote"], "source_id": first["source_id"],
                   "title": first.get("title"), "url": first.get("url"),
                   "citation_status": "located", "inference_status": "not_reviewed", "verified": False}
        findings = self.notebook.setdefault("findings", [])
        anchors = {(c["source_id"], c["quote"]) for c in citations}
        normalize = lambda text: re.sub(r"\W+", "", text).casefold()
        for previous in findings:
            old_refs = previous.get("citations") or [previous]
            old_anchors = {(c.get("source_id"), c.get("quote")) for c in old_refs}
            if (anchors == old_anchors and previous.get("kind") == kind
                    and previous.get("calculation_ids", []) == finding["calculation_ids"]
                    and SequenceMatcher(None, normalize(previous["claim"]), normalize(claim)).ratio() >= .86):
                # Do not pretend semantic equivalence: retain the alternative
                # wording as an audit note, but it earns no progress/budget.
                variants = previous.setdefault("wording_variants", [])
                if claim != previous["claim"] and claim not in variants and len(variants) < 3:
                    variants.append(claim)
                return {"ok": True, "progress": False, "note": "相同证据上的近重复判断已保留，不算新增调查进展。"}
        if len(findings) >= 20:
            return {"ok": False, "progress": False, "error": "发现数量已达上限，请收束调查"}
        findings.append(finding)
        self._publish()
        return {"ok": True, "progress": True, "finding": finding,
                "note": "只确认引文存在，分析结论未自动核实，也不改变评分"}

    def _chunk_read(self, source, index):
        return any(s.get("kb_id") == source.get("kb_id") and s.get("doc_id") == source.get("doc_id")
                   and s.get("chunk_index") == index and s.get("read_complete", s.get("read")) for s in self.sources.values())

    async def document_chunks(self, source):
        entry = self._scope(source)
        key = (entry["kb_id"], str(UUID(str(source["doc_id"]))))
        if key not in self._chunks:
            rows = await asyncio.to_thread(self.scout.milvus_service.get_document_chunks, entry["collection"], key[1])
            if not rows:
                raise ValueError("未能取得材料片段，可能为索引或读取故障；不能认定材料没有内容")
            if any(type(r.get("chunk_index")) is not int or r["chunk_index"] < 0
                   or not isinstance(r.get("content"), str)
                   or (r.get("doc_id") is not None and str(r["doc_id"]) != key[1]) for r in rows):
                raise ValueError("材料片段索引格式无效")
            if len({r["chunk_index"] for r in rows}) != len(rows):
                raise ValueError("材料片段索引重复，不能确定读取依据")
            self._chunks[key] = sorted(rows, key=lambda r: r["chunk_index"])
        return self._chunks[key]

    async def read_source(self, sid, source, args):
        self._scope(source)
        navigation = {}
        if source.get("is_local"):
            entry = self._scope(source)
            rows = await self.document_chunks(source)
            index = args.get("chunk_index", source.get("chunk_index", 0))
            if type(index) is not int or index < 0:
                raise ValueError("chunk_index 必须是非负整数")
            row = next((r for r in rows if r["chunk_index"] == index), None)
            if row is None:
                return {"ok": False, "progress": False, "error": "目标片段不可用，请从实际目录选择", "available_chunks": [r["chunk_index"] for r in rows[:80]]}
            expanded = {**source, "chunk_index": index, "summary": row["content"]}
            expanded.pop("statement_scope", None)
            expanded.pop("statement_scope_marks", None)
            self.scout._annotate_statement_scope([expanded], entry["collection"])
            sid, _ = self.add_source(expanded)
            if not sid:
                raise ValueError("来源数量已达上限")
            source = self.sources[sid]
            source["known_chunk_count"] = len(rows)
            unread = [r["chunk_index"] for r in rows if r["chunk_index"] != index and not self._chunk_read(source, r["chunk_index"])]
            navigation = {"known_chunk_count": len(rows), "next_chunk_index": next((i for i in unread if i > index), unread[0] if unread else None),
                          "unread_chunk_indices": unread[:80], "index_truncated": len(rows) >= 4000,
                          "note": "仅当前片段算已读；未读片段不能视作未提供。"}
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
            full_text = source["web_text"]
        else:
            full_text = row["content"]
        if not full_text:
            return {"ok": False, "progress": False, "error": "来源没有可读文本"}
        offset = args.get("offset", 0)
        if type(offset) is not int or offset < 0 or offset >= len(full_text):
            raise ValueError("offset 必须位于可读正文范围内")
        text = full_text[offset:offset + 6000]
        cached = source.get("read_receipt")
        if cached and cached.get("text") == text[:6000] and cached.get("offset", 0) == offset:
            return {**cached, "navigation": navigation, "progress": False, "cached": True,
                    "note": "这是已读原文缓存，没有新增证据或重新抽取。请选择引文记录发现，或finish列明未决问题。"}
        read_texts = source.setdefault("read_texts", [])
        unseen_text = text[:6000] not in read_texts
        fresh_read = not any(begin <= offset and end >= offset + len(text) for begin, end in source.get("read_ranges", []))
        if (unseen_text and len(read_texts) >= 6) or (fresh_read and len(source.get("read_ranges", [])) >= 6):
            return {"ok": False, "progress": False, "error": "单片段阅读窗口已达上限，保留原引文；剩余内容尚未读完。"}
        source["read"] = True
        if unseen_text:
            read_texts.append(text[:6000])
        ranges = sorted(source.get("read_ranges", []) + [[offset, offset + len(text)]])
        covered = 0
        for begin, end in ranges:
            if begin > covered:
                break
            covered = max(covered, end)
        source["read_ranges"] = ranges
        source["read_complete"] = covered >= len(full_text)
        # IDs select exact server-observed text, never fuzzy-match model prose.
        options = source.setdefault("quote_options", {})
        for paragraph in re.split(r"[\r\n]+|(?<=[。！？])", text[:6000]):
            for start in range(0, len(paragraph), 1000):
                line = paragraph[start:start + 1000].strip()
                if 6 <= len(line) <= 1200 and line not in options.values() and len(options) < 120:
                    options[f"q{len(options) + 1}"] = line
        section = {"id": "agent_research", "title": "自主调查", "description": self.state["query"]}
        self.scout._retain_corpus_for_investigation(self.state, [{**source, "summary": text}], section["id"])
        receipt = {"ok": True, "progress": fresh_read, "source_id": sid,
                "title": source.get("title"), "chunk_index": source.get("chunk_index"),
                "text": text[:6000], "navigation": navigation,
                "evidence_feedback": {"status": "not_requested", "note": "原文已读，未自动抽取或核实字段；确需核查时调用extract_evidence。"},
                "quote_options": {key: quote for key, quote in options.items() if quote in text},
                "content_kind": "document_chunk" if source.get("is_local") else "web_page",
                "offset": offset, "total_chars": len(full_text),
                "next_offset": covered if covered < len(full_text) else None,
                "truncated": source.get("truncated", False)}
        source["read_receipt"] = receipt
        self._publish()
        return receipt

    async def extract_evidence(self, source):
        """Independent, explicit extraction; original verification remains intact."""
        feedback = {}
        section = {"id": "agent_research", "title": "自主调查", "description": self.state["query"]}
        projection = {**source, "summary": source["read_receipt"]["text"]}
        if source.get("is_local"):
            try:
                from config.dd_checklist import CHECKLIST_BY_ID
            except ImportError:
                from app.config.dd_checklist import CHECKLIST_BY_ID
            feedback["allowed_field_ids"] = [c.get("field_id") for c in self.state.get("field_checks", [])
                                             if c.get("field_id") in CHECKLIST_BY_ID]
            feedback["analysis_boundary"] = (
                "核实候选只能使用 allowed_field_ids；账龄和回款等不在清单中的分析可用 record_finding 记录，"
                "不得虚构字段或强行映射为其他已核实项。抽取失败不代表材料缺失。")
            before = len(self.state.get("rag_evidence_rejections", []))
            candidates_before = len(self.state.get("rag_evidence_candidates", []))
            try:
                analysis = await asyncio.wait_for(self.scout._analyze_search_results(
                    self.state["query"], section, [projection], hypotheses=[],
                    subject_name=self.state.get("company_name") or self.state.get("subject_name") or "",
                    due_diligence_mode=True,
                    active_field_ids=[c.get("field_id", "") for c in self.state.get("field_checks", [])],
                    all_active_fields=True,
                    extraction_timeout=20,
                ), timeout=30)
                if not isinstance(analysis, dict):
                    raise ValueError("invalid extraction result")
                collect_analysis_evidence(self.state, analysis, [projection], section["id"])
            except Exception as exc:
                # Reading succeeded independently of extraction. Preserve the
                # original text, but never silently turn an extraction outage
                # into a successful empty result. Cancellation still propagates.
                feedback["error"] = f"证据抽取失败：{type(exc).__name__}；原文可读，未因此核实任何字段"
            # Final aggregation stays at the research node boundary. Return
            # candidate/rejection feedback immediately so the agent can re-read.
            feedback.update(candidate_count=len(self.state.get("rag_evidence_candidates", [])),
                            new_candidates=max(0, len(self.state.get("rag_evidence_candidates", [])) - candidates_before),
                            rejections=[r.get("reason") for r in self.state.get("rag_evidence_rejections", [])[before:]][:8])
            source["extraction_error"] = feedback.get("error")
        feedback["status"] = "failed" if feedback.get("error") else "extracted_pending_verification"
        source["extraction_receipt"] = feedback
        return feedback

    async def run(self):
        if self.state.get("search_local") and self.state.get("_user_id") and not self.notebook.get("documents"):
            started = monotonic()
            receipt = await self.list_materials()
            self.notebook["elapsed_seconds"] = self.notebook.get("elapsed_seconds", 0) + monotonic() - started
            self.notebook.setdefault("actions", []).append({"action": "list_materials", "arguments": {},
                "reason": "服务端提供本次授权材料目录，供AI自主选择", "result": receipt})
        brief = {"query": self.state["query"], "subject": self.state.get("company_name") or self.state.get("subject_name"),
                 "as_of": self.state.get("as_of"),
                 "known_facts": [{"content": f.get("content"), "source": f.get("source_name")}
                                 for f in self.state.get("facts", [])[:16]],
                 "followup_questions": self.state.get("pending_search_queries", [])[:6]}
        result = await investigate(
            brief=brief, tools=self.definitions(), choose=self.choose, execute=self.execute,
            notebook=self.notebook,
            emit=lambda event: self.scout.add_message(self.state, "research_step", event),
            budget=InvestigationBudget(max_steps=24, max_seconds=max(0, 420 - self.notebook.get("elapsed_seconds", 0))),
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
    result = {k: notebook.get(k) for k in
              ("status", "questions", "findings", "summary", "missing_materials", "elapsed_seconds", "calculations")}
    sources = list((notebook.get("sources") or {}).values())
    documents = []
    for doc in notebook.get("documents", [])[:40]:
        matching = [s for s in sources if s.get("kb_id") == doc["kb_id"] and s.get("doc_id") == doc["doc_id"]]
        documents.append({"title": doc["title"], "source_id": doc["source_id"],
                          "index_status": doc["index_status"],
                          "chunk_count": max([doc.get("chunk_count", 0)] + [s.get("known_chunk_count", 0) for s in matching]),
                          "read_chunks": len({s.get("chunk_index") for s in matching if s.get("read_complete", s.get("read"))})})
    result["material_coverage"] = {"catalog_status": notebook.get("catalog_status", "not_requested"),
        "documents_total": len(documents), "documents_read": sum(d["read_chunks"] > 0 for d in documents),
        "known_chunks": sum(d["chunk_count"] for d in documents), "read_chunks": sum(d["read_chunks"] for d in documents),
        "truncated": bool(notebook.get("catalog_truncated")), "documents": documents}
    return result
