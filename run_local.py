#!/usr/bin/env python3
"""
consul-slides ローカルクローラー

使い方:
  python3 run_local.py

必要な環境変数（未設定時は対話入力）:
  GIST_TOKEN  : GitHub Personal Access Token (gist write 権限)
  GIST_ID     : Gist ID (例: 30013c3c5e896a2ba0b9ed7578128397)
"""
import os
import sys
import subprocess
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _ensure_deps():
    """必要なライブラリをインストールする"""
    required = ["requests", "beautifulsoup4", "playwright", "python-pptx", "pdfplumber"]
    for pkg in required:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            logger.info(f"{pkg} をインストール中...")
            subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"], check=True)

    # Playwright ブラウザのインストール
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(headless=True)
                b.close()
            except Exception:
                logger.info("Playwright Chromium をインストール中...")
                subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        logger.warning(f"Playwright 確認失敗: {e}")


def _get_env(key: str, prompt: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        val = input(f"{prompt}: ").strip()
    return val


def main():
    print("=" * 60)
    print("consul-slides ローカルクローラー")
    print("=" * 60)

    # 環境変数の確認
    gist_token = _get_env("GIST_TOKEN", "GitHub Personal Access Token (gist 権限)")
    gist_id = _get_env("GIST_ID", "Gist ID")

    if not gist_token or not gist_id:
        print("GIST_TOKEN と GIST_ID が必要です。")
        sys.exit(1)

    os.environ["GIST_TOKEN"] = gist_token
    os.environ["GIST_ID"] = gist_id

    # 依存ライブラリのインストール
    logger.info("依存ライブラリを確認中...")
    _ensure_deps()

    # スクリプトのルートディレクトリを sys.path に追加
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Step 1: 既存 URL の読み込み
    existing_urls = set()
    try:
        from scripts.gist_sync import load_existing_urls
        existing_urls = load_existing_urls()
        logger.info(f"既存 URL 読み込み: {len(existing_urls)} 件")
    except Exception as e:
        logger.warning(f"既存 URL 読み込み失敗（空セットで継続）: {e}")

    # Step 2: クロール
    from scripts.crawler import crawl
    logger.info("クロール開始（METI + NDL）...")
    new_entries = crawl(existing_urls)
    logger.info(f"クロール完了: 新規 {len(new_entries)} 件")

    if not new_entries:
        logger.info("新規エントリーなし。終了します。")
        return

    # Step 3: タグ付け
    from scripts.tagger import tag_entries
    logger.info("タグ付け開始...")
    tagged = tag_entries(new_entries)
    logger.info(f"タグ付け完了: {len(tagged)} 件")

    # 統計表示
    from collections import Counter
    firms = Counter(e.get("firm_name", "不明") for e in tagged)
    theme_tagged = sum(1 for e in tagged if e.get("tags", {}).get("theme"))
    meti_hits = sum(1 for e in tagged if e.get("file_type") in ("pdf", "ppt", "pptx"))
    print(f"\n取得結果:")
    print(f"  METI PDF/PPT: {meti_hits} 件")
    print(f"  NDL HTML:     {len(tagged) - meti_hits} 件")
    print(f"  テーマタグあり: {theme_tagged} 件")
    print(f"  ファーム検出: {dict(firms.most_common(5))}")

    # Step 4: Gist 同期
    from scripts.gist_sync import sync
    logger.info("Gist に同期中...")
    success = sync(tagged)
    if success:
        logger.info("✅ Gist 同期完了")
        print(f"\nhttps://ytmori1013-dev.github.io/buzz_tracker/ で確認できます")
    else:
        logger.error("❌ Gist 同期失敗")


if __name__ == "__main__":
    main()
