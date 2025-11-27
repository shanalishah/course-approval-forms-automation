# app/streamlit_app.py

import io
import pandas as pd
import streamlit as st

from caf_parser import parse_caf_pdf


st.set_page_config(
    page_title="Course Approval Extractor",
    layout="wide",
)

st.title("Course Approval Form → Harrison is gay Extractor")

st.markdown(
    """
Upload one or more Course Approval Form (CAF) PDFs and this tool will extract
course information into a structured table. You can review/edit the data and
download it as Excel.
"""
)

# Optional: load program directory
program_dir_df = None
# with st.sidebar:
#     st.header("Configuration")

#     prog_dir_file = st.file_uploader(
#         "Program directory CSV (optional)",
#         type=["csv"],
#         key="prog_dir_uploader",
#     )
#     if prog_dir_file is not None:
#         program_dir_df = pd.read_csv(prog_dir_file)
#         st.success(f"Loaded program directory with {len(program_dir_df)} rows.")

with st.sidebar:
    st.header("Configuration")

    prog_dir_file = st.file_uploader(
        "Program list (Excel or CSV from your system)",
        type=["csv", "xlsx"],
        key="prog_dir_uploader",
    )

    program_dir_df = None
    if prog_dir_file is not None:
        # Read either CSV or Excel
        if prog_dir_file.name.lower().endswith(".xlsx"):
            raw_prog = pd.read_excel(prog_dir_file)
        else:
            raw_prog = pd.read_csv(prog_dir_file)

        # Standardize column names
        raw_prog.columns = [c.strip() for c in raw_prog.columns]

        if "Program Name" not in raw_prog.columns:
            st.error("Program file must have a 'Program Name' column.")
        else:
            # Create the 'Program' column the parser expects
            raw_prog["Program"] = (
                raw_prog["Program Name"]
                .astype(str)
                .str.replace("Star icon", "", regex=False)
                .str.strip()
            )

            program_dir_df = raw_prog
            st.success(f"Loaded program directory with {len(program_dir_df)} rows.")

    st.markdown("---")
    st.caption(
        "Note: This MVP works best with typed/fillable PDFs. "
        "We can add OCR/handwriting support later."
    )

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
        all_rows = []
        for f in uploaded_files:
            # We pass a BytesIO so pdfplumber can re-read the stream as needed
            bytes_io = io.BytesIO(f.read())
            try:
                df_one = parse_caf_pdf(bytes_io, program_directory=program_dir_df)
                if not df_one.empty:
                    df_one["Source File"] = f.name
                    all_rows.append(df_one)
                else:
                    st.warning(f"No courses parsed from file: {f.name}")
            except Exception as e:
                st.error(f"Error parsing {f.name}: {e}")

        if not all_rows:
            st.error("No courses extracted from any file.")
        else:
            df_all = pd.concat(all_rows, ignore_index=True)

            st.success(f"Extracted {len(df_all)} course rows from {len(uploaded_files)} file(s).")

            st.subheader("Extracted Courses (editable)")
            edited_df = st.data_editor(
                df_all,
                num_rows="dynamic",
                use_container_width=True,
                height=500,
                key="courses_editor",
            )

            # Download as Excel
            out_buf = io.BytesIO()
            with pd.ExcelWriter(out_buf, engine="openpyxl") as writer:
                edited_df.to_excel(writer, index=False, sheet_name="Courses")
            out_buf.seek(0)

            st.download_button(
                label="Download as Excel",
                data=out_buf,
                file_name="caf_courses_extracted.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
