# Arrizo Mebel API Server Starter
Write-Host "🪵 Arrizo Mebel API Server ishga tushmoqda..." -ForegroundColor Cyan

# Virtual muhitni faollashtirish va serverni ishga tushirish
& .venv\Scripts\python.exe -m uvicorn web.main:app --reload --host 0.0.0.0 --port 8000
