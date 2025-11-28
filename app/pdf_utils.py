# # app/pdf_utils.py
# import fitz  # PyMuPDF
# import base64
# from typing import List


# def pdf_to_page_images_b64(pdf_bytes: bytes, max_pages: int = 2) -> List[str]:
#     """
#     Render up to max_pages of a PDF into base64-encoded PNG images
#     for use with vision models.
#     """
#     doc = fitz.open(stream=pdf_bytes, filetype="pdf")
#     images_b64: list[str] = []

#     for i, page in enumerate(doc):
#         if i >= max_pages:
#             break
#         pix = page.get_pixmap(dpi=200)
#         png_bytes = pix.tobytes("png")
#         b64 = base64.b64encode(png_bytes).decode("utf-8")
#         images_b64.append(b64)

#     doc.close()
#     return images_b64

# app/pdf_utils.py

import fitz  # PyMuPDF
import base64
from typing import List


def pdf_to_page_images_b64(pdf_bytes: bytes, max_pages: int = 2) -> List[str]:
    """
    Render up to max_pages of a PDF into base64-encoded PNG images.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images_b64: list[str] = []

    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(dpi=200)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("utf-8")
        images_b64.append(b64)

    doc.close()
    return images_b64
