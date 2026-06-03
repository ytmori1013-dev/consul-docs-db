"""
ConsulSlides クローラー（NDL SRU API 版）

国立国会図書館 SRU API を使って経産省・防衛省・内閣府の
委託調査・宇宙政策関連文書を収集する。

GitHub Actions IP からのアクセスが官公庁 WAF でブロックされるため、
直接スクレイピングではなく NDL SRU API（公開 API、ブロックなし）を利用する。

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

# ── NDL SRU API ──────────────────────────────────────────────────────
NDL_SRU_URL = "https://ndlsearch.ndl.go.jp/api/sru"

# 名前空間
SRW_NS     = "http://www.loc.gov/zing/srw/"
DCNDL_NS   = "http://ndl.go.jp/dcndl/terms/"
DCTERMS_NS = "http://purl.org/dc/terms/"
DC_NS      = "http://purl.org/dc/elements/1.1/"
RDF_NS     = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

# クエリセット: (CQL クエリ文字列, 最大取得件数, 省庁ヒント)
QUERY_SETS = [
    # ── 経済産業省 ─────────────────────────────────────────────
    ('creator="経済産業省" AND title="委託調査"',   200, "経済産業省"),
    ('creator="経済産業省" AND title="調査報告"',   200, "経済産業省"),
    ('creator="経済産業省" AND title="報告書"',     200, "経済産業省"),
    ('creator="経済産業省" AND title="調査"',       100, "経済産業省"),

    # ── 防衛省 ─────────────────────────────────────────────────
    ('creator="防衛省" AND title="宇宙"',           200, "防衛省"),
    ('creator="防衛省" AND title="委託調査"',        200, "防衛省"),
    ('creator="防衛省" AND title="調査研究"',        100, "防衛省"),

    # ── 内閣府 宇宙政策 ────────────────────────────────────────
    ('creator="内閣府" AND title="宇宙"',            200, "内閣府"),
    ('publisher="宇宙政策委員会"',                   100, "内閣府"),
    ('creator="内閣府" AND title="宇宙政策"',         100, "内閣府"),

    # ── 宇宙開発一般 ──────────────────────────────────────────
    ('subject="宇宙開発"',                          300, ""),
    ('title="宇宙" AND title="報告書"',              200, ""),
    ('creator="宇宙航空研究開発機構"',               200, ""),
    ('creator="JAXA"',                              100, ""),
]

# ソース別クエリインデックス範囲
METI_INDICES = [0, 1, 2, 3]
MOD_INDICES  = [4, 5, 6]
CAO_INDICES  = [7, 8, 9]

# 取得対象拡張子（直接ファイル URL の判定用）
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
    url: str,
    source_page: str,
    ministry: str,
    context_text: str = "",
) -> dict:
    combined = title + " " + context_text + " " + url
    return {
        "id": _entry_id(url),
        "title": title.strip()[:300] or url.split("/")[-1],
        "url": url,
        "source_page": source_page,
        "ministry": ministry,
        "file_type": _get_file_type(url),
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
        "ndl_subject": "",
        "ndl_description": "",
        "ndl_responsibility": "",
    }


# ─────────────────────────────────────────────────────────────────
# 省庁検出
# ─────────────────────────────────────────────────────────────────

def _detect_ministry(creator: str, publisher: str, hint: str) -> str:
    combined = (creator or "") + " " + (publisher or "")
    if "経済産業省" in combined or "経産省" in combined:
        return "経済産業省"
    if "防衛省" in combined:
        return "防衛省"
    if "内閣府" in combined or "宇宙政策委員会" in combined:
        return "内閣府"
    return hint or "不明"


# ─────────────────────────────────────────────────────────────────
# NDL SRU API 呼び出し
# ─────────────────────────────────────────────────────────────────

def _call_sru(cql_query: str, max_records: int, start_record: int = 1) -> Optional[str]:
    """NDL SRU API を呼び出して XML テキストを返す。"""
    params = {
        "operation": "searchRetrieve",
        "query": cql_query,
        "maximumRecords": max_records,
        "startRecord": start_record,
        "recordSchema": "dcndl",
    }
    try:
        resp = requests.get(
            NDL_SRU_URL,
            params=params,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        logger.warning(f"NDL SRU API エラー (query={cql_query!r}): {e}")
        return None


def _text_from_el(el) -> str:
    """
    RDF/XML 要素から文字列を取得する。
    直接テキスト → rdf:value → rdf:Description/rdf:value の優先順で探す。
    """
    if el is None:
        return ""
    if el.text and el.text.strip():
        return el.text.strip()
    rdf_val = el.find(f"{{{RDF_NS}}}value")
    if rdf_val is not None and rdf_val.text:
        return rdf_val.text.strip()
    desc = el.find(f"{{{RDF_NS}}}Description")
    if desc is not None:
        val = desc.find(f"{{{RDF_NS}}}value")
        if val is not None and val.text:
            return val.text.strip()
    return ""


def _inner_xml_from_record_data(rec_data) -> Optional[ET.Element]:
    """
    srw:recordData 要素から dcndl 内部 XML を取得する。
    子要素として含む場合と、テキストとして HTML エスケープされている場合の両方に対応。
    """
    children = list(rec_data)
    if children:
        return children[0]
    inner_text = (rec_data.text or "").strip()
    if not inner_text:
        return None
    try:
        return ET.fromstring(inner_text)
    except ET.ParseError:
        return None


def _parse_sru_response(
    xml_text: str,
    ministry_hint: str,
    existing_urls: set,
) -> tuple:
    """
    NDL SRU の dcndl レスポンス XML をパースしてエントリリストを返す。

    返り値: (新規エントリリスト, クエリの総ヒット件数)
    """
    results = []
    total_hits = 0

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"SRU XML パースエラー: {e}")
        return results, 0

    srw_ns = {"srw": SRW_NS}

    num_el = root.find(".//srw:numberOfRecords", srw_ns)
    if num_el is not None and num_el.text:
        try:
            total_hits = int(num_el.text)
        except ValueError:
            pass

    for record in root.findall(".//srw:record", srw_ns):
        rec_data = record.find(f"{{{SRW_NS}}}recordData")
        if rec_data is None:
            continue

        inner = _inner_xml_from_record_data(rec_data)
        if inner is None:
            continue

        # NDL カタログ URL (BibAdminResource/@rdf:about)
        bib_admin = inner.find(f".//{{{DCNDL_NS}}}BibAdminResource")
        catalog_url = ""
        if bib_admin is not None:
            catalog_url = bib_admin.get(f"{{{RDF_NS}}}about", "")

        # 書誌メタデータ (BibResource)
        bib_res = inner.find(f".//{{{DCNDL_NS}}}BibResource")
        meta_root = bib_res if bib_res is not None else inner

        # タイトル
        title = ""
        for tag in [f"{{{DCTERMS_NS}}}title", f"{{{DC_NS}}}title"]:
            el = meta_root.find(f".//{tag}")
            if el is None:
                el = inner.find(f".//{tag}")
            t = _text_from_el(el)
            if t:
                title = t
                break

        if not title or len(title) < 10:
            logger.debug(f"タイトル不足スキップ: {title!r}")
            continue

        # 著者・出版者
        creator_el = inner.find(f".//{{{DC_NS}}}creator")
        creator = _text_from_el(creator_el)

        publisher = ""
        for tag in [f"{{{DCTERMS_NS}}}publisher", f"{{{DC_NS}}}publisher"]:
            el = inner.find(f".//{tag}")
            t = _text_from_el(el)
            if t:
                publisher = t
                break

        # 件名
        subjects = []
        for el in inner.findall(f".//{{{DCTERMS_NS}}}subject"):
            t = _text_from_el(el)
            if t:
                subjects.append(t)
        for el in inner.findall(f".//{{{DC_NS}}}subject"):
            t = _text_from_el(el)
            if t:
                subjects.append(t)
        ndl_subject = " ".join(subjects)

        # 説明
        ndl_description = " ".join(
            _text_from_el(e)
            for e in inner.findall(f".//{{{DCTERMS_NS}}}description")
        ).strip()

        # 責任表示
        resp_el = inner.find(f".//{{{DCNDL_NS}}}responsibility")
        ndl_responsibility = _text_from_el(resp_el)

        # dc:identifier から直接ファイル URL を探す（PDF/PPTX 優先）
        file_url = ""
        ndl_id_url = ""
        for id_el in inner.findall(f".//{{{DC_NS}}}identifier"):
            id_text = (id_el.text or "").strip()
            if not id_text.startswith("http"):
                continue
            if _is_file_url(id_text):
                file_url = id_text
                break
            if not ndl_id_url:
                ndl_id_url = id_text

        # URL 優先順位: 直接ファイル URL > NDL 識別子 URL > BibAdminResource URL
        url = file_url or ndl_id_url or catalog_url
        if not url:
            continue

        if url in existing_urls:
            continue

        ministry = _detect_ministry(creator, publisher, ministry_hint)
        context  = " ".join(filter(None, [ndl_subject, ndl_description, ndl_responsibility, creator]))

        entry = _make_entry(title, url, catalog_url or url, ministry, context)
        entry["ndl_subject"]        = ndl_subject
        entry["ndl_description"]    = ndl_description
        entry["ndl_responsibility"] = ndl_responsibility

        results.append(entry)
        existing_urls.add(url)

    logger.debug(
        f"SRU パース完了: {len(results)} 件 / 総ヒット {total_hits} 件 "
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
        cql_query, cnt, hint = QUERY_SETS[idx]
        logger.info(f"NDL SRU クエリ実行: {cql_query!r} cnt={cnt}")

        xml_text = _call_sru(cql_query, cnt)
        if not xml_text:
            continue

        entries, hits = _parse_sru_response(xml_text, hint, existing_urls)
        all_entries.extend(entries)
        total_found += hits

        logger.info(f"  → 新規 {len(entries)} 件 / 総ヒット {hits} 件")
        time.sleep(0.5)

    return all_entries, total_found


def _run_general_queries(existing_urls: set) -> list:
    """省庁横断クエリを実行して結果を返す（source_counts には含めない）"""
    general_indices = [
        i for i in range(len(QUERY_SETS))
        if i not in METI_INDICES + MOD_INDICES + CAO_INDICES
    ]
    entries, _ = _run_queries(general_indices, existing_urls)
    return entries


# ─────────────────────────────────────────────────────────────────
# メイン crawl()
# ─────────────────────────────────────────────────────────────────

def crawl(existing_urls: Optional[set] = None) -> list:
    """
    NDL SRU API を使って経産省・防衛省・内閣府文書を収集する。

    返り値:
        新規エントリーのリスト (dict のリスト)
    """
    if existing_urls is None:
        existing_urls = set()

    source_counts = {"meti": 0, "mod": 0, "cao": 0}

    meti_entries, meti_found = _run_queries(METI_INDICES, existing_urls)
    source_counts["meti"] = meti_found
    logger.info(f"[METI] 新規={len(meti_entries)} 件 / 発見={meti_found} 件")

    mod_entries, mod_found = _run_queries(MOD_INDICES, existing_urls)
    source_counts["mod"] = mod_found
    logger.info(f"[MOD] 新規={len(mod_entries)} 件 / 発見={mod_found} 件")

    cao_entries, cao_found = _run_queries(CAO_INDICES, existing_urls)
    source_counts["cao"] = cao_found
    logger.info(f"[CAO] 新規={len(cao_entries)} 件 / 発見={cao_found} 件")

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
