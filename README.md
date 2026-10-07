# SAM AI Pulse

**Every morning — the 10 papers, repos, open models and news an AI builder needs, in plain English, auto-filed into your RAG Airtable.**

## What it does daily (06:30 UTC)

1. **Scans** HuggingFace Daily Papers, arXiv RSS (cs.AI/cs.CL), GitHub Trending + topic search, HuggingFace trending models, Hacker News
2. **Scores** every candidate (traction + AI-topic signal), quality-gates the pool down to 10: 4 papers · 3 repos · 2 models · 1 news (backfills when a section is thin)
3. **Writes** a layman story per item (hook / what it is / why you should care / signal) with **Gemini 3.8 Flash** — one batched call, free tier
4. **Emails** a dark-themed digest from Gmail → iCloud
5. **Upserts** all 10 into the RAG Airtable (Papers / GitHub / RAG tables, canonical-URL dedupe — `HF Model` and `News` types auto-created via typecast)
6. **Remembers** the last 150 featured URLs (`state.json`) so nothing repeats

## Setup

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...   # Google AI Studio, free tier
export GMAIL_USER=...       # sender gmail
export GMAIL_APP_PASSWORD=  # 16-char app password (spaces stripped)
export EMAIL_TO=iamsamios@icloud.com
export RAG_AIRTABLE_API_KEY=...
export AIRTABLE_BASE_ID=app93PyZf4aDEE7BQ
python pulse.py --send      # omit --send for a dry run
```

Cron: `.github/workflows/daily.yml`. Duplicate-safe: Airtable `performUpsert` on `Canonical URL`.

Built by Anmol Chaudhary · stories by Gemini (free tier, ~15 calls/day of the 1,500/day quota)
