"""Worker nền: lấy job từ bảng ingest_jobs (Postgres) và xử lý tuần tự (hợp với CPU-only)."""
import logging
import threading
import time

from .. import db
from .pipeline import ingest_document

log = logging.getLogger("ingest-worker")
_stop = threading.Event()


def _loop() -> None:
    while not _stop.is_set():
        try:
            job = db.claim_job()
        except Exception:  # noqa: BLE001
            log.exception("claim_job lỗi")
            time.sleep(5)
            continue
        if not job:
            time.sleep(2)
            continue
        ok = True
        try:
            ingest_document(job["doc_id"])
            doc = db.get_document(job["doc_id"])
            ok = bool(doc) and doc["status"] == "indexed"
        except Exception:  # noqa: BLE001
            log.exception("job %s lỗi", job["id"])
            ok = False
        db.finish_job(job["id"], ok)


def start() -> None:
    threading.Thread(target=_loop, name="ingest-worker", daemon=True).start()
