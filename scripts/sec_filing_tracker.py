import json
import os
import re
import time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import requests

USER_AGENT = "SEC-Filing-Tracker mis23ms@gmail.com"  # ← 只改這行
FORMS = {"10-K", "10-Q", "20-F", "8-K", "6-K"}
TICKERS_PATH = os.path.join("config", "tickers.json")
REPORTS_DIR = "reports"
INDEX_PATH = "index.md"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def http_get_json(url: str) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "From": USER_AGENT.split()[-1],  # email
        "Accept": "application/json",
    }

    for attempt in range(4):  # total 4 tries
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code in (403, 429, 500, 502, 503, 504):
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))  # 2s, 4s, 8s
                continue
        r.raise_for_status()
        return r.json()

    raise RuntimeError("Failed to fetch JSON after retries")


def load_tickers():
    with open(TICKERS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)
    # Normalize
    out = []
    for it in items:
        t = it.get("ticker", "").strip().upper()
        n = it.get("name", "").strip()
        if t and n:
            out.append({"ticker": t, "name": n})
    return out


def build_ticker_to_cik():
    # Official SEC mapping file (ticker -> CIK)
    # Requires proper User-Agent.
    data = http_get_json("https://www.sec.gov/files/company_tickers.json")
    mapping = {}
    for _, row in data.items():
        try:
            ticker = str(row["ticker"]).upper()
            cik = int(row["cik_str"])
            mapping[ticker] = cik
        except Exception:
            continue
    return mapping


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


def archive_url(cik: int, accession: str, primary_doc: str) -> str:
    cik_nolead = str(int(cik))
    acc_nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_nolead}/{acc_nodash}/{primary_doc}"


def parse_recent_filings(submissions: dict, cutoff: date):
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    accessions = recent.get("accessionNumber", []) or []
    primary_docs = recent.get("primaryDocument", []) or []

    filings = []
    n = min(len(forms), len(dates), len(accessions), len(primary_docs))
    for i in range(n):
        form = str(forms[i]).strip()
        if form not in FORMS:
            continue
        try:
            filed = datetime.strptime(str(dates[i]), "%Y-%m-%d").date()
        except Exception:
            continue
        if filed < cutoff:
            continue

        acc = str(accessions[i]).strip()
        pdoc = str(primary_docs[i]).strip()
        if not acc or not pdoc:
            continue

        filings.append(
            {
                "form": form,
                "filed": filed.isoformat(),
                "url": archive_url(int(submissions.get("cik", 0) or 0), acc, pdoc),
            }
        )

    # Newest first
    filings.sort(key=lambda x: x["filed"], reverse=True)
    return filings


def write_report(report_date: date, tickers, results):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{report_date.isoformat()}.md")

    lines = []
    lines.append(f"# SEC Filing Tracker — {report_date.isoformat()}")
    lines.append("")

    for item in tickers:
        t = item["ticker"]
        name = item["name"]
        lines.append(f"## {t} — {name}")
        filings = results.get(t, [])
        if filings:
            for f in filings:
                lines.append(f"- {f['form']} | Filed: {f['filed']} | 🔗 [Link]({f['url']})")
        else:
            lines.append("_No new filings in last 30 days_")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def update_index():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    files = [fn for fn in os.listdir(REPORTS_DIR) if DATE_RE.match(fn)]
    files.sort(reverse=True)  # newest first

    lines = ["# SEC Filing Tracker Reports", ""]
    for fn in files:
        date_str = fn.replace(".md", "")
        lines.append(f"- [{date_str}]({REPORTS_DIR}/{fn})")

    lines.append("")
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def cleanup_old_reports(today_tpe: date, keep_days: int = 30):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    cutoff = today_tpe - timedelta(days=keep_days)
    for fn in os.listdir(REPORTS_DIR):
        if not DATE_RE.match(fn):
            continue
        d_str = fn.replace(".md", "")
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if d < cutoff:
            os.remove(os.path.join(REPORTS_DIR, fn))


def main():
    now_tpe = datetime.now(ZoneInfo("Asia/Taipei"))
    report_date = now_tpe.date()
    cutoff = (now_tpe - timedelta(days=30)).date()

    tickers = load_tickers()

    ticker_to_cik = build_ticker_to_cik()

    results = {}
    for it in tickers:
        t = it["ticker"]
        cik = ticker_to_cik.get(t)
        if not cik:
            # Not in SEC mapping => no filings (e.g., non-US listings) -> still output "No new filings"
            results[t] = []
            continue

        subs = http_get_json(submissions_url(cik))
        time.sleep(0.2)
        # Ensure cik is present for URL building
        subs["cik"] = cik
        results[t] = parse_recent_filings(subs, cutoff)

    write_report(report_date, tickers, results)
    update_index()
    cleanup_old_reports(report_date, keep_days=30)


if __name__ == "__main__":
    main()
