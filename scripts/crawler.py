"""
ConsulSlides クローラー

3ソースから官公庁委託調査・審議会PDFを収集する。

ソース1: 経済産業省 審議会（METI）
ソース2: 防衛省 宇宙関連（MOD）
ソース3: 内閣府 宇宙政策委員会（CAO）
"""

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from scripts.firms import detect_firm
except ImportError:
    from firms import detect_firm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 取得対象ファイル拡張子
ALLOWED_EXTS = (".pdf", ".pptx", ".ppt")

# Chrome 120 偽装 User-Agent
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── ソース1: METI 審議会 ──────────────────────────────────────────
# 年度別インデックスページをすべて対象にする
METI_SHINGIKAI_URLS = [
    "https://www.meti.go.jp/shingikai/index.html",
    "https://www.meti.go.jp/shingikai/index_2025.html",
    "https://www.meti.go.jp/shingikai/index_2024.html",
    "https://www.meti.go.jp/shingikai/index_2023.html",
]
METI_BASE = "https://www.meti.go.jp"

# ── ソース2: 防衛省 宇宙関連 ─────────────────────────────────────
# 起点URL群（宇宙・SDA関連）
MOD_START_URLS = [
    "https://www.mod.go.jp/j/policy/space/",
    "https://www.mod.go.jp/atla/",
    "https://www.mod.go.jp/j/policy/hyouka/",
]
# 404フォールバック用
MOD_FALLBACK_URL = "https://www.mod.go.jp/j/"
# 宇宙関連キーワード（リンクアンカー/ファイル名/URL中に含まれるもの）
MOD_SPACE_KEYWORDS = ["宇宙", "衛星", "SDA", "SSA", "コンステ"]

# ── ソース3: 内閣府 宇宙政策委員会 ──────────────────────────────
CAO_SPACE_URL = "https://www8.cao.go.jp/space/"


# ─────────────────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────────────────

def _entry_id(url: str) -> str:
    """URL の md5 ハッシュをエントリーIDとして返す"""
    return hashlib.md5(url.encode()).hexdigest()


def _extract_fiscal_year(text: str) -> str:
    """テキストから令和X年度/平成X年度/RX年度を検出して返す"""
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
    """firms.detect_firm に委譲してファーム名を検出する"""
    return detect_firm(text)


def _get_file_type(url: str) -> str:
    """URLからファイル種別（pdf/pptx/ppt）を判定する"""
    path = urlparse(url).path.lower()
    if path.endswith(".pptx"):
        return "pptx"
    if path.endswith(".ppt"):
        return "ppt"
    if path.endswith(".pdf"):
        return "pdf"
    return "unknown"


def _is_allowed_ext(url: str) -> bool:
    """URLが収集対象の拡張子か判定する"""
    path = urlparse(url).path.lower().split("?")[0]
    return path.endswith(ALLOWED_EXTS)


def _make_entry(
    title: str,
    file_url: str,
    source_page: str,
    ministry: str,
    context_text: str = "",
) -> dict:
    """エントリー辞書を生成して返す"""
    combined = title + " " + context_text + " " + file_url
    return {
        "id": _entry_id(file_url),
        "title": title.strip()[:300] or file_url.split("/")[-1],
        "url": file_url,
        "source_page": source_page,
        "ministry": ministry,
        "file_type": _get_file_type(file_url),
        "fiscal_year": _extract_fiscal_year(combined),
        "firm_name": _extract_firm_name(combined),
        "slide_type": "",
        "landscape_ratio": None,
        "page_count": 0,
        "avg_visuals": None,
        "avg_chars": None,
        "tags": {"structure": [], "design": [], "theme": [], "year": []},
        "highlight_slides": [],
        "extraction_failed": False,
        "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ─────────────────────────────────────────────────────────────────
# HTTP ユーティリティ
# ─────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 30, retries: int = 3) -> Optional[requests.Response]:
    """GETリクエスト。タイムアウト30秒・最大3回リトライ（指数バックオフ）。"""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r
        except requests.HTTPError as e:
            # 4xxは即座に諦める（リトライ不要）
            if e.response is not None and 400 <= e.response.status_code < 500:
                logger.debug(f"HTTP {e.response.status_code}: {url}")
                return None
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.debug(f"リトライ {attempt + 1}/{retries} ({wait}s): {url}")
                time.sleep(wait)
            else:
                logger.warning(f"取得失敗 {url}: {e}")
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                logger.warning(f"取得失敗 {url}: {e}")
    return None


def _url_exists(url: str, timeout: int = 30) -> bool:
    """
    HEADリクエストでURLの存在確認。200のみ採用。
    HEAD非対応サーバーに対しては bytes=0-0 GETでフォールバック。
    """
    try:
        r = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return True
        # HEAD を拒否するサーバーは GET で確認
        if r.status_code in (403, 405, 501):
            raise requests.RequestException("HEAD not supported")
        return False
    except Exception:
        try:
            get_headers = {**HEADERS, "Range": "bytes=0-0"}
            r = requests.get(
                url, headers=get_headers, timeout=timeout, stream=True, allow_redirects=True
            )
            ok = r.status_code in (200, 206)
            r.close()
            return ok
        except Exception:
            return False


def _collect_file_links(
    soup: BeautifulSoup,
    base_url: str,
    existing_urls: set,
) -> list[tuple[str, str, str]]:
    """
    BeautifulSoupのオブジェクトから許可拡張子のリンクを収集する。
    返り値: [(abs_url, anchor_text, context_text), ...]
    既存URLは除外するが URL存在確認（_url_exists）はここでは行わない。
    """
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        abs_url = urljoin(base_url, href)
        if not _is_allowed_ext(abs_url):
            continue
        if abs_url in existing_urls:
            continue
        anchor_text = a.get_text(strip=True)
        # 親要素のテキストをコンテキストとして取得
        parent_text = ""
        if a.parent:
            parent_text = a.parent.get_text(" ", strip=True)[:500]
        results.append((abs_url, anchor_text, parent_text))
    return results


# ─────────────────────────────────────────────────────────────────
# ソース1: METI 審議会
# ─────────────────────────────────────────────────────────────────

def _crawl_meti(existing_urls: set) -> Tuple[list, int]:
    """
    経済産業省 審議会ページからPDFを収集する。
    起点ページ（index.html / index_2025.html 等）→ 各審議会ページ → PDF の2階層を辿る。

    返り値: (新規エントリーリスト, 合計リンク発見数（既存含む）)
    """
    results = []
    total_found = [0]  # ネスト関数からも更新できるようリストで保持
    visited_pages: set = set()

    def visit_page(page_url: str, depth: int, source_page: str) -> None:
        """depth=0: 起点, depth=1: 審議会ページ, depth=2: PDF収集のみ"""
        if page_url in visited_pages:
            return
        visited_pages.add(page_url)

        r = _get(page_url)
        if not r:
            return

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = urljoin(page_url, href)

            if _is_allowed_ext(abs_url):
                total_found[0] += 1  # 既存・重複含む全リンク数（サイレント死検出用）
                if abs_url in existing_urls:
                    continue
                if not _url_exists(abs_url):
                    logger.debug(f"METI: リンク切れ除外: {abs_url}")
                    continue
                anchor_text = a.get_text(strip=True)
                parent_text = a.parent.get_text(" ", strip=True)[:500] if a.parent else ""
                title = anchor_text or abs_url.split("/")[-1]
                entry = _make_entry(title, abs_url, page_url, "経済産業省", parent_text)
                results.append(entry)
                existing_urls.add(abs_url)

            elif depth < 2:
                # 審議会サブページへのリンクを辿る（METI内かつ.html）
                parsed = urlparse(abs_url)
                if (
                    parsed.netloc == "www.meti.go.jp"
                    and parsed.path.endswith((".html", ".htm"))
                    and "/shingikai/" in parsed.path
                    and abs_url not in visited_pages
                ):
                    time.sleep(0.3)
                    visit_page(abs_url, depth + 1, page_url)

    for start_url in METI_SHINGIKAI_URLS:
        try:
            logger.info(f"METI: 起点URL訪問: {start_url}")
            visit_page(start_url, depth=0, source_page=start_url)
        except Exception as e:
            logger.error(f"METI: 起点URL処理エラー ({start_url}): {e}")

    logger.info(
        f"METI: 合計発見={total_found[0]} 件 / 新規={len(results)} 件 "
        f"(訪問ページ数={len(visited_pages)})"
    )
    return results, total_found[0]


# ─────────────────────────────────────────────────────────────────
# ソース2: 防衛省 宇宙関連
# ─────────────────────────────────────────────────────────────────

def _is_space_related(url: str, anchor_text: str) -> bool:
    """URLまたはアンカーテキストが宇宙関連キーワードを含むか判定する"""
    combined = url + " " + anchor_text
    return any(kw in combined for kw in MOD_SPACE_KEYWORDS)


def _crawl_mod_from(start_url: str, existing_urls: set) -> Tuple[list, int]:
    """
    指定URLの1階層先PDFのうち、宇宙関連キーワードを含むものを収集する。
    返り値: (新規エントリーリスト, 合計リンク発見数（既存含む）)
    """
    results = []
    total_found = 0
    r = _get(start_url)
    if not r:
        return results, 0

    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        abs_url = urljoin(start_url, href)
        if not _is_allowed_ext(abs_url):
            continue
        anchor_text = a.get_text(strip=True)
        filename = urlparse(abs_url).path.split("/")[-1]
        if not _is_space_related(abs_url, anchor_text + " " + filename):
            continue
        total_found += 1  # 宇宙関連の全リンク数（既存含む）
        if abs_url in existing_urls:
            continue
        if not _url_exists(abs_url):
            logger.debug(f"MOD: リンク切れ除外: {abs_url}")
            continue
        parent_text = a.parent.get_text(" ", strip=True)[:500] if a.parent else ""
        title = anchor_text or filename
        entry = _make_entry(title, abs_url, start_url, "防衛省", parent_text)
        results.append(entry)
        existing_urls.add(abs_url)

    return results, total_found


def _crawl_mod(existing_urls: set) -> Tuple[list, int]:
    """
    防衛省の宇宙関連PDFを収集する。
    各起点URLの1階層先のPDFのみ。404の場合はフォールバックURLから探索。
    返り値: (新規エントリーリスト, 合計リンク発見数（既存含む）)
    """
    results = []
    total_found = 0
    any_success = False

    for start_url in MOD_START_URLS:
        try:
            found, found_count = _crawl_mod_from(start_url, existing_urls)
            results.extend(found)
            total_found += found_count
            if found_count > 0:
                any_success = True
            logger.debug(f"MOD: {start_url} → 新規{len(found)}件 / 発見{found_count}件")
        except Exception as e:
            logger.error(f"MOD: URL処理エラー ({start_url}): {e}")

    # 404フォールバック: 何も取れなかった場合にフォールバックURLから探索
    if not any_success:
        logger.info(f"MOD: フォールバック探索: {MOD_FALLBACK_URL}")
        try:
            fallback, fb_count = _crawl_mod_from(MOD_FALLBACK_URL, existing_urls)
            results.extend(fallback)
            total_found += fb_count
            logger.info(f"MOD: フォールバックから {len(fallback)} 件取得")
        except Exception as e:
            logger.error(f"MOD: フォールバックエラー: {e}")

    logger.info(f"MOD: 合計発見={total_found} 件 / 新規={len(results)} 件")
    return results, total_found


# ─────────────────────────────────────────────────────────────────
# ソース3: 内閣府 宇宙政策委員会
# ─────────────────────────────────────────────────────────────────

def _crawl_cao(existing_urls: set) -> Tuple[list, int]:
    """
    内閣府宇宙政策委員会ページから2階層まで配下のPDFを収集する。
    返り値: (新規エントリーリスト, 合計リンク発見数（既存含む）)
    """
    results = []
    total_found = [0]  # ネスト関数からも更新できるようリストで保持
    visited_pages: set = set()

    def visit_page(page_url: str, depth: int) -> None:
        """depth=0: 起点, depth=1: 1階層, depth=2: 2階層（PDFのみ収集）"""
        if page_url in visited_pages:
            return
        visited_pages.add(page_url)

        r = _get(page_url)
        if not r:
            return

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = urljoin(page_url, href)

            if _is_allowed_ext(abs_url):
                total_found[0] += 1  # 既存・重複含む全リンク数（サイレント死検出用）
                if abs_url in existing_urls:
                    continue
                if not _url_exists(abs_url):
                    logger.debug(f"CAO: リンク切れ除外: {abs_url}")
                    continue
                anchor_text = a.get_text(strip=True)
                parent_text = a.parent.get_text(" ", strip=True)[:500] if a.parent else ""
                title = anchor_text or abs_url.split("/")[-1]
                entry = _make_entry(title, abs_url, page_url, "内閣府", parent_text)
                results.append(entry)
                existing_urls.add(abs_url)

            elif depth < 2:
                # cao.go.jp 配下の.htmlページを辿る
                parsed = urlparse(abs_url)
                if (
                    "cao.go.jp" in parsed.netloc
                    and parsed.path.endswith((".html", ".htm", "/"))
                    and abs_url.startswith(CAO_SPACE_URL)
                    and abs_url not in visited_pages
                ):
                    time.sleep(0.3)
                    visit_page(abs_url, depth + 1)

    try:
        logger.info(f"CAO: 起点URL訪問: {CAO_SPACE_URL}")
        visit_page(CAO_SPACE_URL, depth=0)
    except Exception as e:
        logger.error(f"CAO: 起点URL処理エラー: {e}")

    logger.info(
        f"CAO: 合計発見={total_found[0]} 件 / 新規={len(results)} 件 "
        f"(訪問ページ数={len(visited_pages)})"
    )
    return results, total_found[0]


# ─────────────────────────────────────────────────────────────────
# メイン crawl()
# ─────────────────────────────────────────────────────────────────

def crawl(
    existing_urls: Optional[set] = None,
) -> Tuple[list, dict]:
    """
    3ソースをクロールして新規エントリーと件数統計を返す。

    返り値:
        (entries_list, source_counts)
        source_counts = {"meti": N, "mod": N, "cao": N}

    後方互換:
        entries, counts = crawl(...)  で受け取れる。
    """
    if existing_urls is None:
        existing_urls = set()

    meti_results: list = []
    mod_results: list = []
    cao_results: list = []
    # source_counts はリンク「発見数」（既存含む）をカウントする
    # → 0 = ソースが完全に死んでいる（サイレント死検出用）
    source_counts = {"meti": 0, "mod": 0, "cao": 0}

    # ソース1: METI 審議会
    try:
        meti_results, meti_found = _crawl_meti(existing_urls)
        source_counts["meti"] = meti_found
        logger.info(f"[METI] 新規={len(meti_results)} 件 / 発見={meti_found} 件")
    except Exception as e:
        logger.error(f"[METI] クロール全体エラー: {e}")

    # ソース2: 防衛省 宇宙関連
    try:
        mod_results, mod_found = _crawl_mod(existing_urls)
        source_counts["mod"] = mod_found
        logger.info(f"[MOD] 新規={len(mod_results)} 件 / 発見={mod_found} 件")
    except Exception as e:
        logger.error(f"[MOD] クロール全体エラー: {e}")

    # ソース3: 内閣府 宇宙政策委員会
    try:
        cao_results, cao_found = _crawl_cao(existing_urls)
        source_counts["cao"] = cao_found
        logger.info(f"[CAO] 新規={len(cao_results)} 件 / 発見={cao_found} 件")
    except Exception as e:
        logger.error(f"[CAO] クロール全体エラー: {e}")

    all_results = meti_results + mod_results + cao_results

    logger.info(
        f"クロール完了: 新規合計 {len(all_results)} 件"
        f" / リンク発見 METI={source_counts['meti']},"
        f" MOD={source_counts['mod']},"
        f" CAO={source_counts['cao']}"
    )
    return all_results, source_counts


if __name__ == "__main__":
    import json

    entries, counts = crawl()
    print(json.dumps(entries[:25], ensure_ascii=False, indent=2))
    print(f"\nソース別件数: {counts}")
