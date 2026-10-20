#!/usr/bin/env python3
"""
Dental news site builder.

What it does each time it runs:
  1. Reads the feed list in feeds.txt
  2. Finds articles it hasn't seen yet (from the last few days)
  3. Asks Claude to write a short summary + "why it matters" line for each
  4. Saves everything to data/stories.json
  5. Builds the website into the site/ folder

It uses only Python's built-in libraries, so there is nothing to install.

Run it:
  python build.py          normal run (needs ANTHROPIC_API_KEY set)
  python build.py --test   test run with sample data, no API key needed
"""

import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# SETTINGS — the only part you should need to change
# ---------------------------------------------------------------------------
SITE_NAME = "The Dental Recall"
TAGLINE = "Dental news, summarized for the people in the chair."
MODEL = "claude-haiku-5-5"              # fast, inexpensive Claude model
MAX_NEW_PER_RUN = 40                    # cap on new summaries per run (controls cost)

# PubMed (research). NCBI asks every program that uses PubMed to identify
# itself with a contact email. Put your email between the quotes.
CONTACT_EMAIL = ""
PUBMED_DAYS = 2                         # look back this many days per search
PUBMED_PER_SEARCH = 10                  # max papers per search per run

# Items whose title contains any of these words are skipped without being
# summarized (member newsletters, letters, ads). Not case-sensitive.
SKIP_TITLE_WORDS = ["e-newsletter", "president's letter", "president’s letter",
                    "advocacy insider", "advocacy flash", "coalition letter", "sponsored"]
MAX_ARTICLE_AGE_DAYS = 3                # ignore feed items older than this
KEEP_DAYS = 30                          # stories older than this drop off the site
MAX_STORIES = 300                       # hard cap on stories kept
CATEGORIES = ["research", "clinical", "business", "tech", "products", "policy", "education", "events", "people"]

# Short display names for sources. If a feed's title contains the text on the
# left, the site shows the name on the right. Add a line when you add a feed.
SOURCE_NAMES = {
    "Becker": "Becker's Dental",
    "Dental Tribune": "Dental Tribune",
    "Dentistry IQ": "DentistryIQ",
    "DentistryIQ": "DentistryIQ",
    "Dentistry Today": "Dentistry Today",
    "British Dental Journal": "BDJ",
    "Dental Products Report": "Dental Products Report",
    "Dental Economics": "Dental Economics",
    "Registered Dental Hygienists": "RDH",
    "Perio Implant Advisory": "Perio-Implant Advisory",
    "Orthodontic Products": "Orthodontic Products",
    "Decisions in Dentistry": "Decisions in Dentistry",
    "NIDCR": "NIDCR (NIH)",
    "AAOMS": "AAOMS",
}
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "stories.json"
FEEDS_FILE = ROOT / "feeds.txt"
TEMPLATE_FILE = ROOT / "template.html"
SITE_DIR = ROOT / "site"
TEST_FEED = ROOT / "tests" / "sample_feed.xml"

BOT_NAME = "DentalRecallBot"
USER_AGENT = f"Mozilla/5.0 (compatible; {BOT_NAME}/1.0; +https://dentalrecall.github.io)"
API_URL = "https://api.anthropic.com/v1/messages"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- feeds ----

def read_feed_list():
    if not FEEDS_FILE.exists():
        return []
    urls = []
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


_robots_cache = {}


def robots_allowed(url):
    """Respect each site's robots.txt: if a site asks bots not to read a
    feed, we skip it."""
    from urllib.parse import urlsplit
    from urllib.robotparser import RobotFileParser
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots_cache:
        rp = RobotFileParser()
        try:
            rp.parse(fetch(base + "/robots.txt", check=False).decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e:
            # No robots.txt (404) means everything is allowed; 401/403 means stay out.
            rp.parse(["User-agent: *", "Disallow: /"] if e.code in (401, 403) else [])
        except Exception:
            rp.parse([])
        _robots_cache[base] = rp
    return _robots_cache[base].can_fetch(BOT_NAME, url)


def fetch(url, check=True):
    if check and not robots_allowed(url):
        raise PermissionError("site's robots.txt asks bots not to read this address")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def clean_text(raw, limit=2000):
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def local(tag):
    """Strip the XML namespace from a tag name."""
    return tag.rsplit("}", 1)[-1].lower()


def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and (c.text or "").strip():
            return c.text
    return ""


def parse_feed(xml_bytes):
    """Return (source_name, [items]) for RSS 2.0 or Atom."""
    root = ET.fromstring(xml_bytes)
    items, source = [], ""

    if local(root.tag) == "feed":  # Atom
        source = child_text(root, "title")
        for e in root:
            if local(e.tag) != "entry":
                continue
            link = ""
            for c in e:
                if local(c.tag) == "link" and c.get("rel", "alternate") == "alternate":
                    link = c.get("href", "")
                    break
            items.append({
                "title": clean_text(child_text(e, "title"), 300),
                "link": link,
                "text": clean_text(child_text(e, "content") or child_text(e, "summary")),
                "published": parse_date(child_text(e, "published") or child_text(e, "updated")),
            })
    else:  # RSS
        channel = next((c for c in root if local(c.tag) == "channel"), root)
        source = child_text(channel, "title")
        for it in root.iter():
            if local(it.tag) != "item":
                continue
            body = child_text(it, "encoded") or child_text(it, "description")
            items.append({
                "title": clean_text(child_text(it, "title"), 300),
                "link": child_text(it, "link").strip(),
                "text": clean_text(body),
                "published": parse_date(child_text(it, "pubdate") or child_text(it, "date")),
            })

    source = clean_text(source, 80) or "Unknown source"
    return source, [i for i in items if i["title"] and i["link"].startswith("http")]


def pubmed_search(term):
    """Recent papers with abstracts for a PubMed search, via NCBI's official API."""
    from urllib.parse import urlencode
    common = {"db": "pubmed", "tool": "dentalrecall"}
    email = CONTACT_EMAIL.strip() or os.environ.get("CONTACT_EMAIL", "").strip()
    if email:
        common["email"] = email
    q = f"({term}) AND hasabstract NOT (letter[pt] OR comment[pt] OR editorial[pt] OR erratum[pt])"
    url = EUTILS + "esearch.fcgi?" + urlencode(dict(common, term=q, reldate=PUBMED_DAYS,
        datetype="edat", retmax=PUBMED_PER_SEARCH, retmode="json", sort="pub_date"))
    ids = json.loads(fetch(url, check=False))["esearchresult"]["idlist"]
    time.sleep(0.4)  # NCBI allows at most 3 requests per second
    if not ids:
        return []
    url = EUTILS + "efetch.fcgi?" + urlencode(dict(common, id=",".join(ids), retmode="xml"))
    root = ET.fromstring(fetch(url, check=False))
    time.sleep(0.4)
    items = []
    for art in root.iter("PubmedArticle"):
        pmid = art.findtext(".//PMID")
        title = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""
        abstract = " ".join("".join(a.itertext()) for a in art.iter("AbstractText"))
        journal = art.findtext(".//Journal/ISOAbbreviation") or art.findtext(".//Journal/Title") or "PubMed"
        if pmid and title and abstract:
            items.append({
                "title": clean_text(title, 300),
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "text": clean_text(abstract, 2500),
                "published": None,
                "source": journal,
                "research": True,
            })
    return items


def collect_items(test_mode):
    sources = [str(TEST_FEED)] if test_mode else read_feed_list()
    all_items = []
    for src in sources:
        if src.lower().startswith("pubmed:"):
            term = src.split(":", 1)[1].strip()
            try:
                items = pubmed_search(term)
                all_items.extend(items)
                log(f"OK    PubMed {term[:60]}: {len(items)} papers")
            except Exception as e:
                log(f"SKIP  PubMed {term[:60]} ({type(e).__name__}: {e})")
            continue
        try:
            raw = Path(src).read_bytes() if test_mode else fetch(src)
            name, items = parse_feed(raw)
            for i in items:
                i["source"] = name
            all_items.extend(items)
            log(f"OK    {name}: {len(items)} items")
        except Exception as e:  # one broken feed should never stop the run
            log(f"SKIP  {src} ({type(e).__name__}: {e})")
    return all_items


# ----------------------------------------------------------- summarize ----

PROMPT = """You write short news summaries for a website read by dentists and dental specialists.

Below is an article title and the excerpt from the publisher's RSS feed. Use ONLY facts stated in this text. Never add numbers, names or findings that are not in it. Write in your own words; do not copy sentences from the excerpt.

Save your result with the save_story tool.

Title: {title}
Source: {source}
Excerpt: {text}"""


# Using a "tool" makes Claude return its answer as clean, structured data,
# so quotation marks in a headline can never break it.
STORY_TOOL = {
    "name": "save_story",
    "description": "Save the summary of one news article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {"type": "boolean", "description": "True if this is real news useful to dental professionals. False for ads, sponsored content, job listings, webinars for sale, or non-dental topics."},
            "category": {"type": "string", "enum": CATEGORIES, "description": (
                "Pick the ONE best fit. "
                "research = a study, trial or review and its findings. "
                "clinical = techniques, treatment guidance, infection control, patient care. "
                "business = practice ownership, DSOs, deals, finances, earnings, staffing, insurance. "
                "tech = software, AI, imaging, digital workflow. "
                "products = a specific new product launch or update. "
                "policy = laws, regulation, licensing, government or association policy. "
                "education = dental schools, training programs, residencies, CE, students. "
                "events = ONLY a conference, meeting or event itself (dates, sessions). "
                "people = appointments, awards, retirements, obituaries.")},
            "headline": {"type": "string", "description": "A clear headline in your own words, at most 90 characters, no clickbait."},
            "summary": {"type": "string", "description": "2 to 3 plain sentences on what happened or what was found. If the excerpt is thin, one sentence describing what the article covers."},
            "why": {"type": "string", "description": "One sentence on what it means for a practicing dentist (patient care, the business of a practice, or the profession). No medical advice beyond what the text supports."},
        },
        "required": ["relevant", "category", "headline", "summary", "why"],
    },
}


def call_claude(item, api_key):
    prompt = PROMPT.format(title=item["title"], source=item["source"],
                           text=item["text"] or "(no excerpt)")
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 800,
        "tools": [STORY_TOOL],
        "tool_choice": {"type": "tool", "name": "save_story"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            for block in data.get("content", []):
                if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                    return block["input"]
            return None
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"API error {e.code}: {detail}") from None


def fake_summary(item):
    """Used by --test so you can preview the site without an API key."""
    return {
        "relevant": "sponsored" not in item["title"].lower(),
        "category": CATEGORIES[len(item["title"]) % len(CATEGORIES)],
        "headline": item["title"][:90],
        "summary": "TEST MODE: this is placeholder text. A real run replaces it with a two- to three-sentence summary written by Claude.",
        "why": "TEST MODE: placeholder for the practitioner-focused line.",
    }


# --------------------------------------------------------------- build ----

def story_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def load_stories():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return []


def save_stories(stories):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(stories, indent=1, ensure_ascii=False), encoding="utf-8")


def short_source(name):
    for key, short in SOURCE_NAMES.items():
        if key.lower() in name.lower():
            return short
    # Fallback: drop extra text after a dash or bar, e.g. "Name – Tagline"
    return re.split(r"\s+[–—|-]\s+", name)[0].strip() or name


def render(stories):
    stories = [dict(s, source=short_source(s.get("source", ""))) for s in stories]
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    payload = json.dumps(stories, ensure_ascii=False).replace("</", "<\\/")
    page = (template
            .replace("__SITE_NAME__", html.escape(SITE_NAME))
            .replace("__TAGLINE__", html.escape(TAGLINE))
            .replace("__UPDATED__", datetime.now(timezone.utc).isoformat())
            .replace("__CATEGORIES_JSON__", json.dumps(CATEGORIES))
            .replace("__STORIES_JSON__", payload))
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "index.html").write_text(page, encoding="utf-8")
    (SITE_DIR / ".nojekyll").write_text("")


def main():
    test_mode = "--test" in sys.argv
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not test_mode and not api_key:
        sys.exit("ANTHROPIC_API_KEY is not set. Add it as a GitHub secret (see README), "
                 "or run 'python build.py --test' to preview with sample data.")

    stories = load_stories()
    seen = {s["id"] for s in stories}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_ARTICLE_AGE_DAYS)

    items = collect_items(test_mode)
    fresh, ids = [], set()
    for i in items:
        sid = story_id(i["link"])
        if sid in seen or sid in ids:
            continue
        if any(w in i["title"].lower() for w in SKIP_TITLE_WORDS):
            continue
        if not test_mode and i["published"] and i["published"] < cutoff:
            continue
        ids.add(sid)
        fresh.append(i)
    fresh.sort(key=lambda i: i["published"] or now, reverse=True)
    fresh = fresh[:MAX_NEW_PER_RUN]
    log(f"{len(fresh)} new articles to summarize")

    added = 0
    for i in fresh:
        try:
            result = fake_summary(i) if test_mode else call_claude(i, api_key)
        except Exception as e:
            log(f"FAIL  {i['title'][:60]} ({e})")
            continue
        if not result:
            log(f"FAIL  {i['title'][:60]} (no usable reply)")
            continue
        sid = story_id(i["link"])
        if not result.get("relevant", True):
            # remember it so we don't pay to check it again
            stories.append({"id": sid, "hidden": True, "added": now.isoformat()})
            log(f"SKIP  not relevant: {i['title'][:60]}")
            continue
        cat = result.get("category") if result.get("category") in CATEGORIES else "clinical"
        if i.get("research"):
            cat = "research"
        stories.append({
            "id": sid,
            "url": i["link"],
            "source": i["source"],
            "published": (i["published"] or now).isoformat(),
            "added": now.isoformat(),
            "category": cat,
            "headline": str(result.get("headline", i["title"]))[:120],
            "summary": str(result.get("summary", "")),
            "why": str(result.get("why", "")),
        })
        added += 1
        log(f"ADD   {result.get('headline', i['title'])[:70]}")

    keep_after = (now - timedelta(days=KEEP_DAYS)).isoformat()
    stories = [s for s in stories if s.get("added", "") >= keep_after][-MAX_STORIES * 2:]
    save_stories(stories)

    visible = sorted((s for s in stories if not s.get("hidden")),
                     key=lambda s: s["published"], reverse=True)[:MAX_STORIES]
    render(visible)
    log(f"Done: {added} added, {len(visible)} stories on the site -> site/index.html")


if __name__ == "__main__":
    main()
