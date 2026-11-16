"""The drip's proof: what the offer shows, refreshed on the NAS."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "drip"))
import drip  # noqa: E402


def test_floored_moves_once_per_hundred():
    assert drip.floored(8214) == 8200
    assert drip.floored(8299) == 8200
    assert drip.floored(456) == 450
    assert drip.floored(42) == 42


def test_rating_is_weighted_by_count_and_ignores_empty_storefronts():
    # 4.66, rounded down as CLAUDE.md says: never a tenth the app has not earned.
    assert drip.rating_of([(4.7, 114), (4.6, 74), (0, 0)]) == 4.6
    assert drip.rating_of([(4.7, 3), (0, 0)]) == 4.7
    assert drip.rating_of([(0, 0), (0, 0)]) is None


def test_the_count_shown_never_goes_down():
    # Erased devices took 40 ringtones out of the stats: still "plus de 8 200".
    assert drip.merged_proof({"ringtones": 8000}, {"ringtones": 8100}, {"ringtones": 8200}) == {"ringtones": 8200}
    assert drip.merged_proof({"ringtones": 8000}, {"ringtones": 8300}, {"ringtones": 8200}) == {"ringtones": 8300}
    # Nothing read now: catalogue-full.json's figure, still never lower.
    assert drip.merged_proof({"ringtones": 8000, "rating": 4.6}, {}, {"ringtones": 8200}) == {"ringtones": 8200, "rating": 4.6}
    assert drip.merged_proof({"rating": 4.6}, {}, {}) == {"rating": 4.6}


def test_live_proof_keeps_what_it_cannot_read(monkeypatch):
    def get(url, headers=None):
        if "console.json" in url:
            raise OSError("stats down")
        return {"results": [{"averageUserRating": 5.0, "userRatingCount": 1}]} if "country=fr" in url else {"results": []}
    monkeypatch.setattr(drip, "get", get)
    monkeypatch.setattr(drip.time, "sleep", lambda s: None)
    monkeypatch.setattr(drip, "storefronts", {})
    monkeypatch.setattr(drip, "rated_at", 0.0)
    monkeypatch.setenv("CONSOLE_TOKEN", "x")
    assert drip.live_proof() == {"rating": 5.0}
    full = {"sounds": [], "proof": {"rating": 4.7, "ringtones": 8214}}
    catalogue = drip.visible(full, "2026-11-03")
    assert {**catalogue["proof"], **drip.live_proof()} == {"rating": 5.0, "ringtones": 8214}


def test_rating_waits_for_every_storefront(monkeypatch):
    def get(url, headers=None):
        if "country=us" in url:
            raise OSError("rate limited")
        return {"results": [{"averageUserRating": 4.0, "userRatingCount": 3}]}
    monkeypatch.setattr(drip, "get", get)
    monkeypatch.setattr(drip.time, "sleep", lambda s: None)
    monkeypatch.setattr(drip, "storefronts", {})
    monkeypatch.setattr(drip, "rated_at", 0.0)
    monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
    assert drip.live_proof() == {}
    assert drip.rated_at == 0.0
