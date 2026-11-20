from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    section: str = ""
    meta: dict = field(default_factory=dict)


def split_text(text: str, max_chars: int = 1500, overlap: int = 150) -> list[str]:
    """Gom theo dòng/đoạn; đoạn quá dài thì cắt cứng có overlap."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > max_chars:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(line[:max_chars])
            line = line[max_chars - overlap:]
        if len(buf) + len(line) + 1 > max_chars and buf:
            pieces.append(buf)
            tail = buf[-overlap:] if overlap else ""
            buf = (tail + "\n" + line).strip() if tail else line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf.strip():
        pieces.append(buf)
    return [p.strip() for p in pieces if p.strip()]
