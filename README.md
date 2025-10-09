# sportHub RFID Reader Client (Sirit INfinity 510)

A multi-channel TCP client for the Sirit INfinity 510 RFID reader. Connects to event (50008), data (50009), and control (50007) channels, prints messages, highlights EPC tags, and can send control commands after connecting.

## Quick start

```bash
python3 server.py --ip 192.168.1.130 --channels all --event-delimiter $'\r\n\r\n' --data-delimiter $'\r\n\r\n' --raw
```

Bring a tag near the antenna. If tag reports are coming, you'll see lines like:

```
[YYYY-mm-dd HH:MM:SS.mmm] [DATA] ...
[YYYY-mm-dd HH:MM:SS.mmm] [DATA] [TAG] EPC=E2000017221100...
```

To stop the server, simply press `Ctrl-C`.


## Documentation

See [docs/Useful_commands.md](docs/Useful_commands.md) for useful commands to configure the reader.