# Copyright © 2026 XbhbxZty
"""Freeze the Scout-facing input so a model ablation is attributable (BC-56).

## Why this exists

BC-56 needs one specific comparison: **same evidence, different extraction
model**.  Running the pipeline twice with a different Scout model does not give
that, because the Architect is also an LLM: its section titles, descriptions and
search queries drift between runs, the queries change what gets retrieved, and
the retrieved set changes what Scout could possibly extract.  A coverage delta
measured that way cannot be attributed to extraction ability -- exactly the
contamination BADCASES records as "规划查询变化污染归因".

So this module records everything upstream of extraction on a first run, and
replays it byte-identically on later runs.  After that the only free variable
left is the Scout model.

## What is frozen

* the outline (ids, titles, descriptions, search queries) and hypotheses --
  these go into the extraction prompt verbatim
* the retrieved result list **per section id**, keyed by section rather than by
  query text.  Section ids are positional (`sec_1`..`sec_8`) and therefore
  stable; query text is not.  Keying by query would defeat the purpose: a
  different model writes different queries and would miss every recorded entry.

## Why it lives in eval/ and patches from the outside

Production code must not grow a replay branch.  A stub that lives on the
production path is how BC-45 happened -- the double was better than the real
thing and hid a production defect for two versions.  Here the patch is
installed explicitly by the eval runner, is undone in a ``finally``, and is
stamped into ``input.json`` so a replayed run can never be mistaken for a full
end-to-end run.  The scorer refuses to grade a replayed run as pass/fail for
the same reason (BC-25: 生产降级污染实验归因).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


FIXTURE_VERSION = 1


class RetrievalFixture:
    """Recorded Scout-facing input for one case."""

    def __init__(self, payload: Optional[Dict[str, Any]] = None):
        payload = payload or {}
        self.version: int = int(payload.get("version") or FIXTURE_VERSION)
        self.case_id: str = str(payload.get("case_id") or "")
        self.outline: List[Dict[str, Any]] = list(payload.get("outline") or [])
        self.hypotheses: List[Dict[str, Any]] = list(payload.get("hypotheses") or [])
        self.research_questions: List[str] = list(payload.get("research_questions") or [])
        # {section_id: [result, ...]}
        self.results_by_section: Dict[str, List[Dict[str, Any]]] = {
            str(key): list(value)
            for key, value in (payload.get("results_by_section") or {}).items()
        }
        # {section_id: [{"query": str, "ok": bool, "failure_reason": str}, ...]}
        self.searches_by_section: Dict[str, List[Dict[str, Any]]] = {
            str(key): list(value)
            for key, value in (payload.get("searches_by_section") or {}).items()
        }

    # ------------------------------------------------------------- recording

    def note_plan(self, state: Dict[str, Any]) -> None:
        self.outline = json.loads(json.dumps(state.get("outline") or [], ensure_ascii=False))
        self.hypotheses = json.loads(json.dumps(state.get("hypotheses") or [], ensure_ascii=False))
        self.research_questions = list(state.get("research_questions") or [])

    def note_search(
        self, section_id: str, query: str, results: List[Dict[str, Any]],
        ok: bool, failure_reason: str,
    ) -> None:
        """Record one retrieval call.

        Failures are recorded as failures, not as empty result sets -- the whole
        point of ``SearchOutcome`` is that those are different things (BC-51),
        and a fixture that flattened them would replay a failure as a clean
        "nothing found".
        """
        self.searches_by_section.setdefault(section_id, []).append({
            "query": query, "ok": bool(ok), "failure_reason": failure_reason,
        })
        if not ok:
            return
        bucket = self.results_by_section.setdefault(section_id, [])
        known = {self._result_key(row) for row in bucket}
        for row in results:
            key = self._result_key(row)
            if key not in known:
                known.add(key)
                bucket.append(json.loads(json.dumps(row, ensure_ascii=False)))

    @staticmethod
    def _result_key(row: Dict[str, Any]) -> str:
        return "|".join(str(row.get(field) or "") for field in
                        ("kb_id", "doc_id", "chunk_index", "url", "summary"))

    # -------------------------------------------------------------- replaying

    def replay_outline(self) -> List[Dict[str, Any]]:
        return json.loads(json.dumps(self.outline, ensure_ascii=False))

    def replay_results(self, section_id: str) -> List[Dict[str, Any]]:
        return json.loads(json.dumps(self.results_by_section.get(section_id, []),
                                     ensure_ascii=False))

    def failed_searches(self, section_id: str) -> List[Dict[str, Any]]:
        return [row for row in self.searches_by_section.get(section_id, [])
                if not row.get("ok")]

    # ---------------------------------------------------------------- storage

    def to_payload(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "case_id": self.case_id,
            "outline": self.outline,
            "hypotheses": self.hypotheses,
            "research_questions": self.research_questions,
            "results_by_section": self.results_by_section,
            "searches_by_section": self.searches_by_section,
        }

    def save(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_payload(), ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(text + "\n", encoding="utf-8")
        return self.content_hash()

    @classmethod
    def load(cls, path: Path) -> "RetrievalFixture":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        fixture = cls(payload)
        if fixture.version != FIXTURE_VERSION:
            raise ValueError(
                f"fixture version {fixture.version} != expected {FIXTURE_VERSION}; "
                f"re-record instead of replaying an incompatible fixture"
            )
        if not fixture.outline:
            raise ValueError("fixture has no outline; it cannot pin the extraction input")
        return fixture

    def content_hash(self) -> str:
        """Stable digest, so a run can prove which frozen input it consumed."""
        text = json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def summary(self) -> Dict[str, Any]:
        return {
            "sections": len(self.outline),
            "results_total": sum(len(rows) for rows in self.results_by_section.values()),
            "results_by_section": {key: len(rows)
                                   for key, rows in sorted(self.results_by_section.items())},
            "failed_searches": sum(len(self.failed_searches(key))
                                   for key in self.searches_by_section),
            "content_sha256": self.content_hash(),
        }


# --------------------------------------------------------------------- install

def install(fixture: RetrievalFixture, mode: str) -> Callable[[], None]:
    """Patch the agent classes for one eval process; returns an undo callable.

    ``mode`` is ``"record"`` or ``"replay"``.  Call the returned function in a
    ``finally`` -- leaving a patched class installed would silently affect any
    later run in the same process.
    """
    if mode not in {"record", "replay"}:
        raise ValueError(f"mode must be record|replay, got {mode!r}")

    from service.deep_research_v2.agents.architect import ChiefArchitect
    from service.deep_research_v2.agents.scout import DeepScout, SearchOutcome
    from service.deep_research_v2.state import ResearchPhase

    original_architect = ChiefArchitect.process
    original_local = DeepScout._execute_local_search
    original_section = DeepScout._research_section

    # Scout does not pass the section into `_execute_local_search`, so the
    # recorder needs to know which section a query belongs to.  Track it around
    # `_research_section` instead of threading a parameter through production
    # code just for the eval harness.
    current_section: Dict[str, str] = {"id": ""}

    async def research_section(self, state, section):
        previous = current_section["id"]
        current_section["id"] = str(section.get("id") or "")
        try:
            return await original_section(self, state, section)
        finally:
            current_section["id"] = previous

    if mode == "record":
        async def architect_process(self, state):
            state = await original_architect(self, state)
            fixture.note_plan(state)
            return state

        async def local_search(self, query, top_k=10, kb_scope=None):
            outcome = await original_local(self, query, top_k=top_k, kb_scope=kb_scope)
            fixture.note_search(
                current_section["id"], query, list(outcome.results),
                outcome.ok, outcome.failure_reason,
            )
            return outcome
    else:
        async def architect_process(self, state):
            # ⚠️ 只接管初始规划。`process` 是按 phase 分派的：REVIEWING 阶段跑的
            # 是修订判定，与本次固定的对象无关——整体替换会让修订轮拿到冻结提纲
            # 并跳过判定逻辑。
            if state.get("phase") != ResearchPhase.INIT.value:
                return await original_architect(self, state)

            # 跳过规划 LLM 调用：Architect 不是被测变量，让它跑会重新引入
            # 这个装置正要消除的那种漂移。
            state["outline"] = fixture.replay_outline()
            state["hypotheses"] = json.loads(
                json.dumps(fixture.hypotheses, ensure_ascii=False))
            state["research_questions"] = list(fixture.research_questions)
            state["phase"] = ResearchPhase.PLANNING.value
            # 提纲事件照发：事件流是评测与前端的共同契约，回放不该让它缺一段。
            self.add_message(state, "outline", {
                "understanding": {"note": "[fixture] 回放已固定提纲，未调用规划模型"},
                "key_entities": state.get("key_entities") or [],
                "outline": state["outline"],
                "research_questions": state["research_questions"],
            })
            self.logger.info(
                f"[fixture] 回放已固定的提纲：{len(state['outline'])} 章，"
                f"跳过 Architect 调用"
            )
            return state

        async def local_search(self, query, top_k=10, kb_scope=None):
            section_id = current_section["id"]
            rows = fixture.replay_results(section_id)
            if not rows:
                failures = fixture.failed_searches(section_id)
                if failures:
                    # 录制时这一段是**失败**的，回放必须仍然是失败。
                    # 把它replay成"查了没有"会让附录少一条免责声明，
                    # 而报告里那句"未发现相关记录"就变成了无依据的正面结论。
                    return SearchOutcome(
                        ok=False,
                        failure_reason=f"[fixture] {failures[0].get('failure_reason') or '录制时检索失败'}",
                        provider="local_kb", query=query,
                    )
            return SearchOutcome(results=rows, ok=True, provider="local_kb", query=query)

    ChiefArchitect.process = architect_process
    DeepScout._execute_local_search = local_search
    DeepScout._research_section = research_section

    def undo() -> None:
        ChiefArchitect.process = original_architect
        DeepScout._execute_local_search = original_local
        DeepScout._research_section = original_section

    return undo
