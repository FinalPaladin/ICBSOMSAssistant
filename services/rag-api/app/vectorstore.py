"""Vector store (Qdrant) + RBAC filter. secure_search là điểm DUY NHẤT truy vấn dữ liệu cho chat."""
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from .auth import User
from .config import settings

_client: QdrantClient | None = None


def client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url, timeout=60)
    return _client


def ensure_collection(dim: int) -> None:
    c = client()
    if c.collection_exists(settings.collection):
        return
    c.create_collection(
        settings.collection,
        vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
    )
    for field, schema in (
        ("allowed_roles", qm.PayloadSchemaType.KEYWORD),
        ("required_permissions", qm.PayloadSchemaType.KEYWORD),
        ("doc_id", qm.PayloadSchemaType.KEYWORD),
        ("screens", qm.PayloadSchemaType.KEYWORD),
        ("is_public", qm.PayloadSchemaType.BOOL),
        ("is_active", qm.PayloadSchemaType.BOOL),
    ):
        try:
            c.create_payload_index(settings.collection, field, schema)
        except Exception:
            pass


def upsert(points: list[qm.PointStruct]) -> None:
    client().upsert(settings.collection, points=points, wait=True)


def delete_doc(doc_id: str) -> None:
    c = client()
    if not c.collection_exists(settings.collection):
        return
    c.delete(settings.collection, points_selector=qm.FilterSelector(
        filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])), wait=True)


def set_access(doc_id: str, allowed_roles, required_permissions, is_public, screens) -> None:
    c = client()
    if not c.collection_exists(settings.collection):
        return
    c.set_payload(
        settings.collection,
        payload={"allowed_roles": allowed_roles, "required_permissions": required_permissions,
                 "is_public": bool(is_public), "screens": screens},
        points=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])),
        wait=True,
    )


def scroll_doc(doc_id: str, limit: int = 300) -> list[dict]:
    c = client()
    if not c.collection_exists(settings.collection):
        return []
    pts, _ = c.scroll(
        settings.collection,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]),
        limit=limit, with_payload=True, with_vectors=False)
    out = [p.payload for p in pts]
    out.sort(key=lambda p: p.get("chunk_index", 0))
    return out


def build_security_filter(user: User) -> qm.Filter:
    """Điều kiện truy cập chunk (union nhiều role):
    is_active AND ( is_public OR ( allowed_roles ∩ user.roles ≠ ∅
                                   AND ( required_permissions rỗng OR required_permissions ∩ user.permissions ≠ ∅ ) ) )
    """
    should = [qm.FieldCondition(key="is_public", match=qm.MatchValue(value=True))]
    if user.roles:
        perm_should: list = [qm.IsEmptyCondition(is_empty=qm.PayloadField(key="required_permissions"))]
        if user.permissions:
            perm_should.append(qm.FieldCondition(key="required_permissions",
                                                 match=qm.MatchAny(any=user.permissions)))
        should.append(qm.Filter(must=[
            qm.FieldCondition(key="allowed_roles", match=qm.MatchAny(any=user.roles)),
            qm.Filter(should=perm_should),
        ]))
    return qm.Filter(must=[
        qm.FieldCondition(key="is_active", match=qm.MatchValue(value=True)),
        qm.Filter(should=should),
    ])


def secure_search(user: User, vector: list[float], limit: int) -> list:
    c = client()
    if not c.collection_exists(settings.collection):
        return []
    res = c.query_points(
        settings.collection, query=vector, query_filter=build_security_filter(user),
        limit=limit, with_payload=True, with_vectors=True)
    return res.points
