# app/caf_parser.py
from __future__ import annotations

import io
import re
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

        # AI sometimes puts full title in Course Number
        if num and not title:
            if not any(ch.isdigit() for ch in num) and len(num) > 10 and not looks_like_code(num):
                df.at[idx, "Course Title"] = num
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
# HYBRID PARSER (rule → AI)
# ==============================

def parse_caf_pdf_hybrid(pdf_bytes: bytes, program_directory: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    rows = _parse_caf_pdf_rule(pdf_bytes)

    # If rule-based gave nothing → AI fallback
    if not rows:
        rows = ai_extract_courses_from_pdf_bytes(pdf_bytes)

    df = pd.DataFrame(rows)

    # Merge with Program Directory if provided
    if program_directory is not None and "Program" in df.columns:
        prog = program_directory.copy()
        if "Program" not in prog.columns and "Program Name" in prog.columns:
            prog["Program"] = (
                prog["Program Name"]
                .astype(str)
                .str.replace("Star icon", "", regex=False)
                .str.strip()
            )

        df = df.merge(
            prog[["Program", "City", "Country"]],
            on="Program",
            how="left",
            suffixes=("", "_dir"),
        )

        df["City"] = df["City"].fillna(df["City_dir"])
        df["Country"] = df["Country"].fillna(df["Country_dir"])
        df = df.drop(columns=[c for c in df.columns if c.endswith("_dir")])

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
        "Students", "Comments", "_source"
    ]

    for col in desired_order:
        if col not in df.columns:
            df[col] = None

    df = df[desired_order]
    return df
