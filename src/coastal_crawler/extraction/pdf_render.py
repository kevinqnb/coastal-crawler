"""PDF page rendering — renders PDF pages to base64-encoded PNG images for OCR.

Fast-mode only: no orientation correction. Requires poppler-utils
(``pdfinfo``/``pdftoppm``) to be installed on the system.
"""

from __future__ import annotations

import base64
import subprocess
from io import BytesIO

from PIL import Image
from pypdf import PdfReader


def _get_pdf_page_dimensions(pdf_path: str, page_num: int) -> tuple[float, float]:
    """Get PDF page dimensions in points using pdfinfo.

    Args:
        pdf_path: Path to PDF file
        page_num: Page number (1-indexed)

    Returns:
        Tuple of (width, height) in points
    """
    result = subprocess.run(
        ["pdfinfo", "-f", str(page_num), "-l", str(page_num), "-box", pdf_path],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise ValueError(f"pdfinfo failed:  {result.stderr}")

    for line in result.stdout.splitlines():
        if "MediaBox" in line:
            parts = line.split(":", 1)[1].strip().split()
            if len(parts) >= 4:
                x0, y0, x1, y1 = map(float, parts[:4])
                return x1 - x0, y1 - y0

    raise ValueError("MediaBox not found in PDF info")


def _load_pdf_page(
    pdf_path: str,
    page_num: int,
    target_longest_dim: int,
) -> Image.Image:
    """Render a PDF page to a PIL Image.

    Args:
        pdf_path: Path to PDF file
        page_num: Page number (1-indexed)
        target_longest_dim: Target size for longest dimension in pixels

    Returns:
        PIL Image object
    """
    width, height = _get_pdf_page_dimensions(pdf_path, page_num)
    dpi = int(target_longest_dim * 72 / max(width, height))

    result = subprocess.run(
        ["pdftoppm", "-png", "-f", str(page_num), "-l", str(page_num), "-r", str(dpi), pdf_path],
        capture_output=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr.decode()}")

    image: Image.Image = Image.open(BytesIO(result.stdout))
    if image.mode != "RGB":
        image = image.convert("RGB")

    return image


def _encode_pil_image(pil_image: Image.Image) -> str:
    """Encode a PIL image to a base64 string."""
    buffered = BytesIO()
    pil_image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def render_pdf_pages(pdf_path: str, target_longest_dim: int = 1024) -> list[str]:
    """Render all pages of a PDF to base64-encoded PNG images.

    Args:
        pdf_path: Path to PDF file
        target_longest_dim: Target size for longest dimension in pixels

    Returns:
        List of base64-encoded strings, one per page.
    """
    reader = PdfReader(pdf_path)
    num_pages = len(reader.pages)

    results = []
    for page_num in range(1, num_pages + 1):
        pil_image = _load_pdf_page(pdf_path, page_num, target_longest_dim)
        results.append(_encode_pil_image(pil_image))

    return results
