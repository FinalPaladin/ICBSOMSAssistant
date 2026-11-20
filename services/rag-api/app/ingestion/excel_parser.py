"""Excel (.xlsx): mỗi dòng -> câu 'Cột: giá trị', gom theo nhóm dòng, giữ sheet_name/row_range."""
import re
import unicodedata

from openpyxl import load_workbook

from .base import Chunk

ROWS_PER_CHUNK = 12
MAX_CHARS = 1500


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", str(v))).strip()


def parse_excel(path: str, title: str) -> list[Chunk]:
    wb = load_workbook(path, data_only=True)
    chunks: list[Chunk] = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        grid = [[_s(c) for c in row] for row in ws.iter_rows(values_only=True)]
        # lan giá trị ô gộp
        for rng in ws.merged_cells.ranges:
            val = _s(ws.cell(rng.min_row, rng.min_col).value)
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if r - 1 < len(grid) and c - 1 < len(grid[r - 1]):
                        grid[r - 1][c - 1] = val
        rows = [(i + 1, r) for i, r in enumerate(grid) if any(r)]
        if not rows:
            continue
        header_no, header = rows[0]
        multi_col = sum(1 for v in header if v) >= 2
        body = rows[1:] if multi_col else rows
        sentences: list[tuple[int, str]] = []
        for rno, r in body:
            if multi_col:
                parts = [f"{header[i] or f'Cột {i + 1}'}: {v}" for i, v in enumerate(r) if v]
                sentences.append((rno, "; ".join(parts)))
            else:
                sentences.append((rno, " ".join(v for v in r if v)))
        group: list[tuple[int, str]] = []
        size = 0

        def emit():
            if not group:
                return
            a, b = group[0][0], group[-1][0]
            text = "\n".join(f"Dòng {n}: {t}" for n, t in group)
            chunks.append(Chunk(
                text=text, section=f"Sheet {ws.title} (dòng {a}-{b})",
                meta={"sheet_name": ws.title, "row_range": f"{a}-{b}"}))

        for n, t in sentences:
            if group and (len(group) >= ROWS_PER_CHUNK or size + len(t) > MAX_CHARS):
                emit()
                group, size = [], 0
            group.append((n, t[:MAX_CHARS]))
            size += len(t)
        emit()
    return chunks
