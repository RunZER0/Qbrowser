# Qbrowser document and artifact subsystem

The computer operator continues to use the visible GUI for website interaction. The optional local document tool uses mature Python libraries to create and inspect files without clicking through Office interfaces.

## Installed packages

The normal `python bootstrap.py` command installs `.[server,native,documents]`. The `documents` extra includes:

- `python-docx`: Word creation, headings, paragraphs, tables, and metadata inspection.
- `reportlab`: PDF generation.
- `PyMuPDF`: PDF text extraction, page counts and page preview PNGs.
- `openpyxl`: Spreadsheet creation, workbook metadata and CSV conversion.
- `python-pptx`: Presentation creation, text extraction and slide QA.
- `Pillow`: Image validation and thumbnail previews.

The `python-multipart` package is installed with the server extra for streaming file intake.

## Create a task, attach original files

    qbrowser add "Read the uploaded research instructions, write a report and save a DOCX"
    qbrowser upload JOB_ID ./instructions.pdf --role input
    qbrowser upload JOB_ID ./notes.docx --role input
    qbrowser versions JOB_ID

Uploaded input files are copied into `QBROWSER_HOME/workspaces/JOB_ID/inputs/`, with immutable, hashed versions under `QBROWSER_HOME/vault/JOB_ID/`. They appear in the agent's next instruction manifest and are readable through `document_action` or the normal desktop GUI.

Upload through the dashboard's **Documents → Upload file** control, or:

    curl -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
      -F "file=@instructions.pdf" \
      http://127.0.0.1:8765/api/jobs/JOB_ID/documents/upload/input

Only allowlisted formats and validated, bounded files are accepted. Office ZIP packages are inspected for corruption, expansion limits and macro/ActiveX payloads. Filenames cannot include directories or alternate streams. Uploaded files stay on the local machine unless the agent subsequently opens them in external services or the model processes their content.

## Create a document

The normalized payload supports DOCX or PDF:

    {
      "title": "Professional Report",
      "font": "Times New Roman",
      "font_size": 12,
      "blocks": [
        {"type": "heading", "level": 1, "text": "Background"},
        {"type": "paragraph", "text": "A concise discussion."},
        {"type": "bullet", "text": "Supporting evidence"},
        {"type": "table", "rows": [["Field", "Value"], ["Conclusion", "Confirmed"]]}
      ]
    }

Use `qbrowser generate JOB_ID report.docx payload.json`, or POST the object to `/api/jobs/JOB_ID/documents/generate` in a `{"filename":"report.docx","data":...}` envelope.

For Excel:

    {"sheets":[{"name":"Budget","headers":true,"rows":[
      ["Category","KES"],["Research",12000],["Travel",4000]
    ]}]}

For PowerPoint:

    {"slides":[{"title":"Project Overview","bullets":["Scope","Current progress"]},
               {"title":"Next Steps","bullets":["Complete evaluation"]}]}

The generated PPTX uses simple academic slides, without card layouts. Word, slides, and spreadsheet contents are bounded to limit memory use and abusive payloads. Spreadsheet values beginning with formula-control characters are stored as plain text rather than executable formulas.

PDF generation uses ReportLab and does not preserve complex DOCX or PPTX layout. For faithful Office-to-PDF export, use the appropriate installed desktop application through Qbrowser's GUI workflow. Unsupported conversions are rejected rather than producing a falsely formatted PDF.

## Convert supported formats

    qbrowser convert JOB_ID input instructions.pdf instructions.txt
    qbrowser convert JOB_ID output report.docx report.md
    qbrowser convert JOB_ID output budget.xlsx budget.csv
    qbrowser convert JOB_ID input source.csv source.xlsx

Supported conversions:
- PDF, DOCX, XLSX, PPTX and CSV → TXT or Markdown (plain extracted text; formatting is not preserved).
- TXT/Markdown → DOCX or PDF.
- CSV → XLSX; first active XLSX sheet → CSV.

Existing Office documents cannot be reliably converted to layout-perfect PDFs with only the pure-Python fallback libraries. Use a GUI-driven office export if visual fidelity matters.

## Version control and lineage

Each saved artifact stores:
- Job ID, input/output role, sanitized filename and monotonically increasing version.
- SHA-256 content digest and size.
- Content type, creation time, parent version digest.
- Parsed metadata and automatically generated QA issues.

Identical content is deduplicated. Changing a document creates a new immutable revision, so the previous text and file can be retrieved. Output files saved through ordinary GUI tools are synchronized on task completion, at native agent milestones marked done, and periodically after GUI actions.

Latest and historical versions are available via `qbrowser versions JOB_ID` and the Documents panel. Downloads always target an immutable snapshot, authenticated by the local API token.

## Preview, review and quality controls

The dashboard supports preview, download and QA review of each revision. PDFs render as genuine PNG previews; images render as thumbnails. Word, Excel, PowerPoint and text files currently use a **text-only preview**, with a conspicuous layout limitation notice.

`qbrowser document-qa JOB_ID input instructions.pdf` reports metadata and issues. Heuristics include empty files, abnormal Word font sizes and narrow page margins, text box overlap and out-of-slide shapes in PowerPoint, workbook dimensions, in-text author/year citation consistency, missing references headings and citation/reference mismatches.

These checks cannot certify academic accuracy, source existence, plagiarism, grammar or exact rendered Office layout. External source URLs are extracted for separate verification through a browser workflow. `source_verification` is explicitly reported as internal consistency only.

## Authenticated local routes

- `GET /api/jobs/{id}/documents` lists all input and output revisions.
- `POST /api/jobs/{id}/documents/upload/{role}` accepts multipart upload.
- `POST /api/jobs/{id}/documents/generate` creates an output.
- `POST /api/jobs/{id}/documents/convert` converts an existing version.
- `GET /api/jobs/{id}/documents/{role}/{filename}/versions` lists version history.
- `GET /api/jobs/{id}/documents/{role}/{filename}/review` performs QA and metadata extraction.
- `GET /api/jobs/{id}/documents/{role}/{filename}/preview?version=1` previews an immutable version.
- `GET /api/jobs/{id}/documents/{role}/{filename}/download?version=1` downloads with `Content-Disposition: attachment`.
- `POST /api/jobs/{id}/documents/retention` returns a global retention dry-run only.

All requests require the same local bearer token as the rest of Qbrowser whenever `QBROWSER_API_TOKEN` is set.

## Retention and deletion

Preview eligible revisions without deleting:

    qbrowser purge-documents --days 90

Perform irreversible cleanup of versions belonging to finished jobs older than the threshold:

    qbrowser purge-documents --days 90 --confirm

Cleanup removes expired immutable bytes and matching working files, while preserving version metadata with `deleted_at` so the audit trail remains. It does not delete active-job files.

For opt-in scheduled cleanup, set `QBROWSER_ARTIFACT_RETENTION_DAYS=90` in `.env`. Setting `0` (the default) disables automatic deletion. Do not enable this until you have backed up output files and decided on a policy for the clients' documents.

**Privacy:** model inference may transmit screenshot contents and selected extracted document content to the configured external provider. Qbrowser vault bytes are not encrypted at rest; use an encrypted user volume or OS account isolation for sensitive work. Retention removes bytes but is not forensic secure erasure on SSDs.
