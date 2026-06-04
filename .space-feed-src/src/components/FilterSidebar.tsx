"use client";

export type Filters = {
  source: string;
  lang: string;
  org: string;
  text: string;
};

type Props = {
  filters: Filters;
  orgs: string[];
  onChange: (f: Filters) => void;
};

const SOURCES = [
  { value: "", label: "すべて" },
  { value: "ndl", label: "省庁・NDL" },
  { value: "edinet", label: "企業IR" },
  { value: "nasa", label: "NASA" },
  { value: "rss", label: "ニュース" },
];

const LANGS = [
  { value: "", label: "すべて" },
  { value: "ja", label: "日本語" },
  { value: "en", label: "英語" },
];

export default function FilterSidebar({ filters, orgs, onChange }: Props) {
  const set = (key: keyof Filters, value: string) =>
    onChange({ ...filters, [key]: value });

  return (
    <aside className="w-56 flex-shrink-0 space-y-5">
      <div>
        <input
          type="search"
          placeholder="キーワード検索..."
          value={filters.text}
          onChange={(e) => set("text", e.target.value)}
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
      </div>

      <FilterGroup label="ソース">
        {SOURCES.map((s) => (
          <Chip
            key={s.value}
            label={s.label}
            active={filters.source === s.value}
            onClick={() => set("source", s.value)}
          />
        ))}
      </FilterGroup>

      <FilterGroup label="言語">
        {LANGS.map((l) => (
          <Chip
            key={l.value}
            label={l.label}
            active={filters.lang === l.value}
            onClick={() => set("lang", l.value)}
          />
        ))}
      </FilterGroup>

      {orgs.length > 0 && (
        <FilterGroup label="組織・企業">
          <Chip
            label="すべて"
            active={filters.org === ""}
            onClick={() => set("org", "")}
          />
          {orgs.slice(0, 15).map((o) => (
            <Chip
              key={o}
              label={o}
              active={filters.org === o}
              onClick={() => set("org", o)}
            />
          ))}
        </FilterGroup>
      )}
    </aside>
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
        {label}
      </p>
      <div className="flex flex-wrap gap-1">{children}</div>
    </div>
  );
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-xs px-2 py-1 rounded-full border transition-colors ${
        active
          ? "bg-blue-600 text-white border-blue-600"
          : "bg-white text-gray-600 border-gray-300 hover:border-blue-400"
      }`}
    >
      {label}
    </button>
  );
}
