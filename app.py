import streamlit as st
from pypdf import PdfReader, PdfWriter
from pathlib import Path
import io
import zipfile
from datetime import datetime, time
import pytz

# ────────────────────────────────────────────────
# PAGE CONFIG & FIXED FONT STYLING (Helvetica/Arial)
# ────────────────────────────────────────────────
st.set_page_config(page_title="PDF Metadata Editor", page_icon="🧹", layout="wide")

# Injecting CSS to fix font to Helvetica/Arial globally
st.markdown("""
    <style>
    html, body, [class*="css"], .stText, .stMarkdown, p, h1, h2, h3, h4, span, label {
        font-family: "Helvetica", "Arial", sans-serif !important;
    }
    /* Specifically targeting input boxes and buttons */
    .stButton>button, .stTextInput>div>div>input, .stDateInput>div>div>input, .stNumberInput>div>div>input {
        font-family: "Helvetica", "Arial", sans-serif !important;
    }
    /* Info box styling for font mapping */
    .font-info {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
        border-left: 3px solid #ff4b4b;
        margin-bottom: 10px;
        font-size: 0.9em;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🧹 PDF Metadata Editor..(Kl_Rk)")
st.markdown("Upload PDFs → edit or clear metadata → download modified files")

# Default Application (Creator) and Producer values
DEFAULT_CREATOR = "JasperReports Library version 6.20.5-3efcf2e67f959db3888d79f73dde2dbd7acb4f8e"
DEFAULT_PRODUCER = "OpenPDF 1.3.30"

# Font Mapping Reference
FONT_MAPPING = {
    "Helvetica": {
        "type": "Type 1",
        "encoding": "Ansi",
        "actual_font": "ArialMT",
        "actual_font_type": "TrueType"
    },
    "Helvetica-Bold": {
        "type": "Type 1", 
        "encoding": "Ansi",
        "actual_font": "Arial-BoldMT",
        "actual_font_type": "TrueType"
    },
    "Helvetica-BoldOblique": {
        "type": "Type 1",
        "encoding": "Ansi", 
        "actual_font": "Arial-BoldItalicMT",
        "actual_font_type": "TrueType"
    }
}

# ────────────────────────────────────────────────
# Session state
# ────────────────────────────────────────────────
if "metadata_values" not in st.session_state:
    st.session_state.metadata_values = {
        "title": "", "author": "", "subject": "", "keywords": "",
        "creator": DEFAULT_CREATOR,  # Application that created the PDF
        "producer": DEFAULT_PRODUCER,  # PDF Producer
        "creation_date": None, 
        "creation_time": None,
        "creation_seconds": 0,
        "mod_date": None, 
        "mod_time": None,
        "mod_seconds": 0,
    }

if "last_first_filename" not in st.session_state:
    st.session_state.last_first_filename = None

if "run_action" not in st.session_state:
    st.session_state.run_action = None

if "processed_results" not in st.session_state:
    st.session_state.processed_results = None

if "processed_errors" not in st.session_state:
    st.session_state.processed_errors = []

if "last_action" not in st.session_state:
    st.session_state.last_action = None

# ────────────────────────────────────────────────
# Helper functions
# ────────────────────────────────────────────────
def pdf_date_to_datetime(pdf_date_str):
    if not pdf_date_str or not isinstance(pdf_date_str, str) or not pdf_date_str.startswith("D:"):
        return None
    try:
        clean = pdf_date_str[2:].replace("'", "").replace("Z", "").split("+")[0].split("-")[0]
        if len(clean) >= 14:
            dt = datetime.strptime(clean[:14], "%Y%m%d%H%M%S")
            return dt.replace(tzinfo=pytz.UTC)
        elif len(clean) >= 8:
            dt = datetime.strptime(clean[:8], "%Y%m%d")
            return dt.replace(tzinfo=pytz.UTC)
    except:
        return None

def format_pdf_date(dt):
    if dt is None:
        return ""
    utc = dt.astimezone(pytz.UTC)
    return f"D:{utc.strftime('%Y%m%d%H%M%S')}Z"

def extract_metadata_dict(pdf_bytes, filename):
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        info = reader.metadata or {}
        c_dt = pdf_date_to_datetime(info.get("/CreationDate", ""))
        m_dt = pdf_date_to_datetime(info.get("/ModDate", ""))
        
        # Get values from PDF, if empty use defaults
        pdf_creator = info.get("/Creator", "")
        if not pdf_creator:
            pdf_creator = DEFAULT_CREATOR
            
        pdf_producer = info.get("/Producer", "")
        if not pdf_producer:
            pdf_producer = DEFAULT_PRODUCER
            
        return {
            "title": info.get("/Title", ""),
            "author": info.get("/Author", ""),
            "subject": info.get("/Subject", ""),
            "keywords": info.get("/Keywords", ""),
            "creator": pdf_creator,
            "producer": pdf_producer,
            "creation_date": c_dt.date() if c_dt else None,
            "creation_time": c_dt.time() if c_dt else None,
            "creation_seconds": c_dt.second if c_dt else 0,
            "mod_date": m_dt.date() if m_dt else None,
            "mod_time": m_dt.time() if m_dt else None,
            "mod_seconds": m_dt.second if m_dt else 0,
        }
    except Exception as e:
        st.warning(f"Could not read metadata from {filename}: {e}")
        return {
            "title": "", "author": "", "subject": "", "keywords": "",
            "creator": DEFAULT_CREATOR,
            "producer": DEFAULT_PRODUCER,
            "creation_date": None, "creation_time": None, "creation_seconds": 0,
            "mod_date": None, "mod_time": None, "mod_seconds": 0,
        }

# ────────────────────────────────────────────────
# File uploader
# ────────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "Upload PDF file(s)",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:
    first = uploaded_files[0]
    if first.name != st.session_state.last_first_filename:
        meta = extract_metadata_dict(first.getvalue(), first.name)
        st.session_state.metadata_values.update(meta)
        st.session_state.last_first_filename = first.name

if not uploaded_files:
    st.info("Please upload one or more PDF files.")
    st.stop()

# ────────────────────────────────────────────────
# Font Mapping Reference Display
# ────────────────────────────────────────────────
with st.expander("📋 Font Mapping Reference (Helvetica → ArialMT)", expanded=False):
    st.markdown("""
    **Font Substitution Mapping:**
    """)
    
    col_f1, col_f2, col_f3 = st.columns(3)
    
    with col_f1:
        st.markdown("""
        **Helvetica**
        - Type: Type 1
        - Encoding: Ansi
        - Actual Font: ArialMT
        - Actual Font Type: TrueType
        """)
    
    with col_f2:
        st.markdown("""
        **Helvetica-Bold**
        - Type: Type 1
        - Encoding: Ansi
        - Actual Font: Arial-BoldMT
        - Actual Font Type: TrueType
        """)
    
    with col_f3:
        st.markdown("""
        **Helvetica-BoldOblique**
        - Type: Type 1
        - Encoding: Ansi
        - Actual Font: Arial-BoldItalicMT
        - Actual Font Type: TrueType
        """)

# ────────────────────────────────────────────────
# Editor UI
# ────────────────────────────────────────────────
st.subheader("Edit Metadata")

# Display font mapping info above fields
st.markdown("""
<div class="font-info">
<strong>Font Configuration:</strong> Fields use Helvetica family names with ArialMT TrueType substitution<br>
<small>Helvetica → ArialMT | Helvetica-Bold → Arial-BoldMT | Helvetica-BoldOblique → Arial-BoldItalicMT</small>
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    title = st.text_input(
        "Title (Helvetica → ArialMT)", 
        st.session_state.metadata_values["title"],
        help="Base font: Helvetica (Type 1) → Actual: ArialMT (TrueType)"
    )
    author = st.text_input(
        "Author (Helvetica-Bold → Arial-BoldMT)",   
        st.session_state.metadata_values["author"],
        help="Base font: Helvetica-Bold (Type 1) → Actual: Arial-BoldMT (TrueType)"
    )
    subject = st.text_input(
        "Subject",  
        st.session_state.metadata_values["subject"],
        help="Standard text field"
    )
    keywords = st.text_input(
        "Keywords", 
        st.session_state.metadata_values["keywords"],
        help="Comma-separated keywords"
    )

with col2:
    creator = st.text_input(
        "Creator (Application)",  
        st.session_state.metadata_values["creator"],
        help="Application that created the PDF"
    )
    producer = st.text_input(
        "Producer", 
        st.session_state.metadata_values["producer"],
        help="PDF producer library/engine"
    )

st.markdown("### Dates (with Seconds)")
cold1, cold2 = st.columns(2)

with cold1:
    c_date = st.date_input("Creation Date", st.session_state.metadata_values["creation_date"])
    c_time_cols = st.columns([3, 1])
    with c_time_cols[0]:
        c_time_val = st.session_state.metadata_values["creation_time"]
        if c_time_val is None:
            c_time_val = time(0, 0, 0)
        c_time = st.time_input("Creation Time (HH:MM)", c_time_val)
    with c_time_cols[1]:
        c_seconds = st.number_input("Sec", min_value=0, max_value=59, 
                                    value=st.session_state.metadata_values["creation_seconds"],
                                    key="c_sec")

with cold2:
    m_date = st.date_input("Modification Date", st.session_state.metadata_values["mod_date"])
    m_time_cols = st.columns([3, 1])
    with m_time_cols[0]:
        m_time_val = st.session_state.metadata_values["mod_time"]
        if m_time_val is None:
            m_time_val = time(0, 0, 0)
        m_time = st.time_input("Modification Time (HH:MM)", m_time_val)
    with m_time_cols[1]:
        m_seconds = st.number_input("Sec", min_value=0, max_value=59, 
                                    value=st.session_state.metadata_values["mod_seconds"],
                                    key="m_sec")

colb1, colb2 = st.columns(2)
with colb1:
    if st.button("💾 Apply Changes", type="primary", use_container_width=True):
        st.session_state.run_action = "apply"
with colb2:
    if st.button("🗑️ Clear All Metadata", use_container_width=True):
        st.session_state.run_action = "clear"

# ────────────────────────────────────────────────
# Processing
# ────────────────────────────────────────────────
if st.session_state.run_action in ("apply", "clear"):
    action = st.session_state.run_action
    st.session_state.run_action = None

    results = []
    errors = []

    progress = st.progress(0)
    status = st.empty()
    total = len(uploaded_files)

    for i, file in enumerate(uploaded_files, 1):
        status.text(f"Processing {i}/{total}: {file.name}")
        try:
            reader = PdfReader(io.BytesIO(file.getvalue()))
            writer = PdfWriter()
            
            writer.append_pages_from_reader(reader)

            if action == "clear":
                writer.add_metadata({})
            else:
                meta = {}
                if title.strip():    meta["/Title"]    = title.strip()
                if author.strip():   meta["/Author"]   = author.strip()
                if subject.strip():  meta["/Subject"]  = subject.strip()
                if keywords.strip(): meta["/Keywords"] = keywords.strip()
                if creator.strip():  meta["/Creator"]  = creator.strip()
                if producer.strip(): meta["/Producer"] = producer.strip()

                # Combine date, time, and seconds for Creation Date
                if c_date:
                    ct = time(c_time.hour, c_time.minute, c_seconds)
                    cdt = datetime.combine(c_date, ct)
                    meta["/CreationDate"] = format_pdf_date(cdt)

                # Combine date, time, and seconds for Modification Date
                if m_date:
                    mt = time(m_time.hour, m_time.minute, m_seconds)
                    mdt = datetime.combine(m_date, mt)
                    meta["/ModDate"] = format_pdf_date(mdt)

                writer.add_metadata(meta)

            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)

            prefix = "[CLEARED]" if action == "clear" else "[EDITED]"
            new_name = f"{prefix} {Path(file.name).stem}.pdf"
            results.append((new_name, buf.getvalue()))

        except Exception as e:
            errors.append(f"{file.name}: {str(e)}")

        progress.progress(i / total)

    progress.empty()
    status.empty()

    st.session_state.processed_results = results
    st.session_state.processed_errors = errors
    st.session_state.last_action = action

    st.success(f"Processed {len(results)} file(s) successfully")
    if errors:
        st.error("Some files failed:\n" + "\n".join(errors))

# ────────────────────────────────────────────────
# Persistent Download Section
# ────────────────────────────────────────────────
if st.session_state.processed_results and len(st.session_state.processed_results) > 0:
    st.markdown("### Download Results")
    
    results = st.session_state.processed_results
    
    if len(results) == 1:
        name, data = results[0]
        st.download_button(
            label=f"⬇️ Download edited file: {name}",
            data=data,
            file_name=name,
            mime="application/pdf",
            use_container_width=True
        )
    else:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in results:
                zf.writestr(name, data)
        zip_buffer.seek(0)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        label = "cleared" if st.session_state.last_action == "clear" else "edited"
        zip_name = f"pdfs_{label}_{ts}.zip"

        st.download_button(
            label=f"⬇️ Download ZIP ({len(results)} files)",
            data=zip_buffer,
            file_name=zip_name,
            mime="application/zip",
            use_container_width=True
        )

    if st.button("Clear previous results (start fresh)"):
        st.session_state.processed_results = None
        st.session_state.processed_errors = []
        st.session_state.last_action = None
        st.rerun()

st.caption("Helvetica/Arial UI Fixed • Streamlit + pypdf • Seconds precision enabled • Font mapping: Helvetica → ArialMT")
