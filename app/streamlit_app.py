# app/streamlit_app.py
from __future__ import annotations

from pathlib import Path
import io

import pandas as pd
import streamlit as st

from caf_parser import parse_caf_pdf_hybrid


# ----------------------------------------------------
# Paths / constants
# ----------------------------------------------------
APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR.parent / "data"
DEFAULT_PROGRAMS_PATH = DATA_DIR / "programs.csv"


# ----------------------------------------------------
# Helpers
# ----------------------------------------------------
@st.cache_data
def load_default_program_directory() -> pd.DataFrame:
    """Load built-in programs.csv if it exists."""
    if DEFAULT_PROGRAMS_PATH.exists():
        try:
            return pd.read_csv(DEFAULT_PROGRAMS_PATH)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def load_program_directory_from_upload(upload) -> pd.DataFrame:
    if upload is None:
        return pd.DataFrame()

    try:
        if upload.name.lower().endswith(".xlsx"):
            return pd.read_excel(upload)
        else:
            return pd.read_csv(upload)
    except Exception:
        return pd.DataFrame()


# ----------------------------------------------------
# UI
# ----------------------------------------------------
st.set_page_config(
    page_title="Course Approval Form → Excel Extractor",
    layout="wide",
)

st.title("Course Approval Form → Excel Extractor")

# ----- Sidebar: configuration -----
st.sidebar.header("Configuration")
st.sidebar.markdown(
    "Program list (Excel or CSV from your system). "
    "If you don’t upload anything, the app will use `data/programs.csv` "
    "from the repo."
)

programs_upload = st.sidebar.file_uploader(
    "Program list file",
    type=["csv", "xlsx"],
    accept_multiple_files=False,
)

# Decide which program directory to use
uploaded_program_df = load_program_directory_from_upload(programs_upload)
if not uploaded_program_df.empty:
    program_directory = uploaded_program_df
else:
    program_directory = load_default_program_directory()

# ----- Main: CAF upload -----
st.subheader("Upload CAF PDFs")

uploaded_pdfs = st.file_uploader(
    "Drag and drop one or more CAF PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

extract_btn = st.button("Extract Courses")

if extract_btn:
    if not uploaded_pdfs:
        st.warning("Please upload at least one CAF PDF.")
    else:
        all_dfs = []
        for f in uploaded_pdfs:
            pdf_bytes = f.read()
            try:
                df = parse_caf_pdf_hybrid(
                    pdf_bytes,
                    program_directory if not program_directory.empty else None,
                )
                if not df.empty:
                    df.insert(0, "Source File", f.name)
                all_dfs.append(df)
            except Exception as e:
                st.error(f"Error parsing {f.name}: {e}")

        if not all_dfs:
            st.warning("No courses extracted from any file.")
        else:
            result_df = pd.concat(all_dfs, ignore_index=True)

            st.subheader("Extracted Courses (editable)")
            edited_df = st.data_editor(
                result_df,
                num_rows="dynamic",
                use_container_width=True,
            )

            # Download
            out_buffer = io.BytesIO()
            edited_df.to_excel(out_buffer, index=False, engine="openpyxl")
            out_buffer.seek(0)

            st.download_button(
                "Download as Excel",
                data=out_buffer,
                file_name="extracted_courses.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
