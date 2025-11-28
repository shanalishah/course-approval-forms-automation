Here’s your updated app/caf_parser.py with fuzzy program matching wired in. You can copy-paste the whole file.

# app/caf_parser.py
from __future__ import annotations

import io
import re
import difflib
from typing import Optional, List

import pandas as pd
import pdfplumber

from ai_extractor import ai_extract_courses_from_pdf_bytes


# ==============================
# Smart course number/title split
# ==============================

COURSE_CODE_RE = re.compile(r"^[A-Za-z]{2,}\s*\d{2,}[A-Za-z0-9\-]*$")


def _split_course_number_title(raw: str) -> tuple[str, str]:
    if not raw:
        return "", ""
    text = " ".join(str(raw).split())

    # 1) Pattern: STAT 1003 Calculus
    m = re.match(r"^([A-Za-z]{2,}\s*\d{2,}[A-Za-z0-9\-]*)\s*(.*)$", text)
    if m:
        num = m.group(1).strip()
        rest = m.group(2).lstrip(":-– ").strip()
        return num, rest

    # 2) Pattern: STAT 1003 - Calculus
    m = re.match(r"^(.+?)\s*[-–]\s*(.+)$", text)
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        if COURSE_CODE_RE.match(left):
            return left, right
        return "", text  # left is not valid → whole thing is title

    # 3) If no digits → pure title
    if not any(ch.isdigit() for ch in text):
        return "", text

    # 4) Fallback: first token contains digits
    parts = text.split()
    if any(ch.isdigit() for ch in parts[0]):
        num = parts[0]
        title = " ".join(parts[1:]).strip()
        return num, title

    # 5) Everything else → treat as title
    return "", text


# ========================================
# Normalize AI output for title-only courses
# ========================================

def _normalize_course_number_title_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    def looks_like_code(s: str) -> bool:
        if not isinstance(s, str):
            return False
        s = s.strip()
        if not s:
            return False
        first = s.split()[0]
        return bool(COURSE_CODE_RE.match(first))

    for idx, row in df.iterrows():
        num = (row.get("Course Number") or "").strip()
        title = (row.get("Course Title") or "").strip()

        # nothing to fix
        if not num:
            continue

        lower_num = num.lower()
        lower_title = title.lower()

        has_digit_num = any(ch.isdigit() for ch in num)

        # CASE 1: number field actually looks like a long title (no digits, not a code)
        is_pure_title_num = (
            not has_digit_num
            and len(num) > 10
            and not looks_like_code(num)
        )

        # CASE 2: number field itself mentions credits (weird but possible)
        num_mentions_credits = ("credit" in lower_num and not looks_like_code(num))

        # CASE 3: title is basically just "(4 credits)" etc.
        title_is_just_credits = (
            bool(title)
            and "credit" in lower_title
            and len(title) <= 20
        )

        if is_pure_title_num or num_mentions_credits or title_is_just_credits:
            # merge the two pieces if title exists and is just credits
            new_title = num
            if title and title_is_just_credits:
                new_title = f"{num} {title}".strip()

            df.at[idx, "Course Title"] = new_title
            df.at[idx, "Course Number"] = ""

    return df


# ==============================
# RULE-BASED PARSER (pdfplumber)
# ==============================

def _parse_caf_pdf_rule(pdf_bytes: bytes) -> List[dict]:
    """
    Attempt to extract rows using table-based pdfplumber parsing.
    If nothing is found, return an empty list.
    """
    rows = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            table = page.extract_table()
            if not table or len(table) <= 1:
                return []

            header = [h.strip() if h else "" for h in table[0]]
            lines = table[1:]

            # Find column indices
            def find_col(keywords: List[str]) -> Optional[int]:
                for i, h in enumerate(header):
                    for kw in keywords:
                        if kw.lower() in h.lower():
                            return i
                return None

            idx_foreign = find_col(["Subject", "Number", "Title"])
            idx_equiv = find_col(["UR Course Equivalent"])
            idx_elective = find_col(["Elective"])
            idx_major = find_col(["Major"])
            idx_comments = find_col(["Comment"])

            for line in lines:
                if not any(line):
                    continue

                foreign_raw = (line[idx_foreign] or "").strip() if idx_foreign is not None else ""
                if not foreign_raw:
                    continue

                num, title = _split_course_number_title(foreign_raw)

                eqv = (line[idx_equiv] or "").strip() if idx_equiv is not None else ""
                elective_val = (line[idx_elective] or "").strip() if idx_elective is not None else ""
                major_val = (line[idx_major] or "").strip() if idx_major is not None else ""
                comments = (line[idx_comments] or "").strip() if idx_comments is not None else ""

                # Determine type_of_credit
                if elective_val and major_val:
                    credit = "Major/Minor, Elective"
                elif elective_val:
                    credit = "Elective"
                elif major_val:
                    credit = "Major/Minor"
                else:
                    credit = ""

                rows.append(
                    {
                        "Program": "",
                        "Year": None,
                        "Term": "",
                        "City": None,
                        "Country": None,
                        "Course Number": num,
                        "Course Title": title,
                        "UR Course Equivalent": eqv,
                        "Discipline": "",
                        "Type of Credit": credit,
                        "US Credits": None,
                        "Foreign Credits": None,
                        "Link Course Search": "",
                        "Link to Syllabus": "",
                        "Students": "",
                        "Comments": comments,
                        "_source": "rule",
                    }
                )
    except Exception:
        return []
    return rows


# ==============================
# PROGRAM DIRECTORY ENRICHMENT
# ==============================

def _enrich_with_program_directory(df: pd.DataFrame,
                                   program_directory: pd.DataFrame) -> pd.DataFrame:
    """
    Enrich df with City/Country from program_directory.

    Strategy:
      1) Exact match on Program
      2) For rows still missing City/Country, use fuzzy matching
         (closest Program name in the directory).
    """
    if df.empty or program_directory is None or program_directory.empty:
        return df

    prog = program_directory.copy()

    # Normalize program name column in directory
    if "Program" not in prog.columns:
        if "Program Name" in prog.columns:
            prog["Program"] = (
                prog["Program Name"]
                .astype(str)
                .str.replace("Star icon", "", regex=False)
                .str.strip()
            )
        else:
            # Nothing we can do
            return df

    # Keep only the columns we need
    prog_small = prog[["Program", "City", "Country"]].copy()

    # 1) Exact merge on Program
    merged = df.merge(
        prog_small,
        on="Program",
        how="left",
        suffixes=("", "_dir"),
    )

    # Use directory values where present
    if "City_dir" in merged.columns:
        merged["City"] = merged["City"].fillna(merged["City_dir"])
        merged.drop(columns=["City_dir"], inplace=True)
    if "Country_dir" in merged.columns:
        merged["Country"] = merged["Country"].fillna(merged["Country_dir"])
        merged.drop(columns=["Country_dir"], inplace=True)

    # 2) Fuzzy match for rows still missing City/Country
    mask_needs_match = merged["Program"].notna() & merged["City"].isna() & merged["Country"].isna()
    if mask_needs_match.any():
        dir_programs = prog_small["Program"].dropna().unique().tolist()

        def best_match(name: str) -> Optional[str]:
            name = str(name).strip()
            if not name:
                return None
            matches = difflib.get_close_matches(name, dir_programs, n=1, cutoff=0.6)
            return matches[0] if matches else None

        merged.loc[mask_needs_match, "_prog_match"] = (
            merged.loc[mask_needs_match, "Program"].apply(best_match)
        )

        # merge City/Country from directory based on the matched name
        match_df = prog_small.rename(columns={"Program": "_prog_match"})
        merged = merged.merge(
            match_df,
            on="_prog_match",
            how="left",
            suffixes=("", "_match"),
        )

        if "City_match" in merged.columns:
            merged["City"] = merged["City"].fillna(merged["City_match"])
            merged.drop(columns=["City_match"], inplace=True)

        if "Country_match" in merged.columns:
            merged["Country"] = merged["Country"].fillna(merged["Country_match"])
            merged.drop(columns=["Country_match"], inplace=True)

        merged.drop(columns=["_prog_match"], inplace=True, errors="ignore")

    return merged


# ==============================
# HYBRID PARSER (rule → AI)
# ==============================

def parse_caf_pdf_hybrid(pdf_bytes: bytes, program_directory: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    rows = _parse_caf_pdf_rule(pdf_bytes)

    # If rule-based gave nothing → AI fallback
    if not rows:
        rows = ai_extract_courses_from_pdf_bytes(pdf_bytes)

    df = pd.DataFrame(rows)

    # Merge with Program Directory if provided (exact + fuzzy)
    if program_directory is not None and "Program" in df.columns:
        df = _enrich_with_program_directory(df, program_directory)

    # === Normalize AI misplacements ===
    df = _normalize_course_number_title_columns(df)

    # === DROP rows without approvals ===
    df["Type of Credit"] = df["Type of Credit"].fillna("").str.strip()
    df = df[df["Type of Credit"] != ""].reset_index(drop=True)

    # Final column order
    desired_order = [
        "Program", "Year", "Term", "City", "Country",
        "Course Number", "Course Title", "UR Course Equivalent",
        "Discipline", "Type of Credit",
        "US Credits", "Foreign Credits",
        "Link Course Search", "Link to Syllabus",
        "Students", "Comments", "_source",
    ]

    for col in desired_order:
        if col not in df.columns:
            df[col] = None

    df = df[desired_order]
    return df
