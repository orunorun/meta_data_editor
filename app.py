#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Streamlit PDF Metadata Editor – Timezone‑aware version.
Fixes the "00:30 becomes 06:00" bug by writing the correct UTC offset.
"""

import io
import zipfile
from datetime import datetime, time
from pathlib import Path

import streamlit as st
import pytz
from pypdf import PdfReader, PdfWriter

# ----------------------------------------------------------------------
# Page config & CSS (Helvetica / Arial)
# ----------------------------------------------------------------------
st.set_page_config(page_title="PDF Metadata Editor", page_icon="🧹", layout="wide")
st.markdown(
    """
    <style>
    html, body, [class*="css"], .stText, .stMarkdown, p, h1, h2, h3, h4,
    span, label, button, input {
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
st.markdown("Upload PDFs → edit metadata (with correct timezone) → download")

# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------
DEFAULT_CREATOR = (
    "JasperReports Library version 6.20.5-3efcf2e67f959db3888d79f73dde2dbd7acb4f8e"
)
DEFAULT_PRODUCER = "OpenPDF 1.3.30"

# ----------------------------------------------------------------------
# Timezone selection (CRITICAL FIX)
# ----------------------------------------------------------------------
# Common zones – add more if needed. Default to Asia/Kolkata (IST) because
# the "00:30 → 06:00" symptom matches a server running UTC while user is in IST.
TZ_OPTIONS = [
    "Asia/Kolkata",   # IST  +5:30
    "UTC",            # UTC  +0
    "Asia/Dubai",     # GST  +4
    "Europe/London",  # GMT/BST
    "Europe/Paris",   # CET/CEST
    "America/New_York",
    "America/Los_Angeles",
    "Australia/Sydney",
]

# Detect server tz for a smarter default, but fallback to Asia/Kolkata
try:
    import tzlocal
    local_tz_name = str(tzlocal.get_localzone())
    default_tz_index = TZ_OPTIONS.index(local_tz_name) if local_tz_name in TZ_OPTIONS else 0
except Exception:
    default_tz_index = 0  # Default to Asia/Kolkata

selected_tz = st.selectbox(
    "🌍 Your Local Timezone (for correct date saving)",
    TZ_OPTIONS,
    index=default_tz_index,
    help="Pick the timezone where YOU are located. This ensures 00:30 stays 00:30.",
)
TARGET_TZ = pytz.timezone(selected_tz)

# ----------------------------------------------------------------------
# Session state
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
        "creation_time": None,  # datetime.time or None
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

# ----------------------------------------------------------------------
# Helper functions – robust PDF date handling
# ----------------------------------------------------------------------
def pdf_date_to_datetime(pdf_date_str: str):
    """
    Parse a PDF “D:” string (e.g., D:20260213003010+05'30') into an aware datetime.
    Preserves the original timezone offset.
    """
    if not isinstance(pdf_date_str, str) or not pdf_date_str.startswith("D:"):
        return None

    raw = pdf_date_str[2:]  # remove leading "D:"

    # ---- extract YYYYMMDDHHMMSS (up to 14 digits) ----
    base_len = min(14, len(raw))
    base = raw[:base_len].ljust(14, "0")
    try:
        dt_naive = datetime.strptime(base, "%Y%m%d%H%M%S")
    except ValueError:
        return None

    # ---- parse timezone offset (+hh'mm', -hh'mm', Z, or nothing) ----
    tz_part = raw[base_len:]
    if not tz_part or tz_part == "Z":
        tz = pytz.UTC
    else:
        sign = tz_part[0]
        # extract digits, ignoring quotes
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


def format_pdf_date(dt: datetime, tz: pytz.timezone) -> str:
    """
    Convert a datetime (naïve or aware) to PDF “D:” format with offset.
    Naïve datetimes are treated as being in the provided *tz*.
    """
    if dt is None:
        return ""

    # If naïve, assume the user‑selected timezone
    if dt.tzinfo is None:
        dt = tz.localize(dt)

    offset = dt.utcoffset()
    if offset is None or offset.total_seconds() == 0:
        return f"D:{dt.strftime('%Y%m%d%H%M%S')}Z"

    total_min = int(offset.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    total_min = abs(total_min)
    hrs, mins = divmod(total_min, 60)
    return f"D:{dt.strftime('%Y%m%d%H%M%S')}{sign}{hrs:02d}'{mins:02d}'"


def extract_metadata_dict(pdf_bytes: bytes, filename: str):
    """
    Read PDF metadata. Dates are returned as timezone‑aware datetimes (or None).
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        info = reader.metadata or {}

        c_dt = pdf_date_to_datetime(info.get("/CreationDate", ""))
        m_dt = pdf_date_to_datetime(info.get("/ModDate", ""))

        return {
            "title": info.get("/Title", ""),
            "author": info.get("/Author", ""),
            "subject": info.get("/Subject", ""),
            "keywords": info.get("/Keywords", ""),
            "creator": info.get("/Creator", DEFAULT_CREATOR) or DEFAULT_CREATOR,
            "producer": info.get("/Producer", DEFAULT_PRODUCER) or DEFAULT_PRODUCER,
            "creation_dt": c_dt,   # aware datetime or None
            "mod_dt": m_dt,        # aware datetime or None
        }
    except Exception as exc:
        st.warning(f"Could not read metadata from {filename}: {exc}")
        return {
            "title": "", "author": "", "subject": "", "keywords": "",
            "creator": DEFAULT_CREATOR, "producer": DEFAULT_PRODUCER,
            "creation_dt": None, "mod_dt": None,
        }


def populate_session(extracted: dict):
    """Fill session state from an extracted metadata dict."""
    m = st.session_state.metadata_values
    m["title"] = extracted["title"]
    m["author"] = extracted["author"]
    m["subject"] = extracted["subject"]
    m["keywords"] = extracted["keywords"]
    m["creator"] = extracted["creator"]
    m["producer"] = extracted["producer"]

    # Creation date
    if extracted["creation_dt"]:
        m["creation_date"] = extracted["creation_dt"].date()
        m["creation_time"] = extracted["creation_dt"].time()
        m["creation_seconds"] = extracted["creation_dt"].second
    else:
        m["creation_date"] = None
        m["creation_time"] = time(0, 0, 0)
        m["creation_seconds"] = 0

    # Mod date
    if extracted["mod_dt"]:
        m["mod_date"] = extracted["mod_dt"].date()
        m["mod_time"] = extracted["mod_dt"].time()
        m["mod_seconds"] = extracted["mod_dt"].second
    else:
        m["mod_date"] = None
        m["mod_time"] = time(0, 0, 0)
        m["mod_seconds"] = 0


# ----------------------------------------------------------------------
# File uploader
# ----------------------------------------------------------------------
uploaded_files = st.file_uploader(
    "Upload PDF file(s)", type=["pdf"], accept_multiple_files=True
)

if uploaded_files:
    first = uploaded_files[0]
    if first.name != st.session_state.last_first_filename:
        meta = extract_metadata_dict(first.getvalue(), first.name)
        populate_session(meta)
        st.session_state.last_first_filename = first.name
else:
    st.info("Please upload one or more PDF files.")
    st.stop()

# ----------------------------------------------------------------------
# Font mapping info (expander)
# ----------------------------------------------------------------------
with st.expander("📋 Font Mapping Reference (Helvetica → ArialMT)", expanded=False):
    st.markdown("**Font Substitution Mapping:**")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Helvetica**\n- Type: Type 1\n- Actual: ArialMT (TrueType)")
    with c2:
        st.markdown("**Helvetica‑Bold**\n- Type: Type 1\n- Actual: Arial‑BoldMT")
    with c3:
        st.markdown("**Helvetica‑BoldOblique**\n- Actual: Arial‑BoldItalicMT")

# ----------------------------------------------------------------------
# Editor UI
# ----------------------------------------------------------------------
st.subheader("Edit Metadata")
st.markdown(
    '<div class="font-info">UI uses Helvetica names; PDF embeds ArialMT.</div>',
    unsafe_allow_html=True,
)

col1, col2 = st.columns(2)
with col1:
    title = st.text_input("Title", st.session_state.metadata_values["title"])
    author = st.text_input("Author", st.session_state.metadata_values["author"])
    subject = st.text_input("Subject", st.session_state.metadata_values["subject"])
    keywords = st.text_input("Keywords", st.session_state.metadata_values["keywords"])

with col2:
    creator = st.text_input("Creator", st.session_state.metadata_values["creator"])
    producer = st.text_input("Producer", st.session_state.metadata_values["producer"])

st.markdown("### Dates (with Seconds)")
cold1, cold2 = st.columns(2)

with cold1:
    c_date = st.date_input(
        "Creation Date", st.session_state.metadata_values["creation_date"]
    )
    c_cols = st.columns([3, 1])
    with c_cols[0]:
        c_val = st.session_state.metadata_values["creation_time"] or time(0, 0, 0)
        c_time = st.time_input("Creation Time (HH:MM)", c_val)
    with c_cols[1]:
        c_sec = st.number_input(
            "Sec",
            0,
            59,
            st.session_state.metadata_values["creation_seconds"],
            key="c_sec",
        )

with cold2:
    m_date = st.date_input(
        "Modification Date", st.session_state.metadata_values["mod_date"]
    )
    m_cols = st.columns([3, 1])
    with m_cols[0]:
        m_val = st.session_state.metadata_values["mod_time"] or time(0, 0, 0)
        m_time = st.time_input("Modification Time (HH:MM)", m_val)
    with m_cols[1]:
        m_sec = st.number_input(
            "Sec",
            0,
            59,
            st.session_state.metadata_values["mod_seconds"],
            key="m_sec",
        )

# Option to apply same dates to all files or keep original per‑file dates
apply_same_dates = st.checkbox(
    "Use the dates above for **all** uploaded PDFs", value=True
)

st.markdown("---")
btn1, btn2 = st.columns(2)
with btn1:
    if st.button("💾 Apply Changes", type="primary", use_container_width=True):
        st.session_state.run_action = "apply"
with btn2:
    if st.button("🗑️ Clear All Metadata", use_container_width=True):
        st.session_state.run_action = "clear"

# ----------------------------------------------------------------------
# Processing
# ----------------------------------------------------------------------
if st.session_state.run_action in ("apply", "clear"):
    action = st.session_state.run_action
    st.session_state.run_action = None

    results = []
    errors = []
    prog = st.progress(0)
    status = st.empty()
    total = len(uploaded_files)

    for i, file in enumerate(uploaded_files, 1):
        status.text(f"Processing {i}/{total}: {file.name}")
        try:
            reader = PdfReader(io.BytesIO(file.getvalue()))
            writer = PdfWriter()
            writer.append_pages_from_reader(reader)

            if action == "clear":
                # Wipe everything including auto‑added dates
                writer.add_metadata({})
                if hasattr(writer, "_info"):
                    writer._info.pop("/CreationDate", None)
                    writer._info.pop("/ModDate", None)
            else:
                meta = {}
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

                # ----- Dates (use TARGET_TZ, not server tz) -----
                if apply_same_dates:
                    if c_date:
                        ct = time(c_time.hour, c_time.minute, c_sec)
                        cdt = datetime.combine(c_date, ct)
                        # CRITICAL: use TARGET_TZ so 00:30 stays 00:30+05'30 (not Z)
                        meta["/CreationDate"] = format_pdf_date(cdt, TARGET_TZ)

                    if m_date:
                        mt = time(m_time.hour, m_time.minute, m_sec)
                        mdt = datetime.combine(m_date, mt)
                        meta["/ModDate"] = format_pdf_date(mdt, TARGET_TZ)
                else:
                    # Keep original dates from this specific file
                    src = extract_metadata_dict(file.getvalue(), file.name)
                    if src.get("creation_dt"):
                        meta["/CreationDate"] = format_pdf_date(
                            src["creation_dt"], src["creation_dt"].tzinfo
                        )
                    if src.get("mod_dt"):
                        meta["/ModDate"] = format_pdf_date(
                            src["mod_dt"], src["mod_dt"].tzinfo
                        )

                writer.add_metadata(meta)

            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)

            prefix = "[CLEARED]" if action == "clear" else "[EDITED]"
            new_name = f"{prefix} {Path(file.name).stem}.pdf"
            results.append((new_name, buf.getvalue()))

        except Exception as exc:
            errors.append(f"{file.name}: {exc}")

        prog.progress(i / total)

    prog.empty()
    status.empty()

    st.session_state.processed_results = results
    st.session_state.processed_errors = errors
    st.session_state.last_action = action

    st.success(f"✅ Processed {len(results)} file(s).")
    if errors:
        st.error("❌ Errors:\n" + "\n".join(errors))

# ----------------------------------------------------------------------
# Download section
# ----------------------------------------------------------------------
if st.session_state.processed_results:
    st.markdown("### Download Results")
    res = st.session_state.processed_results

    if len(res) == 1:
        name, data = res[0]
        st.download_button(
            f"⬇️ Download: {name}", data, name, "application/pdf"
        )
    else:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in res:
                zf.writestr(name, data)
        zip_buf.seek(0)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        lbl = "cleared" if st.session_state.last_action == "clear" else "edited"
        zip_name = f"pdfs_{lbl}_{ts}.zip"
        st.download_button(
            f"⬇️ Download ZIP ({len(res)} files)",
            zip_buf,
            zip_name,
            "application/zip",
        )

    if st.button("Clear previous results (start fresh)"):
        st.session_state.processed_results = None
        st.session_state.processed_errors = []
        st.session_state.last_action = None
        st.rerun()

st.caption("Helvetica/Arial UI • Streamlit + pypdf • Timezone‑aware dates")
