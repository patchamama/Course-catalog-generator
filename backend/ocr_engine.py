"""
OCR Extract Engine — multiple backends for PDF and image text extraction.

Supported methods:
  pymupdf   — PyMuPDF text layer (best for PDFs with embedded text)
  pdfminer  — pdfminer.six text extraction
  tesseract — Tesseract OCR via pytesseract + Pillow
  easyocr   — EasyOCR (deep learning, 80+ languages)
  paddleocr — PaddleOCR (strong for tables/layouts)
  ollama    — Ollama vision model (llava, gemma3, minicpm-v, …)
"""
from __future__ import annotations

import base64
import io
import json
import shutil
from typing import Generator, Optional

# ── Known vision-capable model name prefixes ──────────────────────────────────
VISION_MODEL_PREFIXES = (
    'llava', 'minicpm', 'moondream', 'bakllava', 'cogvlm', 'internvl',
    'qwen-vl', 'yi-vl', 'gemma3', 'llama3.2-vision', 'llama3.2:',
    'granite3', 'pixtral', 'mistral-small3', 'phi3.5', 'phi-3.5',
    'deepseek-vl', 'glm-4v', 'llava-phi3', 'llava-llama3', 'llava-llama',
)

# ── Method metadata ───────────────────────────────────────────────────────────
METHODS: dict[str, dict] = {
    'pymupdf':   {
        'label': 'PyMuPDF',
        'subtitle': 'PDF text layer',
        'category': 'pdf',
        'pip': 'PyMuPDF',
        'note': 'Fast — reads embedded text, no OCR needed',
        'supports_images': False,
    },
    'pdfminer':  {
        'label': 'PDFMiner',
        'subtitle': 'PDF text extraction',
        'category': 'pdf',
        'pip': 'pdfminer.six',
        'note': 'Alternative PDF text extraction with layout analysis',
        'supports_images': False,
    },
    'tesseract': {
        'label': 'Tesseract',
        'subtitle': 'Classic OCR',
        'category': 'ocr',
        'pip': 'pytesseract Pillow',
        'note': 'Requires tesseract binary. Supports 100+ languages.',
        'supports_images': True,
    },
    'easyocr':   {
        'label': 'EasyOCR',
        'subtitle': 'Deep learning OCR',
        'category': 'ocr',
        'pip': 'easyocr',
        'note': 'Accurate deep learning OCR, 80+ languages, GPU optional',
        'supports_images': True,
    },
    'paddleocr': {
        'label': 'PaddleOCR',
        'subtitle': 'Layout-aware OCR',
        'category': 'ocr',
        'pip': 'paddlepaddle paddleocr',
        'note': 'Excellent for tables and complex document layouts',
        'supports_images': True,
    },
    'ollama':    {
        'label': 'Ollama Vision',
        'subtitle': 'AI vision model',
        'category': 'ai',
        'pip': None,
        'note': 'Uses llava, gemma3, minicpm-v, or any vision model via Ollama',
        'supports_images': True,
    },
}


# ── Availability checks ───────────────────────────────────────────────────────

def check_methods(ollama_url: str = '') -> dict:
    """Return availability status dict for each OCR method."""
    status: dict[str, dict] = {}

    # PyMuPDF
    try:
        import fitz  # noqa: F401
        status['pymupdf'] = {'available': True}
    except ImportError:
        status['pymupdf'] = {'available': False, 'install': 'pip install PyMuPDF'}

    # PDFMiner
    try:
        import pdfminer  # noqa: F401
        status['pdfminer'] = {'available': True}
    except ImportError:
        status['pdfminer'] = {'available': False, 'install': 'pip install pdfminer.six'}

    # Tesseract
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        tess_bin = shutil.which('tesseract') or pytesseract.get_tesseract_version
        status['tesseract'] = {'available': True}
    except Exception:
        status['tesseract'] = {
            'available': False,
            'install': 'pip install pytesseract Pillow  +  install tesseract binary',
        }

    # EasyOCR
    try:
        import easyocr  # noqa: F401
        status['easyocr'] = {'available': True}
    except ImportError:
        status['easyocr'] = {'available': False, 'install': 'pip install easyocr'}

    # PaddleOCR
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        status['paddleocr'] = {'available': True}
    except ImportError:
        status['paddleocr'] = {'available': False, 'install': 'pip install paddlepaddle paddleocr'}

    # Ollama — always shown; model availability checked by caller
    if ollama_url:
        try:
            import urllib.request as _u
            with _u.urlopen(ollama_url + '/api/tags', timeout=2) as r:
                data = json.loads(r.read())
                models = [m['name'] for m in data.get('models', [])]
                vision = [m for m in models if _is_vision_model(m)]
                status['ollama'] = {'available': bool(vision), 'models': models, 'vision': vision}
        except Exception:
            status['ollama'] = {'available': False, 'models': [], 'vision': [],
                                'install': 'Start Ollama and pull a vision model (llava, gemma3…)'}
    else:
        status['ollama'] = {'available': False, 'models': [], 'vision': [],
                            'install': 'Start Ollama service'}

    return status


def _is_vision_model(name: str) -> bool:
    nl = name.lower()
    return any(nl.startswith(p) or p in nl for p in VISION_MODEL_PREFIXES)


# ── PDF utilities ─────────────────────────────────────────────────────────────

def pdf_page_count(pdf_bytes: bytes) -> int:
    """Return number of pages in a PDF."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        n = len(doc)
        doc.close()
        return n
    except ImportError:
        pass
    try:
        from pdfminer.pdfpage import PDFPage
        return sum(1 for _ in PDFPage.get_pages(io.BytesIO(pdf_bytes)))
    except ImportError:
        pass
    return 1


def parse_page_range(spec: str, total: int) -> list[int]:
    """Parse '1', '1-3', '1,3,5', 'all' → 0-indexed page list."""
    if not spec or spec.lower() == 'all':
        return list(range(total))
    result: set[int] = set()
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            try:
                result.update(range(int(a) - 1, int(b)))
            except ValueError:
                pass
        else:
            try:
                result.add(int(part) - 1)
            except ValueError:
                pass
    return sorted(p for p in result if 0 <= p < total)


def pdf_to_images(pdf_bytes: bytes, pages: list[int], dpi: int = 200) -> list[tuple[int, bytes]]:
    """Convert PDF pages to PNG images. Returns [(1-indexed_page, png_bytes), …]."""
    # Try PyMuPDF first — no extra binary needed
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        imgs = []
        for pg in pages:
            if 0 <= pg < len(doc):
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = doc[pg].get_pixmap(matrix=mat)
                imgs.append((pg + 1, pix.tobytes('png')))
        doc.close()
        return imgs
    except ImportError:
        pass

    # Fallback: pdf2image (needs poppler)
    try:
        from pdf2image import convert_from_bytes
        pil_imgs = convert_from_bytes(pdf_bytes, dpi=dpi)
        imgs = []
        for pg in pages:
            if 0 <= pg < len(pil_imgs):
                buf = io.BytesIO()
                pil_imgs[pg].save(buf, format='PNG')
                imgs.append((pg + 1, buf.getvalue()))
        return imgs
    except ImportError:
        pass

    raise RuntimeError(
        "Cannot render PDF pages to images. "
        "Install PyMuPDF (pip install PyMuPDF) or pdf2image+poppler."
    )


def image_bytes_to_png(data: bytes) -> bytes:
    """Convert any image format to PNG bytes."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except ImportError:
        return data  # return as-is if Pillow not available


# ── Extraction methods ────────────────────────────────────────────────────────

def extract_pymupdf(pdf_bytes: bytes, pages: list[int]) -> list[tuple[int, str]]:
    """Extract text layer from PDF using PyMuPDF."""
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    result = []
    for pg in pages:
        if 0 <= pg < len(doc):
            text = doc[pg].get_text('text').strip()
            result.append((pg + 1, text))
    doc.close()
    return result


def extract_pdfminer(pdf_bytes: bytes, pages: list[int]) -> list[tuple[int, str]]:
    """Extract text from PDF using pdfminer.six."""
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    result = []
    for pg in pages:
        buf = io.StringIO()
        try:
            extract_text_to_fp(
                io.BytesIO(pdf_bytes), buf,
                page_numbers=[pg],
                laparams=LAParams(),
                output_type='text',
                codec='utf-8',
            )
            result.append((pg + 1, buf.getvalue().strip()))
        except Exception as e:
            result.append((pg + 1, f'[Error on page {pg + 1}: {e}]'))
    return result


def extract_tesseract(
    img_pages: list[tuple[int, bytes]],
    lang: str = 'eng',
) -> list[tuple[int, str]]:
    """Extract text from images using Tesseract OCR."""
    import pytesseract
    from PIL import Image
    result = []
    for pg_num, png_bytes in img_pages:
        try:
            img = Image.open(io.BytesIO(png_bytes))
            text = pytesseract.image_to_string(img, lang=lang)
            result.append((pg_num, text.strip()))
        except Exception as e:
            result.append((pg_num, f'[Tesseract error on page {pg_num}: {e}]'))
    return result


def extract_easyocr(
    img_pages: list[tuple[int, bytes]],
    langs: Optional[list[str]] = None,
) -> list[tuple[int, str]]:
    """Extract text from images using EasyOCR."""
    import easyocr
    langs = langs or ['en']
    reader = easyocr.Reader(langs, gpu=False, verbose=False)
    result = []
    for pg_num, png_bytes in img_pages:
        try:
            texts = reader.readtext(png_bytes, detail=0, paragraph=True)
            result.append((pg_num, '\n'.join(texts)))
        except Exception as e:
            result.append((pg_num, f'[EasyOCR error on page {pg_num}: {e}]'))
    return result


def extract_paddleocr(
    img_pages: list[tuple[int, bytes]],
    lang: str = 'en',
) -> list[tuple[int, str]]:
    """Extract text from images using PaddleOCR."""
    import numpy as np
    from PIL import Image
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    result = []
    for pg_num, png_bytes in img_pages:
        try:
            img = Image.open(io.BytesIO(png_bytes)).convert('RGB')
            arr = np.array(img)
            ocr_result = ocr.ocr(arr, cls=True)
            lines = []
            if ocr_result and ocr_result[0]:
                for item in ocr_result[0]:
                    if item and len(item) >= 2 and item[1]:
                        lines.append(item[1][0])
            result.append((pg_num, '\n'.join(lines)))
        except Exception as e:
            result.append((pg_num, f'[PaddleOCR error on page {pg_num}: {e}]'))
    return result


def stream_ollama(
    img_pages: list[tuple[int, bytes]],
    ollama_url: str,
    model: str,
    prompt: str = '',
    output_format: str = 'markdown',
) -> Generator[dict, None, None]:
    """Stream OCR extraction via Ollama vision model.

    Yields dicts:
      {'type': 'token',     'page': N, 'token': '…'}
      {'type': 'page_done', 'page': N, 'text':  '…'}
      {'type': 'error',     'page': N, 'text':  '…'}
    """
    import urllib.request as ureq

    if not prompt:
        if output_format == 'markdown':
            prompt = (
                "You are an OCR assistant. Extract ALL text from this image exactly as it appears. "
                "Format the output as clean Markdown: use # for headings, ** for bold, "
                "| tables |, ``` code blocks ```, and - bullet lists where appropriate. "
                "Preserve the document structure. Do not add commentary or explanations."
            )
        else:
            prompt = (
                "You are an OCR assistant. Extract ALL text from this image verbatim. "
                "Preserve the original line breaks and layout as closely as possible. "
                "Do not add commentary or explanations."
            )

    for pg_num, png_bytes in img_pages:
        b64 = base64.b64encode(png_bytes).decode()
        payload = {
            'model': model,
            'prompt': prompt,
            'images': [b64],
            'stream': True,
        }
        body = json.dumps(payload).encode()
        req = ureq.Request(
            ollama_url + '/api/generate',
            data=body,
            headers={'Content-Type': 'application/json'},
        )
        page_text = ''
        try:
            with ureq.urlopen(req, timeout=180) as resp:
                for raw in resp:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get('response', '')
                    if token:
                        page_text += token
                        yield {'type': 'token', 'page': pg_num, 'token': token}
                    if chunk.get('done'):
                        break
        except Exception as e:
            yield {'type': 'error', 'page': pg_num, 'text': str(e)}
            continue
        yield {'type': 'page_done', 'page': pg_num, 'text': page_text}
