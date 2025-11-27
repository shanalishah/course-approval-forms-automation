# app/ai_extractor.py
from __future__ import annotations

import json
from typing import List, Dict, Any

from openai import OpenAI

from pdf_utils import pdf_to_page_images_b64

client = OpenAI()  # reads OPENAI_API_KEY from env/Streamlit secrets


SYSTEM_PROMPT = """
You are a careful assistant that reads COURSE APPROVAL FORMS (CAF) from images.

Each form usually has a table with columns like:
- Course Subject/Number and Title (foreign course)
- UR Course Equivalent
- Elective Approval
- Major/Minor Approval
- Comments

Your task:
1. Identify ALL rows that correspond to individual foreign courses.
2. For each course, extract:
   - program           (string, if visible; otherwise "")
   - term              (e.g., "Fall", "Spring", if visible; otherwise "")
   - year              (4-digit int, if visible; otherwise null)
   - student           (student name, if visible; otherwise "")
   - course_number     (foreign)
   - course_title      (foreign)
   - ur_course_equiv   (UR course text; may be empty)
   - type_of_credit    (one of: "", "Elective", "Major/Minor", "Major/Minor, Elective")
   - comments          (text from comments column, if any)

Rules for type_of_credit:
- Look WHERE the instructor's name or signature is written in that course row.
- If it appears only in the Elective column -> "Elective".
- Only in the Major/Minor column -> "Major/Minor".
- Appears in both columns OR between them -> "Major/Minor, Elective".
- If no approval mark -> "".

Return ONLY valid JSON, no commentary, in this shape:

{
  "program": "...",
  "term": "...",
  "year": 2025,
  "student": "...",
  "courses": [
     {
       "course_number": "...",
       "course_title": "...",
       "ur_course_equiv": "...",
       "type_of_credit": "...",
       "comments": "..."
     }
  ]
}
"""


def _call_gpt4o_on_images(images_b64: List[str]) -> Dict[str, Any]:
    """
    Send one or more base64 PNG images to GPT-4o vision and return parsed JSON dict.
    """
    content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": "Extract the course information from these Course Approval Form pages.",
        }
    ]

    # IMPORTANT: type must be 'image_url', not 'input_image'
    for b64 in images_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )

    resp = client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )

    msg = resp.choices[0].message
    raw_content = msg.content

    # Normalize message content to a plain string
    if isinstance(raw_content, str):
        text = raw_content
    elif isinstance(raw_content, list):
        text = "".join(
            part.get("text", "") for part in raw_content if isinstance(part, dict)
        )
    else:
        text = str(raw_content or "")

    text = text.strip()

    # Strip ```json ``` fences if the model wrapped it
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.lower().startswith("json"):
                text = text.split("\n", 1)[1]

    return json.loads(text)


def ai_extract_courses_from_pdf_bytes(pdf_bytes: bytes) -> List[dict]:
    """
    High-level: convert PDF to images, send to GPT-4o, return a list of row dicts.
    """
    images_b64 = pdf_to_page_images_b64(pdf_bytes, max_pages=2)
    if not images_b64:
        return []

    data = _call_gpt4o_on_images(images_b64)

    program = data.get("program", "") or ""
    term = data.get("term", "") or ""
    year = data.get("year")
    student = data.get("student", "") or ""
    courses = data.get("courses", []) or []

    rows: List[dict] = []
    for c in courses:
        rows.append(
            {
                "Program": program,
                "Year": year,
                "Term": term,
                "City": None,
                "Country": None,
                "Course Number": c.get("course_number", "") or "",
                "Course Title": c.get("course_title", "") or "",
                "UR Course Equivalent": c.get("ur_course_equiv", "") or "",
                "Discipline": "",
                "Type of Credit": c.get("type_of_credit", "") or "",
                "US Credits": None,
                "Foreign Credits": None,
                "Link Course Search": "",
                "Link to Syllabus": "",
                "Students": student,
                "Comments": c.get("comments", "") or "",
                "_source": "ai",
            }
        )
    return rows
