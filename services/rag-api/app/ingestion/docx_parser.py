"""Word (.docx): giữ cấu trúc heading làm section_path, bảng -> Markdown."""
import hashlib
import re
import unicodedata

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .. import ollama_client
from ..config import settings
from .base import Chunk, split_text


def _heading_level(p: Paragraph) -> int:
    name = (p.style.name if p.style is not None else "").lower()
    if name == "title":
        return 1
    m = re.match(r"(heading|tiêu đề)\s*(\d+)", name)
    if m:
        return max(1, min(int(m.group(2)), 6))
    # Heuristic: dòng ngắn, in đậm toàn bộ, không kết thúc bằng dấu chấm => tiêu đề phụ (level 4 để không xoá mục cha cấp 2/3)
    text = p.text.strip()
    if text and len(text) <= 90 and not text.endswith((".", ":", ";", ",")):
        runs = [r for r in p.runs if r.text.strip()]
        if runs and all(r.bold for r in runs):
            return 4
    return 0


def _table_md(table: Table) -> str:
    rows = []
    for row in table.rows:
        cells, seen = [], set()
        for c in row.cells:
            if id(c._tc) in seen:
                continue
            seen.add(id(c._tc))
            cells.append(re.sub(r"\s+", " ", c.text).strip().replace("|", "/"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in rows[0]) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
V_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
IMG_TYPES = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp"}
MIN_IMAGE_BYTES = 8000  # bỏ icon/logo nhỏ


def _image_rids(el) -> list[str]:
    rids = [b.get(R_NS + "embed") for b in el.iter(A_BLIP)]
    rids += [v.get(R_NS + "id") for v in el.iter(V_IMAGEDATA)]
    return [r for r in rids if r]


def parse_docx(path: str, title: str) -> list[Chunk]:
    doc = Document(path)
    sections: list[tuple[list[str], list[str]]] = []
    stack: list[str] = []
    cur_path: list[str] = []
    cur_lines: list[str] = []
    img_seen: set[str] = set()
    img = {"ok": 0, "skipped_novision": 0, "failed": 0, "ignored": 0}

    def flush():
        if any(l.strip() for l in cur_lines):
            sections.append((list(cur_path), list(cur_lines)))

    def read_images(el):
        for rid in _image_rids(el):
            part = doc.part.related_parts.get(rid)
            if part is None or part.content_type not in IMG_TYPES or len(part.blob) < MIN_IMAGE_BYTES:
                img["ignored"] += 1
                continue
            h = hashlib.sha1(part.blob).hexdigest()
            if h in img_seen:
                continue
            img_seen.add(h)
            if not ollama_client.vision_enabled():
                img["skipped_novision"] += 1
                continue
            if img["ok"] + img["failed"] >= settings.max_images_per_doc:
                img["ignored"] += 1
                continue
            try:
                text = ollama_client.describe_image_bytes(part.blob).strip()
                if not text or "BỎ_QUA" in text.upper().replace(" ", "_"):
                    img["ignored"] += 1
                    continue
                cur_lines.append("[Nội dung hình minh hoạ] " + unicodedata.normalize("NFC", text))
                img["ok"] += 1
            except Exception:  # noqa: BLE001
                img["failed"] += 1

    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            text = unicodedata.normalize("NFC", p.text).strip()
            read_images(child)
            if not text:
                continue
            level = _heading_level(p)
            if level:
                flush()
                stack = stack[: level - 1]
                stack.append(text)
                cur_path, cur_lines = list(stack), []
            else:
                style = (p.style.name if p.style is not None else "").lower()
                cur_lines.append(("- " + text) if "list" in style else text)
        elif tag == "tbl":
            md = unicodedata.normalize("NFC", _table_md(Table(child, doc)))
            if md:
                cur_lines.append(md)
            read_images(child)
    flush()

    # Gom các tiểu mục ngắn thuộc cùng mục cha (cấp <= 2) để không bị tách rời Scope / Precondition / Flow
    merged: list[tuple[list[str], list[str]]] = []
    for path_list, lines in sections:
        sub_title = " > ".join(path_list[2:]) if len(path_list) > 2 else ""
        block = ([f"[{sub_title}]"] + lines) if sub_title else list(lines)
        block_len = sum(len(x) + 1 for x in block)
        if merged and len(path_list) >= 2 and len(merged[-1][0]) >= 2 and path_list[:2] == merged[-1][0][:2]:
            prev_len = sum(len(x) + 1 for x in merged[-1][1])
            if prev_len + block_len <= 1450:
                merged[-1][1].extend(block)
                continue
        merged.append((path_list[:2] if len(path_list) >= 2 else path_list, block))

    chunks: list[Chunk] = []
    for path_list, lines in merged:
        section = " > ".join(path_list) if path_list else "Nội dung"
        for piece in split_text("\n".join(lines)):
            chunks.append(Chunk(text=piece, section=section))

    notes = []
    if img["ok"]:
        notes.append(f"Đã đọc {img['ok']} hình trong file Word.")
    if img["skipped_novision"]:
        notes.append(f"{img['skipped_novision']} hình chưa được đọc (chưa bật VISION_MODEL).")
    if img["failed"]:
        notes.append(f"{img['failed']} hình đọc lỗi.")
    if notes and chunks:
        chunks[0].meta["note"] = " ".join(notes)
    return chunks
