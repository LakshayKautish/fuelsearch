"""
FuelSearch station crawler: builds data/stations.csv (every pump: brand, name, address, coordinates).

Runs weekly (stations rarely change) and is resumable: outlet pages already fetched are kept in
data/station_cache.json, so each run continues where the last stopped. India has ~90,000 pumps;
at one request per second a full first crawl takes a few days of scheduled runs, then only new
outlets are fetched.

Sources
  IOC    locator.iocl.com            /location/{state}/{district}  -> outlet pages with lat/long
  HPCL   petrolpump.hpretail.in      same platform as IOCL
  JIO    mobilitystation.jiobp.com   same platform as IOCL
  BPCL, NAYARA, SHELL   OpenStreetMap (Overpass API), amenity=fuel with a brand tag
  Gaps   cardekho.com/fuel-stations/{town}  name + address, no coordinates (location=town)

Usage
  python crawl_stations.py --district haryana/kaithal          # one district (pilot)
  python crawl_stations.py --state haryana --max-pages 3000    # one state, capped per run
  python crawl_stations.py --osm haryana                       # OSM brands for a state
"""
import argparse, csv, json, re, sys, time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DATA = Path("data")
CACHE = DATA / "station_cache.json"
OUT = DATA / "stations.csv"
UA = {"User-Agent": "FuelSearchBot/0.3 (station directory; contact: you@example.com)"}
PAUSE = 1.0
FIELDS = ["id", "brand", "name", "address", "town", "district", "state", "pincode", "lat", "lon",
          "location", "phone", "source", "source_url", "dealer_code"]

LOCATORS = {   # brand -> (host, id prefix, brand words to strip from names)
    "IOC":  ("https://locator.iocl.com", "ioc", r"^indianoil[,\s]*"),
    "HPCL": ("https://petrolpump.hpretail.in", "hp", r"^hindustan petroleum corporation limited[,\s(]*(m/?s\.?\s*)?"),
    "JIO":  ("https://mobilitystation.jiobp.com", "jio", r"^jio-?bp[,\s]*"),
}
OSM_BRANDS = {"BPCL": r"bharat petroleum|bpcl", "NAYARA": r"nayara|essar", "SHELL": r"shell",
              "IOC": r"indian ?oil|iocl", "HPCL": r"hindustan petroleum|hpcl|\bhp\b", "JIO": r"jio|reliance"}


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    time.sleep(PAUSE)
    r.raise_for_status()
    return r.text


def load_cache():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def save_cache(c):
    CACHE.write_text(json.dumps(c, ensure_ascii=False))


# ---------------------------------------------------------------- locator platform
def listing_urls(host, path, max_pages):
    """Walk /location/... listing pages (state -> district -> locality, plus ?page=N) and
    yield outlet page URLs (they end in /Home)."""
    seen, queue, outlets = set(), [urljoin(host, "/location/" + path)], []
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            soup = BeautifulSoup(get(url), "html.parser")
        except Exception as e:
            print(f"  [list] {url}: {e}", file=sys.stderr)
            continue
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"]).split("#")[0]
            if urlparse(href).netloc != urlparse(host).netloc:
                continue
            p = urlparse(href).path
            if p.endswith("/Home") and href not in outlets:
                outlets.append(href)
            elif p.startswith("/location/" + path) and href not in seen:
                queue.append(href)
            elif "page=" in href and urlparse(href).path == urlparse(url).path and href not in seen:
                queue.append(href)
    return outlets


PLACEHOLDER_LOCALITIES = {"baraut"}   # IOC tags many Haryana pumps "Baraut"; the street address has the real town


def parse_outlet(html, url, brand):
    """Outlet pages carry schema.org JSON-LD (GasStation / LocalBusiness) with geo + address."""
    soup = BeautifulSoup(html, "html.parser")
    info = {}
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if isinstance(d, dict) and ("geo" in d or "address" in d):
                info = d
                break
        if info:
            break
    geo = info.get("geo") or {}
    adr = info.get("address") or {}
    lat = geo.get("latitude") or _meta(soup, "place:location:latitude")
    lon = geo.get("longitude") or _meta(soup, "place:location:longitude")
    if not lat:
        m = re.search(r'"latitude"\s*:\s*"?(-?\d+\.\d+)"?\s*,\s*"longitude"\s*:\s*"?(-?\d+\.\d+)', html)
        if m:
            lat, lon = m.groups()
    host, prefix, strip = LOCATORS[brand]
    raw = info.get("name") or (soup.title.get_text() if soup.title else "")
    name = re.sub(strip, "", raw, flags=re.I).strip(" ,()")
    num = re.search(r"-(\d{4,})/Home$", url)
    return {
        "id": f"{prefix}-{num.group(1) if num else abs(hash(url))}",
        "brand": brand, "name": name[:80],
        "address": adr.get("streetAddress", "") if isinstance(adr, dict) else str(adr),
        "town": (lambda t: "" if t.strip().lower() in PLACEHOLDER_LOCALITIES and "baghpat" not in url else t)(
            adr.get("addressLocality", "") if isinstance(adr, dict) else ""),
        "district": "", "state": adr.get("addressRegion", "") if isinstance(adr, dict) else "",
        "pincode": adr.get("postalCode", "") if isinstance(adr, dict) else "",
        "lat": lat or "", "lon": lon or "", "location": "exact" if lat else "town",
        "phone": info.get("telephone", ""), "source": urlparse(host).netloc, "source_url": url, "dealer_code": "",
    }


def _meta(soup, prop):
    m = soup.find("meta", attrs={"property": prop})
    return m["content"] if m and m.get("content") else None


def crawl_locator(brand, path, max_pages, cache):
    host = LOCATORS[brand][0]
    urls = listing_urls(host, path, max_pages)
    print(f"[{brand}] {len(urls)} outlets under {path}")
    district = path.split("/")[1].replace("-", " ").title() if "/" in path else ""
    for u in urls:
        if u in cache:
            continue
        try:
            st = parse_outlet(get(u), u, brand)
            st["district"] = st["district"] or district
            cache[u] = st
        except Exception as e:
            print(f"  [outlet] {u}: {e}", file=sys.stderr)
        if len(cache) % 50 == 0:
            save_cache(cache)
    save_cache(cache)


# ---------------------------------------------------------------- OpenStreetMap
def crawl_osm(state, cache):
    state = state.replace("-", " ")
    q = f"""[out:json][timeout:180];
area["ISO3166-2"~"^IN-"]["name"~"{state}",i]["admin_level"="4"]->.a;
nwr["amenity"="fuel"](area.a);out center tags;"""
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, headers=UA, timeout=240)
    r.raise_for_status()
    n = 0
    for el in r.json().get("elements", []):
        t = el.get("tags", {})
        label = " ".join([t.get("brand", ""), t.get("operator", ""), t.get("name", "")]).lower()
        brand = next((b for b, pat in OSM_BRANDS.items() if re.search(pat, label)), None)
        if not brand:
            continue
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        key = f"osm:{el['type']}/{el['id']}"
        cache[key] = {"id": f"osm-{el['type'][0]}{el['id']}", "brand": brand,
                      "name": t.get("name", brand), "address": t.get("addr:street", ""),
                      "town": t.get("addr:city", ""), "district": t.get("addr:district", ""),
                      "state": state.title(), "pincode": t.get("addr:postcode", ""), "lat": lat, "lon": lon,
                      "location": "exact", "phone": t.get("phone", ""), "source": "openstreetmap.org",
                      "source_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}", "dealer_code": ""}
        n += 1
    save_cache(cache)
    print(f"[osm] {n} branded pumps in {state}")


# ---------------------------------------------------------------- merge
def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("filling station", "").replace("kisan sewa kendra", "ksk"))


def write_csv(cache):
    """Merge cache with the existing CSV. Locator rows win over OSM/CarDekho for the same pump
    (same brand and phone, or same brand + pincode + name). Hand edits such as dealer codes survive."""
    rows = {}
    if OUT.exists():
        with OUT.open(newline="") as f:
            rows = {r["id"]: r for r in csv.DictReader(f)}
    rank = {"exact": 0, "town": 1}
    for st in cache.values():
        dup = next((r for r in rows.values() if r["brand"] == st["brand"] and (
            (st["phone"] and r["phone"] and st["phone"][-10:] == r["phone"][-10:]) or
            (st["pincode"] == r["pincode"] and norm(st["name"]) == norm(r["name"])))), None)
        if dup and dup["id"] != st["id"]:
            if rank.get(st["location"], 2) < rank.get(dup["location"], 2):
                st["dealer_code"] = st["dealer_code"] or dup.get("dealer_code", "")
                del rows[dup["id"]]
            else:
                continue
        keep = rows.get(st["id"], {}).get("dealer_code", "")
        rows[st["id"]] = {k: str(st.get(k, "") or "") for k in FIELDS}
        rows[st["id"]]["dealer_code"] = rows[st["id"]]["dealer_code"] or keep
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: (r["state"], r["district"], r["brand"], r["name"])))
    print(f"{len(rows)} stations in {OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--district", help="state/district path, e.g. haryana/kaithal")
    ap.add_argument("--state", help="state path, e.g. haryana")
    ap.add_argument("--osm", help="state name for OpenStreetMap brands, e.g. Haryana")
    ap.add_argument("--brands", default="IOC,HPCL,JIO")
    ap.add_argument("--max-pages", type=int, default=500, help="listing pages per brand per run")
    a = ap.parse_args()
    cache = load_cache()
    path = a.district or a.state
    if path:
        for b in a.brands.split(","):
            crawl_locator(b.strip(), path.lower(), a.max_pages, cache)
    if a.osm:
        crawl_osm(a.osm, cache)
    write_csv(cache)


if __name__ == "__main__":
    main()
