# RaceTag Reader Service 

Minimal TCP client for the Sirit INfinity 510 reader focused on the core flow:

- Establishes a connection to the CONTROL socket (port 50007) to configure the reader.
- Connects to the EVENT socket (port 50008) to receive configured tag events.
- Binds events and then applies commands from the init file (commonly: register arrive/depart and reporting fields).
- Prints concise debug lines when tag events occur.
- Sends events to a backend over HTTP (with Bearer token support) or uses a mock backend for testing.

## Quick start

Open a terminal under src/ and run:

```bash
# By default it will try to read 'init_commands' file
python3 racetag_reader_service.py --ip 192.168.1.130

# Provide a custom init commands file
python3 racetag_reader_service.py --ip 192.168.1.130 --init_commands_file init_commands.txt

# With interactive CONTROL and raw socket debug
python3 racetag_reader_service.py --ip 192.168.1.130 --interactive --raw

# Send events to a backend over HTTP (backend Bearer token will be used if provided)
python3 racetag_reader_service.py --ip 192.168.1.130 \
	--backend-url http://localhost:8000 --backend-token mytoken

# Use a mock backend client (no network, logs events locally)
python3 racetag_reader_service.py --ip 192.168.1.130 \
	--backend-transport mock
```

Expected logs on success (example):

```
[YYYY-mm-dd HH:MM:SS.mmm] Connecting to CONTROL at <ip>:50007...
[YYYY-mm-dd HH:MM:SS.mmm] CONTROL connected.
[YYYY-mm-dd HH:MM:SS.mmm] Connecting to EVENT at <ip>:50008...
[YYYY-mm-dd HH:MM:SS.mmm] EVENT connected.
[YYYY-mm-dd HH:MM:SS.mmm] [SESSION] obtained id from EVENT: <N>
[YYYY-mm-dd HH:MM:SS.mmm] [CONTROL] >> reader.events.bind(id = <N>)
[YYYY-mm-dd HH:MM:SS.mmm] [CONTROL] [CTRL] ok
[YYYY-mm-dd HH:MM:SS.mmm] [SESSION] bound event channel id <N>
[YYYY-mm-dd HH:MM:SS.mmm] [CONTROL] >> <other init command>
[YYYY-mm-dd HH:MM:SS.mmm] [CONTROL] [CTRL] ok|error
...
[YYYY-mm-dd HH:MM:SS.mmm] [SESSION] configuration applied (<K> commands from init_commands init file)
[YYYY-mm-dd HH:MM:SS.mmm] [EVENT] [EVENT] event.connection id = <N>
[YYYY-mm-dd HH:MM:SS.mmm] [EVENT] [ARRIVE] event.tag.arrive tag_id=0x..., antenna=..., rssi=...
[YYYY-mm-dd HH:MM:SS.mmm] [EVENT] [TAG][NEW] TAG=...
[YYYY-mm-dd HH:MM:SS.mmm] [EVENT] [DEPART] event.tag.depart tag_id=0x..., antenna=..., repeat=...
[YYYY-mm-dd HH:MM:SS.mmm] [EVENT] [TAG] TAG=...
```

Press Ctrl-C to stop. On exit, the reader is set to standby automatically.

### Docker and Docker Compose

You can run the service in a container. The application reads configuration from environment variables, which you can provide via a `.env` file.

1) Prepare environment

```bash
cp .env.example .env
# Edit .env and set at least READER_IP. Set BACKEND_URL if using HTTP transport.
```

2) Build and run with Compose (recommended)

```bash
docker compose build
docker compose up
```

Notes:
- The container initiates outbound TCP connections to the reader at `READER_IP:CONTROL_PORT` and `READER_IP:EVENT_PORT`.
- If your reader/backend are on the same LAN and you need minimal networking translation, consider using host networking on Linux by uncommenting `network_mode: host` in `docker-compose.yml`.
- To mount a custom init commands file, uncomment the `volumes` example in `docker-compose.yml`.

3) Run with `docker run` (optional)

```bash
docker build -t racetag-reader-service:latest .
docker run --rm --env-file .env racetag-reader-service:latest
# On Linux, host networking (optional):
# docker run --rm --env-file .env --network host racetag-reader-service:latest
```

Overriding CLI flags:
- Environment variables set defaults; CLI flags still take precedence. If you really need CLI flags, you can add a `command:` override in `docker-compose.yml`, for example:

```yaml
services:
	racetag-reader:
		command: ["python", "src/racetag_reader_service.py", "--raw", "--interactive"]
```

### Backend integration

Flags related to backend delivery:
- `--backend-url`: Backend base URL to send events (e.g., http://localhost:8000). Enables HTTP transport by default.
- `--backend-token`: Optional Bearer token for backend auth.
- `--backend-transport`: Transport to use when sending events. Options:
	- `http` (default): requires `--backend-url`.
	- `mock`: no network; prints and stores events locally for testing.

## Configuration file

The program sends `reader.events.bind(id = N)` after obtaining the session id and then sends the rest of the init commands from the `init_commands` file (or the file provided via `--init_commands_file`).

- Default file name is `init_commands` (no extension). Use `--init_commands_file` to override.
- Plain text format, one command per line. Lines starting with `#` and blank lines are ignored.

Rules:
- File is read only after the first `event.connection id = N` is received and `reader.events.bind(id = N)` is sent.
- Empty lines and lines starting with `#` are ignored.
- The file can include any commands, including `reader.events.bind(...)` if desired.

## Notes

- The client expects both CONTROL responses and EVENT messages delimited by CRLFCRLF (`\r\n\r\n`).
- Arrive/depart events are highlighted; other reader messages (e.g., `event.connection id`) may also be printed for context.
- See `docs/` for protocol PDFs and device references.