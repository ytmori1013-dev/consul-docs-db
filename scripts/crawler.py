"""
経産省委託調査報告書クローラー

取得戦略（優先順位順）：
1. METI サイトマップ XML から PDF/PPT リンクを直接抽出
2. METI の主要レポートページ群を横断スキャン
3. NDL（国立国会図書館）検索 API で補完
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
METI_BASE = "https://www.meti.go.jp"

# サイトマップ XML の候補 URL
SITEMAP_URLS = [
    "https://www.meti.go.jp/sitemap.xml",
    "https://www.meti.go.jp/sitemap_index.xml",
]

# PDF/PPT を含む可能性が高い METI ページのパス
METI_REPORT_PAGES = [
    "/report/whitepaper/index.html",
    "/policy/economy/keiei_innovation/sangyokinyu/houkokusyo.html",
    "/topic/data/e90622aj.html",
]

# NDL 検索 API（SRU）
NDL_SRU_URL = "https://ndlsearch.ndl.go.jp/api/sru"

# ファーム名検出パターン（部分一致）
FIRM_PATTERNS = [
    ("McKinsey", ["McKinsey", "マッキンゼー"]),
    ("BCG", ["BCG", "ボストンコンサルティング", "Boston Consulting"]),
    ("Deloitte", ["デロイト", "Deloitte", "デロイトトーマツ"]),
    ("PwC", ["PwC", "プライスウォーター"]),
    ("Accenture", ["アクセンチュア", "Accenture"]),
    ("NRI", ["NRI", "野村総合研究所"]),
    ("三菱UFJリサーチ", ["三菱UFJ", "MURC", "三菱UFJリサーチ"]),
    ("KPMG", ["KPMG"]),
    ("EY", ["EY", "アーンスト"]),
    ("Roland Berger", ["ローランドベルガー", "Roland Berger"]),
    ("A.T. Kearney", ["ATカーニー", "A.T. Kearney", "Kearney"]),
    ("Bain", ["Bain", "ベイン"]),
    ("IBM", ["IBM", "日本IBM"]),
    ("三菱総合研究所", ["三菱総合研究所", "MRI"]),
    ("みずほリサーチ", ["みずほリサーチ", "みずほ総合研究所"]),
    ("富士通総研", ["富士通総研", "FRI"]),
    ("日立コンサルティング", ["日立コンサルティング"]),
    ("NTTデータ", ["NTTデータ", "NTT DATA"]),
    ("矢野経済研究所", ["矢野経済研究所"]),
    ("PRI", ["政策研究所", "PRI"]),
]


def _entry_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _extract_fiscal_year(text: str) -> str:
    m = re.search(r"令和(\d+)年度", text)
    if m:
        return f"令和{m.group(1)}"
    m = re.search(r"平成(\d+)年度", text)
    if m:
        return f"平成{m.group(1)}"
    m = re.search(r"R(\d+)年度", text)
    if m:
        return f"令和{m.group(1)}"
    return ""


def _extract_firm_name(text: str) -> str:
    for firm_name, patterns in FIRM_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                return firm_name
    return "不明"


def _get_file_type(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".pptx"):
        return "pptx"
    if path.endswith(".ppt"):
        return "ppt"
    if path.endswith(".pdf"):
        return "pdf"
    return "unknown"


def _make_entry(title: str, file_url: str, context_text: str = "") -> dict:
    combined = title + " " + context_text
    return {
        "id": _entry_id(file_url),
        "title": title.strip()[:300],
        "url": file_url,
        "file_type": _get_file_type(file_url),
        "published_date": "",
        "fiscal_year": _extract_fiscal_year(combined),
        "firm_name": _extract_firm_name(combined),
        "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _get(url: str, timeout: int = 8) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        logger.warning(f"取得失敗 {url}: {e}")
        return None


# ── 戦略1: サイトマップ XML ──────────────────────────────────────

def _crawl_sitemap(existing_urls: set) -> list:
    """METI サイトマップ XML から PDF/PPT URL を直接抽出する"""
    results = []
    for sitemap_url in SITEMAP_URLS:
        r = _get(sitemap_url)
        if not r:
            continue
        try:
            root = ET.fromstring(r.content)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            # サイトマップインデックスの場合
            for sitemap in root.findall(".//sm:sitemap/sm:loc", ns):
                child_url = sitemap.text.strip()
                if any(k in child_url for k in ["report", "topic", "policy"]):
                    child_r = _get(child_url)
                    if child_r:
                        results.extend(_extract_from_sitemap_xml(child_r.content, existing_urls))
                        time.sleep(0.3)
            # 通常のサイトマップ
            results.extend(_extract_from_sitemap_xml(r.content, existing_urls))
            if results:
                logger.info(f"サイトマップから {len(results)} 件取得")
                break
        except Exception as e:
            logger.warning(f"サイトマップパースエラー: {e}")
    return results


def _extract_from_sitemap_xml(content: bytes, existing_urls: set) -> list:
    results = []
    try:
        root = ET.fromstring(content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:url/sm:loc", ns):
            url = loc.text.strip()
            if re.search(r"\.(pdf|ppt|pptx)$", url, re.I) and url not in existing_urls:
                title = url.split("/")[-1]
                results.append(_make_entry(title, url))
                existing_urls.add(url)
    except Exception:
        pass
    return results


# ── 戦略2: METI ページ横断スキャン ───────────────────────────────

def _crawl_meti_pages(existing_urls: set) -> list:
    """METI の主要ページを横断して PDF/PPT リンクを抽出する"""
    results = []
    visited_pages = set()

    def scan_page(page_url: str, depth: int = 0):
        if depth > 2 or page_url in visited_pages:
            return
        visited_pages.add(page_url)

        r = _get(page_url)
        if not r:
            return

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            abs_url = urljoin(METI_BASE, href)

            if re.search(r"\.(pdf|ppt|pptx)$", abs_url, re.I):
                if abs_url not in existing_urls:
                    title = a.get_text(strip=True) or abs_url.split("/")[-1]
                    parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
                    results.append(_make_entry(title, abs_url, parent_text))
                    existing_urls.add(abs_url)

            # 同一ドメインの report/topic/policy ページは再帰スキャン
            elif (abs_url.startswith(METI_BASE)
                  and abs_url.endswith(".html")
                  and any(k in abs_url for k in ["/report/", "/topic/", "/policy/", "/research/"])
                  and depth < 2):
                time.sleep(0.2)
                scan_page(abs_url, depth + 1)

        time.sleep(0.3)

    for page_path in METI_REPORT_PAGES:
        scan_page(METI_BASE + page_path)
        if len(results) >= 200:
            break

    logger.info(f"METI ページスキャンから {len(results)} 件取得")
    return results


# ── 戦略3: NDL 検索 API ──────────────────────────────────────────

def _crawl_ndl(existing_urls: set, max_records: int = 100) -> list:
    """
    国立国会図書館 SRU API で経産省委託調査報告書を検索する。

    recordData は HTML エンコードされた文字列として返るため、
    テキストを再パースして rdf:about の URL とタイトルを抽出する。
    """
    DCNDL = "http://ndl.go.jp/dcndl/terms/"
    DCTERMS = "http://purl.org/dc/terms/"
    DC = "http://purl.org/dc/elements/1.1/"
    RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

    results = []
    query = 'creator="経済産業省" AND title="委託調査"'
    params = {
        "operation": "searchRetrieve",
        "query": query,
        "maximumRecords": max_records,
        "recordSchema": "dcndl",
        "startRecord": 1,
    }
    try:
        r = requests.get(NDL_SRU_URL, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"srw": "http://www.loc.gov/zing/srw/"}

        for record in root.findall(".//srw:record", ns):
            try:
                rec_data = record.find(".//srw:recordData", ns)
                if rec_data is None:
                    continue

                # recordData の text は HTML エンコードされた RDF/XML 文字列
                inner_text = (rec_data.text or "").strip()
                if not inner_text:
                    continue
                inner = ET.fromstring(inner_text)

                # カタログ URL を BibAdminResource の rdf:about から取得
                bib_admin = inner.find(f"{{{DCNDL}}}BibAdminResource")
                if bib_admin is None:
                    continue
                entry_url = bib_admin.get(f"{{{RDF}}}about", "")
                if not entry_url or entry_url in existing_urls:
                    continue

                bib_res = inner.find(f"{{{DCNDL}}}BibResource")
                title = ""
                published_date = ""
                creator = ""
                series_title = ""

                if bib_res is not None:
                    # タイトル（dcterms:title 優先、なければ dc:title/rdf:value）
                    t = bib_res.find(f"{{{DCTERMS}}}title")
                    if t is not None:
                        title = (t.text or "").strip()
                    if not title:
                        dc_t = bib_res.find(f"{{{DC}}}title")
                        if dc_t is not None:
                            rdf_v = dc_t.find(f".//{{{RDF}}}value")
                            if rdf_v is not None:
                                title = (rdf_v.text or "").strip()

                    # 発行日
                    d = bib_res.find(f"{{{DCTERMS}}}issued")
                    if d is not None:
                        published_date = (d.text or "").strip()

                    # 作成者（ファーム名抽出に使用）
                    c = bib_res.find(f"{{{DC}}}creator")
                    if c is not None:
                        creator = (c.text or "").strip()

                    # シリーズタイトル（年度抽出に使用）
                    s = bib_res.find(f"{{{DCNDL}}}seriesTitle")
                    if s is not None:
                        rdf_v = s.find(f".//{{{RDF}}}value")
                        if rdf_v is not None:
                            series_title = (rdf_v.text or "").strip()

                context = creator + " " + series_title
                entry = _make_entry(title or entry_url, entry_url, context)
                entry["published_date"] = published_date
                entry["file_type"] = "html"  # NDL カタログページ

                results.append(entry)
                existing_urls.add(entry_url)

            except Exception:
                continue

        logger.info(f"NDL API から {len(results)} 件取得")
    except Exception as e:
        logger.warning(f"NDL API エラー: {e}")

    return results


# ── メイン ───────────────────────────────────────────────────────

def crawl(existing_urls: Optional[set] = None) -> list:
    """
    3段階戦略でクロールし、新規エントリーを返す。

    1. METI サイトマップ XML
    2. METI ページ横断スキャン
    3. NDL 検索 API
    """
    if existing_urls is None:
        existing_urls = set()

    all_results = []

    # 戦略1
    try:
        r1 = _crawl_sitemap(existing_urls)
        all_results.extend(r1)
        logger.info(f"[戦略1 サイトマップ] {len(r1)} 件")
    except Exception as e:
        logger.error(f"戦略1 エラー: {e}")

    # 戦略2
    try:
        r2 = _crawl_meti_pages(existing_urls)
        all_results.extend(r2)
        logger.info(f"[戦略2 ページスキャン] {len(r2)} 件")
    except Exception as e:
        logger.error(f"戦略2 エラー: {e}")

    # 戦略3: NDL（METI が取れない場合も含め常に実行）
    try:
        r3 = _crawl_ndl(existing_urls)
        all_results.extend(r3)
        logger.info(f"[戦略3 NDL API] {len(r3)} 件")
    except Exception as e:
        logger.error(f"戦略3 エラー: {e}")

    logger.info(f"クロール完了: 合計 {len(all_results)} 件（新規）")
    return all_results


if __name__ == "__main__":
    import json
    entries = crawl()
    print(json.dumps(entries, ensure_ascii=False, indent=2))
