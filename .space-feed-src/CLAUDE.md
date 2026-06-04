@AGENTS.md

# Space Feed — Claude 作業ガイドライン

## 最重要: 手動作業ゼロ原則
ユーザーが手動でやる作業は極力なくすこと。GitHub リポジトリ作成・Supabase セットアップ・デプロイなど、
自動化できるものはすべてコードや CLI で対応する。どうしても必要な手動ステップは明確に最小限で伝える。

## プロジェクト概要
宇宙・防衛インテリジェンス読書サービス。省庁報告書・企業IR・海外資料・ニュースを集約。

## 技術スタック
- Frontend: Next.js (App Router) + Tailwind CSS
- Backend: Supabase (Auth + PostgreSQL)
- Deploy: Vercel
- Crawler: GitHub Actions (Python)

## DB スキーマ
`supabase/migrations/` 以下に SQL マイグレーションを管理。
