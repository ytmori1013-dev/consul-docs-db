"""
経産省委託調査報告書クローラー

https://www.meti.go.jp/topic/data/e90622aj.html から
PPT/PDFファイルのリンクとメタデータを収集する。
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TARGET_URL = "https://www.meti.go.jp/topic/data/e90622aj.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ファーム名検出パターン（部分一致）
FIRM_PATTERNS = [
    ("McKinsey", ["McKinsey", "マッキンゼー"]),
    ("BCG", ["BCG", "ボストンコンサルティング", "Boston Consulting"]),
    ("Deloitte", ["デロイト", "Deloitte", "デロイトトーマツ"]),
    ("PwC", ["PwC", "プライスウォーター", "PricewaterhouseCoopers"]),
    ("Accenture", ["アクセンチュア", "Accenture"]),
    ("NRI", ["NRI", "野村総合研究所"]),
    ("三菱UFJリサーチ", ["三菱UFJ", "MURC", "三菱UFJリサーチ"]),
    ("KPMG", ["KPMG"]),
    ("EY", ["EY", "アーンスト", "Ernst & Young"]),
    ("Roland Berger", ["ローランドベルガー", "Roland Berger"]),
    ("A.T. Kearney", ["ATカーニー", "A.T. Kearney", "Kearney"]),
    ("Bain", ["Bain", "ベイン"]),
    ("IBM", ["IBM", "日本IBM"]),
    ("三菱総合研究所", ["三菱総合研究所", "MRI"]),
    ("みずほリサーチ", ["みずほリサーチ", "みずほ総合研究所", "Mizuho Research"]),
    ("富士通総研", ["富士通総研", "FRI"]),
    ("日立コンサルティング", ["日立コンサルティング"]),
    ("NTTデータ", ["NTTデータ", "NTT DATA"]),
    ("野村証券", ["野村証券"]),
    ("インテグラル", ["インテグラル"]),
]


def _entry_id(url: str) -> str:
    """URLのMD5ハッシュをIDとして使用"""
    return hashlib.md5(url.encode()).hexdigest()


def _extract_fiscal_year(text: str) -> str:
    """テキストから年度（令和・平成）を抽出する"""
    m = re.search(r"令和(\d+)年度", text)
    if m:
        return f"令和{m.group(1)}"
    m = re.search(r"平成(\d+)年度", text)
    if m:
        return f"平成{m.group(1)}"
    # 例: R6年度
    m = re.search(r"R(\d+)年度", text)
    if m:
        return f"令和{m.group(1)}"
    return ""


def _extract_firm_name(text: str) -> str:
    """テキストからファーム名を抽出する。見つからない場合は '不明' を返す"""
    for firm_name, patterns in FIRM_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                return firm_name
    return "不明"


def _get_file_type(url: str) -> str:
    """URLからファイル種別を判定する"""
    path = urlparse(url).path.lower()
    if path.endswith(".pptx"):
        return "pptx"
    if path.endswith(".ppt"):
        return "ppt"
    if path.endswith(".pdf"):
        return "pdf"
    return "unknown"


def crawl(existing_urls: Optional[set] = None) -> list:
    """
    経産省委託調査報告書ページをクロールしてメタデータリストを返す。

    Args:
        existing_urls: 既存URLセット（重複排除用）

    Returns:
        新規エントリーのメタデータリスト
    """
    if existing_urls is None:
        existing_urls = set()

    results = []

    try:
        resp = requests.get(TARGET_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        # 文字コードを自動検出
        resp.encoding = resp.apparent_encoding or "utf-8"
        logger.info(f"ページ取得成功: {TARGET_URL} ({len(resp.text)} 文字)")
    except Exception as e:
        logger.error(f"ページ取得エラー: {e}")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = "https://www.meti.go.jp"

    for a_tag in soup.find_all("a", href=True):
        try:
            href = a_tag["href"]
            if not re.search(r"\.(ppt|pptx|pdf)$", href, re.IGNORECASE):
                continue

            file_url = urljoin(base_url, href)

            if file_url in existing_urls:
                logger.debug(f"スキップ（既存）: {file_url}")
                continue

            # タイトル取得：aタグのテキスト → 親要素テキスト → ファイル名
            title = a_tag.get_text(strip=True)
            if not title:
                parent = a_tag.parent
                title = parent.get_text(strip=True) if parent else ""
            if not title:
                title = href.split("/")[-1]

            # タイトルが長すぎる場合は前後テキストから適切な部分を抽出
            if len(title) > 200:
                title = title[:200].strip()

            file_type = _get_file_type(file_url)
            fiscal_year = _extract_fiscal_year(title)
            firm_name = _extract_firm_name(title)

            # 公開日：親要素テキストから日付パターンを検索
            published_date = ""
            parent = a_tag.parent
            if parent:
                date_m = re.search(
                    r"(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})",
                    parent.get_text()
                )
                if date_m:
                    published_date = (
                        f"{date_m.group(1)}-"
                        f"{int(date_m.group(2)):02d}-"
                        f"{int(date_m.group(3)):02d}"
                    )

            entry = {
                "id": _entry_id(file_url),
                "title": title,
                "url": file_url,
                "file_type": file_type,
                "published_date": published_date,
                "fiscal_year": fiscal_year,
                "firm_name": firm_name,
                "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }
            results.append(entry)
            existing_urls.add(file_url)
            logger.info(f"取得: {title[:60]} ({file_type})")

        except Exception as e:
            logger.error(f"リンク処理エラー: {e}")
            continue

        time.sleep(0.1)

    logger.info(f"クロール完了: 新規 {len(results)} 件")
    return results


if __name__ == "__main__":
    import json
    entries = crawl()
    print(json.dumps(entries, ensure_ascii=False, indent=2))
