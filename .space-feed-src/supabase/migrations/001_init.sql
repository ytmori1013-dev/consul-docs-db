-- documents: 文書マスタ（クローラーが upsert）
create table if not exists documents (
  id           uuid primary key default gen_random_uuid(),
  title        text not null,
  url          text unique not null,
  source       text check (source in ('ndl', 'edinet', 'nasa', 'rss')),
  org          text,
  file_type    text check (file_type in ('pdf', 'pptx', 'html')),
  lang         text default 'ja',
  tags         text[],
  published_at date,
  created_at   timestamptz default now()
);

create index if not exists documents_source_idx       on documents(source);
create index if not exists documents_org_idx          on documents(org);
create index if not exists documents_lang_idx         on documents(lang);
create index if not exists documents_published_at_idx on documents(published_at desc);

-- bookmarks: ユーザーのブックマーク＋メモ
create table if not exists bookmarks (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references auth.users not null,
  document_id uuid references documents not null,
  note        text default '',
  created_at  timestamptz default now(),
  unique(user_id, document_id)
);

-- RLS: bookmarks は自分のデータのみ読み書き可
alter table bookmarks enable row level security;

create policy "own bookmarks" on bookmarks
  for all using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- documents は全員読み取り可（クローラーは service_role キーで書き込む）
alter table documents enable row level security;

create policy "public read documents" on documents
  for select using (true);
