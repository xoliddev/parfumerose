# Dockerfile - Telegram Bot (parfumerose)
# Render.com / docker-compose orqali ishga tushadi

FROM python:3.11-slim

WORKDIR /app

# 1. Python kutubxonalarini o'rnatish
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# 2. FFmpeg o'rnatish (audio convert uchun)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 3. Loyiha kodlarini nusxalash
COPY . .

# 4. Data papkasini yaratish
RUN mkdir -p /app/data

# 5. Botni ishga tushirish
CMD ["python", "bot.py"]
