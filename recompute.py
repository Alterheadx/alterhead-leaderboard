#!/usr/bin/env python3
"""Общий зачёт PigPonyPocalypse (Steam AppId 4385110).

За каждого босса игрок получает 100 × (его ДПС / ДПС первого места)^1.5 очков, общий счёт — сумма по четырём боссам
сложности (максимум 400). В Steam счёт хранится целым: очки × 100. Таблицы Overall_5.1_Normal и Overall_5.2_Hard
созданы с onlytrustedwrites — писать в них может только этот скрипт ключом издателя (STEAM_PUBLISHER_KEY).

Запуск: STEAM_PUBLISHER_KEY=... python3 recompute.py [--dry-run] [--verbose]
  --dry-run — только читает таблицы и печатает, что бы записал.
"""
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APPID = 4385110
API = "https://partner.steam-api.com/ISteamLeaderboards"
EXPONENT = 1.5
MAX_POINTS = 100.0   # за первое место на боссе
SCALE = 100          # счёт в Steam = очки × 100
DETAILS_FORMAT = 1   # details записи: формат, ДПС по 4 боссам, очки × 100 по 4 боссам, время расчёта (unix)
PAGE = 1000
RETRIES = 4

# порядок прохождения; имя таблицы босса — как DPSLeaderbordManager.SteamLeaderboardName в игре
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


def call(method, params, post=False):
    """запрос к Web API с повторами на сетевые ошибки и 5xx; ключ в ответах об ошибках не печатается."""
    params = dict(params, key=KEY, appid=APPID)
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{method}/"
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, data=data) if post else urllib.request.Request(url + "?" + data.decode())
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
    raise RuntimeError(f"Web API не ответил после {RETRIES} попыток: {last}")


def board_ids():
    data = call("GetLeaderboardsForGame/v2", {})
    return {b["name"]: int(b["id"]) for b in data["response"]["leaderboards"]}


def entries(board_id):
    """все записи таблицы: steamid → (score, details hex)."""
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
        log(f"  ! таблица {board_id}: ожидалось {total} записей, прочитано {len(result)}")
    return result


def points(dps, top):
    if top <= 0 or dps <= 0:
        return 0.0
    return MAX_POINTS * (min(dps, top) / top) ** EXPONENT


def compute(difficulty, ids):
    """очки игроков сложности: steamid → (score, dps[4], points[4])."""
    per_boss = []
    for i in range(1, 5):
        name = boss_board(i, difficulty)
        if name not in ids:
            raise RuntimeError(f"нет таблицы {name}")
        board = entries(ids[name])
        top = max((s for s, _ in board.values()), default=0)
        per_boss.append((board, top))
        log(f"  {name}: {len(board)} записей, лидер {top}")

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


def details_hex(dps, pts):
    values = [DETAILS_FORMAT] + list(dps) + [int(round(p * SCALE)) for p in pts] + [int(time.time())]
    return struct.pack("<%di" % len(values), *values).hex()


def set_score(board_id, steamid, score, details):
    base = {"leaderboardid": board_id, "steamid": steamid, "score": score, "scoremethod": "ForceUpdate"}
    try:
        data = call("SetLeaderboardScore/v1", dict(base, details=details), post=True)
    except RuntimeError as e:
        # details в неожиданном формате — запись без них важнее
        log(f"  ! details отклонены ({e}), пишу без них")
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
        raise RuntimeError(f"нет таблицы {name}")
    board_id = ids[name]
    old = entries(board_id)

    changed = [(sid, s) for sid, (s, _, _) in new.items() if old.get(sid, (None, ""))[0] != s]
    stale = [sid for sid in old if sid not in new]
    log(f"  {name}: было {len(old)}, стало {len(new)}, изменить {len(changed)}, удалить {len(stale)}")

    for steamid, score in sorted(changed, key=lambda x: -x[1]):
        _, dps, pts = new[steamid]
        if VERBOSE or DRY:
            log(f"  {'[dry] ' if DRY else ''}{steamid}: {score / SCALE:.2f} очков  дпс {dps}  очки {[round(p, 1) for p in pts]}")
        if not DRY:
            set_score(board_id, steamid, score, details_hex(dps, pts))
    for steamid in stale:
        log(f"  {'[dry] ' if DRY else ''}удалить {steamid}: записей по боссам больше нет")
        if not DRY:
            delete_score(board_id, steamid)
    return len(changed) + len(stale)


def main():
    if not KEY:
        print("нет STEAM_PUBLISHER_KEY", file=sys.stderr)
        return 2
    ids = board_ids()
    total = 0
    for difficulty in DIFFICULTIES:
        total += sync(difficulty, ids)
    log(f"готово: {total} правок{' (dry run)' if DRY else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
