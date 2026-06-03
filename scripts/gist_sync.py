"""
GitHub Gistとスライドデータを同期するモジュール

環境変数:
- GIST_TOKEN: GitHub Personal Access Token
- GIST_ID: 保存先のGist ID
"""
import json
import logging
import os
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")
DATA_FILENAME = "slides.json"
MAX_SLIDES = 2000  # 保存する最大件数

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

        raw_resp = requests.get(raw_url, timeout=30)
        raw_resp.raise_for_status()
        data = raw_resp.json()
        logger.info(f"Gistからデータ読み込み完了: {len(data.get('slides', []))} 件")
        return data

    except Exception as e:
        logger.error(f"Gistデータ読み込みエラー: {e}")
        return _empty_data()


def _empty_data() -> dict:
    """空のデータ構造を返す"""
    return {
        "last_updated": "",
        "total_count": 0,
        "slides": [],
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
        resp = requests.patch(url, headers=HEADERS, json=payload, timeout=60)
        resp.raise_for_status()
        logger.info(f"Gistへのデータ保存完了: {data['total_count']} 件")
        return True

    except Exception as e:
        logger.error(f"Gistデータ保存エラー: {e}")
        return False


def merge_slides(existing_slides: list, new_slides: list) -> list:
    """
    既存スライドと新規スライドをidで重複排除してマージする。
    新着優先でソートし MAX_SLIDES 件に制限する（古いものから削除）。
    PDF/PPTX 直リンクを持つエントリを優先保護し、html-only を先に削除。
    """
    merged: dict = {}

    for slide in existing_slides:
        sid = slide.get("id")
        if sid:
            merged[sid] = slide

    # 新規データで上書き（最新情報優先）
    for slide in new_slides:
        sid = slide.get("id")
        if sid:
            merged[sid] = slide

    result = sorted(
        merged.values(),
        key=lambda s: s.get("crawled_at", ""),
        reverse=True,
    )

    if len(result) > MAX_SLIDES:
        logger.info(f"スライド数を MAX_SLIDES={MAX_SLIDES} 件に制限しました。")
        # PDF/PPTX 直リンクを持つエントリを優先保護し、html-only を先に削除
        has_file = [s for s in result if s.get("file_type") in ("pdf", "pptx", "ppt")]
        html_only = [s for s in result if s.get("file_type") not in ("pdf", "pptx", "ppt")]
        combined = has_file + html_only
        result = combined[:MAX_SLIDES]

    return result


def sync(new_slides: list) -> bool:
    """
    新規スライドデータをGistと同期する。

    Args:
        new_slides: タグ付け済みの新規スライドリスト

    Returns:
        保存成功: True / 失敗: False
    """
    existing_data = _load_gist_data()
    merged_slides = merge_slides(existing_data.get("slides", []), new_slides)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    new_data = {
        "last_updated": now,
        "total_count": len(merged_slides),
        "slides": merged_slides,
    }

    logger.info(f"同期準備完了: 新規 {len(new_slides)} 件, 合計 {len(merged_slides)} 件")
    return _save_gist_data(new_data)


def load_existing_urls() -> set:
    """既存データのURL一覧を返す（クローラーの重複排除用）"""
    data = _load_gist_data()
    urls = {slide.get("url", "") for slide in data.get("slides", [])}
    urls.discard("")
    logger.info(f"既存URL読み込み: {len(urls)} 件")
    return urls


def load_total_count() -> int:
    """Gistの総件数を返す"""
    data = _load_gist_data()
    return data.get("total_count", 0)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    slides = json.load(sys.stdin)
    success = sync(slides)
    sys.exit(0 if success else 1)
