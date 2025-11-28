# app/streamlit_app.py
import pandas as pd
import streamlit as st

from caf_parser import parse_caf_pdf_hybrid


st.set_page_config(
    page_title="CAF → Excel Extractor",
    layout="wide",
)


st.title("📄 Course Approval Form → Excel Extractor")
st.markdown(
    "Upload Course Approval Forms (CAF) and automatically extract approved courses into a structured excel-ready table."
)

# ===============================
# Sidebar: Program Directory Load
# ===============================

with st.sidebar:
    st.header("Configuration")

    prog_dir_file = st.file_uploader(
        "Program list (Excel/CSV from your system)",
        type=["csv", "xlsx"],
        key="prog_dir_uploader",
    )

    program_dir_df = None
    if prog_dir_file is not None:
        if prog_dir_file.name.lower().endswith(".xlsx"):
            raw_prog = pd.read_excel(prog_dir_file)
        else:
            raw_prog = pd.read_csv(prog_dir_file)

        raw_prog.columns = [c.strip() for c in raw_prog.columns]

        if "Program Name" in raw_prog.columns:
            raw_prog["Program"] = (
                raw_prog["Program Name"]
                .astype(str)
                .str.replace("Star icon", "", regex=False)
                .str.strip()
            )
        elif "Program" not in raw_prog.columns:
            st.error("Program file must have either 'Program Name' or 'Program'.")
        else:
            raw_prog["Program"] = raw_prog["Program"].astype(str).str.strip()

        program_dir_df = raw_prog
        st.success(f"Loaded program directory with {len(program_dir_df)} rows.")

# ===============================
# CAF Files Upload
# ===============================

st.subheader("Upload CAF PDFs")
uploaded_files = st.file_uploader(
    "Drag and drop one or more CAF PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

# ===============================
# Extract Button & Processing
# ===============================

if st.button("Extract Courses", disabled=not uploaded_files):
    if not uploaded_files:
        st.warning("Please upload at least one CAF PDF file.")
        st.stop()

    all_rows = []

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
        width="stretch",
        height=600,
        key="courses_editor",
    )

    # Download
    @st.cache_data
    def to_excel_bytes(df):
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
