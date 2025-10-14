# sportHub RFID Reader Client (Sirit INfinity 510)

Minimal TCP client for the Sirit INfinity 510 reader focused on the core flow:

- Establishes a connection to the CONTROL socket (port 50007) to configure the reader.
- Connects to the EVENT socket (port 50008) to receive configured tag events.
- Binds events and then applies commands from the init file (commonly: register arrive/depart and reporting fields).
- Prints concise debug lines when tag events occur.

## Quick start

```bash
# By default it will try to read 'init_commands' file
python3 server.py --ip 192.168.1.130

# Provide a custom init commands file
python3 server.py --ip 192.168.1.130 --init_commands_file init_commands.txt

# With interactive CONTROL and raw socket debug
python3 server.py --ip 192.168.1.130 --interactive --raw
```

Expected logs on success (example):

```
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
```

Press Ctrl-C to stop. On exit, the reader is set to standby automatically.

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