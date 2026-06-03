"""
ConsulSlides クローラー（NDL OpenSearch API 版）

国立国会図書館 OpenSearch API を使って経産省・防衛省・内閣府の
委託調査・宇宙政策関連文書を収集する。

GitHub Actions IP からのアクセスが官公庁 WAF でブロックされるため、
直接スクレイピングではなく NDL API（公開 API、ブロックなし）を利用する。

返却形式:
    crawl(existing_urls) -> entries_list  (dict のリスト)
"""

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

try:
    from scripts.firms import detect_firm
except ImportError:
    from firms import detect_firm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── NDL API ─────────────────────────────────────────────────────────
NDL_API = "https://iss.ndl.go.jp/api/opensearch"

# Dublin Core 名前空間
DC    = "http://purl.org/dc/elements/1.1/"
DCT   = "http://purl.org/dc/terms/"
DCNDL = "http://ndl.go.jp/dcndl/terms/"

# クエリセット: (query文字列, 最大取得件数, 省庁ヒント)
# 省庁ヒントは NDL メタデータから検出できなかった場合のフォールバック
QUERY_SETS = [
    # 経済産業省
    ('creator="経済産業省" AND title="委託調査"',  200, "経済産業省"),
    ('creator="経済産業省" AND title="調査報告"',  200, "経済産業省"),
    ('publisher="経済産業省" AND title="委託"',    100, "経済産業省"),
    ('creator="経済産業省" AND title="産業"',      100, "経済産業省"),
    # 防衛省
    ('creator="防衛省" AND title="宇宙"',          200, "防衛省"),
    ('creator="防衛省" AND title="委託調査"',      200, "防衛省"),
    ('creator="防衛省" AND title="調査研究"',      100, "防衛省"),
    ('publisher="防衛省" AND title="委託"',        100, "防衛省"),
    # 内閣府 宇宙政策委員会
    ('creator="内閣府" AND title="宇宙"',          200, "内閣府"),
    ('publisher="宇宙政策委員会"',                  100, "内閣府"),
    ('title="宇宙政策" AND creator="内閣府"',       100, "内閣府"),
    # 宇宙一般（省庁横断・信頼できる発行元に絞る）
    ('creator="JAXA" AND title="報告"',             100, ""),
    ('publisher="宇宙航空研究開発機構"',               100, ""),
]

# ソース別クエリインデックス範囲（QUERY_SETS のインデックス）
METI_INDICES = [0, 1, 2, 3]
MOD_INDICES  = [4, 5, 6, 7]
CAO_INDICES  = [8, 9, 10]

# 取得対象拡張子
ALLOWED_EXTS = (".pdf", ".pptx", ".ppt")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ─────────────────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────────────────

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


def _get_file_type(url: str) -> str:
    path = urlparse(url).path.lower().split("?")[0]
    if path.endswith(".pptx"):
        return "pptx"
    if path.endswith(".ppt"):
        return "ppt"
    if path.endswith(".pdf"):
        return "pdf"
    return "html"


def _is_file_url(url: str) -> bool:
    path = urlparse(url).path.lower().split("?")[0]
    return path.endswith(ALLOWED_EXTS)


def _make_entry(
    title: str,
    file_url: str,
    source_page: str,
    ministry: str,
    context_text: str = "",
) -> dict:
    combined = title + " " + context_text + " " + file_url
    return {
        "id": _entry_id(file_url),
        "title": title.strip()[:300] or file_url.split("/")[-1],
        "url": file_url,
        "source_page": source_page,
        "ministry": ministry,
        "file_type": _get_file_type(file_url),
        "fiscal_year": _extract_fiscal_year(combined),
        "firm_name": detect_firm(context_text) if context_text else "不明",
        "slide_type": "",
        "landscape_ratio": None,
        "page_count": 0,
        "avg_visuals": None,
        "avg_chars": None,
        "tags": {"structure": [], "design": [], "theme": [], "year": []},
        "highlight_slides": [],
        "extraction_failed": False,
        "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # NDL メタデータ（tagger.py がテーマ判定に使用）
        "ndl_subject": "",
        "ndl_description": "",
        "ndl_responsibility": "",
    }


# ─────────────────────────────────────────────────────────────────
# 省庁検出
# ─────────────────────────────────────────────────────────────────

def _detect_ministry(creator: str, publisher: str, hint: str) -> str:
    """NDL メタデータから省庁名を検出する"""
    combined = (creator or "") + " " + (publisher or "")
    if "経済産業省" in combined or "経産省" in combined:
        return "経済産業省"
    if "防衛省" in combined:
        return "防衛省"
    if "内閣府" in combined or "宇宙政策委員会" in combined:
        return "内閣府"
    return hint or "不明"


# ─────────────────────────────────────────────────────────────────
# NDL API 呼び出し
# ─────────────────────────────────────────────────────────────────

def _call_ndl_api(query: str, cnt: int, idx: int = 1) -> Optional[str]:
    """NDL OpenSearch API を呼び出して XML テキストを返す"""
    params = {
        "q": query,
        "cnt": cnt,
        "idx": idx,
        "sortorder": "sort_published_date_desc",
    }
    try:
        resp = requests.get(
            NDL_API,
            params=params,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        logger.warning(f"NDL API エラー (q={query!r}): {e}")
        return None


def _parse_ndl_xml(
    xml_text: str,
    ministry_hint: str,
    existing_urls: set,
) -> tuple:
    """
    NDL OpenSearch API の RSS XML をパースしてエントリリストを返す。

    返り値: (新規エントリリスト, クエリの総ヒット件数)
    """
    results = []
    total_hits = 0

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"NDL XML パースエラー: {e}")
        return results, 0

    channel = root.find("channel")
    if channel is None:
        return results, 0

    # openSearch:totalResults から総件数を取得
    total_el = channel.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    if total_el is not None and total_el.text:
        try:
            total_hits = int(total_el.text)
        except ValueError:
            pass

    items = channel.findall("item")
    if not items:
        return results, total_hits

    for item in items:
        # 基本フィールド
        title     = item.findtext("title", "").strip()
        link      = item.findtext("link", "").strip()

        # Dublin Core フィールド
        dc_creator   = item.findtext(f"{{{DC}}}creator",     "").strip()
        dc_publisher = item.findtext(f"{{{DC}}}publisher",   "").strip()
        dc_subject   = item.findtext(f"{{{DC}}}subject",     "").strip()
        dc_desc      = item.findtext(f"{{{DC}}}description", "").strip()

        # dcndl:responsibility（委託先・受託者が含まれることが多い）
        dcndl_resp = item.findtext(f"{{{DCNDL}}}responsibility", "").strip()

        # dc:identifier から直接ファイル URL を探す（複数要素）
        identifiers = [
            e.text.strip()
            for e in item.findall(f"{{{DC}}}identifier")
            if e.text and e.text.strip().startswith("http")
        ]

        # .pdf/.pptx/.ppt URL を優先
        file_url = ""
        for id_val in identifiers:
            if _is_file_url(id_val):
                file_url = id_val
                break

        # ファイル直リンクがない場合はスキップ（NDL カタログ HTML は UI でエラーになるため）
        if not file_url:
            continue

        url = file_url
        if url in existing_urls:
            continue

        # タイトル品質フィルタ: 10文字未満は小説・辞書等の可能性が高いためスキップ
        if len(title) < 10:
            logger.debug(f"タイトル短すぎスキップ: {title!r}")
            continue

        ministry = _detect_ministry(dc_creator, dc_publisher, ministry_hint)
        context  = " ".join(filter(None, [dc_subject, dc_desc, dcndl_resp, dc_creator]))

        entry = _make_entry(title, url, link or url, ministry, context)
        # NDL メタデータをエントリに保存（tagger がテーマ・ファーム判定に利用）
        entry["ndl_subject"]        = dc_subject
        entry["ndl_description"]    = dc_desc
        entry["ndl_responsibility"] = dcndl_resp

        results.append(entry)
        existing_urls.add(url)

    logger.debug(
        f"パース完了: {len(results)} 件 / 総ヒット {total_hits} 件 "
        f"(hint={ministry_hint!r})"
    )
    return results, total_hits


# ─────────────────────────────────────────────────────────────────
# クエリ実行
# ─────────────────────────────────────────────────────────────────

def _run_queries(query_indices: list, existing_urls: set) -> tuple:
    """
    指定インデックスのクエリを実行して結果をまとめて返す。

    返り値: (新規エントリリスト, 合計発見件数（重複含む・サイレント死検出用）)
    """
    all_entries: list = []
    total_found: int  = 0

    for idx in query_indices:
        query, cnt, hint = QUERY_SETS[idx]
        logger.info(f"NDL クエリ実行: q={query!r} cnt={cnt}")

        xml_text = _call_ndl_api(query, cnt)
        if not xml_text:
            continue

        entries, hits = _parse_ndl_xml(xml_text, hint, existing_urls)
        all_entries.extend(entries)
        total_found += hits

        logger.info(f"  → 新規 {len(entries)} 件 / 総ヒット {hits} 件")
        time.sleep(0.5)  # NDL API へのレート制限配慮

    return all_entries, total_found


# ─────────────────────────────────────────────────────────────────
# 宇宙横断クエリ（省庁不問）
# ─────────────────────────────────────────────────────────────────

def _run_general_queries(existing_urls: set) -> list:
    """省庁横断クエリを実行して結果を返す（source_counts には含めない）"""
    general_indices = [i for i in range(len(QUERY_SETS)) if i not in METI_INDICES + MOD_INDICES + CAO_INDICES]
    entries, _ = _run_queries(general_indices, existing_urls)
    return entries


# ─────────────────────────────────────────────────────────────────
# メイン crawl()
# ─────────────────────────────────────────────────────────────────

def crawl(existing_urls: Optional[set] = None) -> list:
    """
    NDL OpenSearch API を使って経産省・防衛省・内閣府文書を収集する。

    返り値:
        新規エントリーのリスト (dict のリスト)
    """
    if existing_urls is None:
        existing_urls = set()

    source_counts = {"meti": 0, "mod": 0, "cao": 0}

    # 経済産業省クエリ
    meti_entries, meti_found = _run_queries(METI_INDICES, existing_urls)
    source_counts["meti"] = meti_found
    logger.info(f"[METI] 新規={len(meti_entries)} 件 / 発見={meti_found} 件")

    # 防衛省クエリ
    mod_entries, mod_found = _run_queries(MOD_INDICES, existing_urls)
    source_counts["mod"] = mod_found
    logger.info(f"[MOD] 新規={len(mod_entries)} 件 / 発見={mod_found} 件")

    # 内閣府クエリ
    cao_entries, cao_found = _run_queries(CAO_INDICES, existing_urls)
    source_counts["cao"] = cao_found
    logger.info(f"[CAO] 新規={len(cao_entries)} 件 / 発見={cao_found} 件")

    # 宇宙横断クエリ（source_counts 対象外）
    general_entries = _run_general_queries(existing_urls)
    logger.info(f"[GENERAL] 新規={len(general_entries)} 件")

    all_results = meti_entries + mod_entries + cao_entries + general_entries

    logger.info(
        f"クロール完了: 新規合計 {len(all_results)} 件"
        f" / NDL ヒット METI={source_counts['meti']},"
        f" MOD={source_counts['mod']},"
        f" CAO={source_counts['cao']}"
    )
    return all_results


if __name__ == "__main__":
    import json

    entries = crawl()
    print(json.dumps(entries[:25], ensure_ascii=False, indent=2))
    print(f"\n合計新規件数: {len(entries)}")
