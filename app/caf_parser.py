# app/caf_parser.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional

import pdfplumber
import pandas as pd
import re

from ai_extractor import ai_extract_courses_from_pdf_bytes


TERM_PAT = re.compile(r"\b(Fall|Spring|Summer|Winter)\s+(\d{4})\b", re.IGNORECASE)


@dataclass
class CAFRow:
    Program: str
    Year: Optional[int]
    Term: Optional[str]
    City: Optional[str]
    Country: Optional[str]
    Course_Number: str
    Course_Title: str
    UR_Course_Equivalent: str
    Discipline: str
    Type_of_Credit: str
    US_Credits: Optional[float]
    Foreign_Credits: Optional[float]
    Link_Course_Search: str
    Link_to_Syllabus: str
    Students: str
    Comments: str
    _source: str = "rule"


def _split_course_number_title(raw: str) -> tuple[str, str]:
    """
    Split something like:
      'STAT 1003 - Statistical Techniques'
    into ('STAT 1003', 'Statistical Techniques').
    """
    if not raw:
        return "", ""
    text = " ".join(str(raw).split())
    m = re.match(r"^([A-Za-z0-9/ ]+?)\s*[-–]\s*(.+)$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _type_of_credit_from_cells(mm_cell: str | None, el_cell: str | None) -> str:
    """
    Your rule:
      - Only Major/Minor cell filled -> "Major/Minor"
      - Only Elective cell filled -> "Elective"
      - Both filled OR writing spanning both -> "Major/Minor, Elective"
      - Neither -> ""
    """
    mm = bool(mm_cell and str(mm_cell).strip())
    el = bool(el_cell and str(el_cell).strip())

    if mm and el:
        return "Major/Minor, Elective"
    if mm:
        return "Major/Minor"
    if el:
        return "Elective"
    return ""


def _extract_header_info(page_text: str) -> dict:
    """
    Very light header parsing: Program, Term, Year, Student.
    This is template-dependent and can be refined later.
    """
    program = ""
    student = ""
    term = None
    year = None

    # Program (e.g., "College where course(s) taken: IES Abroad Milan / University of Bocconi")
    m_prog = re.search(r"College where course\(s\) taken.*?:\s*(.+)", page_text)
    if m_prog:
        program = m_prog.group(1).strip()

    # Student name
    m_name = re.search(r"Name\s*:? ?(.+)", page_text)
    if m_name:
        # take up to end of line
        student = m_name.group(1).splitlines()[0].strip()

    # Term + year (e.g., "Fall 2025")
    m_term = TERM_PAT.search(page_text)
    if m_term:
        term = m_term.group(1).title()
        year = int(m_term.group(2))

    return {
        "Program": program,
        "Students": student,
        "Term": term,
        "Year": year,
    }


def _find_tables(page) -> tuple[Optional[list], Optional[list]]:
    """
    Identify:
    - course_table: contains 'Course Subject/Number and Title'
    - dept_table: contains 'UR Course Equivalent'
    Returns (course_table, dept_table). They may be the same table.
    """
    tables = page.extract_tables() or []

    course_table = None
    dept_table = None

    def header_text(rows: list) -> str:
        if not rows:
            return ""
        # Check first one or two rows
        txt = " ".join([(c or "") for c in rows[0]])
        if len(rows) > 1:
            txt += " " + " ".join([(c or "") for c in rows[1]])
        txt = txt.lower()
        return re.sub(r"\s+", " ", txt)

    for t in tables:
        h = header_text(t)
        if "course subject/number" in h or "course subject / number" in h:
            course_table = t
        if "ur course equivalent" in h or "urcourseequivalent" in h:
            dept_table = t

    # If one table has both pieces, use it for both
    if course_table is None and dept_table is not None:
        h = header_text(dept_table)
        if "course subject/number" in h:
            course_table = dept_table
    if dept_table is None and course_table is not None:
        h = header_text(course_table)
        if "ur course equivalent" in h:
            dept_table = course_table

    return course_table, dept_table


def _parse_rule_based(pdf_bytes: bytes) -> List[CAFRow]:
    """
    Rule-based extraction using pdfplumber; works well on typed/fillable CAFs.
    """
    rows: List[CAFRow] = []

    with pdfplumber.open(pdf_bytes) as pdf:
        if not pdf.pages:
            return rows

        page = pdf.pages[0]
        page_text = page.extract_text() or ""
        header = _extract_header_info(page_text)

        program = header["Program"]
        term = header["Term"]
        year = header["Year"]
        student = header["Students"]

        # In this basic version, City/Country are filled from program_directory later
        city = None
        country = None

        course_table, dept_table = _find_tables(page)
        if course_table is None:
            return rows  # nothing to do

        # --- 1) Extract foreign course list ---
        courses: list[dict] = []
        # assume first row is header
        for row in course_table[1:]:
            if not row:
                continue
            raw_course = (row[0] or "").strip()
            if not raw_course:
                continue
            number, title = _split_course_number_title(raw_course)
            courses.append({"number": number, "title": title})

        # --- 2) Extract meta rows (UR equiv + approvals + comments) ---
        meta_rows: list[dict] = []
        if dept_table is not None and len(dept_table) > 2:
            # Often first row is header, second row is dept header; start from row 2
            for row in dept_table[2:]:
                # pad to at least 5 columns
                r = list(row) + [""] * (5 - len(row))
                ur_cell = r[0]
                el_cell = r[1]
                mm_cell = r[2]
                comments = r[4] if len(r) > 4 else ""
                if any([ur_cell, el_cell, mm_cell, comments]):
                    meta_rows.append(
                        {
                            "ur": (ur_cell or "").strip(),
                            "elective": el_cell or "",
                            "mm": mm_cell or "",
                            "comments": (comments or "").strip(),
                        }
                    )

        # --- 3) Align courses with meta rows by position ---
        for idx, course in enumerate(courses):
            meta = meta_rows[idx] if idx < len(meta_rows) else {}

            type_credit = _type_of_credit_from_cells(
                meta.get("mm", ""),
                meta.get("elective", ""),
            )

            rows.append(
                CAFRow(
                    Program=program,
                    Year=year,
                    Term=term,
                    City=city,
                    Country=country,
                    Course_Number=course["number"],
                    Course_Title=course["title"],
                    UR_Course_Equivalent=meta.get("ur", ""),
                    Discipline="",
                    Type_of_Credit=type_credit,
                    US_Credits=None,
                    Foreign_Credits=None,
                    Link_Course_Search="",
                    Link_to_Syllabus="",
                    Students=student,
                    Comments=meta.get("comments", ""),
                    _source="rule",
                )
            )

    return rows


def parse_caf_pdf_hybrid(pdf_bytes: bytes,
                         program_directory: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Main entry point:
      1) Try rule-based pdfplumber parser.
      2) If it returns 0 rows, fall back to GPT-4o vision (AI).
      3) Optionally enrich with program directory (City/Country).
    """
    # --- 1) Rule-based ---
    rows: List[CAFRow] = []
    try:
        rows = _parse_rule_based(pdf_bytes)
    except Exception:
        rows = []

    # --- 2) AI fallback if needed ---
    if not rows:
        ai_rows = ai_extract_courses_from_pdf_bytes(pdf_bytes)
        df = pd.DataFrame(ai_rows)
    else:
        df = pd.DataFrame([asdict(r) for r in rows])

        # Normalize column names to final schema
        df = df.rename(
            columns={
                "Course_Number": "Course Number",
                "Course_Title": "Course Title",
                "Type_of_Credit": "Type of Credit",
                "US_Credits": "US Credits",
                "Foreign_Credits": "Foreign Credits",
                "Link_Course_Search": "Link Course Search",
                "Link_to_Syllabus": "Link to Syllabus",
            }
        )

    # --- 3) Enrich City/Country via program_directory ---
    if program_directory is not None and not df.empty and "Program" in df.columns:
        prog_df = program_directory.copy()
        prog_df.columns = [c.strip() for c in prog_df.columns]

        # Determine which column in the program file is the program key
        key_col = None
        if "Program" in prog_df.columns:
            key_col = "Program"
        elif "Program Name" in prog_df.columns:
            key_col = "Program Name"

        if key_col is not None:
            # Clean "Star icon" prefix etc.
            prog_df["Program_key_clean"] = (
                prog_df[key_col].astype(str)
                .str.replace("Star icon", "", regex=False)
                .str.strip()
                .str.lower()
            )
            df["Program_key_clean"] = df["Program"].astype(str).str.strip().str.lower()

            prog_sub = prog_df[["Program_key_clean", "City", "Country"]].drop_duplicates()

            df = df.merge(
                prog_sub,
                on="Program_key_clean",
                how="left",
                suffixes=("", "_from_prog"),
            )

            # Prefer city/country from program directory if our columns are empty
            if "City" in df.columns and "City_from_prog" in df.columns:
                df["City"] = df["City"].fillna(df["City_from_prog"])
            else:
                df["City"] = df.get("City_from_prog")

            if "Country" in df.columns and "Country_from_prog" in df.columns:
                df["Country"] = df["Country"].fillna(df["Country_from_prog"])
            else:
                df["Country"] = df.get("Country_from_prog")

            df = df.drop(columns=[c for c in df.columns if c.endswith("_from_prog") or c == "Program_key_clean"])

    # --- 4) Final column order ---
    if not df.empty:
        desired_order = [
            "Program",
            "Year",
            "Term",
            "City",
            "Country",
            "Course Number",
            "Course Title",
            "UR Course Equivalent",
            "Discipline",
            "Type of Credit",
            "US Credits",
            "Foreign Credits",
            "Link Course Search",
            "Link to Syllabus",
            "Students",
            "Comments",
            "_source",
        ]
        for col in desired_order:
            if col not in df.columns:
                df[col] = None
        df = df[desired_order]

    return df
