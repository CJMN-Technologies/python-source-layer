import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    print("Missing SUPABASE_URL or SUPABASE_KEY in .env")
    sys.exit(1)

supabase = create_client(supabase_url, supabase_key)

def clean_and_renumber():
    res = supabase.schema("external").table("academic_lgu_events").select("*").execute()
    rows = res.data if hasattr(res, "data") else res
    
    if not rows:
        print("No rows found in database.")
        return
        
    # Sort by scraped_at so we keep the oldest versions
    rows.sort(key=lambda x: x.get("scraped_at", ""))
    
    seen_texts = set()
    to_keep = []
    
    for row in rows:
        combined_text = f"{row.get('post_text') or ''} {row.get('image_text') or ''}".strip()
        prefix = combined_text[:100]
        
        if prefix and prefix in seen_texts:
            continue # Duplicate
        else:
            if prefix:
                seen_texts.add(prefix)
            to_keep.append(row)
            
    print(f"Total original rows: {len(rows)}")
    print(f"Duplicates to remove: {len(rows) - len(to_keep)}")
    print(f"Rows to keep: {len(to_keep)}")
    
    print("Deleting ALL rows temporarily to safely re-number IDs without primary key collisions...")
    # Delete in chunks
    all_ids = [r["id"] for r in rows]
    for i in range(0, len(all_ids), 100):
        chunk = all_ids[i:i+100]
        supabase.schema("external").table("academic_lgu_events").delete().in_("id", chunk).execute()
        
    print("Renumbering remaining rows...")
    
    # Re-categorize everything based on source_name just to be sure
    for row in to_keep:
        name_lower = row.get("source_name", "").lower()
        if "pagasa" in name_lower:
            row["category"] = "pagasa"
        elif "government" in name_lower or "pio" in name_lower:
            row["category"] = "lgu"
        else:
            row["category"] = "acad"
            
    academic = [r for r in to_keep if r["category"] == "acad"]
    lgu = [r for r in to_keep if r["category"] == "lgu"]
    pagasa = [r for r in to_keep if r["category"] == "pagasa"]
    
    def extract_num(id_str):
        parts = id_str.split("_")
        if len(parts) >= 3 and parts[-1].isdigit():
            return int(parts[-1])
        parts = id_str.split("-")
        if len(parts) >= 3 and parts[-1].isdigit():
            return int(parts[-1])
        return 999999
        
    academic.sort(key=lambda x: extract_num(x.get("id", "")))
    lgu.sort(key=lambda x: extract_num(x.get("id", "")))
    pagasa.sort(key=lambda x: extract_num(x.get("id", "")))
    
    final_rows = []
    for i, row in enumerate(academic, start=1):
        row["id"] = f"external_acad_{i:04d}"
        final_rows.append(row)
        
    for i, row in enumerate(lgu, start=1):
        row["id"] = f"external_lgu_{i:04d}"
        final_rows.append(row)
        
    for i, row in enumerate(pagasa, start=1):
        row["id"] = f"external_pagasa_{i:04d}"
        final_rows.append(row)
        
    print("Re-inserting cleaned rows into the database...")
    for i in range(0, len(final_rows), 50):
        chunk = final_rows[i:i+50]
        supabase.schema("external").table("academic_lgu_events").insert(chunk).execute()
        
    print("Done! Database is clean and IDs are perfectly sequential.")

if __name__ == "__main__":
    clean_and_renumber()
