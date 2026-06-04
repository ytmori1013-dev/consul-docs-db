"""
タグ精度検証スクリプト（手動実行用）

使い方:
  1. scripts/eval_truth.json に正解データを記入（20件程度）
     形式: [{"id":"xxx","structure":["Issue Tree"],"design":["図解中心"]}, ...]
  2. python3 scripts/eval_tags.py

Gistの自動タグ結果と eval_truth.json を突合し、
構造タグ・デザインタグの precision / recall / F1 を算出して表示します。
"""
import json, os, sys, math

TRUTH_FILE = os.path.join(os.path.dirname(__file__), "eval_truth.json")
GIST_ID = os.environ.get("GIST_ID", "")

def fetch_gist_slides():
    """GistからJSONを取得してスライドリストを返す"""
    import requests
    if not GIST_ID:
        print("GIST_ID が未設定です。GIST_ID=xxx python3 scripts/eval_tags.py で実行してください。")
        sys.exit(1)
    headers = {}
    token = os.environ.get("GIST_TOKEN", "")
    if token:
        headers["Authorization"] = f"token {token}"
    resp = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers, timeout=15)
    resp.raise_for_status()
    gist = resp.json()
    files = gist.get("files", {})
    if "slides.json" not in files:
        print("slides.json が Gist に存在しません。")
        sys.exit(1)
    raw = requests.get(files["slides.json"]["raw_url"], timeout=30)
    data = raw.json()
    return {s["id"]: s for s in data.get("slides", [])}

def compute_metrics(pred_tags, true_tags):
    """precision/recall/F1を計算"""
    pred = set(pred_tags)
    true = set(true_tags)
    if not pred and not true:
        return 1.0, 1.0, 1.0
    tp = len(pred & true)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(true) if true else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1

def main():
    with open(TRUTH_FILE, encoding="utf-8") as f:
        truth_data = json.load(f)

    if not truth_data:
        print("正解データ未入力 (scripts/eval_truth.json が空配列)")
        print("フォーマット: [{\"id\":\"xxx\",\"structure\":[\"Issue Tree\"],\"design\":[\"図解中心\"]}, ...]")
        sys.exit(0)

    slides = fetch_gist_slides()

    struct_metrics = []
    design_metrics = []
    missing = []

    for item in truth_data:
        sid = item.get("id", "")
        if sid not in slides:
            missing.append(sid)
            continue
        slide = slides[sid]

        # 構造タグ: tags.structure は [{name, level}] or [str]
        struct_auto = []
        for s in (slide.get("tags", {}).get("structure", [])):
            if isinstance(s, dict):
                struct_auto.append(s["name"])
            else:
                struct_auto.append(s)

        # デザインタグ: tags.design は [str]
        design_auto = slide.get("tags", {}).get("design", [])

        true_struct = item.get("structure", [])
        true_design = item.get("design", [])

        p, r, f = compute_metrics(struct_auto, true_struct)
        struct_metrics.append((p, r, f))

        p, r, f = compute_metrics(design_auto, true_design)
        design_metrics.append((p, r, f))

    def avg(lst): return sum(lst) / len(lst) if lst else 0.0

    n = len(struct_metrics)
    print(f"=== タグ精度評価 (N={n}件) ===")
    print(f"構造タグ  Precision: {avg([m[0] for m in struct_metrics]):.3f}  "
          f"Recall: {avg([m[1] for m in struct_metrics]):.3f}  "
          f"F1: {avg([m[2] for m in struct_metrics]):.3f}")
    print(f"デザインタグ Precision: {avg([m[0] for m in design_metrics]):.3f}  "
          f"Recall: {avg([m[1] for m in design_metrics]):.3f}  "
          f"F1: {avg([m[2] for m in design_metrics]):.3f}")
    if missing:
        print(f"警告: 以下のIDがGistに見つかりませんでした: {missing}")

if __name__ == "__main__":
    main()
