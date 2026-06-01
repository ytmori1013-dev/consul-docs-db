"""
ステマフィルタ3層でバズツイートを除外するモジュール

Layer1: 過去30日間に3回以上バズしたauthor_idを除外
Layer2: フォロワー急増（前回比200%以上）のアカウントを除外
Layer3: エンゲージメント率異常値（0.5超）を除外（フォロワー1000未満はスキップ）
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# フィルタ定数
BUZZ_COUNT_30D_LIMIT = 3          # 30日以内のバズ許容回数（これ以上は除外）
FOLLOWER_GROWTH_RATE_LIMIT = 2.0  # フォロワー増加率閾値（200%）
ENGAGEMENT_RATE_LIMIT = 0.5       # エンゲージメント率閾値
MIN_FOLLOWERS_FOR_ENGAGEMENT = 1000  # Layer3を適用する最低フォロワー数


def _layer1_buzz_frequency(
    tweets: list[dict], author_history: dict[str, dict]
) -> list[dict]:
    """
    Layer1: 過去30日間に3回以上バズしたauthor_idを除外する
    """
    filtered = []
    excluded_count = 0
    for tweet in tweets:
        aid = tweet["author_id"]
        history = author_history.get(aid, {})
        buzz_count = history.get("buzz_count_30d", 0)
        if buzz_count >= BUZZ_COUNT_30D_LIMIT:
            logger.debug(f"Layer1除外: {aid} (30日バズ回数: {buzz_count})")
            excluded_count += 1
        else:
            filtered.append(tweet)
    logger.info(f"Layer1フィルタ: {excluded_count} 件除外 → {len(filtered)} 件残存")
    return filtered


def _layer2_follower_growth(
    tweets: list[dict], author_history: dict[str, dict]
) -> list[dict]:
    """
    Layer2: フォロワー急増（前回比200%以上）のアカウントを除外する
    """
    filtered = []
    excluded_count = 0
    for tweet in tweets:
        aid = tweet["author_id"]
        current_followers = tweet.get("followers_count", 0)
        history = author_history.get(aid, {})
        followers_hist = history.get("followers_history", [])

        # 履歴がなければフィルタスキップ
        if not followers_hist or current_followers == 0:
            filtered.append(tweet)
            continue

        prev_followers = followers_hist[-1]
        # 前回が0の場合は比較できないのでスキップ
        if prev_followers == 0:
            filtered.append(tweet)
            continue

        growth_rate = current_followers / prev_followers
        if growth_rate >= FOLLOWER_GROWTH_RATE_LIMIT:
            logger.debug(
                f"Layer2除外: {aid} (フォロワー増加率: {growth_rate:.1f}x, "
                f"{prev_followers} → {current_followers})"
            )
            excluded_count += 1
        else:
            filtered.append(tweet)

    logger.info(f"Layer2フィルタ: {excluded_count} 件除外 → {len(filtered)} 件残存")
    return filtered


def _layer3_engagement_rate(tweets: list[dict]) -> list[dict]:
    """
    Layer3: エンゲージメント率異常値（0.5超）を除外する
    フォロワー1000未満のアカウントはスキップ
    """
    filtered = []
    excluded_count = 0
    for tweet in tweets:
        followers = tweet.get("followers_count", 0)

        # フォロワー1000未満はLayer3をスキップ
        if followers < MIN_FOLLOWERS_FOR_ENGAGEMENT:
            tweet["engagement_rate"] = None
            filtered.append(tweet)
            continue

        likes = tweet.get("likes", 0)
        retweets = tweet.get("retweets", 0)
        engagement_rate = (likes + retweets) / followers
        tweet["engagement_rate"] = round(engagement_rate, 4)

        if engagement_rate > ENGAGEMENT_RATE_LIMIT:
            logger.debug(
                f"Layer3除外: {tweet['author_id']} "
                f"(エンゲージメント率: {engagement_rate:.3f})"
            )
            excluded_count += 1
        else:
            filtered.append(tweet)

    logger.info(f"Layer3フィルタ: {excluded_count} 件除外 → {len(filtered)} 件残存")
    return filtered


def update_author_history(
    tweets: list[dict], author_history: dict[str, dict]
) -> dict[str, dict]:
    """
    フィルタ後のツイートをもとにauthor_historyを更新する
    - buzz_count_30d: 過去30日のバズ回数をインクリメント
    - followers_history: 最新フォロワー数を追記（最大10件保持）
    """
    now = datetime.now(timezone.utc)
    updated = dict(author_history)

    for tweet in tweets:
        aid = tweet["author_id"]
        if aid not in updated:
            updated[aid] = {"buzz_count_30d": 0, "followers_history": []}

        # バズ回数インクリメント
        updated[aid]["buzz_count_30d"] = updated[aid].get("buzz_count_30d", 0) + 1

        # フォロワー履歴を追加（最大10件）
        hist = updated[aid].get("followers_history", [])
        fc = tweet.get("followers_count", 0)
        if fc > 0:
            hist.append(fc)
            updated[aid]["followers_history"] = hist[-10:]

    return updated


def apply_filters(
    tweets: list[dict], author_history: dict[str, dict]
) -> tuple[list[dict], dict[str, dict]]:
    """
    3層フィルタを順番に適用してフィルタ済みツイートとauthor_historyを返す

    Returns:
        (filtered_tweets, updated_author_history)
    """
    logger.info(f"フィルタ開始: {len(tweets)} 件")

    tweets = _layer1_buzz_frequency(tweets, author_history)
    tweets = _layer2_follower_growth(tweets, author_history)
    tweets = _layer3_engagement_rate(tweets)

    updated_history = update_author_history(tweets, author_history)

    logger.info(f"フィルタ完了: {len(tweets)} 件が通過")
    return tweets, updated_history


if __name__ == "__main__":
    import json
    import sys

    # テスト用サンプルデータ
    sample_tweets = [
        {
            "tweet_id": "1",
            "text": "テストツイート",
            "author": "テストユーザー",
            "author_id": "test_user",
            "followers_count": 5000,
            "likes": 15000,
            "retweets": 2000,
            "timestamp": "2024-01-01T00:00:00",
        }
    ]
    sample_history: dict = {}

    filtered, history = apply_filters(sample_tweets, sample_history)
    print(json.dumps({"filtered": filtered, "history": history}, ensure_ascii=False, indent=2))
