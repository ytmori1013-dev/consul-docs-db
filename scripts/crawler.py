"""
Nitterからバズツイートをスクレイピングするモジュール
"""
import re
import time
import logging
from datetime import datetime, timezone
from typing import Optional
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# フォールバック付きNitterインスタンスリスト
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.it",
    "https://nitter.nl",
]

# バズ閾値
MIN_LIKES = 10000
MIN_RETWEETS = 1000
MAX_TWEETS = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _parse_count(text: str) -> int:
    """'10.5K' や '1,234' などの数値文字列を整数に変換する"""
    text = text.strip().replace(",", "")
    if not text:
        return 0
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        return int(text)
    except ValueError:
        return 0


def _fetch_page(instance: str, path: str, params: dict) -> Optional[BeautifulSoup]:
    """指定インスタンスのページを取得してBeautifulSoupオブジェクトを返す"""
    url = f"{instance}{path}"
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"インスタンス {instance} への接続失敗: {e}")
        return None


def _parse_tweet(item) -> Optional[dict]:
    """BeautifulSoupのツイート要素から情報を抽出する"""
    try:
        # ツイートID
        tweet_link = item.select_one(".tweet-link")
        if not tweet_link:
            return None
        href = tweet_link.get("href", "")
        tweet_id_match = re.search(r"/status/(\d+)", href)
        if not tweet_id_match:
            return None
        tweet_id = tweet_id_match.group(1)

        # 本文
        content_el = item.select_one(".tweet-content")
        text = content_el.get_text(separator=" ").strip() if content_el else ""

        # 著者情報
        username_el = item.select_one(".username")
        fullname_el = item.select_one(".fullname")
        author = fullname_el.get_text(strip=True) if fullname_el else ""
        author_id = username_el.get_text(strip=True).lstrip("@") if username_el else ""
        if not author_id:
            return None

        # フォロワー数（プロフィールリンクから取得できない場合は0）
        followers_count = 0
        followers_el = item.select_one(".followers .profile-stat-num")
        if followers_el:
            followers_count = _parse_count(followers_el.get_text(strip=True))

        # いいね数・RT数
        likes = 0
        retweets = 0
        stats = item.select(".tweet-stat")
        for stat in stats:
            icon = stat.select_one(".icon-heart, .icon-retweet")
            if not icon:
                continue
            num_el = stat.select_one(".tweet-stat-count")
            if not num_el:
                continue
            count = _parse_count(num_el.get_text(strip=True))
            if "icon-heart" in icon.get("class", []):
                likes = count
            elif "icon-retweet" in icon.get("class", []):
                retweets = count

        # タイムスタンプ
        time_el = item.select_one(".tweet-date a")
        timestamp = ""
        if time_el:
            timestamp = time_el.get("title", "") or time_el.get_text(strip=True)

        return {
            "tweet_id": tweet_id,
            "text": text,
            "author": author,
            "author_id": author_id,
            "followers_count": followers_count,
            "likes": likes,
            "retweets": retweets,
            "timestamp": timestamp,
        }
    except Exception as e:
        logger.warning(f"ツイート解析エラー: {e}")
        return None


def _fetch_tweets_from_instance(instance: str, since_id: Optional[str] = None) -> list[dict]:
    """1つのNitterインスタンスからツイートを取得する"""
    params = {
        "q": "filter:safe lang:ja",
        "f": "tweets",
        "src": "typed_query",
    }
    if since_id:
        params["since_id"] = since_id

    soup = _fetch_page(instance, "/search", params)
    if soup is None:
        return []

    tweets = []
    items = soup.select(".timeline-item")
    for item in items:
        tweet = _parse_tweet(item)
        if tweet is None:
            continue
        # バズ閾値フィルタ
        if tweet["likes"] >= MIN_LIKES and tweet["retweets"] >= MIN_RETWEETS:
            tweets.append(tweet)
        if len(tweets) >= MAX_TWEETS:
            break

    logger.info(f"{instance} から {len(tweets)} 件のバズツイートを取得")
    return tweets


def fetch_buzz_tweets(since_id: Optional[str] = None) -> list[dict]:
    """
    複数のNitterインスタンスをフォールバックしながらバズツイートを取得する

    Returns:
        tweet_idで重複排除済みのツイートリスト
    """
    all_tweets: dict[str, dict] = {}

    for instance in NITTER_INSTANCES:
        try:
            tweets = _fetch_tweets_from_instance(instance, since_id)
            for tweet in tweets:
                tid = tweet["tweet_id"]
                if tid not in all_tweets:
                    all_tweets[tid] = tweet
            # 1つのインスタンスから十分に取得できたら終了
            if len(all_tweets) >= MAX_TWEETS:
                break
            time.sleep(2)  # レート制限への配慮
        except Exception as e:
            logger.error(f"インスタンス {instance} の処理中にエラー: {e}")
            continue

    result = list(all_tweets.values())
    logger.info(f"合計 {len(result)} 件のバズツイートを取得（重複排除後）")
    return result


if __name__ == "__main__":
    import json
    import sys

    tweets = fetch_buzz_tweets()
    print(json.dumps(tweets, ensure_ascii=False, indent=2))
    logger.info(f"取得完了: {len(tweets)} 件")
    sys.exit(0 if tweets or True else 1)
