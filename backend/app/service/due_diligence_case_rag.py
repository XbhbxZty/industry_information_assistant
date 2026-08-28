"""Offline retrieval for loader-authorized due-diligence case materials.

This module deliberately has no filesystem, vector-store, embedding, evaluation,
or oracle dependency.  The only accepted input is the immutable package produced
by :func:`service.due_diligence_case.load_due_diligence_case_package`; therefore
all material bytes have already passed the production loader's explicit manifest,
symlink, path, date, and digest gates before this module sees them.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field as dataclass_field
import hashlib
import hmac
import json
import re
import secrets
import threading
from typing import Any, Mapping
import weakref

if __package__ and __package__.startswith("app."):
    from app.service.due_diligence_case import (
        AuthorizedCaseMaterial,
        LoadedDueDiligenceCasePackage,
        validate_loaded_due_diligence_case_package,
    )
else:
    from service.due_diligence_case import (
        AuthorizedCaseMaterial,
        LoadedDueDiligenceCasePackage,
        validate_loaded_due_diligence_case_package,
    )


RAG_VERSION = "case_material_rag_v1"
_MAX_QUERY_CHARS = 2048
_MAX_TOP_K = 50
_MAX_MATERIAL_BYTES = 1_000_000
_MAX_TOTAL_BYTES = 4_000_000
_MAX_LINES_PER_MATERIAL = 20_000
_MAX_LINE_CHARS = 20_000
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class CaseMaterialRagError(ValueError):
    """An offline corpus or result violates the case-material trust contract."""


@dataclass(frozen=True)
class CaseMaterialDocument:
    """One authorized material represented without its full raw bytes."""

    session_id: str
    case_id: str
    subject_entity_id: str
    subject_name: str
    document_id: str
    material_id: str
    material_type: str
    material_sha256: str
    provenance: str
    source_channel: str
    issuer: str
    cutoff_date: str
    date_kind: str
    factual_date: str | None
    document_date: str | None
    publication_date: str | None
    observed_at: str | None
    reporting_period_start: str | None
    reporting_period_end: str | None
    date_unknown_reason: str | None
    chunk_count: int


@dataclass(frozen=True)
class CaseMaterialChunk:
    """A stable, auditable text chunk derived from one authorized material."""

    session_id: str
    case_id: str
    subject_entity_id: str
    subject_name: str
    document_id: str
    material_id: str
    material_type: str
    material_sha256: str
    chunk_id: str
    chunk_sha256: str
    locator: str
    chunk_index: int
    text: str
    provenance: str
    source_channel: str
    issuer: str
    cutoff_date: str
    date_kind: str
    factual_date: str | None
    document_date: str | None
    publication_date: str | None
    observed_at: str | None
    reporting_period_start: str | None
    reporting_period_end: str | None
    date_unknown_reason: str | None
    eligible_for_structured_evidence: bool


@dataclass(frozen=True)
class CaseMaterialCorpus:
    """A builder-issued, session- and subject-scoped in-memory corpus."""

    session_id: str
    case_id: str
    subject_entity_id: str
    subject_name: str
    cutoff_date: str
    documents: tuple[CaseMaterialDocument, ...]
    chunks: tuple[CaseMaterialChunk, ...]
    # Issued only by ``build_case_material_corpus``.  This is intentionally not
    # metadata: a frozen dataclass can otherwise be copied or bypassed with
    # ``object.__setattr__`` by an accidental/untrusted in-process caller.
    _seal: str = dataclass_field(default="", repr=False, compare=False)

    def to_metadata(self) -> dict[str, Any]:
        """Audit metadata only; chunk text and package bytes are intentionally absent."""

        return {
            "version": RAG_VERSION,
            "session_id": self.session_id,
            "case_id": self.case_id,
            "subject_entity_id": self.subject_entity_id,
            "subject_name": self.subject_name,
            "cutoff_date": self.cutoff_date,
            "documents": [
                {
                    "document_id": document.document_id,
                    "material_id": document.material_id,
                    "material_sha256": document.material_sha256,
                    "chunk_count": document.chunk_count,
                }
                for document in self.documents
            ],
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "chunk_sha256": chunk.chunk_sha256,
                    "material_id": chunk.material_id,
                    "locator": chunk.locator,
                }
                for chunk in self.chunks
            ],
        }


# Corpus text is a capability too.  A material package seal only authenticates
# the loader boundary; this second, object-identity-bound seal authenticates the
# exact derived corpus supplied to retrieval and result validation.
_CORPUS_SEAL_KEY = secrets.token_bytes(32)
_CORPUS_SEAL_LOCK = threading.RLock()
_CORPUS_SEAL_REGISTRY: dict[
    int, tuple[weakref.ReferenceType[CaseMaterialCorpus], str]
] = {}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _corpus_seal_payload(corpus: CaseMaterialCorpus) -> bytes:
    """Canonicalise every corpus field except the seal itself."""

    return _canonical_json({
        "session_id": corpus.session_id,
        "case_id": corpus.case_id,
        "subject_entity_id": corpus.subject_entity_id,
        "subject_name": corpus.subject_name,
        "cutoff_date": corpus.cutoff_date,
        "documents": [asdict(document) for document in corpus.documents],
        "chunks": [asdict(chunk) for chunk in corpus.chunks],
    })


def _corpus_seal(corpus: CaseMaterialCorpus) -> str:
    return hmac.new(_CORPUS_SEAL_KEY, _corpus_seal_payload(corpus), hashlib.sha256).hexdigest()


def _register_case_material_corpus(corpus: CaseMaterialCorpus) -> None:
    """Issue the derived-corpus capability and bind it to this exact object."""

    seal = _corpus_seal(corpus)
    object.__setattr__(corpus, "_seal", seal)
    corpus_id = id(corpus)

    def _cleanup(reference: weakref.ReferenceType[CaseMaterialCorpus]) -> None:
        with _CORPUS_SEAL_LOCK:
            registered = _CORPUS_SEAL_REGISTRY.get(corpus_id)
            if registered is not None and registered[0] is reference:
                _CORPUS_SEAL_REGISTRY.pop(corpus_id, None)

    reference = weakref.ref(corpus, _cleanup)
    with _CORPUS_SEAL_LOCK:
        _CORPUS_SEAL_REGISTRY[corpus_id] = (reference, seal)


def _header_value(value: Any) -> str:
    """Keep provenance readable in a one-line header without changing stored metadata."""

    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _require_package(value: Any) -> LoadedDueDiligenceCasePackage:
    if not isinstance(value, LoadedDueDiligenceCasePackage):
        raise TypeError(
            "case-material RAG accepts only LoadedDueDiligenceCasePackage; "
            "paths, strings, lists, and raw descriptors are not allowed"
        )
    return value


def _require_session_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
        raise ValueError("session_id must be a non-empty string of at most 256 characters")
    if "\x00" in value:
        raise ValueError("session_id must not contain NUL")
    return value.strip()


def _material_units(material: AuthorizedCaseMaterial) -> tuple[tuple[str, str], ...]:
    """Parse the deliberately small, strict first-version material surface."""

    if not isinstance(material, AuthorizedCaseMaterial):
        raise TypeError("package contains a non-authorized material")
    suffix = material.relative_path.rsplit(".", 1)[-1].lower() if "." in material.relative_path else ""
    if suffix not in {"txt", "jsonl"}:
        raise CaseMaterialRagError(
            f"material {material.material_id} has unsupported offline RAG format: "
            f"{material.relative_path}"
        )
    if not isinstance(material.content_bytes, bytes):
        raise CaseMaterialRagError(f"material {material.material_id} has non-bytes content")
    if len(material.content_bytes) > _MAX_MATERIAL_BYTES:
        raise CaseMaterialRagError(
            f"material {material.material_id} exceeds the per-material byte limit"
        )
    try:
        decoded = material.content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaseMaterialRagError(
            f"material {material.material_id} is not strict UTF-8"
        ) from exc

    lines = decoded.splitlines()
    if len(lines) > _MAX_LINES_PER_MATERIAL:
        raise CaseMaterialRagError(
            f"material {material.material_id} exceeds the per-material line limit"
        )
    units: list[tuple[str, str]] = []
    for line_number, line in enumerate(lines, start=1):
        if len(line) > _MAX_LINE_CHARS:
            raise CaseMaterialRagError(
                f"material {material.material_id} line {line_number} exceeds the line length limit"
            )
        if not line.strip():
            continue
        if suffix == "jsonl":
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaseMaterialRagError(
                    f"material {material.material_id} has invalid JSONL at line {line_number}"
                ) from exc
            if not isinstance(parsed, Mapping):
                raise CaseMaterialRagError(
                    f"material {material.material_id} JSONL line {line_number} must be an object"
                )
        units.append((f"line:{line_number}", line))
    if not units:
        raise CaseMaterialRagError(f"material {material.material_id} has no non-blank text units")
    return tuple(units)


def _validate_material_date(material: AuthorizedCaseMaterial) -> bool:
    """Return structural-evidence eligibility; unknown dates stay retrievable only."""

    date_values = {
        "document_date": material.document_date,
        "publication_date": material.publication_date,
        "observed_at": material.observed_at,
        "reporting_period_start": material.reporting_period_start,
        "reporting_period_end": material.reporting_period_end,
    }
    for kind, value in date_values.items():
        if value is not None and value > material.cutoff_date:
            raise CaseMaterialRagError(
                f"material {material.material_id} {kind} is after the case cutoff"
            )
    if (
        material.reporting_period_start is not None
        and material.reporting_period_end is not None
        and material.reporting_period_start > material.reporting_period_end
    ):
        raise CaseMaterialRagError(
            f"material {material.material_id} reporting period is internally inconsistent"
        )

    factual_candidates = tuple(
        (kind, value)
        for kind, value in (
            ("document_date", material.document_date),
            ("publication_date", material.publication_date),
            ("observed_at", material.observed_at),
            ("reporting_period_end", material.reporting_period_end),
        )
        if value is not None
    )
    if material.date_unknown_reason:
        if (
            material.factual_date is not None
            or material.date_kind != "unknown"
            or any(value is not None for value in date_values.values())
        ):
            raise CaseMaterialRagError(
                f"material {material.material_id} has contradictory unknown-date metadata"
            )
        return False
    if not factual_candidates or not material.factual_date:
        raise CaseMaterialRagError(
            f"material {material.material_id} lacks both factual date and date_unknown_reason"
        )
    expected_kind, expected_date = max(
        enumerate(factual_candidates), key=lambda item: (item[1][1], -item[0])
    )[1]
    if material.date_kind != expected_kind or material.factual_date != expected_date:
        raise CaseMaterialRagError(
            f"material {material.material_id} factual date does not match its latest concrete date"
        )
    if material.factual_date > material.cutoff_date:
        raise CaseMaterialRagError(
            f"material {material.material_id} factual date is after the case cutoff"
        )
    return True


def _chunk_header(material: AuthorizedCaseMaterial | CaseMaterialChunk, locator: str) -> str:
    date_value = material.factual_date or ""
    material_sha256 = getattr(material, "sha256", None) or getattr(material, "material_sha256")
    lines = [
        f"[case_id={material.case_id}; source_id={material.material_id}; locator={locator}]",
        f"主体：{_header_value(material.subject_name)}",
        f"材料类型：{_header_value(material.material_type)}",
        f"材料SHA256：{material_sha256}",
        f"来源说明：{_header_value(material.provenance)}",
        f"来源渠道：{_header_value(material.source_channel)}",
        f"签发方：{_header_value(material.issuer)}",
        f"事实日期类型：{material.date_kind}",
        f"事实日期：{date_value}",
        f"日期未知原因：{_header_value(material.date_unknown_reason)}",
        f"研究截止日：{material.cutoff_date}",
    ]
    return "\n".join(lines)


def _expected_document_id(
    case_id: str,
    subject_entity_id: str,
    material_id: str,
    material_sha256: str,
) -> str:
    return hashlib.sha256(
        f"{case_id}\0{subject_entity_id}\0{material_id}\0{material_sha256}".encode("utf-8")
    ).hexdigest()[:32]


def _expected_chunk_id(
    document_id: str,
    chunk_index: int,
    locator: str,
    chunk_sha256: str,
) -> str:
    return hashlib.sha256(
        f"{document_id}\0{chunk_index}\0{locator}\0{chunk_sha256}".encode("utf-8")
    ).hexdigest()[:40]


def _require_corpus_identity(corpus: CaseMaterialCorpus) -> None:
    with _CORPUS_SEAL_LOCK:
        registered = _CORPUS_SEAL_REGISTRY.get(id(corpus))
    if registered is None or registered[0]() is not corpus:
        raise CaseMaterialRagError("case-material corpus was not issued by this builder")
    if not hmac.compare_digest(registered[1], corpus._seal):
        raise CaseMaterialRagError("case-material corpus seal is invalid")
    if not hmac.compare_digest(_corpus_seal(corpus), corpus._seal):
        raise CaseMaterialRagError("case-material corpus content no longer matches its builder seal")


def validate_case_material_corpus(corpus: CaseMaterialCorpus) -> CaseMaterialCorpus:
    """Fail closed unless this exact corpus is builder-issued and fully coherent."""

    if not isinstance(corpus, CaseMaterialCorpus):
        raise TypeError("expected CaseMaterialCorpus")
    _require_corpus_identity(corpus)
    _require_session_id(corpus.session_id)
    if not all(isinstance(value, str) and value for value in (
        corpus.case_id,
        corpus.subject_entity_id,
        corpus.subject_name,
        corpus.cutoff_date,
    )):
        raise CaseMaterialRagError("case-material corpus has incomplete case scope")
    if not isinstance(corpus.documents, tuple) or not isinstance(corpus.chunks, tuple):
        raise CaseMaterialRagError("case-material corpus collections must be immutable tuples")
    if not corpus.documents or not corpus.chunks:
        raise CaseMaterialRagError("case-material corpus must contain documents and chunks")
    if any(not isinstance(document, CaseMaterialDocument) for document in corpus.documents):
        raise CaseMaterialRagError("case-material corpus contains an invalid document")
    if any(not isinstance(chunk, CaseMaterialChunk) for chunk in corpus.chunks):
        raise CaseMaterialRagError("case-material corpus contains an invalid chunk")

    documents_by_id = {document.document_id: document for document in corpus.documents}
    if len(documents_by_id) != len(corpus.documents):
        raise CaseMaterialRagError("case-material corpus has duplicate document ids")
    material_ids = {document.material_id for document in corpus.documents}
    if len(material_ids) != len(corpus.documents):
        raise CaseMaterialRagError("case-material corpus has duplicate material documents")

    chunks_by_document: dict[str, list[CaseMaterialChunk]] = {
        document_id: [] for document_id in documents_by_id
    }
    chunk_ids: set[str] = set()
    for chunk in corpus.chunks:
        if chunk.chunk_id in chunk_ids:
            raise CaseMaterialRagError("case-material corpus has duplicate chunk ids")
        chunk_ids.add(chunk.chunk_id)
        if chunk.document_id not in chunks_by_document:
            raise CaseMaterialRagError("case-material chunk belongs to an unknown document")
        chunks_by_document[chunk.document_id].append(chunk)

    document_fields = (
        "session_id", "case_id", "subject_entity_id", "subject_name", "material_id",
        "material_type", "material_sha256", "provenance", "source_channel", "issuer",
        "cutoff_date", "date_kind", "factual_date", "document_date", "publication_date",
        "observed_at", "reporting_period_start", "reporting_period_end", "date_unknown_reason",
    )
    for document in corpus.documents:
        if (
            document.session_id != corpus.session_id
            or document.case_id != corpus.case_id
            or document.subject_entity_id != corpus.subject_entity_id
            or document.subject_name != corpus.subject_name
            or document.cutoff_date != corpus.cutoff_date
            or document.document_id != _expected_document_id(
                corpus.case_id,
                corpus.subject_entity_id,
                document.material_id,
                document.material_sha256,
            )
        ):
            raise CaseMaterialRagError("case-material document does not match corpus scope or identity")
        if not document.material_sha256 or len(document.material_sha256) != 64:
            raise CaseMaterialRagError("case-material document has an invalid material digest")
        document_chunks = sorted(
            chunks_by_document[document.document_id], key=lambda item: item.chunk_index
        )
        if not document_chunks or document.chunk_count != len(document_chunks):
            raise CaseMaterialRagError("case-material document has an invalid chunk count")
        if [chunk.chunk_index for chunk in document_chunks] != list(range(len(document_chunks))):
            raise CaseMaterialRagError("case-material document chunk indexes are not stable")
        locators: set[str] = set()
        for chunk in document_chunks:
            if chunk.locator in locators:
                raise CaseMaterialRagError("case-material document has duplicate chunk locators")
            locators.add(chunk.locator)
            if any(getattr(chunk, field_name) != getattr(document, field_name)
                   for field_name in document_fields):
                raise CaseMaterialRagError("case-material chunk does not match its document metadata")
            if chunk.chunk_sha256 != _sha256_text(chunk.text):
                raise CaseMaterialRagError("case-material chunk text hash is invalid")
            if chunk.chunk_id != _expected_chunk_id(
                document.document_id, chunk.chunk_index, chunk.locator, chunk.chunk_sha256
            ):
                raise CaseMaterialRagError("case-material chunk identity is invalid")
            header = _chunk_header(chunk, chunk.locator)
            if not chunk.text.startswith(f"{header}\n") or chunk.text == f"{header}\n":
                raise CaseMaterialRagError("case-material chunk text does not have its canonical header")
            if _validate_material_date(chunk) != chunk.eligible_for_structured_evidence:
                raise CaseMaterialRagError("case-material chunk date state is inconsistent")
    return corpus


def build_case_material_corpus(
    package: LoadedDueDiligenceCasePackage,
    session_id: str,
    subject_entity_id: str | None = None,
) -> CaseMaterialCorpus:
    """Build stable in-memory chunks for one explicitly scoped case subject.

    The function intentionally cannot open files.  It receives no case path and
    only processes bytes that were materialised by the production loader.
    """

    package = _require_package(package)
    # This must happen before inspecting any public package field.  A frozen
    # dataclass alone is not an authorization capability: this loader-issued,
    # process-local seal binds the complete case and material descriptor set.
    validate_loaded_due_diligence_case_package(package)
    session_id = _require_session_id(session_id)
    case = package.case
    case_id = case.manifest.case_id
    selected_subject_id = subject_entity_id or case.manifest.primary_subject_id
    if not isinstance(selected_subject_id, str) or not selected_subject_id.strip():
        raise ValueError("subject_entity_id must be a non-empty string when provided")
    selected_subject_id = selected_subject_id.strip()
    entities = {entity.entity_id: entity for entity in case.entities}
    subject = entities.get(selected_subject_id)
    if subject is None:
        raise CaseMaterialRagError("selected subject is missing from the loaded case")
    if package.case_id != case_id:
        raise CaseMaterialRagError("package identity does not match its loaded case")

    documents: list[CaseMaterialDocument] = []
    chunks: list[CaseMaterialChunk] = []
    total_bytes = 0
    selected_materials = [
        material for material in package.materials
        if material.subject_entity_id == selected_subject_id
    ]
    if not selected_materials:
        raise CaseMaterialRagError("selected subject has no authorized case materials")
    for material in sorted(package.materials, key=lambda item: item.material_id):
        if material.case_id != case_id or material.cutoff_date != case.manifest.cutoff_date.isoformat():
            raise CaseMaterialRagError(f"material {material.material_id} has a foreign case scope")
        if material.subject_entity_id != selected_subject_id:
            # Other-subject materials stay present in the sealed package, but are
            # deliberately excluded from this explicitly selected corpus.
            continue
        if material.subject_name != subject.name:
            raise CaseMaterialRagError(f"material {material.material_id} subject name does not match case")
        if not material.sha256 or hashlib.sha256(material.content_bytes).hexdigest() != material.sha256:
            raise CaseMaterialRagError(f"material {material.material_id} digest verification failed")
        total_bytes += len(material.content_bytes)
        if total_bytes > _MAX_TOTAL_BYTES:
            raise CaseMaterialRagError("case-material corpus exceeds the total byte limit")

        eligible = _validate_material_date(material)
        document_id = _expected_document_id(
            case_id, selected_subject_id, material.material_id, material.sha256
        )
        units = _material_units(material)
        document_chunks: list[CaseMaterialChunk] = []
        for chunk_index, (locator, body) in enumerate(units):
            text = f"{_chunk_header(material, locator)}\n{body}"
            chunk_sha256 = _sha256_text(text)
            chunk_id = _expected_chunk_id(document_id, chunk_index, locator, chunk_sha256)
            document_chunks.append(CaseMaterialChunk(
                session_id=session_id,
                case_id=case_id,
                subject_entity_id=selected_subject_id,
                subject_name=subject.name,
                document_id=document_id,
                material_id=material.material_id,
                material_type=material.material_type,
                material_sha256=material.sha256,
                chunk_id=chunk_id,
                chunk_sha256=chunk_sha256,
                locator=locator,
                chunk_index=chunk_index,
                text=text,
                provenance=material.provenance,
                source_channel=material.source_channel,
                issuer=material.issuer,
                cutoff_date=material.cutoff_date,
                date_kind=material.date_kind,
                factual_date=material.factual_date,
                document_date=material.document_date,
                publication_date=material.publication_date,
                observed_at=material.observed_at,
                reporting_period_start=material.reporting_period_start,
                reporting_period_end=material.reporting_period_end,
                date_unknown_reason=material.date_unknown_reason,
                eligible_for_structured_evidence=eligible,
            ))
        documents.append(CaseMaterialDocument(
            session_id=session_id,
            case_id=case_id,
            subject_entity_id=selected_subject_id,
            subject_name=subject.name,
            document_id=document_id,
            material_id=material.material_id,
            material_type=material.material_type,
            material_sha256=material.sha256,
            provenance=material.provenance,
            source_channel=material.source_channel,
            issuer=material.issuer,
            cutoff_date=material.cutoff_date,
            date_kind=material.date_kind,
            factual_date=material.factual_date,
            document_date=material.document_date,
            publication_date=material.publication_date,
            observed_at=material.observed_at,
            reporting_period_start=material.reporting_period_start,
            reporting_period_end=material.reporting_period_end,
            date_unknown_reason=material.date_unknown_reason,
            chunk_count=len(document_chunks),
        ))
        chunks.extend(document_chunks)

    if not documents or not chunks:
        raise CaseMaterialRagError("selected subject has no usable materials for offline retrieval")
    corpus = CaseMaterialCorpus(
        session_id=session_id,
        case_id=case_id,
        subject_entity_id=selected_subject_id,
        subject_name=subject.name,
        cutoff_date=case.manifest.cutoff_date.isoformat(),
        documents=tuple(documents),
        chunks=tuple(chunks),
    )
    _register_case_material_corpus(corpus)
    return validate_case_material_corpus(corpus)


def _tokens(value: str) -> tuple[str, ...]:
    """Tokenise ASCII identifiers and individual CJK characters deterministically."""

    ascii_tokens = [match.group(0).lower() for match in _ASCII_TOKEN_RE.finditer(value)]
    cjk_tokens = [char for char in value if "\u4e00" <= char <= "\u9fff"]
    return tuple(ascii_tokens + cjk_tokens)


def _metadata_for_chunk(chunk: CaseMaterialChunk) -> dict[str, Any]:
    return {
        "version": RAG_VERSION,
        "session_id": chunk.session_id,
        "case_id": chunk.case_id,
        "subject_entity_id": chunk.subject_entity_id,
        "subject_name": chunk.subject_name,
        "document_id": chunk.document_id,
        "material_id": chunk.material_id,
        "material_type": chunk.material_type,
        "material_sha256": chunk.material_sha256,
        "chunk_id": chunk.chunk_id,
        "chunk_sha256": chunk.chunk_sha256,
        "locator": chunk.locator,
        "chunk_index": chunk.chunk_index,
        "cutoff_date": chunk.cutoff_date,
        "provenance": chunk.provenance,
        "source_channel": chunk.source_channel,
        "issuer": chunk.issuer,
        "date_kind": chunk.date_kind,
        "factual_date": chunk.factual_date,
        "document_date": chunk.document_date,
        "publication_date": chunk.publication_date,
        "observed_at": chunk.observed_at,
        "reporting_period_start": chunk.reporting_period_start,
        "reporting_period_end": chunk.reporting_period_end,
        "date_unknown_reason": chunk.date_unknown_reason,
        "eligible_for_structured_evidence": chunk.eligible_for_structured_evidence,
    }


def _result_for_chunk(chunk: CaseMaterialChunk) -> dict[str, Any]:
    metadata = _metadata_for_chunk(chunk)
    return {
        "result_kind": RAG_VERSION,
        "authorized_case_material": True,
        "summary": chunk.text,
        "snippet": chunk.text[:200],
        "title": f"{chunk.material_id} — {chunk.material_type}",
        "url": f"local://case-material/{chunk.case_id}/{chunk.material_id}/{chunk.chunk_id}",
        "source_name": f"案例授权材料／{chunk.issuer}",
        "document_id": chunk.document_id,
        "chunk_index": chunk.chunk_index,
        "case_material": metadata,
    }


def _require_corpus(value: Any) -> CaseMaterialCorpus:
    if not isinstance(value, CaseMaterialCorpus):
        raise TypeError("offline retrieval accepts only CaseMaterialCorpus")
    return validate_case_material_corpus(value)


def _require_query(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("query must be a string")
    query = value.strip()
    if not query or len(query) > _MAX_QUERY_CHARS or "\x00" in query:
        raise ValueError("query must be non-empty, NUL-free, and at most 2048 characters")
    if not _tokens(query):
        raise ValueError("query must contain searchable ASCII or CJK text")
    return query


def _require_top_k(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("top_k must be an integer")
    if not 1 <= value <= _MAX_TOP_K:
        raise ValueError(f"top_k must be between 1 and {_MAX_TOP_K}")
    return value


def retrieve_case_materials(
    corpus: CaseMaterialCorpus,
    query: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Return deterministically ranked authorized case-material chunks."""

    corpus = _require_corpus(corpus)
    query = _require_query(query)
    top_k = _require_top_k(top_k)
    query_terms = set(_tokens(query))
    ranked: list[tuple[int, CaseMaterialChunk]] = []
    for chunk in corpus.chunks:
        frequency = Counter(_tokens(chunk.text))
        score = sum(frequency[term] for term in query_terms)
        if score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: (
        -item[0], item[1].material_id, item[1].locator, item[1].chunk_index, item[1].chunk_id,
    ))
    return [_result_for_chunk(chunk) for _score, chunk in ranked[:top_k]]


def _find_chunk(corpus: CaseMaterialCorpus, chunk_id: str) -> CaseMaterialChunk:
    matches = [chunk for chunk in corpus.chunks if chunk.chunk_id == chunk_id]
    if len(matches) != 1:
        raise CaseMaterialRagError("result references an unknown or ambiguous authorized chunk")
    return matches[0]


def validate_case_material_result(
    corpus: CaseMaterialCorpus,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless a result exactly belongs to this corpus and chunk.

    It rejects generic KB rows as well as rows copied from another session,
    case, subject, or modified after retrieval.  Its result kind is deliberately
    distinct from the existing local-KB bridge contract.
    """

    corpus = _require_corpus(corpus)
    if not isinstance(result, Mapping):
        raise TypeError("result must be a mapping")
    canonical_keys = {
        "result_kind", "authorized_case_material", "summary", "snippet", "title", "url",
        "source_name", "document_id", "chunk_index", "case_material",
    }
    if set(result) != canonical_keys:
        raise CaseMaterialRagError("case-material result does not use the canonical result shape")
    if result.get("result_kind") != RAG_VERSION or result.get("authorized_case_material") is not True:
        raise CaseMaterialRagError("result is not an authorized case-material RAG result")
    metadata = result.get("case_material")
    if not isinstance(metadata, Mapping):
        raise CaseMaterialRagError("ordinary KB result is not a case-material result")

    for key, expected in (
        ("version", RAG_VERSION),
        ("session_id", corpus.session_id),
        ("case_id", corpus.case_id),
        ("subject_entity_id", corpus.subject_entity_id),
        ("subject_name", corpus.subject_name),
        ("cutoff_date", corpus.cutoff_date),
    ):
        if metadata.get(key) != expected:
            raise CaseMaterialRagError(f"case-material result has mismatched {key}")

    chunk_id = metadata.get("chunk_id")
    if not isinstance(chunk_id, str) or not chunk_id:
        raise CaseMaterialRagError("case-material result has no valid chunk_id")
    chunk = _find_chunk(corpus, chunk_id)
    if _validate_material_date(chunk) != chunk.eligible_for_structured_evidence:
        raise CaseMaterialRagError(
            "case-material chunk has inconsistent structural-evidence eligibility"
        )
    expected_metadata = _metadata_for_chunk(chunk)
    if set(metadata) != set(expected_metadata):
        raise CaseMaterialRagError("case-material result metadata is not canonical")
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise CaseMaterialRagError(f"case-material result has mismatched {key}")

    if result.get("summary") != chunk.text:
        raise CaseMaterialRagError("case-material result summary does not match the authorized chunk")
    if _sha256_text(str(result["summary"])) != chunk.chunk_sha256:
        raise CaseMaterialRagError("case-material result summary hash does not match the authorized chunk")
    if result.get("snippet") != chunk.text[:200]:
        raise CaseMaterialRagError("case-material result snippet does not match the authorized chunk")
    if result.get("title") != f"{chunk.material_id} — {chunk.material_type}":
        raise CaseMaterialRagError("case-material result has mismatched title")
    if result.get("url") != f"local://case-material/{chunk.case_id}/{chunk.material_id}/{chunk.chunk_id}":
        raise CaseMaterialRagError("case-material result has mismatched URL")
    if result.get("source_name") != f"案例授权材料／{chunk.issuer}":
        raise CaseMaterialRagError("case-material result has mismatched source identity")
    if result.get("document_id") != chunk.document_id or result.get("chunk_index") != chunk.chunk_index:
        raise CaseMaterialRagError("case-material result has mismatched document identity")
    return dict(expected_metadata)


__all__ = [
    "CaseMaterialChunk",
    "CaseMaterialCorpus",
    "CaseMaterialDocument",
    "CaseMaterialRagError",
    "RAG_VERSION",
    "build_case_material_corpus",
    "retrieve_case_materials",
    "validate_case_material_corpus",
    "validate_case_material_result",
]
