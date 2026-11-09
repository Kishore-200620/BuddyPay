
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
def extract_text_from_pdf(file_path: str) -> str:
    """
    Extract text from a PDF.

    First attempts native text extraction using PyMuPDF.
    If no selectable text is found, performs OCR using Tesseract.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError("Invoice PDF file not found")

    extracted_pages = []

    try:
        with pymupdf.open(path) as document:

            if document.is_encrypted:
                raise ValueError("Encrypted PDF files are not supported")

            if document.page_count == 0:
                raise ValueError("PDF contains no pages")

            for page_number, page in enumerate(document, start=1):

                # First attempt: native PDF text extraction
                page_text = page.get_text("text").strip()

                if page_text:
                    extracted_pages.append(
                        f"--- Page {page_number} ---\n{page_text}"
                    )
                    continue

                # Second attempt: OCR for image-based pages
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(2, 2),
                    alpha=False
                )

                image = Image.frombytes(
                    "RGB",
                    [pixmap.width, pixmap.height],
                    pixmap.samples
                )

                ocr_text = pytesseract.image_to_string(
                    image,
                    lang="eng"
                ).strip()

                if ocr_text:
                    extracted_pages.append(
                        f"--- Page {page_number} (OCR) ---\n{ocr_text}"
                    )

    except pymupdf.FileDataError as error:
        raise ValueError("Invalid or corrupted PDF file") from error

    return "\n\n".join(extracted_pages)
