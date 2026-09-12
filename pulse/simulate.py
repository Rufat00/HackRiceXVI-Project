"""Fake a crowd so one person can demo a full room.

    python simulate.py ABCD            # party code from the host screen
    python simulate.py ABCD --guests 12 --seconds 120 --base http://127.0.0.1:5000

Guests join, request songs (with a shared taste so a favorite emerges), vote
on what's playing based on their taste vs the track's genres, and pitch/vote
themes. Ctrl+C to stop."""
import argparse
import random
import time

import requests

TASTES = [["latin", "reggaeton", "afrobeats"], ["hip hop", "trap"], ["pop", "dance pop"], ["2000s"], ["chill", "r&b"], ["rock", "pop punk"]]
THEMES = ["latin night", "2000s throwback", "hip hop only", "chill vibes", "pop bangers"]
NAMES = ["Ana", "Ben", "Cy", "Dee", "Eli", "Fay", "Gus", "Hana", "Ivy", "Jae", "Kai", "Lou", "Mo", "Nia", "Oz", "Pia"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code"); ap.add_argument("--guests", type=int, default=8)
    ap.add_argument("--seconds", type=int, default=180); ap.add_argument("--base", default="http://127.0.0.1:5000")
    a = ap.parse_args()
    base = f"{a.base}/api/parties/{a.code.upper()}"
    guests = []
    for i in range(a.guests):
        s = requests.Session()
        r = s.post(f"{base}/join", json={"nickname": NAMES[i % len(NAMES)] + ("" if i < len(NAMES) else str(i))})
        r.raise_for_status()
        taste = random.choice(TASTES[: max(2, len(TASTES) - i // 3)])  # early guests skew toward a shared taste
        guests.append((s, taste))
        print("joined", r.json()["guest"]["nickname"], "likes", taste)
    # a couple of theme pitches
    for s, taste in guests[:3]:
        s.post(f"{base}/themes", json={"text": random.choice(THEMES)})
    seen_play = {}
    end = time.time() + a.seconds
    while time.time() < end:
        st = guests[0][0].get(f"{base}/state").json()
        np_ = st["now_playing"]
        for s, taste in guests:
            r = random.random()
            if r < 0.25:  # request something they like
                q = random.choice(taste)
                tr = s.get(f"{base}/search", params={"q": q}).json()["tracks"]
                if tr:
                    s.post(f"{base}/suggest", json={"track_id": random.choice(tr[:3])["id"]})
            elif r < 0.6 and np_ and seen_play.get(id(s)) != np_["play_id"]:
                seen_play[id(s)] = np_["play_id"]
                genres = set(np_["track"]["genres"]) | ({str(np_["track"]["year"] // 10 * 10) + "s"} if np_["track"]["year"] else set())
                like = bool(genres & set(taste)) or random.random() < 0.35
                s.post(f"{base}/vote", json={"value": 1 if like else -1})
            elif r < 0.7:
                th = st["themes"]
                if th:
                    t = random.choice(th[:3]); s.post(f"{base}/themes/{t['id']}/vote", json={"value": 1 if random.random() < 0.75 else -1})
            elif r < 0.8 and st["queue"]:
                q = random.choice(st["queue"][:4])
                if q["source"] == "crowd":
                    s.post(f"{base}/queue/vote", json={"track_id": q["track"]["id"], "value": 1 if set(q["track"]["genres"]) & set(taste) else -1})
        time.sleep(2.5)


if __name__ == "__main__":
    main()
