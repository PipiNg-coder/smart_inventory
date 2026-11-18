# FastAPI ghi phiếu -> BigQuery (triển khai riêng, ví dụ Google Cloud Run)
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY api.py api_security.py web_api.py ./
COPY utils ./utils
# Cloud Run truyền cổng qua biến PORT
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8080}"]
