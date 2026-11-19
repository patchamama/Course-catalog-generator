# Course Catalog Backend — Docker image
# Includes all text extraction libraries and a pre-downloaded multilingual
# embedding model (paraphrase-multilingual-MiniLM-L12-v2, ~400 MB, ONNX).
#
# Build:  docker build -t course-catalog .
# Run:    docker run -p 5000:5000 -v /path/to/courses:/courses course-catalog

FROM python:3.11-slim

WORKDIR /app

# System libraries needed by some text extraction packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the multilingual embedding model into the image so containers
# start without needing internet access at runtime.
ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache
RUN python -c "\
from fastembed import TextEmbedding; \
print('Downloading embedding model...'); \
TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'); \
print('Model cached.')"

# Copy application source
COPY backend/catalog_server.py backend/search_indexer.py ./

# Use the Docker-specific configuration (reads settings from environment variables)
COPY backend/env_docker.py ./env.py

EXPOSE 5000

# Unbuffered output so logs appear immediately in `docker logs`
ENV PYTHONUNBUFFERED=1

CMD ["python", "catalog_server.py"]
