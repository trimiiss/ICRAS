"""Generate minimal valid PDF files for sample bundles.

These are real PDFs (valid PDF 1.0 structure) containing a single page
with deterministic clause text. No external PDF library needed.
"""

from pathlib import Path


def make_minimal_pdf(lines: list[str]) -> bytes:
    """Build a minimal but valid PDF file with a single page and text lines.

    The PDF follows the PDF 1.0 specification with the minimum required
    objects: catalog, pages, page, font, and a content stream.

    Args:
        lines: Text lines to display on the single page.

    Returns:
        The complete PDF file as bytes.
    """
    # We build the PDF by hand so we don't need any library.
    # Object 1: Catalog
    # Object 2: Pages
    # Object 3: Page
    # Object 4: Font (Helvetica)
    # Object 5: Content stream

    text_commands = ["BT /F1 10 Tf 72 720 Td 14 TL"]
    for line in lines:
        text_commands.append(f"({_escape_pdf_text(line)}) Tj T*")
    text_commands.append("ET")
    content_stream = "\n".join(text_commands)
    content_bytes = content_stream.encode("latin-1")
    stream_length = len(content_bytes)

    objects = []
    offsets = []

    def add_obj(obj_str: str) -> None:
        objects.append(obj_str)

    # Object 1 - Catalog
    add_obj("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    # Object 2 - Pages
    add_obj("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    # Object 3 - Page
    add_obj(
        "3 0 obj\n"
        "<< /Type /Page /Parent 2 0 R "
        "/MediaBox [0 0 612 792] "
        "/Contents 5 0 R "
        "/Resources << /Font << /F1 4 0 R >> >> >>\n"
        "endobj\n"
    )
    # Object 4 - Font
    add_obj(
        "4 0 obj\n"
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        "endobj\n"
    )
    # Object 5 - Content stream
    add_obj(
        f"5 0 obj\n"
        f"<< /Length {stream_length} >>\n"
        f"stream\n"
        f"{content_stream}\n"
        f"endstream\n"
        f"endobj\n"
    )

    # Build the file
    header = b"%PDF-1.0\n"
    body = b""
    for obj_str in objects:
        offsets.append(len(header) + len(body))
        body += obj_str.encode("latin-1")

    # Cross-reference table
    xref_offset = len(header) + len(body)
    xref = "xref\n"
    xref += f"0 {len(objects) + 1}\n"
    xref += "0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"

    # Trailer
    trailer = (
        f"trailer\n"
        f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n"
        f"{xref_offset}\n"
        f"%%EOF\n"
    )

    return header + body + xref.encode("latin-1") + trailer.encode("latin-1")


def _escape_pdf_text(text: str) -> str:
    """Escape characters that are special in PDF text literals."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def main() -> None:
    """Generate PDF files for both sample bundles."""
    base = Path(__file__).resolve().parent

    bundles = {
        "clean_nda": [
            "Mutual Non-Disclosure Agreement - Acme Corporation",
            "1 Parties",
            "This Agreement is between Genpact LLC and Acme Corporation.",
            "2 Effective Date",
            "The effective date is January 15, 2025.",
            "3 Confidentiality",
            "Each party shall protect Confidential Information and use it only for Project Alpha.",
            "4 Termination",
            "Either party may terminate this Agreement with 30 days written notice.",
            "5 Payment Terms",
            "No fees are due under this NDA, and any approved invoices are payable net 30.",
            "6 Liability Cap",
            "Each party's aggregate liability is capped at 100000 USD.",
            "7 Indemnity",
            "Each party shall indemnify the other for third-party claims caused by breach.",
            "8 Governing Law",
            "This Agreement is governed by the laws of Delaware, USA.",
            "9 Auto-Renewal",
            "This Agreement does not auto-renew after expiration.",
            "10 Data Protection",
            "Each party shall comply with applicable data protection and privacy laws.",
        ],
        "services_agreement": [
            "Master Services Agreement - TechServe Solutions Ltd.",
            "1 Parties",
            "This Agreement is between Genpact LLC and TechServe Solutions Ltd.",
            "2 Effective Date",
            "The effective date is February 1, 2025.",
            "3 Payment Terms",
            "Invoices are payable net 45 after receipt of a valid invoice.",
            "4 Termination",
            "Either party may terminate for material breach after a 30 day cure period.",
            "5 Governing Law",
            "This Agreement is governed by the laws of New York, USA.",
        ],
    }

    for bundle_name, lines in bundles.items():
        pdf_path = base / "data" / "bundles" / bundle_name / "contract.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_bytes = make_minimal_pdf(lines)
        pdf_path.write_bytes(pdf_bytes)
        print(f"Created {pdf_path} ({len(pdf_bytes)} bytes)")


if __name__ == "__main__":
    main()
