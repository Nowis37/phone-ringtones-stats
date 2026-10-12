"""The numbers the console shows, computed from the two tables.

Test devices (debug and TestFlight builds) are left out unless asked for: the
phones that skew the numbers most are the ones used to build the app.
"""

import json
import time
from collections import Counter

from .store import Store

DAY = 86400


def compute(store: Store, include_tests: bool = False, now: int | None = None, days: int = 30) -> dict:
    """Everything the console shows. `days` is how far back the day-by-day series go."""
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

    def active(since: int) -> int:
        return len({d for d, at in opened if at >= since})

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

    def bucket(rows: list[tuple], start: int, end: int) -> int:
        return sum(1 for row in rows if start <= row[1] < end)

    daily = []
    for back in range(max(1, days) - 1, -1, -1):
        start = (now // DAY - back) * DAY
        end = start + DAY
        daily.append({
            "day": start,
            "new": sum(1 for f in first.values() if start <= f < end),
            "active": len({d for d, at in opened if start <= at < end}),
            "created": bucket(created, start, end),
            "ads": bucket(ads, start, end),
            "purchases": bucket(purchases, start, end),
        })

    # Where a ringtone starts from: the closest thing the app has to "by creator",
    # since nothing about a person or a file is ever sent.
    by_source = Counter(json.loads(props).get("source", "?") for d, _, props in created if d in ids)
    by_product = Counter(json.loads(props).get("product", "?") for d, _, props in purchases if d in ids)

    since30 = now - 30 * DAY
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
        "new_7": sum(1 for f in first.values() if f >= now - 7 * DAY),
        "created_total": sum(creators),
        "created_7": sum(1 for d, at, _ in created if at >= now - 7 * DAY and d in ids),
        "creators": len(creators),
        "per_creator": round(sum(creators) / len(creators), 2) if creators else 0,
        "buckets": [(k, buckets.get(k, 0)) for k in ("0", "1", "2", "3–5", "6+")],
        "retention": retention,
        "came_back": came_back,
        "daily": daily,
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
        "by_product": sorted(by_product.items(), key=lambda kv: -kv[1]),
        "dropped": store.query("select name, count from dropped order by count desc limit 20"),
        "tests_hidden": 0 if include_tests else store.query("select count(*) from devices where is_test = 1")[0][0],
    }
