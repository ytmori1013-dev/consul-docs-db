"use client";

import { Document } from "@/lib/supabase";

const SOURCE_LABEL: Record<string, string> = {
  ndl: "国立国会図書館",
  edinet: "EDINET",
  nasa: "NASA",
  rss: "ニュース",
};

const SOURCE_COLOR: Record<string, string> = {
  ndl: "bg-blue-100 text-blue-800",
  edinet: "bg-green-100 text-green-800",
  nasa: "bg-purple-100 text-purple-800",
  rss: "bg-orange-100 text-orange-800",
};

const FILE_ICON: Record<string, string> = {
  pdf: "📄",
  pptx: "📊",
  html: "🔗",
};

type Props = {
  doc: Document;
  bookmarked: boolean;
  onBookmark: (docId: string) => void;
};

export default function DocumentCard({ doc, bookmarked, onBookmark }: Props) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${SOURCE_COLOR[doc.source] ?? "bg-gray-100 text-gray-700"}`}
            >
              {SOURCE_LABEL[doc.source] ?? doc.source}
            </span>
            {doc.file_type && (
              <span className="text-sm">{FILE_ICON[doc.file_type] ?? ""}</span>
            )}
            {doc.lang === "en" && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">EN</span>
            )}
          </div>
          <a
            href={doc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-medium text-gray-900 hover:text-blue-600 line-clamp-2"
          >
            {doc.title}
          </a>
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            {doc.org && (
              <span className="text-xs text-gray-500">{doc.org}</span>
            )}
            {doc.published_at && (
              <span className="text-xs text-gray-400">
                {doc.published_at.slice(0, 10)}
              </span>
            )}
          </div>
          {doc.tags && doc.tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {doc.tags.slice(0, 4).map((tag) => (
                <span
                  key={tag}
                  className="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={() => onBookmark(doc.id)}
          className={`flex-shrink-0 text-xl transition-transform hover:scale-110 ${bookmarked ? "opacity-100" : "opacity-30 hover:opacity-60"}`}
          title={bookmarked ? "ブックマーク解除" : "ブックマーク"}
        >
          🔖
        </button>
      </div>
    </div>
  );
}
