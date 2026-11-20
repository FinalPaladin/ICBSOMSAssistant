# OMS Assistant – Chatbot RAG nội bộ (bản chạy thử trên máy local)

Chatbot hỏi đáp nghiệp vụ OMS, **chạy 100% local** (Ollama + Qdrant, không gọi API ngoài), có phân quyền theo
JWT HS256 (`role` + `permission`), nhận biết màn hình OMS, từ chối khi không có dữ liệu, ghi log chat.
Không hiển thị trích dẫn nguồn (nguồn chỉ lưu nội bộ để Admin tra soát).

## Yêu cầu
- Docker Desktop (đang chạy), RAM ≥ 16GB cấp cho Docker (khuyến nghị 20GB+ – xem `dev-local/wslconfig.example`)
- Internet **một lần** để tải model & image

## Chạy lần đầu
```powershell
cd "c:\Users\tv20330067\Desktop\AI Chat Bot\oms-assistant"
Copy-Item .env.example .env                 # chỉnh .env nếu cần
.\scripts\pull-models.ps1                   # tải bge-m3 + qwen2.5:7b-instruct (~6GB)
docker compose up -d --build
```
Mở **http://localhost:8080** (trang test: chat + upload tài liệu + xem log).

## Cách test
1. Chọn persona **"Admin quản trị tài liệu"** → tab **Tài liệu** → upload `.docx` / `.drawio` / `.xlsx`
   (điền `allowed_roles`, ví dụ `OMS.Online`; chọn màn hình liên quan nếu có).
2. Chờ trạng thái `indexed` → bấm **Xem chunks** để kiểm tra kết quả parse (đặc biệt flow draw.io).
3. Chuyển persona **"Nhân viên Online"** → tab **Chat** → hỏi. Chọn màn hình để thử câu "màn hình này…".
4. Chuyển persona **"Nhân viên Offline"** → hỏi cùng câu → phải bị từ chối nếu tài liệu chỉ dành cho Online (RBAC).
5. Hỏi câu ngoài phạm vi → phải trả lời "không tìm thấy thông tin…".
6. Tab **Lịch sử chat** xem `top_score`, lý do từ chối, nguồn nội bộ → dùng để chỉnh `MIN_SCORE` trong `.env`
   (sửa xong: `docker compose up -d rag-api`).

## File hình
- **Khuyến nghị:** upload file **`.drawio` gốc** (parse chính xác từng bước/nhánh/swimlane).
- Ảnh `.png/.jpg`: cần model đọc ảnh – `.\scripts\pull-models.ps1 -Vision qwen2.5vl:7b`, đặt `VISION_MODEL=qwen2.5vl:7b`
  trong `.env`. Độ chính xác thấp hơn `.drawio`; chạy CPU sẽ chậm (vài phút/ảnh).
- Ảnh nhúng trong file Word hiện **chưa** được đọc (chỉ lấy chữ & bảng).

## Lệnh hữu ích
```powershell
docker compose logs -f rag-api       # xem log
docker compose down                  # dừng (dữ liệu giữ nguyên trong volume)
docker compose down -v               # dừng + XOÁ toàn bộ dữ liệu index/log (cẩn thận)
```

## Đổi model (so sánh 4B vs 7B)
```powershell
.\scripts\pull-models.ps1 -Llm qwen3:4b-instruct-2507-q4_K_M
# sửa LLM_MODEL trong .env, rồi:
docker compose up -d rag-api
```
(đổi `EMBED_MODEL` thì phải xoá volume Qdrant & upload lại tài liệu)

## Cấu trúc
```
oms-assistant/
├─ docker-compose.yml, .env.example
├─ config/screens.yaml              # Screen Registry
├─ scripts/pull-models.ps1
├─ services/rag-api/app/
│  ├─ main.py        # API: chat (SSE), admin, dev token
│  ├─ auth.py        # JWT HS256, role/permission, rate limit
│  ├─ vectorstore.py # Qdrant + bộ lọc RBAC (secure_search)
│  ├─ rag.py         # Retrieval -> ngưỡng -> LLM -> groundedness
│  ├─ screens.py     # Ngữ cảnh màn hình
│  ├─ ingestion/     # docx / drawio / xlsx / image parser + pipeline
│  └─ static/index.html   # Trang test
└─ dev-local/wslconfig.example
```

## Bản MVP này CHƯA có (theo kế hoạch)
Reranker (bge-reranker) & hybrid sparse search, Postgres/Redis (đang dùng SQLite + background task), widget React
đóng gói cho OMS, golden-set eval/load test, Nginx/TLS. Sẽ bổ sung sau khi test chất lượng xong.

> `ENV=dev` bật `/dev/token` (ai cũng tạo được token) – **chỉ dùng local**. PROD bắt buộc `ENV=prod` + secret thật.
