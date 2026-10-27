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
WEEK = 7 * DAY
# How many weeks of arrivals the console's cohort table shows.
COHORT_WEEKS = 12
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


def weekly_cohorts(first: dict[str, int], opens: dict[str, list[int]], now: int,
                   weeks: int = COHORT_WEEKS) -> list[dict]:
    """Who comes back, week after week, per week of arrival (27 Oct 2026, Simon:
    « rétention par semaine d'arrivée »). Arrival weeks begin on Monday in Paris.
    A device's week k is days 7k to 7k+6 after its own first day, not the k-th
    calendar week: a phone that arrived on Sunday is not judged on the Monday
    after. Each cell is [came back that week, could have]: only the devices
    whose week k is over count, so a young week is never read as a bad one."""
    today = datetime.fromtimestamp(now, PARIS).date()
    monday = today - timedelta(days=today.weekday())
    starts = [int(datetime.combine(monday - timedelta(weeks=k), clock(), PARIS).timestamp())
              for k in range(weeks - 1, -1, -1)]
    members: list[list[str]] = [[] for _ in starts]
    for device, at in first.items():
        if at >= starts[0]:
            members[bisect_right(starts, at) - 1].append(device)
    # The weeks after arrival in which each device opened the app.
    seen = {d: {(at - first[d]) // WEEK for at in opens.get(d, ()) if at >= first[d]}
            for group in members for d in group}
    out = []
    for start, group in zip(starts, members, strict=True):
        cells = []
        for k in range(1, weeks):
            could = [d for d in group if first[d] + (k + 1) * WEEK <= now]
            if not could:
                break
            cells.append([sum(1 for d in could if k in seen[d]), len(could)])
        out.append({"week": start, "size": len(group), "weeks": cells})
    return out


# Word of mouth (1.4): the buttons arrived with 1.4, out on 27 Oct 2026. The
# events do not carry the app's version, a device does (the last it sent), so
# the base is the devices on 1.4 or later that made a ringtone since that day.
SHARING_SINCE = int(datetime(2026, 10, 27, tzinfo=PARIS).timestamp())
SHARING_VERSION = (1, 4)
# Four buttons, not four steps: each is a send that went through, and what was
# tried. An invite is tried when its sheet opens, and sent when Messages says
# so; the three others are sheets that close with `completed`.
SHARING = {
    "invite": ("invite_ended", "invite_started", lambda p: p.get("result") == "sent"),
    "ringtone": ("ringtone_sent", "ringtone_sent", lambda p: p.get("completed") == "yes"),
    "story": ("story_shared", "story_shared", lambda p: p.get("completed") == "yes"),
    "sound": ("sound_sent", "sound_sent", lambda p: p.get("completed") == "yes"),
}
INVITE_FROM = ("ad_gate", "home", "settings")


def version(app: str | None) -> tuple[int, ...]:
    """« 1.4 (25) » as (1, 4): the build number aside. Nothing readable is (0,)."""
    head = (app or "").split(" ")[0]
    parts = []
    for piece in head.split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts) or (0,)


def word_of_mouth(apps: dict[str, str | None], created: list[tuple], shared: list[tuple]) -> dict:
    """What each sharing button did since 1.4: the devices whose send went
    through, the sends and the tries, and the invites by the screen they came
    from. `apps` maps each device to its version, `created` and `shared` are
    (device, name, at, props) rows already read since `SHARING_SINCE`."""
    base = {d for d, _, at, _ in created
            if at >= SHARING_SINCE and d in apps and version(apps[d]) >= SHARING_VERSION}
    rows = []
    for device, name, at, props in shared:
        if device not in apps or at < SHARING_SINCE:
            continue
        try:
            parsed = json.loads(props or "{}")
        except ValueError:
            parsed = {}
        rows.append((device, name, parsed))
    channels = {}
    for key, (done_name, tried_name, went) in SHARING.items():
        done = [(d, p) for d, n, p in rows if n == done_name and went(p)]
        channels[key] = {
            "devices": len({d for d, _ in done}),
            "sent": len(done),
            "tried": sum(1 for _, n, _ in rows if n == tried_name),
        }
    def invites(where: str, name: str, went=lambda p: True) -> int:
        return sum(1 for _, n, p in rows if n == name and p.get("from") == where and went(p))

    went_invite = SHARING["invite"][2]
    invites_from = {where: [invites(where, "invite_ended", went_invite), invites(where, "invite_started")]
                    for where in INVITE_FROM}
    return {"since": SHARING_SINCE, "version": ".".join(map(str, SHARING_VERSION)), "base": len(base),
            "channels": channels, "invites_from": invites_from}


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
    cohorts = weekly_cohorts(first, opens_by_device, now)
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

    names = sorted({n for done, tried, _ in SHARING.values() for n in (done, tried)})
    sharing = word_of_mouth(
        dict(store.query(f"select id, app from devices d where 1=1 {test}")),
        store.query(
            f"select e.device, e.name, e.at, e.props from events e join devices d on d.id = e.device "
            f"where e.name = 'ringtone_created' and e.at >= ? {test}", (SHARING_SINCE,)),
        store.query(
            f"select e.device, e.name, e.at, e.props from events e join devices d on d.id = e.device "
            f"where e.name in ({','.join('?' * len(names))}) and e.at >= ? {test}", (*names, SHARING_SINCE)),
    )

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
        "cohorts": cohorts,
        "sharing": sharing,
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
