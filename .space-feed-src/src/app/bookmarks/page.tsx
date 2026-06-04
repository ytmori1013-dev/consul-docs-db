"use client";

import { useEffect, useState } from "react";
import { supabase, Bookmark } from "@/lib/supabase";
import NoteModal from "@/components/NoteModal";

export default function BookmarksPage() {
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<{ id: string; email?: string } | null>(null);
  const [editing, setEditing] = useState<Bookmark | null>(null);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setUser(data.user));
  }, []);

  useEffect(() => {
    if (!user) { setLoading(false); return; }
    supabase
      .from("bookmarks")
      .select("*, document:documents(*)")
      .eq("user_id", user.id)
      .order("created_at", { ascending: false })
      .then(({ data }) => {
        setBookmarks(data ?? []);
        setLoading(false);
      });
  }, [user]);

  const saveNote = async (note: string) => {
    if (!editing) return;
    await supabase.from("bookmarks").update({ note }).eq("id", editing.id);
    setBookmarks((prev) => prev.map((b) => b.id === editing.id ? { ...b, note } : b));
    setEditing(null);
  };

  const removeBookmark = async (id: string) => {
    await supabase.from("bookmarks").delete().eq("id", id);
    setBookmarks((prev) => prev.filter((b) => b.id !== id));
  };

  if (!user) return (
    <div className="min-h-screen flex items-center justify-center text-gray-500">
      <p>ブックマークの表示にはログインが必要です</p>
    </div>
  );

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-3">
          <a href="/" className="text-gray-400 hover:text-gray-600">← フィード</a>
          <h1 className="font-bold text-gray-900">🔖 ブックマーク</h1>
        </div>
      </header>

      <div className="max-w-3xl mx-auto px-4 py-6">
        {loading ? (
          <div className="space-y-3">
            {[...Array(4)].map((_, i) => <div key={i} className="h-20 bg-white border border-gray-200 rounded-lg animate-pulse" />)}
          </div>
        ) : bookmarks.length === 0 ? (
          <p className="text-center text-gray-400 py-16">ブックマークはまだありません</p>
        ) : (
          <div className="space-y-3">
            {bookmarks.map((bm) => (
              <div key={bm.id} className="bg-white border border-gray-200 rounded-lg p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <a
                      href={bm.document?.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium text-gray-900 hover:text-blue-600 line-clamp-2"
                    >
                      {bm.document?.title ?? "(タイトルなし)"}
                    </a>
                    <p className="text-xs text-gray-400 mt-0.5">{bm.document?.org}</p>
                    {bm.note && (
                      <p className="mt-2 text-sm text-gray-600 bg-yellow-50 rounded p-2 whitespace-pre-wrap">{bm.note}</p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setEditing(bm)}
                      className="text-xs text-gray-400 hover:text-blue-600"
                    >
                      ✏️
                    </button>
                    <button
                      onClick={() => removeBookmark(bm.id)}
                      className="text-xs text-gray-400 hover:text-red-500"
                    >
                      🗑
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {editing && editing.document && (
        <NoteModal
          doc={editing.document}
          initialNote={editing.note}
          onSave={saveNote}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
