# Racetag Backend

Minimal FastAPI backend to ingest race tag events, compute a race classification, and stream live updates.


Endpoints:
- POST /events/tag/batch: ingest a batch (body TagEventBatchDTO) and returns BatchIngestResultDTO with events_processed counter
- GET /classification: current classification (ordered)
- GET /race: race metadata and participants
- GET /stream: Server-Sent Events stream of lap/standings updates

Notes:
- `/classification` returns a snapshot of standings at the moment of the request. For live updates, use `/stream` (SSE).

Send events from reader client running the `racetag-reader-service`. Follow the [quick start instructions](https://github.com/paclema/racetag-reader-service?tab=readme-ov-file#quick-start)

Run locally this backend with:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt


uvicorn --app-dir racetag-backend app:app --reload --host 0.0.0.0 --port 8600

# Optional: configure env variables first
export PORT=8600
export RACETAG_API_KEY=changeme
export RACE_TOTAL_LAPS=5

uvicorn --app-dir racetag-backend app:app --reload --host 0.0.0.0 --port ${PORT}

deactivate
```

Using Docker:

```bash
# Create a simple image running uvicorn
docker build -t racetag-backend .

# Default port
docker run -p 8600:8600 racetag-backend

# Custom port: map host 9000 -> container ${PORT}, pass PORT env
docker run -e PORT=9000 -p 9000:9000 racetag-backend

# Optional: skip openapi model code generation (use existing racetag-backend/models_api.py)
docker build --target runtime-nocodegen -t racetag-backend:nocodegen .
docker run -p 8600:8600 racetag-backend:nocodegen
```

### Using Docker Compose

You can also run it with Docker Compose. Follow the [doker-compose.yml](docker-compose.yml) as reference.

Check the default environment variables in [.env.example](.env.example) and create your own `.env` file to set them up if needed.

```bash
# Build and start
docker compose up --build -d

# Logs
docker compose logs -f

# Stop
docker compose down
```

Notes:
- Storage is in-memory for MVP; replace with a DB for production.

## OpenAPI-first workflow

This project includes an OpenAPI spec at `openapi.yaml`. Recommended workflow:

1) Define/modify contract in `openapi.yaml` (paths, schemas, examples).
2) Generate clients (frontend) or typed models from the spec.
3) Implement or adapt FastAPI endpoints to match the spec (keeping domain logic in `domain/`).

Why not auto-generate the entire FastAPI server on every change?
- Full server generation can overwrite your custom routing and calls to domain classes. Instead, we keep `app.py` as a thin layer and evolve it manually against the spec.
- What we do auto-generate safely: client SDKs and Pydantic models.

### Generate models from OpenAPI (Pydantic)

Use a dedicated local virtualenv to generate Pydantic models from the spec. Schemas in `openapi.yaml` use DTO suffixes (TagEventDTO, ParticipantDTO), and the generated file is `models_api.py`.

To generate again the API models after redefining the OpenAPI specification, the next command with create a Python env to build up a replace the current `models_api.py` file:


```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install datamodel-code-generator

datamodel-codegen --input openapi.yaml --input-file-type openapi --output racetag-backend/models_api.py

deactivate
```

Then import it in your domain source code with: `from models_api import TagEventDTO, ParticipantDTO, EventType`. Avoid overwriting domain code; keep business models in `domain/`.

### Generate frontend client SDK (TypeScript)

Using OpenAPI Generator CLI:

```bash
# Install once (requires Java)
# brew install openapi-generator
# or
# npm install @openapitools/openapi-generator-cli -g

openapi-generator-cli generate \
	-i openapi.yaml \
	-g typescript-fetch \
	-o ./frontend-api \
	--additional-properties=typescriptThreePlus=true
```

This produces a typed client for the frontend. You can regenerate it when the spec changes.

### Generate a temporary FastAPI server (stubs) safely

If you want scaffolding for new endpoints from the spec, generate the server into a separate temporary folder and never over your hand-written app/domain. Two options:

Option A: fastapi-code-generator

```bash
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

pip install fastapi-code-generator
mkdir -p _gen/server

fastapi-codegen --input openapi.yaml --output _gen/server

deactivate
```

Option B: OpenAPI Generator (python-fastapi)

```bash
openapi-generator-cli generate \
	-i openapi.yaml \
	-g python-fastapi \
	-o _gen/server
```

Workflow with generated server:
- Treat `_gen/server` as throwaway scaffolding. Do NOT edit generated files.
- Use it to inspect path/operation signatures (request/response models) and copy only what you need into your real routers (`app.py` or dedicated routers).
- Keep domain logic in `domain/` and import it from your hand-written endpoints.

Overwriting policy for models:
- It’s acceptable to regenerate `models_api.py` on each contract change, as long as domain types/functions live in `domain/`.
- If you start importing generated models broadly, keep them behind a stable module name (`models_api`) and avoid manual edits.

### Adding a new endpoint (process)

1) Edit `openapi.yaml` to add the new path, method, request/response schemas.
2) Regenerate client SDKs and/or models as needed.
3) Implement the FastAPI endpoint in `app.py` (or route module) and call into domain code (`domain/`).

Important: we do NOT auto-generate the FastAPI server on each change to avoid losing hand-written domain wiring. If you do use a server generator, place generated code in a separate folder and wire it to the domain manually without overwriting existing files.
