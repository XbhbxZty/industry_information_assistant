"""Regression tests for the loader-authorized, offline case-material retriever."""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import types

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for _path in (str(BACKEND), str(APP)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from service.due_diligence_case import (  # noqa: E402
    AuthorizedCaseMaterial,
    CaseValidationError,
    LoadedDueDiligenceCasePackage,
    load_due_diligence_case_package,
    validate_loaded_due_diligence_case_package,
)
from service.due_diligence_case_rag import (  # noqa: E402
    CaseMaterialCorpus,
    CaseMaterialRagError,
    build_case_material_corpus,
    retrieve_case_materials,
    validate_case_material_result,
)
from service import due_diligence_case_rag as case_rag  # noqa: E402


SIMULATION_ROOT = BACKEND / "eval" / "simulation_cases"
REAL_CASE_ROOT = BACKEND / "eval" / "real_cases"
CASES = (
    "case_a_consistent",
    "case_b_hidden_financing",
    "case_c_receivable_authenticity",
    "case_d_insufficient_data",
)
ORACLE_CANARY = (
    "expected_decision",
    "expected_claim_verdicts",
    "known_conflicts",
    "verdict",
)


def _package(case_name: str) -> LoadedDueDiligenceCasePackage:
    return load_due_diligence_case_package(SIMULATION_ROOT / case_name)


def _copied_case(tmp_path: Path, name: str = "case_a_consistent") -> Path:
    copied = tmp_path / "case"
    shutil.copytree(SIMULATION_ROOT / name, copied)
    return copied


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("case_name", CASES)
def test_loader_materialises_exactly_the_two_manifest_authorized_materials(case_name: str):
    package = _package(case_name)

    assert len(package.materials) == 2
    assert {material.material_id for material in package.materials} == {
        descriptor.material_id for descriptor in package.case.materials
    }
    assert all(isinstance(material, AuthorizedCaseMaterial) for material in package.materials)
    for material in package.materials:
        assert material.subject_entity_id == package.primary_subject_id
        assert material.sha256 == hashlib.sha256(material.content_bytes).hexdigest()
        assert material.factual_date is not None
        assert material.date_unknown_reason is None
        assert material.factual_date <= material.cutoff_date

    serialized = json.dumps(package.to_metadata(), ensure_ascii=False, sort_keys=True)
    assert "content_bytes" not in serialized
    assert not any(canary in serialized for canary in ORACLE_CANARY)


@pytest.mark.parametrize("case_name", CASES)
def test_package_and_corpus_never_discover_answer_layer_lures(
    case_name: str, monkeypatch: pytest.MonkeyPatch
):
    original_text = Path.read_text
    original_bytes = Path.read_bytes

    def _guard_path(path: Path) -> None:
        if {"oracle", "reference", "post_cutoff"} & set(path.parts):
            raise AssertionError(f"answer-layer path was read: {path}")

    def guarded_read_text(self: Path, *args, **kwargs):
        _guard_path(self)
        return original_text(self, *args, **kwargs)

    def guarded_read_bytes(self: Path, *args, **kwargs):
        _guard_path(self)
        return original_bytes(self, *args, **kwargs)

    def forbidden_discovery(*_args, **_kwargs):
        raise AssertionError("case-material path discovery is forbidden")

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(Path, "glob", forbidden_discovery)
    monkeypatch.setattr(Path, "rglob", forbidden_discovery)

    package = _package(case_name)
    corpus = build_case_material_corpus(package, f"session-{case_name}")
    assert len(corpus.documents) == 2
    assert corpus.chunks


@pytest.mark.parametrize("case_name", CASES)
def test_four_cases_build_isolated_primary_subject_corpora(case_name: str):
    package = _package(case_name)
    corpus = build_case_material_corpus(package, f"session-{case_name}")

    assert len(corpus.documents) == len(package.materials) == 2
    assert {chunk.case_id for chunk in corpus.chunks} == {package.case_id}
    assert {chunk.subject_entity_id for chunk in corpus.chunks} == {package.primary_subject_id}
    assert {chunk.material_id for chunk in corpus.chunks} == {
        material.material_id for material in package.materials
    }
    assert all(chunk.text.startswith(
        f"[case_id={package.case_id}; source_id={chunk.material_id}; locator={chunk.locator}]"
    ) for chunk in corpus.chunks)
    assert all(chunk.chunk_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
               for chunk in corpus.chunks)


def test_builder_accepts_only_loader_authorized_package_not_paths_or_raw_lists():
    for unsafe in (
        SIMULATION_ROOT / "case_a_consistent",
        str(SIMULATION_ROOT / "case_a_consistent"),
        [],
        _package("case_a_consistent").materials,
    ):
        with pytest.raises(TypeError):
            build_case_material_corpus(unsafe, "session-a")  # type: ignore[arg-type]

    # A sealed public-data case directory is a path, therefore it has no route
    # into this retriever even before looking at any of its contents.
    real_case_directory = next(REAL_CASE_ROOT.glob("case_*.md"))
    with pytest.raises(TypeError):
        build_case_material_corpus(real_case_directory, "session-a")  # type: ignore[arg-type]


def test_jsonl_retrieval_is_lexical_stable_and_result_is_json_safe():
    corpus = build_case_material_corpus(_package("case_a_consistent"), "session-a")

    first = retrieve_case_materials(corpus, "invoice_summary", top_k=3)
    second = retrieve_case_materials(corpus, "invoice_summary", top_k=3)

    assert first == second
    assert first and first[0]["result_kind"] == "case_material_rag_v1"
    assert first[0]["authorized_case_material"] is True
    # This is intentionally not an existing local-KB row: the legacy bridge
    # gate is ``result.get('is_local') is True`` and therefore cannot accept it.
    assert first[0].get("is_local") is not True
    assert not {"is_local", "kb_id", "kb_name", "date", "publication_date"} & set(first[0])
    assert first[0]["case_material"]["material_id"] == "a-material-transaction"
    assert first[0]["summary"].startswith("[case_id=SIM-FAC-A; source_id=a-material-transaction;")
    assert json.loads(json.dumps(first, ensure_ascii=False)) == first
    assert all(not isinstance(value, bytes) for row in first for value in row.values())
    assert validate_case_material_result(corpus, first[0])["chunk_id"] == \
        first[0]["case_material"]["chunk_id"]


def test_retrieval_arguments_are_strict():
    corpus = build_case_material_corpus(_package("case_a_consistent"), "session-a")
    for query in ("", "  ", "\x00", 1):
        with pytest.raises((TypeError, ValueError)):
            retrieve_case_materials(corpus, query)  # type: ignore[arg-type]
    for top_k in (0, 51, True, "1"):
        with pytest.raises((TypeError, ValueError)):
            retrieve_case_materials(corpus, "合同", top_k=top_k)  # type: ignore[arg-type]


def test_result_validator_rejects_cross_scope_generic_kb_and_integrity_tampering():
    corpus_a = build_case_material_corpus(_package("case_a_consistent"), "same-session")
    corpus_b = build_case_material_corpus(_package("case_b_hidden_financing"), "same-session")
    result = retrieve_case_materials(corpus_a, "FICT-A-CON-20260508-01")[0]

    assert validate_case_material_result(corpus_a, result)["case_id"] == corpus_a.case_id
    with pytest.raises(CaseMaterialRagError, match="case_id"):
        validate_case_material_result(corpus_b, result)

    tamper_cases = []
    changed_session = deepcopy(result)
    changed_session["case_material"]["session_id"] = "another-session"
    tamper_cases.append(changed_session)
    changed_subject = deepcopy(result)
    changed_subject["case_material"]["subject_entity_id"] = "other-subject"
    tamper_cases.append(changed_subject)
    changed_material = deepcopy(result)
    changed_material["case_material"]["material_id"] = "not-authorized"
    tamper_cases.append(changed_material)
    changed_material_hash = deepcopy(result)
    changed_material_hash["case_material"]["material_sha256"] = "0" * 64
    tamper_cases.append(changed_material_hash)
    changed_chunk_hash = deepcopy(result)
    changed_chunk_hash["case_material"]["chunk_sha256"] = "0" * 64
    tamper_cases.append(changed_chunk_hash)
    changed_summary = deepcopy(result)
    changed_summary["summary"] += " 篡改"
    tamper_cases.append(changed_summary)
    generic_local = deepcopy(result)
    generic_local.pop("case_material")
    generic_local["url"] = "local://kb/user-kb/doc"
    tamper_cases.append(generic_local)

    for tampered in tamper_cases:
        with pytest.raises(CaseMaterialRagError):
            validate_case_material_result(corpus_a, tampered)


def test_date_unknown_remains_retrievable_but_is_not_eligible_for_structured_evidence(tmp_path: Path):
    copied = _copied_case(tmp_path)
    material_index = copied / "input" / "structured" / "materials.json"
    materials = json.loads(material_index.read_text(encoding="utf-8"))
    materials[0].pop("document_date")
    materials[0]["date_unknown_reason"] = "原始材料未载明可验证事实日期。"
    _write_json(material_index, materials)

    corpus = build_case_material_corpus(
        load_due_diligence_case_package(copied), "session-unknown-date"
    )
    result = next(
        item for item in retrieve_case_materials(corpus, "FICT-A-CON-20260508-01")
        if item["case_material"]["material_id"] == "a-material-transaction"
    )

    assert result["case_material"]["date_unknown_reason"]
    assert result["case_material"]["factual_date"] is None
    assert result["case_material"]["date_kind"] == "unknown"
    assert result["case_material"]["eligible_for_structured_evidence"] is False
    assert validate_case_material_result(corpus, result)["eligible_for_structured_evidence"] is False


def test_replaced_or_publicly_constructed_package_fails_before_retrieval():
    package = _package("case_a_consistent")
    other_case = _package("case_b_hidden_financing").case
    public = LoadedDueDiligenceCasePackage(case=package.case, materials=package.materials)
    replacement_cases = (
        replace(package),
        replace(package, case=other_case),
        replace(package, materials=package.materials),
        replace(package, materials=(replace(package.materials[0], content_bytes=b"changed"), *package.materials[1:])),
        replace(package, materials=(replace(package.materials[0], sha256="0" * 64), *package.materials[1:])),
        replace(package, materials=(replace(package.materials[0], factual_date="2026-01-01"), *package.materials[1:])),
        public,
    )
    for invalid in replacement_cases:
        with pytest.raises(CaseValidationError):
            validate_loaded_due_diligence_case_package(invalid)
        with pytest.raises(CaseValidationError):
            build_case_material_corpus(invalid, "session-invalid")

    # Even bypassing frozen dataclass assignment cannot retain an issued seal:
    # the HMAC covers raw bytes, declared digest, dates, and complete case data.
    altered = _package("case_a_consistent")
    object.__setattr__(
        altered,
        "materials",
        (replace(altered.materials[0], content_bytes=b"altered bytes"), *altered.materials[1:]),
    )
    with pytest.raises(CaseValidationError):
        validate_loaded_due_diligence_case_package(altered)


def test_corpus_is_builder_issued_and_rejects_oracle_text_tampering():
    corpus = build_case_material_corpus(_package("case_a_consistent"), "session-corpus-seal")
    result = retrieve_case_materials(corpus, "FICT-A-CON-20260508-01")[0]
    original = corpus.chunks[0]
    forged_text = f"{original.text}\nexpected_decision oracle marker"
    forged_sha256 = hashlib.sha256(forged_text.encode("utf-8")).hexdigest()
    forged_chunk = replace(
        original,
        text=forged_text,
        chunk_sha256=forged_sha256,
        chunk_id=case_rag._expected_chunk_id(
            original.document_id, original.chunk_index, original.locator, forged_sha256
        ),
    )
    public = CaseMaterialCorpus(
        session_id=corpus.session_id,
        case_id=corpus.case_id,
        subject_entity_id=corpus.subject_entity_id,
        subject_name=corpus.subject_name,
        cutoff_date=corpus.cutoff_date,
        documents=corpus.documents,
        chunks=corpus.chunks,
    )
    replaced = replace(corpus, chunks=(forged_chunk, *corpus.chunks[1:]))
    overwritten = build_case_material_corpus(
        _package("case_a_consistent"), "session-corpus-seal"
    )
    object.__setattr__(overwritten, "chunks", (forged_chunk, *overwritten.chunks[1:]))

    for invalid in (public, replace(corpus), replaced, overwritten):
        with pytest.raises(CaseMaterialRagError):
            retrieve_case_materials(invalid, "FICT-A-CON-20260508-01")
        with pytest.raises(CaseMaterialRagError):
            validate_case_material_result(invalid, result)


def test_loader_selected_date_is_latest_concrete_material_date(tmp_path: Path):
    copied = _copied_case(tmp_path)
    material_index = copied / "input" / "structured" / "materials.json"
    materials = json.loads(material_index.read_text(encoding="utf-8"))
    materials[0].update({
        "publication_date": "2026-06-20",
        "observed_at": "2026-06-21",
        "reporting_period": {"start_date": "2026-06-01", "end_date": "2026-06-22"},
    })
    _write_json(material_index, materials)

    package = load_due_diligence_case_package(copied)
    material = next(item for item in package.materials if item.material_id == "a-material-transaction")
    assert material.factual_date == "2026-06-22"
    assert material.date_kind == "reporting_period_end"
    corpus = build_case_material_corpus(package, "session-latest-date")
    result = next(
        item for item in retrieve_case_materials(corpus, "FICT-A-CON-20260508-01")
        if item["case_material"]["material_id"] == "a-material-transaction"
    )
    assert result["case_material"]["factual_date"] == "2026-06-22"
    assert result["case_material"]["publication_date"] == "2026-06-20"
    assert "publication_date" not in result


def test_explicit_non_primary_subject_uses_a_separate_authorized_corpus(tmp_path: Path):
    copied = _copied_case(tmp_path)
    content = "债务人专属确认材料 FICT-A-DEBTOR-ONLY\n"
    debtor_file = copied / "input" / "documents" / "debtor.txt"
    debtor_file.write_text(content, encoding="utf-8")
    material_index = copied / "input" / "structured" / "materials.json"
    materials = json.loads(material_index.read_text(encoding="utf-8"))
    materials.append({
        "material_id": "a-material-debtor-only",
        "subject_entity_id": "ent-a-debtor",
        "material_type": "debtor_confirmation",
        "relative_path": "input/documents/debtor.txt",
        "provenance": "债务人提供的确认文本【纯虚构】",
        "source_channel": "enterprise_submitted",
        "issuer": "启程新能源装备（江苏）有限公司【纯虚构】",
        "document_date": "2026-06-12",
        "sha256": hashlib.sha256(debtor_file.read_bytes()).hexdigest(),
    })
    _write_json(material_index, materials)

    package = load_due_diligence_case_package(copied)
    primary = build_case_material_corpus(package, "session-subjects")
    debtor = build_case_material_corpus(
        package, "session-subjects", subject_entity_id="ent-a-debtor"
    )
    assert {document.material_id for document in primary.documents} == {
        "a-material-transaction", "a-material-site-visit"
    }
    assert {document.material_id for document in debtor.documents} == {"a-material-debtor-only"}
    assert all(
        item["case_material"]["material_id"] != "a-material-debtor-only"
        for item in retrieve_case_materials(primary, "FICT-A-DEBTOR-ONLY")
    )
    assert {
        item["case_material"]["material_id"]
        for item in retrieve_case_materials(debtor, "FICT-A-DEBTOR-ONLY")
    } == {"a-material-debtor-only"}
    with pytest.raises(CaseMaterialRagError, match="no authorized case materials"):
        build_case_material_corpus(package, "session-subjects", subject_entity_id="ent-a-shareholder")


def test_unsupported_format_and_resource_bounds_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    copied = _copied_case(tmp_path)
    raw = (copied / "input" / "documents" / "transaction_documents.jsonl").read_bytes()
    unsupported_file = copied / "input" / "documents" / "transaction_documents.pdf"
    unsupported_file.write_bytes(raw)
    material_index = copied / "input" / "structured" / "materials.json"
    materials = json.loads(material_index.read_text(encoding="utf-8"))
    materials[0]["relative_path"] = "input/documents/transaction_documents.pdf"
    materials[0]["sha256"] = hashlib.sha256(raw).hexdigest()
    _write_json(material_index, materials)
    with pytest.raises(CaseMaterialRagError, match="unsupported"):
        build_case_material_corpus(load_due_diligence_case_package(copied), "session-pdf")

    package = _package("case_a_consistent")
    monkeypatch.setattr(case_rag, "_MAX_MATERIAL_BYTES", 1)
    with pytest.raises(CaseMaterialRagError, match="byte limit"):
        build_case_material_corpus(package, "session-resource")


def test_offline_module_has_no_forbidden_runtime_dependencies_and_still_works_when_poisoned(
    monkeypatch: pytest.MonkeyPatch,
):
    tree = ast.parse(inspect.getsource(case_rag))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("eval") or "oracle" in name for name in imported)
    assert not any("milvus" in name or "embedding" in name for name in imported)
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"glob", "rglob"}
        for node in ast.walk(tree)
    )

    def blocked(*_args, **_kwargs):
        raise AssertionError("offline case-material RAG must not call this dependency")

    milvus = types.ModuleType("service.milvus_service")
    milvus.get_milvus_service = blocked  # type: ignore[attr-defined]
    embedding = types.ModuleType("service.embedding_service")
    embedding.generate_embedding = blocked  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "service.milvus_service", milvus)
    monkeypatch.setitem(sys.modules, "service.embedding_service", embedding)

    corpus = build_case_material_corpus(_package("case_b_hidden_financing"), "session-offline")
    assert retrieve_case_materials(corpus, "FICT-B-CON-20260512-01")


def test_package_materialisation_rejects_missing_material_digest(tmp_path: Path):
    """The general loader is backwards compatible; package materialisation is not."""

    copied = tmp_path / "case"
    shutil.copytree(SIMULATION_ROOT / "case_a_consistent", copied)
    material_index = copied / "input" / "structured" / "materials.json"
    materials = json.loads(material_index.read_text(encoding="utf-8"))
    materials[0].pop("sha256")
    material_index.write_text(json.dumps(materials, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(CaseValidationError, match="requires sha256"):
        load_due_diligence_case_package(copied)


def test_optional_source_issuer_entity_id_must_match_the_declared_entity(tmp_path: Path):
    copied = _copied_case(tmp_path)
    source_index = copied / "input" / "structured" / "sources.json"
    sources = json.loads(source_index.read_text(encoding="utf-8"))
    source = next(item for item in sources["sources"] if item["source_id"] == "a-src-contract")
    source["issuer_entity_id"] = "ent-a-debtor"
    _write_json(source_index, sources)

    with pytest.raises(CaseValidationError, match="issuer must exactly match"):
        load_due_diligence_case_package(copied)


def test_app_namespace_binds_the_app_loader_when_both_import_roots_exist(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.syspath_prepend(str(APP))
    monkeypatch.syspath_prepend(str(BACKEND))
    app_loader = importlib.import_module("app.service.due_diligence_case")
    app_rag = importlib.import_module("app.service.due_diligence_case_rag")

    assert app_rag.LoadedDueDiligenceCasePackage is app_loader.LoadedDueDiligenceCasePackage
    package = app_loader.load_due_diligence_case_package(SIMULATION_ROOT / "case_a_consistent")
    corpus = app_rag.build_case_material_corpus(package, "session-app-namespace")
    assert app_rag.retrieve_case_materials(corpus, "FICT-A-CON-20260508-01")
