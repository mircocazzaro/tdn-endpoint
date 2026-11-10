"""Esportazione dei risultati delle query (SQL e SPARQL) in CSV, Excel e PDF.

Le funzioni ricevono colonne e righe gia' calcolate dalla view: non eseguono
query e non conoscono il database. I valori mancanti (None, NaN, NaT) diventano
celle vuote.
"""

import csv
import datetime as dt
import io
import re
from xml.sax.saxutils import escape

import pandas as pd

EXPORT_FORMATS = ('csv', 'xlsx', 'pdf')

CONTENT_TYPES = {
    'csv': 'text/csv; charset=utf-8',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pdf': 'application/pdf',
}

# Excel interpreta come formula le stringhe che iniziano con questi caratteri.
_FORMULA_PREFIXES = ('=', '+', '-', '@')
_NUMBER = re.compile(r'^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$')


def _missing(value):
    try:
        return value is None or bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value):
    return '' if _missing(value) else str(value)


def _csv_cell(value):
    text = _text(value)
    # Una stringa che Excel leggerebbe come formula viene resa testo con un apice.
    if text.startswith(_FORMULA_PREFIXES) and not _NUMBER.match(text):
        return "'" + text
    return text


def to_csv(columns, rows):
    """CSV con separatore ';' (Excel in italiano) e BOM UTF-8 (accenti corretti)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow([_text(c) for c in columns])
    for row in rows:
        writer.writerow([_csv_cell(v) for v in row])
    return (chr(0xFEFF) + buf.getvalue()).encode('utf-8')


def to_xlsx(columns, rows, sheet_title='Risultati'):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]

    header = [_text(c) for c in columns]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    widths = [min(len(h), 60) for h in header]
    for row in rows:
        values = []
        for v in row:
            if _missing(v):
                values.append(None)
            elif isinstance(v, dt.datetime) and v.tzinfo is not None:
                # Excel non accetta datetime con fuso orario: resta testo.
                values.append(str(v))
            elif isinstance(v, (bool, int, float, dt.date)):
                values.append(v)
            else:
                values.append(str(v))
        ws.append(values)
        for i, v in enumerate(values[:len(widths)]):
            if isinstance(v, str):
                # Testo mai interpretato come formula, anche se inizia con '='.
                ws.cell(row=ws.max_row, column=i + 1).data_type = 's'
                widths[i] = max(widths[i], min(len(v), 60))

    ws.freeze_panes = 'A2'
    if rows and header:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{ws.max_row}"
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(w + 2, 8)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(title, columns, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle('cell', parent=styles['BodyText'], fontSize=7, leading=8.5)
    head_style = ParagraphStyle('head', parent=cell_style, fontName='Helvetica-Bold')

    page_w, _ = landscape(A4)
    margin = 28
    n = max(len(columns), 1)

    # Larghezza proporzionale alla lunghezza del contenuto (su un campione di righe).
    sample = [[_text(v) for v in row] for row in rows[:200]]
    weights = []
    for i, col in enumerate(columns):
        longest = max([len(_text(col))] + [len(r[i]) for r in sample if i < len(r)])
        weights.append(min(max(longest, 4), 40))
    total = float(sum(weights)) or 1.0
    col_widths = [(page_w - 2 * margin) * w / total for w in weights]

    data = [[Paragraph(escape(_text(c)), head_style) for c in columns]]
    for row in rows:
        data.append([Paragraph(escape(_text(v)), cell_style) for v in row])

    table = Table(data, colWidths=col_widths if columns else [page_w - 2 * margin],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9ecef')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))

    story = [
        Paragraph(escape(title), styles['Heading2']),
        Paragraph(f"{len(rows)} rows", styles['Normal']),
        Spacer(1, 8),
        table,
    ]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title=title,
                            leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin)
    doc.build(story)
    return buf.getvalue()


def render(fmt, title, columns, rows):
    """Restituisce i byte del file nel formato richiesto."""
    if fmt == 'csv':
        return to_csv(columns, rows)
    if fmt == 'xlsx':
        return to_xlsx(columns, rows, sheet_title=title)
    if fmt == 'pdf':
        return to_pdf(title, columns, rows)
    raise ValueError(f"unsupported export format: {fmt}")
