from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

from pa_docket.sources.fixture.generator import (
    SyntheticCharge,
    SyntheticDefendant,
    SyntheticDocket,
    render_pdf,
    sample_cases,
)

OUT = Path("tests/fixtures/pdfs")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fixtures = {}
    cases = sample_cases()
    cases.extend(
        [
            SyntheticDocket(
                "MD-01-CR-0000200-2025",
                filing_date="01/05/2025",
                defendants=[SyntheticDefendant("Single Charge")],
                charges=[SyntheticCharge("1", "Retail theft", "01/01/2025", "Pending")],
            ),
            SyntheticDocket(
                "MD-01-CR-0000201-2025",
                filing_date="01/06/2025",
                defendants=[SyntheticDefendant("Affiant Example")],
                charges=[SyntheticCharge("1", "Assault", "01/01/2025", "Pending", affiant="Named Affiant")],
            ),
        ]
    )
    for case in cases:
        filename = f"{case.docket_number}.pdf"
        data = render_pdf(case)
        (OUT / filename).write_bytes(data)
        fixtures[filename] = {
            "docket_number": case.docket_number,
            "defendant_names": [d.name for d in case.defendants],
            "charge_count": len(case.charges),
        }
    duplicate_data = render_pdf(cases[0])
    (OUT / "duplicate-same-hash.pdf").write_bytes(duplicate_data)
    modified = fitz.open(stream=duplicate_data, filetype="pdf")
    modified[0].insert_text((48, 780), "Modified version marker", fontsize=8)
    (OUT / "modified-same-docket.pdf").write_bytes(modified.tobytes())
    modified.close()
    (OUT / "corrupt-truncated.pdf").write_bytes(b"%PDF-1.7\ntruncated")
    (OUT / "bad-magic.pdf").write_bytes(b"HTML error page pretending to be a PDF")
    unsupported = fitz.open()
    page = unsupported.new_page()
    page.insert_text((48, 48), "Court record. Docket data follows in an unsupported layout.")
    (OUT / "unsupported-layout.pdf").write_bytes(unsupported.tobytes())
    unsupported.close()
    image_bytes = BytesIO()
    image = Image.new("RGB", (700, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "Scanned docket sheet - synthetic image fixture", fill="black")
    image.save(image_bytes, format="PNG")
    image_only = fitz.open()
    page = image_only.new_page()
    page.insert_image(page.rect, stream=image_bytes.getvalue())
    (OUT / "image-only.pdf").write_bytes(image_only.tobytes())
    image_only.close()
    (OUT.parent / "expected.json").write_text(json.dumps(fixtures, indent=2), encoding="utf-8")
    print(f"Generated synthetic PDF fixtures in {OUT.resolve()}")


if __name__ == "__main__":
    main()
