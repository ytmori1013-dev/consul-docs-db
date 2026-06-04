"""RSS フィードクローラー — ニュース収集"""

import os
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

FEEDS = [
    ("https://spacenews.com/feed/",                      "SpaceNews",       "en"),
    ("https://www.defensenews.com/rss/",                 "Defense News",    "en"),
    ("https://www.mod.go.jp/j/press/news/rss.xml",       "防衛省",           "ja"),
    ("https://www.jaxa.jp/rss/press_j.rdf",              "JAXA",            "ja"),
    ("https://www8.cao.go.jp/space/rss.xml",             "内閣府宇宙政策",   "ja"),
]

SPACE_DEFENSE_KEYWORDS_JA = ["宇宙", "防衛", "衛星", "ロケット", "ミサイル", "安全保障", "JAXA"]
SPACE_DEFENSE_KEYWORDS_EN = ["space", "defense", "satellite", "missile", "rocket", "aerospace"]


def _is_relevant(title: str, lang: str) -> bool:
    t = title.lower()
    kws = SPACE_DEFENSE_KEYWORDS_EN if lang == "en" else SPACE_DEFENSE_KEYWORDS_JA
    return any(k.lower() in t for k in kws)


def _parse_rss(xml_text: str, org: str, lang: str):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = []
    for item in root.findall(".//item") or root.findall(".//atom:entry", ns):
        title_el = item.find("title") or item.find("atom:title", ns)
        link_el = item.find("link") or item.find("atom:link", ns)
        date_el = item.find("pubDate") or item.find("dc:date", {"dc": "http://purl.org/dc/elements/1.1/"}) or item.find("atom:published", ns)

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        url = (link_el.text or link_el.get("href", "")).strip() if link_el is not None else ""
        if not title or not url:
            continue
        if lang not in ("ja",) and not _is_relevant(title, lang):
            continue

        pub_date = None
        if date_el is not None and date_el.text:
            try:
                pub_date = str(parsedate_to_datetime(date_el.text).date())
            except Exception:
                try:
                    pub_date = str(datetime.fromisoformat(date_el.text[:10]).date())
                except Exception:
                    pass

        entries.append({
            "title": title,
            "url": url,
            "source": "rss",
            "org": org,
            "file_type": "html",
            "lang": lang,
            "tags": None,
            "published_at": pub_date,
        })
    return entries


def crawl():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    total = 0
    for feed_url, org, lang in FEEDS:
        try:
            resp = requests.get(feed_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            entries = _parse_rss(resp.text, org, lang)
            for entry in entries:
                res = sb.table("documents").upsert(entry, on_conflict="url").execute()
                if res.data:
                    total += 1
            print(f"  {org}: {len(entries)} 件")
        except Exception as e:
            print(f"  {org} エラー: {e}")
    print(f"RSS クロール完了: {total} 件 upsert")


if __name__ == "__main__":
    crawl()
