"use client";

import { useEffect, useRef, useState } from "react";
import { Document } from "@/lib/supabase";

type Props = {
  doc: Document;
  initialNote: string;
  onSave: (note: string) => void;
  onClose: () => void;
};

export default function NoteModal({ doc, initialNote, onSave, onClose }: Props) {
  const [note, setNote] = useState(initialNote);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-semibold text-gray-900 mb-1 line-clamp-2">{doc.title}</h2>
        <p className="text-xs text-gray-400 mb-4">{doc.org}</p>
        <textarea
          ref={textareaRef}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="メモを入力..."
          rows={6}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 resize-none"
        />
        <div className="flex justify-end gap-2 mt-4">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
          >
            キャンセル
          </button>
          <button
            onClick={() => onSave(note)}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
