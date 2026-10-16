#!/usr/bin/env python3
"""Peters Marktbericht: holt echte Preisentwicklung (Eurostat HICP, Deutschland)
und schreibt markt.json für LS-Chicken. Nur Standardbibliothek."""
import json, urllib.request, datetime, os, sys

API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr"
SERIES = {               # Schlüssel im Spiel -> ECOICOP-2-Code
    "food": "CP011", "poultry": "CP011224", "pork": "CP011222", "beef": "CP011221",
    "meatprep": "CP01125", "oils": "CP0115", "veg": "CP0117", "bread": "CP01113",
    "soft": "CP0126", "beer": "CP0213", "coffee": "CP0122",
    "fuel": "CP0722", "diesel": "CP07221", "elec": "CP0451", "gas": "CP0452",
}
PEGEL = "https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/KAUB/W/currentmeasurement.json"
BRENT = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
SUEZ = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
        "?where=portname%3D%27Suez%20Canal%27&outFields=date,n_total&orderByFields=date%20DESC&resultRecordCount=7&f=json")
SUEZ_BASE = 72          # Schiffe pro Tag im Normalbetrieb (2023, vor den Umleitungen ums Kap)

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ls-chicken-marktbericht", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/BZ%3DF?range=13mo&interval=1d"
ERR = []

def brent_yahoo():
    r = json.loads(get(YAHOO))["chart"]["result"][0]
    pts = [(datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat(), c)
           for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c]
    d, v = pts[-1]
    ago = (datetime.date.fromisoformat(d) - datetime.timedelta(days=365)).isoformat()
    old = [x for x in pts if x[0] <= ago]
    return {"usd": round(v, 2), "date": d, "yoy": round((v / old[-1][1] - 1) * 100, 1) if old else None, "src": "Yahoo BZ=F"}

# ---------- Kalender & Verkehr ----------
NAGER = "https://date.nager.at/api/v3/PublicHolidays/{y}/DE"
FERIEN = "https://ferien-api.de/api/v1/holidays/NW/{y}"
OPENHOL = ("https://openholidaysapi.org/SchoolHolidays?countryIsoCode=DE&subdivisionCode=DE-NW&languageIsoCode=DE"
           "&validFrom={a}-01-01&validTo={b}-12-31")
OLDB = "https://api.openligadb.de/getmatchdata/{liga}/{saison}"
AUTOBAHN = "https://verkehr.autobahn.de/o/autobahn/{ab}/services/{svc}"
AB_BOX = (51.50, 51.76, 7.60, 8.15)       # Raum Kamen/Hamm/Unna/Werl/Soest: lat_min, lat_max, lon_min, lon_max
CLUBS = {"Dortmund": "BVB", "Schalke": "S04"}

def feiertage(today):
    out = []
    for y in (today.year, today.year + 1):
        for h in json.loads(get(NAGER.format(y=y))):
            if h.get("global") or "DE-NW" in (h.get("counties") or []):
                out.append({"d": h["date"], "name": h["localName"]})
    return sorted({h["d"]: h for h in out}.values(), key=lambda h: h["d"])

def ferien(today):
    out = []
    try:                                            # OpenHolidays (EU-gefördert), sonst ferien-api.de
        for f in json.loads(get(OPENHOL.format(a=today.year, b=today.year + 1))):
            n = next((x["text"] for x in f.get("name", []) if x.get("language") == "DE"), "Ferien")
            out.append({"s": f["startDate"][:10], "e": f["endDate"][:10], "name": n})
    except Exception as e:
        ERR.append("OpenHolidays: %s" % e)
    for y in (() if out else (today.year, today.year + 1)):
        try:
            for f in json.loads(get(FERIEN.format(y=y))):
                n = f.get("name", "Ferien")
                out.append({"s": f["start"][:10], "e": f["end"][:10], "name": n[:1].upper() + n[1:]})
        except Exception as e:
            ERR.append("Ferien %s: %s" % (y, e))
    if not out:
        raise RuntimeError("keine Ferientermine")
    return sorted({f["s"]: f for f in out}.values(), key=lambda f: f["s"])

def bundesliga(today):
    saison = today.year if today.month >= 7 else today.year - 1
    out = []
    for liga in ("bl1", "bl2"):
        try:
            ms = json.loads(get(OLDB.format(liga=liga, saison=saison)))
        except Exception as e:
            ERR.append("OpenLigaDB %s: %s" % (liga, e)); continue
        for m in ms:
            t1, t2 = m["team1"]["teamName"], m["team2"]["teamName"]
            for key, club in CLUBS.items():
                if key in t1 or key in t2:
                    res = [r for r in m.get("matchResults") or [] if r.get("resultTypeID") == 2]
                    heim = key in t1
                    g = (res[0]["pointsTeam1"], res[0]["pointsTeam2"]) if res else None
                    out.append({"d": m["matchDateTime"][:10], "t": m["matchDateTime"][11:16], "club": club, "heim": heim,
                                "gegner": (t2 if heim else t1), "done": bool(m.get("matchIsFinished")) and g is not None,
                                "tore": ([g[0], g[1]] if heim else [g[1], g[0]]) if g else None, "liga": liga})
    out.sort(key=lambda x: (x["d"], x["t"]))
    return out

def autobahn():
    items = []
    for ab in ("A1", "A2", "A44", "A445"):
        for svc in ("roadworks", "closure"):
            try:
                j = json.loads(get(AUTOBAHN.format(ab=ab, svc=svc)))
            except Exception as e:
                ERR.append("Autobahn %s %s: %s" % (ab, svc, e)); continue
            for it in j.get(svc, []):
                c = it.get("coordinate") or {}
                try:
                    lat, lon = float(c["lat"]), float(c["long"])
                except Exception:
                    continue
                if AB_BOX[0] <= lat <= AB_BOX[1] and AB_BOX[2] <= lon <= AB_BOX[3]:
                    items.append({"ab": ab, "typ": svc, "titel": (it.get("title") or "")[:80],
                                  "ort": (it.get("subtitle") or "")[:80], "voll": str(it.get("isBlocked")).lower() == "true"})
    seen, uniq = set(), []
    for i in items:
        k = (i["ab"], i["titel"], i["ort"])
        if k not in seen:
            seen.add(k); uniq.append(i)
    return {"n": len(uniq), "voll": sum(1 for i in uniq if i["voll"]), "liste": uniq[:6]}

def kal():
    today = datetime.date.today()
    k = {}
    for key, fn in (("feiertage", lambda: feiertage(today)), ("ferien", lambda: ferien(today)),
                    ("bl", lambda: bundesliga(today)), ("autobahn", autobahn)):
        try:
            k[key] = fn()
        except Exception as e:
            ERR.append("%s: %s" % (key, e))
    return k

def welt():
    """Rhein, Öl, Suez. Jede Quelle für sich, ein Ausfall stoppt nichts."""
    w = {}
    try:
        j = json.loads(get(PEGEL))
        w["kaub"] = {"cm": round(float(j["value"])), "ts": j["timestamp"][:16], "state": j.get("stateMnwMhw", "")}
    except Exception as e:
        ERR.append("Pegel: %s" % e)
    try:
        rows = [l.split(",") for l in get(BRENT).strip().splitlines()[1:]]
        rows = [(d, float(v)) for d, v in rows if v not in (".", "")]
        d, v = rows[-1]
        ago = datetime.date.fromisoformat(d) - datetime.timedelta(days=365)
        old = [x for x in rows if x[0] <= ago.isoformat()]
        w["brent"] = {"usd": round(v, 2), "date": d, "yoy": round((v / old[-1][1] - 1) * 100, 1) if old else None, "src": "FRED"}
    except Exception as e:
        ERR.append("Brent FRED: %s" % e)
        try:
            w["brent"] = brent_yahoo()
        except Exception as e2:
            ERR.append("Brent Yahoo: %s" % e2)
    try:
        feats = json.loads(get(SUEZ))["features"]
        n = [f["attributes"]["n_total"] for f in feats]
        dt = feats[0]["attributes"]["date"]
        if isinstance(dt, (int, float)):
            dt = datetime.datetime.fromtimestamp(dt / 1000, datetime.timezone.utc).date().isoformat()
        w["suez"] = {"avg7": round(sum(n) / len(n), 1), "base": SUEZ_BASE, "date": str(dt)[:10]}
    except Exception as e:
        ERR.append("Suez: %s" % e)
    return w
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "markt.json")

def fetch():
    q = "&".join(["geo=DE", "unit=RCH_A", "lastTimePeriod=1", "format=JSON", "lang=EN"] +
                 ["coicop18=" + c for c in SERIES.values()])
    with urllib.request.urlopen(API + "?" + q, timeout=60) as r:
        return json.load(r)

def parse(js):
    ids, size = js["id"], js["size"]
    cat = {d: js["dimension"][d]["category"]["index"] for d in ids}
    period = next(iter(cat["time"]))
    out = {}
    for key, code in SERIES.items():
        if code not in cat["coicop18"]:
            continue
        pos = {d: 0 for d in ids}
        pos["coicop18"] = cat["coicop18"][code]
        pos["unit"] = cat["unit"].get("RCH_A", 0)
        pos["time"] = cat["time"][period]
        flat = 0
        for d, n in zip(ids, size):
            flat = flat * n + pos[d]
        v = js["value"].get(str(flat))
        if v is not None:
            out[key] = round(float(v), 1)
    return period, out

def week_id(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"

def main():
    old = {}
    try:
        old = json.load(open(OUT))
    except Exception:
        pass
    w = {**old.get("welt", {}), **welt()}           # was nicht kommt, bleibt alt
    k = {**old.get("kal", {}), **kal()}
    try:
        period, yoy = parse(fetch())
    except Exception as e:
        ERR.append("Eurostat: %s" % e)
        if not old:
            raise
        period, yoy = old["period"], old["yoy"]      # Preise bleiben alt, Welt wird trotzdem frisch
    if len(yoy) < 5:
        print("Zu wenige Werte:", yoy, file=sys.stderr)
        if not old:
            return 1
        period, yoy = old["period"], old["yoy"]
    today = datetime.date.today()
    wk = week_id(today)
    hist = old.get("hist", {})
    if wk not in hist:                                # erster Lauf der Woche friert die Challenge-Werte ein
        hist[wk] = {"period": period, "yoy": yoy, "welt": w, "kal": k}
    hist = dict(sorted(hist.items())[-26:])          # ein halbes Jahr reicht
    data = {"src": "Eurostat HICP Deutschland (prc_hicp_minr, Veränderung zum Vorjahresmonat)",
            "period": period, "fetched": today.isoformat(), "week": wk, "yoy": yoy, "welt": w, "kal": k, "errors": ERR, "hist": hist}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(wk, period, yoy, w, {x: (len(v) if isinstance(v, list) else v) for x, v in k.items()}, ERR)
    return 0

if __name__ == "__main__":
    sys.exit(main())
