"""PostgreSQL: tài liệu, log chat, audit, hàng đợi ingest (bền vững, sống sót khi restart)."""
import os
import time
import uuid

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  title TEXT, file_name TEXT, source_type TEXT, sha256 TEXT,
  status TEXT, error TEXT, chunks INTEGER DEFAULT 0,
  allowed_roles JSONB DEFAULT '[]', required_permissions JSONB DEFAULT '[]',
  is_public BOOLEAN DEFAULT FALSE, screens JSONB DEFAULT '[]',
  uploaded_by TEXT, uploaded_at TIMESTAMPTZ DEFAULT now(), file_path TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT, user_id TEXT, user_name TEXT, roles JSONB,
  screen_id TEXT, question TEXT, answer TEXT,
  refused BOOLEAN DEFAULT FALSE, refuse_reason TEXT, top_score REAL,
  latency_ms INTEGER, model TEXT, sources JSONB, feedback INTEGER,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY, actor TEXT, action TEXT, detail JSONB, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ingest_jobs (
  id BIGSERIAL PRIMARY KEY, doc_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT now(), started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ
);
"""


def init() -> None:
    global _pool
    os.makedirs(settings.files_dir, exist_ok=True)
    pool = ConnectionPool(settings.database_url, min_size=1, max_size=8,
                          kwargs={"row_factory": dict_row}, open=False)
    last = None
    for _ in range(30):  # chờ Postgres sẵn sàng
        try:
            pool.open(wait=True, timeout=5)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    else:
        raise RuntimeError(f"Không kết nối được PostgreSQL: {last}")
    _pool = pool
    with _pool.connection() as c:
        c.execute(SCHEMA)
        c.execute("UPDATE ingest_jobs SET status='pending' WHERE status='running'")


def _exec(sql: str, params=None) -> None:
    with _pool.connection() as c:
        c.execute(sql, params)


def _all(sql: str, params=None) -> list[dict]:
    with _pool.connection() as c:
        return c.execute(sql, params).fetchall()


# ---- documents ----
def insert_document(d: dict) -> None:
    _exec(
        "INSERT INTO documents (id,title,file_name,source_type,sha256,status,allowed_roles,required_permissions,"
        "is_public,screens,uploaded_by,file_path) VALUES (%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s)",
        (d["id"], d["title"], d["file_name"], d["source_type"], d["sha256"], Jsonb(d["allowed_roles"]),
         Jsonb(d["required_permissions"]), d["is_public"], Jsonb(d["screens"]), d["uploaded_by"], d["file_path"]))


def get_document(doc_id: str) -> dict | None:
    rows = _all("SELECT * FROM documents WHERE id=%s", (doc_id,))
    return rows[0] if rows else None


def list_documents() -> list[dict]:
    return _all("SELECT * FROM documents WHERE status NOT IN ('deleted','replaced') ORDER BY uploaded_at DESC")


def find_documents_by_file_name(file_name: str) -> list[dict]:
    return _all("SELECT * FROM documents WHERE file_name=%s AND status NOT IN ('deleted','replaced')", (file_name,))


def set_document_status(doc_id: str, status: str, error: str = "", chunks: int | None = None) -> None:
    if chunks is None:
        _exec("UPDATE documents SET status=%s, error=%s WHERE id=%s", (status, error, doc_id))
    else:
        _exec("UPDATE documents SET status=%s, error=%s, chunks=%s WHERE id=%s", (status, error, chunks, doc_id))


def update_document_access(doc_id: str, allowed_roles, required_permissions, is_public, screens) -> None:
    _exec("UPDATE documents SET allowed_roles=%s, required_permissions=%s, is_public=%s, screens=%s WHERE id=%s",
          (Jsonb(allowed_roles), Jsonb(required_permissions), is_public, Jsonb(screens), doc_id))


# ---- ingest queue ----
def enqueue_ingest(doc_id: str) -> None:
    _exec("INSERT INTO ingest_jobs (doc_id) VALUES (%s)", (doc_id,))


def claim_job() -> dict | None:
    rows = _all(
        "UPDATE ingest_jobs SET status='running', started_at=now() WHERE id = ("
        " SELECT id FROM ingest_jobs WHERE status='pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
        ") RETURNING id, doc_id")
    return rows[0] if rows else None


def finish_job(job_id: int, ok: bool) -> None:
    _exec("UPDATE ingest_jobs SET status=%s, finished_at=now() WHERE id=%s", ("done" if ok else "failed", job_id))


# ---- chat log ----
def log_message(m: dict) -> str:
    mid = m.get("id") or str(uuid.uuid4())
    _exec(
        "INSERT INTO messages (id,conversation_id,user_id,user_name,roles,screen_id,question,answer,refused,"
        "refuse_reason,top_score,latency_ms,model,sources) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (mid, m["conversation_id"], m["user_id"], m["user_name"], Jsonb(m["roles"]), m.get("screen_id"),
         m["question"], m["answer"], m["refused"], m.get("refuse_reason", ""), m.get("top_score"),
         m.get("latency_ms"), m.get("model"), Jsonb(m.get("sources", []))))
    return mid


def get_history(conversation_id: str, user_id: str, limit: int = 3) -> list[dict]:
    rows = _all(
        "SELECT question, answer FROM messages WHERE conversation_id=%s AND user_id=%s AND refused=FALSE "
        "ORDER BY created_at DESC LIMIT %s", (conversation_id, user_id, limit))
    return list(reversed(rows))


def set_feedback(message_id: str, user_id: str, rating: int) -> None:
    _exec("UPDATE messages SET feedback=%s WHERE id=%s AND user_id=%s", (rating, message_id, user_id))


def list_messages(limit: int = 100) -> list[dict]:
    rows = _all("SELECT * FROM messages ORDER BY created_at DESC LIMIT %s", (limit,))
    for r in rows:
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return rows


def audit(actor: str, action: str, detail: dict) -> None:
    _exec("INSERT INTO audit_logs (id,actor,action,detail) VALUES (%s,%s,%s,%s)",
          (str(uuid.uuid4()), actor, action, Jsonb(detail)))
