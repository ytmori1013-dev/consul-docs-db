"""NDL SRU API クローラー — 防衛省・内閣府宇宙・JAXA 報告書"""

import os
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

NDL_SRU = "https://ndlsearch.ndl.go.jp/api/sru"

SRW = "http://www.loc.gov/zing/srw/"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
DCNDL = "http://ndl.go.jp/dcndl/terms/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

QUERY_SETS = [
    ('creator="防衛省" AND title="宇宙"',            200, "防衛省"),
    ('creator="防衛省" AND title="委託調査"',          200, "防衛省"),
    ('creator="防衛省" AND title="調査研究"',          100, "防衛省"),
    ('creator="内閣府" AND title="宇宙"',             200, "内閣府"),
    ('publisher="宇宙政策委員会"',                     100, "内閣府"),
    ('creator="宇宙航空研究開発機構"',                  200, "JAXA"),
    ('creator="JAXA"',                               100, "JAXA"),
    ('subject="宇宙開発"',                            300, ""),
    ('title="宇宙" AND title="報告書"',               200, ""),
    ('creator="経済産業省" AND title="委託調査"',       100, "経済産業省"),
]


def _text(el):
    if el is None:
        return ""
    for tag in [f"{{{RDF}}}value", f"{{{RDF}}}Description/{{{RDF}}}value"]:
        child = el.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return (el.text or "").strip()


def _call_sru(cql, max_records=100, start=1):
    resp = requests.get(
        NDL_SRU,
        params={
            "operation": "searchRetrieve",
            "version": "1.2",
            "query": cql,
            "maximumRecords": str(min(max_records, 200)),
            "recordSchema": "dcndl",
            "startRecord": str(start),
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.text


def _parse(xml_text, org_hint):
    root = ET.fromstring(xml_text)
    entries = []
    for rec in root.findall(f".//{{{SRW}}}record"):
        rec_data = rec.find(f"{{{SRW}}}recordData")
        if rec_data is None:
            continue
        bib = rec_data.find(f".//{{{DCNDL}}}BibResource")
        if bib is None:
            continue

        # title
        title_el = bib.find(f"{{{DC}}}title")
        title = _text(title_el)
        if not title or len(title) < 8:
            continue

        # URL
        url = ""
        file_type = "html"
        for ident in bib.findall(f"{{{DC}}}identifier"):
            v = _text(ident)
            if v.startswith("http") and any(v.lower().endswith(ext) for ext in (".pdf", ".pptx", ".ppt")):
                url = v
                file_type = v.rsplit(".", 1)[-1].lower()
                break
        if not url:
            admin = rec_data.find(f".//{{{DCNDL}}}BibAdminResource")
            if admin is not None:
                about = admin.get(f"{{{RDF}}}about", "")
                if about.startswith("http"):
                    url = about
            if not url:
                continue

        # org
        org = org_hint
        if not org:
            cr_el = bib.find(f"{{{DC}}}creator")
            org = _text(cr_el) or ""

        # date
        pub_date = None
        date_el = bib.find(f"{{{DC}}}date") or bib.find(f"{{{DCTERMS}}}issued")
        if date_el is not None:
            raw = _text(date_el)[:10]
            try:
                pub_date = str(date.fromisoformat(raw))
            except ValueError:
                if len(raw) >= 4:
                    try:
                        pub_date = f"{raw[:4]}-01-01"
                    except Exception:
                        pass

        # tags from subject
        tags = []
        for subj_el in bib.findall(f"{{{DC}}}subject"):
            v = _text(subj_el)
            if v:
                tags.append(v)

        entries.append({
            "title": title,
            "url": url,
            "source": "ndl",
            "org": org or None,
            "file_type": file_type,
            "lang": "ja",
            "tags": tags or None,
            "published_at": pub_date,
        })
    return entries


def crawl():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    total_new = 0

    for cql, max_count, org_hint in QUERY_SETS:
        fetched = 0
        start = 1
        while fetched < max_count:
            batch = min(200, max_count - fetched)
            try:
                xml = _call_sru(cql, batch, start)
            except Exception as e:
                print(f"  SRU error: {e}")
                break
            entries = _parse(xml, org_hint)
            if not entries:
                break
            for entry in entries:
                res = sb.table("documents").upsert(entry, on_conflict="url").execute()
                if res.data:
                    total_new += 1
            fetched += len(entries)
            start += len(entries)
            if len(entries) < batch:
                break
            time.sleep(1)
        print(f"  クエリ完了: '{cql[:40]}...' → {fetched} 件")

    print(f"NDL クロール完了: {total_new} 件 upsert")


if __name__ == "__main__":
    crawl()
