"""
経産省委託調査報告書クローラー

取得戦略（優先順位順）：
0. Playwright で METI JS ダッシュボードをレンダリングして PDF/PPT リンクを抽出
1. METI サイトマップ XML から PDF/PPT リンクを直接抽出（静的 HTTP）
2. METI の主要レポートページ群を横断スキャン（静的 HTTP）
3. NDL（国立国会図書館）検索 API で補完（常に実行）
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

# Playwright でレンダリングする METI ページ（JS ダッシュボード）
METI_PLAYWRIGHT_PAGES = [
    "https://www.meti.go.jp/topic/data/e90622aj.html",
]

# 静的 HTML スキャン対象の METI ページパス
METI_REPORT_PAGES = [
    "/report/whitepaper/index.html",
    "/policy/economy/keiei_innovation/sangyokinyu/houkokusyo.html",
    "/topic/data/e90622aj.html",
]

# サイトマップ XML の候補 URL
SITEMAP_URLS = [
    "https://www.meti.go.jp/sitemap.xml",
    "https://www.meti.go.jp/sitemap_index.xml",
]

# NDL 検索 API（SRU）
NDL_SRU_URL = "https://ndlsearch.ndl.go.jp/api/sru"

# ファーム名検出パターン（部分一致）
FIRM_PATTERNS = [
    ("McKinsey", ["McKinsey", "マッキンゼー", "マッキンゼイ"]),
    ("BCG", ["BCG", "ボストンコンサルティング", "Boston Consulting"]),
    ("Deloitte", ["デロイト", "Deloitte", "デロイトトーマツ", "DTT"]),
    ("PwC", ["PwC", "プライスウォーター", "PricewaterhouseCoopers"]),
    ("Accenture", ["アクセンチュア", "Accenture"]),
    ("NRI", ["NRI", "野村総合研究所", "野村総研"]),
    ("三菱UFJリサーチ", ["三菱UFJ", "MURC", "三菱UFJリサーチ"]),
    ("KPMG", ["KPMG", "あずさ監査法人"]),
    ("EY", ["EY", "アーンスト", "Ernst & Young", "新日本監査法人"]),
    ("Roland Berger", ["ローランドベルガー", "Roland Berger"]),
    ("A.T. Kearney", ["ATカーニー", "A.T. Kearney", "Kearney"]),
    ("Bain", ["Bain", "ベイン"]),
    ("IBM", ["IBM", "日本IBM"]),
    ("三菱総合研究所", ["三菱総合研究所", "MRI"]),
    ("みずほリサーチ", ["みずほリサーチ", "みずほ総合研究所", "みずほ情報総研"]),
    ("富士通総研", ["富士通総研", "FRI"]),
    ("日立コンサルティング", ["日立コンサルティング"]),
    ("NTTデータ", ["NTTデータ経営研究所", "NTT DATA"]),
    ("矢野経済研究所", ["矢野経済研究所"]),
    ("日本総研", ["日本総研", "日本総合研究所", "JRI"]),
    ("PwCコンサルティング", ["PwCコンサルティング"]),
    ("Strategy&", ["Strategy&", "ストラテジー"]),
    ("A.D. Little", ["A.D.リトル", "ADリトル", "Arthur D. Little"]),
    ("コーポレイトディレクション", ["コーポレイトディレクション", "CDI"]),
    ("ベリングポイント", ["ベリングポイント", "BearingPoint"]),
    ("パシフィックコンサルタンツ", ["パシフィックコンサルタンツ"]),
    ("大和総研", ["大和総研", "大和総合研究所"]),
    ("農林中金総合研究所", ["農林中金総研"]),
    ("価値総合研究所", ["価値総合研究所"]),
    ("産業能率大学", ["産業能率大学"]),
    ("電通総研", ["電通総研", "電通国際情報サービス"]),
    ("シード・プランニング", ["シード・プランニング"]),
    ("エヌ・ティ・ティ・データ経営研究所", ["NTTデータ経営研究所"]),
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
    # 委託先パターンを最優先で試みる
    m = re.search(r"委託先[：:]\s*(.{2,20}?)(?:株式会社|有限会社|合同会社|一般社団|$)", text)
    if m:
        candidate = m.group(1).strip()
        if candidate:
            for firm_name, patterns in FIRM_PATTERNS:
                for pattern in patterns:
                    if pattern in candidate:
                        return firm_name
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


def _get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        logger.warning(f"取得失敗 {url}: {e}")
        return None


# ── 戦略0: Playwright で METI JS ページをレンダリング ──────────────

def _crawl_meti_playwright(existing_urls: set) -> list:
    """
    Playwright で JavaScript レンダリングされた METI ページから
    PDF/PPT リンクとその周辺テキスト（ファーム名・年度）を抽出する。
    Playwright 未インストールまたは接続エラーの場合は空リストを返す。
    """
    results = []
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.info("Playwright 未インストール。戦略0をスキップします。")
        return results

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                ignore_https_errors=True,
            )
            page = context.new_page()

            for target_url in METI_PLAYWRIGHT_PAGES:
                try:
                    logger.info(f"Playwright: {target_url}")
                    page.goto(target_url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(2000)

                    # 全リンクを取得
                    links = page.query_selector_all("a[href]")
                    for link in links:
                        try:
                            href = link.get_attribute("href") or ""
                            if not re.search(r"\.(pdf|ppt|pptx)$", href, re.I):
                                continue
                            abs_url = href if href.startswith("http") else urljoin(METI_BASE, href)
                            if abs_url in existing_urls:
                                continue

                            link_text = (link.inner_text() or "").strip()
                            # 親要素のテキスト（ファーム名・年度が入ることが多い）
                            parent = link.evaluate("el => el.closest('tr,li,div,p') ? el.closest('tr,li,div,p').innerText : ''")
                            context_text = (parent or link_text)[:500]

                            title = link_text or abs_url.split("/")[-1]
                            entry = _make_entry(title, abs_url, context_text)
                            results.append(entry)
                            existing_urls.add(abs_url)
                        except Exception:
                            continue

                    logger.info(f"Playwright: {target_url} → {len(results)} 件取得")

                except PWTimeout:
                    logger.warning(f"Playwright タイムアウト: {target_url}")
                except Exception as e:
                    logger.warning(f"Playwright エラー ({target_url}): {e}")

            browser.close()
    except Exception as e:
        logger.warning(f"Playwright 全体エラー: {e}")

    return results


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
            for sitemap in root.findall(".//sm:sitemap/sm:loc", ns):
                child_url = sitemap.text.strip()
                if any(k in child_url for k in ["report", "topic", "policy"]):
                    child_r = _get(child_url)
                    if child_r:
                        results.extend(_extract_from_sitemap_xml(child_r.content, existing_urls))
                        time.sleep(0.3)
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
    """METI の主要ページを横断して PDF/PPT リンクを抽出する（静的 HTML）"""
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

def _crawl_ndl(existing_urls: set, max_records: int = 500) -> list:
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

                inner_text = (rec_data.text or "").strip()
                if not inner_text:
                    continue
                inner = ET.fromstring(inner_text)

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
                description = ""

                if bib_res is not None:
                    t = bib_res.find(f"{{{DCTERMS}}}title")
                    if t is not None:
                        title = (t.text or "").strip()
                    if not title:
                        dc_t = bib_res.find(f"{{{DC}}}title")
                        if dc_t is not None:
                            rdf_v = dc_t.find(f".//{{{RDF}}}value")
                            if rdf_v is not None:
                                title = (rdf_v.text or "").strip()

                    d = bib_res.find(f"{{{DCTERMS}}}issued")
                    if d is not None:
                        published_date = (d.text or "").strip()

                    c = bib_res.find(f"{{{DC}}}creator")
                    if c is not None:
                        creator = (c.text or "").strip()

                    s = bib_res.find(f"{{{DCNDL}}}seriesTitle")
                    if s is not None:
                        rdf_v = s.find(f".//{{{RDF}}}value")
                        if rdf_v is not None:
                            series_title = (rdf_v.text or "").strip()

                    # 説明文（ファーム名が含まれることがある）
                    for desc_el in bib_res.findall(f"{{{DCTERMS}}}description"):
                        description += (desc_el.text or "") + " "

                context = creator + " " + series_title + " " + description
                entry = _make_entry(title or entry_url, entry_url, context)
                entry["published_date"] = published_date
                entry["file_type"] = "html"

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
    4段階戦略でクロールし、新規エントリーを返す。

    0. Playwright で METI JS ページをレンダリング（最優先）
    1. METI サイトマップ XML
    2. METI ページ横断スキャン
    3. NDL 検索 API（常に実行）
    """
    if existing_urls is None:
        existing_urls = set()

    all_results = []

    # 戦略0: Playwright
    try:
        r0 = _crawl_meti_playwright(existing_urls)
        all_results.extend(r0)
        logger.info(f"[戦略0 Playwright] {len(r0)} 件")
    except Exception as e:
        logger.error(f"戦略0 エラー: {e}")

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

    # 戦略3: NDL（常に実行）
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
