"""Releases the sounds of the day, on the NAS (Simon, 29 Oct 2026).

The app lists every sound of catalogue.json at once, but some come out one a
day ("drip": the remixes and our own ringtones). scripts/sounds/publish.sh
puts every sound online with catalogue-full.json, the whole list with each
release day; this service turns it into catalogue.json, the list the app
reads, without the sounds whose day has not come.

It runs every hour at five past, Paris time: a sound comes out at 00:05, or
an hour later if the NAS or the network was down. Nothing is sent when the
list has not changed (R2's ETag is the file's MD5).

    python3 drip.py                         # the service
    python3 drip.py visible full.json [day] # the visible list, on stdout

The R2 key (R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, scoped by
Cloudflare to the sounds bucket) comes from the environment and goes to curl
on its standard input, never on its command line.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zoneinfo

PARIS = zoneinfo.ZoneInfo("Europe/Paris")


def visible(full: dict, today: str) -> dict:
    """The catalogue as the app gets it on `today`: the held-back sounds whose
    day has not come are left out, and the "drip" marks taken off."""
    hidden = {s["id"] for s in full["sounds"] if s.get("drip") and (s.get("released") or "9999") > today}
    sounds = [{k: v for k, v in s.items() if k != "drip"} for s in full["sounds"] if s["id"] not in hidden]
    return {**full, "sounds": sounds, "popular": [i for i in full.get("popular", []) if i not in hidden]}


def dumps(catalogue: dict) -> bytes:
    # The same bytes as publish.sh writes, so the two never undo each other.
    return json.dumps(catalogue, ensure_ascii=False, indent=1).encode()


def r2(*args: str) -> subprocess.CompletedProcess:
    """A request signed with the bucket's key, fed to curl on stdin."""
    key = f'user = "{os.environ["R2_ACCESS_KEY_ID"]}:{os.environ["R2_SECRET_ACCESS_KEY"]}"\n'
    return subprocess.run(["curl", "-s", "-K", "-", "--aws-sigv4", "aws:amz:auto:s3", *args],
                          input=key.encode(), capture_output=True, timeout=60)


def release() -> str:
    bucket = os.environ["R2_ENDPOINT"].rstrip("/") + "/phoneringtones-sounds"
    # Read from the bucket itself, not the public address: Cloudflare's cache
    # could hand back a list several minutes old.
    got = r2("-f", f"{bucket}/catalogue-full.json")
    if got.returncode != 0:
        return f"catalogue-full.json illisible (curl {got.returncode})"
    today = datetime.datetime.now(PARIS).date().isoformat()
    data = dumps(visible(json.loads(got.stdout), today))
    head = r2("-I", f"{bucket}/catalogue.json").stdout.decode()
    etag = next((line.split(":", 1)[1].strip().strip('"') for line in head.splitlines()
                 if line.lower().startswith("etag:")), "")
    if etag == hashlib.md5(data).hexdigest():
        return f"{today} : inchangé"
    if os.environ.get("DRY_RUN"):
        return f"{today} : à envoyer ({len(data)} octets), DRY_RUN"
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        f.write(data)
        f.flush()
        code = r2("-X", "PUT", "-T", f.name, "-H", "Content-Type: application/json; charset=utf-8",
                  "-H", "Cache-Control: public, max-age=300", "-o", "/dev/null", "-w", "%{http_code}",
                  f"{bucket}/catalogue.json").stdout.decode()
    return f"{today} : catalogue.json envoyé, HTTP {code}"


def main() -> None:
    if sys.argv[1:2] == ["visible"]:
        full = json.load(open(sys.argv[2]))
        today = sys.argv[3] if len(sys.argv) > 3 else datetime.datetime.now(PARIS).date().isoformat()
        sys.stdout.buffer.write(dumps(visible(full, today)))
        return
    while True:
        try:
            print(release(), flush=True)
        except Exception as error:  # the next hour tries again
            print(f"erreur : {error}", flush=True)
        now = datetime.datetime.now(PARIS)
        wake = (now + datetime.timedelta(hours=1)).replace(minute=5, second=0, microsecond=0)
        if now.minute < 5:
            wake = now.replace(minute=5, second=0, microsecond=0)
        time.sleep(max(60, (wake - now).total_seconds()))


if __name__ == "__main__":
    main()
