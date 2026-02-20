#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Streamlit PDF‑Metadata editor (fixed date‑time handling).

Features
--------
* Upload one or many PDFs.
* View / edit title, author, subject, keywords, creator, producer.
* Edit creation‑/modification‑date **with seconds**.
* Keep each file’s original timestamps or overwrite all of them (checkbox).
* “Clear all metadata” removes every entry, including the auto‑added dates.
* Download a single edited PDF or a ZIP containing all results.
* Fixed Helvetica ► ArialMT font‑mapping info (purely UI‑side).
"""

import io
import zipfile
from datetime import datetime, time
from pathlib import Path

import streamlit as st
import pytz
from pypdf import PdfReader, PdfWriter

# ----------------------------------------------------------------------
# Detect the system’s local timezone (fallback → UTC)
# ----------------------------------------------------------------------
try:
    import tzlocal  # pip install tzlocal
    LOCAL_TZ = tzlocal.get_localzone()
except Exception:  # pragma: no cover
    LOCAL_TZ = pytz.UTC

# ----------------------------------------------------------------------
# Page configuration & global CSS (Helvetica / Arial)
# ----------------------------------------------------------------------
st.set_page_config(page_title="PDF Metadata Editor", page_icon="🧹", layout="wide")
st.markdown(
    """
    <style>
    html, body, [class*="css"], .stText, .stMarkdown, p, h1, h2, h3, h4,
    span, label {
        font-family: "Helvetica", "Arial", sans-serif !important;
    }
    .stButton>button,
    .stTextInput>div>div>input,
    .stDateInput>div>div>input,
    .stTimeInput>div>div>input,
    .stNumberInput>div>div>input {
        font-family: "Helvetica", "Arial", sans-serif !important;
    }
    .font-info {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
        border-left: 3px solid #ff4b4b;
        margin-bottom: 10px;
        font-size: 0.9em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧹 PDF Metadata Editor (Kl_Rk)")
st.markdown("Upload PDFs → edit or clear metadata → download modified files")

# ----------------------------------------------------------------------
# Default Creator / Producer strings (used when a PDF has none)
# ----------------------------------------------------------------------
DEFAULT_CREATOR = (
    "JasperReports Library version 6.20.5-3efcf2e67f959db3888d79f73dde2dbd7acb4f8e"
)
DEFAULT_PRODUCER = "OpenPDF 1.3.30"

# ----------------------------------------------------------------------
# Font‑mapping reference (purely informational)
# ----------------------------------------------------------------------
FONT_MAPPING = {
    "Helvetica": {
        "type": "Type 1",
        "encoding": "Ansi",
        "actual_font": "ArialMT",
        "actual_font_type": "TrueType",
    },
    "Helvetica-Bold": {
        "type": "Type 1",
        "encoding": "Ansi",
        "actual_font": "Arial-BoldMT",
        "actual_font_type": "TrueType",
    },
    "Helvetica-BoldOblique": {
        "type": "Type 1",
        "encoding": "Ansi",
        "actual_font": "Arial-BoldItalicMT",
        "actual_font_type": "TrueType",
    },
}

# ----------------------------------------------------------------------
# Session‑state defaults
# ----------------------------------------------------------------------
if "metadata_values" not in st.session_state:
    st.session_state.metadata_values = {
        "title": "",
        "author": "",
        "subject": "",
        "keywords": "",
        "creator": DEFAULT_CREATOR,
        "producer": DEFAULT_PRODUCER,
        "creation_date": None,
        "creation_time": time(0, 0, 0),
        "creation_seconds": 0,
        "mod_date": None,
        "mod_time": time(0, 0, 0),
        "mod_seconds": 0,
    }

if "last_first_filename" not in st.session_state:
    st.session_state.last_first_filename = None

if "run_action" not in st.session_state:
    st.session_state.run_action = None  # "apply" | "clear"

if "processed_results" not in st.session_state:
    st.session_state.processed_results = None

if "processed_errors" not in st.session_state:
    st.session_state.processed_errors = []

if "last_action" not in st.session_state:
    st.session_state.last_action = None

# ----------------------------------------------------------------------
# Helper functions – robust PDF date ↔ datetime conversion
# ----------------------------------------------------------------------
def pdf_date_to_datetime(pdf_date_str: str):
    """
    Convert a PDF “D:” string to a timezone‑aware ``datetime``.
    Handles the full ``+hh'mm'`` / ``-hh'mm'`` / ``Z`` offset forms.
    Returns ``None`` on malformed input.
    """
    if not isinstance(pdf_date_str, str) or not pdf_date_str.startswith("D:"):
        return None

    raw = pdf_date_str[2:]           # strip the leading “D:”

    # ------------------------------------------------------------------
    # 1️⃣ Extract the basic `YYYYMMDDHHmmSS` part (pad with zeros)
    # ------------------------------------------------------------------
    base_len = min(14, len(raw))
    base_part = raw[:base_len]
    padded = base_part.ljust(14, "0")
    try:
        dt_naive = datetime.strptime(padded, "%Y%m%d%H%M%S")
    except ValueError:
        return None

    # ------------------------------------------------------------------
    # 2️⃣ Parse the optional timezone designator
    # ------------------------------------------------------------------
    tz_part = raw[base_len:]   # could be "", "Z", "+05'30'", "-04'00'", etc.
    if not tz_part or tz_part == "Z":
        tz = pytz.UTC
    else:
        sign = tz_part[0]
        # keep only the digits
        hrs = "".join(filter(str.isdigit, tz_part[1:3]))
        mins = "".join(filter(str.isdigit, tz_part[4:6])) if len(tz_part) >= 6 else "00"
        try:
            offset_min = int(hrs) * 60 + int(mins)
        except ValueError:
            offset_min = 0
        if sign == "-":
            offset_min = -offset_min
        tz = pytz.FixedOffset(offset_min)

    return dt_naive.replace(tzinfo=tz)


def format_pdf_date(dt: datetime, tz: pytz.timezone = None) -> str:
    """
    Turn a ``datetime`` (naïve or aware) into a PDF “D:” string.
    • If *dt* is naïve we assume it is in *tz* (defaults to the system
      local timezone).  
    • The resulting string contains the proper offset, e.g.
      ``D:20240910123000+05'30'`` or ``…Z`` when the offset is zero.
    """
    if dt is None:
        return ""

    # ------------------------------------------------------------------
    # Localise naïve datetimes
    # ------------------------------------------------------------------
    if dt.tzinfo is None:
        tz = tz or LOCAL_TZ
        dt = tz.localize(dt)

    # ------------------------------------------------------------------
    # Compute offset part
    # ------------------------------------------------------------------
    offset = dt.utcoffset()
    if offset is None or offset.total_seconds() == 0:
        # UTC – the compact “Z” designator
        return f"D:{dt.strftime('%Y%m%d%H%M%S')}Z"

    total_min = int(offset.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    total_min = abs(total_min)
    hrs, mins = divmod(total_min, 60)
    return f"D:{dt.strftime('%Y%m%d%H%M%S')}{sign}{hrs:02d}'{mins:02d}'"


def extract_metadata_dict(pdf_bytes: bytes, filename: str):
    """
    Return a dict with the most common Info‑dictionary entries.
    Dates are returned as *aware* ``datetime`` objects (or ``None``).
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        info = reader.metadata or {}

        c_dt = pdf_date_to_datetime(info.get("/CreationDate", ""))
        m_dt = pdf_date_to_datetime(info.get("/ModDate", ""))

        # Use defaults when the PDF lacks these fields
        creator = info.get("/Creator", DEFAULT_CREATOR) or DEFAULT_CREATOR
        producer = info.get("/Producer", DEFAULT_PRODUCER) or DEFAULT_PRODUCER

        return {
            "title": info.get("/Title", ""),
            "author": info.get("/Author", ""),
            "subject": info.get("/Subject", ""),
            "keywords": info.get("/Keywords", ""),
            "creator": creator,
            "producer": producer,
            "creation_dt": c_dt,
            "mod_dt": m_dt,
        }
    except Exception as exc:
        st.warning(f"Could not read metadata from {filename}: {exc}")
        return {
            "title": "",
            "author": "",
            "subject": "",
            "keywords": "",
            "creator": DEFAULT_CREATOR,
            "producer": DEFAULT_PRODUCER,
            "creation_dt": None,
            "mod_dt": None,
        }


def _populate_session_from_extracted(extracted: dict):
    """Write the values from ``extract_metadata_dict`` into ``st.session_state``."""
    meta = st.session_state.metadata_values
    meta["title"] = extracted.get("title", "")
    meta["author"] = extracted.get("author", "")
    meta["subject"] = extracted.get("subject", "")
    meta["keywords"] = extracted.get("keywords", "")
    meta["creator"] = extracted.get("creator", DEFAULT_CREATOR)
    meta["producer"] = extracted.get("producer", DEFAULT_PRODUCER)

    c_dt = extracted.get("creation_dt")
    if c_dt:
        meta["creation_date"] = c_dt.date()
        meta["creation_time"] = c_dt.time()
        meta["creation_seconds"] = c_dt.second
    else:
        meta["creation_date"] = None
        meta["creation_time"] = time(0, 0, 0)
        meta["creation_seconds"] = 0

    m_dt = extracted.get("mod_dt")
    if m_dt:
        meta["mod_date"] = m_dt.date()
        meta["mod_time"] = m_dt.time()
        meta["mod_seconds"] = m_dt.second
    else:
        meta["mod_date"] = None
        meta["mod_time"] = time(0, 0, 0)
        meta["mod_seconds"] = 0


# ----------------------------------------------------------------------
# File uploader
# ----------------------------------------------------------------------
uploaded_files = st.file_uploader(
    "Upload PDF file(s)", type=["pdf"], accept_multiple_files=True
)

if uploaded_files:
    # On a *new* batch we initialise the UI fields with the metadata from
    # the *first* uploaded PDF (just for convenience).
    first = uploaded_files[0]
    if first.name != st.session_state.last_first_filename:
        extracted = extract_metadata_dict(first.getvalue(), first.name)
        _populate_session_from_extracted(extracted)
        st.session_state.last_first_filename = first.name
else:
    st.info("Please upload one or more PDF files.")
    st.stop()

# ----------------------------------------------------------------------
# Font‑mapping reference (expander)
# ----------------------------------------------------------------------
with st.expander("📋 Font Mapping Reference (Helvetica → ArialMT)", expanded=False):
    st.markdown("**Font Substitution Mapping:**")
    col_f1, col_f2, col_f3 = st.columns(3)

    with col_f1:
        st.markdown(
            """
            **Helvetica**  
            - Type: Type 1  
            - Encoding: Ansi  
            - Actual Font: ArialMT  
            - Actual Font Type: TrueType
            """
        )
    with col_f2:
        st.markdown(
            """
            **Helvetica‑Bold**  
            - Type: Type 1  
            - Encoding: Ansi  
            - Actual Font: Arial‑BoldMT  
            - Actual Font Type: TrueType
            """
        )
    with col_f3:
        st.markdown(
            """
            **Helvetica‑BoldOblique**  
            - Type: Type 1  
            - Encoding: Ansi  
            - Actual Font: Arial‑BoldItalicMT  
            - Actual Font Type: TrueType
            """
        )

# ----------------------------------------------------------------------
# Editor UI
# ----------------------------------------------------------------------
st.subheader("Edit Metadata")

# ----- short font‑info banner ---------------------------------------
st.markdown(
    """
    <div class="font-info">
    <strong>Font Configuration:</strong> UI fields use Helvetica family names,
    but the PDF will embed the equivalent ArialMT TrueType fonts.
    </div>
    """,
    unsafe_allow_html=True,
)

col1, col2 = st.columns(2)
with col1:
    title = st.text_input(
        "Title (Helvetica → ArialMT)",
        st.session_state.metadata_values["title"],
        help="Base font: Helvetica (Type 1) → Actual: ArialMT (TrueType)",
    )
    author = st.text_input(
        "Author (Helvetica‑Bold → Arial‑BoldMT)",
        st.session_state.metadata_values["author"],
        help="Base font: Helvetica‑Bold (Type 1) → Actual: Arial‑BoldMT (TrueType)",
    )
    subject = st.text_input(
        "Subject",
        st.session_state.metadata_values["subject"],
        help="Standard text field",
    )
    keywords = st.text_input(
        "Keywords",
        st.session_state.metadata_values["keywords"],
        help="Comma‑separated list",
    )
with col2:
    creator = st.text_input(
        "Creator (Application)",
        st.session_state.metadata_values["creator"],
        help="Application that originally created the PDF",
    )
    producer = st.text_input(
        "Producer",
        st.session_state.metadata_values["producer"],
        help="PDF producing library / engine",
    )

# ----- date / time (seconds) ---------------------------------------
st.markdown("### Dates (seconds precision)")
date_col1, date_col2 = st.columns(2)

with date_col1:
    c_date = st.date_input(
        "Creation Date", st.session_state.metadata_values["creation_date"]
    )
    c_time_val = st.session_state.metadata_values["creation_time"]
    c_time = st.time_input(
        "Creation Time (HH:MM:SS)",
        value=c_time_val,
        # No `step` argument – Streamlit accepts seconds natively.
    )
    c_seconds = st.number_input(
        "Seconds",
        min_value=0,
        max_value=59,
        value=st.session_state.metadata_values["creation_seconds"],
        key="c_sec",
    )

with date_col2:
    m_date = st.date_input(
        "Modification Date", st.session_state.metadata_values["mod_date"]
    )
    m_time_val = st.session_state.metadata_values["mod_time"]
    m_time = st.time_input(
        "Modification Time (HH:MM:SS)",
        value=m_time_val,
        # no step
    )
    m_seconds = st.number_input(
        "Seconds",
        min_value=0,
        max_value=59,
        value=st.session_state.metadata_values["mod_seconds"],
        key="m_sec",
    )

# ----- Apply‑same‑dates toggle ---------------------------------------
apply_same_dates = st.checkbox(
    "Use the dates shown above for **all** uploaded PDFs",
    value=True,
    key="apply_same_dates",
)

# ----- Action buttons -----------------------------------------------
btn_col1, btn_col2 = st.columns(2)
with btn_col1:
    if st.button("💾 Apply Changes", type="primary", use_container_width=True):
        st.session_state.run_action = "apply"
with btn_col2:
    if st.button("🗑️ Clear All Metadata", use_container_width=True):
        st.session_state.run_action = "clear"

# ----------------------------------------------------------------------
# Processing (apply / clear)
# ----------------------------------------------------------------------
if st.session_state.run_action in ("apply", "clear"):
    action = st.session_state.run_action
    st.session_state.run_action = None

    results = []
    errors = []

    progress = st.progress(0)
    status = st.empty()
    total = len(uploaded_files)

    for i, file in enumerate(uploaded_files, start=1):
        status.text(f"Processing {i}/{total}: **{file.name}**")
        try:
            # ----- read original PDF ---------------------------------
            reader = PdfReader(io.BytesIO(file.getvalue()))
            writer = PdfWriter()
            writer.append_pages_from_reader(reader)

            if action == "clear":
                # Remove everything, then explicitly delete the auto‑added dates.
                writer.add_metadata({})
                if hasattr(writer, "_info"):
                    writer._info.pop("/CreationDate", None)
                    writer._info.pop("/ModDate", None)
            else:
                meta = {}

                # ----- simple text fields ------------------------------
                if title.strip():
                    meta["/Title"] = title.strip()
                if author.strip():
                    meta["/Author"] = author.strip()
                if subject.strip():
                    meta["/Subject"] = subject.strip()
                if keywords.strip():
                    meta["/Keywords"] = keywords.strip()
                if creator.strip():
                    meta["/Creator"] = creator.strip()
                if producer.strip():
                    meta["/Producer"] = producer.strip()

                # ----- dates -------------------------------------------
                if apply_same_dates:
                    # Use the UI values (the same for every file)
                    if c_date:
                        ct = time(c_time.hour, c_time.minute, c_seconds)
                        cdt = datetime.combine(c_date, ct)
                        meta["/CreationDate"] = format_pdf_date(cdt, tz=LOCAL_TZ)

                    if m_date:
                        mt = time(m_time.hour, m_time.minute, m_seconds)
                        mdt = datetime.combine(m_date, mt)
                        meta["/ModDate"] = format_pdf_date(mdt, tz=LOCAL_TZ)
                else:
                    # Keep each file’s original timestamps (if they exist)
                    src = extract_metadata_dict(file.getvalue(), file.name)
                    if src.get("creation_dt"):
                        meta["/CreationDate"] = format_pdf_date(
                            src["creation_dt"], tz=src["creation_dt"].tzinfo
                        )
                    if src.get("mod_dt"):
                        meta["/ModDate"] = format_pdf_date(
                            src["mod_dt"], tz=src["mod_dt"].tzinfo
                        )

                writer.add_metadata(meta)

            # ----- write to a buffer ---------------------------------
            out_buf = io.BytesIO()
            writer.write(out_buf)
            out_buf.seek(0)

            prefix = "[CLEARED]" if action == "clear" else "[EDITED]"
            new_name = f"{prefix} {Path(file.name).stem}.pdf"
            results.append((new_name, out_buf.getvalue()))

        except Exception as exc:
            errors.append(f"{file.name}: {exc}")

        progress.progress(i / total)

    progress.empty()
    status.empty()

    st.session_state.processed_results = results
    st.session_state.processed_errors = errors
    st.session_state.last_action = action

    st.success(f"✅ Processed {len(results)} file(s) successfully.")
    if errors:
        st.error("❌ Some files failed:\n" + "\n".join(errors))

# ----------------------------------------------------------------------
# Download section (single PDF or ZIP)
# ----------------------------------------------------------------------
if st.session_state.processed_results:
    st.markdown("### Download Results")
    results = st.session_state.processed_results

    if len(results) == 1:
        fname, data = results[0]
        st.download_button(
            label=f"⬇️ Download: {fname}",
            data=data,
            file_name=fname,
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in results:
                zf.writestr(fname, data)
        zip_buf.seek(0)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"pdfs_{'cleared' if st.session_state.last_action == 'clear' else 'edited'}_{ts}.zip"

        st.download_button(
            label=f"⬇️ Download ZIP ({len(results)} files)",
            data=zip_buf,
            file_name=zip_name,
            mime="application/zip",
            use_container_width=True,
        )

    # ----- reset button (start a fresh round) ------------------------
    if st.button("Clear previous results (start fresh)"):
        st.session_state.processed_results = None
        st.session_state.processed_errors = []
        st.session_state.last_action = None
        st.rerun()

# ----------------------------------------------------------------------
# Footer
# ----------------------------------------------------------------------
st.caption(
    "Helvetica/Arial UI fixed • Streamlit + pypdf • Seconds precision • "
    "Font mapping: Helvetica → ArialMT"
)
