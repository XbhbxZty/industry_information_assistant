"""Read-only archive reprojection; not a recorded prompt or a model evaluation.

Compare identical final-notebook inputs, treating the last tool result as the
observation. Real finish/review calls may have used a different observation.
Only relevant memory blocks and the recall capability are included, not the
full historical tools/options/budget envelope. No archives are written.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "eval" / "agent_e2e_pack" / "runs"
DEFAULT_RUNS = ("ACTION-recovery-T01-001", "ACTION-recovery-T01-002")


def load_projections():
    # Avoid service/__init__.py and its application/database dependencies.
    # The isolated package loads the actual pure production modules unchanged.
    name = "_offline_memory_projection"
    package = ModuleType(name)
    package.__path__ = [str(ROOT / "app" / "service" / "deep_research_v2")]
    sys.modules[name] = package
    investigator = importlib.import_module(name + ".investigator")
    memory = importlib.import_module(name + ".working_memory")
    return investigator.evidence_context, memory.working_memory


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def visible_quotes(context):
    result = {}
    for item in context.get("cited_evidence", []):
        if item.get("quote_id"):
            result[(item["source_id"], item["quote_id"])] = item["quote"]
    for item in context.get("read_evidence", []):
        for qid, quote in item.get("quote_options", {}).items():
            key = (item["source_id"], qid)
            if key in result and result[key] != quote:
                raise AssertionError("A visible quotation ID has conflicting bodies")
            result[key] = quote
    return result


def dereference_quotes(value, bodies):
    if isinstance(value, list):
        return [dereference_quotes(item, bodies) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: dereference_quotes(item, bodies) for key, item in value.items()}
    if "quote_ref" in result:
        if result.pop("quote_ref") != "cited_evidence/read_evidence":
            raise AssertionError("Unknown quote reference target")
        key = (result.get("source_id"), result.get("quote_id"))
        if key not in bodies:
            raise AssertionError("A compacted quote cannot be resolved in this input")
        result["quote"] = bodies[key]
    return result


def citation_count(context):
    return sum(len(item.get("citations", [])) for item in
               context.get("investigation_plan", []) + context.get("findings", [])) + sum(
        len(item.get("variables", {})) for item in context.get("calculations", []))


def evaluate(path, evidence_context, working_memory):
    archive_bytes = path.read_bytes()
    archive = json.loads(archive_bytes)
    notebook = archive["agent_investigation"]
    notebook_before = deepcopy(notebook)
    evidence = evidence_context(notebook)
    context = {
        "brief": notebook["brief"],
        "questions": notebook.get("questions", []),
        "findings": notebook.get("findings", [])[-16:],
        "recent_actions": notebook.get("actions", [])[-4:],
        "observation": notebook["actions"][-1]["result"],
        **evidence,
        "tools": {"recall_evidence": "enabled_for_identical_input_projection"},
    }
    before = deepcopy(context)
    compact = working_memory(context, notebook)
    bodies = visible_quotes(compact)
    restored = dereference_quotes(compact, bodies)
    checks = {
        "input_context_unchanged": context == before,
        "notebook_unchanged": notebook == notebook_before,
        "archive_bytes_unchanged": path.read_bytes() == archive_bytes,
    }
    # Equality checks include all question text, completion conditions, answers,
    # limitations, statuses, formulae, values, units and exact citation bodies.
    for field in ("brief", "questions", "investigation_plan", "findings", "calculations",
                  "read_evidence", "cited_evidence", "material_catalog",
                  "catalog_status", "catalog_truncated", "plan_required"):
        checks[field + "_preserved"] = restored.get(field) == before.get(field)
    checks["visible_quotes_preserved"] = bodies == visible_quotes(before)

    all_read_quotes = {
        (sid, qid): quote
        for sid, source in notebook.get("sources", {}).items() if source.get("read")
        for qid, quote in source.get("quote_options", {}).items()
        if any(quote in text for text in source.get("read_texts", []))
    }
    visible_read = sum(bodies.get(key) == quote for key, quote in all_read_quotes.items())
    old_chars, new_chars = len(encoded(before)), len(encoded(compact))
    answers = {}
    for question in before.get("investigation_plan", []):
        answer = question.get("answer")
        if answer:
            quoted = encoded(answer)[1:-1]
            answers[question["id"]] = {
                "before": encoded(before).count(quoted),
                "after": encoded(compact).count(quoted),
            }
    return {
        "archive": path.parent.name,
        "before_json_chars": old_chars,
        "after_json_chars": new_chars,
        "reduction_chars": old_chars - new_chars,
        "reduction_percent": round((old_chars - new_chars) / old_chars * 100, 2),
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "canonical_citation_bindings_checked": citation_count(before),
        "already_read_quote_count": len(all_read_quotes),
        "already_read_quotes_visible": visible_read,
        "all_read_quotes_visible_in_this_archive": visible_read == len(all_read_quotes),
        "original_projection_truncated_texts": sum(
            bool(item.get("truncated")) for item in before.get("read_evidence", [])),
        "original_projection_omitted_read_sources": sum(
            bool(source.get("read")) for source in notebook.get("sources", {}).values())
            - len(before.get("read_evidence", [])),
        "complete_answer_occurrences": answers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="*", type=Path,
                        help="Explicit result.json files; defaults to the two ACTION T01 archives")
    args = parser.parse_args()
    evidence_context, working_memory = load_projections()
    paths = args.archives or [RUNS / run / "result.json" for run in DEFAULT_RUNS]
    results = [evaluate(path, evidence_context, working_memory) for path in paths]
    print(json.dumps({
        "test_type": "deterministic_archive_memory_reprojection_not_historical_prompt",
        "unit": "JSON characters, not tokens",
        "input_boundary": "Final notebook, latest tool receipt as observation; common memory blocks plus identical recall capability, not full runtime envelope",
        "model_calls": False, "database_access": False, "archive_writes": False,
        "quality_claim": "State preservation and context duplication only; no financial reasoning or live effect verdict",
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 0 if all(item["all_checks_pass"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
