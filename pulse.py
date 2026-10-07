"""
SAM AI Pulse — unified daily digest.
Sources: HF Daily Papers, arxiv RSS, GitHub trending+search, HF trending models, HN.
-> quality-gated 10 -> Gemini layman stories -> Gmail HTML email -> Airtable RAG upsert.
Zero paid deps beyond the free Gemini key. Env: GEMINI_API_KEY, GMAIL_USER,
GMAIL_APP_PASSWORD, EMAIL_TO, RAG_AIRTABLE_API_KEY (or AIRTABLE_API_KEY), AIRTABLE_BASE_ID,
GITHUB_TOKEN (optional, raises GitHub rate limits).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

BASE = Path(__file__).parent
STATE = BASE / "state.json"
TODAY = dt.date.today().isoformat()

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

GMAIL_USER = os.environ.get("GMAIL_USER", "").strip()
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "iamsamios@icloud.com").strip()
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "app93PyZf4aDEE7BQ").strip()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and not os.environ.get(k):
            os.environ[k] = v


load_dotenv(Path.home() / ".omp/profiles/sam/agent/.env")
AT_TOKEN = (os.environ.get("RAG_AIRTABLE_API_KEY") or os.environ.get("AIRTABLE_API_KEY") or "").strip()

T_RAG, T_PAPERS, T_GITHUB = "tblGFrHOp8QPPpTmD", "tblf4BK3M6U05K06Q", "tblOlCzt9Yuh1knSB"
AT_HDR = {"Authorization": f"Bearer {AT_TOKEN}", "Content-Type": "application/json"}

TOTAL = int(os.environ.get("PULSE_TOTAL", "10"))
QUOTA = {"paper": 4, "repo": 3, "model": 2, "news": 1}
HDR = {"User-Agent": "sam-ai-pulse/1.0"}
AI_WORDS = w = (
    "llm|language model|agent|agentic|reasoning|transformer|diffusion|multimodal|mcp\\b|rag\\b|"
    "retrieval|fine.?tun|inference|robot|benchmark|alignment|quantiz|vision.?language|diffusion|"
    "reinforcement|embedding|grpo|rlhf|openai|anthropic|claude|gemini|qwen|llama|mistral|deepseek|kimi|gpt"
)
AI_RE = re.compile(AI_WORDS, re.I)


def cats(title: str, text: str) -> str:
    t = f"{title} {text}".lower()
    if re.search(r"agent|mcp|tool.?use|orchestrat", t):
        return "AI Agents"
    if re.search(r"code|program|software|terminal|ide|copilot", t):
        return "Coding+Learning"
    if re.search(r"bio|protein|genom|drug|chemic", t):
        return "Bioinformatics"
    if re.search(r"robot|embodied|world model|video|3d|quantum", t):
        return "Future Tech"
    return "General"


# ── SOURCES ─────────────────────────────────────────────────────────────────
def src_hf_daily() -> list[dict]:
    out = []
    try:
        r = requests.get("https://huggingface.co/api/daily_papers", headers=HDR, timeout=25)
        for p in r.json()[:40]:
            pp = p.get("paper", {})
            aid = pp.get("id", "")
            if not aid:
                continue
            out.append({
                "kind": "paper", "title": pp.get("title", "").strip(),
                "url": f"https://arxiv.org/abs/{aid}", "canon": f"https://arxiv.org/abs/{aid}",
                "summary": (pp.get("summary") or "").strip(), "topics": ",".join((pp.get("title", "").split())[:8]),
                "date": (pp.get("publishedAt") or TODAY)[:10], "likes": p.get("numLikes", 0) or 0,
            })
    except Exception as e:
        print(f"[pulse] hf_daily err: {e}")
    return out


def src_arxiv() -> list[dict]:
    out = []
    for cs in ("cs.AI", "cs.CL"):
        try:
            r = requests.get(f"http://export.arxiv.org/rss/{cs}", headers=HDR, timeout=25)
            for item in ET.fromstring(r.text).findall("{http://purl.org/rss/1.0/}item")[:20]:
                title = (item.findtext("{http://purl.org/rss/1.0/}title") or "").strip().replace("\n", " ")
                link = (item.findtext("{http://purl.org/rss/1.0/}link") or "").strip()
                link = link.replace("/pdf/", "/abs/").split("#")[0].split("?")[0]
                summ = (item.findtext("{http://purl.org/rss/1.0/}description") or "").strip()
                if title and link:
                    out.append({"kind": "paper", "title": title, "url": link, "canon": link,
                                "summary": re.sub(r"<[^>]+>", "", summ)[:3000], "topics": cs,
                                "date": TODAY, "likes": 0})
        except Exception as e:
            print(f"[pulse] arxiv {cs} err: {e}")
    return out


def src_github_trending() -> list[dict]:
    out = []
    try:
        r = requests.get("https://github.com/trending?since=daily", headers=HDR, timeout=25)
        for art in re.findall(r'<article class="Box-row">(.*?)</article>', r.text, re.S)[:25]:
            m = re.search(r'href="/([^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*', art)
            name_m = re.search(r'<h2[^>]*>.*?/([^/]+/[^<\s]+)', art, re.S)
            full = (name_m.group(1).replace("\n", "").strip() if name_m else None)
            if not full:
                hm = re.search(r'h1[^>]*>\s*<a href="/([^"]+)"', art, re.S)
                full = hm.group(1).strip() if hm else None
            if not full or "/" not in full:
                continue
            desc_m = re.search(r'<p class="col-9[^"]*">(.*?)</p>', art, re.S)
            full = re.sub(r"\s+", "", full)
            if not re.match(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$", full):
                continue
            stars_m = re.search(r'([\d,]+)\s*stars\s*today', art)
            tot_m = re.findall(r'([\d,]+)</a>', art)
            lang_m = re.search(r'itemprop="programmingLanguage">([^<]+)', art)
            out.append({"kind": "repo", "title": full, "url": f"https://github.com/{full}",
                        "canon": f"https://github.com/{full.lower()}",
                        "summary": html.unescape(re.sub(r"<[^>]+>", "", desc_m.group(1)).strip()) if desc_m else "",
                        "topics": "", "date": TODAY,
                        "stars_today": int((stars_m.group(1) if stars_m else "0").replace(",", "")),
                        "stars": int((tot_m[0] if tot_m else "0").replace(",", "")),
                        "lang": lang_m.group(1) if lang_m else ""})
    except Exception as e:
        print(f"[pulse] gh trending err: {e}")
    return out


def src_github_search() -> list[dict]:
    if not GH_TOKEN:
        return []
    out = []
    since = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    for q in ("topic:llm", "topic:ai-agents", "topic:mcp"):
        try:
            r = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": f"{q} created:>{since} stars:>30", "sort": "stars", "order": "desc", "per_page": 10},
                headers={**HDR, "Authorization": f"Bearer {GH_TOKEN}"}, timeout=25)
            for it in r.json().get("items", []):
                out.append({"kind": "repo", "title": it["full_name"], "url": it["html_url"],
                            "canon": it["html_url"].lower(), "summary": it.get("description") or "",
                            "topics": ",".join(it.get("topics", [])[:6]), "date": TODAY,
                            "stars_today": 0, "stars": it.get("stargazers_count", 0),
                            "lang": it.get("language") or ""})
        except Exception as e:
            print(f"[pulse] gh search {q} err: {e}")
    return out


def src_hf_models() -> list[dict]:
    out = []
    try:
        r = requests.get("https://huggingface.co/api/models",
                         params={"sort": "likes7d", "direction": -1, "limit": 30, "full": "false"},
                         headers=HDR, timeout=25)
        for m in r.json():
            mid = m.get("id", "")
            lk7 = m.get("likes7d", m.get("likes", 0)) or 0
            if lk7 < 5:
                continue
            tags = m.get("tags", [])
            task = next((t.split(":", 1)[1] for t in tags if t.startswith("pipeline_tag:")), "")
            out.append({"kind": "model", "title": mid, "url": f"https://huggingface.co/{mid}",
                        "canon": f"https://huggingface.co/{mid.lower()}",
                        "summary": f"{task or 'model'} | tags: {', '.join(tags[:8])} | downloads: {m.get('downloads', 0):,}",
                        "topics": ",".join(tags[:8]), "date": (m.get("lastModified") or TODAY)[:10], "likes": lk7})
    except Exception as e:
        print(f"[pulse] hf models err: {e}")
    return out


def src_hn() -> list[dict]:
    out = []
    try:
        ts = int((dt.datetime.now() - dt.timedelta(hours=30)).timestamp())
        r = requests.get("https://hn.algolia.com/api/v1/search",
                         params={"query": "AI LLM agent open model", "tags": "story",
                                 "numericFilters": f"points>40,created_at_i>{ts}", "hitsPerPage": 15},
                         headers=HDR, timeout=25)
        for h in r.json().get("hits", []):
            title = h.get("title", "")
            if not AI_RE.search(title):
                continue
            link = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
            out.append({"kind": "news", "title": title, "url": link, "canon": link,
                        "summary": f"Hacker News | {h.get('points',0)} points | {h.get('num_comments',0)} comments",
                        "topics": "hacker-news", "date": TODAY, "likes": h.get("points", 0)})
    except Exception as e:
        print(f"[pulse] hn err: {e}")
    return out


# ── SCORE + PICK ────────────────────────────────────────────────────────────
def score(c: dict) -> float:
    boost = len(AI_RE.findall(f"{c['title']} {c['summary']} {c['topics']}")) * 3
    if c["kind"] == "paper":
        return (c.get("likes", 0) * 6) + boost + 15
    if c["kind"] == "repo":
        return (c.get("stars_today", 0) * 2) + (min(c.get("stars", 0), 50000) / 250) + boost + 10
    if c["kind"] == "model":
        return (c.get("likes", 0) / 8) + boost + 12
    return c.get("likes", 0) / 4 + boost


def pick10(cands: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for c in cands:
        k = c["canon"].rstrip("/").lower()
        if k not in by or score(c) > score(by[k]):
            by[k] = c
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    sent = set(st.get("sent_canon", []))
    fresh = [c for c in by.values() if c["canon"].rstrip("/").lower() not in sent]
    pool = fresh if len(fresh) >= TOTAL else list(by.values())
    for c in pool:
        c["score"] = score(c)
    buckets = {k: sorted([c for c in pool if c["kind"] == k], key=lambda x: -x["score"]) for k in QUOTA}
    picked: list[dict] = []
    for kind, n in QUOTA.items():
        picked.extend(buckets[kind][:n])
    deficit = TOTAL - len(picked)
    if deficit > 0:  # quality-gated backfill from papers/repos pool
        rest = sorted([c for c in pool if c not in picked], key=lambda x: -x["score"])
        picked.extend(rest[:deficit])
    order = {"paper": 0, "repo": 1, "model": 2, "news": 3}
    return sorted(picked[:TOTAL], key=lambda c: (order[c["kind"]], -c["score"]))


# ── GEMINI ──────────────────────────────────────────────────────────────────
# Model chain is ground-truthed against the key's own /v1beta/models list
# (gemini-3.8-flash was a bogus redirect string and is NOT offered -> 503s).
_MODEL_CHAIN = [m for m in (
    os.environ.get("GEMINI_MODEL", "").strip(),
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
) if m]


def gemini(system: str, user: str, max_tokens: int = 4096) -> str:
    """Rotate through the key-offered model chain on 503/timeout/empty content.
    Free tier occasionally 503s under load; short jitter + model hop absorbs it."""
    payload_msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
    last_exc: Exception | None = None
    for model in _MODEL_CHAIN:
        for attempt in range(2):
            try:
                r = requests.post(
                    GEMINI_URL,
                    headers={"Authorization": f"Bearer {GEMINI_KEY}", "Content-Type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens, "messages": payload_msgs},
                    timeout=(20, 150))
                r.raise_for_status()
                data = r.json()
                content = (data["choices"][0]["message"].get("content") or "").strip()
                if content:
                    if model != _MODEL_CHAIN[0]:
                        print(f"[pulse] note: stories via fallback model {model}")
                    return content
                last_exc = RuntimeError(f"{model}: empty content (finish={data['choices'][0].get('finish_reason')})")
            except Exception as exc:  # 503 / timeout / transient
                last_exc = exc
            time.sleep(4 + attempt * 6)
    raise RuntimeError(f"all gemini models failed: {last_exc}")


STORY_SYS = """You write "SAM AI Pulse" — a daily 10-item AI briefing for a smart NON-specialist founder.
For each item return a mini story. Voice: a brilliant friend explaining over coffee. No hype words (revolutionary, game-changing, groundbreaking).
Fields per item:
- "n": item number (must echo input)
- "hook": ONE sentence, max 18 words — the surprising or exciting thing.
- "story": 3-4 sentences in plain English — what it is, what problem it solves, why a layperson should care. Name the concrete technique/benchmark. No jargon without a gloss.
- "signal": ONE sentence — the traction fact (stars/upvotes/downloads/points) and what that signals.
Return ONLY a JSON array of objects, no markdown fences."""

INTRO_SYS = """You are the editor of "SAM AI Pulse". Given today's 10 items, write 2 punchy sentences (max 45 words total):
what the AI frontier actually did today and the one thread connecting the day. No hype. Plain English."""


def write_stories(items: list[dict]) -> None:
    brief = [{"n": i + 1, "kind": c["kind"], "title": c["title"], "url": c["url"],
              "summary": c["summary"][:600], "topics": c.get("topics", "")[:120],
              "likes": c.get("likes", 0), "stars_today": c.get("stars_today", 0),
              "stars": c.get("stars", 0)} for i, c in enumerate(items)]
    stories: dict[int, dict] = {}
    try:
        raw = gemini(STORY_SYS, json.dumps(brief, ensure_ascii=False))
        m = re.search(r"\[.*\]", raw, re.S)
        for s in json.loads(m.group(0)):
            stories[int(s.get("n", 0))] = s
    except Exception as e:
        print(f"[pulse] stories batch err: {e}; retrying one-by-one")
        for b in brief:
            try:
                raw = gemini(STORY_SYS, json.dumps([b], ensure_ascii=False), 700)
                m = re.search(r"\[.*\]", raw, re.S)
                s = json.loads(m.group(0))[0]
                stories[b["n"]] = s
            except Exception as e2:
                print(f"[pulse] item {b['n']} story err: {e2}")
    ok = 0
    for i, c in enumerate(items):
        s = stories.get(i + 1) or {}
        if s.get("story"):
            ok += 1
        c["hook"] = s.get("hook") or c["title"][:110]
        c["story"] = s.get("story") or (c["summary"][:350] + ("…" if len(c["summary"]) > 350 else ""))
        c["signal"] = s.get("signal") or ""
    print(f"[pulse] stories model-written: {ok}/{len(items)}")


def write_intro(items: list[dict]) -> str:
    try:
        return gemini(INTRO_SYS, "\n".join(f"{i+1}. {c['title']}" for i, c in enumerate(items)), 160).strip()
    except Exception as e:
        print(f"[pulse] intro err: {e}")
        return ""


# ── EMAIL ───────────────────────────────────────────────────────────────────
SEC = {"paper": ("PAPERS", "#60a5fa"), "repo": ("REPOS", "#4ade80"),
       "model": ("OPEN MODELS", "#f59e0b"), "news": ("NEWS", "#f472b6")}


def item_html(c: dict, n: int) -> str:
    label, color = SEC[c["kind"]]
    badge = f'<span style="background:{color}22;color:{color};padding:2px 9px;border-radius:10px;font-size:10px;font-weight:700;letter-spacing:.08em;">{label}</span>'
    stars = f' · ⭐ {c["stars"]:,} (+{c["stars_today"]} today)' if c.get("stars") else ""
    likes = f' · ▲ {c["likes"]}' if c.get("likes") else ""
    return f"""
<div style="margin:26px 0;padding:20px 22px;background:#12161f;border:1px solid #1e2635;border-radius:12px;">
  <div style="margin-bottom:8px;"><span style="color:#64748b;font-weight:700;font-size:13px;">#{n}</span> {badge}
    <span style="color:#475569;font-size:11px;float:right;margin-top:2px;">{html.escape(c.get("date",""))}</span></div>
  <a href="{c['url']}" style="color:#e2e8f0;font-size:17px;font-weight:700;text-decoration:none;line-height:1.35;">{html.escape(c['title'])}</a>
  <p style="color:#8b95a7;font-size:13.5px;font-style:italic;margin:8px 0 4px;">{html.escape(c['hook'])}</p>
  <p style="color:#cbd5e1;font-size:14.5px;line-height:1.75;margin:8px 0;">{html.escape(c['story'])}</p>
  <p style="color:#64748b;font-size:12.5px;margin:6px 0 0;"><strong style="color:#94a3b8;">Signal:</strong> {html.escape(c['signal'])}{stars}{likes}</p>
</div>"""


def build_html(issue: int, items: list[dict], intro: str, scanned: int) -> str:
    body = "".join(item_html(c, i) for i, c in enumerate(items, 1))
    date_str = dt.date.today().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#0b0e14;padding:26px 12px;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
<div style="max-width:640px;margin:0 auto;">
  <div style="padding:26px 24px 18px;background:linear-gradient(135deg,#111827,#1e1b4b);border-radius:14px;border:1px solid #263149;">
    <div style="color:#818cf8;font-size:11px;font-weight:800;letter-spacing:.22em;">SAM AI PULSE · ISSUE #{issue:03d} · {date_str.upper()}</div>
    <h1 style="color:#f1f5f9;font-size:23px;margin:10px 0 6px;">Today's 10, from {scanned} scanned</h1>
    <p style="color:#a5b4d4;font-size:14.5px;line-height:1.65;margin:0;">{html.escape(intro)}</p>
  </div>
  {body}
  <p style="color:#334155;font-size:11.5px;text-align:center;margin:30px 0 8px;">10 from {scanned} scanned · arxiv · HF papers · GitHub · HF models · HN · written by {GEMINI_MODEL} · auto-filed to your RAG Airtable</p>
</div></body></html>"""


def send_mail(html_body: str, subject: str) -> None:
    """Gmail SMTP with transport fallback. GitHub-hosted runners often get their
    connection dropped mid-AUTH on 587/STARTTLS (Google flags datacenter IPs);
    465 direct SSL succeeds where STARTTLS is reset."""
    msg = MIMEMultipart("alternative")
    msg["From"], msg["To"], msg["Subject"] = GMAIL_USER, EMAIL_TO, subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    raw = msg.as_string()
    last: Exception | None = None

    def _ssl465():
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, EMAIL_TO, raw)

    def _tls587():
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, EMAIL_TO, raw)

    for name, fn in (("ssl/465", _ssl465), ("starttls/587", _tls587),
                     ("ssl/465", _ssl465), ("starttls/587", _tls587)):
        try:
            fn()
            print(f"[pulse] smtp transport ok: {name}")
            return
        except Exception as exc:
            last = exc
            print(f"[pulse] smtp {name} failed: {type(exc).__name__}: {exc}")
            time.sleep(5)
    raise RuntimeError(f"gmail smtp failed on all transports: {last}")


# ── AIRTABLE ────────────────────────────────────────────────────────────────
def at_upsert(tid: str, records: list[dict]) -> int:
    done = 0
    for i in range(0, len(records), 10):
        batch = records[i:i + 10]
        r = requests.post(f"https://api.airtable.com/v0/{BASE_ID}/{tid}",
                          headers=AT_HDR,
                          json={"performUpsert": {"fieldsToMergeOn": ["Canonical URL"]},
                                "typecast": True, "records": batch}, timeout=40)
        if r.status_code != 200:
            print(f"[pulse] airtable {tid} err {r.status_code}: {r.text[:200]}")
            continue
        done += len(r.json().get("records", []))
    return done


def rag_payload(c: dict) -> dict:
    prov = json.dumps([{"id": "sam-ai-pulse", "subj": f"SAM AI Pulse {TODAY}", "date": TODAY}])
    typ = {"paper": "ArXiv" if "arxiv" in c["canon"] else "Research Paper",
           "repo": "GitHub", "model": "HF Model", "news": "News"}[c["kind"]]
    return {"fields": {"Canonical URL": c["canon"], "URL": c["url"], "Title": c["title"][:255],
                       "Type": typ, "Summary": (c["story"] + "\n\n" + c["summary"])[:9500],
                       "Key Topics": c.get("topics", "")[:255], "Email Subject": f"SAM AI Pulse {TODAY}",
                       "Source Emails": prov, "First Seen Date": TODAY, "Times Referenced": 1,
                       "Status": "Processed"}}


def typed_payload(c: dict) -> dict:
    base = rag_payload(c)["fields"]
    base.pop("Type", None)
    base.pop("Source Emails", None)
    base["Category"] = cats(c["title"], c["summary"])
    if c["kind"] == "repo":
        base["Stars"] = c.get("stars", 0)
        base["Language"] = c.get("lang", "")
    else:
        base["Published Date"] = c.get("date") or TODAY
        base["Has Code"] = bool(c.get("code_url"))
        if c.get("code_url"):
            base["Code Repo URL"] = c["code_url"]
    return {"fields": base}


def push_airtable(items: list[dict]) -> tuple[int, int, int]:
    if not AT_TOKEN:
        print("[pulse] AIRTABLE TOKEN MISSING — skipping upsert")
        return 0, 0, 0
    rag = sum(1 for _ in [1]) and at_upsert(T_RAG, [rag_payload(c) for c in items])
    papers = at_upsert(T_PAPERS, [typed_payload(c) for c in items if c["kind"] == "paper"])
    gh = at_upsert(T_GITHUB, [typed_payload(c) for c in items if c["kind"] == "repo"])
    return rag, papers, gh


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> int:
    print(f"[pulse] SAM AI Pulse — {TODAY}")
    missing = [k for k, v in (("GEMINI_API_KEY", GEMINI_KEY), ("GMAIL_USER", GMAIL_USER),
                              ("GMAIL_APP_PASSWORD", GMAIL_PASS)) if not v]
    if missing:
        print(f"[pulse] FATAL missing env: {', '.join(missing)}")
        return 2

    groups = {
        "hf_daily": src_hf_daily(), "arxiv": src_arxiv(),
        "gh_trending": src_github_trending(), "gh_search": src_github_search(),
        "hf_models": src_hf_models(), "hn": src_hn(),
    }
    cands = [c for g in groups.values() for c in g]
    print("[pulse] scanned: " + " | ".join(f"{k}={len(v)}" for k, v in groups.items()) + f" -> total={len(cands)}")
    if len(cands) < 5:
        print("[pulse] FATAL: too few candidates; aborting before sending a thin email")
        return 3

    items = pick10(cands)
    print("[pulse] picked " + ",".join(f"{c['kind']}" for c in items))
    write_stories(items)
    intro = write_intro(items)

    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    issue = int(st.get("issue", 0)) + 1
    subject = f"SAM AI Pulse #{issue:03d} — {dt.date.today().strftime('%b %d')} | {len(items)} picks from {len(cands)}"
    if "--send" in sys.argv:
        send_mail(build_html(issue, items, intro, len(cands)), subject)
        print(f"[pulse] EMAIL_SENT to={EMAIL_TO} subject=\"{subject}\"")
    else:
        print("[pulse] dry run: email not sent")
    rag_n, pap_n, gh_n = push_airtable(items)
    print(f"[pulse] AIRTABLE_UPSERT rag={rag_n} papers={pap_n} github={gh_n}")

    st = {"issue": issue, "last_run": TODAY,
          "sent_canon": ((st.get("sent_canon") or []) + [c["canon"].rstrip("/").lower() for c in items])[-150:]}
    STATE.write_text(json.dumps(st, indent=1))
    print(f"[pulse] state saved: issue={issue}")
    print("[pulse] DONE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
