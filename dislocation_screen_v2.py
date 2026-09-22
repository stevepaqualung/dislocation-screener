"""
Asymmetric Market Dislocation Analyst - Daily Data Pull & Email Digest
========================================================================
v2: adds direct hyperlinks to every referenced SEC filing and ticker quote.

Pulls:
  1. SEC EDGAR full-text search: recent 8-K / S-1 / Form 10 filings mentioning
     spin-off, separation, merger, tender offer, going-private keywords (free,
     no API key -- uses SEC's public efts.sec.gov endpoint).
  2. SEC EDGAR submissions feed for a watchlist of tickers you're tracking
     (e.g. post-spinoff names) to catch new 8-Ks/13-Ds.
  3. Yahoo Finance (yfinance) snapshot for a small/micro-cap watchlist:
     market cap, volume, short interest proxy, price vs. moving averages.
  4. Assembles everything into an HTML digest -- every filing and ticker is a
     clickable hyperlink straight to the EDGAR document or Yahoo Finance page --
     and sends it via the Gmail API.

Setup required (one-time):
  pip install requests yfinance google-api-python-client google-auth-httplib2 google-auth-oauthlib
  1. Go to console.cloud.google.com -> create project -> enable "Gmail API"
  2. OAuth consent screen: set User type = External, Publishing status = Testing,
     and add your Gmail address under Test users (Internal mode will throw
     "Access blocked: ... can only be used within its organization").
  3. Create OAuth Client ID (type: Desktop App) -> download as credentials.json
     into the same folder as this script.
  4. First run will open a browser to authorize; it caches token.json after that.
  5. Set EMAIL_TO below (and EMAIL_FROM if different from your authorized account).

Schedule it:
  - Linux/Mac: cron entry, e.g.  NO_AT_BRIDGE=1 30 6 * * * /usr/bin/python3 /path/to/this_script.py
    (NO_AT_BRIDGE=1 silences the harmless GTK "atk-bridge" warning during the
    browser-based OAuth step; not needed once token.json exists and no browser opens.)
  - Windows: Task Scheduler, daily at 06:30.

SEC EDGAR fair-access rules: max 10 requests/second, always send a descriptive
User-Agent with contact info (SEC will block generic/blank User-Agents).
"""

import base64
import json
import os
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from config import USER_AGENT, EMAIL_TO, EMAIL_SUBJECT_PREFIX, WATCHLIST

import requests

# ---------------------------------------------------------------------------
# CONFIG - edit these
# ---------------------------------------------------------------------------
USER_AGENT = "AsymmetricDislocationAnalyst steven.romero@gmail.com"  # SEC requires contact info
EMAIL_TO = "steven.romero@gmail.com"          # <-- set your Gmail address
EMAIL_SUBJECT_PREFIX = "Asymmetric Dislocation Daily Screen"

FTS_KEYWORDS = [
    "spin-off",
    "special committee",
    "tender offer",
    "going private",
    "reverse morris trust",
    "strategic alternatives",
]
FTS_FORM_TYPES = ["8-K", "SC 13D", "SC TO-T", "PREM14A", "DEFM14A", "10-12B"]

WATCHLIST = ["VYLR", "FLEX", "PETS", "FTHM", "PLNH", "NEUP", "TORO", "MOD", "THRM"]

CIK_MAP_FILE = "ticker_cik_map.json"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def edgar_filing_index_url(cik, accession_no):
    """Build the EDGAR filing index page URL (lists all documents in the filing)."""
    cik_int = str(int(cik))  # strips leading zeros
    accn_nodash = accession_no.replace("-", "")
    accn_dash = accession_no
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{accn_dash}-index.htm"


def edgar_document_url(cik, accession_no, filename):
    """Build a direct link to the primary document within a filing, if known."""
    cik_int = str(int(cik))
    accn_nodash = accession_no.replace("-", "")
    if filename:
        return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{filename}"
    return edgar_filing_index_url(cik, accession_no)


def yahoo_finance_url(ticker):
    return f"https://finance.yahoo.com/quote/{ticker}"


# ---------------------------------------------------------------------------
# 1. SEC EDGAR full-text search (catalyst discovery)
# ---------------------------------------------------------------------------
def edgar_full_text_search(keyword, form_types=None, days_back=1):
    """Query SEC's free full-text search API for a keyword in recent filings."""
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days_back)
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": f'"{keyword}"',
        "dateRange": "custom",
        "startdt": start_date.isoformat(),
        "enddt": end_date.isoformat(),
    }
    if form_types:
        params["forms"] = ",".join(form_types)

    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"keyword": keyword, "error": str(e), "hits": []}

    hits = []
    for h in data.get("hits", {}).get("hits", [])[:10]:
        src = h.get("_source", {})
        # _id looks like "0001193125-26-123456:doc-name.htm"
        raw_id = h.get("_id", "")
        accession_no, _, filename = raw_id.partition(":")
        ciks = src.get("cik") or src.get("ciks") or []
        cik = ciks[0] if isinstance(ciks, list) and ciks else (ciks or None)

        link = None
        if cik and accession_no:
            link = edgar_document_url(cik, accession_no, filename)

        hits.append({
            "company": src.get("display_names", ["?"])[0] if src.get("display_names") else "?",
            "form": src.get("root_form", src.get("file_type", "?")),
            "filed": src.get("file_date", "?"),
            "accession": accession_no or "?",
            "url": link,
        })
    return {"keyword": keyword, "hits": hits}


def scan_catalyst_keywords():
    results = []
    for kw in FTS_KEYWORDS:
        results.append(edgar_full_text_search(kw, FTS_FORM_TYPES, days_back=2))
        time.sleep(0.15)  # stay well under 10 req/sec
    return results


# ---------------------------------------------------------------------------
# 2. Watchlist filing tracker via submissions API
# ---------------------------------------------------------------------------
def load_or_build_cik_map(tickers):
    if os.path.exists(CIK_MAP_FILE):
        with open(CIK_MAP_FILE) as f:
            cache = json.load(f)
    else:
        cache = {}

    missing = [t for t in tickers if t not in cache]
    if missing:
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=15
        )
        resp.raise_for_status()
        all_companies = resp.json()
        ticker_to_cik = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in all_companies.values()
        }
        for t in missing:
            if t.upper() in ticker_to_cik:
                cache[t] = ticker_to_cik[t.upper()]
        with open(CIK_MAP_FILE, "w") as f:
            json.dump(cache, f)
    return cache


def recent_filings_for_ticker(ticker, cik, days_back=2):
    headers = {"User-Agent": USER_AGENT}
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "filings": []}

    recent = data.get("filings", {}).get("recent", {})
    cutoff = (datetime.utcnow().date() - timedelta(days=days_back)).isoformat()
    filings = []
    for form, date, accn, doc in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if date >= cutoff:
            filings.append({
                "form": form,
                "date": date,
                "accession": accn,
                "doc": doc,
                "url": edgar_document_url(cik, accn, doc),
            })
    return {"ticker": ticker, "filings": filings}


def scan_watchlist_filings():
    cik_map = load_or_build_cik_map(WATCHLIST)
    results = []
    for t in WATCHLIST:
        cik = cik_map.get(t)
        if not cik:
            results.append({"ticker": t, "error": "CIK not found", "filings": []})
            continue
        results.append(recent_filings_for_ticker(t, cik))
        time.sleep(0.15)
    return results


# ---------------------------------------------------------------------------
# 3. Market data snapshot (yfinance) - size, liquidity, momentum context
# ---------------------------------------------------------------------------
def market_snapshot(tickers):
    import yfinance as yf

    rows = []
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            info = tk.fast_info
            hist = tk.history(period="1mo")
            if hist.empty:
                continue
            price = info.get("lastPrice") or hist["Close"].iloc[-1]
            mcap = info.get("marketCap")
            avg_vol = hist["Volume"].mean()
            pct_1m = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
            rows.append({
                "ticker": t,
                "price": round(float(price), 2) if price else None,
                "market_cap_m": round(mcap / 1e6, 1) if mcap else None,
                "avg_vol_30d": int(avg_vol) if avg_vol else None,
                "pct_move_1m": round(pct_1m, 1),
                "url": yahoo_finance_url(t),
            })
        except Exception as e:
            rows.append({"ticker": t, "error": str(e), "url": yahoo_finance_url(t)})
    return rows


# ---------------------------------------------------------------------------
# 4. Build HTML digest (with hyperlinks)
# ---------------------------------------------------------------------------
def _link(text, url):
    if url:
        return f'<a href="{url}">{text}</a>'
    return text


def build_html_digest(catalyst_hits, watchlist_filings, market_data):
    today = datetime.now().strftime("%A, %B %d, %Y")
    html = [f"<h2>Asymmetric Dislocation Screen &mdash; {today}</h2>"]

    html.append("<h3>1. New Catalyst Filings (EDGAR Full-Text Search)</h3>")
    any_hits = False
    for block in catalyst_hits:
        if block.get("hits"):
            any_hits = True
            html.append(f"<b>Keyword: {block['keyword']}</b><ul>")
            for h in block["hits"]:
                label = f"{h['company']} &mdash; {h['form']} filed {h['filed']}"
                html.append(f"<li>{_link(label, h.get('url'))}</li>")
            html.append("</ul>")
    if not any_hits:
        html.append("<p>No new matching filings in the lookback window.</p>")

    html.append("<h3>2. Watchlist Filing Activity</h3>")
    any_wl = False
    for w in watchlist_filings:
        if w.get("filings"):
            any_wl = True
            html.append(f"<b>{_link(w['ticker'], yahoo_finance_url(w['ticker']))}</b><ul>")
            for f in w["filings"]:
                label = f"{f['form']} filed {f['date']}"
                html.append(f"<li>{_link(label, f.get('url'))}</li>")
            html.append("</ul>")
    if not any_wl:
        html.append("<p>No new watchlist filings in the lookback window.</p>")

    html.append("<h3>3. Market Snapshot</h3>")
    html.append(
        "<table border='1' cellpadding='4' cellspacing='0'>"
        "<tr><th>Ticker</th><th>Price</th><th>Mkt Cap ($M)</th>"
        "<th>Avg Vol (30d)</th><th>1M % Move</th></tr>"
    )
    for row in market_data:
        ticker_link = _link(row["ticker"], row.get("url"))
        if "error" in row:
            html.append(f"<tr><td>{ticker_link}</td><td colspan='4'>error: {row['error']}</td></tr>")
            continue
        html.append(
            f"<tr><td>{ticker_link}</td><td>{row.get('price')}</td>"
            f"<td>{row.get('market_cap_m')}</td><td>{row.get('avg_vol_30d')}</td>"
            f"<td>{row.get('pct_move_1m')}%</td></tr>"
        )
    html.append("</table>")

    return "\n".join(html)


# ---------------------------------------------------------------------------
# 5. Send via Gmail API
# ---------------------------------------------------------------------------
def send_gmail(subject, html_body, to_addr):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    message = MIMEText(html_body, "html")
    message["to"] = to_addr
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    print("Scanning EDGAR full-text search for catalyst keywords...")
    catalyst_hits = scan_catalyst_keywords()

    print("Checking watchlist tickers for new filings...")
    watchlist_filings = scan_watchlist_filings()

    print("Pulling market snapshot via yfinance...")
    market_data = market_snapshot(WATCHLIST)

    print("Building digest...")
    html_body = build_html_digest(catalyst_hits, watchlist_filings, market_data)

    subject = f"{EMAIL_SUBJECT_PREFIX} - {datetime.now().strftime('%Y-%m-%d')}"
    print("Sending email...")
    send_gmail(subject, html_body, EMAIL_TO)
    print("Done.")


if __name__ == "__main__":
    main()
