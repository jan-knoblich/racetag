# syntax=docker/dockerfile:1.6

# Build minimal Python runtime image
FROM python:3.11-slim AS runtime

# Ensure no Python bytecode and unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Ensure Docker sends SIGTERM (explicit)
STOPSIGNAL SIGTERM

# Create non-root user
RUN useradd -u 10001 -m appuser
WORKDIR /app

# Install Python deps first for better layering
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src ./src
COPY src/init_commands ./src/init_commands

# Run the service directly; env defaults are handled inside Python
USER appuser
CMD ["python", "src/racetag_reader_service.py"]
