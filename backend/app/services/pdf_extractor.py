"""
PDF text extraction service.

Most councils/operators issue PCNs as either:
  (a) a "born-digital" PDF (text was typeset, e.g. exported from a system) — or
  (b) a scanned/photographed PDF (it's really just an image of a page).

We try the cheap, fast path first (pdfplumber, direct text extraction) and
only fall back to OCR (pytesseract, via pdf2image rendering each page to an
image) when that yields little or no text. OCR is much slower and slightly
less accurate, so we don't want to pay that cost for every upload.
"""
import io

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes

# Below this many characters of directly-extracted text, assume the PDF is
# a scanned image with no text layer and fall back to OCR.
MIN_TEXT_LENGTH_BEFORE_OCR_FALLBACK = 20


class PDFExtractionError(Exception):
    """Raised when a PDF can't be read or produces no usable text at all."""


def _extract_text_directly(pdf_bytes: bytes) -> str:
    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts).strip()


def _extract_text_via_ocr(pdf_bytes: bytes) -> str:
    images = convert_from_bytes(pdf_bytes, dpi=150)
    text_parts = [pytesseract.image_to_string(image) for image in images]
    return "\n".join(text_parts).strip()


def extract_text_from_pdf(pdf_bytes: bytes) -> dict:
    """Returns {"text": str, "method": "direct" | "ocr"}.

    Raises PDFExtractionError if the file can't be parsed as a PDF or no
    text could be recovered by either method.
    """
    try:
        direct_text = _extract_text_directly(pdf_bytes)
    except Exception as exc:  # pdfplumber/pypdfium raise various errors on bad input
        raise PDFExtractionError(f"Could not read file as a PDF: {exc}") from exc

    if len(direct_text) >= MIN_TEXT_LENGTH_BEFORE_OCR_FALLBACK:
        return {"text": direct_text, "method": "direct"}

    try:
        ocr_text = _extract_text_via_ocr(pdf_bytes)
    except Exception as exc:
        raise PDFExtractionError(f"OCR fallback failed: {exc}") from exc

    if not ocr_text:
        raise PDFExtractionError(
            "No text could be extracted from this PDF, even with OCR. "
            "The scan may be too low-quality or the file may be corrupt."
        )

    return {"text": ocr_text, "method": "ocr"}
