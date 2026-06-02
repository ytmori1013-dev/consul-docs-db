"""
PPT/PDFファイルのタグ付けモジュール

テキスト抽出 + キーワードスコアリングにより4軸タグを自動付与する。
- 構造タグ：キーワード出現回数でスコア化（◎=3以上 / ○=1-2）
- デザインタグ：PPT/PDFの実構造（図形数・表数・文字密度）から判定
- slide_type：1ページ目の縦横比で slide（横長）/ document（縦長）を判定
- 注目スライド：図形数・構造語・タイトル語で最大3枚を抽出

抽出失敗時もタイトルのみで必ずタグ付けして返す。
html エントリ（NDL カタログ等）はタイトル・年度からテーマ・年度タグを付与する。
"""
import logging
import os
import re
import tempfile
from typing import Optional

import requests

try:
    from scripts.firms import detect_firm
except ImportError:
    from firms import detect_firm

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MIN_TEXT_LEN = 100  # これ未満は抽出失敗とみなす

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ===== 構造タグ定義（スコアリング用・キーワード拡充版） =====
STRUCTURE_TAG_RULES = {
    "Issue Tree": [
        "論点", "課題ツリー", "イシュー", "Issue", "issue tree",
        "論点整理", "課題構造", "ツリー", "課題分解",
    ],
    "MECE": [
        "MECE", "mece", "漏れなく", "ダブりなく", "網羅的",
        "排他的", "切り口", "相互排他",
    ],
    "So What": [
        "示唆", "インプリケーション", "So What", "so what",
        "つまり", "したがって", "含意", "ポイント",
    ],
    "ピラミッド構造": [
        "ピラミッド", "結論から", "キーメッセージ", "主張", "論拠", "メッセージライン",
    ],
    "仮説思考": [
        "仮説", "Hypothesis", "hypothesis", "仮説検証", "検証", "想定", "仮説設定",
    ],
    "ファクトベース": [
        "データ分析", "定量", "統計", "回帰", "相関",
        "エビデンス", "実績値", "ファクト", "定量分析",
    ],
}

# ===== テーマタグ定義 =====
THEME_TAG_RULES = {
    "DX・デジタル": [
        "DX", "デジタル", "digital", "Digital", "IT化", "システム化",
        "AI", "人工知能", "クラウド", "データ活用", "プラットフォーム",
        "IoT", "Society 5.0", "デジタル化", "情報化", "サイバー",
        "フィンテック", "FinTech", "ブロックチェーン", "データ連携",
    ],
    "人的資本": [
        "人材", "HR", "人的資本", "採用", "育成", "スキル", "人事",
        "労働市場", "賃金", "働き方改革", "多様性", "ダイバーシティ",
        "リスキリング", "リカレント", "キャリア", "人材育成", "雇用",
        "労働力", "人手不足", "外国人材",
    ],
    "GX・脱炭素": [
        "GX", "カーボン", "脱炭素", "ESG", "気候変動", "温暖化",
        "再生可能エネルギー", "省エネ", "水素", "EV", "排出権",
        "サステナ", "カーボンニュートラル", "2050", "グリーン",
        "再エネ", "太陽光", "風力", "蓄電池",
    ],
    "スタートアップ": [
        "スタートアップ", "ベンチャー", "起業", "新規事業",
        "イノベーション", "オープンイノベーション", "エコシステム",
        "VC", "CVC", "ユニコーン", "シード", "アクセラレータ",
        "新事業", "事業創造",
    ],
    "社会保障": [
        "医療", "介護", "年金", "社会保障", "福祉", "保険",
        "少子化", "高齢化", "子育て", "育児", "出生率",
        "医療費", "健康", "病院", "薬", "製薬",
    ],
    "産業政策": [
        "産業政策", "製造業", "ものづくり", "サプライチェーン", "工場",
        "経済安全保障", "半導体", "自動車", "電池", "素材",
        "競争力", "産業振興", "中小企業", "下請け",
    ],
    "地域・まちづくり": [
        "地域振興", "地方創生", "まちづくり", "自治体", "地域活性",
        "地方", "観光", "農業", "インフラ",
        "地域経済", "地方都市", "過疎", "移住", "関係人口",
    ],
    "海外・グローバル": [
        "海外展開", "グローバル", "輸出", "国際競争", "海外進出",
        "ASEAN", "中国", "インド", "欧州", "FTA", "通商",
        "貿易", "国際市場", "外資", "直接投資",
    ],
    "防衛・宇宙": [
        "防衛", "宇宙", "安全保障", "防衛産業", "宇宙開発", "JAXA",
        "防衛装備", "ミサイル", "衛星", "宇宙利用",
        "航空機", "艦艇", "サイバー防衛", "安全保障技術",
        "防衛省", "防衛装備庁", "自衛隊", "国防",
    ],
}

# データビジュアル判定キーワード
DATA_VIZ_KW = ["グラフ", "chart", "図表", "棒グラフ", "折れ線", "散布図", "ヒートマップ", "可視化"]

# 注目スライド：タイトルに含まれると注目度アップ
HIGHLIGHT_TITLE_KW = ["全体像", "サマリ", "サマリー", "まとめ", "ロードマップ", "フレームワーク", "概要", "結論"]
HIGHLIGHT_BODY_KW  = ["論点", "示唆", "まとめ", "提言", "全体像"]


# ===== テキスト抽出 =====

def extract_from_pptx(file_path: str) -> dict:
    """
    PPTXからテキスト・構造情報を抽出する。
    Returns: text, slide_count, figure_count, table_count, slide_type, per_slide
    """
    result = {
        "text": "", "slide_count": 0, "figure_count": 0,
        "table_count": 0, "slide_type": "slide", "per_slide": [],
    }
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        prs = Presentation(file_path)
        try:
            w, h = prs.slide_width, prs.slide_height
            if w and h:
                result["slide_type"] = "slide" if w >= h else "document"
        except Exception:
            pass

        texts = []
        figure_types = {MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.AUTO_SHAPE, MSO_SHAPE_TYPE.GROUP}
        figure_count = 0
        table_count = 0
        per_slide = []

        for idx, slide in enumerate(prs.slides, start=1):
            slide_texts = []
            slide_figures = 0
            slide_title = ""
            for shape in slide.shapes:
                try:
                    if shape.shape_type in figure_types:
                        figure_count += 1
                        slide_figures += 1
                except Exception:
                    pass
                if getattr(shape, "has_table", False):
                    table_count += 1
                    try:
                        for row in shape.table.rows:
                            for cell in row.cells:
                                t = (cell.text or "").strip()
                                if t:
                                    slide_texts.append(t)
                    except Exception:
                        pass
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        line = "".join(run.text for run in para.runs).strip()
                        if line:
                            slide_texts.append(line)
                            if not slide_title:
                                slide_title = line

            slide_body = "\n".join(slide_texts)
            texts.append(slide_body)
            per_slide.append({
                "page": idx,
                "text_len": len(slide_body),
                "figures": slide_figures,
                "title": slide_title[:60],
            })

        result["text"] = "\n".join(texts)
        result["slide_count"] = len(prs.slides)
        result["figure_count"] = figure_count
        result["table_count"] = table_count
        result["per_slide"] = per_slide
    except Exception as e:
        logger.warning(f"PPTXテキスト抽出失敗: {e}")
    return result


def extract_from_pdf(file_path: str) -> dict:
    """
    PDFからテキスト・構造情報を抽出する。
    pdfplumber → 失敗/文字数不足なら pypdf/PyPDF2 でフォールバック。
    Returns: text, slide_count, figure_count, table_count, slide_type, per_slide
    """
    result = {
        "text": "", "slide_count": 0, "figure_count": 0,
        "table_count": 0, "slide_type": "", "per_slide": [],
    }

    def _set_type(w, h):
        try:
            result["slide_type"] = "slide" if float(w) > float(h) else "document"
        except Exception:
            pass

    try:
        import pdfplumber
        texts = []
        per_slide = []
        image_count = 0
        with pdfplumber.open(file_path) as pdf:
            pages = pdf.pages
            result["slide_count"] = len(pages)
            if pages:
                p0 = pages[0]
                _set_type(p0.width or 0, p0.height or 0)
            for idx, page in enumerate(pages, start=1):
                text = page.extract_text() or ""
                texts.append(text)
                imgs = len(page.images) if hasattr(page, "images") else 0
                image_count += imgs
                per_slide.append({
                    "page": idx, "text_len": len(text), "figures": imgs,
                    "title": (text.strip().split("\n")[0][:60] if text.strip() else ""),
                })
        result["text"] = "\n".join(texts)
        result["figure_count"] = image_count
        result["per_slide"] = per_slide
        if len(result["text"]) >= MIN_TEXT_LEN:
            return result
    except Exception as e:
        logger.warning(f"pdfplumber 抽出失敗（フォールバック）: {e}")

    # フォールバック: pypdf / PyPDF2
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader  # type: ignore
        reader = PdfReader(file_path)
        pages = reader.pages
        if not result["slide_count"]:
            result["slide_count"] = len(pages)
        if not result["slide_type"] and pages:
            try:
                box = pages[0].mediabox
                _set_type(box.width, box.height)
            except Exception:
                pass
        texts = []
        per_slide = []
        for idx, page in enumerate(pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            texts.append(text)
            per_slide.append({
                "page": idx, "text_len": len(text), "figures": 0,
                "title": (text.strip().split("\n")[0][:60] if text.strip() else ""),
            })
        fallback_text = "\n".join(texts)
        if len(fallback_text) > len(result["text"]):
            result["text"] = fallback_text
            result["per_slide"] = per_slide
    except Exception as e:
        logger.warning(f"PyPDF2 フォールバックも失敗: {e}")
    return result


def download_file(url: str) -> Optional[str]:
    """
    URLからファイルをダウンロードし一時ファイルパスを返す。
    .pdf/.ppt/.pptx のみ対象。失敗・サイズ超過時は None。
    タイムアウト30秒・最大3回リトライ。
    """
    url_lower = url.lower().split("?")[0]
    if not any(url_lower.endswith(ext) for ext in (".pdf", ".ppt", ".pptx")):
        return None

    import time as _time
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
            resp.raise_for_status()

            content_length = int(resp.headers.get("Content-Length", 0))
            if content_length > MAX_FILE_SIZE:
                logger.warning(f"ファイルサイズ超過({content_length}バイト)スキップ: {url}")
                return None

            suffix = (".pptx" if url_lower.endswith(".pptx") else
                      ".ppt"  if url_lower.endswith(".ppt")  else ".pdf")
            total = 0
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    total += len(chunk)
                    if total > MAX_FILE_SIZE:
                        logger.warning(f"ダウンロード中にサイズ超過: {url}")
                        f.close()
                        os.unlink(f.name)
                        return None
                return f.name
        except Exception as e:
            if attempt < 2:
                _time.sleep(2 ** attempt)
            else:
                logger.warning(f"ダウンロード失敗: {url}: {e}")
    return None


# ===== タグ判定 =====

def score_structure_tags(text: str) -> dict:
    """
    構造タグをキーワード出現回数でスコア化する。
    Returns: { tag_name: {"score": int, "mark": "◎"/"○"} }（score>=1のみ）
    """
    scores = {}
    for tag_name, keywords in STRUCTURE_TAG_RULES.items():
        score = sum(text.count(kw) for kw in keywords)
        if score >= 1:
            scores[tag_name] = {"score": score, "mark": "◎" if score >= 3 else "○"}
    return scores


def detect_theme_tags(text: str) -> list:
    """テキストからテーマタグを検出する"""
    return [name for name, kws in THEME_TAG_RULES.items() if any(kw in text for kw in kws)]


def detect_design_tags(text: str, slide_count: int, figure_count: int, table_count: int) -> list:
    """PPT/PDFの実構造からデザインタグを判定する"""
    tags = []
    if slide_count > 0:
        if figure_count / slide_count > 3:
            tags.append("図解中心")
        if len(text) / slide_count > 300:
            tags.append("テキスト重視")
    if table_count >= 3:
        tags.append("表多用")
    if sum(text.count(kw) for kw in DATA_VIZ_KW) >= 10:
        tags.append("データビジュアル")
    return tags


def detect_year_tag(title: str, fiscal_year: str) -> list:
    """年度タグを生成する"""
    if fiscal_year:
        return [fiscal_year]
    m = re.search(r"(令和\d+|平成\d+)", title)
    if m:
        return [m.group(1)]
    return []


def detect_highlight_slides(per_slide: list) -> list:
    """
    注目スライドを最大3枚抽出する。
    図形数・構造語・タイトル語でスコアリングして上位を返す。
    """
    if not per_slide:
        return []
    scored = []
    for s in per_slide:
        score = min(s.get("figures", 0), 10) * 0.5
        title = s.get("title", "")
        if any(kw in title for kw in HIGHLIGHT_TITLE_KW):
            score += 5
        if any(kw in title for kw in HIGHLIGHT_BODY_KW):
            score += 2
        if score > 0:
            scored.append((score, s["page"]))
    scored.sort(reverse=True)
    return [p for _, p in scored[:3]]


# ===== メイン処理 =====

def tag_entry(entry: dict) -> dict:
    """
    1件のエントリーにタグを付与して返す。
    - PDF/PPT: ダウンロードしてテキスト・構造抽出 → スコアリング
    - html/unknown: タイトル・年度のみでテーマ・年度タグ付与
    抽出失敗時もタイトルのみで必ず返す（後方互換）。
    """
    url = entry.get("url", "")
    title = entry.get("title", "")
    file_type = entry.get("file_type", "")
    fiscal_year = entry.get("fiscal_year", "")
    firm_name = entry.get("firm_name", "不明")

    full_text = title
    slide_count = entry.get("slide_count", 0)
    figure_count = 0
    table_count = 0
    slide_type = entry.get("slide_type", "")
    per_slide = []
    extraction_failed = entry.get("extraction_failed", False)
    file_path = None

    try:
        if file_type in ("pdf", "ppt", "pptx"):
            file_path = download_file(url)
            if file_path:
                if file_type in ("ppt", "pptx"):
                    ext = extract_from_pptx(file_path)
                else:
                    ext = extract_from_pdf(file_path)

                extracted = ext.get("text", "")
                slide_count  = ext.get("slide_count", 0) or slide_count
                figure_count = ext.get("figure_count", 0)
                table_count  = ext.get("table_count", 0)
                slide_type   = ext.get("slide_type", "") or slide_type
                per_slide    = ext.get("per_slide", [])

                logger.info(
                    f"抽出: {len(extracted)}文字 slides={slide_count} "
                    f"figs={figure_count} tables={table_count} ({title[:30]})"
                )

                if len(extracted) < MIN_TEXT_LEN:
                    extraction_failed = True
                else:
                    full_text = title + "\n" + extracted
                    if firm_name == "不明":
                        firm_name = detect_firm(extracted[:3000])
            else:
                extraction_failed = True
    except Exception as e:
        logger.warning(f"テキスト抽出スキップ ({url}): {e}")
        extraction_failed = True
    finally:
        if file_path:
            try:
                os.unlink(file_path)
            except Exception:
                pass

    if firm_name == "不明":
        firm_name = detect_firm(full_text)

    structure_scores = score_structure_tags(full_text)

    tagged = {
        **entry,
        "firm_name": firm_name,
        "slide_type": slide_type,
        "extraction_failed": extraction_failed,
        "highlight_slides": detect_highlight_slides(per_slide),
        "structure_scores": structure_scores,
        "tags": {
            "structure": list(structure_scores.keys()),
            "design": detect_design_tags(full_text, slide_count, figure_count, table_count),
            "theme": detect_theme_tags(full_text),
            "year": detect_year_tag(title, fiscal_year),
        },
        "slide_count": slide_count,
    }
    tagged.setdefault("thumbnail_urls", entry.get("thumbnail_urls", []))
    return tagged


def tag_entries(entries: list) -> list:
    """エントリーリスト全件にタグを付与する"""
    results = []
    total = len(entries)
    for i, entry in enumerate(entries):
        try:
            logger.info(f"タグ付け中 ({i + 1}/{total}): {entry.get('title', '')[:50]}")
            results.append(tag_entry(entry))
        except Exception as e:
            logger.error(f"タグ付けエラー（空タグで保存）: {e}")
            results.append({
                **entry,
                "tags": {"structure": [], "design": [], "theme": [], "year": []},
                "structure_scores": {},
                "slide_count": entry.get("slide_count", 0),
            })
    return results


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    entries = json.load(sys.stdin)
    print(json.dumps(tag_entries(entries), ensure_ascii=False, indent=2))
