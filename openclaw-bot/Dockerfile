FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install APScheduler==3.11.0 beautifulsoup4==4.13.4 httpx==0.28.1

COPY . .

RUN mkdir -p /app/data/memory /app/data/skills /app/data/files

EXPOSE 10000

CMD ["python", "main.py"]
