"""
コンサルティングファーム名の検出・正規化を行う共有モジュール

crawler.py / tagger.py / pptx_to_image.py から共通利用する。
表記ゆれを正規化マップで吸収し、見つからない場合は "不明" を返す。
"""
import re

# 表記ゆれ → 正規化名のマップ（長い表記を優先するため、登録順に注意）
FIRM_NORMALIZE = {
    # 外資戦略
    "マッキンゼー・アンド・カンパニー": "McKinsey",
    "マッキンゼー": "McKinsey",
    "マッキンゼイ": "McKinsey",
    "McKinsey & Company": "McKinsey",
    "McKinsey": "McKinsey",
    "ボストン・コンサルティング・グループ": "BCG",
    "ボストンコンサルティンググループ": "BCG",
    "ボストンコンサルティング": "BCG",
    "Boston Consulting": "BCG",
    "BCG": "BCG",
    "ベイン・アンド・カンパニー": "Bain",
    "ベイン": "Bain",
    "Bain": "Bain",
    "A.T. カーニー": "A.T. Kearney",
    "ATカーニー": "A.T. Kearney",
    "A.T. Kearney": "A.T. Kearney",
    "Kearney": "A.T. Kearney",
    "ローランド・ベルガー": "Roland Berger",
    "ローランドベルガー": "Roland Berger",
    "Roland Berger": "Roland Berger",
    "アーサー・D・リトル": "A.D. Little",
    "A.D.リトル": "A.D. Little",
    "ADリトル": "A.D. Little",
    "Arthur D. Little": "A.D. Little",
    "コーポレイトディレクション": "CDI",
    "CDI": "CDI",
    # 総合系・監査法人系
    "デロイトトーマツ": "Deloitte",
    "デロイト トーマツ": "Deloitte",
    "有限責任監査法人トーマツ": "Deloitte",
    "デロイト": "Deloitte",
    "Deloitte": "Deloitte",
    "DTT": "Deloitte",
    "PwCコンサルティング": "PwC",
    "PwCあらた": "PwC",
    "プライスウォーターハウスクーパース": "PwC",
    "プライスウォーター": "PwC",
    "PricewaterhouseCoopers": "PwC",
    "PwC": "PwC",
    "KPMGコンサルティング": "KPMG",
    "あずさ監査法人": "KPMG",
    "KPMG": "KPMG",
    "アーンスト・アンド・ヤング": "EY",
    "EYストラテジー": "EY",
    "EYストラフィー": "EY",
    "新日本監査法人": "EY",
    "アーンスト": "EY",
    "Ernst & Young": "EY",
    "EY": "EY",
    "アクセンチュア": "Accenture",
    "Accenture": "Accenture",
    "アビームコンサルティング": "ABeam",
    "アビーム": "ABeam",
    "ABeam": "ABeam",
    "日本IBM": "IBM",
    "IBM": "IBM",
    # 国内シンクタンク
    "野村総合研究所": "NRI",
    "野村総研": "NRI",
    "Nomura Research": "NRI",
    "NRI": "NRI",
    "三菱UFJリサーチ&コンサルティング": "MURC",
    "三菱UFJリサーチ": "MURC",
    "三菱UFJ": "MURC",
    "MURC": "MURC",
    "三菱総合研究所": "MRI",
    "三菱総研": "MRI",
    "MRI": "MRI",
    "みずほリサーチ&テクノロジーズ": "Mizuho",
    "みずほリサーチ": "Mizuho",
    "みずほ情報総研": "Mizuho",
    "みずほ総合研究所": "Mizuho",
    "日本総合研究所": "JRI",
    "日本総研": "JRI",
    "JRI": "JRI",
    "大和総研": "大和総研",
    "大和総合研究所": "大和総研",
    "NTTデータ経営研究所": "NTTデータ経営研",
    "NTTデータ経営研": "NTTデータ経営研",
    "富士通総研": "富士通総研",
    "FRI": "富士通総研",
    "電通総研": "電通総研",
    "電通国際情報サービス": "電通総研",
    "電通": "電通",
    "博報堂": "博報堂",
    "リクルート": "Recruit",
    "矢野経済研究所": "矢野経済研究所",
    "日立コンサルティング": "日立コンサルティング",
    "パシフィックコンサルタンツ": "パシフィックコンサルタンツ",
    "価値総合研究所": "価値総合研究所",
    "産業能率大学": "産業能率大学",
    "シード・プランニング": "シード・プランニング",
    "ベリングポイント": "BearingPoint",
    "BearingPoint": "BearingPoint",
    "Strategy&": "Strategy&",
    "ストラテジー&": "Strategy&",
    # 防衛・宇宙系受託企業
    "三菱電機株式会社": "三菱電機",
    "三菱電機": "三菱電機",
    "三菱重工業株式会社": "三菱重工",
    "三菱重工業": "三菱重工",
    "三菱重工": "三菱重工",
    "川崎重工業株式会社": "川崎重工",
    "川崎重工業": "川崎重工",
    "川崎重工": "川崎重工",
    "石川島播磨重工業": "IHI",
    "IHIエアロスペース": "IHI",
    "IHI": "IHI",
    "宇宙航空研究開発機構": "JAXA",
    "JAXA": "JAXA",
    "日本電気株式会社": "NEC",
    "日本電気": "NEC",
    "NEC": "NEC",
    "富士通株式会社": "富士通",
    "富士通": "富士通",
    "日立製作所": "日立",
    "株式会社日立製作所": "日立",
    "東芝インフラシステムズ": "東芝",
    "東芝": "東芝",
    "防衛装備庁": "防衛装備庁",
    "技術研究本部": "防衛省技研",
}

# マッチング時は長いキーから順に走査して部分一致の取りこぼし・誤検出を防ぐ
_SORTED_KEYS = sorted(FIRM_NORMALIZE.keys(), key=len, reverse=True)


def detect_firm(text: str) -> str:
    """
    テキストからコンサルファーム名を検出して正規化名を返す。

    1. 「委託先：○○」パターンを最優先で抽出
    2. 本文中の表記ゆれを正規化マップで吸収（長い表記優先）
    3. 見つからなければ "不明"
    複数検出時は最初に見つかったものを採用する。
    """
    if not text:
        return "不明"

    # 1. 委託先パターン優先（委託先・受託者・実施機関 等）
    m = re.search(
        r"(?:委託先|受託者|受託事業者|実施機関|請負者)[：:\s]*([^\n。、]{2,40})",
        text,
    )
    if m:
        candidate = m.group(1)
        for key in _SORTED_KEYS:
            if key in candidate:
                return FIRM_NORMALIZE[key]

    # 2. 本文全体から検出（長い表記優先）
    for key in _SORTED_KEYS:
        if key in text:
            return FIRM_NORMALIZE[key]

    return "不明"


def all_firm_names() -> list:
    """正規化後のファーム名の一覧（重複なし）を返す"""
    return sorted(set(FIRM_NORMALIZE.values()))
