"""Client Ollama: embedding (sync), chat streaming (async), vision (sync)."""
import base64
import hashlib
import json
import os
from typing import AsyncIterator

import httpx

from .config import settings


def _options(num_predict: int | None = None) -> dict:
    opts = {
        "temperature": settings.temperature,
        "num_ctx": settings.num_ctx,
        "num_predict": num_predict or settings.num_predict,
        "repeat_penalty": 1.05,
        "stop": ["\nuser\n", "\nUser:", "\nassistant\n", "<|im_end|>", "<|endoftext|>"],
    }
    if settings.num_thread > 0:
        opts["num_thread"] = settings.num_thread
    return opts


def _chat_payload(messages: list[dict], stream: bool, num_predict: int | None = None) -> dict:
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": stream,
        "keep_alive": -1,
        "options": _options(num_predict),
    }
    if settings.llm_model.startswith("qwen3"):
        payload["think"] = False
    return payload


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    with httpx.Client(timeout=300) as c:
        r = c.post(f"{settings.ollama_url}/api/embed",
                   json={"model": settings.embed_model, "input": texts, "keep_alive": -1})
        r.raise_for_status()
        return r.json()["embeddings"]


async def chat_stream(messages: list[dict]) -> AsyncIterator[str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as c:
        async with c.stream("POST", f"{settings.ollama_url}/api/chat",
                            json=_chat_payload(messages, True)) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                piece = obj.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if obj.get("done"):
                    break


async def chat_once(messages: list[dict], num_predict: int = 80) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
        r = await c.post(f"{settings.ollama_url}/api/chat",
                         json=_chat_payload(messages, False, num_predict))
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()


def vision_enabled() -> bool:
    return bool(settings.vision_model)


def describe_image(path: str) -> str:
    with open(path, "rb") as f:
        return describe_image_bytes(f.read())


def describe_image_bytes(data: bytes) -> str:
    if not settings.vision_model:
        raise ValueError(
            "Chưa bật model đọc ảnh (VISION_MODEL). Hãy upload file .drawio gốc, "
            "hoặc cấu hình VISION_MODEL (vd qwen2.5vl:3b) rồi thử lại.")
    cache_dir = os.path.join(settings.data_dir, "image_cache")
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.sha1((settings.vision_model + ":").encode() + data).hexdigest()
    cache_file = os.path.join(cache_dir, f"{key}.txt")
    if os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            return f.read().strip()
    b64 = base64.b64encode(data).decode()
    prompt = (
        "Đây là ảnh trong tài liệu nghiệp vụ OMS (sơ đồ quy trình, ảnh chụp màn hình hoặc bảng). "
        "Hãy chép lại CHÍNH XÁC toàn bộ chữ nhìn thấy. Nếu là sơ đồ: mô tả tuần tự từng bước, các nhánh điều kiện "
        "(có/không) và bộ phận/vai trò thực hiện. Nếu là ảnh màn hình: liệt kê tên màn hình, các trường, nút và nhãn. "
        "Trả lời bằng tiếng Việt, chỉ mô tả những gì có trong ảnh, không suy đoán. Nếu ảnh chỉ là logo/trang trí "
        "không có nội dung nghiệp vụ, trả lời đúng một từ: BỎ_QUA.")
    with httpx.Client(timeout=900) as c:
        r = c.post(f"{settings.ollama_url}/api/chat", json={
            "model": settings.vision_model,
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            "stream": False, "keep_alive": 0, "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 1200},
        })
        r.raise_for_status()
        text = r.json().get("message", {}).get("content", "").strip()
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(text)
    return text
