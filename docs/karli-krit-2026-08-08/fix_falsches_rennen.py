"""Reparatur: versehentlich im LAUF-Rennen gekoppelte Rad-Plaketten (101-175)
ins Arbeits-Rennen verschieben und die Nummern im Lauf wieder freigeben.

Usage: python3 fix_falsches_rennen.py [--von 101] [--bis 175]
       [--ziel "Default race"] [--dry-run]
Endet mit aktivem Lauf-Rennen, damit die Koppel-Session dort weitergehen kann.
"""
import argparse
import json
import subprocess
import sys
import urllib.request


def _req(base, method, path, body=None):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r) if r.status != 204 and method != "DELETE" else None


def discover_base():
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                         capture_output=True, text=True, check=False).stdout
    ports = {l.rsplit(":", 1)[-1].split(" ")[0].replace("(LISTEN)", "").strip()
             for l in out.splitlines() if "127.0.0.1:" in l}
    for port in sorted(ports):
        base = f"http://127.0.0.1:{port}"
        try:
            if "active_race_id" in _req(base, "GET", "/races"):
                return base
        except Exception:
            continue
    return None


ap = argparse.ArgumentParser()
ap.add_argument("--von", type=int, default=101)
ap.add_argument("--bis", type=int, default=175)
ap.add_argument("--quelle", default="Lauf")
ap.add_argument("--ziel", default="Default race")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

base = discover_base()
if not base:
    sys.exit("App läuft nicht?")
races_resp = _req(base, "GET", "/races")
races = races_resp["items"]
original_active = races_resp["active_race_id"]
lauf = next(r for r in races if args.quelle.lower() in r["name"].lower())
ziel = next(r for r in races if args.ziel.lower() in r["name"].lower())
print(f"Quelle: {lauf['name']}  ->  Ziel: {ziel['name']}")

_req(base, "POST", f"/races/{lauf['id']}/activate")
riders = _req(base, "GET", "/riders")["items"]
move = [r for r in riders
        if r["bib"].isdigit() and args.von <= int(r["bib"]) <= args.bis]
move.sort(key=lambda r: int(r["bib"]))
print(f"Im Lauf-Rennen auf {args.von}-{args.bis} gekoppelt: {len(move)} Zeilen")
for r in move:
    named = f" name={r['name']!r}" if r["name"].strip() else ""
    print(f"  Nr. {r['bib']} = …{r['tag_id'][-8:]}{named}  ({r['created_at']})")

if args.dry_run:
    print("\nDRY-RUN — nichts geändert.")
    sys.exit(0)

_req(base, "POST", f"/races/{ziel['id']}/activate")
existing = {r["bib"]: r for r in _req(base, "GET", "/riders")["items"]}
conflicts = [r for r in move
             if r["bib"] in existing and existing[r["bib"]]["tag_id"] != r["tag_id"]]
if conflicts:
    _req(base, "POST", f"/races/{lauf['id']}/activate")
    sys.exit(f"ABBRUCH: Ziel-Rennen hat {len(conflicts)} andere Tags auf diesen "
             f"Nummern (z. B. Nr. {conflicts[0]['bib']}). Erst klären!")
for r in move:
    _req(base, "POST", "/riders", {"tag_id": r["tag_id"], "bib": r["bib"],
                                   "name": r["name"]})
print(f"{len(move)} Kopplungen ins Ziel-Rennen geschrieben.")

_req(base, "POST", f"/races/{lauf['id']}/activate")
for r in move:
    _req(base, "DELETE", f"/riders/{r['tag_id']}")
rest = [r for r in _req(base, "GET", "/riders")["items"]
        if r["bib"].isdigit() and args.von <= int(r["bib"]) <= args.bis]
print(f"Im Lauf-Rennen gelöscht; verbleibend auf {args.von}-{args.bis}: {len(rest)}")
_req(base, "POST", f"/races/{original_active}/activate")
print("Ursprünglich aktives Rennen wiederhergestellt.")
