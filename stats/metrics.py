"""The numbers the console shows, computed from the two tables.

Test devices (debug and TestFlight builds) are left out unless asked for: the
phones that skew the numbers most are the ones used to build the app.
"""

import json
import time
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta
from datetime import time as clock
from zoneinfo import ZoneInfo

from .store import Store

DAY = 86400
# Days begin at midnight in Paris (27 Oct 2026, Simon: « les jours s'arrêtent
# à 1 h du matin » - they were UTC days, and France is an hour or two ahead).
PARIS = ZoneInfo("Europe/Paris")


def paris_midnights(now: int, count: int) -> list[int]:
    """The `count` last midnights in Paris, oldest first, today's last. A day
    across a change of time is 23 or 25 hours long: each midnight is computed,
    never added."""
    today = datetime.fromtimestamp(now, PARIS).date()
    return [int(datetime.combine(today - timedelta(days=k), clock(), PARIS).timestamp())
            for k in range(count - 1, -1, -1)]


def compute(store: Store, include_tests: bool = False, now: int | None = None, days: int = 30,
            hours_back: int = 0) -> dict:
    """Everything the console shows. `days` is how far back the day-by-day series go;
    `hours_back` moves the 48 hours that many windows of 48 hours into the past."""
    now = now or int(time.time())
    test = "" if include_tests else "and d.is_test = 0"
    devices = store.query(f"select id, first_seen, last_seen from devices d where 1=1 {test}")
    ids = {d[0] for d in devices}
    opened = store.query(
        f"select e.device, e.at from events e join devices d on d.id = e.device "
        f"where e.name = 'app_opened' {test}"
    )
    created = store.query(
        f"select e.device, e.at, e.props from events e join devices d on d.id = e.device "
        f"where e.name = 'ringtone_created' {test}"
    )

    # Who comes back: among devices old enough to have had the chance, the share
    # that opened the app again N days or more after their first day. Counting
    # yesterday's arrivals in a 7-day rate would put them all in the "no" column.
    first = {d[0]: d[1] for d in devices}
    opens_by_device: dict[str, list[int]] = {}
    for device, at in opened:
        opens_by_device.setdefault(device, []).append(at)
    retention = {}
    for n in (1, 7, 30):
        eligible = [d for d, f in first.items() if now - f >= n * DAY]
        back = [d for d in eligible if any(at - first[d] >= n * DAY for at in opens_by_device.get(d, []))]
        retention[n] = (len(back), len(eligible))
    came_back = sum(1 for d, ats in opens_by_device.items()
                    if d in first and len({(at - first[d]) // DAY for at in ats} - {0}) > 0)

    per_device = Counter(device for device, _, _ in created)
    buckets = Counter()
    for device in ids:
        n = per_device.get(device, 0)
        buckets["0" if n == 0 else "1" if n == 1 else "2" if n == 2 else "3–5" if n <= 5 else "6+"] += 1
    creators = [n for d, n in per_device.items() if d in ids]

    def active(since: int, until: int | None = None) -> int:
        return len({d for d, at in opened if at >= since and (until is None or at < until)})

    # Ads watched and purchases, read the same way as creations: test devices out
    # unless asked for, so a day of building the app does not look like a launch.
    ads = store.query(
        f"select e.device, e.at from events e join devices d on d.id = e.device "
        f"where e.name = 'ad_watched' {test}"
    )
    purchases = store.query(
        f"select e.device, e.at, e.props from events e join devices d on d.id = e.device "
        f"where e.name = 'purchase' {test}"
    )

    def series(starts: list[int], stop: int) -> list[dict]:
        """One bucket per start, oldest first, the last one ending at `stop`.
        One pass over each list: the console asks for 13 months of days and
        48 hours, and a scan per bucket would be thousands of scans."""
        count = len(starts)

        def slot(at: int) -> int | None:
            if at < starts[0] or at >= stop:
                return None
            return bisect_right(starts, at) - 1

        rows = [{"new": 0, "active": set(), "created": 0, "ads": 0, "purchases": 0} for _ in range(count)]
        for f in first.values():
            if (k := slot(f)) is not None:
                rows[k]["new"] += 1
        for d, at in opened:
            if (k := slot(at)) is not None:
                rows[k]["active"].add(d)
        for key, events in (("created", created), ("ads", ads), ("purchases", purchases)):
            for row in events:
                if (k := slot(row[1])) is not None:
                    rows[k][key] += 1
        return [{**r, "active": len(r["active"]), "at": starts[k]} for k, r in enumerate(rows)]

    midnights = paris_midnights(now, max(1, days))
    tomorrow = int(datetime.combine(datetime.fromtimestamp(now, PARIS).date() + timedelta(days=1),
                                    clock(), PARIS).timestamp())
    daily = [{**r, "day": r.pop("at")} for r in series(midnights, tomorrow)]
    # For the console's « Heure » zoom: the last 48 hours, the current one last
    # (an hour is the same in Paris and in UTC: the offset is whole hours).
    end_hour = (now - max(0, hours_back) * 48 * 3600) // 3600 * 3600
    hourly = series([end_hour - 3600 * k for k in range(47, -1, -1)], end_hour + 3600)

    # Where a ringtone starts from: the closest thing the app has to "by creator",
    # since nothing about a person or a file is ever sent.
    by_source = Counter(json.loads(props).get("source", "?") for d, _, props in created if d in ids)
    by_product = Counter(json.loads(props).get("product", "?") for d, _, props in purchases if d in ids)

    since30 = now - 30 * DAY
    # What went into the ringtones of the last 30 days: every detail of
    # ringtone_created, value by value, as a share of those ringtones. Generic on
    # purpose: a detail the app starts sending (fades, volume…) shows up here as
    # soon as the vocabulary admits it, with no change to this file.
    recent = [json.loads(props) for d, at, props in created if at >= since30 and d in ids]
    pairs = Counter((key, str(value)) for props in recent for key, value in props.items())
    effects = {
        "creations": len(recent),
        "rows": [{"detail": key, "value": value, "count": count,
                  "share": round(count / len(recent), 4)}
                 for (key, value), count in sorted(pairs.items(), key=lambda kv: (kv[0][0], -kv[1]))],
    }
    # Each sound: listens ("streams", 3 s or more), the devices that listened,
    # stars, and the ringtones made from it. Hidden in the app for now (Simon,
    # 14 Oct 2026); this is where we see what to keep and what to post.
    sound_events = store.query(
        f"select e.device, e.name, e.at, e.props from events e join devices d on d.id = e.device "
        f"where e.name in ('sound_played', 'sound_favorited', 'ringtone_created') {test}")
    per_sound: dict[str, dict] = {}
    since7 = now - 7 * DAY
    for device, name, at, props in sound_events:
        p = json.loads(props)
        sound = p.get("sound")
        if not sound:
            continue
        row = per_sound.setdefault(sound, {"plays": 0, "plays_30": 0, "listeners": set(), "stars": 0, "created": 0,
                                           "created_7": 0, "plays_7": 0})
        if name == "sound_played":
            row["plays"] += 1
            row["plays_30"] += at >= since30
            row["plays_7"] += at >= since7
            row["listeners"].add(device)
        elif name == "sound_favorited":
            row["stars"] += 1 if p.get("on") == "yes" else -1
        else:
            row["created"] += 1
            row["created_7"] += at >= since7
    sounds = sorted(([s, r["plays"], r["plays_30"], len(r["listeners"]), max(0, r["stars"]), r["created"]]
                     for s, r in per_sound.items()), key=lambda r: (-r[1], -r[5], r[0]))
    # The week's most popular sounds (1.4): the most made into a ringtone in the
    # last 7 days, then the most listened to. scripts/sounds/popular.sh reads it
    # and publishes it with the catalogue: the app never asks this server.
    popular_7 = sorted(([s, r["created_7"], r["plays_7"]] for s, r in per_sound.items() if r["created_7"] or r["plays_7"]),
                       key=lambda r: (-r[1], -r[2], r[0]))[:20]
    features = store.query(
        f"select e.name, count(*), count(distinct e.device) from events e join devices d on d.id = e.device "
        f"where e.at >= ? {test} group by e.name order by e.name", (since30,))
    details = store.query(
        f"select e.name, e.props, count(*) from events e join devices d on d.id = e.device "
        f"where e.at >= ? and e.props != '{{}}' {test} group by e.name, e.props order by e.name, 3 desc",
        (since30,))

    def top(column: str) -> list[tuple]:
        return store.query(
            f"select coalesce({column}, '?'), count(*) from devices d where 1=1 {test} "
            f"group by 1 order by 2 desc limit 12")

    return {
        "now": now,
        "devices": len(ids),
        "active_1": active(now - DAY),
        "active_7": active(now - 7 * DAY),
        "active_30": active(now - 30 * DAY),
        # The same windows one step earlier, for the console's « + % »: the 24 h
        # before the last 24 h, and so on.
        "active_1_prev": active(now - 2 * DAY, now - DAY),
        # « En direct » for the console's turning card: phones that sent
        # something in the last 5 and 30 minutes. The app sends at opening and
        # when it goes to the background, so a long sitting shows at its start.
        "live_5": sum(1 for d in devices if d[2] >= now - 300),
        "live_30": sum(1 for d in devices if d[2] >= now - 1800),
        "active_7_prev": active(now - 14 * DAY, now - 7 * DAY),
        "active_30_prev": active(now - 60 * DAY, now - 30 * DAY),
        "new_7": sum(1 for f in first.values() if f >= now - 7 * DAY),
        "created_total": sum(creators),
        "created_7": sum(1 for d, at, _ in created if at >= now - 7 * DAY and d in ids),
        "creators": len(creators),
        "per_creator": round(sum(creators) / len(creators), 2) if creators else 0,
        "buckets": [(k, buckets.get(k, 0)) for k in ("0", "1", "2", "3–5", "6+")],
        "retention": retention,
        "came_back": came_back,
        "daily": daily,
        "hourly": hourly,
        "hours_back": max(0, hours_back),
        "features": features,
        "details": details,
        "os": top("substr(os, 1, instr(os || '.', '.') - 1)"),
        "country": top("country"),
        "lang": top("lang"),
        "app": top("app"),
        "model": top("model"),
        "ads_total": len(ads),
        "ads_7": sum(1 for _, at in ads if at >= now - 7 * DAY),
        "ads_devices_30": len({d for d, at in ads if at >= since30}),
        "purchases_total": len(purchases),
        "purchases_30": sum(1 for _, at, _ in purchases if at >= since30),
        "purchasers": len({d for d, _, _ in purchases}),
        "by_source": sorted(by_source.items(), key=lambda kv: -kv[1]),
        "effects": effects,
        "sounds": sounds,
        "popular_7": popular_7,
        "by_product": sorted(by_product.items(), key=lambda kv: -kv[1]),
        "dropped": store.query("select name, count from dropped order by count desc limit 20"),
        "tests_hidden": 0 if include_tests else store.query("select count(*) from devices where is_test = 1")[0][0],
    }
