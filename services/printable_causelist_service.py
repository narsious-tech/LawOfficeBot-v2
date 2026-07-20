from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Any
from reportlab.lib.pagesizes import legal
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.lib.units import mm


def _s(v: Any) -> str:
    return " ".join(str(v or "").split())


def build_causelist_pdf(groups: list[dict[str, Any]], target_date: date, blackout_dates: list[str], out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(out_path, pagesize=legal, rightMargin=10*mm, leftMargin=10*mm, topMargin=10*mm, bottomMargin=10*mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle('title', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=13, leading=15, alignment=TA_CENTER, spaceAfter=2)
    sub = ParagraphStyle('sub', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=TA_CENTER)
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, leading=10)
    court_style = ParagraphStyle('court', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.6, leading=10)
    story = [
        Paragraph('FROM THE OFFICE OF SH. AJAY CHAWLA', title),
        Paragraph('ADVOCATE, LUDHIANA | Chamber No. 247 | Regn. No. P/522/2010', sub),
        Spacer(1, 2*mm),
        Paragraph(f'CAUSE LIST FOR: {target_date.strftime("%d-%m-%Y (%a)")}', sub),
        Spacer(1, 2*mm),
        Paragraph('<b>Blackout Dates:</b> ' + (', '.join(blackout_dates) if blackout_dates else 'None recorded'), small),
        Spacer(1, 2*mm),
    ]
    serial = 1
    for group in groups:
        court = _s(group.get('court_name')) or 'Court not recorded'
        judge = _s(group.get('judge_name')) or 'Judge not recorded'
        floor = _s(group.get('floor')) or '-'
        room = _s(group.get('room')) or '-'
        data = [[Paragraph(f'<b>{judge}</b> ({court}) | Floor: {floor} | Room: {room}', court_style)]]
        t = Table(data, colWidths=[190*mm])
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#E8EEF7')),('BOX',(0,0),(-1,-1),0.5,colors.grey),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
        rows = [t]
        for c in group.get('cases') or []:
            no = _s(c.get('case_number')) or 'Case number not recorded'
            title_text = _s(c.get('case_title')) or 'Title not recorded'
            prev = _s(c.get('previous_date'))
            stage = _s(c.get('stage')) or 'Purpose not recorded'
            detail = f'<b>{serial}. {no}</b> {title_text}' + (f' ({prev})' if prev else '') + f' - {stage}'
            rows.append(Paragraph(detail, small)); serial += 1
        story.append(KeepTogether(rows)); story.append(Spacer(1,1.3*mm))
    doc.build(story)
    return out_path
