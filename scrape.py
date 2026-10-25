"""
FuelSearch daily price collector.

Run once a day after the 6:00 AM IST revision (see .github/workflows/daily-prices.yml).
Reads  data/cities.csv      master list: city, state, lat, lon, cardekho_slug
Writes data/prices.json     what the website loads
       data/history.csv     one row per city, fuel and source per day

Sources
-------
PRIMARY   mypetrolprice.com   (written permission held; keep the email on file)
CHECKS    grouped into families. A check only counts once per family, because
          sources in the same family copy the same upstream feed.
            official     omc_sms      pump replies to the IOCL / HPCL / BPCL SMS price checks (sms_pilot.py)
            aggregator   cardekho     every city page + its "nearby cities" table
                         goodreturns  metros and state capitals
          (CarDekho and GoodReturns matched each other to the paisa for all
           12 cities compared on 25 Oct 2026, so they are one family.)
BRANDS    Jio-bp / Nayara set their own prices: stored separately, never a check.

Status per city and fuel
  verified    a check from any family is within TOL_OK (₹0.30) of the primary
  differs     the nearest check is ₹0.30–₹1.00 away: both prices shown
  conflict    the nearest check is more than ₹1.00 away: yesterday's verified
              price is held (or the city is hidden) and the city is listed for review
  unverified  no check covers the city today ("1 source")
"""
import csv, json, re, sys, time, datetime as dt
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "FuelSearchBot/0.3 (contact: you@example.com)"}
DATA = Path("data")
TOL_OK, TOL_CONFLICT = 0.30, 1.00
SUSPECT_PETROL = {115.01}      # placeholder value seen in the primary feed; drops that city's row
FAMILY = {"omc_sms": "official", "cardekho": "aggregator", "goodreturns": "aggregator"}
FAMILY_ORDER = {"official": 0, "aggregator": 1}
PAUSE = 1.0                    # seconds between requests to the same site

ALIASES = {"Bangalore": "Bengaluru", "Bhubhaneswar": "Bhubaneswar", "Aizwal": "Aizawl", "Panjim": "Panaji",
           "Trivandrum": "Thiruvananthapuram", "Vizag": "Visakhapatnam", "Mysore": "Mysuru",
           "Mangalore": "Mangaluru", "Gurgaon": "Gurugram", "New Delhi": "Delhi", "Allahabad": "Prayagraj",
           "Tumkur": "Tumakuru", "Shimoga": "Shivamogga", "Chikmagalur": "Chikkamagaluru",
           "Chickmagaluru": "Chikkamagaluru", "Kakkanad": "Kochi", "Hubli": "Hubballi-Dharwad",
           "Dharwad": "Hubballi-Dharwad", "Gulbarga": "Kalaburagi", "Belgaum": "Belagavi"}

IOCL_DEALER_CODES = {  # IOCL's published sample codes (iocl.com/petrol-diesel-price); add HPCL/BPCL codes as collected
    "Delhi": "102090", "Mumbai": "108412", "Kolkata": "119941", "Chennai": "133649", "Gurugram": "102082",
    "Faridabad": "102287", "Ghaziabad": "154410", "Bengaluru": "118219", "Hyderabad": "134483",
    "Bhopal": "169398", "Bhubaneswar": "124305", "Chandigarh": "102790", "Gangtok": "159289",
    "Guwahati": "159571", "Imphal": "159875", "Itanagar": "160647", "Jaipur": "123143", "Jammu": "108726",
    "Jalandhar": "108743", "Kohima": "160154", "Lucknow": "155054", "Panaji": "125676", "Patna": "166873",
    "Puducherry": "135299", "Port Blair": "220191", "Raipur": "169751", "Ranchi": "166751",
    "Shillong": "159828", "Shimla": "109295", "Srinagar": "109536", "Thiruvananthapuram": "124923",
    "Silvassa": "367896", "Daman": "177747", "Vijayawada": "127611", "Visakhapatnam": "127290",
    "Leh": "194259", "Agartala": "159850", "Aizawl": "160181", "Ambala": "102049",
}


def canon(city):
    city = re.sub(r"\s+", " ", city).strip()
    return ALIASES.get(city, city)


def num(text):
    m = re.search(r"(\d{2,3}\.\d{1,2})", text.replace(",", ""))
    return float(m.group(1)) if m else None


def ist_today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    time.sleep(PAUSE)
    return BeautifulSoup(r.text, "html.parser")


def load_master():
    with open(DATA / "cities.csv", newline="") as f:
        return {r["city"]: r for r in csv.DictReader(f)}


# ------------------------------------------------------------------ primary
def mypetrolprice(master):
    rows = {}
    for fuel, url in [("petrol", "https://www.mypetrolprice.com/petrol-price-in-india.aspx"),
                      ("diesel", "https://www.mypetrolprice.com/diesel-price-in-india.aspx")]:
        soup, state = get(url), None
        for el in soup.find_all(["h2", "a"]):
            if el.name == "h2":
                state = el.get_text(strip=True)
            elif re.search(r"/(Petrol|Diesel)-price-in-", el.get("href", "")) and state:
                b = el.find_next(["b", "strong"])
                v = b and num(b.get_text())
                if v:
                    rows.setdefault(canon(el.get_text(strip=True)), {})[fuel] = v
    for city, r in list(rows.items()):
        if r.get("petrol") in SUSPECT_PETROL:      # placeholder pair: drop both fuels
            del rows[city]
    return rows


# ------------------------------------------------------------------ checks
def cardekho(master):
    """One page per city and fuel. Each page also lists ~10 nearby cities, which
    fills in other cities and saves requests. Slugs live in cities.csv."""
    rows = {}
    for city, m in master.items():
        slug = (m.get("cardekho_slug") or "").strip()
        if not slug:
            continue
        for fuel in ("petrol", "diesel"):
            if fuel in rows.get(city, {}):
                continue                        # already filled from a neighbour's table
            try:
                soup = get(f"https://www.cardekho.com/{fuel}-price-in-{slug}-city")
            except Exception as e:
                print(f"  [cardekho] {city} {fuel}: {e}", file=sys.stderr)
                continue
            text = soup.get_text(" ", strip=True)
            mm = re.search(r"price in .{2,40}? stands at ₹\s*([\d.]+)", text, re.I)
            if mm:
                rows.setdefault(city, {})[fuel] = float(mm.group(1))
            for tr in soup.select("table tr"):                       # nearby cities
                tds = tr.find_all("td")
                if len(tds) >= 2:
                    v = num(tds[1].get_text())
                    n = canon(tds[0].get_text(strip=True))
                    if v and 60 < v < 150 and n in master:
                        rows.setdefault(n, {}).setdefault(fuel, v)
    return rows


def goodreturns(master):
    rows = {}
    for fuel, url in [("petrol", "https://www.goodreturns.in/petrol-price.html"),
                      ("diesel", "https://www.goodreturns.in/diesel-price.html")]:
        for tr in get(url).select("table tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                v = num(tds[1].get_text())
                if v and 60 < v < 150:
                    rows.setdefault(canon(tds[0].get_text(strip=True)), {})[fuel] = v
    return rows


def read_sms(path=DATA / "omc_sms.csv"):
    """CSV written by sms_pilot.py: date,dealer_code,company,city,reply.
    Returns {dealer_code: {"petrol": x, "diesel": y, "company": c, "city": city}} for today."""
    out, today = {}, ist_today()
    if path.exists():
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                if r.get("date") != today:
                    continue
                rec = {"company": r.get("company", ""), "city": canon(r.get("city", ""))}
                for fuel, pat in [("petrol", r"(?:MS|Petrol)\D{0,12}(\d{2,3}\.\d{1,2})"),
                                  ("diesel", r"(?:HSD|Diesel)\D{0,12}(\d{2,3}\.\d{1,2})")]:
                    m = re.search(pat, r.get("reply", ""), re.I)
                    if m:
                        rec[fuel] = float(m.group(1))
                out[r["dealer_code"]] = rec
    return out


def omc_sms(master):
    """City-level check from the pump SMS replies: median of today's pump prices in each city."""
    by = {}
    for rec in read_sms().values():
        for fuel in ("petrol", "diesel"):
            if fuel in rec and rec["city"]:
                by.setdefault(rec["city"], {}).setdefault(fuel, []).append(rec[fuel])
    return {c: {f: sorted(v)[len(v) // 2] for f, v in d.items()} for c, d in by.items()}


def private_brands(path=DATA / "brands.csv"):
    """CSV: region,state,brand,petrol,diesel,lat,lon. Jio-bp / Nayara / Shell set their own prices;
    a row with region == state is that brand's state-wide price."""
    out = []
    if path.exists():
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                out.append({"city": r["region"], "state": r["state"], "brand": r["brand"],
                            "lat": float(r["lat"]), "lon": float(r["lon"]),
                            "petrol": float(r["petrol"]) if r["petrol"] else None,
                            "diesel": float(r["diesel"]) if r["diesel"] else None})
    return out


BRAND_CODE = {"Jio-bp": "JIO", "Nayara": "NAYARA", "Shell": "SHELL"}
PSU = {"IOC", "HPCL", "BPCL"}


def station_prices(result, path=DATA / "stations.csv"):
    """Price every pump. Best source first:
       sms    the pump's own price from the oil company's SMS check today (exact)
       city   the verified city price for IOC / HPCL / BPCL pumps in that district
       brand  the brand's own state price for Jio-bp / Nayara / Shell
    Live user reports are layered on top by the website."""
    if not path.exists():
        return []
    sms = read_sms()
    city = {c["city"]: c for c in result["cities"]}
    pvt = {}
    for p in result["private"]:
        if p["city"] == p["state"] or p["city"].startswith(p["state"]):
            pvt[(BRAND_CODE.get(p["brand"], p["brand"].upper()), p["state"])] = p
    out = []
    with path.open(newline="") as f:
        for st in csv.DictReader(f):
            e = {k: st[k] for k in ("id", "brand", "name", "address", "town", "district", "state",
                                    "pincode", "location", "phone", "source_url")}
            e["lat"] = float(st["lat"]) if st["lat"] else None
            e["lon"] = float(st["lon"]) if st["lon"] else None
            own = sms.get(st.get("dealer_code") or "-")
            base = city.get(canon(st["district"])) or city.get(canon(st["town"]))
            for fuel in ("petrol", "diesel"):
                if own and fuel in own:
                    e[fuel] = {"price": own[fuel], "source": "sms"}
                elif st["brand"] in PSU and base and base.get(fuel) and base[fuel].get("price") is not None:
                    e[fuel] = {"price": base[fuel]["price"], "source": "city", "status": base[fuel]["status"],
                               "city": base["city"]}
                elif (st["brand"], st["state"]) in pvt and pvt[(st["brand"], st["state"])][fuel] is not None:
                    e[fuel] = {"price": pvt[(st["brand"], st["state"])][fuel], "source": "brand"}
                else:
                    e[fuel] = None
            out.append(e)
    return out


CHECKS = [omc_sms, cardekho, goodreturns]


# ------------------------------------------------------------------ consensus
def verify(primary, checks, yesterday=None):
    """primary: float|None. checks: {source: float}. yesterday: last good price.
    One value per family counts (official first, then aggregator)."""
    fam = {}
    for src in sorted(checks, key=lambda s: list(FAMILY).index(s) if s in FAMILY else 99):
        fam.setdefault(FAMILY.get(src, src), checks[src])
    sources = ({"mypetrolprice": primary} if primary is not None else {}) | checks
    if primary is None and not fam:
        return None
    if not fam:
        return {"price": primary, "status": "unverified", "sources": sources}
    if primary is None:
        best = min(fam, key=lambda f: FAMILY_ORDER.get(f, 9))
        return {"price": fam[best], "status": "unverified", "sources": sources}
    nearest = min(abs(v - primary) for v in fam.values())
    if nearest <= TOL_OK:
        return {"price": primary, "status": "verified", "sources": sources}
    if nearest <= TOL_CONFLICT:
        return {"price": primary, "status": "differs", "sources": sources,
                "other": min(fam.values(), key=lambda v: abs(v - primary))}
    return {"price": yesterday, "status": "conflict", "sources": sources}


def build(master, primary, checks, previous, today):
    prev = {c["city"]: c for c in previous}
    cities, review, counts = [], [], {}
    for city, m in master.items():
        entry = {"city": city, "state": m["state"], "lat": float(m["lat"]), "lon": float(m["lon"])}
        for fuel in ("petrol", "diesel"):
            ch = {s: d[city][fuel] for s, d in checks.items() if fuel in d.get(city, {})}
            last = (prev.get(city) or {}).get(fuel) or {}
            y = last.get("price") if last.get("status") in ("verified", "differs") else None
            res = verify(primary.get(city, {}).get(fuel), ch, y)
            if res:
                counts[res["status"]] = counts.get(res["status"], 0) + 1
                if res["status"] == "conflict":
                    review.append({"city": city, "fuel": fuel, "sources": res["sources"]})
            entry[fuel] = res
        if entry["petrol"] or entry["diesel"]:
            cities.append(entry)
    return {"asof": today, "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
            "counts": counts, "review": review, "cities": cities, "private": private_brands()}


def main():
    master = load_master()
    try:
        primary = mypetrolprice(master)
    except Exception as e:
        sys.exit(f"Primary source failed ({e}); keeping yesterday's prices.json")
    checks = {}
    for fn in CHECKS:
        try:
            checks[fn.__name__] = fn(master)
            print(f"[ok]   {fn.__name__}: {len(checks[fn.__name__])} cities")
        except Exception as e:
            print(f"[fail] {fn.__name__}: {e}", file=sys.stderr)
    missing = sorted(set(primary) - set(master))
    if missing:
        print(f"[info] {len(missing)} primary cities not in cities.csv (add lat/lon to show them): {missing[:15]}")

    out = DATA / "prices.json"
    previous = json.loads(out.read_text())["cities"] if out.exists() else []
    today = ist_today()
    result = build(master, primary, checks, previous, today)
    result["stations"] = station_prices(result)
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))

    hist = DATA / "history.csv"
    new = not hist.exists()
    with hist.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "city", "state", "fuel", "source", "price", "status"])
        for c in result["cities"]:
            for fuel in ("petrol", "diesel"):
                if c[fuel]:
                    for src, v in c[fuel]["sources"].items():
                        w.writerow([today, c["city"], c["state"], fuel, src, v, c[fuel]["status"]])
    print(f"{len(result['cities'])} cities {result['counts']}")
    for r in result["review"]:
        print("  REVIEW", r)


if __name__ == "__main__":
    main()
