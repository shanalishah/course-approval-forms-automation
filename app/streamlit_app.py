# app/streamlit_app.py
import io

import pandas as pd
import streamlit as st

from caf_parser import parse_caf_pdf_hybrid


st.set_page_config(
    page_title="Course Approval Form → Excel Extractor",
    layout="wide",
)

st.title("Course Approval Form → Excel Extractor")

st.markdown(
    """
Upload one or more Course Approval Form (CAF) PDFs and this tool will extract
course information into a structured table. It uses a **hybrid approach**:

- 🧮 Rule-based parsing for typed/fillable PDFs (fast & free)
- 🤖 GPT-4o vision fallback for scanned / messy forms

You can review/edit the extracted table and download it as Excel.
"""
)


# ---------- Helpers ----------

def load_program_directory(uploaded_file) -> pd.DataFrame | None:
    """
    Load your big program list (Program Name, City, Country, etc.)
    from CSV or Excel and standardize into a DataFrame.
    """
    if uploaded_file is None:
        return None

    filename = uploaded_file.name.lower()
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        df = pd.read_excel(uploaded_file)
    else:
        df = pd.read_csv(uploaded_file)

    df.columns = [c.strip() for c in df.columns]

    # we keep original structure; parser will look for Program / Program Name later
    if "Program Name" not in df.columns and "Program" not in df.columns:
        st.warning("Program list does not have 'Program Name' or 'Program' column – enrichment will be skipped.")
    return df


def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Courses")
    buf.seek(0)
    return buf.getvalue()


# ---------- Sidebar: Program directory ----------

with st.sidebar:
    st.header("Configuration")

    prog_dir_file = st.file_uploader(
        "Program list (Excel or CSV from your system)",
        type=["csv", "xlsx"],
        key="prog_dir_uploader",
    )

    program_dir_df = load_program_directory(prog_dir_file)
    if program_dir_df is not None:
        st.success(f"Loaded program directory with {len(program_dir_df)} rows.")

    st.markdown("---")
    st.caption(
        "Note: Hybrid mode – rule-based first, then GPT-4o if needed. "
        "Handwritten / scanned PDFs may consume OpenAI credits."
    )


# ---------- Main: CAF PDFs upload ----------

uploaded_files = st.file_uploader(
    "Upload CAF PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    key="caf_uploader",
)

if uploaded_files:
    st.info(f"{len(uploaded_files)} file(s) uploaded. Click **Extract Courses** to process.")
else:
    st.warning("Upload at least one CAF PDF to begin.")


if st.button("Extract Courses"):
    if not uploaded_files:
        st.error("Please upload at least one PDF.")
    else:
        all_dfs: list[pd.DataFrame] = []

        for f in uploaded_files:
            pdf_bytes = f.getvalue()
            st.write(f"Processing **{f.name}** ...")

            try:
                df_one = parse_caf_pdf_hybrid(pdf_bytes, program_directory=program_dir_df)
            except Exception as e:
                st.error(f"Error parsing {f.name}: {e}")
                continue

            if df_one is None or df_one.empty:
                st.warning(f"No courses extracted from file: {f.name}")
                continue

            df_one["Source File"] = f.name
            all_dfs.append(df_one)

        if not all_dfs:
            st.error("No courses extracted from any file.")
        else:
            df_all = pd.concat(all_dfs, ignore_index=True)

            st.success(
                f"Extracted **{len(df_all)} course rows** from "
                f"**{len(uploaded_files)} file(s)**."
            )

            st.subheader("Extracted Courses (editable)")

            edited_df = st.data_editor(
                df_all,
                use_container_width=True,
                num_rows="dynamic",
                height=550,
                key="courses_editor",
            )

            st.caption(
                "Column `_source` shows whether a row came from the rule-based parser (`rule`) "
                "or GPT-4o (`ai`)."
            )

            excel_bytes = df_to_excel_bytes(edited_df)

            st.download_button(
                label="Download as Excel",
                data=excel_bytes,
                file_name="caf_courses_extracted.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
