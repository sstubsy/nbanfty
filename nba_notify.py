#!/usr/bin/env python3
"""NBA score notifier.

Modes:
  python nba_notify.py            notify once per newly finished game (uses state file)
  python nba_notify.py --digest   one notification with all of today's finals + top performers
  python nba_notify.py --dry-run  print instead of sending (combine with either mode)

Env:
  NTFY_TOPIC   required unless --dry-run. Pick something unguessable.
  NTFY_SERVER  optional, defaults to https://ntfy.sh
  TZ_NAME      optional, defaults to America/Los_Angeles
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
STATE = Path(__file__).with_name("notified.json")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nba-notify/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def todays_games(tz):
    day = datetime.now(ZoneInfo(tz)).strftime("%Y%m%d")
    data = get(f"{BASE}/scoreboard?dates={day}")
    games = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        games.append({
            "id": ev["id"],
            "final": ev["status"]["type"]["completed"],
            "live": ev["status"]["type"]["state"] == "in",
            "detail": ev["status"]["type"].get("shortDetail", ""),
            "away": teams["away"]["team"]["abbreviation"],
            "home": teams["home"]["team"]["abbreviation"],
            "away_score": int(teams["away"].get("score") or 0),
            "home_score": int(teams["home"].get("score") or 0),
        })
    return games


def score_line(g):
    return f"{g['away']} {g['away_score']}, {g['home']} {g['home_score']}"


def top_performers(game_id, n=2):
    """Rank players in one game by a rough impact score, return short lines."""
    data = get(f"{BASE}/summary?event={game_id}")
    out = []
    for team in data.get("boxscore", {}).get("players", []):
        abbr = team["team"]["abbreviation"]
        for block in team.get("statistics", []):
            labels = block.get("labels", [])
            for a in block.get("athletes", []):
                row = dict(zip(labels, a.get("stats", [])))

                def num(k):
                    try:
                        return int(str(row.get(k, 0)).split("-")[0])
                    except ValueError:
                        return 0

                pts, reb, ast = num("PTS"), num("REB"), num("AST")
                stl, blk, to = num("STL"), num("BLK"), num("TO")
                impact = pts + 1.2 * reb + 1.5 * ast + 3 * stl + 3 * blk - to
                out.append((impact, f"{a['athlete']['displayName']} ({abbr}) "
                                    f"{pts}p {reb}r {ast}a" +
                                    (f" {blk}b" if blk >= 3 else "") +
                                    (f" {stl}s" if stl >= 3 else "")))
    out.sort(reverse=True)
    return [line for _, line in out[:n]]


def send(title, body, dry):
    if dry:
        print(f"[{title}]\n{body}\n")
        return
    topic = os.environ["NTFY_TOPIC"]
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    req = urllib.request.Request(
        f"{server}/{topic}", data=body.encode("utf-8"),
        headers={"Title": title, "Tags": "basketball"}, method="POST")
    urllib.request.urlopen(req, timeout=20).read()


def load_state():
    try:
        return set(json.loads(STATE.read_text()))
    except (FileNotFoundError, ValueError):
        return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    tz = os.environ.get("TZ_NAME", "America/Los_Angeles")

    if not args.dry_run and not os.environ.get("NTFY_TOPIC"):
        sys.exit("Set NTFY_TOPIC (or use --dry-run).")

    games = todays_games(tz)
    finals = [g for g in games if g["final"]]

    if args.digest:
        if not finals:
            print("No finished games yet.")
            return
        lines = [score_line(g) for g in finals]
        best = []
        for g in finals:
            try:
                best += top_performers(g["id"], 1)
            except Exception as e:  # box score missing for a game is not fatal
                print(f"skip box score {g['id']}: {e}", file=sys.stderr)
        body = "\n".join(lines)
        if best:
            body += "\n\nTop performers\n" + "\n".join(best)
        send(f"NBA finals ({len(finals)} games)", body, args.dry_run)
        return

    seen = load_state()
    for g in finals:
        if g["id"] in seen:
            continue
        body = score_line(g)
        try:
            body += "\n" + "\n".join(top_performers(g["id"], 2))
        except Exception as e:
            print(f"skip box score {g['id']}: {e}", file=sys.stderr)
        send("Final: " + score_line(g), body, args.dry_run)
        seen.add(g["id"])
    if not args.dry_run:
        STATE.write_text(json.dumps(sorted(seen)))


if __name__ == "__main__":
    main()
