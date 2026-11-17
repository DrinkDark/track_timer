"""PDF results: one table per race with the photo-finish strip (reportlab)."""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build_pdf(session: dict, races: list[dict], data_dir: Path) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"Results - {session['name']}")
    styles = getSampleStyleSheet()
    story = [Paragraph(f"Results: {session['name']}", styles["Title"]),
             Paragraph(session.get("date") or "", styles["Normal"]), Spacer(1, 6 * mm)]
    shown = [r for r in races if r["status"] in ("review", "locked")]
    for i, race in enumerate(shown):
        story.append(Paragraph(f"{race['event']} - heat {race['heat']}"
                               + ("" if race["status"] == "locked" else " (not locked)"), styles["Heading2"]))
        data = [["Place", "Lane", "Bib", "Name", "Club", "Time", "Status"]]
        entries = sorted(race["entries"], key=lambda e: (e["place"] is None, e["place"] or 0, e["lane"]))
        for e in entries:
            data.append([e["place"] or "", e["lane"], e["bib"], e["name"], e["club"], e["time"], e["status"]])
        t = Table(data, repeatRows=1, colWidths=[14 * mm, 14 * mm, 16 * mm, 55 * mm, 40 * mm, 22 * mm, 18 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (5, 1), (5, -1), "RIGHT"),
        ]))
        story.append(t)
        strip = data_dir / "races" / str(race["id"]) / "strip.jpg"
        if strip.exists():
            story += [Spacer(1, 3 * mm), Paragraph("Photo finish (time runs left to right)", styles["Italic"])]
            story.append(Image(str(strip), width=175 * mm, height=45 * mm))
        if (i + 1) % 2 == 0 and i + 1 < len(shown):
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 8 * mm))
    if not shown:
        story.append(Paragraph("No recorded races yet.", styles["Normal"]))
    doc.build(story)
    return buf.getvalue()
