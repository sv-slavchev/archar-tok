#!/usr/bin/env python3
"""
Подготвя данните за страницата със статистика:
  docs/data.json  — всички прекъсвания, трафопостове с очертания, време, проверки
  docs/index.html — копие на site/index.html (самата страница)
  data/incidents.csv — обобщени прекъсвания за основното село (за Excel)
"""
import csv
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

CITY = os.environ.get("ERM_CITY", "АРЧАР")
OBSTINA_NAME = os.environ.get("ERM_OBSTINA_NAME", "Димово")
LAT, LON = 43.81, 22.91            # за данните за времето
MERGE_MIN = 20                     # прекъсвания, започнали до 20 мин едно след друго = едно събитие
TZ = ZoneInfo("Europe/Sofia")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DOCS = os.path.join(HERE, "docs")
SITE = os.path.join(HERE, "site", "index.html")
FMT = "%d.%m.%Y %H:%M"

GUST_KMH = 50      # пориви на вятъра, км/ч
RAIN_MM = 5        # валеж за прозореца, мм
SNOW = set(range(71, 78)) | {85, 86}
STORM = set(range(95, 100))


def dt(s):
    try:
        return datetime.strptime(s, FMT)
    except (TypeError, ValueError):
        return None


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M") if d else None


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- време (Open-Meteo, безплатно, без ключ) ---------------------------
WEATHER = os.path.join(DATA, "weather.json")


def update_weather():
    cache = read_json(WEATHER, {})
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
            "latitude": LAT, "longitude": LON, "past_days": 92, "forecast_days": 1,
            "hourly": "wind_gusts_10m,precipitation,weather_code,temperature_2m",
            "timezone": "Europe/Sofia"})
        r.raise_for_status()
        h = r.json()["hourly"]
        for i, t in enumerate(h["time"]):
            cache[t] = [h["wind_gusts_10m"][i], h["precipitation"][i],
                        h["weather_code"][i], h["temperature_2m"][i]]
        os.makedirs(DATA, exist_ok=True)
        with open(WEATHER, "w", encoding="utf-8") as f:
            json.dump(cache, f, separators=(",", ":"), sort_keys=True)
    except Exception as e:  # времето е допълнително — не спираме отчета
        print("Open-Meteo:", e)
    return cache


def weather_for(cache, start):
    """Времето от 2 часа преди до 1 час след началото."""
    t = start.replace(minute=0) - timedelta(hours=2)
    last = start + timedelta(hours=1)
    gust = rain = 0.0
    codes, temp, found = set(), None, False
    while t <= last:
        v = cache.get(t.strftime("%Y-%m-%dT%H:00"))
        if v and v[0] is not None:
            found = True
            gust = max(gust, v[0] or 0)
            rain += v[1] or 0
            codes.add(v[2])
            temp = v[3] if temp is None else temp
        t += timedelta(hours=1)
    if not found:
        return None
    storm, snow = bool(codes & STORM), bool(codes & SNOW)
    return {"gust": round(gust), "rain": round(rain, 1), "temp": temp,
            "storm": storm, "snow": snow,
            "bad": gust >= GUST_KMH or rain >= RAIN_MM or storm or snow}


# ---------- край на прекъсване --------------------------------------------------
def end_estimate(r, now):
    """Реалният край е между последното „видяно“ и първото „липсва“."""
    if r["status"] == "open":
        return now, None
    last, gone, exp = dt(r["last_seen"]), dt(r["gone_at"]), dt(r["expected_end"])
    if last and gone:
        gap = (gone - last).total_seconds() / 60
        if exp and last <= exp <= gone:
            return exp, gap
        return last + (gone - last) / 2, gap
    return gone or exp or last, None


def build_incidents(rows, now):
    by_city = {}
    for r in rows:
        if dt(r["begin"]):
            by_city.setdefault(r["city"], []).append(r)
    incidents = []
    for city, rs in by_city.items():
        rs.sort(key=lambda r: dt(r["begin"]))
        groups = []
        for r in rs:
            b = dt(r["begin"])
            if groups and b - groups[-1]["begin"] <= timedelta(minutes=MERGE_MIN) \
                    and r["type"] == groups[-1]["type"]:
                groups[-1]["rows"].append(r)
            else:
                groups.append({"begin": b, "type": r["type"], "rows": [r]})
        for g in groups:
            ends, gaps = [], []
            ongoing = any(r["status"] == "open" for r in g["rows"])
            for r in g["rows"]:
                e, gap = end_estimate(r, now)
                if e:
                    ends.append(e)
                if gap is not None:
                    gaps.append(gap)
            end = now if ongoing else max(ends)
            exp = max((dt(r["expected_end"]) for r in g["rows"] if dt(r["expected_end"])), default=None)
            exp0 = max((dt(r.get("first_expected_end") or r["expected_end"]) for r in g["rows"]
                        if dt(r.get("first_expected_end") or r["expected_end"])), default=None)
            incidents.append({
                "city": city,
                "b": g["begin"], "e": end, "ongoing": ongoing,
                "hours": round(max((end - g["begin"]).total_seconds() / 3600, 0), 2),
                "planned": g["type"].lower().startswith("планир"),
                "type": g["type"] or "?",
                "exp": exp, "exp0": exp0,
                "prec": round(max(gaps) / 2) if gaps else None,   # точност на края, ± мин
                "tps": sorted({r["ceo"] for r in g["rows"]}),
            })
    incidents.sort(key=lambda i: i["b"])
    return incidents


def main():
    now = datetime.now(TZ).replace(tzinfo=None, second=0, microsecond=0)
    rows = read_csv(os.path.join(DATA, "outages.csv"))
    runs = read_csv(os.path.join(DATA, "runs.csv"))
    tps = read_json(os.path.join(DATA, "tps.json"), {})
    cache = update_weather()
    incidents = build_incidents(rows, now)

    # трафопостове, видени в записите, но без очертание (напр. стари записи)
    for r in rows:
        if r["ceo"] and r["ceo"] not in tps:
            tps[r["ceo"]] = {"city": r["city"], "lat": None, "lon": None, "hull": []}

    out_inc = []
    for i in incidents:
        w = weather_for(cache, i["b"])
        delay = None
        if not i["ongoing"] and i["exp0"]:
            delay = round((i["e"] - i["exp0"]).total_seconds() / 60)
        out_inc.append({
            "city": i["city"], "b": iso(i["b"]), "e": iso(i["e"]), "ongoing": i["ongoing"],
            "h": i["hours"], "planned": i["planned"], "exp": iso(i["exp"]), "exp0": iso(i["exp0"]),
            "delay": delay, "prec": i["prec"], "tps": i["tps"], "w": w,
        })

    ok_runs = [r for r in runs if r["status"] in ("ok", "empty")]
    per_day = Counter(dt(r["checked_at"]).strftime("%Y-%m-%d") for r in ok_runs if dt(r["checked_at"]))
    last = runs[-1] if runs else None
    streets = read_json(os.path.join(DATA, "tps_streets.json"), {})
    for c, names in streets.items():
        if c in tps:
            tps[c] = {**tps[c], "streets": names}
    village = read_json(os.path.join(DATA, "village.json"), {})
    data = {
        "generated": iso(now),
        "outline": village.get("outline"),
        "city": CITY, "obstina": OBSTINA_NAME,
        "first_check": iso(dt(runs[0]["checked_at"])) if runs else None,
        "last_check": {"at": iso(dt(last["checked_at"])), "status": last["status"]} if last else None,
        "checks": {"total": len(runs), "ok": len(ok_runs), "per_day": per_day},
        "thresholds": {"gust": GUST_KMH, "rain": RAIN_MM},
        "tps": tps,
        "incidents": out_inc,
    }
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    if os.path.exists(SITE):
        shutil.copyfile(SITE, os.path.join(DOCS, "index.html"))

    # CSV за Excel — само основното село
    with open(os.path.join(DATA, "incidents.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["начало", "край (оценка)", "продължава", "часове", "вид", "брой ТП",
                    "трафопостове", "обявен край", "закъснение мин", "пориви км/ч", "валеж мм", "лошо време"])
        for i in out_inc:
            if CITY not in i["city"].upper():
                continue
            wt = i["w"] or {}
            w.writerow([i["b"].replace("T", " "), i["e"].replace("T", " "), "да" if i["ongoing"] else "",
                        i["h"], "планирано" if i["planned"] else "непланирано", len(i["tps"]),
                        " ".join(i["tps"]), (i["exp0"] or "").replace("T", " "),
                        "" if i["delay"] is None else i["delay"],
                        wt.get("gust", ""), wt.get("rain", ""),
                        {True: "да", False: "не"}.get(wt.get("bad"), "")])
    n = sum(1 for i in out_inc if CITY in i["city"].upper())
    print(f"{n} прекъсвания в {CITY} ({len(out_inc)} в общината); docs/data.json е обновен")


if __name__ == "__main__":
    main()
