"""
Симулира ~4 месеца проверки (формат като на ЕРМ) и строи страницата в /tmp/sim.
Пускане: python tests/simulate.py   → после отворете /tmp/sim/docs/index.html през уеб сървър.
"""
import json, os, random, shutil, sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import monitor, report

OUT = "/tmp/sim"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(f"{OUT}/data")
os.makedirs(f"{OUT}/site")
shutil.copy(f"{ROOT}/site/index.html", f"{OUT}/site/index.html")
tps = json.load(open(f"{ROOT}/data/tps.json", encoding="utf-8"))
json.dump(tps, open(f"{OUT}/data/tps.json", "w", encoding="utf-8"), ensure_ascii=False)
for m in (monitor, report):
    m.DATA = f"{OUT}/data"
monitor.OUTAGES = f"{OUT}/data/outages.csv"; monitor.RUNS = f"{OUT}/data/runs.csv"
report.WEATHER = f"{OUT}/data/weather.json"; report.DOCS = f"{OUT}/docs"; report.SITE = f"{OUT}/site/index.html"

F = "%d.%m.%Y %H:%M"
random.seed(7)
by_city = {}
for c, t in tps.items():
    by_city.setdefault(t["city"], []).append(c)
arch = by_city["АРЧАР"]
hot = ["VD_0874", "VD_0873", "VD_0871", "VD_7048"]          # „проблемни“ трафопостове

t0, end = datetime(2026, 8, 1), datetime(2026, 11, 24, 15, 10)
events = []
d = t0
while d < end:
    for _ in range(2 if random.random() < .15 else 1):
        if random.random() < .22:
            b = d + timedelta(hours=random.choice([3, 7, 11, 14, 17, 19, 20, 21, 23]), minutes=random.choice([5, 20, 35, 47]))
            dur = timedelta(minutes=random.choice([35, 50, 80, 110, 140, 200, 320]))
            r = random.random()
            if r < .35:
                ts = random.sample(hot, random.randint(1, 3))
            elif r < .7:
                ts = random.sample(arch, random.randint(3, 8))
            else:
                ts = arch + random.sample(by_city["ИЗВОР, общ. Димово"], 2) + by_city["ЛАГОШЕВЦИ"]
            exp0 = b + dur + timedelta(minutes=random.choice([-40, -20, 0, 15]))
            events.append((b, b + dur, exp0, "непланирано", ts))
        if random.random() < .05:
            ts = random.sample(by_city[random.choice(["ШИПОТ", "ДЪРЖАНИЦА", "ВЛАДИЧЕНЦИ", "МАЛИ ДРЕНОВЕЦ"])], 1)
            b = d + timedelta(hours=random.randint(0, 22))
            events.append((b, b + timedelta(minutes=90), b + timedelta(minutes=120), "непланирано", ts))
    if random.random() < .035:
        b = d + timedelta(hours=9, minutes=50)
        events.append((b, b + timedelta(hours=6, minutes=10), b + timedelta(hours=6, minutes=10), "планирано", list(tps)))
    d += timedelta(days=1)
events.append((datetime(2026, 11, 24, 9, 50), datetime(2026, 11, 24, 16, 0), datetime(2026, 11, 24, 16, 0), "планирано", list(tps)))

weather = {}
w = t0
while w < end + timedelta(days=1):
    storm = random.random() < .04
    weather[w.strftime("%Y-%m-%dT%H:00")] = [random.randint(55, 85) if storm else random.randint(5, 35),
                                              4.0 if storm else 0.0, 95 if storm else 2, 8]
    w += timedelta(hours=1)
json.dump(weather, open(report.WEATHER, "w"))
report.update_weather = lambda: weather


def payload(t):
    obj = {}
    for b, e, exp0, typ, ts in events:
        if b <= t < e:
            exp = exp0 if t < exp0 else exp0 + timedelta(minutes=60)   # ЕРМ отлага, ако закъснява
            for c in ts:
                g = tps[c]
                obj[str(len(obj) + 1)] = {"lat": str(g["lat"]), "lon": str(g["lon"]), "typedist": typ,
                    "begin_event": b.strftime(F), "end_event": exp.strftime(F), "city_name": g["city"],
                    "points": {"cnt": 0}, "ceo": c}
    return json.dumps(obj, ensure_ascii=False) if obj else ""


class Clock:  # подменя „сега“ в monitor.now_str
    t = t0
monitor.now_str = lambda: Clock.t.strftime(F)

t = t0
while t <= end:
    Clock.t = t
    if random.random() < .97:                     # понякога проверката пропада
        status, recs = monitor.parse(200, payload(t))
        rows = monitor.update(monitor.load(), status, recs, t.strftime(F))
        monitor.save(rows); monitor.log_run(status, len(recs))
    rows = monitor.load()
    t += timedelta(minutes=15 if monitor.has_open(rows) else 30)

report.datetime = type("FixedDT", (datetime,), {"now": classmethod(lambda cls, tz=None: end.replace(tzinfo=tz))})
report.main()
print("събития:", len(events))
