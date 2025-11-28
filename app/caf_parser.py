# app/caf_parser.py
from __future__ import annotations

import io
import re
from typing import Optional, List

from difflib import get_close_matches

import pandas as pd
import pdfplumber

from ai_extractor import ai_extract_courses_from_pdf_bytes


# ==============================
# Smart course number/title split
# ==============================

COURSE_CODE_RE = re.compile(r"^[A-Za-z]{2,}\s*\d{2,}[A-Za-z0-9\-]*$")


def _split_course_number_title(raw: str) -> tuple[str, str]:
    """
    Take a raw foreign course string (e.g., 'STAT 1003 - Calculus I')
    and split it into (course_number, course_title).
    """
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
        # left is not a valid code → treat entire string as title
        return "", text

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
    """
    Fix cases where the AI puts the full title into Course Number
    or splits title and '(4 credits)' weirdly.
    """
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
    rows: List[dict] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            # we only try first page for now (most CAFs are 1–2 pages with table on page 1)
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

                # Determine type_of_credit based on where signatures/marks are
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
        # If anything goes wrong, we just fall back to AI
        return []
    return rows


# ==============================
# PROGRAM NAME MATCHING HELPERS
# ==============================

def _normalize_program_str(x: str) -> str:
    """
    Normalize program strings for comparison:
    - remove 'Star icon'
    - lowercase
    - strip whitespace
    """
    return str(x).replace("Star icon", "").strip().lower()


def _match_program_name(value: str, program_list: List[str]) -> Optional[str]:
    """
    Attempts to match a CAF 'Program' string to a directory program using:
    1. Exact match (case-insensitive)
    2. Token inclusion (DIS Copenhagen → 'DIS - Study Abroad in Copenhagen, Denmark')
    3. Fuzzy match (difflib)
    """
    if not value:
        return None

    value_norm = _normalize_program_str(value)
    tokens = set(value_norm.split())
    if not tokens:
        return None

    # 1) Exact match
    for p in program_list:
        if _normalize_program_str(p) == value_norm:
            return p

    # 2) Token inclusion match
    for p in program_list:
        ptoks = set(_normalize_program_str(p).split())
        if tokens.issubset(ptoks) or ptoks.issubset(tokens):
            return p

    # 3) Fuzzy match
    dir_norm = [_normalize_program_str(p) for p in program_list]
    fuzzy = get_close_matches(value_norm, dir_norm, n=1, cutoff=0.6)
    if fuzzy:
        match_norm = fuzzy[0]
        for p in program_list:
            if _normalize_program_str(p) == match_norm:
                return p

    return None


# ==============================
# HYBRID PARSER (rule → AI)
# ==============================

def parse_caf_pdf_hybrid(pdf_bytes: bytes, program_directory: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    1. Try rule-based table parsing first.
    2. If no rows found, fall back to AI extraction.
    3. Normalize course number/title.
    4. Enrich Program → City/Country from program_directory via smart matching.
    5. Drop rows without any approval (Type of Credit empty).
    """
    # --- 1) RULE-BASED PARSE ---
    rows = _parse_caf_pdf_rule(pdf_bytes)

    # --- 2) AI FALLBACK ---
    if not rows:
        rows = ai_extract_courses_from_pdf_bytes(pdf_bytes)

    df = pd.DataFrame(rows)

    # If completely empty, just return
    if df.empty:
        return df

    # --- 3) Normalize AI misplacements ---
    df = _normalize_course_number_title_columns(df)

    # --- 4) Enrich Program → City/Country via smart merge ---
    if program_directory is not None and "Program" in df.columns:
        prog = program_directory.copy()

        # Normalize program directory
        if "Program" not in prog.columns and "Program Name" in prog.columns:
            prog["Program"] = (
                prog["Program Name"]
                .astype(str)
                .str.replace("Star icon", "", regex=False)
                .str.strip()
            )
        prog["Program"] = prog["Program"].astype(str).str.strip()

        all_programs = prog["Program"].tolist()

        # Try to match each CAF Program to a directory Program
        df["Matched Program"] = df["Program"].apply(
            lambda x: _match_program_name(str(x), all_programs)
        )

        # Merge on the matched program
        df = df.merge(
            prog[["Program", "City", "Country"]],
            left_on="Matched Program",
            right_on="Program",
            how="left",
            suffixes=("", "_dir"),
        )

        # Prefer directory City/Country where available
        df["City"] = df["City_dir"].combine_first(df.get("City"))
        df["Country"] = df["Country_dir"].combine_first(df.get("Country"))

        # Clean up helper columns
        df = df.drop(columns=[c for c in df.columns if c.endswith("_dir")])
        df = df.drop(columns=["Matched Program"], errors="ignore")

    # --- 5) DROP rows without approvals ---
    df["Type of Credit"] = df["Type of Credit"].fillna("").str.strip()
    df = df[df["Type of Credit"] != ""].reset_index(drop=True)

    # --- 6) Final column order ---
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
