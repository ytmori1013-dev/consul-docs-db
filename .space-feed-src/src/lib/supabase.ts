import { createClient, SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (!_client) {
    _client = createClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? ""
    );
  }
  return _client;
}

// 後方互換用の Proxy — 既存コードが supabase.from(...) で動く
export const supabase = new Proxy({} as SupabaseClient, {
  get(_, prop) {
    return (getSupabase() as unknown as Record<string | symbol, unknown>)[prop];
  },
});

export type Document = {
  id: string;
  title: string;
  url: string;
  source: "ndl" | "edinet" | "nasa" | "rss";
  org: string | null;
  file_type: "pdf" | "pptx" | "html" | null;
  lang: string;
  tags: string[] | null;
  published_at: string | null;
  created_at: string;
};

export type Bookmark = {
  id: string;
  user_id: string;
  document_id: string;
  note: string;
  created_at: string;
  document?: Document;
};
