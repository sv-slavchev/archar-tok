"""Симулира няколко дни проверки с данни във формата на ЕРМ и строи отчета."""
import json, os, sys, shutil, random
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor, report

tmp = "/tmp/simdata"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
monitor.DATA = report.DATA = tmp
monitor.OUTAGES = f"{tmp}/outages.csv"; monitor.RUNS = f"{tmp}/runs.csv"
report.WEATHER = f"{tmp}/weather.json"; report.OUT_HTML = f"{tmp}/index.html"

F = "%d.%m.%Y %H:%M"
def rec(ceo, city, typ, b, e):
    return {"lat":"0","lon":"0","typedist":typ,"type_event":"1","begin_event":b.strftime(F),
            "end_event":e.strftime(F),"city_name":city,"points":{"cnt":0},"cities":"","grid_id":"","ceo":ceo}
TPS = ["VD_0866","VD_0867","VD_0868","VD_0869","VD_0870","VD_0872","VD_0873","VD_0874","VD_0875"]
random.seed(1)
events = []  # (begin, real_end, expected_end, type, tps)
t0 = datetime(2026, 9, 1)
for d in range(80):
    if random.random() < 0.18:
        b = t0 + timedelta(days=d, hours=random.randint(0,23), minutes=random.choice([5,20,35,47]))
        dur = timedelta(minutes=random.choice([40,90,130,200,320]))
        events.append((b, b+dur, b+dur+timedelta(minutes=random.choice([-20,10,30])), "непланирано",
                       random.sample(TPS, random.randint(1,9))))
    if random.random() < 0.04:
        b = t0 + timedelta(days=d, hours=9)
        events.append((b, b+timedelta(hours=7), b+timedelta(hours=7,minutes=30), "планирано", TPS))
# реалният случай от днес: 19.11 19:47 – 22:00 VD_0874
b = datetime(2026,11,19,19,47); events.append((b, datetime(2026,11,19,21,40), datetime(2026,11,19,22,0), "непланирано", ["VD_0874"]))
b = datetime(2026,11,20,0,35); events.append((b, datetime(2026,11,20,3,0), datetime(2026,11,20,2,0), "непланирано", TPS))
weather = {}
t = t0
while t < datetime(2026,11,21):
    stormy = random.random() < 0.05
    weather[t.strftime("%Y-%m-%dT%H:00")] = [random.randint(55,80) if stormy else random.randint(5,30), 3.0 if stormy else 0, 95 if stormy else 1, 10]
    t += timedelta(hours=1)
json.dump(weather, open(report.WEATHER,"w"))

t = t0
end = datetime(2026,11,20,0,40)
while t <= end:
    active = [e for e in events if e[0] <= t < e[1]]
    obj = {}
    for e in active:
        for tp in e[4]:
            obj[str(len(obj)+1)] = rec(tp, "АРЧАР", e[3], e[0], e[2])
        obj[str(len(obj)+1)] = rec("VD_0859", "ЛАГОШЕВЦИ", e[3], e[0], e[2])
    body = json.dumps(obj, ensure_ascii=False) if obj else ""
    if random.random() < 0.02: body = "tomuch"
    status, recs = monitor.parse(200, "\r\n" + body)
    ts = t.strftime(F)
    rows = monitor.update(monitor.load(), status, recs, ts)
    monitor.save(rows); monitor.log_run(status, len(recs))
    t += timedelta(minutes=30)

report.update_weather = lambda: weather
now = end
rows = report.read_csv(monitor.OUTAGES); runs = report.read_csv(monitor.RUNS)
inc = report.build_incidents(rows, now)
for i in inc: i["weather"] = report.weather_for(weather, i["begin"], i["end"])
open(report.OUT_HTML,"w").write(report.page(inc, runs, now))
report.write_incidents_csv(inc)
print("истински събития:", len(events), " открити:", len(inc))
for e, i in zip(sorted(events), inc):
    print(e[0].strftime(F), "реално", round((e[1]-e[0]).total_seconds()/3600,2), "ч | оценка", round(i["hours"],2), "ч", i["type"], "ongoing" if i["ongoing"] else "")
