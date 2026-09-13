"""Party engine: candidates -> DJ brain -> playback. One tick() per poll."""
import json
import time
from datetime import datetime, timezone

from flask import current_app

from ..db import get_db
from . import catalog, dj
from .spotify import SpotifyError
from .youtube import YouTubeError


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).strftime("%Y-%m-%d %H:%M:%S")


def _sp():
    return current_app.extensions.get("spotify")


def _yt():
    return current_app.extensions.get("youtube")


def tokens_for(party):
    return json.loads(party["spotify_tokens"]) if party["spotify_tokens"] else None


def save_tokens(party_id, tokens):
    get_db().execute("UPDATE parties SET spotify_tokens=? WHERE id=?", (json.dumps(tokens), party_id))
    get_db().commit()


def spotify_live(party):
    return bool(_sp()) and not current_app.config["SPOTIFY_MOCK"] and tokens_for(party) is not None


def youtube_live(party):
    """YouTube is the fallback player until this party connects Spotify."""
    return bool(_yt()) and not spotify_live(party)


def track_playable(party, track):
    track_id = (track or {}).get("id", "")
    if spotify_live(party):
        return track_id.startswith("spotify:")
    if youtube_live(party):
        return track_id.startswith("youtube:")
    return track_id.startswith("mock:")


# ---------------------------------------------------------------- tracks
def upsert_track(t):
    db = get_db()
    db.execute(
        """INSERT INTO tracks (id,title,artist,album,year,duration_ms,genres,energy,danceability,valence,tempo,popularity,art_url,preview_url,explicit)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET genres=excluded.genres, energy=COALESCE(excluded.energy, tracks.energy),
             danceability=COALESCE(excluded.danceability, tracks.danceability), valence=COALESCE(excluded.valence, tracks.valence),
             tempo=COALESCE(excluded.tempo, tracks.tempo), popularity=COALESCE(excluded.popularity, tracks.popularity),
             art_url=COALESCE(excluded.art_url, tracks.art_url)""",
        (t["id"], t["title"], t["artist"], t.get("album"), t.get("year"), t["duration_ms"], json.dumps(t.get("genres", [])),
         t.get("energy"), t.get("danceability"), t.get("valence"), t.get("tempo"), t.get("popularity"),
         t.get("art_url"), t.get("preview_url"), int(bool(t.get("explicit")))),
    )


def track_dict(row):
    d = dict(row)
    d["genres"] = json.loads(d["genres"] or "[]")
    return d


def get_track(track_id):
    r = get_db().execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
    return track_dict(r) if r else None


_search_cache = {}  # {(source, query): (expires_at, results)}
SEARCH_CACHE_TTL_S = 120
YOUTUBE_SEARCH_CACHE_TTL_S = 60 * 60


def search_tracks(party, q):
    source = "spotify" if spotify_live(party) else "youtube" if youtube_live(party) else "catalog"
    key = (source, q.strip().lower())
    hit = _search_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    if spotify_live(party):
        toks, changed = _sp().ensure_fresh(tokens_for(party))
        if changed:
            save_tokens(party["id"], toks)
        results = _sp().search(toks, q)
    elif youtube_live(party):
        results = _yt().search(q)
    else:
        results = catalog.search(q)
    for t in results:
        upsert_track(t)
    get_db().commit()
    ttl = YOUTUBE_SEARCH_CACHE_TTL_S if source == "youtube" else SEARCH_CACHE_TTL_S
    _search_cache[key] = (time.time() + ttl, results)
    return results


def youtube_chart_tracks(party):
    """Cached mainstream music chart for Auto DJ fallback recommendations."""
    region = current_app.config["YOUTUBE_REGION_CODE"].upper()
    key = ("youtube", f"__most_popular_music__:{region}")
    hit = _search_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    results = _yt().trending(region=region, limit=25)
    for track in results:
        upsert_track(track)
    get_db().commit()
    _search_cache[key] = (time.time() + YOUTUBE_SEARCH_CACHE_TTL_S, results)
    return results


# ---------------------------------------------------------------- state
def current_play(party_id):
    return get_db().execute(
        "SELECT * FROM plays WHERE party_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1", (party_id,)).fetchone()


def play_votes(play_id):
    r = get_db().execute(
        "SELECT SUM(value=1) AS up, SUM(value=-1) AS down FROM play_votes WHERE play_id=?", (play_id,)).fetchone()
    return int(r["up"] or 0), int(r["down"] or 0)


def elapsed_ms(play):
    # MOCK_TIME_SCALE only makes sense for the simulated player. If Spotify is
    # genuinely connected we must reason in real time regardless of that
    # setting, since we now lean on this clock to decide when to bother
    # calling Spotify at all.
    scale = 1.0 if spotify_live_for_clock() else current_app.config["MOCK_TIME_SCALE"]
    started = datetime.strptime(play["started_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int((_now() - started).total_seconds() * 1000 * scale)


def spotify_live_for_clock():
    # elapsed_ms only receives a play row, so use configured real-player
    # availability. YouTube also runs at wall-clock speed.
    return bool(_yt()) or (bool(_sp()) and not current_app.config["SPOTIFY_MOCK"])


# How often (in seconds of a track's own elapsed time) we double-check with a
# real Spotify call while a track is mid-play, to catch a host manually
# changing the song. Between heartbeats we trust our own clock, which is the
# single biggest cut to Spotify API call volume — previously every poll
# (~every 3s per open host screen) called /me/player regardless.
SPOTIFY_POLL_HEARTBEAT_S = 20


def recent_plays(party_id, limit=12):
    rows = get_db().execute(
        """SELECT p.*, t.genres, t.energy, t.artist FROM plays p JOIN tracks t ON t.id=p.track_id
           WHERE p.party_id=? ORDER BY p.id DESC LIMIT ?""", (party_id, limit)).fetchall()
    out = []
    for r in rows:
        up, down = play_votes(r["id"])
        out.append({"track_id": r["track_id"], "genres": json.loads(r["genres"] or "[]"), "energy": r["energy"],
                    "artist": r["artist"], "up": up, "down": down, "started_at": r["started_at"], "play_id": r["id"]})
    return out


def active_theme(party):
    db = get_db()
    if party["pinned_theme_id"]:
        r = db.execute("SELECT * FROM themes WHERE id=?", (party["pinned_theme_id"],)).fetchone()
        if r:
            return _theme_dict(r, pinned=True)
    r = db.execute(
        """SELECT t.*, COALESCE(SUM(v.value),0) AS net FROM themes t LEFT JOIN theme_votes v ON v.theme_id=t.id
           WHERE t.party_id=? GROUP BY t.id HAVING net >= 1 ORDER BY net DESC, t.id ASC LIMIT 1""", (party["id"],)).fetchone()
    return _theme_dict(r) if r else None


def _theme_dict(r, pinned=False):
    d = dict(r)
    d["keywords"] = json.loads(d["keywords"] or "[]")
    d["pinned"] = pinned
    return d


def _phase(elapsed_min, total_min):
    f = elapsed_min / total_min if total_min else elapsed_min / 240.0
    return "warm-up" if f < 0.15 else "building" if f < 0.35 else "peak" if f < 0.80 else "wind-down"


def party_context(party):
    """Everything the scorer needs, computed once per tick."""
    rp = recent_plays(party["id"])
    elapsed = dj.minutes_since(party["started_at"])
    total = None
    if party["ends_at"]:
        total = max(1.0, dj.minutes_since(party["started_at"]) - dj.minutes_since(party["ends_at"]))
    theme = active_theme(party)
    target = dj.energy_arc(elapsed, total)
    if theme and theme.get("target_energy") is not None:
        target = 0.5 * target + 0.5 * theme["target_energy"]
    target += dj.crowd_energy_adjust(list(reversed(rp)))
    target = max(0.2, min(0.98, target))
    settings = json.loads(party["settings"] or "{}")
    no_replay = current_app.config["NO_REPLAY_MINUTES"]
    played_recently = {p["track_id"] for p in rp if dj.minutes_since(p["started_at"]) < no_replay}
    return {
        "theme": theme, "target_energy": round(target, 2), "affinity": dj.genre_affinity(rp),
        "recent_artists": [p["artist"] for p in rp[:4]], "played_recently": played_recently,
        "block_explicit": bool(settings.get("block_explicit")), "elapsed_min": round(elapsed, 1),
        "phase": _phase(elapsed, total),
    }


# ---------------------------------------------------------------- candidates
def candidates(party, ctx, include_fallback=True):
    db = get_db()
    rows = db.execute(
        """SELECT s.track_id, COUNT(DISTINCT s.guest_id) AS n, MIN(s.created_at) AS first_at,
                  (SELECT COALESCE(SUM(value),0) FROM suggestion_votes v WHERE v.party_id=s.party_id AND v.track_id=s.track_id) AS net
           FROM suggestions s WHERE s.party_id=? GROUP BY s.track_id""", (party["id"],)).fetchall()
    out = []
    seen = set()
    for r in rows:
        t = get_track(r["track_id"])
        if not t or not track_playable(party, t):
            continue
        seen.add(t["id"])
        c = dict(ctx, n_suggesters=r["n"], net_votes=r["net"], first_suggested_min_ago=dj.minutes_since(r["first_at"]),
                 played_recently=t["id"] in ctx["played_recently"])
        s, comp = dj.score_candidate(t, c)
        out.append({"track": t, "score": s, "components": comp, "n_suggesters": r["n"], "net_votes": r["net"],
                    "explanation": dj.explain(comp, c, t), "source": "crowd"})
    if include_fallback and (not out or max(o["score"] for o in out) < 1.0 or len(out) < 3):
        for t in fallback_pool(party, ctx):
            if t["id"] in seen:
                continue
            c = dict(ctx, n_suggesters=0, net_votes=0, first_suggested_min_ago=0, played_recently=t["id"] in ctx["played_recently"])
            s, comp = dj.score_candidate(t, c)
            out.append({"track": t, "score": s - 1.0, "components": comp, "n_suggesters": 0, "net_votes": 0,
                        "explanation": "DJ autopilot: " + dj.explain(comp, c, t), "source": "dj"})
    out.sort(key=lambda o: o["score"], reverse=True)
    return out


_fallback_cache = {}  # party_id -> (fetched_at_monotonic, query, tracks)
_FALLBACK_CACHE_SECONDS = 25
_RATE_LIMIT_BACKOFF_SECONDS = 300  # 5 min backoff after a real 429


def fallback_pool(party, ctx):
    """When the crowd hasn't queued enough, the DJ fills from the catalog
    (mock), YouTube's regional music chart, or a theme-driven Spotify search.

    Cached briefly: without this, an empty queue on a live party means every
    3-second poll re-searches Spotify, which burns through their rate limit
    fast (this is exactly what happened during testing — repeated polling
    with nothing queued hammered /search continuously)."""
    if spotify_live(party) or youtube_live(party):
        if youtube_live(party):
            # Use YouTube's regional Music chart as the recommendation pool.
            # The DJ scorer applies themes, learned affinity, energy and
            # replay penalties locally; it never searches literal phrases
            # such as "groovy party song" anymore.
            q = f"mainstream-chart:{current_app.config['YOUTUBE_REGION_CODE'].upper()}"
        else:
            kws = (ctx["theme"] or {}).get("keywords") or []
            vibe = "chill" if ctx["target_energy"] < 0.5 else "upbeat" if ctx["target_energy"] > 0.72 else "groovy"
            q = " ".join(kws[:2]) if kws else f"party {vibe}"
        cached = _fallback_cache.get(party["id"])
        if cached and cached[1] == q and (time.monotonic() - cached[0]) < _FALLBACK_CACHE_SECONDS:
            return cached[2]
        try:
            tracks = youtube_chart_tracks(party) if youtube_live(party) else search_tracks(party, q)
        except YouTubeError:
            _fallback_cache[party["id"]] = (time.monotonic(), q, [])
            raise
        except SpotifyError as e:
            if "429" in str(e) or "QUOTA_EXCEEDED" in str(e):
                # Still cache the failure briefly so a rate limit doesn't
                # itself get hammered every poll while it's in effect.
                _fallback_cache[party["id"]] = (time.monotonic(), q, [])
                raise
            return []
        _fallback_cache[party["id"]] = (time.monotonic(), q, tracks)
        return tracks
    return catalog.CATALOG


# ---------------------------------------------------------------- playback
def start_play(party, cand, chosen_by="dj"):
    db = get_db()
    t = cand["track"]
    upsert_track(t)
    if spotify_live(party):
        toks, changed = _sp().ensure_fresh(tokens_for(party))
        if changed:
            save_tokens(party["id"], toks)
        _sp().play(toks, t["id"])
    cur = db.execute(
        "INSERT INTO plays (party_id, track_id, chosen_by, score, explanation) VALUES (?,?,?,?,?)",
        (party["id"], t["id"], chosen_by, cand.get("score"), json.dumps({"components": cand.get("components"), "text": cand.get("explanation")})))
    # Once it plays, everyone's suggestion for it is fulfilled.
    db.execute("DELETE FROM suggestions WHERE party_id=? AND track_id=?", (party["id"], t["id"]))
    db.execute("DELETE FROM suggestion_votes WHERE party_id=? AND track_id=?", (party["id"], t["id"]))
    db.commit()
    return cur.lastrowid


def end_play(play_id, reason):
    get_db().execute("UPDATE plays SET ended_at=?, end_reason=? WHERE id=? AND ended_at IS NULL", (_iso(), reason, play_id))
    get_db().commit()


def advance(party, reason, chosen_by="dj"):
    cur = current_play(party["id"])
    if cur:
        end_play(cur["id"], reason)
    ctx = party_context(party)
    cands = candidates(party, ctx)
    if not cands:
        return None
    start_play(party, cands[0], chosen_by)
    return cands[0]


def tick(party):
    """Advance the party by one poll. Returns a list of things that happened."""
    events = []
    cfg = current_app.config
    cur = current_play(party["id"])

    # Parties can outlive a server restart and the configured playback source
    # can change (for example, demo -> YouTube). Never leave an old mock or
    # Spotify row stuck as "now playing" under the new player.
    if cur:
        current_track = get_track(cur["track_id"])
        if not track_playable(party, current_track):
            picked = advance(party, "source_changed") if party["auto_dj"] else None
            if not party["auto_dj"]:
                end_play(cur["id"], "source_changed")
            if picked:
                events.append({"type": "started", "track": picked["track"]["title"]})
            return events

    if cur:
        up, down = play_votes(cur["id"])
        if dj.should_skip(up, down, cfg["SKIP_MIN_VOTES"], cfg["SKIP_NET_RATIO"]):
            picked = advance(party, "skipped_by_crowd")
            events.append({"type": "crowd_skip", "up": up, "down": down, "next": picked["track"]["title"] if picked else None})
            return events

    if spotify_live(party):
        if cur is None:
            # Nothing recorded as playing -> always try to start. This is the
            # one case where we must ask Spotify's live state, since we have
            # no local clock to reason from yet.
            toks, changed = _sp().ensure_fresh(tokens_for(party))
            if changed:
                save_tokens(party["id"], toks)
            if party["auto_dj"]:
                try:
                    picked = advance(party, "start")
                except SpotifyError as e:
                    return [{"type": "spotify_error", "detail": str(e)}]
                if picked:
                    events.append({"type": "started", "track": picked["track"]["title"]})
                else:
                    events.append({"type": "no_candidates"})
            return events

        # A track is already recorded as playing. Rather than call Spotify's
        # /me/player on every single poll (every ~3s per open host screen —
        # by far the largest source of API calls in practice), reason from
        # our own clock first. We started this track at `cur['started_at']`
        # and know its duration; only fall through to an actual Spotify call
        # when we're near the end (to catch skip/finish precisely) or on a
        # slower heartbeat (to catch a host manually changing the song).
        t = get_track(cur["track_id"])
        local_elapsed = elapsed_ms(cur)
        near_end = t and (t["duration_ms"] - local_elapsed) < 4000
        # Heartbeat bucket derived from this play's own elapsed time, not
        # shared wall-clock -- deterministic per track (doesn't jitter
        # against poll timing) and staggered across parties (doesn't cause
        # every party's heartbeat to land in the same instant).
        heartbeat_due = (local_elapsed // 1000) % SPOTIFY_POLL_HEARTBEAT_S < 3
        if not near_end and not heartbeat_due:
            return events  # trust the local clock this tick; no API call

        toks, changed = _sp().ensure_fresh(tokens_for(party))
        if changed:
            save_tokens(party["id"], toks)
        try:
            st = _sp().state(toks)
        except SpotifyError as e:
            return [{"type": "spotify_error", "detail": str(e)}]
        if st is None:

            # Host closed Spotify or nothing active; consider the song over.
            if elapsed_ms(cur) > 30000:
                end_play(cur["id"], "finished")
            return events
        if st["track_id"] != cur["track_id"]:
            # Host manually changed the song on their phone — respect it and log it.
            end_play(cur["id"], "replaced")
            t = get_track(st["track_id"])
            if not t:
                try:
                    t = _sp().get_track(toks, st["track_id"])
                    upsert_track(t); get_db().commit()
                except SpotifyError:
                    t = None
            if t:
                get_db().execute("INSERT INTO plays (party_id, track_id, chosen_by) VALUES (?,?,?)", (party["id"], t["id"], "host"))
                get_db().commit()
                events.append({"type": "host_override", "track": t["title"]})
            return events
        if st["duration_ms"] - st["progress_ms"] < 2500 or (not st["is_playing"] and st["progress_ms"] == 0):
            picked = advance(party, "finished")
            if picked:
                events.append({"type": "advanced", "track": picked["track"]["title"]})
        return events

    # The browser's official YouTube player is authoritative for completion.
    # It calls the host/player-ended endpoint when the IFrame FINISH event
    # fires, so guest polling must never silently advance the track here.
    if youtube_live(party):
        if cur is None and party["auto_dj"]:
            try:
                picked = advance(party, "start")
            except YouTubeError as e:
                return [{"type": "youtube_error", "detail": str(e)}]
            if picked:
                events.append({"type": "started", "track": picked["track"]["title"]})
            else:
                events.append({"type": "no_candidates"})
        return events

    # ---- mock player -------------------------------------------------------
    if cur is None:
        if party["auto_dj"]:
            picked = advance(party, "start")
            if picked:
                events.append({"type": "started", "track": picked["track"]["title"]})
        return events
    t = get_track(cur["track_id"])
    if t and elapsed_ms(cur) >= t["duration_ms"]:
        picked = advance(party, "finished")
        if picked:
            events.append({"type": "advanced", "track": picked["track"]["title"]})
    return events


# ---------------------------------------------------------------- views for the API
def now_playing(party, guest_id=None):
    cur = current_play(party["id"])
    if not cur:
        return None
    t = get_track(cur["track_id"])
    up, down = play_votes(cur["id"])
    my = None
    if guest_id:
        r = get_db().execute("SELECT value FROM play_votes WHERE play_id=? AND guest_id=?", (cur["id"], guest_id)).fetchone()
        my = r["value"] if r else None
    el = elapsed_ms(cur)
    if spotify_live(party):
        # In live mode use Spotify's own progress when we have it (cheap: reuse last tick's state is out of scope; approximate).
        pass
    ex = json.loads(cur["explanation"] or "{}")
    return {"play_id": cur["id"], "track": t, "up": up, "down": down, "my_vote": my, "elapsed_ms": min(el, t["duration_ms"]),
            "chosen_by": cur["chosen_by"], "explanation": ex.get("text"), "components": ex.get("components"),
            "skip_threshold": {"min_votes": current_app.config["SKIP_MIN_VOTES"], "net_ratio": current_app.config["SKIP_NET_RATIO"]}}


def queue_view(party, ctx, guest_id=None, limit=8):
    cands = candidates(party, ctx)[:limit]
    my = {}
    if guest_id:
        for r in get_db().execute("SELECT track_id, value FROM suggestion_votes WHERE party_id=? AND guest_id=?", (party["id"], guest_id)):
            my[r["track_id"]] = r["value"]
    for c in cands:
        c["my_vote"] = my.get(c["track"]["id"])
    return cands


def themes_view(party, guest_id=None):
    rows = get_db().execute(
        """SELECT t.*, g.nickname, COALESCE(SUM(v.value),0) AS net, COUNT(v.guest_id) AS n_votes
           FROM themes t JOIN guests g ON g.id=t.guest_id LEFT JOIN theme_votes v ON v.theme_id=t.id
           WHERE t.party_id=? GROUP BY t.id ORDER BY net DESC, t.id ASC""", (party["id"],)).fetchall()
    my = {}
    if guest_id:
        for r in get_db().execute("SELECT theme_id, value FROM theme_votes WHERE guest_id=?", (guest_id,)):
            my[r["theme_id"]] = r["value"]
    out = []
    for r in rows:
        d = _theme_dict(r)
        d["my_vote"] = my.get(r["id"])
        d["pinned"] = (party["pinned_theme_id"] == r["id"])
        out.append(d)
    return out


def history_view(party, limit=15):
    rows = get_db().execute(
        """SELECT p.*, t.title, t.artist, t.art_url FROM plays p JOIN tracks t ON t.id=p.track_id
           WHERE p.party_id=? AND p.ended_at IS NOT NULL ORDER BY p.id DESC LIMIT ?""", (party["id"], limit)).fetchall()
    out = []
    for r in rows:
        up, down = play_votes(r["id"])
        out.append({"title": r["title"], "artist": r["artist"], "art_url": r["art_url"], "up": up, "down": down,
                    "end_reason": r["end_reason"], "chosen_by": r["chosen_by"], "started_at": r["started_at"]})
    return out


def stats(party):
    db = get_db()
    g = db.execute("SELECT COUNT(*) AS n FROM guests WHERE party_id=? AND last_seen > datetime('now','-10 minutes')", (party["id"],)).fetchone()["n"]
    s = db.execute("SELECT COUNT(*) AS n FROM suggestions WHERE party_id=?", (party["id"],)).fetchone()["n"]
    v = db.execute("SELECT COUNT(*) AS n FROM play_votes pv JOIN plays p ON p.id=pv.play_id WHERE p.party_id=?", (party["id"],)).fetchone()["n"]
    p = db.execute("SELECT COUNT(*) AS n FROM plays WHERE party_id=? AND ended_at IS NOT NULL", (party["id"],)).fetchone()["n"]
    return {"guests_active": g, "open_requests": s, "votes_cast": v, "songs_played": p}
