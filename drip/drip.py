"""Releases the sounds of the day, on the NAS (Simon, 29 Oct 2026).

The app lists every sound of catalogue.json at once, but some come out one a
day ("drip": the remixes and our own ringtones). scripts/sounds/publish.sh
puts every sound online with catalogue-full.json, the whole list with each
release day; this service turns it into catalogue.json, the list the app
reads, without the sounds whose day has not come.

It runs every hour at five past, Paris time: a sound comes out at 00:05, or
an hour later if the NAS or the network was down. Nothing is sent when the
list has not changed (R2's ETag is the file's MD5).

It also keeps the offer's proof up to date (Simon, 3 Nov 2026: "si mon Mac
est éteint c'est relou"): the ringtones made, read every hour from the stats
service next door, and the worldwide App Store rating, read once a day. A
figure that cannot be read keeps the one publish.sh put in catalogue-full.json.

    python3 drip.py                         # the service
    python3 drip.py visible full.json [day] # the visible list, on stdout
    python3 drip.py proof                   # the proof as it would be sent

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
import urllib.request
import zoneinfo

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
APP_ID = "6814750259"
# Every storefront of the app: the rating is the average of all of them,
# weighted by their number of ratings (the same list as scripts/site/build.py).
STOREFRONTS = """ad ae ag ai al am ao ar at au az ba bb bd be bf bg bh bj bm bn bo br bs bt bw by bz ca cd cg
ch ci cl cm cn co cr cv cy cz de dk dm do dz ec ee eg es fi fj fm fr ga gb gd ge gh gm gr gt gw gy hk hn
hr hu id ie il in iq is it jm jo jp ke kg kh kn kr kw ky kz la lb lc lk lr lt lu lv ly ma md me mg mk ml
mm mn mo mr ms mt mu mv mw mx my mz na ne ng ni nl no np nr nz om pa pe pg ph pk pl pt pw py qa ro rs ru
rw sa sb sc se sg si sk sl sn sr st sv sz tc td th tj tm tn to tr tt tw tz ua ug us uy uz vc ve vg vn vu
ws xk ye za zm zw""".split()


def visible(full: dict, today: str) -> dict:
    """The catalogue as the app gets it on `today`: the held-back sounds whose
    day has not come are left out, and the "drip" marks taken off."""
    hidden = {s["id"] for s in full["sounds"] if s.get("drip") and (s.get("released") or "9999") > today}
    sounds = [{k: v for k, v in s.items() if k != "drip"} for s in full["sounds"] if s["id"] not in hidden]
    return {**full, "sounds": sounds, "popular": [i for i in full.get("popular", []) if i not in hidden]}


def floored(count: int) -> int:
    """The ringtones made as the offer shows them ("plus de 8 200"), the same
    steps as the app: the list then changes once per hundred, not every hour."""
    step = 100 if count >= 1000 else 10 if count >= 100 else 1
    return count // step * step


def rating_of(rows: list) -> float | None:
    """The worldwide rating to a tenth, as the site and the App Store round it:
    (average, count) per storefront."""
    count = sum(n for _, n in rows)
    return round(sum(a * n for a, n in rows) / count, 1) if count else None


def get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "phoneringtones-drip", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# Each storefront's last (average, count), kept across the hours: a storefront
# Apple does not answer for keeps its value, and the rating waits until every
# one has been read once.
storefronts: dict = {}
rated_at = 0.0


def live_proof() -> dict:
    """The figures read now; a missing one is left to catalogue-full.json."""
    global rated_at
    proof = {}
    token = os.environ.get("CONSOLE_TOKEN")
    if token:
        try:
            made = get(os.environ.get("STATS_URL", "http://stats:8000") + "/v1/console.json",
                       {"Authorization": f"Bearer {token}"})["created_total"]
            if made > 0:
                proof["ringtones"] = floored(made)
        except Exception as error:
            print(f"sonneries créées illisibles : {type(error).__name__}", flush=True)
    if time.time() - rated_at > 86400:
        for country in STOREFRONTS:
            try:
                r = get(f"https://itunes.apple.com/lookup?id={APP_ID}&country={country}")["results"]
                storefronts[country] = (r[0].get("averageUserRating", 0), r[0].get("userRatingCount", 0)) if r else (0, 0)
            except Exception:
                pass
            time.sleep(0.5)  # gently: Apple limits this address to a few calls a minute
        if len(storefronts) == len(STOREFRONTS):
            rated_at = time.time()
    rating = rating_of(list(storefronts.values())) if len(storefronts) == len(STOREFRONTS) else None
    if rating:
        proof["rating"] = rating
    return proof


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
    catalogue = visible(json.loads(got.stdout), today)
    proof = catalogue["proof"] = {**catalogue.get("proof", {}), **live_proof()}
    label = f"{today} (note {proof.get('rating', '?')}, {proof.get('ringtones', '?')} sonneries)"
    data = dumps(catalogue)
    head = r2("-I", f"{bucket}/catalogue.json").stdout.decode()
    etag = next((line.split(":", 1)[1].strip().strip('"') for line in head.splitlines()
                 if line.lower().startswith("etag:")), "")
    if etag == hashlib.md5(data).hexdigest():
        return f"{label} : inchangé"
    if os.environ.get("DRY_RUN"):
        return f"{label} : à envoyer ({len(data)} octets), DRY_RUN"
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        f.write(data)
        f.flush()
        code = r2("-X", "PUT", "-T", f.name, "-H", "Content-Type: application/json; charset=utf-8",
                  "-H", "Cache-Control: public, max-age=300", "-o", "/dev/null", "-w", "%{http_code}",
                  f"{bucket}/catalogue.json").stdout.decode()
    return f"{label} : catalogue.json envoyé, HTTP {code}"


def main() -> None:
    if sys.argv[1:2] == ["visible"]:
        full = json.load(open(sys.argv[2]))
        today = sys.argv[3] if len(sys.argv) > 3 else datetime.datetime.now(PARIS).date().isoformat()
        sys.stdout.buffer.write(dumps(visible(full, today)))
        return
    if sys.argv[1:2] == ["proof"]:
        print(json.dumps(live_proof()))
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
