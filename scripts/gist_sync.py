"""
GitHub Gistとデータを同期するモジュール

環境変数:
- GIST_TOKEN: GitHub Personal Access Token
- GIST_ID: 保存先のGist ID
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")
DATA_FILENAME = "data.json"
MAX_TWEETS = 1000  # 保存する最大ツイート数

HEADERS = {
    "Authorization": f"token {GIST_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


def _load_gist_data() -> dict:
    """GistからJSONデータを読み込む。失敗した場合は空データを返す。"""
    if not GIST_ID:
        logger.warning("GIST_IDが設定されていません。空データを使用します。")
        return _empty_data()

    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        gist = resp.json()

        files = gist.get("files", {})
        if DATA_FILENAME not in files:
            logger.info(f"Gistに {DATA_FILENAME} が存在しません。新規作成します。")
            return _empty_data()

        raw_url = files[DATA_FILENAME].get("raw_url", "")
        if not raw_url:
            return _empty_data()

        raw_resp = requests.get(raw_url, timeout=15)
        raw_resp.raise_for_status()
        data = raw_resp.json()
        logger.info(
            f"Gistからデータ読み込み完了: {len(data.get('tweets', []))} 件"
        )
        return data
    except Exception as e:
        logger.error(f"Gistデータ読み込みエラー: {e}")
        return _empty_data()


def _empty_data() -> dict:
    """空のデータ構造を返す"""
    return {
        "last_updated": "",
        "tweets": [],
        "author_history": {},
    }


def _save_gist_data(data: dict) -> bool:
    """データをGistに保存する。成功した場合はTrueを返す。"""
    if not GIST_ID or not GIST_TOKEN:
        logger.warning("GIST_ID または GIST_TOKEN が未設定です。保存をスキップします。")
        return False

    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        payload = {
            "files": {
                DATA_FILENAME: {
                    "content": json.dumps(data, ensure_ascii=False, indent=2)
                }
            }
        }
        resp = requests.patch(url, headers=HEADERS, json=payload, timeout=30)
        resp.raise_for_status()
        logger.info("Gistへのデータ保存完了")
        return True
    except Exception as e:
        logger.error(f"Gistデータ保存エラー: {e}")
        return False


def merge_tweets(
    existing_tweets: list[dict], new_tweets: list[dict]
) -> list[dict]:
    """
    既存ツイートと新規ツイートをtweetIdで重複排除してマージする。
    新しいものが先頭になるよう並び替え、MAX_TWEETS件に制限する。
    """
    merged: dict[str, dict] = {}

    # 既存を先に追加
    for tweet in existing_tweets:
        tid = tweet.get("tweet_id")
        if tid:
            merged[tid] = tweet

    # 新規で上書き（最新データ優先）
    for tweet in new_tweets:
        tid = tweet.get("tweet_id")
        if tid:
            merged[tid] = tweet

    # タイムスタンプで降順ソート（取得できない場合はtweet_idで降順）
    result = sorted(
        merged.values(),
        key=lambda t: (t.get("tweet_id", "0"),),
        reverse=True,
    )

    # 最新MAX_TWEETS件に絞る
    if len(result) > MAX_TWEETS:
        result = result[:MAX_TWEETS]
        logger.info(f"ツイート数をMAX_TWEETS={MAX_TWEETS}件に制限しました。")

    return result


def merge_author_history(
    existing_history: dict[str, dict], new_history: dict[str, dict]
) -> dict[str, dict]:
    """
    author_historyをマージする。新しいデータで上書き。
    """
    merged = dict(existing_history)
    merged.update(new_history)
    return merged


def sync(new_tweets: list[dict], updated_author_history: dict[str, dict]) -> bool:
    """
    新規ツイートとauthor_historyをGistと同期する。

    Args:
        new_tweets: フィルタ・分類済みの新規ツイートリスト
        updated_author_history: フィルタ処理で更新されたauthor_history

    Returns:
        保存成功: True / 失敗: False
    """
    # 既存データを読み込む
    existing_data = _load_gist_data()

    # マージ
    merged_tweets = merge_tweets(existing_data.get("tweets", []), new_tweets)
    merged_history = merge_author_history(
        existing_data.get("author_history", {}), updated_author_history
    )

    # 保存データ構造を作成
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    new_data = {
        "last_updated": now,
        "tweets": merged_tweets,
        "author_history": merged_history,
    }

    logger.info(
        f"同期準備完了: 新規 {len(new_tweets)} 件, "
        f"合計 {len(merged_tweets)} 件"
    )

    return _save_gist_data(new_data)


def load_author_history() -> dict[str, dict]:
    """
    Gistから既存のauthor_historyを読み込む。
    フィルタ処理で使用するために事前に呼び出す。
    """
    data = _load_gist_data()
    history = data.get("author_history", {})
    logger.info(f"author_history読み込み: {len(history)} アカウント")
    return history


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    # パイプラインからの入力を想定
    try:
        input_data = json.load(sys.stdin)
        tweets = input_data.get("tweets", [])
        author_history = input_data.get("author_history", {})
    except Exception as e:
        logger.error(f"入力データの読み込みエラー: {e}")
        sys.exit(1)

    success = sync(tweets, author_history)
    sys.exit(0 if success else 1)
