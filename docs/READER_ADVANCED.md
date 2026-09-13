# Reader: advanced setup, CLI and internals

_Audience: Jan (maintainer). The race marshal never needs this page; their guide is [BEDIENUNGSANLEITUNG-WINDOWS.md](BEDIENUNGSANLEITUNG-WINDOWS.md)._

This page collects everything about the Sirit INfinity 510 that goes beyond "plug it into the FritzBox and start Racetag": direct access to the reader (web portal, SSH, CLI), the one-time network setup, alternative cabling, manual IP finding as a fallback, and how the reader-service connects, discovers the reader and reports its status.

The binding interface definitions live in [PLAN-WINDOWS-NONTECHIE-CONTRACT.md](PLAN-WINDOWS-NONTECHIE-CONTRACT.md) (§2 status protocol, §3 reader-service). Where this page and the contract disagree, the contract and the code win. The vendor documentation is in [`apps/reader-service/docs/Sirit INfinity 510/`](../apps/reader-service/docs/Sirit%20INfinity%20510/).

---

## 1. Direct access to the reader

Racetag configures the reader on every connect (session bind, UTC clock, init commands, antenna detection and power). Direct access is only needed for diagnosis or settings Racetag does not manage (region, network mode).

### 1.1 Web portal

Browse to `http://<reader-ip>/` (e.g. `http://192.168.178.22/`). Login: user `admin`, password `readeradmin`. If a modern browser forces HTTPS, type `http://` explicitly or use a private window. The portal also answers on `http://<serial>.local` (e.g. `http://00179e0037d2.local`) where mDNS works.

### 1.2 SSH

The reader's SSH uses legacy algorithms that modern OpenSSH disables by default, so pass them explicitly:

```
ssh -oKexAlgorithms=+diffie-hellman-group14-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    cliuser@<reader-ip>
```

User is `cliuser`, **no password** (just press Enter). To avoid typing the flags every time, add this to `~/.ssh/config` (on Windows: `%USERPROFILE%\.ssh\config`, OpenSSH client is built into Windows 10/11):

```
Host 192.168.178.22 169.254.1.2 *.local
    KexAlgorithms +diffie-hellman-group14-sha1
    HostKeyAlgorithms +ssh-rsa
    PubkeyAcceptedAlgorithms +ssh-rsa
    User cliuser
```

Then `ssh 192.168.178.22` works.

### 1.3 CLI syntax

- **Read** a setting: type the bare name, e.g. `antennas.1.conducted_power`
- **Set** a setting: `name=value`, e.g. `setup.operating_mode=active`
- **Call** a method: include parens, e.g. `tag.db.get()`

### 1.4 Antenna transmit power

`conducted_power` is in **units of 0.1 dBm** (`190` = 19.0 dBm). Datasheet max is **+30 dBm** (`300`).

Racetag applies the power from Settings ("Antennenleistung", persisted as `antenna_power`, default `300`) to every detected port on every connect. A changed value reaches the reader-service in the next heartbeat reply (≤ 2 s) and triggers a reconnect; no app restart. Use the CLI only to experiment:

```
antennas.1.conducted_power            # read current value
antennas.1.conducted_power=250        # set antenna 1 to 25.0 dBm
setup.operating_mode=active           # re-activate so the change takes effect
antennas.1.conducted_power            # confirm it was accepted
```

Step up gradually (`220` → `250` → `300`) and stop at the lowest power that reads the race tags reliably. **More power = more range = more stray-tag bleed-through**: a tag in range (even one in an adjacent room) can register a phantom row in the standings. The reader is in region `etsi` / `en302208_dense`, which caps **ERP** (conducted power + antenna gain); if a high value is rejected, use the highest value the reader accepts. A manual CLI change is overwritten by Racetag at the next connect.

### 1.5 Clock and time zone

The 510 has no RTC and drifts. Racetag pushes the host UTC clock at every bind and again every 30 minutes while connected (`--clock-resync-interval`, default 1800 s), always zone first. To set it manually:

```
info.time_zone=UTC                    # set zone FIRST
info.time=2026-05-25T12:40:00         # then the time, interpreted as UTC
info.time                             # read back
```

### 1.6 Other handy commands

```
setup.operating_mode                          # read mode (expect: active)
setup.operating_mode=active                   # start reading
tag.db.get()                                  # dump tags the reader currently sees
tag.db.clear()                                # clear the reader's tag database
antennas.detected                             # ports with an antenna attached
info.serial_number                            # serial (what discovery verifies)
reader.profile.show_running_config()          # dump the full current config
reader.reboot()                               # reboot the reader (~60 s)
```

> **Persistence caveat.** A hard power loss has been observed to reset the reader's network config to the factory static IP `169.254.1.2`. The exact command to persist the running config to the startup profile is firmware-specific (see the `reader.profile.*` namespace) and has not been confirmed. Discovery probes `169.254.1.2` on every run (§5), but whether a Windows PC on the FritzBox subnet can actually reach it is unverified (§8).

---

## 2. One-time reader network setup (factory IP → DHCP)

The reader ships with the static link-local IP `169.254.1.2`. To use it on a normal network, flip it to DHCP once.

1. Power the reader (it boots in about 30 s) and connect its Ethernet port **directly** to a computer.
2. Give that computer's Ethernet adapter a fixed address in the link-local range: `169.254.1.100`, netmask `255.255.0.0`, no gateway/DNS.
   - **Windows:** `Win + R` → `ncpa.cpl` → right-click the Ethernet adapter → Properties → "Internetprotokoll, Version 4 (TCP/IPv4)" → Properties → "Folgende IP-Adresse verwenden".
   - **macOS:** System Settings → Network → Ethernet → Details → TCP/IP → Configure IPv4: Manually.
3. Switch the reader to DHCP:
   ```
   ssh cliuser@169.254.1.2          # no password
   com.network.1.set(dhcp)
   reader.reboot()
   ```
4. Set the computer's Ethernet adapter back to automatic (DHCP).
5. Plug the reader into the FritzBox **LAN 2**. **Not LAN 1**: LAN 1 is the Gigabit port and does not negotiate a link with the Sirit's old PHY (confirmed 2026-07-26).
6. Recommended: in the FritzBox (Heimnetz → Netzwerk → the `sirit510` device) enable "Diesem Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen". FritzOS 6.x can only pin the address the device currently has. The current kit uses `192.168.178.22` (MAC `00:17:9E:00:37:D2`).

A fixed lease is convenient but no longer required: if the address changes, discovery finds the reader and the backend persists the new IP (§5).

---

## 3. Alternative topologies (no router)

Both variants give the PC and the reader one common subnet, which discovery sweeps automatically. Neither is part of the supported race-day setup; use them only when the FritzBox is unavailable.

### 3.1 Direct cable on Windows (Internet Connection Sharing)

1. Connect the reader to the PC with an Ethernet cable.
2. `Win + R` → `ncpa.cpl`. Right-click the **Wi-Fi** adapter → Properties → **Sharing** tab.
3. Tick "Anderen Benutzern im Netzwerk gestatten, diese Verbindung des Computers als Internetverbindung zu verwenden" and pick the Ethernet adapter as the home network connection.
4. Windows sets the Ethernet adapter to `192.168.137.1` and runs a DHCP server on it; the reader gets a `192.168.137.x` address.

Enabling ICS needs admin rights, which the target operator PC may not grant.

### 3.2 Direct cable on macOS (Internet Sharing)

1. System Settings → General → Sharing → Internet Sharing → ⓘ.
2. Share your connection from: Wi-Fi. To devices using: the Ethernet adapter. Toggle on.
3. macOS runs a DHCP server on the Ethernet adapter (`192.168.2.x` on recent versions, `10.0.2.x` on some older ones; check the adapter's actual address).

### 3.3 Operating on the factory link-local IP

Earlier field tests ran the reader untouched on `169.254.1.2` with the Mac on a manual `169.254.1.100/255.255.0.0` (see [FIELD_TEST_CHECKLIST.md](FIELD_TEST_CHECKLIST.md) and [issues/TODO-portable-network-config.md](issues/TODO-portable-network-config.md)). This avoids reader config persistence but needs a manual host IP, which a non-technical operator cannot set. It remains a fallback for Jan's own machine.

---

## 4. Finding the reader manually (fallback)

Normally unnecessary: discovery (§5) does this. Useful when discovery reports nothing and you want to know why.

| Method | Command |
| --- | --- |
| ARP table, Windows | `arp -a \| findstr "00-17-9e"` |
| ARP table, macOS/Linux | `arp -a \| grep -i "0:17:9e"` |
| FritzBox | Heimnetz → Netzwerk, device name starting with `sirit` or MAC `00:17:9E:…` |
| mDNS | `ping 00179eXXXXXX.local` (serial from the reader sticker) |
| Port check, Windows | `Test-NetConnection 192.168.178.22 -Port 50007` (and `-Port 50008`) |
| Port check, macOS | `nc -vz -w 3 192.168.178.22 50007` |

A found IP can be typed into Settings → "Reader-Adresse (IP)"; the reader-service switches to it within one heartbeat. `PATCH /config {"reader_ip": null}` (or clearing the field) removes the persisted IP, and the service falls back to searching.

---

## 5. Discovery internals

Implementation: `apps/reader-service/src/discovery.py` (stdlib only, no admin rights, no raw sockets). Contract: §3.3 and §3.4.

### 5.1 Algorithm

`discover()` builds a target list, probes every address once in a thread pool, and returns `Candidate(ip, serial, source)` objects sorted by source (`connected`, `arp`, `sweep`, `linklocal`) and then by address. It never raises; failures are logged under `[DISCOVERY]` in `reader.log` and return `[]`.

1. **ARP table.** `arp -a` (Linux without net-tools: `ip neigh`) is parsed for MACs starting with the Sirit OUI `00:17:9e`. The parser handles Windows (`00-17-9e-…`, English and German headers), macOS (`0:17:9e:…`, unpadded) and `ip neigh` formats. Instant when the reader has already talked on the LAN. Source `arp`.
2. **Subnet sweep.** Local IPv4 networks come from `arp -a` "Interface:/Schnittstelle:" lines plus `ipconfig` IPv4/subnet lines (English and German) on Windows, and `ip -4 addr` or `ifconfig` elsewhere. The address of the default route (UDP-connect trick, no packet sent) is added if it is not already covered. Loopback is skipped. Each network is capped at a /24 around the local address even if the mask is wider, and every host address in it is probed. Source `sweep`.
3. **Factory IP.** `169.254.1.2` is always probed (`extra_ips`). Source `linklocal`.
4. **Probe.** TCP connect to CONTROL port 50007 with a 300 ms timeout, send `info.serial_number\r\n`, read up to 1 s for the reply. `ok <hex>` → the upper-case serial; a bare `ok`/`error` → a Sirit without a serial (`serial: null`); anything else or no answer → not a reader. The connection is closed right away. Up to 64 probes run in parallel, so a full /24 takes roughly 1.5 s (more with several adapters: VPN, Hyper-V and VirtualBox adapters each add a /24).
5. `arp`, `ipconfig`, `ifconfig` and `ip` run with a 3 s timeout and, on Windows, `CREATE_NO_WINDOW` so no console flashes.

mDNS (`<serial>.local`) is not used: the service type is unknown.

### 5.2 When discovery runs

| Trigger | Behaviour |
| --- | --- |
| No target IP (no `--ip`, nothing persisted, no `READER_IP`) | State `searching`. Discovery runs immediately, then at most every `--discover-interval` (60 s). Exactly one candidate becomes the target. |
| `--discover-after-failures` (6) consecutive failed connect/configure attempts | Automatic discovery, at most once per `--discover-interval`. With the back-off (§6.2) and 3 s connect timeouts this is roughly a minute after the reader disappeared. |
| `discover` command from the backend (Settings "Reader suchen", assistant) | Runs on the connection thread. While connected, the current IP is skipped (`skip_ips`) and prepended as `Candidate(current_ip, current_serial, "connected")`. Results are only reported; a configured target is never replaced by a manual search. Only when the client has no target at all does a single result get adopted. |
| `--no-discover` | Disables the automatic triggers; commands still work. |

While a discover command runs, the connection thread is busy for about 4–5 s. Probes and resyncs are skipped during that time; events keep flowing on the EVENT socket.

### 5.3 Target selection (automatic triggers)

- The configured/current IP is among the candidates → keep it.
- Exactly one candidate and it differs → switch, `target_source = "discovery"`, set `discovered_ip` in the heartbeat until the backend's reply echoes it as `config.reader_ip`.
- Zero or two or more candidates → keep the current target (or stay `searching`) and report `error: "no reader found"` / `"multiple readers found"`. The UI shows "mehrere gefunden" and lists the candidates with serials in Settings.

The backend persists a valid `discovered_ip` **before** building the reply and logs `reader_ip changed via discovery: old -> new` on `racetag.backend` (visible in `backend.log` in the desktop build). Each discovery is taken over once: heartbeats repeating the same `(pid, discovered_ip)` are ignored, so a `reader_ip` the operator saves in the meantime is not overwritten; `discovered_ip: null` or a new reader-service `pid` ends that discovery. While `discovered_ip` is pending, the reader-service ignores `config.reader_ip` from replies to POSTs that did not yet carry the discovered IP, so an in-flight reply cannot flip it back to the old address.

### 5.4 Docker

The `python:3.11-slim` image has no `arp`, `ip` or `ifconfig`. Discovery there falls back to the default-route /24 sweep plus the factory IP; the ARP step finds nothing.

---

## 6. Reader-service connection lifecycle

Implementation: `apps/reader-service/src/sirit_client.py`. Contract §3.2. `SiritClient.start()` returns immediately; a connection thread owns both sockets.

### 6.1 States

| State | Meaning | UI (Reader pill) |
| --- | --- | --- |
| `searching` | No usable target; discovery running or waiting | amber "wird gesucht…" |
| `connecting` | TCP connect to CONTROL (50007) then EVENT (50008), 3 s timeout each; also while waiting between attempts | amber "verbinde…" / "verbinde neu in N s" |
| `configuring` | Sockets up: wait for `event.connection id` (10 s), bind, UTC clock push, init commands, antenna detection and power | amber "wird eingerichtet…" |
| `active` | Bound and configured, events flow | green "verbunden (IP)" |
| `lost` | Dropped after `configuring`/`active`; waiting to reconnect | amber "Verbindung verloren, verbinde neu…" |
| `stopped` | Final heartbeat during shutdown | red "gestoppt"; amber "wird neu gestartet…" in the desktop build while `supervisor.restart_pending` or `supervisor.running` is true |
| `unknown` | Backend-side only: no heartbeat for 6 s | red "keine Rückmeldung"; amber "startet…" before the first heartbeat while the supervised child runs (up to 30 s after page load) |

### 6.2 Reconnect rules

- **Loss detection:** EOF, RST or a socket error on either channel, a receive thread that ends unexpectedly, a failed send (`ControlSendError`), a failed configuration, or 3 unanswered liveness probes/resyncs in a row. TCP keepalive (idle 15 s, interval 5 s, 8 probes = 40–55 s; Windows `SIO_KEEPALIVE_VALS` plus best-effort `TCP_MAXRT` 60 s, macOS `TCP_KEEPALIVE`, Linux `TCP_KEEPIDLE`) turns a dead peer into a socket error; the probe covers the case where unacknowledged data keeps keepalive from firing.
- **Short outages keep the connection.** A cable blip of up to about 35 s does not close the sockets, so the passes the reader emitted meanwhile are delivered by TCP once the link is back; the status stays `active` and `reader.log` gets `no reply to liveness probe within 5s (1/3)`. A silent link counts as lost 35–50 s after it went quiet.
- **Liveness probe:** `info.time` every 15 s, only when no other CONTROL query is pending; no reply within 5 s = one miss; 3 misses in a row = lost. Any reply resets the count.
- **Clock resync:** `info.time_zone=UTC` + `info.time=<now>` every 30 min; an unanswered resync counts as a miss like a probe and is retried after the probe interval.
- **Back-off:** 1, 2, 2, … s between attempts; `consecutive_failures` counts since the last `active`. A config change or a command interrupts the wait. When an automatic discovery run sees the reader at the current target IP, the next attempt starts without a back-off.
- **Stop:** every requested stop (window closed, stdin EOF, signal) puts the reader into `standby` once before the sockets close.
- **Per connection reset:** session id and bind flag, reader serial, antenna list, `antenna_reads`, tag presence. A tag that was in the field during the outage is therefore counted again on its next arrive. **Kept:** seen-tag history, reader-side cooldown timestamps, the backend client and its spool.
- A connection generation counter tags receive threads and bind work, so a replaced connection can never touch the new socket. A new `event.connection id` inside the same connection re-binds (W-061).
- Sockets get a 5 s I/O timeout after connect so a send on a stalled link cannot hang.

### 6.3 Error strings

The heartbeat's `error` field is English on the wire; the frontend (`translateReaderError` in `script.js`) maps it to German and shows "technischer Fehler (Details im Protokoll)" for anything unrecognised.

`CONTROL connection refused` / `EVENT connection refused`, `CONTROL connect timeout` / `EVENT connect timeout`, `CONTROL connect failed: <os error>`, `no event.connection id within 10s`, `configuration timeout`, `configuration failed: <detail>`, `CONTROL send failed: <os error>`, `CONTROL connection closed by the reader` / `EVENT connection closed by the reader`, `CONTROL socket error: <e>` / `EVENT socket error: <e>`, `no reply to 3 liveness probes in a row (5s each)`, `no reply to 3 clock resyncs in a row (5s each)`, `no reader found`, `multiple readers found`, `no reader ip configured`.

---

## 7. Status heartbeat and command protocol (summary)

Full definition: contract §2. Backend implementation: `apps/backend/racetag-backend/reader_status_hub.py`; reader side: `apps/reader-service/src/status_reporter.py`.

```
reader-service                                   backend
     |  POST /reader/status  {state, ip, ...}       |
     |  every 2 s and immediately on state change   |
     | -------------------------------------------> |  persist discovered_ip (if any)
     |                                              |  store status in memory, maybe SSE reader_status
     |  200 {config: {reader_ip, antenna_power},    |
     |       command: null | {id, type}}            |
     | <------------------------------------------- |
```

- **Heartbeat body** always carries every key: `state`, `ip`, `target_source` (`cli`/`config`/`discovery`), `serial`, `antennas`, `antenna_power`, `antenna_reads` (raw arrives per port since the current connection, before presence gating), `last_event_at`, `connected_since`, `error`, `consecutive_failures`, `next_retry_s`, `candidates`, `discovered_ip`, `discovery`, `reader_service_version`, `pid`. Timeout 1 s, no retry, no spool; errors logged at most once per 60 s.
- **Config via reply.** `PATCH /config` only persists. A different `config.reader_ip` in the reply switches the target (`target_source = "config"`), resets failures and reconnects; a different `antenna_power` reconnects with the new power; `null` values are ignored. Works identically in the desktop build and in Docker. Changing either value mid-race causes a reconnect of a few seconds.
- **Commands** (`discover`, `reconnect`) are queued by the backend with monotonic ids, delivered in **one** reply each, never in reply to a `stopped` heartbeat, and duplicate reconnects are merged.
- **`POST /reader/discover`** queues `discover` and waits up to 15 s for a heartbeat whose `discovery.request_id` matches. Response `{candidates, error}` with `error` `null`, `"timeout"`, `"reader_service_unavailable"` (status `unknown`, returns immediately; exception: before the first heartbeat while the desktop supervisor reports the child running, the command waits for that heartbeat within the 15 s) or a reader-service string such as `"no reader found"`. Concurrent calls share one command; a `discover` that arrives while a discovery run is in progress is answered with that run's result.
- **`POST /reader/restart`** returns `202 {accepted: true, via}`: `supervisor` (desktop: the reader-service process is restarted) or `command` (Docker: a `reconnect` command). If the supervisor's `restart()` raises, the backend falls back to the command.
- **`GET /reader/status`** returns the last status plus `updated_at`, `age_s` and a `supervisor` block (`running`, `pid`, `restart_count`, `last_exit_code`, `restart_pending` = a restart was accepted and the replacement child is not spawned yet; `null` without a controller). After 6 s without a heartbeat the state is `unknown`; the last known fields are kept.
- **SSE `reader_status`** is published when `state`, `ip`, `serial`, `antennas`, `error`, `candidates` or `consecutive_failures` change, at most every 5 s otherwise (so read counters refresh), and once on the transition to `unknown`.

### 7.1 Desktop supervisor

In the desktop build the shell (`apps/desktop/reader_supervisor.py`) runs the reader-service as a child process (`Racetag.exe --reader-service …` when frozen, so Task Manager shows two `Racetag.exe` processes).

- **Argv** is rebuilt from the persisted config on every spawn: `--backend-url`, `--ip` only when an IP is known, `--antenna-power`, `--antenna-ports`, `--min-lap-interval`, `--init_commands_file`, always `--stop-on-stdin-eof`. The child inherits `RACETAG_VERSION` and `RACETAG_LOG_DIR`.
- **Crash restart:** any exit not requested by the shell restarts the child after 1, 2, 5, 10, 30, 30, … s; the sequence resets after 60 s of uptime. `restart()` (the "Reader neu verbinden" button) returns immediately and skips a pending back-off.
- **Stop:** close stdin (the child flushes its spool, sets the reader to standby, sends `stopped`, exits 0) → wait up to 5 s → `terminate()` → `kill()`.
- **Windows:** the child is assigned to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and started with `CREATE_NO_WINDOW`, so killing the shell from Task Manager kills the child too. On POSIX the PID file `~/.racetag/reader-service.pid` and the next launch's stale-process reap cover a hard crash.
- The child's raw stderr goes to `~/.racetag/logs/reader-stderr.log` (rotated to `.1` above 1 MB at spawn), so an import error in a broken build is not lost.

---

## 8. Hardware assumptions not yet verified

Everything above was tested against an in-process fake Sirit on `127.0.0.1` (macOS). These points need the real reader and a real Windows PC; record the results in the Windows resilience rehearsal in [DRESS_REHEARSAL_COMMANDS.md](DRESS_REHEARSAL_COMMANDS.md).

- `info.time` returns exactly one `ok` line, and `info.time_zone=UTC` / `info.time=<iso>` return exactly one `ok`/`error` line each (the probe and resync count replies).
- A short second CONTROL connection from a discovery probe does not disturb an active session.
- Pulled cable / reader reboot mid-session: time until `lost` and a clean reconnect with a new `event.connection id`, re-bind, clock push and antenna config.
- Windows applies `SIO_KEEPALIVE_VALS` on the reader sockets without error.
- Windows `arp -a` / `ipconfig` output in the OEM code page (German and English) parses; no console window flashes.
- Windows reaches `169.254.1.2` from a PC on the FritzBox subnet (needs an on-link `169.254.0.0/16` route on the reader's interface). If not, a reader that fell back to its factory IP after a power loss will not be found automatically; the fallback is §2 or §3.3.
- The 15 s `POST /reader/discover` timeout is enough on PCs with several network adapters.
