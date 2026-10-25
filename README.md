# FuelSearch

Fuel prices across India down to the individual pump, on a map, with every price traced to its source.
Search a city or use your location, see every pump with today's price, cheapest first, and report the
price you saw.

## How a pump's price is decided (best first)

| Shown as | Where it comes from | Accuracy |
|---|---|---|
| **Pump price · oil company SMS** | `sms_pilot.py` texts the pump's dealer code to IOC/HPCL/BPCL each morning | Exact for that pump |
| **Reported by drivers** | The "Report the price you saw" form (Supabase). Shown only if within ₹3 of the official price | Exact, if recent |
| **{City} price · verified** | mypetrolprice city price, checked against CarDekho / GoodReturns / SMS | Within a few paise for IOC/HPCL/BPCL |
| **{Brand} {State} price** | `data/brands.csv` for Jio-bp, Nayara, Shell (they set their own) | State level |

City prices: **verified** (two sources within ₹0.30), **check at pump** (₹0.30–₹1.00 apart, both shown),
**held** (over ₹1.00 apart: yesterday's price shown, listed under `review` in `prices.json`), **1 source**.
CarDekho and GoodReturns publish identical figures, so they count as one check.

## Files

| File | What it does |
|---|---|
| `index.html`, `config.js` | The website. Loads `data/prices.json`; `config.js` holds the Supabase keys for reports |
| `scrape.py` | Daily: city prices + checks, prices every pump, writes `data/prices.json`, `data/history.csv` |
| `crawl_stations.py` | Weekly: builds `data/stations.csv` from the IOC, HPCL and Jio-bp locators, OpenStreetMap and CarDekho |
| `sms_pilot.py` | Morning SMS price checks for pumps with a dealer code (runs on an always-on machine with an Android phone) |
| `supabase/schema.sql` | Reports database: tables, rate limits, sanity checks, the public view the site reads |
| `tools/upload_expected.py` | Sends each pump's expected price to Supabase so odd reports get flagged |
| `tools/build_map.py` | Rebuilds the state map from the 2019 district boundaries |
| `data/cities.csv` | Cities with coordinates and CarDekho slugs |
| `data/stations.csv` | Every pump. Add `dealer_code` here to switch on SMS prices for that pump |
| `data/crawl_queue.txt` | States the weekly crawl works through, three per run |

## Launch checklist

1. **GitHub**: create a repository, upload this folder (including `.github`). Settings → Pages → deploy from `main`, root.
2. **Daily prices**: Actions → enable → run *daily-prices* once by hand.
3. **Pumps**: Actions → *weekly-stations* → run with `haryana/kaithal` to test, then let it work through `crawl_queue.txt`.
   A full first pass of India (~90,000 pumps at one request a second) takes a few weeks of weekly runs; run it more often while building up.
4. **Reports**: create a free Supabase project → SQL editor → run `supabase/schema.sql`.
   Put the project URL and **anon** key in `config.js`. Add `SUPABASE_URL` and the **service-role** key
   (`SUPABASE_SERVICE_KEY`) as GitHub Actions secrets: never put the service key in the site.
5. **SMS pilot (Kaithal)**: collect dealer codes from the boards at Kaithal pumps into `data/stations.csv`.
   Install an SMS gateway app on an Android phone with an Indian SIM, then schedule on an always-on machine:
   `python sms_pilot.py send Kaithal` at 6:05 AM IST and `python sms_pilot.py collect` at 6:20 AM IST.
   Send one test message to each price line first: HPCL's and BPCL's formats are not confirmed for 2026.
6. **Domain**: point your domain at GitHub Pages (Settings → Pages → custom domain). HTTPS is needed for "Use my location".

## Before going public

- Keep mypetrolprice's written permission on file; check CarDekho's and GoodReturns' terms for automated access, or ask them.
- Brand logos: add the files to `logos/` and list them in `data/logos.json` (see `logos/README.md`). Until then pumps show coloured brand badges.
- For heavy traffic, add a bot check (e.g. Cloudflare Turnstile) in front of `submit_report`.
- Review flagged reports and suggested dealer codes in Supabase (`reports where status = 'flagged'`, view `dealer_code_suggestions`).

## Credits

Boundaries: [Indian district boundaries 2019](https://github.com/guneetnarula/indian-district-boundaries) by Guneet Narula with the Datameet community.
Pumps: locator.iocl.com, petrolpump.hpretail.in, mobilitystation.jiobp.com, OpenStreetMap contributors (ODbL), cardekho.com.
Prices: mypetrolprice.com (with permission), cardekho.com, goodreturns.in, oil company SMS services.
