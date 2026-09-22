# Asymmetric Dislocation Screener

Daily automated screen for special-situation and micro/small-cap dislocation
catalysts (spin-offs, tender offers, going-private deals, merger arbitrage)
using free SEC EDGAR endpoints plus a Yahoo Finance market snapshot, delivered
as an HTML email via the Gmail API.

## Setup

1. Clone the repo and install dependencies:

   ```bash
   git clone https://github.com/YOUR-USERNAME/dislocation-screener.git
   cd dislocation-screener
   python3 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install requests yfinance google-api-python-client google-auth-httplib2 google-auth-oauthlib
   ```

2. Create your personal config:

   ```bash
   cp config.example.py config.py
   ```

   Edit `config.py` with your real `EMAIL_TO`, `USER_AGENT` (SEC requires a
   real contact string), and watchlist tickers. This file is gitignored and
   will never be pushed.

3. Set up Gmail API OAuth credentials:

   - Go to [Google Cloud Console](https://console.cloud.google.com/) and create
     a project.
   - Enable the **Gmail API**.
   - Under **OAuth consent screen** (Google Auth Platform → Audience):
     - Set **User type** to **External**.
     - Keep **Publishing status** on **Testing**.
     - Add your Gmail address under **Test users**.
   - Create an **OAuth Client ID** (type: Desktop App) and download it as
     `credentials.json` into the project root. This file is gitignored.

4. Run it once manually to complete the OAuth consent flow:

   ```bash
   python3 dislocation_screen_v2.py
   ```

   This opens a browser for one-time authorization and caches a `token.json`
   (also gitignored) for future runs.

## Scheduling

**Linux/Mac (cron)** — runs daily at 6:30 AM:

```bash
crontab -e
# add:
30 6 * * * cd /path/to/dislocation-screener && NO_AT_BRIDGE=1 /path/to/venv/bin/python3 dislocation_screen_v2.py >> screen.log 2>&1
```

**Windows** — use Task Scheduler, daily trigger at 06:30, action pointing to
your venv's `python.exe` with the script path as the argument.

## Security Notes

- `credentials.json`, `token.json`, and `config.py` are all gitignored — never
  remove them from `.gitignore` or commit them manually.
- If you ever fork or share this repo publicly, double check `git log` doesn't
  contain any of those files from an earlier commit before the `.gitignore`
  was in place; removing a file later doesn't erase it from history.
- GitHub's push protection will block a push if it detects a recognizable
  secret pattern, but don't rely on it as your only safeguard.

## Data Sources

- SEC EDGAR full-text search (`efts.sec.gov`) — free, rate-limited to 10
  requests/second, requires a descriptive `User-Agent`.
- SEC EDGAR submissions API (`data.sec.gov/submissions`) — per-company filing
  history by CIK.
- Yahoo Finance via the unofficial `yfinance` package — directional market
  data only, not execution-grade.
