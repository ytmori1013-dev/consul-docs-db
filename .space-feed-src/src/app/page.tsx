"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, Document, Bookmark } from "@/lib/supabase";
import DocumentCard from "@/components/DocumentCard";
import FilterSidebar, { Filters } from "@/components/FilterSidebar";
import NoteModal from "@/components/NoteModal";

const PAGE_SIZE = 30;

export default function Home() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [user, setUser] = useState<{ id: string; email?: string } | null>(null);
  const [filters, setFilters] = useState<Filters>({ source: "", lang: "", org: "", text: "" });
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [noteModal, setNoteModal] = useState<{ doc: Document; note: string } | null>(null);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setUser(data.user));
    const { data: sub } = supabase.auth.onAuthStateChange((_, session) => {
      setUser(session?.user ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!user) { setBookmarks([]); return; }
    supabase.from("bookmarks").select("*").eq("user_id", user.id)
      .then(({ data }) => setBookmarks(data ?? []));
  }, [user]);

  useEffect(() => {
    setLoading(true);
    let q = supabase
      .from("documents")
      .select("*", { count: "exact" })
      .order("published_at", { ascending: false, nullsFirst: false })
      .order("created_at", { ascending: false })
      .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1);

    if (filters.source) q = q.eq("source", filters.source);
    if (filters.lang) q = q.eq("lang", filters.lang);
    if (filters.org) q = q.eq("org", filters.org);
    if (filters.text) q = q.ilike("title", `%${filters.text}%`);

    q.then(({ data, count }) => {
      setDocuments(data ?? []);
      setTotal(count ?? 0);
      setLoading(false);
    });
  }, [filters, page]);

  useEffect(() => { setPage(0); }, [filters]);

  const bookmarkIds = useMemo(() => new Set(bookmarks.map((b) => b.document_id)), [bookmarks]);

  const orgs = useMemo(() => {
    const set = new Set<string>();
    documents.forEach((d) => { if (d.org) set.add(d.org); });
    return [...set].sort();
  }, [documents]);

  const handleBookmark = useCallback(async (docId: string) => {
    if (!user) { alert("ブックマークにはログインが必要です"); return; }
    const existing = bookmarks.find((b) => b.document_id === docId);
    if (existing) {
      await supabase.from("bookmarks").delete().eq("id", existing.id);
      setBookmarks((prev) => prev.filter((b) => b.id !== existing.id));
    } else {
      const doc = documents.find((d) => d.id === docId)!;
      setNoteModal({ doc, note: "" });
    }
  }, [user, bookmarks, documents]);

  const saveNote = useCallback(async (note: string) => {
    if (!user || !noteModal) return;
    const { data } = await supabase
      .from("bookmarks")
      .upsert({ user_id: user.id, document_id: noteModal.doc.id, note })
      .select().single();
    if (data) setBookmarks((prev) => [...prev.filter((b) => b.document_id !== noteModal.doc.id), data]);
    setNoteModal(null);
  }, [user, noteModal]);

  const handleLogin = async () => {
    const email = prompt("メールアドレス");
    if (!email) return;
    const password = prompt("パスワード");
    if (!password) return;
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      const { error: e2 } = await supabase.auth.signUp({ email, password });
      if (e2) alert(e2.message);
      else alert("確認メールを送信しました");
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-gray-900">🛰 Space Feed</h1>
            <p className="text-xs text-gray-500">宇宙・防衛インテリジェンス</p>
          </div>
          <div className="flex items-center gap-3">
            {user ? (
              <>
                <a href="/bookmarks" className="text-sm text-blue-600 hover:underline">🔖 ブックマーク</a>
                <span className="text-xs text-gray-500">{user.email}</span>
                <button onClick={() => supabase.auth.signOut()} className="text-xs text-gray-400 hover:text-gray-600">ログアウト</button>
              </>
            ) : (
              <button onClick={handleLogin} className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700">ログイン</button>
            )}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-6 flex gap-6">
        <FilterSidebar filters={filters} orgs={orgs} onChange={setFilters} />
        <main className="flex-1 min-w-0">
          <p className="text-sm text-gray-500 mb-4">{loading ? "読み込み中..." : `${total.toLocaleString()} 件`}</p>
          {loading ? (
            <div className="space-y-3">
              {[...Array(6)].map((_, i) => <div key={i} className="h-24 bg-white border border-gray-200 rounded-lg animate-pulse" />)}
            </div>
          ) : (
            <>
              <div className="space-y-3">
                {documents.map((doc) => (
                  <DocumentCard key={doc.id} doc={doc} bookmarked={bookmarkIds.has(doc.id)} onBookmark={handleBookmark} />
                ))}
              </div>
              {total > PAGE_SIZE && (
                <div className="flex justify-center gap-2 mt-6">
                  <button disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="px-4 py-2 text-sm border rounded-lg disabled:opacity-40 hover:bg-gray-50">← 前へ</button>
                  <span className="px-4 py-2 text-sm text-gray-500">{page + 1} / {Math.ceil(total / PAGE_SIZE)}</span>
                  <button disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => setPage((p) => p + 1)} className="px-4 py-2 text-sm border rounded-lg disabled:opacity-40 hover:bg-gray-50">次へ →</button>
                </div>
              )}
            </>
          )}
        </main>
      </div>

      {noteModal && (
        <NoteModal doc={noteModal.doc} initialNote={noteModal.note} onSave={saveNote} onClose={() => setNoteModal(null)} />
      )}
    </div>
  );
}
