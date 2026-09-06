# Production image for the FastAPI backend (also serves the bundled
# static portal UI from app/static/index.html — see app/main.py).
#
# Installs, beyond the pip packages in requirements.txt:
#   - tesseract-ocr + poppler-utils: needed by app/services/pdf_extractor.py
#     (pytesseract + pdf2image) for the OCR fallback on scanned/photographed
#     PCN notices.
#   - Playwright's Chromium + its OS-level dependencies: needed by
#     app/services/submission_bot.py to drive the sandbox appeal submission.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Downloads the Chromium binary Playwright's Python package was pinned to,
# plus the OS libraries it needs to actually run headless on Debian.
RUN playwright install --with-deps chromium

COPY app ./app

# Render (and most PaaS hosts) inject PORT at runtime; 8000 is the local
# dev fallback so `docker run` without -e PORT=... still works.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
