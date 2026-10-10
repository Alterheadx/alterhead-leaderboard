#!/usr/bin/env python3
"""Overall ranking for PigPonyPocalypse (Steam app 4385110).

Per boss: 100 * (dps / top_dps) ** 1.75, summed over the four bosses of a difficulty.
Stored on Steam as points * 100.

    STEAM_PUBLISHER_KEY=... python3 recompute.py [--dry-run] [--verbose]
"""
import json
import os
import struct
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

APPID = 4385110
API = "https://partner.steam-api.com/ISteamLeaderboards"
EXPONENT = 1.75
MAX_POINTS = 100.0   # for #1 on a boss
SCALE = 100          # steam score = points * 100
DETAILS_FORMAT = 1   # details: format, dps x4, points*100 x4, unix time
PAGE = 1000
RETRIES = 4

# in play order
BOSSES = ["ArcaneCorruptedPig", "SwyingSwyan", "BoarSentinel", "SwineKing"]
DIFFICULTIES = {"Normal": 1, "Hard": 2}


def boss_board(index, difficulty):
    return f"BestDPS_{index}.{DIFFICULTIES[difficulty]}_{BOSSES[index - 1]}_{difficulty}"


def overall_board(difficulty):
    return f"Overall_5.{DIFFICULTIES[difficulty]}_{difficulty}"


KEY = os.environ.get("STEAM_PUBLISHER_KEY", "").strip()
DRY = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def multipart(fields, blobs):
    """multipart body; rawbinary params (details) have to go as raw bytes."""
    boundary = "----ppp" + uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    for name, data in blobs.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{name}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def call(method, params, post=False, blobs=None):
    """Web API call with retries on network errors and 5xx."""
    params = dict(params, key=KEY, appid=APPID)
    url = f"{API}/{method}/"
    last = None
    for attempt in range(RETRIES):
        try:
            if blobs:
                body, content_type = multipart(params, blobs)
                req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
            elif post:
                req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode())
            else:
                req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params))
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            last = f"HTTP {e.code} {method}: {body}"
            if e.code < 500:
                raise RuntimeError(last)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = f"{method}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Web API failed after {RETRIES} attempts: {last}")


def board_ids():
    data = call("GetLeaderboardsForGame/v2", {})
    return {b["name"]: int(b["id"]) for b in data["response"]["leaderboards"]}


def entries(board_id):
    """steamid -> (score, details hex) for the whole board."""
    result = {}
    start = 1
    total = None
    while True:
        data = call("GetLeaderboardEntries/v1", {
            "leaderboardid": board_id, "datarequest": "RequestGlobal",
            "rangestart": start, "rangeend": start + PAGE - 1,
        })
        info = data["leaderboardEntryInformation"]
        total = int(info.get("totalLeaderBoardEntryCount", 0))
        page = info.get("leaderboardEntries") or []
        for e in page:
            result[str(e["steamID"])] = (int(e["score"]), e.get("detailData") or "")
        if not page or len(result) >= total:
            break
        start += PAGE
    if total and len(result) != total:
        log(f"  ! board {board_id}: expected {total} entries, read {len(result)}")
    return result


def points(dps, top):
    if top <= 0 or dps <= 0:
        return 0.0
    return MAX_POINTS * (min(dps, top) / top) ** EXPONENT


def compute(difficulty, ids):
    """steamid -> (score, dps[4], points[4]) for one difficulty."""
    per_boss = []
    for i in range(1, 5):
        name = boss_board(i, difficulty)
        if name not in ids:
            raise RuntimeError(f"board {name} not found")
        board = entries(ids[name])
        top = max((s for s, _ in board.values()), default=0)
        per_boss.append((board, top))
        log(f"  {name}: {len(board)} entries, top {top}")

    players = {}
    for i, (board, top) in enumerate(per_boss):
        for steamid, (score, _) in board.items():
            dps, pts = players.setdefault(steamid, ([0, 0, 0, 0], [0.0, 0.0, 0.0, 0.0]))
            dps[i] = score
            pts[i] = points(score, top)
    result = {}
    for steamid, (dps, pts) in players.items():
        result[steamid] = (int(round(sum(pts) * SCALE)), dps, pts)
    return result


def details_bytes(dps, pts):
    values = [DETAILS_FORMAT] + list(dps) + [int(round(p * SCALE)) for p in pts] + [int(time.time())]
    return struct.pack("<%di" % len(values), *values)


def details_valid(detail_hex):
    """True if details start with our format number."""
    try:
        raw = bytes.fromhex(detail_hex or "")
    except ValueError:
        return False
    return len(raw) >= 4 and struct.unpack("<i", raw[:4])[0] == DETAILS_FORMAT


def set_score(board_id, steamid, score, details):
    base = {"leaderboardid": board_id, "steamid": steamid, "score": score, "scoremethod": "ForceUpdate"}
    try:
        data = call("SetLeaderboardScore/v1", base, blobs={"details": details})
    except RuntimeError as e:
        # fall back to writing the score without details
        log(f"  ! details rejected ({e}), writing without them")
        data = call("SetLeaderboardScore/v1", base, post=True)
    res = data.get("result", {})
    if int(res.get("result", 0)) != 1:
        raise RuntimeError(f"SetLeaderboardScore {steamid}: {json.dumps(data)[:300]}")


def delete_score(board_id, steamid):
    call("DeleteLeaderboardScore/v1", {"leaderboardid": board_id, "steamid": steamid}, post=True)


def sync(difficulty, ids):
    log(f"== {difficulty}")
    new = compute(difficulty, ids)
    name = overall_board(difficulty)
    if name not in ids:
        raise RuntimeError(f"board {name} not found")
    board_id = ids[name]
    old = entries(board_id)

    changed = [(sid, s) for sid, (s, _, _) in new.items()
               if old.get(sid, (None, ""))[0] != s or not details_valid(old.get(sid, (None, ""))[1])]
    stale = [sid for sid in old if sid not in new]
    log(f"  {name}: was {len(old)}, now {len(new)}, update {len(changed)}, remove {len(stale)}")

    for steamid, score in sorted(changed, key=lambda x: -x[1]):
        _, dps, pts = new[steamid]
        if VERBOSE or DRY:
            log(f"  {'[dry] ' if DRY else ''}{steamid}: {score / SCALE:.2f} points  dps {dps}  per boss {[round(p, 1) for p in pts]}")
        if not DRY:
            set_score(board_id, steamid, score, details_bytes(dps, pts))
    for steamid in stale:
        log(f"  {'[dry] ' if DRY else ''}remove {steamid}: no boss entries left")
        if not DRY:
            delete_score(board_id, steamid)
    return len(changed) + len(stale)


def main():
    if not KEY:
        print("STEAM_PUBLISHER_KEY is not set", file=sys.stderr)
        return 2
    ids = board_ids()
    total = 0
    for difficulty in DIFFICULTIES:
        total += sync(difficulty, ids)
    log(f"done: {total} changes{' (dry run)' if DRY else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
