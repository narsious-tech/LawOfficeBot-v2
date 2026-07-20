from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any


class PdfDependencyUnavailable(RuntimeError):
    """Raised when the optional ReportLab PDF dependency is unavailable."""


def _s(v: Any) -> str:
    return " ".join(str(v or "").split())


def build_causelist_pdf(
    groups: list[dict[str, Any]],
    target_date: date,
    blackout_dates: list[str],
    out_path: str,
) -> str:
    """Build the Legal-size printable cause list.

    ReportLab is imported lazily so a missing optional PDF dependency never
    prevents the Telegram bot from starting. Railway should install ReportLab
    through requirements.txt, but callers may safely fall back to text output.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import legal
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "reportlab" or (exc.name or "").startswith("reportlab."):
            raise PdfDependencyUnavailable(
                "Printable PDF support is temporarily unavailable because "
                "ReportLab is not installed."
            ) from exc
        raise

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        out_path,
        pagesize=legal,
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "title",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=15,
        alignment=TA_CENTER,
        spaceAfter=2,
    )
    sub = ParagraphStyle(
        "sub",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
    )
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    court_style = ParagraphStyle(
        "court",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.6,
        leading=10,
    )
    story = [
        Paragraph("FROM THE OFFICE OF SH. AJAY CHAWLA", title),
        Paragraph(
            "ADVOCATE, LUDHIANA | Chamber No. 247 | Regn. No. P/522/2010",
            sub,
        ),
        Spacer(1, 2 * mm),
        Paragraph(f"CAUSE LIST FOR: {target_date.strftime('%d-%m-%Y (%a)')}", sub),
        Spacer(1, 2 * mm),
        Paragraph(
            "<b>Blackout Dates:</b> "
            + (", ".join(blackout_dates) if blackout_dates else "None recorded"),
            small,
        ),
        Spacer(1, 2 * mm),
    ]
    serial = 1
    for group in groups:
        court = _s(group.get("court_name")) or "Court not recorded"
        judge = _s(group.get("judge_name")) or "Judge not recorded"
        floor = _s(group.get("floor")) or "-"
        room = _s(group.get("room")) or "-"
        data = [[Paragraph(
            f"<b>{judge}</b> ({court}) | Floor: {floor} | Room: {room}",
            court_style,
        )]]
        table = Table(data, colWidths=[190 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E8EEF7")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        rows = [table]
        for case in group.get("cases") or []:
            number = _s(case.get("case_number")) or "Case number not recorded"
            case_title = _s(case.get("case_title")) or "Title not recorded"
            previous = _s(case.get("previous_date"))
            stage = _s(case.get("stage")) or "Purpose not recorded"
            detail = (
                f"<b>{serial}. {number}</b> {case_title}"
                + (f" ({previous})" if previous else "")
                + f" - {stage}"
            )
            rows.append(Paragraph(detail, small))
            serial += 1
        story.append(KeepTogether(rows))
        story.append(Spacer(1, 1.3 * mm))
    doc.build(story)
    return out_path
