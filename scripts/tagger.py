"""
PPT/PDFファイルのタグ付けモジュール

テキスト抽出 + キーワードマッチングにより4軸タグを自動付与する。
テキスト抽出失敗時はタイトルのみでタグ付けしてスキップしない。
"""
import logging
import os
import re
import tempfile
from typing import Optional

import requests

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ===== 構造タグ定義 =====
STRUCTURE_TAG_RULES = {
    "Issue Tree": ["論点", "課題ツリー", "イシュー", "Issue", "issue tree"],
    "MECE": ["MECE", "mece", "漏れなくダブりなく", "網羅"],
    "So What": ["示唆", "インプリケーション", "So What", "so what", "つまり"],
    "ピラミッド構造": ["ピラミッド", "結論から", "メッセージライン"],
    "Hypothesis driven": ["仮説", "Hypothesis", "hypothesis", "仮説検証"],
    "データドリブン": ["データ分析", "定量", "統計", "回帰分析"],
}

# ===== テーマタグ定義 =====
THEME_TAG_RULES = {
    "DX・デジタル": ["DX", "デジタル", "digital", "Digital", "IT化", "システム化"],
    "人的資本": ["人材", "HR", "人的資本", "採用", "育成", "スキル", "人事"],
    "GX・脱炭素": ["GX", "カーボン", "脱炭素", "ESG", "気候変動", "温暖化"],
    "スタートアップ": ["スタートアップ", "ベンチャー", "起業", "新規事業"],
    "社会保障": ["医療", "介護", "年金", "社会保障", "福祉", "保険"],
    "産業政策": ["産業政策", "製造業", "ものづくり", "サプライチェーン", "工場"],
    "地域・まちづくり": ["地域振興", "地方創生", "まちづくり", "自治体", "地域活性"],
    "海外・グローバル": ["海外展開", "グローバル", "輸出", "国際競争", "海外進出"],
}

# データビジュアライズキーワード
DATA_VIZ_KW = ["グラフ", "チャート", "chart", "graph", "棒グラフ", "折れ線", "散布図", "ヒートマップ", "可視化"]

# インフォグラフィックキーワード
INFOGRAPHIC_KW = ["アイコン", "イラスト", "icon", "illustration", "インフォグラフィック"]

# crawler.py と同じファームパターン（循環インポート回避のため複製）
FIRM_PATTERNS = [
    ("McKinsey", ["McKinsey", "マッキンゼー"]),
    ("BCG", ["BCG", "ボストンコンサルティング", "Boston Consulting"]),
    ("Deloitte", ["デロイト", "Deloitte", "デロイトトーマツ"]),
    ("PwC", ["PwC", "プライスウォーター"]),
    ("Accenture", ["アクセンチュア", "Accenture"]),
    ("NRI", ["NRI", "野村総合研究所"]),
    ("三菱UFJリサーチ", ["三菱UFJ", "MURC"]),
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
]


def _extract_firm_from_text(text: str) -> str:
    """テキストからファーム名を抽出。見つからない場合は '不明' を返す"""
    for firm_name, patterns in FIRM_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                return firm_name
    return "不明"


# ===== テキスト抽出 =====

def extract_text_from_pptx(file_path: str) -> tuple:
    """
    PPTXからテキストを抽出する。

    Returns:
        (テキスト全文, スライド枚数, 図形数)
    """
    try:
        from pptx import Presentation

        prs = Presentation(file_path)
        texts = []
        slide_count = len(prs.slides)
        shape_count = 0

        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        line = "".join(run.text for run in para.runs).strip()
                        if line:
                            texts.append(line)
                else:
                    # テキスト以外の図形（グラフ・画像など）
                    shape_count += 1

        return "\n".join(texts), slide_count, shape_count

    except Exception as e:
        logger.warning(f"PPTXテキスト抽出失敗: {e}")
        return "", 0, 0


def extract_text_from_pdf(file_path: str) -> tuple:
    """
    PDFからテキストを抽出する。

    Returns:
        (テキスト全文, ページ数, 推定図表数)
    """
    try:
        import pdfplumber

        texts = []
        page_count = 0
        image_count = 0

        with pdfplumber.open(file_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)
                if hasattr(page, "images"):
                    image_count += len(page.images)

        return "\n".join(texts), page_count, image_count

    except Exception as e:
        logger.warning(f"PDFテキスト抽出失敗: {e}")
        return "", 0, 0


def download_file(url: str) -> Optional[str]:
    """
    URLからファイルをダウンロードし一時ファイルパスを返す。
    失敗またはサイズ超過時はNoneを返す。
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()

        # ファイルサイズ確認
        content_length = int(resp.headers.get("Content-Length", 0))
        if content_length > MAX_FILE_SIZE:
            logger.warning(f"ファイルサイズ超過({content_length}バイト)スキップ: {url}")
            return None

        url_lower = url.lower()
        if ".pptx" in url_lower:
            suffix = ".pptx"
        elif ".ppt" in url_lower:
            suffix = ".ppt"
        elif ".pdf" in url_lower:
            suffix = ".pdf"
        else:
            suffix = ".bin"

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
        logger.warning(f"ダウンロード失敗: {url}: {e}")
        return None


# ===== タグ判定 =====

def detect_structure_tags(text: str) -> list:
    """テキストから構造タグを検出する"""
    tags = []
    for tag_name, keywords in STRUCTURE_TAG_RULES.items():
        if any(kw in text for kw in keywords):
            tags.append(tag_name)
    return tags


def detect_theme_tags(text: str) -> list:
    """テキストからテーマタグを検出する"""
    tags = []
    for tag_name, keywords in THEME_TAG_RULES.items():
        if any(kw in text for kw in keywords):
            tags.append(tag_name)
    return tags


def detect_design_tags(text: str, slide_count: int, shape_count: int) -> list:
    """スライド枚数・図形数・キーワードからデザインタグを検出する"""
    tags = []

    if slide_count > 0:
        ratio = shape_count / slide_count
        if ratio > 0.6:
            tags.append("図解中心")
        elif ratio < 0.3:
            tags.append("テキスト重視")

    if sum(1 for kw in DATA_VIZ_KW if kw in text) >= 2:
        tags.append("データビジュアライズ")

    if sum(1 for kw in INFOGRAPHIC_KW if kw in text) >= 2:
        tags.append("インフォグラフィック")

    return tags


def detect_year_tag(title: str, fiscal_year: str) -> list:
    """年度タグを生成する"""
    if fiscal_year:
        return [fiscal_year]
    m = re.search(r"(令和\d+|平成\d+)", title)
    if m:
        return [m.group(1)]
    return []


# ===== メイン処理 =====

def tag_entry(entry: dict) -> dict:
    """
    1件のエントリーにタグを付与して返す。
    テキスト抽出に失敗してもタイトルのみでタグ付けして必ず返す。
    """
    url = entry.get("url", "")
    title = entry.get("title", "")
    file_type = entry.get("file_type", "")
    fiscal_year = entry.get("fiscal_year", "")
    firm_name = entry.get("firm_name", "不明")

    # テキスト抽出を試みる（失敗時はタイトルのみ）
    full_text = title
    slide_count = 0
    shape_count = 0
    file_path = None

    try:
        file_path = download_file(url)
        if file_path:
            if file_type in ("ppt", "pptx"):
                extracted, slide_count, shape_count = extract_text_from_pptx(file_path)
                if extracted:
                    full_text = title + "\n" + extracted
            elif file_type == "pdf":
                extracted, slide_count, shape_count = extract_text_from_pdf(file_path)
                if extracted:
                    full_text = title + "\n" + extracted
    except Exception as e:
        logger.warning(f"テキスト抽出スキップ ({url}): {e}")
    finally:
        if file_path:
            try:
                os.unlink(file_path)
            except Exception:
                pass

    # ファーム名が未検出の場合、抽出テキスト全体で再試行
    if firm_name == "不明" and full_text != title:
        firm_name = _extract_firm_from_text(full_text)

    tagged = {
        **entry,
        "firm_name": firm_name,
        "tags": {
            "structure": detect_structure_tags(full_text),
            "design": detect_design_tags(full_text, slide_count, shape_count),
            "theme": detect_theme_tags(full_text),
            "year": detect_year_tag(title, fiscal_year),
        },
        "slide_count": slide_count,
    }
    return tagged


def tag_entries(entries: list) -> list:
    """エントリーリスト全件にタグを付与する"""
    results = []
    total = len(entries)
    for i, entry in enumerate(entries):
        try:
            logger.info(f"タグ付け中 ({i + 1}/{total}): {entry.get('title', '')[:50]}")
            tagged = tag_entry(entry)
            results.append(tagged)
        except Exception as e:
            logger.error(f"タグ付けエラー（空タグで保存）: {e}")
            # エラー時も空タグで保存してスキップしない
            results.append({
                **entry,
                "tags": {"structure": [], "design": [], "theme": [], "year": []},
                "slide_count": 0,
            })
    return results


if __name__ == "__main__":
    import json, sys, logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    entries = json.load(sys.stdin)
    tagged = tag_entries(entries)
    print(json.dumps(tagged, ensure_ascii=False, indent=2))
