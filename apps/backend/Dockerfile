#############################################
# Stage: codegen (generate API models from OpenAPI)
#############################################
FROM python:3.13.9-slim AS codegen

# Install tools first so this layer is cached across builds
COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
	&& pip install --no-cache-dir -r /code/requirements.txt datamodel-code-generator==0.35.0

WORKDIR /code
# Copy only the spec to keep cache efficient; changing other files won't invalidate codegen
COPY openapi.yaml /code/openapi.yaml
RUN mkdir -p /openapi_build \
	&& datamodel-codegen --input openapi.yaml --input-file-type openapi --output /openapi_build/models_api.py

#############################################
# Stage: runtime (app image with generated models)
#############################################
FROM python:3.13.9-slim AS runtime-base

# Prevent Python from writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PORT=8600

# Set workdir to project root inside image
WORKDIR /app

# Install runtime dependencies first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the entire app (excluding items in .dockerignore)
COPY . /app

# Expose default uvicorn port (container runtime port). You can override with -p during docker run.
EXPOSE 8600

# Move to the python package folder
WORKDIR /app/racetag-backend

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
	CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/docs')" || exit 1

# Start FastAPI app (module path is racetag-backend/app.py), honor PORT env variable
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]

#############################################
# Stage: runtime-nocodegen (optional, skip codegen)
# Build with: docker build --target runtime-nocodegen -t racetag-backend .
#############################################
FROM runtime-base AS runtime-nocodegen

#############################################
# Stage: runtime (app image with codegen output copied in)
# Keep as the last stage so `docker build .` uses codegen by default
#############################################
FROM runtime-base AS runtime
# Overwrite generated API models to keep image in sync with OpenAPI
COPY --from=codegen /openapi_build/models_api.py /app/racetag-backend/models_api.py