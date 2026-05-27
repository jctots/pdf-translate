FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY webhook.py backfill.py ./
EXPOSE 8081
CMD ["uvicorn", "webhook:app", "--host", "0.0.0.0", "--port", "8081"]
