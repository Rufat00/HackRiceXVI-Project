import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402
from app.services import dj  # noqa: E402


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


# ---------------------------------------------------------------- party flow
def make_app(**cfg):
    d = tempfile.mkdtemp()
    base = dict(TESTING=True, DATABASE_PATH=os.path.join(d, "p.db"), SPOTIFY_MOCK=True, MOCK_TIME_SCALE=1.0, SKIP_MIN_VOTES=3)
    base.update(cfg)
    app = create_app(base)
    # These caches are process-global by design (shared across parties/hosts
    # in real usage), which means they must be reset between tests, or one
    # test's cached Spotify results/failures leak into the next.
    from app.services import party as _P
    _P._search_cache.clear()
    _P._fallback_cache.clear()
    return app


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


def test_autoplay_starts_even_when_spotify_reports_stale_playing_state():
    """Regression test: previously, if Spotify's /me/player reported something
    (possibly stale) as still playing while Pulse had no current play recorded,
    autopilot would never start a new song. It must always start when Pulse's
    own state has nothing playing."""
    import app.services.party as P

    class FakeSpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False
        def state(self, tokens):
            # Simulates Spotify claiming something is actively, healthily playing
            # even though Pulse has no record of starting it (e.g. stale session).
            return {"track_id": "spotify:track:doesnotmatter", "progress_ms": 5000,
                    "duration_ms": 200000, "is_playing": True, "device": "legion5"}
        def play(self, tokens, uri): self.played = uri
        def search(self, tokens, q, limit=12):
            return [{"id": "spotify:track:fake1", "title": "Fake Song", "artist": "Fake Artist",
                    "album": None, "year": 2024, "duration_ms": 180000, "genres": ["pop"],
                    "energy": .7, "danceability": .7, "valence": .7, "tempo": 120.0,
                    "popularity": 60, "art_url": None, "preview_url": None, "explicit": 0}]
        def get_track(self, tokens, uri): return None

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = FakeSpotify()
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("FAKE", "t", "tok", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='FAKE'").fetchone()
        # Directly exercise tick(): Pulse has no current play recorded, so it must
        # start regardless of the fake Spotify's claim that something is already playing.
        events = P.tick(party)
        assert any(e["type"] == "started" for e in events), events
        assert P.current_play(party["id"]) is not None


def test_spotify_state_not_polled_every_tick_once_track_is_underway():
    """The biggest source of avoidable Spotify API calls was calling
    /me/player on every single tick while a track was mid-play. Once a track
    is underway and not near its end, tick() should trust the local clock and
    not call state() at all."""
    import app.services.party as P

    calls = {"state": 0}

    class FakeSpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False
        def state(self, tokens):
            calls["state"] += 1
            return {"track_id": "spotify:track:fake1", "progress_ms": 5000,
                    "duration_ms": 180000, "is_playing": True, "device": "d"}
        def play(self, tokens, uri): pass
        def search(self, tokens, q, limit=12):
            return [{"id": "spotify:track:fake1", "title": "Fake Song", "artist": "Fake Artist",
                    "album": None, "year": 2024, "duration_ms": 180000, "genres": ["pop"],
                    "energy": .7, "danceability": .7, "valence": .7, "tempo": 120.0,
                    "popularity": 60, "art_url": None, "preview_url": None, "explicit": 0}]
        def get_track(self, tokens, uri): return None

    app = make_app(SPOTIFY_MOCK=False)
    fake = FakeSpotify()
    app.extensions["spotify"] = fake
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("FAKE2", "t", "tok2", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='FAKE2'").fetchone()
        events = P.tick(party)  # first tick: nothing playing -> starts directly, no state() needed
        assert any(e["type"] == "started" for e in events)
        assert calls["state"] == 0, "starting a fresh track shouldn't need a state() call at all"
        party = db.execute("SELECT * FROM parties WHERE code='FAKE2'").fetchone()
        # Immediately after starting, elapsed time is ~0, which is inside the
        # heartbeat window by design (a brief double-check right after a
        # track begins). Move the recorded start time back so the track
        # looks well underway and away from any heartbeat window, then
        # confirm repeated ticks trust the local clock and skip state().
        db.execute("UPDATE plays SET started_at=datetime('now', '-33 seconds') WHERE party_id=? AND ended_at IS NULL",
                   (party["id"],))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='FAKE2'").fetchone()
        extra_calls = 0
        for _ in range(5):
            before = calls["state"]
            P.tick(party)
            extra_calls += (calls["state"] - before)
        assert extra_calls == 0, f"expected state() to be skipped entirely mid-track away from a heartbeat window, got {extra_calls} calls"


def test_search_results_are_cached():
    import app.services.party as P

    calls = {"search": 0}

    class FakeSpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False
        def search(self, tokens, q, limit=12):
            calls["search"] += 1
            return [{"id": "spotify:track:cached1", "title": "Cached Song", "artist": "A",
                    "album": None, "year": 2024, "duration_ms": 180000, "genres": [],
                    "energy": .5, "danceability": .5, "valence": .5, "tempo": 100.0,
                    "popularity": 50, "art_url": None, "preview_url": None, "explicit": 0}]

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = FakeSpotify()
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("FAKE3", "t", "tok3", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='FAKE3'").fetchone()
        P.search_tracks(party, "same query")
        P.search_tracks(party, "same query")
        P.search_tracks(party, "Same Query  ")  # case/whitespace-insensitive cache key
        assert calls["search"] == 1, "identical queries should hit the cache, not Spotify, after the first"
        P.search_tracks(party, "a different query")
        assert calls["search"] == 2


def test_spotify_state_still_checked_near_track_end_regardless_of_heartbeat():
    """Even outside a heartbeat window, once a track is nearly over we must
    still check Spotify's real state so a finished song is caught promptly."""
    import app.services.party as P

    calls = {"state": 0}

    class FakeSpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False
        def state(self, tokens):
            calls["state"] += 1
            return {"track_id": "spotify:track:fake1", "progress_ms": 179999,
                    "duration_ms": 180000, "is_playing": True, "device": "d"}
        def play(self, tokens, uri): pass
        def search(self, tokens, q, limit=12):
            return [{"id": "spotify:track:fake1", "title": "Fake Song", "artist": "Fake Artist",
                    "album": None, "year": 2024, "duration_ms": 180000, "genres": [],
                    "energy": .7, "danceability": .7, "valence": .7, "tempo": 120.0,
                    "popularity": 60, "art_url": None, "preview_url": None, "explicit": 0}]
        def get_track(self, tokens, uri): return None

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = FakeSpotify()
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("FAKE4", "t", "tok4", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='FAKE4'").fetchone()
        P.tick(party)  # starts the track
        # Push started_at back so local elapsed is 179.998s into a 180s track:
        # deep in the "near end" zone, and at 179%20=19 -- NOT a heartbeat
        # second ([0,3)) -- isolating that near_end alone forces the check.
        db.execute("UPDATE plays SET started_at=datetime('now', '-179.998 seconds') WHERE party_id=? AND ended_at IS NULL",
                   (party["id"],))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='FAKE4'").fetchone()
        before = calls["state"]
        P.tick(party)
        assert calls["state"] == before + 1, "near the end of a track, state() must still be checked even off-heartbeat"


def test_host_skip_surfaces_rate_limit_instead_of_silently_failing():
    """Regression test: the host's manual Skip button called advance()
    directly with no error handling, so a Spotify 429 (or the cached-failure
    fast path that followed one) produced a silent no-op instead of telling
    the host why nothing happened."""
    from app.services.spotify import SpotifyError

    class RateLimitedSpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False
        def search(self, tokens, q, limit=12):
            raise SpotifyError('GET /search -> 429: {"error":{"status":429,"reason":"QUOTA_EXCEEDED"}}')
        def play(self, tokens, uri): pass

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = RateLimitedSpotify()
    host = app.test_client()
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("RLIM", "t", "rlimtok", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.commit()
    host.set_cookie("pulse_host", "rlimtok")
    r = host.post("/api/parties/RLIM/host/skip", json={})
    assert r.status_code in (502, 409), r.get_json()
    assert "error" in r.get_json() and r.get_json()["error"], "skip must report why it failed, not silently no-op"
    # A second immediate Skip (within the backoff window) must ALSO report
    # the error, not silently return success from the failure cache.
    r2 = host.post("/api/parties/RLIM/host/skip", json={})
    assert r2.status_code in (502, 409)
    assert "error" in r2.get_json() and r2.get_json()["error"]


def test_rate_limit_backs_off_longer_than_routine_empty_queue_cache():
    """A 429 should be remembered (and re-raised) for the longer
    _RATE_LIMIT_BACKOFF_SECONDS window, not the shorter routine
    _FALLBACK_CACHE_SECONDS window used for an ordinary successful search."""
    import app.services.party as P
    from app.services.spotify import SpotifyError

    calls = {"search": 0}

    class FlakySpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False
        def search(self, tokens, q, limit=12):
            calls["search"] += 1
            raise SpotifyError('GET /search -> 429: {"error":{"status":429,"reason":"QUOTA_EXCEEDED"}}')

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = FlakySpotify()
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("BKOF", "t", "bkoftok", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='BKOF'").fetchone()
        ctx = P.party_context(party)

        try:
            P.fallback_pool(party, ctx)
        except SpotifyError:
            pass
        assert calls["search"] == 1

        # Simulate time passing well past the routine 150s cache window but
        # still well within the 300s rate-limit backoff window: must NOT
        # re-call Spotify, must still raise the cached error.
        cached = P._fallback_cache[party["id"]]
        P._fallback_cache[party["id"]] = (cached[0] - 200, cached[1], cached[2], cached[3])
        try:
            P.fallback_pool(party, ctx)
            assert False, "expected the cached rate-limit error to be re-raised"
        except SpotifyError:
            pass
        assert calls["search"] == 1, "should not have re-called Spotify while still inside the 300s backoff"

        # Now simulate time past the FULL backoff window: should retry Spotify.
        P._fallback_cache[party["id"]] = (cached[0] - 301, cached[1], cached[2], cached[3])
        try:
            P.fallback_pool(party, ctx)
        except SpotifyError:
            pass
        assert calls["search"] == 2, "should retry Spotify once the full backoff window has elapsed"


def test_stale_mock_track_never_sent_to_real_spotify():
    """Regression test: a track suggested/played while a party was on the
    mock catalog (id like 'mock:m25') must never be handed to the real
    Spotify API once the party connects -- Spotify rejects non-Spotify URIs
    with a 400. The DJ must silently skip such candidates and start_play
    must refuse to send one even if it somehow slips through."""
    import app.services.party as P
    from app.services.spotify import SpotifyError

    class NeverCalledIfValidatedSpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False
        def play(self, tokens, uri):
            assert uri.startswith("spotify:track:"), f"tried to play a non-Spotify uri: {uri!r}"
        def search(self, tokens, q, limit=12):
            return [{"id": "spotify:track:real1", "title": "Real Song", "artist": "Real Artist",
                    "album": None, "year": 2024, "duration_ms": 180000, "genres": [],
                    "energy": .6, "danceability": .6, "valence": .6, "tempo": 110.0,
                    "popularity": 55, "art_url": None, "preview_url": None, "explicit": 0}]

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = NeverCalledIfValidatedSpotify()
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("STALE", "t", "staletok", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='STALE'").fetchone()
        g = db.execute("INSERT INTO guests (party_id, token, nickname) VALUES (?,?,?)", (party["id"], "g1", "Guest")).lastrowid
        # A leftover mock-catalog track, as if suggested before Spotify connected.
        db.execute("INSERT INTO tracks (id,title,artist,duration_ms,genres) VALUES (?,?,?,?,?)",
                   ("mock:m25", "Fake Mock Song", "Mock Artist", 180000, "[]"))
        db.execute("INSERT INTO suggestions (party_id, track_id, guest_id) VALUES (?,?,?)", (party["id"], "mock:m25", g))
        db.execute("INSERT INTO suggestion_votes (party_id, track_id, guest_id, value) VALUES (?,?,?,1)", (party["id"], "mock:m25", g))
        db.commit()

        # candidates() must not surface the stale mock track at all.
        ctx = P.party_context(party)
        cands = P.candidates(party, ctx)
        assert all(c["track"]["id"] != "mock:m25" for c in cands), "stale mock track leaked into candidates for a live-Spotify party"
        assert any(c["track"]["id"] == "spotify:track:real1" for c in cands), "a real fallback candidate should still be offered"

        # Belt-and-suspenders: even if start_play were called directly with
        # the stale track (bypassing candidates()), it must refuse rather
        # than call Spotify's play() with a bad uri.
        party = db.execute("SELECT * FROM parties WHERE code='STALE'").fetchone()
        bad_cand = {"track": P.get_track("mock:m25"), "score": 1.0, "components": {}, "explanation": "x"}
        try:
            P.start_play(party, bad_cand)
            assert False, "expected start_play to refuse a non-Spotify track id"
        except SpotifyError as e:
            assert "mock:m25" in str(e)

        # And advance() picking the real candidate should work end-to-end.
        picked = P.advance(party, "test")
        assert picked["track"]["id"] == "spotify:track:real1"


def test_seed_real_tracks_endpoint():
    """The host 'Load test tracks' button should require live Spotify, and
    when live, load a deduplicated set of well-formed real Spotify URIs as
    crowd suggestions with zero /search calls."""
    from app.services.real_test_tracks import REAL_TRACKS

    uris = [u for u, *_ in REAL_TRACKS]
    assert len(uris) == len(set(uris)), "duplicate URIs in the real-track list"
    for uri, *_ in REAL_TRACKS:
        assert uri.startswith("spotify:track:") and len(uri) == len("spotify:track:") + 22

    app = make_app(SPOTIFY_MOCK=True)
    host = app.test_client()
    code = host.post("/api/parties", json={"name": "Seed test"}).get_json()["code"]
    r = host.post(f"/api/parties/{code}/host/seed-test-tracks", json={})
    assert r.status_code == 400  # refuses in mock mode

    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token, spotify_tokens) VALUES (?,?,?,?)",
                   ("SEED2", "t", "seedtok2", '{"access":"x","refresh":"y","expires_at":9999999999}'))
        db.execute("""INSERT INTO guests (party_id, token, nickname, is_host)
                      VALUES ((SELECT id FROM parties WHERE code='SEED2'),?,?,1)""", ("seedtok2", "Host"))
        db.commit()

    class NoOpSpotify:
        mock = False
        def ensure_fresh(self, tokens): return tokens, False

    app.extensions["spotify"] = NoOpSpotify()
    app.config["SPOTIFY_MOCK"] = False
    host2 = app.test_client()
    host2.set_cookie("pulse_host", "seedtok2")
    host2.set_cookie("pulse_guest", "seedtok2")
    r = host2.post("/api/parties/SEED2/host/seed-test-tracks", json={})
    assert r.status_code == 200
    d = r.get_json()
    assert d["added"] == d["total"] == len(REAL_TRACKS)

    # candidates() should now surface these as real, playable crowd picks
    import app.services.party as P
    with app.app_context():
        from app.db import get_db
        party = get_db().execute("SELECT * FROM parties WHERE code='SEED2'").fetchone()
        ctx = P.party_context(party)
        cands = P.candidates(party, ctx, include_fallback=False)
        assert len(cands) == len(REAL_TRACKS)
        assert all(c["track"]["id"].startswith("spotify:track:") for c in cands)
        assert all(c["source"] == "crowd" for c in cands)


def test_spotify_callback_auto_seeds_real_tracks():
    """Connecting Spotify for a party should automatically queue the curated
    real-track list -- no manual 'Load test tracks' click needed -- so
    there's something real to play immediately with zero /search calls."""

    class FakeSpotify:
        mock = False
        def auth_url(self, state): return f"https://fake/{state}"
        def exchange_code(self, code): return {"access": "tok", "refresh": "ref", "expires_at": 9999999999}
        def ensure_fresh(self, tokens): return tokens, False
        def play(self, tokens, uri): pass

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = FakeSpotify()
    host = app.test_client()
    code = host.post("/api/parties", json={"name": "Auto-seed test"}).get_json()["code"]

    r = host.get(f"/callback?code=fakecode&state={code}")
    assert r.status_code == 302 and "spotify=connected" in r.headers["Location"]

    st = host.get(f"/api/parties/{code}/state").json
    assert st["party"]["spotify_live"] is True
    assert len(st["queue"]) > 0
    assert all(q["track"]["id"].startswith("spotify:track:") for q in st["queue"])
    assert st["now_playing"] is not None
    assert st["now_playing"]["track"]["id"].startswith("spotify:track:")


def test_seed_and_suggest_is_idempotent():
    """Calling the shared seed-and-suggest helper twice for the same party
    (e.g. a guest reconnect triggers the callback path twice, or the host
    also clicks 'Load test tracks' after auto-seed already ran) must not
    error or duplicate suggestions."""
    from app.services.real_test_tracks import seed_and_suggest, REAL_TRACKS
    import app.services.party as P

    app = make_app(SPOTIFY_MOCK=True)
    with app.app_context():
        from app.db import get_db
        db = get_db()
        db.execute("INSERT INTO parties (code, name, host_token) VALUES (?,?,?)", ("IDEM", "t", "idemtok"))
        gid = db.execute("INSERT INTO guests (party_id, token, nickname, is_host) VALUES ((SELECT id FROM parties WHERE code='IDEM'),?,?,1)",
                         ("idemtok", "Host")).lastrowid
        db.commit()
        party = db.execute("SELECT * FROM parties WHERE code='IDEM'").fetchone()

        added1, total1 = seed_and_suggest(party, gid, P.upsert_track, db)
        assert added1 == total1 == len(REAL_TRACKS)
        added2, total2 = seed_and_suggest(party, gid, P.upsert_track, db)
        assert added2 == 0, "second call should add nothing new (already suggested)"
        assert total2 == len(REAL_TRACKS)

        n = db.execute("SELECT COUNT(*) AS n FROM suggestions WHERE party_id=?", (party["id"],)).fetchone()["n"]
        assert n == len(REAL_TRACKS), "no duplicate suggestion rows after calling twice"


def test_connecting_spotify_auto_seeds_real_tracks_with_no_button_click():
    """Connecting Spotify for a party should immediately queue the curated
    real-track list (no /search call, no manual 'Load test tracks' click
    needed) so autopilot can start playing real audio right away."""
    class FakeSpotify:
        mock = False
        def auth_url(self, state): return f"https://fake/auth?state={state}"
        def exchange_code(self, code):
            return {"access": "a", "refresh": "r", "expires_at": 9999999999}
        def ensure_fresh(self, tokens): return tokens, False
        def play(self, tokens, uri): pass

    app = make_app(SPOTIFY_MOCK=False)
    app.extensions["spotify"] = FakeSpotify()
    host = app.test_client()
    code = host.post("/api/parties", json={"name": "auto-seed test"}).get_json()["code"]

    r = host.get(f"/callback?code=FAKE&state={code}")
    assert r.status_code == 302 and "spotify=connected" in r.headers["Location"]

    d = host.get(f"/api/parties/{code}/state").get_json()
    assert d["party"]["spotify_live"] is True
    assert d["now_playing"] is not None
    assert d["now_playing"]["track"]["id"].startswith("spotify:track:")
    assert len(d["queue"]) > 0
    assert all(q["track"]["id"].startswith("spotify:track:") for q in d["queue"])
