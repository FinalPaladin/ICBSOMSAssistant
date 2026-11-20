"""Điều phối ingest: parse -> embed -> upsert Qdrant. Chạy nền sau khi upload."""
import logging
import os
import uuid

from qdrant_client import models as qm

from .. import db, ollama_client, vectorstore
from .base import Chunk, split_text
from .docx_parser import parse_docx
from .drawio_parser import parse_drawio
from .excel_parser import parse_excel

log = logging.getLogger("ingest")

SUPPORTED = {".docx", ".xlsx", ".drawio", ".xml", ".png", ".jpg", ".jpeg"}
FORBIDDEN = {".docm", ".xlsm", ".exe", ".bat", ".js", ".vbs"}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}


def source_type_of(ext: str) -> str:
    if ext == ".docx":
        return "docx"
    if ext == ".xlsx":
        return "xlsx"
    if ext in IMAGE_EXT:
        return "image"
    return "drawio"


def parse_file(path: str, ext: str, title: str) -> list[Chunk]:
    if ext == ".docx":
        return parse_docx(path, title)
    if ext == ".xlsx":
        return parse_excel(path, title)
    if ext in (".drawio", ".xml"):
        return parse_drawio(path, title)
    if ext in IMAGE_EXT:
        text = ollama_client.describe_image(path).strip()
        if not text or "BỎ_QUA" in text.upper().replace(" ", "_"):
            raise ValueError("Ảnh không có nội dung nghiệp vụ để đọc")
        return [Chunk(text=t, section="Nội dung ảnh") for t in split_text(text)]
    raise ValueError(f"Định dạng {ext} chưa được hỗ trợ")


def ingest_document(doc_id: str) -> None:
    doc = db.get_document(doc_id)
    if not doc:
        return
    db.set_document_status(doc_id, "processing")
    try:
        ext = os.path.splitext(doc["file_name"])[1].lower()
        chunks = parse_file(doc["file_path"], ext, doc["title"])
        chunks = [c for c in chunks if c.text.strip()]
        if not chunks:
            raise ValueError("Không trích xuất được nội dung từ file")

        embed_inputs = [f"{doc['title']} | {c.section}\n{c.text}" for c in chunks]
        vectors: list[list[float]] = []
        for i in range(0, len(embed_inputs), 8):
            vectors += ollama_client.embed(embed_inputs[i:i + 8])
        vectorstore.ensure_collection(len(vectors[0]))

        points = []
        for idx, (c, v) in enumerate(zip(chunks, vectors)):
            points.append(qm.PointStruct(
                id=str(uuid.uuid4()), vector=v,
                payload={
                    "doc_id": doc_id, "doc_title": doc["title"], "file_name": doc["file_name"],
                    "source_type": doc["source_type"], "section": c.section, "chunk_index": idx,
                    "text": c.text, "header": f"{doc['title']} – {c.section}",
                    "allowed_roles": doc["allowed_roles"], "required_permissions": doc["required_permissions"],
                    "is_public": doc["is_public"], "screens": doc["screens"], "is_active": True,
                    **{k: v2 for k, v2 in c.meta.items() if k not in ("warnings", "note")},
                }))
        vectorstore.upsert(points)
        notes = [c.meta["note"] for c in chunks if c.meta.get("note")]
        w = max((c.meta.get("warnings", 0) for c in chunks), default=0)
        if w:
            notes.append(f"Cảnh báo: {w} mũi tên trong sơ đồ không nối đủ 2 đầu nên bị bỏ qua.")
        db.set_document_status(doc_id, "indexed", error=" ".join(notes), chunks=len(points))
        log.info("Indexed %s: %d chunks", doc["file_name"], len(points))
    except Exception as e:  # noqa: BLE001
        log.exception("Ingest failed for %s", doc_id)
        db.set_document_status(doc_id, "failed", error=str(e)[:500])
