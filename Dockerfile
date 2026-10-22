# syntax=docker/dockerfile:1
# Single image: Flask API + built React frontend, served by gunicorn.
#   docker build -t nova .
#   docker run --env-file backend/.env -p 8000:8000 -v nova-data:/data nova

# ---- frontend build
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production DATABASE_URL=sqlite:////data/chat.db LOG_FORMAT=json
# tesseract enables OCR for scanned PDFs and images; remove these two packages for a smaller image without OCR
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt pymysql "psycopg[binary]" redis pytesseract pypdfium2
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --system --uid 10001 --home /app nova && mkdir -p /data && chown -R nova /data \
    && rm -rf tests .env .secret_key *.db
USER nova
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=4).status == 200 else 1)"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
