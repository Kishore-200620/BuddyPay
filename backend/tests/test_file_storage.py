import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.core.file_storage import MAX_FILE_SIZE, validate_pdf


def make_upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
    )


def run_async(coro):
    return asyncio.run(coro)


def test_validate_pdf_accepts_valid_pdf():
    file = make_upload("invoice.pdf", b"%PDF-1.7\ninvoice content")

    result = run_async(validate_pdf(file))

    assert result == b"%PDF-1.7\ninvoice content"


def test_validate_pdf_rejects_missing_filename():
    file = make_upload("", b"%PDF-1.7\ninvoice content")

    with pytest.raises(HTTPException) as exc:
        run_async(validate_pdf(file))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Filename is required"


def test_validate_pdf_rejects_non_pdf_extension():
    file = make_upload("invoice.txt", b"%PDF-1.7\ninvoice content")

    with pytest.raises(HTTPException) as exc:
        run_async(validate_pdf(file))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Only PDF files are allowed"


def test_validate_pdf_rejects_empty_file():
    file = make_upload("invoice.pdf", b"")

    with pytest.raises(HTTPException) as exc:
        run_async(validate_pdf(file))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Uploaded file is empty"


def test_validate_pdf_rejects_invalid_signature():
    file = make_upload("invoice.pdf", b"not a pdf")

    with pytest.raises(HTTPException) as exc:
        run_async(validate_pdf(file))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid PDF file"


def test_validate_pdf_rejects_file_over_10_mb():
    file = make_upload("invoice.pdf", b"%PDF-" + b"a" * MAX_FILE_SIZE)

    with pytest.raises(HTTPException) as exc:
        run_async(validate_pdf(file))

    assert exc.value.status_code == 413
    assert exc.value.detail == "PDF file must not exceed 10 MB"


def test_validate_pdf_accepts_file_at_max_size():
    content = b"%PDF-" + b"a" * (MAX_FILE_SIZE - len(b"%PDF-"))
    file = make_upload("invoice.pdf", content)

    result = run_async(validate_pdf(file))

    assert len(result) == MAX_FILE_SIZE
    assert result.startswith(b"%PDF-")
