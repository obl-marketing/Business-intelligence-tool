"""Extract text from uploaded documents (Excel, CSV, PDF) for the
knowledge base. The extracted text becomes the entry's description so
the agent can read it as ground truth.

Kept dependency-light: pandas (already used) for spreadsheets, pypdf
for PDFs. Large files are truncated with a clear marker so a giant
catalog doesn't blow up the prompt.
"""
from __future__ import annotations
import io

# Cap extracted text so one huge file can't dominate the system prompt.
MAX_CHARS = 20000


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """Return (extracted_text, kind). kind is one of excel/csv/pdf/docx/txt/unknown."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xls")):
        return _from_excel(data), "excel"
    if name.endswith(".csv"):
        return _from_csv(data), "csv"
    if name.endswith(".pdf"):
        return _from_pdf(data), "pdf"
    if name.endswith(".docx"):
        return _from_docx(data), "docx"
    if name.endswith((".txt", ".md")):
        return _from_txt(data), "txt"
    if name.endswith(".doc"):
        return ("(Old-format .doc files aren't supported. Open it in Word and "
                "'Save As' .docx, then re-upload.)"), "unsupported"
    return "", "unknown"


def _truncate(text: str) -> str:
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + (
            f"\n\n... [truncated - file has {len(text):,} characters total, "
            f"showing the first {MAX_CHARS:,}. Upload a focused subset if you "
            "need the rest analysed.]"
        )
    return text


def _from_excel(data: bytes) -> str:
    import pandas as pd
    xls = pd.ExcelFile(io.BytesIO(data))
    parts = []
    for sheet in xls.sheet_names:
        df = xls.parse(sheet)
        if df.empty:
            continue
        parts.append(f"### Sheet: {sheet}  ({len(df)} rows x {len(df.columns)} cols)")
        # markdown table keeps structure readable for the model
        parts.append(df.to_markdown(index=False))
        parts.append("")
    return _truncate("\n".join(parts).strip() or "(workbook had no data rows)")


def _from_csv(data: bytes) -> str:
    import pandas as pd
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception:
        # fall back to latin-1 for odd encodings
        df = pd.read_csv(io.BytesIO(data), encoding="latin-1")
    header = f"### CSV  ({len(df)} rows x {len(df.columns)} cols)\n"
    return _truncate(header + df.to_markdown(index=False))


def _from_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    # also pull table cells - catalogs/specs often live in tables
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    text = "\n".join(parts).strip()
    return _truncate(text or "(Word document had no readable text.)")


def _from_txt(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return _truncate(text.strip() or "(empty text file)")


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages, 1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        if txt.strip():
            parts.append(f"--- Page {i} ---\n{txt.strip()}")
    text = "\n\n".join(parts).strip()
    if not text:
        return ("(No selectable text found in this PDF - it may be a scanned "
                "image. Re-save it as a text-based PDF, or upload a screenshot "
                "instead so the agent can read it visually.)")
    return _truncate(text)
