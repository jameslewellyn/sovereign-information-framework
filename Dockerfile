FROM python:3.12-slim

# system deps for Playwright + pdf2image
RUN apt-get update && apt-get install -y \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN crawl4ai-setup && playwright install chromium --with-deps

COPY . .
RUN mkdir -p data

CMD ["python", "-m", "src.scheduler"]
