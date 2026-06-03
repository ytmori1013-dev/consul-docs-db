# ConsulSlides

ConsulSlides は、経済産業省（METI）・防衛省（MOD）・内閣府（CAO）などの官公庁が公開する資料スライドを自動収集し、構造タグ・デザインタグを付与して GitHub Gist に蓄積するシステムです。毎週月曜日 9:00 UTC に GitHub Actions で自動クロールが実行され、新着スライドのみを差分同期します。

## タグ精度検証

1. `scripts/eval_truth.json` に正解データを記入（20件程度）

   ```json
   [{"id":"md5hash","structure":["Issue Tree","So What"],"design":["図解中心"]}, ...]
   ```

   `id` は Gist の `slides.json` 各エントリの `"id"` フィールド（md5 ハッシュ）

2. 実行:

   ```
   GIST_ID=xxx GIST_TOKEN=yyy python3 scripts/eval_tags.py
   ```

## GitHub Actions 失敗メール通知の有効化

GitHub > Settings > Notifications > Actions で「Email when workflow fails」を有効に。
これで全ソースからの取得が0件になった場合に自動通知されます。
