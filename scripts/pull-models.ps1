# Tải model MỘT LẦN (cần Internet). Sau đó hệ thống chạy hoàn toàn offline.
# Dùng: .\scripts\pull-models.ps1
#       .\scripts\pull-models.ps1 -Llm qwen3:4b-instruct-2507-q4_K_M
#       .\scripts\pull-models.ps1 -Vision qwen2.5vl:7b      (nếu muốn đọc file ảnh PNG/JPG)
param(
  [string]$Llm = "qwen2.5:7b-instruct",
  [string]$Embed = "bge-m3",
  [string]$Vision = "qwen2.5vl:3b"
)
$ErrorActionPreference = "Stop"
$models = @($Embed, $Llm)
if ($Vision) { $models += $Vision }

docker volume create oms_ollama_models | Out-Null
docker rm -f oms-ollama-pull 2>$null | Out-Null
docker run -d --name oms-ollama-pull -v oms_ollama_models:/root/.ollama ollama/ollama | Out-Null
Start-Sleep -Seconds 5
try {
  foreach ($m in $models) {
    Write-Host ">>> Pulling $m ..." -ForegroundColor Cyan
    docker exec oms-ollama-pull ollama pull $m
    if ($LASTEXITCODE -ne 0) { throw "Pull $m failed" }
  }
  docker exec oms-ollama-pull ollama list
} finally {
  docker rm -f oms-ollama-pull | Out-Null
}
Write-Host "Xong. Chạy tiếp: docker compose up -d --build" -ForegroundColor Green
