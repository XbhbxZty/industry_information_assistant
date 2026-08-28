"""Extract the downloaded real-case RAG whitelist into auditable text chunks.

This stage reads only ``raw_sources`` files that are present in both the acquisition
manifest and the cutoff-safe RAG manifest.  It never reads the generated silver
labels in ``reference`` or future facts in ``post_cutoff``.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from pypdf import PdfReader


DEFAULT_CHUNK_SIZE = 1800
DEFAULT_OVERLAP = 180


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1
        elif tag in {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif tag in {"p", "div", "section", "article", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


@dataclass(frozen=True)
class TextUnit:
    locator: str
    text: str


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _clean_text(text: str) -> str:
    text = html.unescape(text).replace("\x00", "")
    # Some malformed PDF CMaps yield lone UTF-16 surrogates. They are not valid
    # UTF-8 and must not be allowed to break deterministic JSONL generation.
    text = re.sub(r"[\ud800-\udfff]", "�", text)
    text = re.sub(r"[\t\f\v ]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf(path: Path) -> tuple[list[TextUnit], dict[str, Any]]:
    reader = PdfReader(path, strict=False)
    units: list[TextUnit] = []
    empty_pages = 0
    encrypted = bool(reader.is_encrypted)
    if encrypted:
        try:
            reader.decrypt("")
        except Exception:
            pass
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = _clean_text(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            units.append(TextUnit(locator=f"page:{index}", text=text))
        else:
            empty_pages += 1
    return units, {
        "parser": "pypdf",
        "page_count": len(reader.pages),
        "text_page_count": len(units),
        "empty_page_count": empty_pages,
        "encrypted": encrypted,
    }


def _extract_html(path: Path) -> tuple[list[TextUnit], dict[str, Any]]:
    raw = path.read_bytes()
    decoded = None
    encoding_used = None
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            decoded = raw.decode(encoding)
            encoding_used = encoding
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        decoded = raw.decode("utf-8", errors="replace")
        encoding_used = "utf-8-replace"
    parser = _VisibleTextParser()
    parser.feed(decoded)
    text = _clean_text("".join(parser.parts))
    units = [TextUnit(locator="html", text=text)] if text else []
    return units, {"parser": "html.parser", "encoding": encoding_used, "page_count": None}


def _extract_binary(path: Path) -> tuple[list[TextUnit], dict[str, Any]]:
    raw = path.read_bytes()
    text = _clean_text(raw.decode("utf-8", errors="replace"))
    units = [TextUnit(locator="binary", text=text)] if text else []
    return units, {"parser": "utf-8-replace", "page_count": None}


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and overlap must be in [0, size)")
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            window = text[start:end]
            candidates = [window.rfind(sep) for sep in ("\n\n", "。", "；", "\n", ". ")]
            boundary = max(candidates)
            if boundary >= size // 2:
                end = start + boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _chunk_units(units: list[TextUnit], size: int, overlap: int) -> list[TextUnit]:
    """Combine short adjacent pages before splitting to avoid tiny page chunks."""
    result: list[TextUnit] = []
    buffered: list[TextUnit] = []
    buffered_size = 0

    def flush() -> None:
        nonlocal buffered, buffered_size
        if not buffered:
            return
        first = buffered[0].locator
        last = buffered[-1].locator
        locator = first if first == last else f"{first}-{last}"
        joined = "\n\n".join(f"[{unit.locator}]\n{unit.text}" for unit in buffered)
        result.append(TextUnit(locator=locator, text=joined))
        buffered = []
        buffered_size = 0

    for unit in units:
        if len(unit.text) >= size:
            flush()
            for part in _split_text(unit.text, size, overlap):
                result.append(TextUnit(locator=unit.locator, text=part))
            continue
        projected = buffered_size + len(unit.text) + (2 if buffered else 0)
        if buffered and projected > size:
            flush()
        buffered.append(unit)
        buffered_size += len(unit.text) + (2 if len(buffered) > 1 else 0)
    flush()
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _prepare_document(
    case_id: str,
    case_dir: Path,
    rag_row: dict[str, Any],
    acquisition: dict[str, Any],
    source: dict[str, Any],
    chunk_size: int,
    overlap: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_id = str(rag_row["source_id"])
    path = Path(str(acquisition["local_path"])).resolve()
    allowed_root = (case_dir / "raw_sources").resolve()
    if allowed_root not in path.parents:
        raise ValueError(f"source path escapes raw_sources: {path}")
    if not path.exists():
        raise FileNotFoundError(path)
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != acquisition.get("sha256"):
        raise ValueError(f"sha256 mismatch for {source_id}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        units, parser_metadata = _extract_pdf(path)
    elif suffix in {".html", ".htm"}:
        units, parser_metadata = _extract_html(path)
    else:
        units, parser_metadata = _extract_binary(path)

    document_id = hashlib.sha256(
        f"{case_id}\0{source_id}\0{actual_sha256}".encode("utf-8")
    ).hexdigest()[:32]
    title = str(source.get("title") or source_id)
    publisher = str(source.get("publisher") or "")
    publication_date = str(source.get("publication_date") or "")
    event_date = str(source.get("event_date") or "")
    url = str(source.get("direct_url") or source.get("url") or rag_row.get("url") or "")
    chunks: list[dict[str, Any]] = []
    for unit in _chunk_units(units, chunk_size, overlap):
        chunk_index = len(chunks)
        citation = (
            f"[case_id={case_id}; source_id={source_id}; locator={unit.locator}]\n"
            f"标题：{title}\n发布机构：{publisher}\n发布日期：{publication_date}\n"
            f"事实/报告期：{event_date}\n来源：{url}\n"
        )
        content = citation + unit.text
        chunk_id = hashlib.sha256(
            f"{document_id}\0{chunk_index}\0{content}".encode("utf-8")
        ).hexdigest()[:40]
        chunks.append({
            "id": chunk_id,
            "doc_id": document_id,
            "kb_id": case_id,
            "filename": f"{source_id}_{path.name}",
            "content": content,
            "chunk_index": chunk_index,
            "source_id": source_id,
            "locator": unit.locator,
            "source_sha256": actual_sha256,
        })
    document = {
        "case_id": case_id,
        "source_id": source_id,
        "document_id": document_id,
        "title": title,
        "publisher": publisher,
        "publication_date": publication_date,
        "event_date": event_date,
        "url": url,
        "local_path": path.as_posix(),
        "sha256": actual_sha256,
        "bytes": path.stat().st_size,
        "chunk_count": len(chunks),
        "character_count": sum(len(unit.text) for unit in units),
        **parser_metadata,
    }
    return document, chunks


def prepare_all(
    processed_root: Path,
    case_ids: set[str] | None = None,
    workers: int = 4,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> dict[str, Any]:
    case_dirs = sorted(path for path in processed_root.glob("case_*") if path.is_dir())
    if case_ids:
        case_dirs = [path for path in case_dirs if path.name in case_ids]
    summaries: list[dict[str, Any]] = []

    for case_dir in case_dirs:
        case_id = case_dir.name
        rag = {row["source_id"]: row for row in _jsonl(case_dir / "rag_manifest.jsonl")}
        acquired = {
            row["source_id"]: row
            for row in _jsonl(case_dir / "acquisition_manifest.jsonl")
            if row.get("status") in {"downloaded", "existing"}
        }
        sources = {row["source_id"]: row for row in _jsonl(case_dir / "sources.jsonl")}
        eligible_ids = sorted(set(rag) & set(acquired) & set(sources))
        documents: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(
                    _prepare_document,
                    case_id,
                    case_dir,
                    rag[source_id],
                    acquired[source_id],
                    sources[source_id],
                    chunk_size,
                    overlap,
                ): source_id
                for source_id in eligible_ids
            }
            for future in as_completed(futures):
                source_id = futures[future]
                try:
                    document, document_chunks = future.result()
                    documents.append(document)
                    chunks.extend(document_chunks)
                    print(f"{case_id}/{source_id}: {len(document_chunks)} chunks", flush=True)
                except Exception as exc:
                    errors.append({
                        "source_id": source_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    })
                    print(f"{case_id}/{source_id}: ERROR {type(exc).__name__}: {exc}", flush=True)

        documents.sort(key=lambda row: row["source_id"])
        chunks.sort(key=lambda row: (row["source_id"], row["chunk_index"]))
        corpus_dir = case_dir / "corpus"
        _write_jsonl(corpus_dir / "documents.jsonl", documents)
        _write_jsonl(corpus_dir / "chunks.jsonl", chunks)
        report = {
            "case_id": case_id,
            "rag_whitelist_count": len(rag),
            "available_whitelist_count": len(eligible_ids),
            "parsed_document_count": len(documents),
            "failed_document_count": len(errors),
            "chunk_count": len(chunks),
            "character_count": sum(row["character_count"] for row in documents),
            "empty_text_documents": [row["source_id"] for row in documents if not row["chunk_count"]],
            "errors": errors,
            "chunk_size": chunk_size,
            "overlap": overlap,
            "input_policy": "rag_manifest ∩ successful_acquisition; raw_sources only",
            "reference_or_post_cutoff_read": False,
        }
        _write_json(corpus_dir / "preparation_report.json", report)
        summaries.append(report)

    summary = {
        "case_count": len(summaries),
        "rag_whitelist_count": sum(row["rag_whitelist_count"] for row in summaries),
        "available_whitelist_count": sum(row["available_whitelist_count"] for row in summaries),
        "parsed_document_count": sum(row["parsed_document_count"] for row in summaries),
        "ingestible_document_count": sum(
            row["parsed_document_count"] - len(row["empty_text_documents"])
            for row in summaries
        ),
        "failed_document_count": sum(row["failed_document_count"] for row in summaries),
        "chunk_count": sum(row["chunk_count"] for row in summaries),
        "character_count": sum(row["character_count"] for row in summaries),
        "cases": summaries,
    }
    if case_ids:
        suffix = "_".join(sorted(case_ids))
        _write_json(processed_root / f"corpus_preparation_summary_{suffix}.json", summary)
    else:
        _write_json(processed_root / "corpus_preparation_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed-root",
        type=Path,
        default=Path(__file__).resolve().parent / "real_cases_processed",
    )
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    args = parser.parse_args()
    summary = prepare_all(
        args.processed_root.resolve(),
        set(args.case_ids or []) or None,
        workers=args.workers,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed_document_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
