#!/usr/bin/env python3
"""Peters Marktbericht: holt echte Preisentwicklung (Eurostat HICP, Deutschland)
und schreibt markt.json für LS-Chicken. Nur Standardbibliothek."""
import json, urllib.request, datetime, os, sys

API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr"
SERIES = {               # Schlüssel im Spiel -> ECOICOP-2-Code
    "food": "CP011", "poultry": "CP011224", "pork": "CP011222", "beef": "CP011221",
    "meatprep": "CP01125", "oils": "CP0115", "veg": "CP0117", "bread": "CP01113",
    "soft": "CP0126", "beer": "CP0213", "coffee": "CP0122",
}
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
    try:
        period, yoy = parse(fetch())
    except Exception as e:
        print("Eurostat nicht erreichbar:", e, file=sys.stderr)
        if old:
            return 0          # alte Datei bleibt stehen
        raise
    if len(yoy) < 5:
        print("Zu wenige Werte:", yoy, file=sys.stderr)
        return 0 if old else 1
    today = datetime.date.today()
    wk = week_id(today)
    hist = old.get("hist", {})
    hist[wk] = {"period": period, "yoy": yoy}
    hist = dict(sorted(hist.items())[-26:])          # ein halbes Jahr reicht
    data = {"src": "Eurostat HICP Deutschland (prc_hicp_minr, Veränderung zum Vorjahresmonat)",
            "period": period, "fetched": today.isoformat(), "week": wk, "yoy": yoy, "hist": hist}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(wk, period, yoy)
    return 0

if __name__ == "__main__":
    sys.exit(main())
