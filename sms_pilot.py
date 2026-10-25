"""
FuelSearch SMS price pilot: the exact price at each pump, straight from the oil company.

Every IOC / HPCL / BPCL pump shows its dealer code on a board at the forecourt. Texting that code to
the company's price line returns that pump's price for the day. This script does that for every pump
in data/stations.csv that has a dealer_code, and writes the replies to data/omc_sms.csv, which
scrape.py turns into "Pump price · SMS" on the site.

Price lines (send ONE test message to each before relying on them; formats can change):
  IOC   "RSP <dealer code>"      -> 9224992249   (published at iocl.com/petrol-diesel-price)
  HPCL  "HPPRICE <dealer code>"  -> 9222201122   (not re-confirmed for 2026: test first)
  BPCL  "RSP<dealer code>"       -> 9223112222   (not re-confirmed for 2026: test first)

What you need
  An Android phone with an Indian SIM running an SMS gateway app that exposes a local HTTP API,
  for example the open-source "SMS Gateway for Android" (sms-gate.app). Set GATEWAY_URL,
  GATEWAY_USER and GATEWAY_PASS below or as environment variables. Normal SMS charges apply:
  about one message per pump per day (Kaithal district: ~92 pumps).

Run on an always-on machine (a laptop, Raspberry Pi or small VPS on the same Wi-Fi as the phone):
  python sms_pilot.py send       # 6:05 AM IST: text every pump's dealer code
  python sms_pilot.py collect    # 6:20 AM IST: read replies, write data/omc_sms.csv, git push
Then the GitHub Action (6:30 AM IST) runs scrape.py and publishes the prices.

Collecting dealer codes
  They are not on the oil company websites. Note the code from the board at each pump (or ask the
  attendant) and put it in the dealer_code column of data/stations.csv. The site's "Report a price"
  form also asks for it, so drivers can fill these in over time.
"""
import csv, datetime as dt, json, os, re, subprocess, sys, time
from pathlib import Path

import requests

DATA = Path("data")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://192.168.1.50:8080")
AUTH = (os.environ.get("GATEWAY_USER", "sms"), os.environ.get("GATEWAY_PASS", "change-me"))
LINES = {
    "IOC":  ("9224992249", "RSP {code}"),
    "HPCL": ("9222201122", "HPPRICE {code}"),
    "BPCL": ("9223112222", "RSP{code}"),
}
SENT_LOG = DATA / "sms_sent.json"
PAUSE = 3.0        # seconds between messages; stay well under any rate limit on the price lines


def ist_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))


def stations_with_codes(district=None):
    with (DATA / "stations.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            if r["brand"] in LINES and r.get("dealer_code", "").strip() and (not district or r["district"].lower() == district.lower()):
                yield r


def gateway_send(number, text):
    """SMS Gateway for Android local API: POST /message. Adjust here if you use another app."""
    r = requests.post(f"{GATEWAY_URL}/message", auth=AUTH, timeout=20,
                      json={"textMessage": {"text": text}, "phoneNumbers": [f"+91{number}"]})
    r.raise_for_status()
    return r.json().get("id")


def gateway_inbox(since):
    """Incoming messages since a time. The app can also POST each reply to a webhook instead;
    if you use that, write the webhook's rows straight into data/omc_sms_inbox.jsonl."""
    inbox = DATA / "omc_sms_inbox.jsonl"
    if inbox.exists():
        for line in inbox.read_text().splitlines():
            m = json.loads(line)
            if m.get("receivedAt", "") >= since.isoformat():
                yield m
    else:
        r = requests.get(f"{GATEWAY_URL}/messages", auth=AUTH, timeout=20, params={"since": since.isoformat()})
        r.raise_for_status()
        yield from r.json()


def send(district=None):
    sent = []
    for st in stations_with_codes(district):
        number, fmt = LINES[st["brand"]]
        text = fmt.format(code=st["dealer_code"].strip())
        try:
            gateway_send(number, text)
            sent.append({"dealer_code": st["dealer_code"].strip(), "brand": st["brand"], "number": number,
                         "city": st["district"], "at": ist_now().isoformat()})
            print(f"sent {text!r} to {number} ({st['name']})")
        except Exception as e:
            print(f"[fail] {st['name']}: {e}", file=sys.stderr)
        time.sleep(PAUSE)
    SENT_LOG.write_text(json.dumps({"date": ist_now().date().isoformat(), "sent": sent}))
    print(f"{len(sent)} price checks sent")


def collect(push=True):
    log = json.loads(SENT_LOG.read_text())
    today = log["date"]
    start = dt.datetime.fromisoformat(min(s["at"] for s in log["sent"])) if log["sent"] else ist_now()
    by_number = {}
    for s in log["sent"]:
        by_number.setdefault(s["number"], []).append(s)
    rows = []
    for m in gateway_inbox(start):
        sender = re.sub(r"\D", "", m.get("phoneNumber", ""))[-10:]
        text = m.get("message", "") or m.get("text", "")
        if sender not in by_number:
            continue
        # replies usually quote the dealer code; match on it, else take the next unmatched request
        code = next((s["dealer_code"] for s in by_number[sender] if s["dealer_code"] in text), None)
        req = next((s for s in by_number[sender] if s["dealer_code"] == code), None) or \
              next((s for s in by_number[sender] if not s.get("done")), None)
        if not req:
            continue
        req["done"] = True
        rows.append({"date": today, "dealer_code": req["dealer_code"], "company": req["brand"],
                     "city": req["city"], "reply": text.replace("\n", " ")})
    path = DATA / "omc_sms.csv"
    keep = []
    if path.exists():
        with path.open(newline="") as f:
            keep = [r for r in csv.DictReader(f) if r["date"] != today]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "dealer_code", "company", "city", "reply"])
        w.writeheader()
        w.writerows(keep[-5000:] + rows)
    print(f"{len(rows)} replies matched out of {len(log['sent'])} sent")
    if push and rows:
        subprocess.run(["git", "add", str(path)], check=True)
        subprocess.run(["git", "commit", "-m", f"SMS pump prices {today}"], check=False)
        subprocess.run(["git", "push"], check=False)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "send":
        send(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "collect":
        collect(push="--no-push" not in sys.argv)
    else:
        sys.exit("usage: python sms_pilot.py send [district] | collect [--no-push]")
