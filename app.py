#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
📄 PDF Metadata Editor (Streamlit + pypdf)

Features
--------
* Upload one or many PDF files.
* View / edit common metadata fields (Title, Author, …).
* Edit Creation‑/Modification‑Date **with seconds** (HH:MM:SS).
* “Apply same dates to all PDFs” **or** keep each file’s original dates.
* Clear all metadata (including the auto‑added dates).
* Download a single edited PDF or a ZIP of all results.
* Fixed Helvetica‑→‑Arial font note (UI‑only).

Dependencies
------------
pip install streamlit pypdf pytz tzlocal
"""

import io
import zipfile
from pathlib import Path
from datetime import datetime, time

import streamlit as st
import pytz
from pypdf import PdfReader, PdfWriter

# ----------------------------------------------------------------------
# ── Detect the machine’s local timezone (fallback → UTC)               ──
# ----------------------------------------------------------------------
try:
    import tzlocal  # type: ignore
    LOCAL_TZ = tzlocal.get_localzone()
except Exception:  # pragma: no cover
    LOCAL_TZ = pytz.UTC

# ----------------------------------------------------------------------
# ── Page configuration & global CSS (Helvetica / Arial)               ──
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
st.markdown(
    "Upload PDFs → edit (or clear) the metadata → download the modified files"
)

# ----------------------------------------------------------------------
# ── Default Creator / Producer values                                 ──
# ----------------------------------------------------------------------
DEFAULT_CREATOR = (
    "JasperReports Library version 6.20.5-3efcf2e67f959db3888d79f73dde2dbd7acb4f8e"
)
DEFAULT_PRODUCER = "OpenPDF 1.3.30"

# ----------------------------------------------------------------------
# ── Font‑mapping reference (purely informational)                     ──
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
# ── Session‑state initialisation                                      ──
# ----------------------------------------------------------------------
if "metadata_values" not in st.session_state:
    st.session_state.metadata_values = {
        "title": "",
        "author": "",
        "subject": "",
        "keywords": "",
        "creator": DEFAULT_CREATOR,
        "producer": DEFAULT_PRODUCER,
        "creation_date": None,   # datetime.date or None
        "creation_time": time(0, 0, 0),
        "mod_date": None,
        "mod_time": time(0, 0, 0),
    }

if "last_first_filename" not in st.session_state:
    st.session_state.last_first_filename = None

if "run_action" not in st.session_state:
    st.session_state.run_action = None  # "apply" | "clear" | None

if "processed_results" not in st.session_state:
    st.session_state.processed_results = None  # [(filename, bytes), ...]

if "processed_errors" not in st.session_state:
    st.session_state.processed_errors = []

if "last_action" not in st.session_state:
    st.session_state.last_action = None  # remember if we edited or cleared

# ----------------------------------------------------------------------
# ── Helper functions                                                 ──
# ----------------------------------------------------------------------
def pdf_date_to_datetime(pdf_date_str: str):
    """
    Convert a PDF “D:” string to an *aware* ``datetime``.
    Handles the full `+hh'mm' / -hh'mm' / Z` offset designators.
    Returns ``None`` on malformed input.
    """
    if not isinstance(pdf_date_str, str) or not pdf_date_str.startswith("D:"):
        return None
    raw = pdf_date_str[2:]  # strip “D:”

    # ------------------------------------------------------------------
    # 1️⃣ Isolate the basic date‑time component (up to 14 digits)
    # ------------------------------------------------------------------
    base_len = min(14, len(raw))
    base_part = raw[:base_len]
    padded = base_part.ljust(14, "0")
    try:
        dt_naive = datetime.strptime(padded, "%Y%m%d%H%M%S")
    except ValueError:
        return None

    # ------------------------------------------------------------------
    # 2️⃣ Parse the optional timezone part (if any)
    # ------------------------------------------------------------------
    tz_part = raw[base_len:]  # e.g. "Z", "+02'00'", "-05'30'"
    if not tz_part or tz_part == "Z":
        tz = pytz.UTC
    else:
        sign = tz_part[0]
        # Normalise "+02'00'" → "+0200"
        hours = "".join(filter(str.isdigit, tz_part[1:3]))
        mins = "".join(filter(str.isdigit, tz_part[4:6])) if len(tz_part) >= 6 else "00"
        try:
            offset_minutes = int(hours) * 60 + int(mins)
        except ValueError:
            offset_minutes = 0
        if sign == "-":
            offset_minutes = -offset_minutes
        tz = pytz.FixedOffset(offset_minutes)

    return dt_naive.replace(tzinfo=tz)


def format_pdf_date(dt: datetime, tz: pytz.timezone = None) -> str:
    """
    Turn a datetime (naïve or aware) into a PDF date string.
    * If *tz* is supplied, the datetime is first localized to that zone.
    * The produced string keeps the original offset (e.g. “+02'00'”) or “Z”.
    """
    if dt is None:
        return ""

    # If dt is naïve, attach a timezone (local zone by default)
    if dt.tzinfo is None:
        tz = tz or LOCAL_TZ
        dt = tz.localize(dt)

    offset = dt.utcoffset()
    if offset is None or offset.total_seconds() == 0:
        # UTC → short “Z” designator
        return f"D:{dt.strftime('%Y%m%d%H%M%S')}Z"

    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hrs, mins = divmod(total_minutes, 60)
    return f"D:{dt.strftime('%Y%m%d%H%M%S')}{sign}{hrs:02d}'{mins:02d}'"


def extract_metadata_dict(pdf_bytes: bytes, filename: str):
    """
    Return a dict with the most common Info‑dictionary entries.
    The dates are returned as *aware* ``datetime`` objects (or ``None``).
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        info = reader.metadata or {}

        creation_dt = pdf_date_to_datetime(info.get("/CreationDate", ""))
        mod_dt = pdf_date_to_datetime(info.get("/ModDate", ""))

        return {
            "title": info.get("/Title", ""),
            "author": info.get("/Author", ""),
            "subject": info.get("/Subject", ""),
            "keywords": info.get("/Keywords", ""),
            "creator": info.get("/Creator", DEFAULT_CREATOR) or DEFAULT_CREATOR,
            "producer": info.get("/Producer", DEFAULT_PRODUCER) or DEFAULT_PRODUCER,
            "creation_dt": creation_dt,   # aware datetime or None
            "mod_dt": mod_dt,             # aware datetime or None
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
    """
    Helper that writes the extracted values into ``st.session_state.metadata_values``.
    """
    meta = st.session_state.metadata_values
    meta["title"] = extracted.get("title", "")
    meta["author"] = extracted.get("author", "")
    meta["subject"] = extracted.get("subject", "")
    meta["keywords"] = extracted.get("keywords", "")
    meta["creator"] = extracted.get("creator", DEFAULT_CREATOR)
    meta["producer"] = extracted.get("producer", DEFAULT_PRODUCER)

    c_dt = extracted.get("creation_dt")
    m_dt = extracted.get("mod_dt")
    meta["creation_date"] = c_dt.date() if c_dt else None
    meta["creation_time"] = c_dt.time() if c_dt else time(0, 0, 0)
    meta["mod_date"] = m_dt.date() if m_dt else None
    meta["mod_time"] = m_dt.time() if m_dt else time(0, 0, 0)


# ----------------------------------------------------------------------
# ── File uploader                                                    ──
# ----------------------------------------------------------------------
uploaded_files = st.file_uploader(
    "Upload PDF file(s)",
    type=["pdf"],
    accept_multiple_files=True,
)

# Initialise UI fields when a *new* batch is uploaded
if uploaded_files:
    # Reset previously processed results as soon as a new upload arrives
    if st.session_state.last_first_filename != uploaded_files[0].name:
        first_file = uploaded_files[0]
        extracted = extract_metadata_dict(first_file.getvalue(), first_file.name)
        _populate_session_from_extracted(extracted)

        # Remember first filename to avoid re‑initialising on every rerun
        st.session_state.last_first_filename = first_file.name
else:
    st.info("Please upload one or more PDF files.")
    st.stop()

# ----------------------------------------------------------------------
# ── Font‑mapping reference (expander)                                 ──
# ----------------------------------------------------------------------
with st.expander(
    "📋 Font Mapping Reference (Helvetica → ArialMT)", expanded=False
):
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
# ── Edit‑metadata UI                                                ──
# ----------------------------------------------------------------------
st.subheader("Edit Metadata")

# --------------------------------------------------------------
# Font‑info banner (purely cosmetic)
# --------------------------------------------------------------
st.markdown(
    """
    <div class="font-info">
    <strong>Font Configuration:</strong> UI uses Helvetica family names,
    but the PDF will embed ArialMT equivalents (Helvetica → ArialMT,
    Helvetica‑Bold → Arial‑BoldMT, …)
    </div>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------
# Left column – text fields
# --------------------------------------------------------------
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

# --------------------------------------------------------------
# Date / time inputs (seconds precision)
# --------------------------------------------------------------
st.markdown("### Dates (seconds precision)")

date_col1, date_col2 = st.columns(2)

with date_col1:
    # ---- Creation -------------------------------------------------
    default_c_date = (
        st.session_state.metadata_values["creation_date"]
        or datetime.now().date()
    )
    c_date = st.date_input("Creation Date", default_c_date, key="c_date")

    default_c_time = st.session_state.metadata_values["creation_time"]
    c_time = st.time_input(
        "Creation Time (HH:MM:SS)",
        value=default_c_time,
        key="c_time",   # **NO step argument – the widget already accepts seconds**
        # format="HH:mm:ss"   # optional, adds a visual HH:MM:SS mask
    )

with date_col2:
    # ---- Modification --------------------------------------------
    default_m_date = (
        st.session_state.metadata_values["mod_date"]
        or datetime.now().date()
    )
    m_date = st.date_input("Modification Date", default_m_date, key="m_date")

    default_m_time = st.session_state.metadata_values["mod_time"]
    m_time = st.time_input(
        "Modification Time (HH:MM:SS)",
        value=default_m_time,
        key="m_time",
        # format="HH:mm:ss"
    )

# --------------------------------------------------------------
# “Apply same dates to all files” toggle
# --------------------------------------------------------------
apply_same_dates = st.checkbox(
    "Use the dates shown above for **all** uploaded PDFs",
    value=True,
    key="apply_same_dates",
)

# --------------------------------------------------------------
# Action buttons
# --------------------------------------------------------------
colb1, colb2 = st.columns(2)
with colb1:
    if st.button("💾 Apply Changes", type="primary", use_container_width=True):
        st.session_state.run_action = "apply"
with colb2:
    if st.button("🗑️ Clear All Metadata", use_container_width=True):
        st.session_state.run_action = "clear"

# ----------------------------------------------------------------------
# ── Processing (apply / clear)                                         ──
# ----------------------------------------------------------------------
if st.session_state.run_action in ("apply", "clear"):
    action = st.session_state.run_action
    st.session_state.run_action = None  # reset immediately

    # Containers for results and errors
    processed = []
    errors = []

    progress_bar = st.progress(0)
    status_text = st.empty()
    total_files = len(uploaded_files)

    for idx, file in enumerate(uploaded_files, start=1):
        status_text.text(f"Processing {idx}/{total_files}: **{file.name}**")
        try:
            src_bytes = file.getvalue()
            reader = PdfReader(io.BytesIO(src_bytes))
            writer = PdfWriter()
            writer.append_pages_from_reader(reader)

            if action == "clear":
                # Remove *all* Info‑dictionary entries, then delete the auto‑added dates
                writer.add_metadata({})
                if hasattr(writer, "_info"):
                    writer._info.pop("/CreationDate", None)
                    writer._info.pop("/ModDate", None)
            else:
                # --------------------------------------------------
                # Build a metadata dictionary for THIS file
                # --------------------------------------------------
                meta_dict = {}

                # ----- simple text entries -------------------------
                if title.strip():
                    meta_dict["/Title"] = title.strip()
                if author.strip():
                    meta_dict["/Author"] = author.strip()
                if subject.strip():
                    meta_dict["/Subject"] = subject.strip()
                if keywords.strip():
                    meta_dict["/Keywords"] = keywords.strip()
                if creator.strip():
                    meta_dict["/Creator"] = creator.strip()
                if producer.strip():
                    meta_dict["/Producer"] = producer.strip()

                # ----- dates ---------------------------------------
                if apply_same_dates:
                    # Use the dates the user edited in the UI for *every* file
                    if c_date:
                        cdt = datetime.combine(c_date, c_time)
                        meta_dict["/CreationDate"] = format_pdf_date(cdt, tz=LOCAL_TZ)
                    if m_date:
                        mdt = datetime.combine(m_date, m_time)
                        meta_dict["/ModDate"] = format_pdf_date(mdt, tz=LOCAL_TZ)
                else:
                    # Preserve each file’s original timestamps (if they exist)
                    src_meta = extract_metadata_dict(src_bytes, file.name)
                    if src_meta.get("creation_dt"):
                        meta_dict["/CreationDate"] = format_pdf_date(
                            src_meta["creation_dt"],
                            tz=src_meta["creation_dt"].tzinfo,
                        )
                    if src_meta.get("mod_dt"):
                        meta_dict["/ModDate"] = format_pdf_date(
                            src_meta["mod_dt"],
                            tz=src_meta["mod_dt"].tzinfo,
                        )

                writer.add_metadata(meta_dict)

            # --------------------------------------------------
            # Write the modified PDF to a BytesIO buffer
            # --------------------------------------------------
            out_buf = io.BytesIO()
            writer.write(out_buf)
            out_buf.seek(0)

            prefix = "[CLEARED]" if action == "clear" else "[EDITED]"
            new_name = f"{prefix} {Path(file.name).stem}.pdf"
            processed.append((new_name, out_buf.getvalue()))
        except Exception as exc:
            errors.append(f"{file.name}: {exc}")

        progress_bar.progress(idx / total_files)

    # Clean up UI helpers
    progress_bar.empty()
    status_text.empty()

    # Store outcomes in session state
    st.session_state.processed_results = processed
    st.session_state.processed_errors = errors
    st.session_state.last_action = action

    # ------------------------------------------------------------------
    # Feedback to the user
    # ------------------------------------------------------------------
    st.success(f"✅ Processed {len(processed)} file(s) successfully.")
    if errors:
        st.error("❌ Some files could not be processed:\n" + "\n".join(errors))

# ----------------------------------------------------------------------
# ── Download section (single file or ZIP)                           ──
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
        # Bundle everything into a ZIP archive in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(
            zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zip_file:
            for fname, data in results:
                zip_file.writestr(fname, data)
        zip_buffer.seek(0)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"pdfs_{'cleared' if st.session_state.last_action == 'clear' else 'edited'}_{ts}.zip"

        st.download_button(
            label=f"⬇️ Download ZIP ({len(results)} files)",
            data=zip_buffer,
            file_name=zip_name,
            mime="application/zip",
            use_container_width=True,
        )

    # ------------------------------------------------------------------
    # Reset button – lets the user start a fresh round without re‑uploading
    # ------------------------------------------------------------------
    if st.button("Clear previous results (start fresh)"):
        st.session_state.processed_results = None
        st.session_state.processed_errors = []
        st.session_state.last_action = None
        st.rerun()

# ----------------------------------------------------------------------
# ── Footer caption ----------------------------------------------------
# ----------------------------------------------------------------------
st.caption(
    "Helvetica/Arial UI fixed • Streamlit + pypdf • Seconds precision • "
    "Font mapping: Helvetica → ArialMT"
)
