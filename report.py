#!/usr/bin/env python3
"""
Прави статистика от data/outages.csv за едно населено място
и генерира docs/index.html (страница с графики) и data/incidents.csv.
"""
import csv
import html
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

CITY = os.environ.get("ERM_CITY", "АРЧАР")
LAT, LON = 43.81, 22.91            # Арчар — за данните за времето
MERGE_MIN = 20                     # прекъсвания, започнали до 20 мин едно след друго = едно събитие
TZ = ZoneInfo("Europe/Sofia")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT_HTML = os.path.join(HERE, "docs", "index.html")
FMT = "%d.%m.%Y %H:%M"

# Праг за „лошо време“ около момента на прекъсването
GUST_KMH = 50      # пориви на вятъра, км/ч
RAIN_MM = 5        # валеж за прозореца, мм
BAD_CODES = set(range(71, 78)) | set(range(85, 87)) | set(range(95, 100))  # сняг, гръмотевици


def dt(s):
    try:
        return datetime.strptime(s, FMT)
    except (TypeError, ValueError):
        return None


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------- време (Open-Meteo, безплатно, без ключ) ---------------------------
WEATHER = os.path.join(DATA, "weather.json")


def update_weather():
    cache = {}
    if os.path.exists(WEATHER):
        with open(WEATHER, encoding="utf-8") as f:
            cache = json.load(f)
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
        with open(WEATHER, "w", encoding="utf-8") as f:
            json.dump(cache, f, separators=(",", ":"), sort_keys=True)
    except Exception as e:  # времето е допълнително — не спираме отчета
        print("Open-Meteo:", e)
    return cache


def weather_for(cache, start, end):
    """Обобщава времето от 2 часа преди до 1 час след началото."""
    t = start.replace(minute=0) - timedelta(hours=2)
    last = start + timedelta(hours=1)
    gust = rain = 0.0
    codes, found = set(), False
    while t <= last:
        v = cache.get(t.strftime("%Y-%m-%dT%H:00"))
        if v and v[0] is not None:
            found = True
            gust = max(gust, v[0] or 0)
            rain += v[1] or 0
            codes.add(v[2])
        t += timedelta(hours=1)
    if not found:
        return None
    bad = gust >= GUST_KMH or rain >= RAIN_MM or bool(codes & BAD_CODES)
    return {"gust": round(gust), "rain": round(rain, 1), "bad": bad,
            "storm": bool(codes & set(range(95, 100))),
            "snow": bool(codes & (set(range(71, 78)) | {85, 86}))}


# ---------- събития -----------------------------------------------------------
def build_incidents(rows, now):
    mine = [r for r in rows if CITY in r["city"].upper() and dt(r["begin"])]
    mine.sort(key=lambda r: dt(r["begin"]))
    groups = []
    for r in mine:
        b = dt(r["begin"])
        if groups and b - groups[-1]["begin"] <= timedelta(minutes=MERGE_MIN) \
                and r["type"] == groups[-1]["type"]:
            groups[-1]["rows"].append(r)
        else:
            groups.append({"begin": b, "type": r["type"], "rows": [r]})

    incidents = []
    for g in groups:
        ends, ongoing = [], False
        for r in g["rows"]:
            if r["status"] == "open":
                ongoing = True
                continue
            last, gone, exp = dt(r["last_seen"]), dt(r["gone_at"]), dt(r["expected_end"])
            # Реалният край е между последното „видяно“ и първото „липсва“.
            if exp and last and gone and last <= exp <= gone:
                ends.append(exp)
            elif last and gone:
                ends.append(last + (gone - last) / 2)
            else:
                ends.append(gone or exp or last)
        end = now if ongoing else max(e for e in ends if e)
        exp_end = max((dt(r["expected_end"]) for r in g["rows"] if dt(r["expected_end"])),
                      default=None)
        incidents.append({
            "begin": g["begin"], "end": end, "ongoing": ongoing,
            "hours": max((end - g["begin"]).total_seconds() / 3600, 0),
            "expected_end": exp_end,
            "type": g["type"] or "?",
            "planned": g["type"].lower().startswith("планир"),
            "tps": sorted({r["ceo"] for r in g["rows"]}),
        })
    return incidents


# ---------- HTML --------------------------------------------------------------
def bars(data, color="#d9534f", unit="", height=150, label_every=1):
    """Проста вертикална стълбовидна графика като SVG."""
    if not data:
        return "<p class='muted'>Няма данни.</p>"
    mx = max(v for _, v in data) or 1
    n = len(data)
    W = 460                                  # фиксирана ширина → еднакъв мащаб на всички графики
    w = min((W - 10) / n, 70)
    out = [f"<svg viewBox='0 0 {W} {height + 40}' class='chart' role='img'>"]
    for i, (lab, v) in enumerate(data):
        h = v / mx * height
        x = 5 + i * w
        out.append(f"<rect x='{x + 2}' y='{height - h + 14}' width='{w - 4}' height='{h}' "
                   f"rx='2' fill='{color}'><title>{html.escape(str(lab))}: {v:g}{unit}</title></rect>")
        if v:
            out.append(f"<text x='{x + w / 2}' y='{height - h + 10}' class='v'>{v:g}</text>")
        if i % label_every == 0:
            out.append(f"<text x='{x + w / 2}' y='{height + 30}' class='l'>{html.escape(str(lab))}</text>")
    out.append("</svg>")
    return "".join(out)


def page(incidents, runs, now):
    done = [i for i in incidents]
    unpl = [i for i in done if not i["planned"]]
    pl = [i for i in done if i["planned"]]
    total_h = sum(i["hours"] for i in done)
    with_w = [i for i in unpl if i.get("weather")]
    calm = [i for i in with_w if not i["weather"]["bad"]]
    last30 = [i for i in done if i["begin"] >= now - timedelta(days=30)]
    since = runs[0]["checked_at"] if runs else "—"
    last_run = runs[-1] if runs else None
    ok_runs = sum(1 for r in runs if r["status"] in ("ok", "empty"))
    ongoing = [i for i in done if i["ongoing"]]

    def kpi(v, label, cls=""):
        return f"<div class='kpi {cls}'><b>{v}</b><span>{label}</span></div>"

    kpis = "".join([
        kpi(len(done), "прекъсвания общо"),
        kpi(len(last30), "за последните 30 дни"),
        kpi(len(unpl), "непланирани", "red"),
        kpi(len(pl), "планирани", "blue"),
        kpi(f"{total_h:.1f} ч", "общо без ток"),
        kpi(f"{(total_h / len(done) if done else 0):.1f} ч", "средно на прекъсване"),
        kpi(f"{len(calm)} от {len(with_w)}" if with_w else "—",
            "непланирани при спокойно време", "green"),
    ])

    months = defaultdict(lambda: [0, 0])
    for i in done:
        months[i["begin"].strftime("%Y-%m")][0 if not i["planned"] else 1] += 1
    mkeys = sorted(months)
    by_month = [(m[5:] + "." + m[2:4], months[m][0]) for m in mkeys]
    hours_m = defaultdict(float)
    for i in done:
        hours_m[i["begin"].strftime("%Y-%m")] += i["hours"]
    by_month_h = [(m[5:] + "." + m[2:4], round(hours_m[m], 1)) for m in mkeys]
    hod = Counter(i["begin"].hour for i in unpl)
    by_hour = [(f"{h:02d}", hod.get(h, 0)) for h in range(24)]
    wd_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
    wd = Counter(i["begin"].weekday() for i in unpl)
    by_wd = [(wd_names[d], wd.get(d, 0)) for d in range(7)]
    buckets = [("<1 ч", 0, 1), ("1–2 ч", 1, 2), ("2–4 ч", 2, 4), ("4–8 ч", 4, 8), (">8 ч", 8, 1e9)]
    by_dur = [(b, sum(1 for i in done if lo <= i["hours"] < hi)) for b, lo, hi in buckets]
    tp = Counter(t for i in done for t in i["tps"])

    rows_html = []
    for i in sorted(done, key=lambda x: x["begin"], reverse=True):
        w = i.get("weather")
        if w is None:
            wtxt = "<span class='muted'>няма данни</span>"
        else:
            tags = []
            if w["storm"]:
                tags.append("гръмотевици")
            if w["snow"]:
                tags.append("сняг")
            wtxt = (f"{'⚠️ ' if w['bad'] else '☀️ '}вятър до {w['gust']} км/ч, "
                    f"валеж {w['rain']} мм{(' · ' + ', '.join(tags)) if tags else ''}")
        rows_html.append(
            f"<tr class='{'pl' if i['planned'] else 'un'}'>"
            f"<td>{i['begin'].strftime('%d.%m.%Y')}</td><td>{i['begin'].strftime('%H:%M')}</td>"
            f"<td>{'продължава' if i['ongoing'] else i['end'].strftime('%H:%M')}</td>"
            f"<td>{i['hours']:.1f}</td><td>{html.escape(i['type'])}</td>"
            f"<td>{len(i['tps'])}</td><td>{wtxt}</td></tr>")

    status_line = ""
    if last_run:
        st = {"ok": "успешна", "empty": "успешна (няма прекъсвания)", "blocked": "БЛОКИРАНА от сайта",
              "limit": "отказ — лимит на заявките"}.get(last_run["status"], last_run["status"])
        status_line = f"Последна проверка: {last_run['checked_at']} — {st}."
    alert = ""
    if ongoing:
        alert = (f"<div class='alert'>⚡ В момента има прекъсване (от "
                 f"{ongoing[0]['begin'].strftime('%d.%m %H:%M')}, очаквано възстановяване "
                 f"{ongoing[0]['expected_end'].strftime('%d.%m %H:%M') if ongoing[0]['expected_end'] else '?'}).</div>")

    return f"""<!DOCTYPE html>
<html lang="bg"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Прекъсвания на тока — {html.escape(CITY.title())}</title>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1f2328;--mut:#6b7280;--red:#d9534f;--blue:#3b7dd8;--green:#2e9d63}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181c;--card:#1f2228;--ink:#e8e8e8;--mut:#9aa0a6}}}}
body{{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}}
main{{max-width:1000px;margin:auto;padding:20px}}
h1{{margin:.2em 0 0;font-size:1.6em}} h2{{font-size:1.1em;margin:0 0 .6em}}
.muted{{color:var(--mut)}} .card{{background:var(--card);border-radius:12px;padding:16px 18px;margin:14px 0;box-shadow:0 1px 3px #0001}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}}
.kpi{{background:var(--card);border-radius:12px;padding:12px 14px;box-shadow:0 1px 3px #0001}}
.kpi b{{display:block;font-size:1.5em}} .kpi span{{color:var(--mut);font-size:.85em}}
.kpi.red b{{color:var(--red)}} .kpi.blue b{{color:var(--blue)}} .kpi.green b{{color:var(--green)}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px}}
.grid2 .card{{margin:0}}
.chart{{width:100%;height:auto;max-height:230px}} .chart .v{{font-size:10px;text-anchor:middle;fill:var(--mut)}}
.chart .l{{font-size:10px;text-anchor:middle;fill:var(--mut)}}
table{{width:100%;border-collapse:collapse;font-size:.9em}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #8882}}
tr.un td:first-child{{border-left:3px solid var(--red)}} tr.pl td:first-child{{border-left:3px solid var(--blue)}}
.alert{{background:#d9534f22;border:1px solid var(--red);border-radius:12px;padding:10px 14px;margin:12px 0}}
.wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>Прекъсвания на тока — с. {html.escape(CITY.title())}</h1>
<div class="muted">По данни от сайта на ЕРМ Запад, проверявани автоматично. Наблюдение от {since}.
{status_line} Успешни проверки: {ok_runs} от {len(runs)}.</div>
{alert}
<div class="kpis" style="margin-top:14px">{kpis}</div>
<div class="grid2" style="margin-top:14px">
<div class="card"><h2>Непланирани прекъсвания по месеци</h2>{bars(by_month)}</div>
<div class="card"><h2>Часове без ток по месеци</h2>{bars(by_month_h, '#e59a3a', ' ч')}</div>
<div class="card"><h2>В кой час започват (непланирани)</h2>{bars(by_hour, label_every=3)}</div>
<div class="card"><h2>Ден от седмицата (непланирани)</h2>{bars(by_wd)}</div>
<div class="card"><h2>Продължителност</h2>{bars(by_dur, '#6c7ae0')}</div>
<div class="card"><h2>Най-често засегнати трафопостове</h2>
<table>{''.join(f'<tr><td>{html.escape(t)}</td><td>{c} пъти</td></tr>' for t, c in tp.most_common(10)) or '<tr><td class=muted>Няма данни</td></tr>'}</table></div>
</div>
<div class="card"><h2>Всички прекъсвания</h2><div class="wrap"><table>
<tr><th>Дата</th><th>Начало</th><th>Край*</th><th>Часа</th><th>Вид</th><th>ТП</th><th>Време (±2 ч от началото)</th></tr>
{''.join(rows_html) or '<tr><td colspan=7 class=muted>Все още няма записани прекъсвания.</td></tr>'}
</table></div>
<p class="muted">* Краят е оценка: моментът, в който прекъсването изчезва от сайта (точност ± половин интервал между проверките),
или обявеното от ЕРМ време, ако попада в този интервал. „⚠️“ = пориви ≥{GUST_KMH} км/ч, валеж ≥{RAIN_MM} мм, сняг или гръмотевици
(данни Open-Meteo). Прекъсвания, които не са публикувани на сайта на ЕРМ, не са включени.</p></div>
<p class="muted">Генерирано на {now.strftime(FMT)}.</p>
</main></body></html>"""


def write_incidents_csv(incidents):
    with open(os.path.join(DATA, "incidents.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["начало", "край (оценка)", "продължава", "часове", "вид", "брой ТП",
                    "трафопостове", "пориви км/ч", "валеж мм", "лошо време"])
        for i in incidents:
            wt = i.get("weather") or {}
            w.writerow([i["begin"].strftime(FMT), i["end"].strftime(FMT),
                        "да" if i["ongoing"] else "", f"{i['hours']:.2f}", i["type"],
                        len(i["tps"]), " ".join(i["tps"]), wt.get("gust", ""), wt.get("rain", ""),
                        {True: "да", False: "не"}.get(wt.get("bad"), "")])


def main():
    now = datetime.now(TZ).replace(tzinfo=None, second=0, microsecond=0)
    rows = read_csv(os.path.join(DATA, "outages.csv"))
    runs = read_csv(os.path.join(DATA, "runs.csv"))
    cache = update_weather()
    incidents = build_incidents(rows, now)
    for i in incidents:
        i["weather"] = weather_for(cache, i["begin"], i["end"])
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page(incidents, runs, now))
    write_incidents_csv(incidents)
    print(f"{len(incidents)} прекъсвания в {CITY}; отчетът е в docs/index.html")


if __name__ == "__main__":
    main()
