#!/usr/bin/env python3
"""
Проверява сайта на ЕРМ Запад за текущи прекъсвания в дадена община
и записва всяко прекъсване (по трафопост) в data/outages.csv.

Пускай го на всеки ~30 минути (GitHub Actions, cron или Task Scheduler).
"""
import csv
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# ---- Настройки --------------------------------------------------------------
OBSTINA = os.environ.get("ERM_OBSTINA", "VID16")      # община Димово
BASE = "https://info.ermzapad.bg/webint/vok/avplan.php"
TZ = ZoneInfo("Europe/Sofia")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUTAGES = os.path.join(DATA, "outages.csv")
RUNS = os.path.join(DATA, "runs.csv")

FIELDS = ["key", "ceo", "city", "type", "begin", "expected_end",
          "first_seen", "last_seen", "gone_at", "status"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def now_str():
    return datetime.now(TZ).strftime("%d.%m.%Y %H:%M")


def fetch(obstina=OBSTINA):
    """Връща (status, records). status: ok | empty | blocked | limit | error:..."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "bg,en;q=0.8"})
    try:
        # 1) отваряме страницата, за да получим бисквитките на защитата (Incapsula)
        s.get(BASE, timeout=30)
        # 2) същата заявка, която прави картата на сайта
        r = s.post(BASE, timeout=60,
                   data={"action": "draw", "gm_obstina": obstina, "lat": 0, "lon": 0},
                   headers={"X-Requested-With": "XMLHttpRequest",
                            "Referer": BASE, "Origin": "https://info.ermzapad.bg"})
    except requests.RequestException as e:
        return f"error:{type(e).__name__}", []
    return parse(r.status_code, r.text)


def parse(code, text):
    body = (text or "").strip()
    if code != 200:
        return f"error:http{code}", []
    if body == "tomuch":
        return "limit", []
    if not body or body in ("[]", "{}", "null"):
        return "empty", []
    if body.startswith("<"):
        # HTML вместо JSON: защитата е блокирала заявката (или общината няма събития)
        low = body.lower()
        if "incapsula" in low or "_incapsula_resource" in low or "captcha" in low:
            return "blocked", []
        return "empty", []
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return "error:badjson", []
    items = obj.values() if isinstance(obj, dict) else obj
    recs = []
    for v in items:
        if not isinstance(v, dict) or "begin_event" not in v:
            continue
        recs.append({
            "ceo": (v.get("ceo") or "").strip(),
            "city": (v.get("city_name") or "").strip(),
            "type": (v.get("typedist") or "").strip(),
            "begin": (v.get("begin_event") or "").strip(),
            "expected_end": (v.get("end_event") or "").strip(),
        })
    return ("ok" if recs else "empty"), recs


def load():
    if not os.path.exists(OUTAGES):
        return {}
    with open(OUTAGES, encoding="utf-8", newline="") as f:
        return {row["key"]: row for row in csv.DictReader(f)}


def save(rows):
    os.makedirs(DATA, exist_ok=True)
    def sortkey(r):
        try:
            return datetime.strptime(r["begin"], "%d.%m.%Y %H:%M")
        except ValueError:
            return datetime.min
    with open(OUTAGES, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=sortkey):
            w.writerow({k: r.get(k, "") for k in FIELDS})


def log_run(status, n):
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(RUNS)
    with open(RUNS, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["checked_at", "status", "records"])
        w.writerow([now_str(), status, n])


def update(rows, status, recs, ts):
    """Обновява състоянието. Затваря само ако проверката е успешна."""
    if status not in ("ok", "empty"):
        return rows  # не знаем нищо — не пипаме отворените записи
    seen = set()
    for r in recs:
        key = f'{r["ceo"]}|{r["begin"]}'
        seen.add(key)
        row = rows.get(key)
        if row is None:
            row = {**r, "key": key, "first_seen": ts, "gone_at": ""}
            rows[key] = row
        row.update(expected_end=r["expected_end"], type=r["type"],
                   city=r["city"], last_seen=ts, status="open", gone_at="")
    for key, row in rows.items():
        if row.get("status") == "open" and key not in seen:
            row["status"] = "closed"
            row["gone_at"] = ts
    return rows


def main():
    ts = now_str()
    status, recs = fetch()
    rows = update(load(), status, recs, ts)
    save(rows)
    log_run(status, len(recs))
    print(f"{ts}  {status}  записи: {len(recs)}")
    if status == "blocked":
        print("Сайтът блокира заявката (защита от ботове). Виж README.", file=sys.stderr)


if __name__ == "__main__":
    main()
