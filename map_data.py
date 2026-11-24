#!/usr/bin/env python3
"""
Изтегля от OpenStreetMap улиците и жилищните зони около селото и записва:
  data/village.json      — контур на селото (за картата)
  data/tps_streets.json  — кои улици попадат в зоната на всеки трафопост (приблизително)

Пуска се автоматично от GitHub Actions, само когато има нужда:
  python map_data.py --if-needed   (ако файловете липсват, остарели са или има нови трафопостове)
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CITY = os.environ.get("ERM_CITY", "АРЧАР")
BBOX = (43.785, 22.870, 43.835, 22.960)          # юг, запад, север, изток — около Арчар
LAT0, LON0 = 43.81, 22.91
KX, KY = 111320 * 0.7217, 110570                  # метри на градус (cos 43.8° ≈ 0.7217)
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
UA = "archar-tok (https://github.com/sv-slavchev/archar-tok)"
MAX_AGE_DAYS = 60


def to_m(lat, lon):
    return ((lon - LON0) * KX, (lat - LAT0) * KY)


def to_ll(x, y):
    return (round(LAT0 + y / KY, 6), round(LON0 + x / KX, 6))


def read_json(name, default):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def overpass():
    s, w, n, e = BBOX
    q = (f"[out:json][timeout:90];("
         f'way["landuse"="residential"]({s},{w},{n},{e});'
         f'relation["landuse"="residential"]({s},{w},{n},{e});'
         f'way["highway"]({s},{w},{n},{e});'
         f");out tags geom;")
    last = None
    for url in OVERPASS:
        try:
            r = requests.post(url, data={"data": q}, headers={"User-Agent": UA}, timeout=120)
            if r.ok and r.text.lstrip().startswith("{"):
                return r.json()["elements"]
            last = f"{url}: HTTP {r.status_code}"
        except requests.RequestException as ex:
            last = f"{url}: {ex}"
        time.sleep(3)
    raise RuntimeError(f"Overpass не отговаря ({last})")


ROADS = {"primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
         "service", "road", "primary_link", "secondary_link", "tertiary_link"}


def build(elements, tps):
    res, streets = [], []
    for el in elements:
        tags = el.get("tags", {})
        if tags.get("landuse") == "residential":
            rings = []
            if el["type"] == "way" and el.get("geometry"):
                rings = [el["geometry"]]
            elif el["type"] == "relation":
                rings = [m["geometry"] for m in el.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
            for g in rings:
                if len(g) >= 4:
                    poly = Polygon([to_m(p["lat"], p["lon"]) for p in g]).buffer(0)
                    if poly.area > 2000:
                        res.append(poly)
        elif el["type"] == "way" and tags.get("highway") in ROADS and el.get("geometry"):
            line = LineString([to_m(p["lat"], p["lon"]) for p in el["geometry"]])
            streets.append((tags.get("name", "").strip(), tags.get("highway"), line))

    # --- контур: жилищни зони + 45 м около жилищните улици, изгладено -------------
    parts = list(res)
    parts += [ln.buffer(45) for nm, hw, ln in streets if hw in ("residential", "living_street", "unclassified", "tertiary")]
    outline = None
    if parts:
        u = unary_union(parts).buffer(25).buffer(-25)
        # махаме малките отделни парчета (единични къщи/стопански дворове)
        geoms = [g for g in getattr(u, "geoms", [u]) if g.area > 30000]
        if geoms:
            u = unary_union(geoms).simplify(6)
            polys = []
            for g in getattr(u, "geoms", [u]):
                polys.append([[to_ll(x, y) for x, y in g.exterior.coords]])
            outline = polys

    # --- улици по трафопост ------------------------------------------------------
    named = [(nm, ln) for nm, hw, ln in streets if nm]
    result = {}
    for ceo, t in tps.items():
        if CITY not in (t.get("city") or "").upper():
            continue
        if t.get("hull") and len(t["hull"]) >= 3:
            area = Polygon([to_m(a, b) for a, b in t["hull"]]).buffer(15)
        elif t.get("lat") is not None:
            area = Point(to_m(t["lat"], t["lon"])).buffer(150)
        else:
            continue
        lens = {}
        for nm, ln in named:
            inter = ln.intersection(area).length
            if inter > 0:
                lens[nm] = lens.get(nm, 0) + inter
        total = {nm: sum(l.length for n2, l in named if n2 == nm) for nm in lens}
        keep = [nm for nm, l in lens.items() if l >= 40 or l >= 0.3 * total[nm]]
        result[ceo] = sorted(keep, key=lambda nm: -lens[nm])
    return outline, result


def needed():
    v = read_json("village.json", None)
    st = read_json("tps_streets.json", None)
    if not v or st is None:
        return True
    try:
        if datetime.now() - datetime.fromisoformat(v.get("updated", "2000-01-01")) > timedelta(days=MAX_AGE_DAYS):
            return True
    except ValueError:
        return True
    tps = read_json("tps.json", {})
    mine = {c for c, t in tps.items() if CITY in (t.get("city") or "").upper()}
    return bool(mine - set(st))


def main():
    if "--if-needed" in sys.argv and not needed():
        print("Картните данни са актуални.")
        return
    tps = read_json("tps.json", {})
    try:
        elements = overpass()
    except Exception as ex:          # картата е допълнителна — не спираме проверките
        print("Пропускам картните данни:", ex)
        return
    outline, streets = build(elements, tps)
    write_json("village.json", {"updated": datetime.now().strftime("%Y-%m-%d"), "outline": outline,
                                "source": "© OpenStreetMap contributors (ODbL)"})
    write_json("tps_streets.json", streets)
    print(f"Контур: {len(outline or [])} части; улици за {len(streets)} трафопоста")


if __name__ == "__main__":
    main()
