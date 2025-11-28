# app/streamlit_app.py
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from caf_parser import parse_caf_pdf_hybrid


st.set_page_config(
    page_title="CAF → Excel Extractor",
    layout="wide",
)

st.title("📄 Course Approval Form → Excel Extractor")
st.markdown(
    """
Upload one or more Course Approval Forms (CAF) as PDFs and automatically extract
**approved** courses into an Excel-ready table.

- Only rows with **Major/Minor or Elective approvals** are kept.
- Program → City/Country is auto-filled using your **programs list**.
"""
)

# ===============================
# Helper: load default programs.csv
# ===============================

@st.cache_data
def load_default_program_directory() -> pd.DataFrame | None:
    """
    Tries to load data/programs.csv relative to the repo root.

    Assumes this file structure:
    repo_root/
      ├─ app/
      │   └─ streamlit_app.py
      └─ data/
          └─ programs.csv
    """
    try:
        # repo_root/app/streamlit_app.py -> repo_root
        repo_root = Path(__file__).resolve().parent.parent
        data_dir = repo_root / "data"
        programs_path = data_dir / "programs.csv"

        if not programs_path.exists():
            return None

        df = pd.read_csv(programs_path)
        return df
    except Exception:
        return None


# ===============================
# Sidebar: Program Directory Load
# ===============================

with st.sidebar:
    st.header("Configuration")

    # 1) Try loading default data/programs.csv
    program_dir_df: pd.DataFrame | None = load_default_program_directory()
    if program_dir_df is not None:
        st.success(
            f"Loaded default program directory from `data/programs.csv` "
            f"({len(program_dir_df)} rows)."
        )
    else:
        st.info(
            "No default `data/programs.csv` found. "
            "You can upload a program list file below."
        )

    # 2) Optional override via upload
    prog_dir_file = st.file_uploader(
        "Override program list (Excel/CSV from your system)",
        type=["csv", "xlsx"],
        key="prog_dir_uploader",
    )

    if prog_dir_file is not None:
        try:
            if prog_dir_file.name.lower().endswith(".xlsx"):
                program_dir_df = pd.read_excel(prog_dir_file)
            else:
                program_dir_df = pd.read_csv(prog_dir_file)

            st.success(
                f"Using uploaded program directory `{prog_dir_file.name}` "
                f"({len(program_dir_df)} rows)."
            )
        except Exception as e:
            st.error(f"Error reading uploaded program directory: {e}")
            program_dir_df = None

    if program_dir_df is None:
        st.warning(
            "⚠ Program directory not available. "
            "City/Country will remain empty unless Program is filled manually."
        )


# ===============================
# CAF Files Upload
# ===============================

st.subheader("Upload CAF PDFs")
uploaded_files = st.file_uploader(
    "Drag and drop one or more CAF PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    key="caf_uploader",
)


# ===============================
# Extract Button & Processing
# ===============================

if st.button("Extract Courses", disabled=not uploaded_files):
    if not uploaded_files:
        st.warning("Please upload at least one CAF PDF file.")
        st.stop()

    all_rows: list[pd.DataFrame] = []

    for file in uploaded_files:
        pdf_bytes = file.read()

        try:
            df = parse_caf_pdf_hybrid(pdf_bytes, program_directory=program_dir_df)
        except Exception as e:
            st.error(f"Error parsing {file.name}: {e}")
            continue

        if df.empty:
            st.warning(f"No approved courses extracted from: {file.name}")
            continue

        df["Source File"] = file.name
        all_rows.append(df)

    if not all_rows:
        st.error("No approved courses extracted from any file.")
        st.stop()

    result_df = pd.concat(all_rows, ignore_index=True)

    # Editable table
    st.subheader("Extracted Courses (editable)")
    st.data_editor(
        result_df,
        width="stretch",   # replaces deprecated use_container_width
        height=600,
        key="courses_editor",
    )

    # Download
    @st.cache_data
    def to_excel_bytes(df: pd.DataFrame) -> bytes:
        import io
        from pandas import ExcelWriter

        output = io.BytesIO()
        with ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        return output.getvalue()

    excel_data = to_excel_bytes(result_df)

    st.download_button(
        "⬇️ Download Excel",
        data=excel_data,
        file_name="CAF_Extracted_Courses.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
