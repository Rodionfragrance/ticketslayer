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
    req = urllib.request.Request(url, headers={"User-Agent": "ls-chicken-marktbericht"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")

def welt():
    """Rhein, Öl, Suez. Jede Quelle für sich, ein Ausfall stoppt nichts."""
    w = {}
    try:
        j = json.loads(get(PEGEL))
        w["kaub"] = {"cm": round(float(j["value"])), "ts": j["timestamp"][:16], "state": j.get("stateMnwMhw", "")}
    except Exception as e:
        print("Pegel:", e, file=sys.stderr)
    try:
        rows = [l.split(",") for l in get(BRENT).strip().splitlines()[1:]]
        rows = [(d, float(v)) for d, v in rows if v not in (".", "")]
        d, v = rows[-1]
        ago = datetime.date.fromisoformat(d) - datetime.timedelta(days=365)
        old = [x for x in rows if x[0] <= ago.isoformat()]
        w["brent"] = {"usd": round(v, 2), "date": d, "yoy": round((v / old[-1][1] - 1) * 100, 1) if old else None}
    except Exception as e:
        print("Brent:", e, file=sys.stderr)
    try:
        feats = json.loads(get(SUEZ))["features"]
        n = [f["attributes"]["n_total"] for f in feats]
        dt = feats[0]["attributes"]["date"]
        if isinstance(dt, (int, float)):
            dt = datetime.datetime.fromtimestamp(dt / 1000, datetime.timezone.utc).date().isoformat()
        w["suez"] = {"avg7": round(sum(n) / len(n), 1), "base": SUEZ_BASE, "date": str(dt)[:10]}
    except Exception as e:
        print("Suez:", e, file=sys.stderr)
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
    try:
        period, yoy = parse(fetch())
    except Exception as e:
        print("Eurostat nicht erreichbar:", e, file=sys.stderr)
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
    hist[wk] = {"period": period, "yoy": yoy, "welt": w}
    hist = dict(sorted(hist.items())[-26:])          # ein halbes Jahr reicht
    data = {"src": "Eurostat HICP Deutschland (prc_hicp_minr, Veränderung zum Vorjahresmonat)",
            "period": period, "fetched": today.isoformat(), "week": wk, "yoy": yoy, "welt": w, "hist": hist}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(wk, period, yoy, w)
    return 0

if __name__ == "__main__":
    sys.exit(main())
