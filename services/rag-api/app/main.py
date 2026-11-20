import json
import logging
import os
import re
import time
import uuid
import hashlib
from contextlib import asynccontextmanager
from typing import Any

import jwt
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, rag, screens, vectorstore
from .auth import User, get_chat_user, get_user, require_kb_admin, is_kb_admin
from .config import settings
from .ingestion import worker
from .ingestion.pipeline import FORBIDDEN, SUPPORTED, source_type_of

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    screens.load()
    worker.start()
    yield


app = FastAPI(title="OMS Assistant", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                   allow_headers=["*"], allow_credentials=False)


def _csv(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


@app.get("/")
def root():
    return RedirectResponse("/ui/")


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.env, "llm": settings.llm_model, "embed": settings.embed_model}


@app.get("/api/me")
def me(user: User = Depends(get_user)):
    return {"id": user.id, "name": user.name, "roles": user.roles, "permissions": user.permissions,
            "is_kb_admin": is_kb_admin(user)}


# ---------------- Chat ----------------
class ScreenCtx(BaseModel):
    screen_id: str | None = Field(default=None, max_length=100)
    state: dict[str, Any] | None = None


class ChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = Field(default=None, max_length=64)
    screen: ScreenCtx | None = None


@app.post("/api/chat")
async def chat(req: ChatReq, user: User = Depends(get_chat_user)):
    async def gen():
        async for event, data in rag.answer(user, req.message, req.conversation_id, req.screen):
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/screens")
def my_screens(user: User = Depends(get_user)):
    return [{"screen_id": s.screen_id, "name": s.name, "suggested_questions": s.suggested_questions}
            for s in screens.accessible(user)]


class FeedbackReq(BaseModel):
    message_id: str
    rating: int = Field(ge=-1, le=1)


@app.post("/api/feedback")
def feedback(req: FeedbackReq, user: User = Depends(get_user)):
    db.set_feedback(req.message_id, user.id, req.rating)
    return {"ok": True}


# ---------------- Admin ----------------
@app.get("/api/admin/screens")
def admin_screens(_: User = Depends(require_kb_admin)):
    return [{"screen_id": s.screen_id, "name": s.name} for s in screens.all_screens()]


@app.get("/api/admin/documents")
def admin_list_docs(_: User = Depends(require_kb_admin)):
    return db.list_documents()


@app.post("/api/admin/documents")
async def admin_upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    allowed_roles: str = Form(""),
    required_permissions: str = Form(""),
    is_public: bool = Form(False),
    screens_csv: str = Form("", alias="screens"),
    user: User = Depends(require_kb_admin),
):
    name = os.path.basename(file.filename or "upload")
    ext = os.path.splitext(name)[1].lower()
    if ext in FORBIDDEN or ext not in SUPPORTED:
        raise HTTPException(400, f"Định dạng {ext or '(không có)'} không được hỗ trợ. Hỗ trợ: {sorted(SUPPORTED)}")
    roles = _csv(allowed_roles)
    if not is_public and not roles:
        raise HTTPException(400, "Cần chọn ít nhất 1 role (allowed_roles) hoặc đánh dấu tài liệu chung (is_public).")
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File vượt quá {settings.max_upload_mb}MB")
    if not data:
        raise HTTPException(400, "File rỗng")

    doc_id = str(uuid.uuid4())
    path = os.path.join(settings.files_dir, f"{doc_id}{ext}")
    with open(path, "wb") as f:
        f.write(data)

    # Upload lại cùng tên file => thay thế phiên bản cũ
    for old in db.find_documents_by_file_name(name):
        vectorstore.delete_doc(old["id"])
        db.set_document_status(old["id"], "replaced")
        db.audit(user.id, "replace_document", {"old": old["id"], "file": name})

    doc = {
        "id": doc_id, "title": title.strip() or os.path.splitext(name)[0], "file_name": name,
        "source_type": source_type_of(ext), "sha256": hashlib.sha256(data).hexdigest(),
        "allowed_roles": roles, "required_permissions": _csv(required_permissions),
        "is_public": is_public, "screens": _csv(screens_csv), "uploaded_by": user.id, "file_path": path,
    }
    db.insert_document(doc)
    db.audit(user.id, "upload_document", {"id": doc_id, "file": name, "roles": roles, "public": is_public})
    db.enqueue_ingest(doc_id)
    return {"id": doc_id, "status": "pending"}


class AccessReq(BaseModel):
    allowed_roles: list[str] = []
    required_permissions: list[str] = []
    is_public: bool = False
    screens: list[str] = []


@app.patch("/api/admin/documents/{doc_id}/access")
def admin_update_access(doc_id: str, req: AccessReq, user: User = Depends(require_kb_admin)):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    if not req.is_public and not req.allowed_roles:
        raise HTTPException(400, "Cần ít nhất 1 role hoặc is_public")
    db.update_document_access(doc_id, req.allowed_roles, req.required_permissions, req.is_public, req.screens)
    vectorstore.set_access(doc_id, req.allowed_roles, req.required_permissions, req.is_public, req.screens)
    db.audit(user.id, "update_access", {"id": doc_id, "before": {k: doc[k] for k in
             ("allowed_roles", "required_permissions", "is_public", "screens")}, "after": req.model_dump()})
    return {"ok": True}


@app.delete("/api/admin/documents/{doc_id}")
def admin_delete(doc_id: str, user: User = Depends(require_kb_admin)):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    vectorstore.delete_doc(doc_id)
    db.set_document_status(doc_id, "deleted")
    try:
        os.remove(doc["file_path"])
    except OSError:
        pass
    db.audit(user.id, "delete_document", {"id": doc_id, "file": doc["file_name"]})
    return {"ok": True}


@app.get("/api/admin/documents/{doc_id}/chunks")
def admin_chunks(doc_id: str, _: User = Depends(require_kb_admin)):
    """Xem kết quả parse (đặc biệt hữu ích để kiểm tra flow Draw.io)."""
    return vectorstore.scroll_doc(doc_id)


@app.get("/api/admin/logs")
def admin_logs(limit: int = 100, _: User = Depends(require_kb_admin)):
    return db.list_messages(min(limit, 500))


# ---------------- Dev only ----------------
if settings.env == "dev":
    class DevTokenReq(BaseModel):
        sub: str = "dev.user"
        name: str = "Dev"
        surname: str = ""
        role: list[str] = []
        permission: list[str] = []

    @app.post("/dev/token")
    def dev_token(req: DevTokenReq):
        now = int(time.time())
        payload = {"sub": req.sub, "name": req.name, "surname": req.surname, "role": req.role,
                   "permission": req.permission, "iat": now, "exp": now + 8 * 3600, "jti": str(uuid.uuid4())}
        return {"token": jwt.encode(payload, settings.jwt_secret, algorithm="HS256")}


app.mount("/ui", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="ui")
