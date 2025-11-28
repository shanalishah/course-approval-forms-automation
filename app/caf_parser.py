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
#   COURSE NUMBER / TITLE SPLIT
# ==============================

COURSE_CODE_RE = re.compile(r"^[A-Za-z]{2,}\s*\d{2,}[A-Za-z0-9\-]*$")


def _split_course_number_title(raw: str) -> tuple[str, str]:
    if not raw:
        return "", ""

    text = " ".join(str(raw).split())

    # 1. PATTERN: "STAT 1003 Calculus"
    m = re.match(r"^([A-Za-z]{2,}\s*\d{2,}[A-Za-z0-9\-]*)\s*(.*)$", text)
    if m:
        num = m.group(1).strip()
        rest = m.group(2).lstrip(":-– ").strip()
        return num, rest

    # 2. PATTERN: "STAT 1003 - Calculus"
    m = re.match(r"^(.+?)\s*[-–]\s*(.+)$", text)
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        if COURSE_CODE_RE.match(left):
            return left, right
        return "", text

    # 3. If no digits → pure title
    if not any(ch.isdigit() for ch in text):
        return "", text

    # 4. Fallback: first token has digits
    parts = text.split()
    if any(ch.isdigit() for ch in parts[0]):
        return parts[0], " ".join(parts[1:]).strip()

    return "", text


# ==============================
#   NORMALIZE TITLE/CODE MIXUPS
# ==============================

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

        if not num:
            continue

        lower_num = num.lower()
        lower_title = title.lower()
        has_digit = any(ch.isdigit() for ch in num)

        # CASE 1 — "IES French Language Course (4 credits)"
        case_pure_title = (
            not has_digit and len(num) > 10 and not looks_like_code(num)
        )

        # CASE 2 — mentions credits
        case_mentions_credits = ("credit" in lower_num and not looks_like_code(num))

        # CASE 3 — title is just "(4 credits)"
        case_title_is_credits = (
            bool(title) and "credit" in lower_title and len(title) <= 20
        )

        if case_pure_title or case_mentions_credits or case_title_is_credits:
            new_title = num
            if case_title_is_credits:
                new_title = f"{num} {title}".strip()

            df.at[idx, "Course Title"] = new_title
            df.at[idx, "Course Number"] = ""

    return df


# ==============================
#      PDFPLUMBER TABLE PARSER
# ==============================

def _parse_caf_pdf_rule(pdf_bytes: bytes) -> List[dict]:
    rows: List[dict] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            table = page.extract_table()

            if not table or len(table) <= 1:
                return []

            header = [h.strip() if h else "" for h in table[0]]
            lines = table[1:]

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

                if elective_val and major_val:
                    credit = "Major/Minor, Elective"
                elif elective_val:
                    credit = "Elective"
                elif major_val:
                    credit = "Major/Minor"
                else:
                    credit = ""

                rows.append({
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
                })

    except Exception:
        return []

    return rows


# ==============================
#      PROGRAM DIRECTORY MERGE
# ==============================

def _enrich_with_program_directory(df: pd.DataFrame, program_directory: pd.DataFrame) -> pd.DataFrame:
    if df.empty or program_directory is None or program_directory.empty:
        return df

    prog = program_directory.copy()

    # Normalize
    if "Program" not in prog.columns:
        if "Program Name" in prog.columns:
            prog["Program"] = (
                prog["Program Name"]
                .astype(str)
                .str.replace("Star icon", "", regex=False)
                .str.strip()
            )
        else:
            return df

    prog_small = prog[["Program", "City", "Country"]].copy()

    # Exact merge
    merged = df.merge(
        prog_small,
        on="Program",
        how="left",
        suffixes=("", "_dir"),
    )

    # Fill exact matches
    if "City_dir" in merged.columns:
        merged["City"] = merged["City"].fillna(merged["City_dir"])
        merged.drop(columns=["City_dir"], inplace=True)

    if "Country_dir" in merged.columns:
        merged["Country"] = merged["Country"].fillna(merged["Country_dir"])
        merged.drop(columns=["Country_dir"], inplace=True)

    # Fuzzy match
    mask = merged["Program"].notna() & merged["City"].isna() & merged["Country"].isna()

    if mask.any():
        all_programs = prog_small["Program"].dropna().unique().tolist()

        def best_match(name: str) -> Optional[str]:
            name = str(name).strip()
            matches = difflib.get_close_matches(name, all_programs, n=1, cutoff=0.6)
            return matches[0] if matches else None

        merged.loc[mask, "_prog_match"] = (
            merged.loc[mask, "Program"].apply(best_match)
        )

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
#        MAIN HYBRID PARSER
# ==============================

def parse_caf_pdf_hybrid(pdf_bytes: bytes, program_directory: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    rows = _parse_caf_pdf_rule(pdf_bytes)

    if not rows:
        rows = ai_extract_courses_from_pdf_bytes(pdf_bytes)

    df = pd.DataFrame(rows)

    if program_directory is not None and "Program" in df.columns:
        df = _enrich_with_program_directory(df, program_directory)

    df = _normalize_course_number_title_columns(df)

    df["Type of Credit"] = df["Type of Credit"].fillna("").str.strip()
    df = df[df["Type of Credit"] != ""].reset_index(drop=True)

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

    return df[desired_order]
