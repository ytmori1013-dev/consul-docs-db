"""
TF-IDF + キーワードマッチでツイートをカテゴリ分類するモジュール

カテゴリ（8つ固定）:
1. AI・テクノロジー
2. ビジネス・キャリア
3. 政治・社会
4. エンタメ・文化
5. スポーツ
6. 科学・教育
7. 生活・健康
8. その他
"""
import logging
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)

CATEGORIES = [
    "AI・テクノロジー",
    "ビジネス・キャリア",
    "政治・社会",
    "エンタメ・文化",
    "スポーツ",
    "科学・教育",
    "生活・健康",
    "その他",
]

# カテゴリ別キーワードリスト
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "AI・テクノロジー": [
        "AI", "人工知能", "機械学習", "ディープラーニング", "ChatGPT", "GPT",
        "LLM", "生成AI", "OpenAI", "Anthropic", "Claude", "Gemini",
        "プログラミング", "Python", "JavaScript", "React", "アプリ", "ソフトウェア",
        "ハードウェア", "テクノロジー", "スタートアップ", "IT", "デジタル",
        "クラウド", "AWS", "Google", "Microsoft", "Apple", "Meta",
        "スマートフォン", "iPhone", "Android", "半導体", "量子コンピュータ",
        "ロボット", "自動化", "DX", "データサイエンス", "ブロックチェーン",
        "Web3", "メタバース", "VR", "AR", "5G", "IoT",
    ],
    "ビジネス・キャリア": [
        "ビジネス", "起業", "経営", "マーケティング", "営業", "転職", "就職",
        "採用", "キャリア", "副業", "フリーランス", "リモートワーク", "在宅",
        "給料", "年収", "昇給", "投資", "株", "FX", "仮想通貨", "資産",
        "節税", "確定申告", "会社", "社長", "CEO", "スキルアップ",
        "資格", "MBA", "コンサル", "外資", "ベンチャー", "IPO",
        "M&A", "経済", "市場", "GDP", "インフレ", "円安", "ドル",
    ],
    "政治・社会": [
        "政治", "選挙", "政府", "首相", "大統領", "国会", "議員", "与党",
        "野党", "自民党", "立憲", "公明党", "維新", "共産党", "法律",
        "裁判", "事件", "逮捕", "捜査", "警察", "消費税", "社会保障",
        "年金", "福祉", "移民", "難民", "外交", "安全保障", "防衛",
        "戦争", "紛争", "国際", "国連", "条約", "デモ", "抗議",
        "人権", "差別", "格差", "貧困", "少子化", "高齢化",
    ],
    "エンタメ・文化": [
        "アニメ", "マンガ", "ゲーム", "映画", "ドラマ", "音楽", "アーティスト",
        "歌手", "俳優", "女優", "アイドル", "声優", "ライブ", "コンサート",
        "フェス", "舞台", "お笑い", "芸人", "バラエティ", "Youtuber",
        "配信", "ストリーマー", "TikTok", "Instagram", "Twitter",
        "Netflix", "Disney", "漫画", "小説", "本", "読書",
        "カフェ", "グルメ", "旅行", "観光", "ファッション", "トレンド",
        "推し", "オタク", "コスプレ", "同人",
    ],
    "スポーツ": [
        "野球", "サッカー", "バスケ", "テニス", "ゴルフ", "水泳", "陸上",
        "柔道", "相撲", "ボクシング", "格闘技", "MMA", "UFC",
        "オリンピック", "ワールドカップ", "WBC", "Jリーグ", "NPB",
        "プロ野球", "MLB", "NBA", "NFL", "プレミアリーグ",
        "選手", "監督", "コーチ", "優勝", "チャンピオン", "決勝",
        "試合", "得点", "ホームラン", "ゴール", "記録", "引退",
        "移籍", "ドラフト", "スポーツ", "アスリート",
    ],
    "科学・教育": [
        "科学", "研究", "論文", "学術", "大学", "学校", "教育", "学習",
        "物理", "化学", "生物", "数学", "天文", "宇宙", "NASA",
        "JAXA", "iPS", "ゲノム", "DNA", "医学", "薬", "治療",
        "病気", "ワクチン", "コロナ", "感染", "環境", "気候変動",
        "SDGs", "再生可能エネルギー", "原子力", "自然", "生態系",
        "発見", "発明", "ノーベル賞", "実験", "シミュレーション",
    ],
    "生活・健康": [
        "健康", "医療", "病院", "薬", "ダイエット", "筋トレ", "運動",
        "食事", "栄養", "レシピ", "料理", "食べ物", "グルメ", "育児",
        "子育て", "妊娠", "出産", "結婚", "離婚", "家族", "ペット",
        "猫", "犬", "住宅", "引越し", "インテリア", "掃除", "整理整頓",
        "節約", "家計", "生活費", "便利", "ライフハック", "ルーティン",
        "睡眠", "メンタル", "ストレス", "うつ", "マインドフルネス",
    ],
}


def _keyword_score(text: str, keywords: list[str]) -> int:
    """テキストにキーワードが何個含まれるかカウントする"""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def classify_tweet(text: str) -> str:
    """
    1件のツイートテキストをキーワードスコアリングでカテゴリ分類する

    Returns:
        最高スコアのカテゴリ名（同点の場合は「その他」）
    """
    if not text or not text.strip():
        return "その他"

    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = _keyword_score(text, keywords)

    # 最高スコアを取得
    max_score = max(scores.values())

    # スコアが0または同点が複数ある場合は「その他」
    if max_score == 0:
        return "その他"

    top_categories = [cat for cat, score in scores.items() if score == max_score]
    if len(top_categories) == 1:
        return top_categories[0]

    return "その他"


def classify_tweets_batch(tweets: list[dict]) -> list[dict]:
    """
    TF-IDFを用いてツイートリストをバッチでカテゴリ分類する

    キーワードスコアリングを主軸にしつつ、TF-IDFで重みを補強する。
    フォールバックとして単純キーワードマッチを使用。
    """
    if not tweets:
        return tweets

    texts = [tweet.get("text", "") for tweet in tweets]

    # TF-IDFベクトルを計算
    tfidf_scores: Optional[np.ndarray] = None
    try:
        if len(texts) >= 2:
            vectorizer = TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(2, 3),
                max_features=5000,
                min_df=1,
            )
            tfidf_matrix = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()

            # カテゴリごとのキーワードをTF-IDFフィーチャーとマッチング
            category_tfidf_scores = np.zeros((len(tweets), len(CATEGORIES) - 1))
            for cat_idx, (cat_name, keywords) in enumerate(
                [(c, CATEGORY_KEYWORDS[c]) for c in CATEGORIES[:-1]]
            ):
                for kw in keywords:
                    kw_indices = [
                        i for i, fn in enumerate(feature_names)
                        if kw.lower() in fn.lower()
                    ]
                    if kw_indices:
                        kw_scores = tfidf_matrix[:, kw_indices].toarray().sum(axis=1)
                        category_tfidf_scores[:, cat_idx] += kw_scores

            tfidf_scores = category_tfidf_scores
    except Exception as e:
        logger.warning(f"TF-IDF計算エラー（キーワードマッチにフォールバック）: {e}")

    # 各ツイートに分類結果を付与
    for i, tweet in enumerate(tweets):
        try:
            text = tweet.get("text", "")

            # キーワードスコア
            kw_scores = {
                cat: _keyword_score(text, CATEGORY_KEYWORDS[cat])
                for cat in CATEGORIES[:-1]
            }

            # TF-IDFスコアと合算
            if tfidf_scores is not None:
                combined = {}
                for j, cat in enumerate(CATEGORIES[:-1]):
                    combined[cat] = kw_scores[cat] + tfidf_scores[i, j] * 0.3
            else:
                combined = dict(kw_scores)

            max_score = max(combined.values()) if combined else 0

            if max_score <= 0:
                tweet["category"] = "その他"
            else:
                top = [c for c, s in combined.items() if s == max_score]
                tweet["category"] = top[0] if len(top) == 1 else "その他"

        except Exception as e:
            logger.warning(f"ツイート {tweet.get('tweet_id', '?')} の分類エラー: {e}")
            tweet["category"] = "その他"

    category_counts = {}
    for tweet in tweets:
        cat = tweet.get("category", "その他")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    logger.info(f"カテゴリ分類完了: {category_counts}")

    return tweets


if __name__ == "__main__":
    import json

    test_tweets = [
        {"tweet_id": "1", "text": "ChatGPTがすごい！AIの進化が止まらない。機械学習の新モデル登場", "author": "a", "author_id": "a"},
        {"tweet_id": "2", "text": "プロ野球の試合で大逆転！ホームランが決勝打に", "author": "b", "author_id": "b"},
        {"tweet_id": "3", "text": "選挙結果が出た。政府の政策に批判続出", "author": "c", "author_id": "c"},
        {"tweet_id": "4", "text": "新アニメ放送開始！推しキャラがかわいすぎる", "author": "d", "author_id": "d"},
    ]

    result = classify_tweets_batch(test_tweets)
    for t in result:
        print(f"{t['tweet_id']}: {t['category']} - {t['text'][:30]}")
