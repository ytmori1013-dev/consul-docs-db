"""
サムネイル生成スクリプト

PPT/PDFの先頭3ページをPNGに変換して docs/thumbnails/ に保存し、
Gistの該当エントリの thumbnail_urls を更新する。

依存: LibreOffice（apt）, poppler-utils（apt）, pdf2image（pip）

1回の実行で処理する最大件数は MAX_BATCH=20（キュー方式）。
500ファイル超過時は最古から削除するローテーションを行う。
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")
DATA_FILENAME = "slides.json"
THUMBNAILS_DIR = Path(__file__).parent.parent / "docs" / "thumbnails"
MAX_BATCH = 20        # 1実行あたり最大処理件数
MAX_FILES = 500       # docs/thumbnails/ の最大ファイル数（超過時にローテーション）
THUMB_WIDTH = 800     # PNG 幅（px）
MAX_PAGES = 3         # 先頭何ページまで生成するか

GIST_HEADERS = {
    "Authorization": f"token {GIST_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}
DL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


# ===== Gist 操作 =====

def _load_gist() -> dict:
    try:
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=GIST_HEADERS, timeout=15)
        r.raise_for_status()
        raw_url = r.json()["files"][DATA_FILENAME]["raw_url"]
        data_r = requests.get(raw_url, timeout=30)
        data_r.raise_for_status()
        return data_r.json()
    except Exception as e:
        logger.error(f"Gist 読み込みエラー: {e}")
        return {}


def _save_gist(data: dict) -> bool:
    try:
        payload = {"files": {DATA_FILENAME: {"content": json.dumps(data, ensure_ascii=False, indent=2)}}}
        r = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=GIST_HEADERS, json=payload, timeout=60)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Gist 保存エラー: {e}")
        return False


# ===== ファイルダウンロード =====

def _download(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, headers=DL_HEADERS, timeout=60, stream=True) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
        return True
    except Exception as e:
        logger.warning(f"ダウンロード失敗 {url}: {e}")
        return False


# ===== 変換処理 =====

def _pptx_to_pdf(src: Path, tmpdir: Path) -> Path | None:
    """LibreOffice で PPT/PPTX を PDF に変換する"""
    try:
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmpdir), str(src)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning(f"LibreOffice 変換失敗: {result.stderr[:200]}")
            return None
        pdf_path = tmpdir / (src.stem + ".pdf")
        return pdf_path if pdf_path.exists() else None
    except Exception as e:
        logger.warning(f"LibreOffice エラー: {e}")
        return None


def _pdf_to_pngs(pdf_path: Path, entry_id: str) -> list:
    """pdf2image で先頭3ページを PNG に変換して docs/thumbnails/ に保存する"""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        logger.error("pdf2image が未インストール。サムネイル生成をスキップ。")
        return []

    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        images = convert_from_path(
            str(pdf_path),
            first_page=1,
            last_page=MAX_PAGES,
            size=(THUMB_WIDTH, None),  # 幅固定・高さ自動
        )
    except Exception as e:
        logger.warning(f"pdf2image 変換失敗: {e}")
        return []

    paths = []
    for i, img in enumerate(images, start=1):
        out_path = THUMBNAILS_DIR / f"{entry_id}_{i}.png"
        try:
            img.save(str(out_path), "PNG", optimize=True)
            paths.append(f"thumbnails/{entry_id}_{i}.png")
            logger.info(f"  サムネイル保存: {out_path.name}")
        except Exception as e:
            logger.warning(f"PNG保存失敗: {e}")
    return paths


# ===== ローテーション =====

def _rotate_thumbnails():
    """docs/thumbnails/ が MAX_FILES 超過時に最古ファイルを削除する"""
    if not THUMBNAILS_DIR.exists():
        return
    files = sorted(THUMBNAILS_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
    if len(files) > MAX_FILES:
        to_delete = files[:len(files) - MAX_FILES]
        for f in to_delete:
            f.unlink(missing_ok=True)
        logger.info(f"ローテーション: {len(to_delete)} ファイル削除")


# ===== メイン =====

def main():
    if not GIST_ID or not GIST_TOKEN:
        logger.error("GIST_ID または GIST_TOKEN が未設定。終了します。")
        return

    data = _load_gist()
    if not data:
        return

    slides = data.get("slides", [])
    # サムネイル未生成かつ実ファイルURLを持つエントリを抽出
    targets = [
        s for s in slides
        if not s.get("thumbnail_urls")
        and s.get("file_type") in ("pdf", "ppt", "pptx")
        and s.get("url", "")
    ][:MAX_BATCH]

    if not targets:
        logger.info("サムネイル生成対象なし。終了します。")
        return

    logger.info(f"サムネイル生成対象: {len(targets)} 件")
    updated_ids = set()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        for entry in targets:
            entry_id = entry["id"]
            url = entry["url"]
            file_type = entry["file_type"]
            ext = f".{file_type}"
            src_path = tmpdir_path / f"{entry_id}{ext}"

            logger.info(f"処理中: {entry.get('title', '')[:50]}")

            if not _download(url, src_path):
                continue

            pdf_path = src_path
            if file_type in ("ppt", "pptx"):
                pdf_path = _pptx_to_pdf(src_path, tmpdir_path)
                if pdf_path is None:
                    continue

            thumb_paths = _pdf_to_pngs(pdf_path, entry_id)
            if not thumb_paths:
                continue

            # スライドデータに thumbnail_urls を書き込む
            for slide in slides:
                if slide["id"] == entry_id:
                    slide["thumbnail_urls"] = thumb_paths
                    updated_ids.add(entry_id)
                    break

    _rotate_thumbnails()

    if updated_ids:
        logger.info(f"{len(updated_ids)} 件の thumbnail_urls を Gist に保存中...")
        _save_gist(data)
    else:
        logger.info("更新なし。Gist 保存をスキップ。")


if __name__ == "__main__":
    main()
