FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py admin.html ./
ENV NOVA_DB=/data/nova.db
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
