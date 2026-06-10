FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY web/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy database module (imported by backend via sys.path)
COPY database/ ./database/

# Copy backend source
COPY web/backend/ ./web/backend/

WORKDIR /app/web/backend

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
