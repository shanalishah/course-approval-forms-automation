# app/caf_parser.py

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Optional, BinaryIO

import re
import pdfplumber
import pandas as pd
from dateutil.parser import parse as parse_date


TERM_PAT = re.compile(r"\b(Fall|Spring|Summer|Winter)\s+(\d{4})\b", re.IGNORECASE)


@dataclass
class CAFRow:
    # From header
    Program: str
    Year: Optional[int]
    Term: Optional[str]
    City: Optional[str]
    Country: Optional[str]
    Students: Optional[str]

    # From table row
    Course_Number: str
    Course_Title: str
    UR_Course_Equivalent: str
    Discipline: str
    Type_of_Credit: str
    US_Credits: Optional[float]
    Foreign_Credits: Optional[float]
    Link_Course_Search: str
    Link_to_Syllabus: str


def _extract_header_info(page_text: str) -> dict:
    """
    Extract Program, Term, Year, Student from the free text of the first page.
    This will likely need minor tweaks once you see your exact text layout.
    """
    program = None
    student = None
    term = None
    year = None

    # Very template-dependent – adjust these patterns to your CAF
    # Example patterns:
    # "College where course(s) taken: IES Abroad Milan / University of Bocconi"
    m_prog = re.search(r"College where course\(s\) taken.*?:\s*(.+)", page_text)
    if m_prog:
        program = m_prog.group(1).strip()

    # "Name: First Last"
    m_name = re.search(r"Name\s*:\s*(.+)", page_text)
    if m_name:
        student = m_name.group(1).strip()

    # "Semester and year taken: Fall 2025"
    m_term = TERM_PAT.search(page_text)
    if m_term:
        term = m_term.group(1).title()
        year = int(m_term.group(2))

    return {
        "Program": program or "",
        "Students": student or "",
        "Term": term,
        "Year": year,
    }


def _split_course_number_title(raw: str) -> tuple[str, str]:
    """
    Heuristic: first token(s) up to dash or first 'word-with-digits' is course number,
    rest is title.
    """
    if not raw:
        return "", ""

    text = " ".join(raw.split())
    # Common pattern: CODE1234 - Title
    m = re.match(r"^([A-Za-z0-9/ ]+?)\s*[-–]\s*(.+)$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Fallback: take first word as course number, rest as title
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _type_of_credit_from_signatures(major_minor_cell: str, elective_cell: str) -> str:
    """
    Decide Type of Credit from the presence of any writing in the approval cells.
    Your rule:
      - Only Major/Minor cell filled -> "Major/Minor"
      - Only Elective cell filled -> "Elective"
      - Both filled OR writing spanning both -> "Major/Minor, Elective"
    """
    mm = bool(major_minor_cell and major_minor_cell.strip())
    el = bool(elective_cell and elective_cell.strip())

    if mm and el:
        return "Major/Minor, Elective"
    elif mm:
        return "Major/Minor"
    elif el:
        return "Elective"
    else:
        return ""  # Unknown / not marked


def parse_caf_pdf(
    file_obj: BinaryIO,
    program_directory: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Parse a single CAF PDF into a DataFrame of CAFRow records.
    Assumes a standard CAF template with a recognizable course table.
    """
    rows: List[CAFRow] = []

    with pdfplumber.open(file_obj) as pdf:
        if not pdf.pages:
            return pd.DataFrame()

        first_page = pdf.pages[0]
        page_text = first_page.extract_text() or ""

        header_info = _extract_header_info(page_text)
        program = header_info["Program"]
        term = header_info["Term"]
        year = header_info["Year"]
        student = header_info["Students"]

        city = None
        country = None
        link_course_search = ""
        default_foreign_credits = None
        default_us_credits = None

        # Enrich from program directory if provided
        if program_directory is not None and program:
            match = program_directory.loc[
                program_directory["Program"].str.strip().str.lower()
                == program.strip().lower()
            ]
            if not match.empty:
                rec = match.iloc[0]
                city = rec.get("City")
                country = rec.get("Country")
                link_course_search = rec.get("Link Course Search", "") or ""
                default_foreign_credits = rec.get("Default Foreign Credits")
                default_us_credits = rec.get("Default US Credits")

        # Extract tables, find the one with the CAF course header
        tables = first_page.extract_tables()
        target_table = None
        for tbl in tables:
            if not tbl:
                continue
            header_row = tbl[0]
            header_str = " ".join([c or "" for c in header_row]).lower()
            if "course subject" in header_str and "ur course" in header_str:
                target_table = tbl
                break

        if target_table is None:
            # No recognizable table; return empty or raise
            return pd.DataFrame()

        # Expected column layout (adjust indices if your template differs)
        # [0] Course Subject/Number and Title
        # [1] UR Course Equivalent
        # [2] Major/Minor approval (signature/name)
        # [3] Elective approval (signature/name)
        # [4] Comments (optional)
        for row in target_table[1:]:  # skip header
            # Pad row to length 5
            padded = list(row) + [""] * (5 - len(row))
            course_cell, ur_cell, mm_cell, el_cell, comments_cell = padded[:5]

            # Skip empty rows
            if not (course_cell and course_cell.strip()):
                continue

            course_number, course_title = _split_course_number_title(course_cell)
            ur_equiv = (ur_cell or "").strip()

            type_of_credit = _type_of_credit_from_signatures(mm_cell, el_cell)

            # For now, Discipline and credits left blank; to be filled via mapping / UI
            discipline = ""
            foreign_credits = default_foreign_credits
            us_credits = default_us_credits

            # Syllabus link: user can paste later; we leave blank
            syllabus_link = ""

            caf_row = CAFRow(
                Program=program,
                Year=year,
                Term=term,
                City=city,
                Country=country,
                Students=student,
                Course_Number=course_number,
                Course_Title=course_title,
                UR_Course_Equivalent=ur_equiv,
                Discipline=discipline,
                Type_of_Credit=type_of_credit,
                US_Credits=us_credits,
                Foreign_Credits=foreign_credits,
                Link_Course_Search=link_course_search,
                Link_to_Syllabus=syllabus_link,
            )
            rows.append(caf_row)

    df = pd.DataFrame([asdict(r) for r in rows])

    # Optional: rename columns to exactly match your sample Excel
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

    # Reorder columns to your preferred schema
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
    ]
    df = df.reindex(columns=desired_order)

    return df
