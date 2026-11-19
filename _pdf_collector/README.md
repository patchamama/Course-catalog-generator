# _pdf_collector

Collect the PDFs of a folder tree into one organised folder — optionally merging the PDFs of each folder.

```bash
python _pdf_collector/collect.py SOURCE [OUTPUT] [--join-pdfs-in-same-folder]
```

- **Copy mode** (default): every PDF is copied to `OUTPUT`, keeping its sub-folder. `OUTPUT` defaults to `SOURCE/_pdf_collection`.
- **`--join-pdfs-in-same-folder`**: the PDFs of each folder become **one** file named after the folder (`Module 1/Module 1.pdf`), in natural order (`2.pdf` before `10.pdf`). Needs PyMuPDF (`pip install pymupdf`, already in `backend/requirements.txt`). Unreadable PDFs are reported and skipped.
- The source folder is never modified; the output folder is never re-scanned.

Used by **Search Console › Tools › PDF Collector**. Tests: `python -m unittest tests.test_md_pdf_tools`.
