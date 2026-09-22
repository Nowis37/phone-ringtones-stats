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
