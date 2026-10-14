import time
import uuid

import pytest
from fastapi.testclient import TestClient

from stats.app import create_app
from stats.metrics import DAY, compute
from stats.store import RETENTION_SECONDS, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "stats.sqlite3")


@pytest.fixture
def client(store):
    return TestClient(create_app(store, admin_token="secret"))


def batch(device, *events, **envelope):
    return {"device": device, "app": "1.0 (3)", "os": "17.5", "model": "iPhone12,1", "lang": "fr",
            **envelope, "events": list(events)}


def test_known_events_are_kept_with_only_their_allowed_details(client, store):
    device = str(uuid.uuid4())
    r = client.post("/v1/events", json=batch(device,
        {"name": "ringtone_created", "props": {"best_part": "yes", "enhance": "no", "file": "Mon ex.mp3"}},
        {"name": "paywall_shown", "props": {"reason": "sound"}}), headers={"CF-IPCountry": "FR"})
    assert r.status_code == 204
    rows = store.query("select name, props from events order by id")
    assert rows == [("ringtone_created", '{"best_part": "yes", "enhance": "no"}'),
                    ("paywall_shown", '{"reason": "sound"}')]
    assert store.query("select country, os, model, lang, is_test from devices") == [("FR", "17.5", "iPhone12,1", "fr", 0)]


def test_nothing_typed_or_picked_can_be_stored(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device,
        {"name": "paywall_shown", "props": {"reason": "Sonnerie de Camille"}},
        {"name": "contact_selected", "props": {"contact": "Camille"}},
        {"name": "search", "props": {"text": "anything"}}), headers={"CF-IPCountry": "XX"})
    stored = " ".join(p for (p,) in store.query("select props from events"))
    assert "Camille" not in stored and "anything" not in stored
    assert store.query("select name, count from dropped") == [("search", 1)]
    assert store.query("select country from devices") == [(None,)]


def test_screens_and_crashes_keep_only_a_known_screen(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device,
        {"name": "screen_viewed", "props": {"screen": "editor", "seconds": 12}},
        {"name": "screen_viewed", "props": {"screen": "Ma sonnerie"}},
        {"name": "crash_reported", "props": {"screen": "paywall", "stack": "0x1 main"}}))
    assert store.query("select name, props from events order by id") == [
        ("screen_viewed", '{"screen": "editor"}'), ("screen_viewed", "{}"),
        ("crash_reported", '{"screen": "paywall"}')]


def test_malformed_envelope_values_are_nulled_not_stored(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device, app="<script>", os="iOS seventeen", model="Simon's iPhone"))
    assert store.query("select app, os, model from devices") == [(None, None, None)]


def test_bad_batches_are_refused(client):
    assert client.post("/v1/events", json={"device": "not-a-uuid", "events": []}).status_code == 400
    too_many = [{"name": "app_opened"}] * 201
    assert client.post("/v1/events", json=batch(str(uuid.uuid4()), *too_many)).status_code == 400


def test_forgetting_a_device_removes_its_events(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device, {"name": "app_opened", "props": {"cold": "yes"}}))
    assert client.delete(f"/v1/devices/{device}").status_code == 204
    assert store.query("select count(*) from events") == [(0,)]
    assert store.query("select count(*) from devices") == [(0,)]


def test_console_needs_the_token(client):
    assert client.get("/console").status_code == 401
    assert client.get("/console", auth=("x", "wrong")).status_code == 401
    page = client.get("/console", auth=("x", "secret"))
    assert page.status_code == 200 and "Qui revient" in page.text


def test_console_refuses_everyone_without_a_configured_token(store):
    assert TestClient(create_app(store, admin_token="")).get("/console", auth=("x", "")).status_code == 401


def test_retention_only_counts_devices_old_enough(store):
    now = int(time.time())
    env = {"app": None, "os": None, "model": None, "lang": None, "country": None, "test": 0}
    # Came 10 days ago, back 8 days later.
    store.record("a", env, [("app_opened", now - 10 * DAY, {}), ("app_opened", now - 2 * DAY, {})], [],
                 now=now - 10 * DAY)
    # Came 10 days ago, never back.
    store.record("b", env, [("app_opened", now - 10 * DAY, {})], [], now=now - 10 * DAY)
    # Came yesterday: not old enough for the 7-day rate.
    store.record("c", env, [("app_opened", now - DAY, {})], [], now=now - DAY)
    m = compute(store, now=now)
    assert m["retention"][7] == (1, 2)
    assert m["retention"][1] == (1, 3)
    assert m["came_back"] == 1


def test_creations_per_device_and_tests_hidden(store):
    now = int(time.time())
    env = {"app": None, "os": None, "model": None, "lang": None, "country": None, "test": 0}
    store.record("a", env, [("ringtone_created", now, {})] * 3, [], now=now)
    store.record("b", env, [("app_opened", now, {})], [], now=now)
    store.record("t", {**env, "test": 1}, [("ringtone_created", now, {})] * 9, [], now=now)
    m = compute(store, now=now)
    assert dict(m["buckets"]) == {"0": 1, "1": 0, "2": 0, "3–5": 1, "6+": 0}
    assert m["created_total"] == 3 and m["tests_hidden"] == 1
    assert compute(store, include_tests=True, now=now)["created_total"] == 12


def test_data_older_than_thirteen_months_is_deleted(store):
    now = int(time.time())
    env = {"app": None, "os": None, "model": None, "lang": None, "country": None, "test": 0}
    old = now - RETENTION_SECONDS - DAY
    store.record("old", env, [("app_opened", old, {})], [], now=old)
    store.db.execute("delete from meta")
    store.record("new", env, [("app_opened", now, {})], [], now=now)
    assert store.query("select id from devices") == [("new",)]


def test_console_json_needs_its_own_token(store):
    client = TestClient(create_app(store, admin_token="secret", console_token="reader"))
    assert client.get("/v1/console.json").status_code == 401
    assert client.get("/v1/console.json", headers={"Authorization": "Bearer secret"}).status_code == 401
    r = client.get("/v1/console.json", headers={"Authorization": "Bearer reader"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"


def test_console_json_refuses_everyone_without_a_configured_token(store):
    client = TestClient(create_app(store, admin_token="secret", console_token=""))
    assert client.get("/v1/console.json", headers={"Authorization": "Bearer "}).status_code == 401


def test_console_json_counts_ads_purchases_and_sources(store):
    client = TestClient(create_app(store, admin_token="secret", console_token="reader"))
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device,
        {"name": "app_opened", "props": {}},
        {"name": "ringtone_created", "props": {"source": "video"}},
        {"name": "ringtone_created", "props": {"source": "sound"}},
        {"name": "ringtone_created", "props": {"source": "video"}},
        {"name": "ad_watched", "props": {}},
        {"name": "purchase", "props": {"product": "lifetime"}}))
    m = client.get("/v1/console.json", headers={"Authorization": "Bearer reader"}).json()
    assert m["devices"] == 1 and m["active_1"] == 1 and m["created_total"] == 3
    assert m["ads_total"] == 1 and m["ads_7"] == 1 and m["ads_devices_30"] == 1
    assert m["purchases_total"] == 1 and m["purchasers"] == 1
    assert m["by_source"] == [["video", 2], ["sound", 1]]
    assert m["by_product"] == [["lifetime", 1]]
    assert len(m["daily"]) == 395  # 13 months: everything the store keeps
    today = m["daily"][-1]
    assert len(m["hourly"]) == 48
    hour = m["hourly"][-1]
    assert (hour["created"], hour["ads"], hour["purchases"], hour["active"], hour["new"]) == (3, 1, 1, 1, 1)
    assert m["hourly"][-2]["created"] == 0 and hour["at"] - m["hourly"][-2]["at"] == 3600
    assert (today["created"], today["ads"], today["purchases"], today["active"]) == (3, 1, 1, 1)
    assert m["retention"]["1"] == [0, 0]
    assert m["collected"] == {"screen_viewed": True, "crash_reported": True}


def test_effects_are_shares_of_the_ringtones_made_in_30_days(store):
    now = int(time.time())
    env = {"app": None, "os": None, "model": None, "lang": None, "country": None, "test": 0}
    store.record("a", env, [
        ("ringtone_created", now, {"source": "video", "speed": "sped_up", "loop": "yes"}),
        ("ringtone_created", now, {"source": "video", "speed": "normal", "loop": "no"}),
        ("ringtone_created", now, {"source": "sound", "speed": "normal", "loop": "no"}),
        ("ringtone_created", now - 40 * DAY, {"source": "audio", "speed": "slowed", "loop": "yes"}),
    ], [], now=now)
    store.record("t", {**env, "test": 1}, [("ringtone_created", now, {"source": "audio"})] * 5, [], now=now)
    effects = compute(store, now=now)["effects"]
    assert effects["creations"] == 3
    rows = {(r["detail"], r["value"]): (r["count"], r["share"]) for r in effects["rows"]}
    assert rows[("source", "video")] == (2, 0.6667)
    assert rows[("speed", "sped_up")] == (1, 0.3333)
    assert ("source", "audio") not in rows  # older than 30 days, or from a test phone


def test_console_shows_the_effects_table(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device,
        {"name": "ringtone_created", "props": {"source": "video", "speed": "sped_up"}}))
    page = client.get("/console", auth=("x", "secret")).text
    assert "Effets utilisés, 30 jours" in page and "accélérée" in page and "100 %" in page


def test_every_setting_of_a_ringtone_is_kept_numbers_as_numbers(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device, {"name": "ringtone_created", "props": {
        "fade_in": "0", "fade_out": "3", "volume": "150", "bass": "light", "optimized": "yes",
        "sound": "ukulele", "duration": "28", "source": "sound",
        "volume_note": "loud", "fade_out_extra": "99999999"}}))
    (props,) = store.query("select props from events where name = 'ringtone_created'")[0]
    import json
    assert json.loads(props) == {"fade_in": 0, "fade_out": 3, "volume": 150, "bass": "light",
                                 "optimized": "yes", "sound": "ukulele", "duration": 28,
                                 "source": "sound"}


def test_a_number_out_of_range_or_not_a_number_is_dropped(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device, {"name": "ringtone_created", "props": {
        "volume": "-5", "duration": "1e3", "fade_in": "123456", "sound": "My Song.mp3"}}))
    (props,) = store.query("select props from events where name = 'ringtone_created'")[0]
    assert props == "{}"


def test_the_way_to_a_purchase_is_kept(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device,
        {"name": "paywall_plan_selected", "props": {"plan": "weekly"}},
        {"name": "purchase_started", "props": {"plan": "yearly", "trial": "yes"}},
        {"name": "purchase_ended", "props": {"plan": "yearly", "result": "cancelled"}},
        {"name": "paywall_dismissed", "props": {"how": "free"}},
        {"name": "purchase", "props": {"product": "yearly", "trial": "yes"}},
        {"name": "purchase", "props": {"product": "daily"}}))
    rows = store.query("select name, props from events order by rowid")
    import json
    assert [(n, json.loads(p)) for n, p in rows] == [
        ("paywall_plan_selected", {"plan": "weekly"}),
        ("purchase_started", {"plan": "yearly", "trial": "yes"}),
        ("purchase_ended", {"plan": "yearly", "result": "cancelled"}),
        ("paywall_dismissed", {"how": "free"}),
        ("purchase", {"product": "yearly", "trial": "yes"}),
        ("purchase", {}),
    ]


def test_series_count_each_event_in_its_own_bucket(store):
    """Days and hours are counted in one pass: an event lands in the bucket of
    its own time, and one older than the window is left out."""
    now = int(time.time())
    env = {"app": None, "os": None, "model": None, "lang": None, "country": None, "test": 0}
    store.record("a", env, [("ringtone_created", now - 3 * DAY, {}), ("ringtone_created", now - 2 * 3600, {}),
                            ("ringtone_created", now - 400 * DAY, {})], [], now=now - 3 * DAY)
    m = compute(store, now=now, days=10)
    assert [d["created"] for d in m["daily"]][-4] == 1
    assert sum(d["created"] for d in m["daily"]) == 2
    assert sum(h["created"] for h in m["hourly"]) == 1
    assert m["daily"][-1]["day"] == (now // DAY) * DAY


def test_the_welcome_is_measured(client, store):
    device = str(uuid.uuid4())
    client.post("/v1/events", json=batch(device,
        {"name": "onboarding_step", "props": {"step": "locks"}},
        {"name": "onboarding_effect", "props": {"effect": "bass"}},
        {"name": "paywall_shown", "props": {"reason": "onboarding"}}))
    import json
    rows = [json.loads(p) for (p,) in store.query("select props from events order by rowid")]
    assert rows == [{"step": "locks"}, {"effect": "bass"}, {"reason": "onboarding"}]



def test_sound_plays_are_counted_per_sound(client, store):
    """A listen of 3 s or more counts per sound, next to its stars and the
    ringtones made from it; a name the server does not know never gets in."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    client.post("/v1/events", json=batch(a,
        {"name": "sound_played", "props": {"sound": "ukulele", "from": "home"}},
        {"name": "sound_played", "props": {"sound": "ukulele", "from": "gallery"}},
        {"name": "sound_played", "props": {"sound": "Mon son.mp3", "from": "home"}},
        {"name": "sound_favorited", "props": {"sound": "ukulele", "on": "yes"}},
        {"name": "ringtone_created", "props": {"source": "sound", "sound": "ukulele"}}))
    client.post("/v1/events", json=batch(b, {"name": "sound_played", "props": {"sound": "menu", "from": "home"}}))
    assert "Mon son" not in " ".join(p for (p,) in store.query("select props from events"))
    sounds = compute(store)["sounds"]
    assert sounds[0] == ["ukulele", 2, 2, 1, 1, 1]
    assert sounds[1] == ["menu", 1, 1, 1, 0, 0]
