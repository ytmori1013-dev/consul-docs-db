"""
はてなブックマークのホットエントリーをスクレイピングするモジュール

認証不要・無料のはてなブックマークを使用。
JSON API と RSS の両方にフォールバック対応。
"""
import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
import hashlib
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# バズ閾値（ブックマーク数）
MIN_BOOKMARKS = 50
MAX_ENTRIES = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# はてなブックマーク カテゴリ（API名 → 表示名）
# general は全カテゴリのトップを含むため最初に処理し、
# 他カテゴリはそこにない固有エントリーを補完する
HATENA_CATEGORIES = [
    "general",
    "it",
    "social",
    "economics",
    "knowledge",
    "entertainment",
    "life",
    "game",
    "fun",
]

HATENA_BASE = "https://b.hatena.ne.jp"

# 収集したデバッグ情報（gist_sync が読み取って保存）
debug_log: list[str] = []


def _dbg(msg: str) -> None:
    """デバッグログを記録する（printでも出力してActions logにも残す）"""
    print(msg, flush=True)
    debug_log.append(msg)


def _entry_id(url: str) -> str:
    """URL の SHA1 先頭16文字をエントリーIDとして使用する"""
    return hashlib.sha1(url.encode()).hexdigest()[:16]


# ── JSON API ──────────────────────────────────────────────────

def _fetch_json_api(category: str) -> list[dict]:
    """
    はてなブックマーク JSON API から取得する。
    /hotentry/{category}.json と ipad.hotentry.json の2形式を試みる。
    """
    urls = [
        f"{HATENA_BASE}/hotentry/{category}.json",
        f"{HATENA_BASE}/api/ipad.hotentry.json?target={category}",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            _dbg(f"[JSON] {url} → {resp.status_code} ({resp.headers.get('Content-Type','')})")
            if resp.status_code != 200:
                continue
            ct = resp.headers.get("Content-Type", "")
            if "json" not in ct and not resp.text.strip().startswith(("[", "{")):
                _dbg(f"  → JSON以外の応答: {resp.text[:80]!r}")
                continue
            data = resp.json()
            # リストまたは dict の items キーに対応
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                entries = data.get("items") or data.get("entries") or []
            else:
                entries = []
            _dbg(f"  → {len(entries)} 件のエントリーを取得")
            return entries
        except Exception as e:
            _dbg(f"  → エラー: {e}")
    return []


def _parse_json_entry(entry: dict, category: str) -> Optional[dict]:
    """JSON APIエントリーをパイプライン共通フォーマットに変換する"""
    try:
        url = entry.get("url") or entry.get("link") or entry.get("canonical_url", "")
        if not url:
            return None
        domain = urlparse(url).netloc or "unknown"
        title = entry.get("title", "")
        description = entry.get("description") or entry.get("summary") or entry.get("content", "")
        text = title
        if description and description != title:
            text = f"{title} — {description[:100]}"

        # ブックマーク数フィールド名を複数試みる
        bk = (
            entry.get("bookmarks_count")
            or entry.get("count")
            or entry.get("bookmarkCount")
            or entry.get("users_count")
            or 0
        )
        bookmark_count = int(bk)
        comment_count = int(entry.get("comment_count") or entry.get("comments_count") or 0)
        timestamp = entry.get("created_at") or entry.get("updated_at") or entry.get("date", "")

        return {
            "tweet_id": _entry_id(url),
            "text": text,
            "author": domain,
            "author_id": _entry_id(url),
            "followers_count": 0,
            "likes": bookmark_count,
            "retweets": comment_count,
            "timestamp": timestamp,
            "url": url,
            "source_category": category,
        }
    except Exception as e:
        _dbg(f"  → JSON エントリー解析エラー: {e}")
        return None


# ── RSS フォールバック ─────────────────────────────────────────

def _fetch_rss(category: str) -> list[dict]:
    """
    はてなブックマーク RSS フィードから取得する（JSONが使えない場合のフォールバック）
    """
    url = f"{HATENA_BASE}/hotentry/{category}.rss"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        _dbg(f"[RSS] {url} → {resp.status_code} ({resp.headers.get('Content-Type','')})")
        if resp.status_code != 200:
            return []
        return _parse_rss(resp.text, category)
    except Exception as e:
        _dbg(f"  → RSSエラー: {e}")
        return []


def _parse_rss(xml_text: str, category: str) -> list[dict]:
    """RSS XML をパースしてエントリーリストを返す"""
    try:
        root = ET.fromstring(xml_text)
        # namespaceを無視して item を探す
        ns = {"dc": "http://purl.org/dc/elements/1.1/",
              "hatena": "http://www.hatena.ne.jp/info/xmlns#"}
        channel = root.find("channel") or root
        items = channel.findall("item")
        _dbg(f"  → RSS item数: {len(items)}")
        entries = []
        for item in items:
            try:
                url = (item.findtext("link") or "").strip()
                title = (item.findtext("title") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()
                # はてな独自タグからブックマーク数を取得
                bk_el = item.find("{http://www.hatena.ne.jp/info/xmlns#}bookmarkcount")
                bk_count = int(bk_el.text) if bk_el is not None and bk_el.text else 0

                if not url or not title:
                    continue
                domain = urlparse(url).netloc or "unknown"
                text = title
                if desc and desc != title:
                    text = f"{title} — {desc[:100]}"

                entries.append({
                    "tweet_id": _entry_id(url),
                    "text": text,
                    "author": domain,
                    "author_id": _entry_id(url),
                    "followers_count": 0,
                    "likes": bk_count,
                    "retweets": 0,
                    "timestamp": pub_date,
                    "url": url,
                    "source_category": category,
                })
            except Exception as e:
                _dbg(f"    RSS item解析エラー: {e}")
        return entries
    except Exception as e:
        _dbg(f"  → RSS XMLパースエラー: {e}")
        return []


# ── メイン取得関数 ─────────────────────────────────────────────

def fetch_buzz_tweets(since_id: Optional[str] = None) -> list[dict]:
    """
    はてなブックマークのホットエントリーをカテゴリ横断で取得する。
    JSON API → RSS の順でフォールバック。
    """
    all_entries: dict[str, dict] = {}

    for category in HATENA_CATEGORIES:
        try:
            entries = _fetch_json_api(category)

            # JSON が空なら RSS にフォールバック
            if not entries:
                _dbg(f"[{category}] JSON 0件 → RSS フォールバック")
                entries = _fetch_rss(category)

            before = len(all_entries)
            for entry in entries:
                if "tweet_id" not in entry:
                    # JSON API エントリーはまだ変換前
                    parsed = _parse_json_entry(entry, category)
                else:
                    # RSS エントリーはすでに変換済み
                    parsed = entry

                if parsed is None or parsed["likes"] < MIN_BOOKMARKS:
                    continue
                eid = parsed["tweet_id"]
                if eid not in all_entries:
                    all_entries[eid] = parsed

            added = len(all_entries) - before
            _dbg(f"[{category}] +{added} 件（バズ閾値{MIN_BOOKMARKS}以上）")

            if len(all_entries) >= MAX_ENTRIES:
                break
            time.sleep(0.3)
        except Exception as e:
            _dbg(f"[{category}] 処理エラー: {e}")
            continue

    result = sorted(all_entries.values(), key=lambda e: e["likes"], reverse=True)[:MAX_ENTRIES]
    _dbg(f"取得合計: {len(result)} 件")
    return result


if __name__ == "__main__":
    import json, sys
    entries = fetch_buzz_tweets()
    print(json.dumps(entries, ensure_ascii=False, indent=2))
    sys.exit(0)
