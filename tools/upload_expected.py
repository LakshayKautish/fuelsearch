"""Upload today's expected price for every pump to Supabase, so driver reports can be sanity-checked.
Needs SUPABASE_URL and SUPABASE_SERVICE_KEY (the service-role key: keep it in GitHub Secrets, never in the site)."""
import json, os, sys
import requests

url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
if not url or not key:
    sys.exit("SUPABASE_URL / SUPABASE_SERVICE_KEY not set; skipping")
d = json.load(open("data/prices.json"))
rows = [{"station_id": s["id"], "fuel": f, "price": s[f]["price"], "asof": d["asof"]}
        for s in d.get("stations", []) for f in ("petrol", "diesel") if s.get(f)]
for i in range(0, len(rows), 1000):
    r = requests.post(f"{url}/rest/v1/station_expected", json=rows[i:i + 1000], timeout=60,
                      headers={"apikey": key, "Authorization": f"Bearer {key}",
                               "Prefer": "resolution=merge-duplicates"})
    r.raise_for_status()
print(f"uploaded {len(rows)} expected prices")
