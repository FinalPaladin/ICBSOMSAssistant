import os


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    env = os.getenv("ENV", "dev")

    jwt_secret = os.getenv("JWT_SECRET", "")
    jwt_audience = os.getenv("JWT_AUDIENCE", "")
    jwt_issuer = os.getenv("JWT_ISSUER", "")
    kb_admin_permission = os.getenv("KB_ADMIN_PERMISSION", "Assistant.KB.Manage")
    kb_admin_roles = _list("KB_ADMIN_ROLES")

    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
    llm_model = os.getenv("LLM_MODEL", "qwen2.5:7b-instruct")
    embed_model = os.getenv("EMBED_MODEL", "bge-m3")
    vision_model = os.getenv("VISION_MODEL", "")
    num_thread = int(os.getenv("NUM_THREAD", "0"))
    num_ctx = int(os.getenv("NUM_CTX", "6144"))
    num_predict = int(os.getenv("NUM_PREDICT", "400"))
    temperature = float(os.getenv("TEMPERATURE", "0.1"))

    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    collection = os.getenv("QDRANT_COLLECTION", "oms_kb")
    top_k_search = int(os.getenv("TOP_K_SEARCH", "12"))
    top_k_final = int(os.getenv("TOP_K_FINAL", "4"))
    max_context_chars = int(os.getenv("MAX_CONTEXT_CHARS", "6000"))
    min_score = float(os.getenv("MIN_SCORE", "0.45"))
    screen_boost = float(os.getenv("SCREEN_BOOST", "0.05"))
    groundedness_enabled = _bool("GROUNDEDNESS_ENABLED", "true")
    ground_min_sim = float(os.getenv("GROUND_MIN_SIM", "0.45"))
    ground_max_ungrounded = float(os.getenv("GROUND_MAX_UNGROUNDED", "0.5"))
    rewrite_enabled = _bool("REWRITE_ENABLED", "true")
    max_concurrent_chat = int(os.getenv("MAX_CONCURRENT_CHAT", "2"))
    rate_limit_per_min = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))

    database_url = os.getenv("DATABASE_URL", "postgresql://assistant:assistant@postgres:5432/assistant")
    max_images_per_doc = int(os.getenv("MAX_IMAGES_PER_DOC", "30"))
    data_dir = os.getenv("DATA_DIR", "/data")
    screens_file = os.getenv("SCREENS_FILE", "/config/screens.yaml")
    cors_origins = _list("CORS_ORIGINS", "*")
    max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "25"))

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "assistant.db")

    @property
    def files_dir(self) -> str:
        return os.path.join(self.data_dir, "files")


settings = Settings()

REFUSAL_TEXT = "Xin lỗi, tôi không tìm thấy thông tin này trong tài liệu nghiệp vụ hiện có."
