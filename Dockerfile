FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m harbor && mkdir -p /data && chown -R harbor:harbor /app /data
USER harbor
CMD ["uvicorn", "harbor.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
