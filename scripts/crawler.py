"""
はてなブックマークのホットエントリーをスクレイピングするモジュール

認証不要・無料のはてなブックマーク APIを使用。
カテゴリ別ホットエントリーを取得し、バズ閾値でフィルタする。
"""
import time
import logging
from datetime import datetime, timezone
from typing import Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# バズ閾値（ブックマーク数）
MIN_BOOKMARKS = 100
MAX_ENTRIES = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# はてなブックマーク カテゴリリスト
# ref: https://b.hatena.ne.jp/hotentry/{category}
HATENA_CATEGORIES = [
    "general",       # 総合
    "social",        # 社会・政治・国際
    "economics",     # 経済・企業
    "life",          # 暮らし・健康
    "knowledge",     # 学び・知識
    "it",            # テクノロジー
    "entertainment", # エンタメ・カルチャー
    "game",          # ゲーム・アニメ
    "fun",           # おもしろ
]

HATENA_API_BASE = "https://b.hatena.ne.jp"


def _fetch_hotentry(category: str) -> list[dict]:
    """指定カテゴリのホットエントリーを取得する"""
    url = f"{HATENA_API_BASE}/hotentry/{category}.json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"カテゴリ '{category}': {len(data)} 件取得")
        return data
    except Exception as e:
        logger.warning(f"カテゴリ '{category}' の取得失敗: {e}")
        return []


def _parse_entry(entry: dict, category: str) -> Optional[dict]:
    """
    はてなブックマークのエントリーをパイプライン共通フォーマットに変換する

    tweet_id     → エントリーURLのハッシュ（一意ID）
    text         → タイトル + 説明文
    author       → エントリードメイン
    author_id    → エントリーURLをIDとして使用
    followers_count → ブックマーク数（likes との比率計算用に同値を設定）
    likes        → ブックマーク数
    retweets     → コメント付きブックマーク数（comment_count）
    timestamp    → 記事の日付
    """
    try:
        url = entry.get("url") or entry.get("link", "")
        if not url:
            return None

        # URLからドメインを取得
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc or "unknown"

        title = entry.get("title", "")
        description = entry.get("description", "") or entry.get("summary", "")
        text = title
        if description and description != title:
            text = f"{title} — {description}"

        bookmark_count = int(entry.get("bookmarks_count") or entry.get("count", 0))
        comment_count  = int(entry.get("bookmarks_of_comment") or 0)

        # 日付
        timestamp = (
            entry.get("created_at")
            or entry.get("updated_at")
            or entry.get("date", "")
            or ""
        )

        # URLのSHA1ハッシュ先頭16文字をIDとして使用
        import hashlib
        entry_id = hashlib.sha1(url.encode()).hexdigest()[:16]

        return {
            "tweet_id": entry_id,
            "text": text,
            "author": domain,
            # エントリーごとに一意のIDにしてフィルタLayer1・2をスキップ
            "author_id": entry_id,
            # followers_count=0 でLayer2・3をスキップ（はてなには該当概念なし）
            "followers_count": 0,
            "likes": bookmark_count,
            "retweets": comment_count,
            "timestamp": timestamp,
            "url": url,
            "source_category": category,
        }
    except Exception as e:
        logger.warning(f"エントリー解析エラー: {e}")
        return None


def fetch_buzz_tweets(since_id: Optional[str] = None) -> list[dict]:
    """
    はてなブックマークのホットエントリーをカテゴリ横断で取得する

    since_id は後方互換のために引数として受け取るが、
    はてなブックマーク APIでは使用しない。

    Returns:
        IDで重複排除済みのエントリーリスト（バズ閾値以上のみ）
    """
    all_entries: dict[str, dict] = {}

    for category in HATENA_CATEGORIES:
        try:
            raw_entries = _fetch_hotentry(category)
            for entry in raw_entries:
                parsed = _parse_entry(entry, category)
                if parsed is None:
                    continue
                if parsed["likes"] < MIN_BOOKMARKS:
                    continue
                eid = parsed["tweet_id"]
                if eid not in all_entries:
                    all_entries[eid] = parsed

            if len(all_entries) >= MAX_ENTRIES:
                break
            time.sleep(0.5)  # レート制限への配慮
        except Exception as e:
            logger.error(f"カテゴリ '{category}' の処理中にエラー: {e}")
            continue

    result = sorted(
        all_entries.values(),
        key=lambda e: e["likes"],
        reverse=True,
    )[:MAX_ENTRIES]

    logger.info(f"合計 {len(result)} 件のバズエントリーを取得（重複排除後）")
    return result


if __name__ == "__main__":
    import json, sys
    entries = fetch_buzz_tweets()
    print(json.dumps(entries, ensure_ascii=False, indent=2))
    logger.info(f"取得完了: {len(entries)} 件")
    sys.exit(0)
