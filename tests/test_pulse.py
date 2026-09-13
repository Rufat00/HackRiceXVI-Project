import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402
from app.services import dj  # noqa: E402
from app.services.youtube import NON_SONG_TITLE, _audio_profile  # noqa: E402


# ---------------------------------------------------------------- dj brain
def test_parse_theme_decades_and_vibes():
    t = dj.parse_theme("2000s throwback bangers")
    assert (t["year_from"], t["year_to"]) == (2000, 2009) and t["target_energy"] == 0.9
    t = dj.parse_theme("80s synth night")
    assert (t["year_from"], t["year_to"]) == (1980, 1989) and "synth pop" in t["keywords"]
    t = dj.parse_theme("chill latin vibes")
    assert "latin" in t["keywords"] and 0.3 < t["target_energy"] < 0.6
    t = dj.parse_theme("hip-hop only")
    assert t["keywords"] == ["hip hop"]


def test_energy_arc_shape():
    assert dj.energy_arc(0, 240) < dj.energy_arc(60, 240) <= dj.energy_arc(120, 240)
    assert dj.energy_arc(235, 240) < dj.energy_arc(120, 240)


def test_affinity_learns_from_votes():
    aff = dj.genre_affinity([{"genres": ["latin"], "up": 6, "down": 0}, {"genres": ["metal"], "up": 0, "down": 5}])
    assert aff["latin"] > 0.5 and aff["metal"] < -0.5


def test_scoring_prefers_demand_theme_and_penalizes_replay():
    base = {"genres": ["pop"], "energy": .8, "year": 2005, "artist": "A", "title": "x", "popularity": 60}
    ctx = {"n_suggesters": 0, "net_votes": 0, "target_energy": .8, "affinity": {}, "recent_artists": [], "played_recently": False}
    s0, _ = dj.score_candidate(base, ctx)
    s_demand, c = dj.score_candidate(base, dict(ctx, n_suggesters=4))
    assert s_demand > s0 and c["demand"] > 0
    theme = dj.parse_theme("2000s pop"); theme["text"] = "2000s pop"
    s_theme, c = dj.score_candidate(base, dict(ctx, theme=theme))
    assert s_theme > s0 and c["theme"] > 0
    s_wrong_decade, c = dj.score_candidate(dict(base, year=1985), dict(ctx, theme=theme))
    assert s_wrong_decade < s_theme
    s_replay, c = dj.score_candidate(base, dict(ctx, played_recently=True))
    assert s_replay < s0 - 5
    s_repeat, c = dj.score_candidate(base, dict(ctx, recent_artists=["A"]))
    assert s_repeat < s0
    assert "asked for it" in dj.explain(dj.score_candidate(base, dict(ctx, n_suggesters=3))[1], dict(ctx, n_suggesters=3), base)


def test_skip_rule():
    assert not dj.should_skip(1, 2, 4, -0.5)        # too few votes
    assert dj.should_skip(1, 5, 4, -0.5)            # buried
    assert not dj.should_skip(3, 3, 4, -0.5)        # split room


def test_youtube_single_song_filter_and_vibe_profile():
    assert NON_SONG_TITLE.search("Summer Hits 2026 - Full Album")
    assert NON_SONG_TITLE.search("Best Dance Music Mix")
    assert not NON_SONG_TITLE.search("Daft Punk - One More Time (Official Video)")
    genres, energy, tempo = _audio_profile("upbeat latin party song")
    assert "latin" in genres and energy > .75 and tempo >= 120


# ---------------------------------------------------------------- party flow
def make_app(**cfg):
    d = tempfile.mkdtemp()
    base = dict(TESTING=True, DATABASE_PATH=os.path.join(d, "p.db"), SPOTIFY_MOCK=True,
                YOUTUBE_API_KEY="", MOCK_TIME_SCALE=1.0, SKIP_MIN_VOTES=3)
    base.update(cfg)
    return create_app(base)


def join(app, code, nick):
    c = app.test_client()
    r = c.post(f"/api/parties/{code}/join", json={"nickname": nick})
    assert r.status_code == 201, r.json
    return c


def test_party_end_to_end():
    app = make_app()
    host = app.test_client()
    r = host.post("/api/parties", json={"name": "Test bash", "host_name": "Marz"})
    assert r.status_code == 201
    code = r.json["code"]
    assert len(code) == 4

    # QR renders
    assert host.get(f"/api/parties/{code}/qr.png").mimetype == "image/png"

    # first state tick starts autopilot
    st = host.get(f"/api/parties/{code}/state").json
    assert st["now_playing"] is not None and st["now_playing"]["chosen_by"] == "dj"
    assert st["me"]["is_host"] is True and len(st["queue"]) > 0

    g1, g2, g3 = join(app, code, "Ana"), join(app, code, "Ben"), join(app, code, "Cy")
    # unjoined can't act
    assert app.test_client().post(f"/api/parties/{code}/vote", json={"value": 1}).status_code == 401

    # search + suggest the same track from 3 guests -> it should top the queue
    res = g1.get(f"/api/parties/{code}/search?q=merengue").json["tracks"]
    tid = res[0]["id"]
    for gc in (g1, g2, g3):
        assert gc.post(f"/api/parties/{code}/suggest", json={"track_id": tid}).status_code == 201
    assert g1.post(f"/api/parties/{code}/suggest", json={"track_id": tid}).status_code == 409
    st = g1.get(f"/api/parties/{code}/state").json
    top = st["queue"][0]
    assert top["track"]["id"] == tid and top["n_suggesters"] == 3 and top["source"] == "crowd"
    assert "3 people asked for it" in top["explanation"]

    # theme: propose + votes -> becomes active and shows in dj block
    r = g1.post(f"/api/parties/{code}/themes", json={"text": "latin night"})
    assert r.status_code == 201 and "latin" in r.json["parsed"]["keywords"]
    theme_id = g1.get(f"/api/parties/{code}/state").json["themes"][0]["id"]
    g2.post(f"/api/parties/{code}/themes/{theme_id}/vote", json={"value": 1})
    st = g2.get(f"/api/parties/{code}/state").json
    assert st["dj"]["active_theme"] == "latin night"

    # host skip -> the crowd's top pick plays next
    r = host.post(f"/api/parties/{code}/host/skip", json={})
    assert r.status_code == 200 and r.json["next"]["id"] == tid
    st = host.get(f"/api/parties/{code}/state").json
    assert st["now_playing"]["track"]["id"] == tid
    assert all(q["track"]["id"] != tid for q in st["queue"])   # consumed from queue
    assert st["history"][0]["end_reason"] == "skipped_by_host"

    # votes on now playing: toggle, and crowd skip when buried
    r = g1.post(f"/api/parties/{code}/vote", json={"value": 1}).json
    assert (r["up"], r["down"], r["my_vote"]) == (1, 0, 1)
    r = g1.post(f"/api/parties/{code}/vote", json={"value": 1}).json      # toggle off
    assert (r["up"], r["my_vote"]) == (0, None)
    for gc in (g1, g2, g3):
        gc.post(f"/api/parties/{code}/vote", json={"value": -1})
    st = g1.get(f"/api/parties/{code}/state").json
    assert any(e["type"] == "crowd_skip" for e in st["events"])
    assert st["now_playing"]["track"]["id"] != tid
    hist = host.get(f"/api/parties/{code}/state").json["history"]
    assert hist[0]["end_reason"] == "skipped_by_crowd" and hist[0]["down"] == 3

    # replay guard: the buried song shouldn't come back
    assert all(q["track"]["id"] != tid for q in st["queue"])

    # affinity: room downvoted a latin song -> latin affinity negative in dj block
    assert st["dj"]["affinity"].get("latin", 0) < 0

    # guest can't use host controls
    assert g1.post(f"/api/parties/{code}/host/skip", json={}).status_code == 403


def test_mock_player_advances_with_time_scale():
    app = make_app(MOCK_TIME_SCALE=400.0)   # 3-min song ≈ 0.45 s
    host = app.test_client()
    code = host.post("/api/parties", json={}).json["code"]
    first = host.get(f"/api/parties/{code}/state").json["now_playing"]["track"]["id"]
    time.sleep(0.8)
    st = host.get(f"/api/parties/{code}/state").json
    assert st["now_playing"]["track"]["id"] != first
    assert any(e["type"] == "advanced" for e in st["events"])


def test_youtube_player_finish_advances_server_queue():
    app = make_app(YOUTUBE_API_KEY="test-key")

    class FakeYouTube:
        def search(self, _query):
            return [
                {"id": f"youtube:test{i}", "title": f"Song {i}", "artist": "Artist",
                 "album": "YouTube", "year": 2026, "duration_ms": 180000,
                 "genres": ["music"], "energy": None, "danceability": None,
                 "valence": None, "tempo": None, "popularity": 50,
                 "art_url": None, "preview_url": None, "explicit": 0}
                for i in range(4)
            ]

        def trending(self, region="US", limit=25):
            return self.search(f"{region}:{limit}")

    app.extensions["youtube"] = FakeYouTube()
    host = app.test_client()
    code = host.post("/api/parties", json={}).json["code"]
    first_state = host.get(f"/api/parties/{code}/state").json
    assert first_state["party"]["playback_source"] == "youtube"
    assert first_state["now_playing"]["track"]["id"].startswith("youtube:")
    play_id = first_state["now_playing"]["play_id"]

    r = host.post(f"/api/parties/{code}/host/player-ended", json={"play_id": play_id})
    assert r.status_code == 200 and r.json["next"]
    next_state = host.get(f"/api/parties/{code}/state").json
    assert next_state["now_playing"]["play_id"] != play_id

    # A duplicated FINISH event from the old iframe must not skip two songs.
    r = host.post(f"/api/parties/{code}/host/player-ended", json={"play_id": play_id})
    assert r.status_code == 200 and r.json["stale"] is True


def test_rate_limit_and_theme_dedupe():
    app = make_app(SUGGESTIONS_PER_GUEST_PER_HOUR=2)
    host = app.test_client()
    code = host.post("/api/parties", json={}).json["code"]
    g1 = join(app, code, "Ana")
    ids = [t["id"] for t in g1.get(f"/api/parties/{code}/search?q=").json["tracks"]]
    host.get(f"/api/parties/{code}/state")
    playing = host.get(f"/api/parties/{code}/state").json["now_playing"]["track"]["id"]
    ids = [i for i in ids if i != playing]
    assert g1.post(f"/api/parties/{code}/suggest", json={"track_id": ids[0]}).status_code == 201
    assert g1.post(f"/api/parties/{code}/suggest", json={"track_id": ids[1]}).status_code == 201
    assert g1.post(f"/api/parties/{code}/suggest", json={"track_id": ids[2]}).status_code == 429
    assert g1.post(f"/api/parties/{code}/themes", json={"text": "90s"}).status_code == 201
    assert g1.post(f"/api/parties/{code}/themes", json={"text": "90s"}).status_code == 409


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main(["-q", __file__]))
