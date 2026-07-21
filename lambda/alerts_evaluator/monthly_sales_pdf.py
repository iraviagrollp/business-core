"""
monthly_sales_pdf — evaluator-only PDF renderer for the Monthly Sales report.

Public surface
--------------
render_monthly_sales_pdf(data: dict) -> bytes
    data    : dict returned by monthly_sales.compute_monthly_sales()
    returns : raw PDF bytes (A4 portrait)

Requires: reportlab (lambda/alerts_evaluator/requirements.txt).
This module is NOT imported by the api Lambda; the api Lambda has no PDF deps.

Design (rebrand 2026-07-11; letterhead/footer/font/header-band restyled
2026-07-20 to match the Purchase Order house design — see `letterhead.py`,
ported from procurement_api/po_pdf.py)
----------------------------
Sections, in order:
  1. Shared letterhead (logo + centered "IRAVI AGRO LIFE LLP" + orange
     tagline + identity line + green/orange double-rule) via letterhead.py,
     followed by this report's own title row: bold subtitle centre /
     "Date: DD-MM-YYYY" + "(Value In Lakhs)" right — NOT part of the shared
     letterhead itself (mirrors how po_pdf.py appends its own title/box row
     after the shared header).
  2. "DAILY NET SALES" — DATE | AP | TS | SUB TOTAL. PROJECTIONS row (shaded),
     31 day rows (DD-MM-YYYY; future blank; zero '-'; negative in parens),
     G. TOTAL row (green header band), EXCESS / SHORT row (shaded,
     leading-minus negatives).
  3. "ANNUAL POSITION & CUMULATIVE SALES (UP TO {prev_month_label_full})" —
     two-row header: STATE | Actual Sales {prev_fy} | Annual Target {cur_fy} |
     UP TO {prev_month_label_full} (spans 4: {prev_fy} | {cur_fy} | DIFF | GROWTH %).
     Rows AP, TS, bold-shaded SUB TOT.
  4. Two small side-by-side tables: "{month} MONTH ONLY" and
     "CUMULATIVE — UP TO / AS ON DATE", each STATE | col | col | DIFF,
     rows AP / TS / SUB TOT.

All money values are formatted in lakhs (raw / 100 000) to 2 dp with Indian-style
thousands grouping; '-' for zero/blank. This report uses no ₹ glyph and no
em-dash in report body text (only the HTML entity `&mdash;` in one sub-heading,
which is safe under Helvetica/WinAnsiEncoding — cp1252 code point 0x97 — and
was already Helvetica-rendered before this restyle), so no glyph-substitution
was needed here; the Lambda already used Helvetica as the primary font.
"""

from __future__ import annotations

from datetime import date as _date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import letterhead

# ── constants ─────────────────────────────────────────────────────────────────
_TOTAL_BG_COLOR = colors.HexColor('#f0f0f0')   # light grey for shaded/total rows
_ALT_ROW_COLOR  = colors.HexColor('#fafafa')   # subtle zebra stripe on even data rows
_CELL_BORDER    = colors.HexColor('#cccccc')

_PAGE_W, _PAGE_H = A4                          # 595.27 pt x 841.89 pt (portrait)
_MARGIN          = 1.0 * cm                    # left and right
_CONTENT_W       = _PAGE_W - 2 * _MARGIN       # approx 538 pt usable width


# ── formatting helpers ────────────────────────────────────────────────────────

def _indian_group(int_str: str) -> str:
    """Group an unsigned integer digit string Indian-style (e.g. '1234567' -> '12,34,567')."""
    if len(int_str) <= 3:
        return int_str
    tail = int_str[-3:]
    head = int_str[:-3]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ','.join(groups) + ',' + tail


def _lakhs_abs(value: float) -> str:
    """Format |value| / 100 000 to 2 dp with Indian-style thousands grouping."""
    lakhs = abs(value) / 100_000
    formatted = f"{lakhs:.2f}"
    int_part, dec_part = formatted.split('.')
    return f"{_indian_group(int_part)}.{dec_part}"


def _fmt_date(date_str: str) -> str:
    """'YYYY-MM-DD' -> 'DD-MM-YYYY', e.g. '01-07-2026'."""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        return date_str


def _cell_daily(day_date_str: str, value: float, as_on_date: str) -> str:
    """Daily-table value cell.

    Rules:
      day_date_str > as_on_date  ->  ""    (future day — leave blank)
      value == 0.0               ->  "-"   (no sales on this day)
      value < 0.0                ->  "(x.xx)"  (negative in parentheses)
      else                       ->  "x.xx"    (lakhs, 2 dp)
    """
    if day_date_str > as_on_date:
        return ""
    if value == 0:
        return "-"
    s = _lakhs_abs(value)
    return f"({s})" if value < 0 else s


def _cell_plain(value: float) -> str:
    """Generic lakhs value cell: zero -> '-'; negative -> leading minus."""
    if value == 0:
        return "-"
    s = _lakhs_abs(value)
    return f"-{s}" if value < 0 else s


def _cell_growth(value) -> str:
    """Growth % cell: None -> '-'; else signed 2 dp number (no % sign)."""
    if value is None:
        return "-"
    return f"-{abs(value):.2f}" if value < 0 else f"{value:.2f}"


# ── shared style helpers ──────────────────────────────────────────────────────

def _ps(name: str, font: str, size: float, align: int,
        color=colors.black, leading: float | None = None) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=font,
        fontSize=size,
        alignment=align,
        leading=leading or (size + 1),
        textColor=color,
    )


def _base_tbl_cmds() -> list:
    """Grid / padding / fontsize commands shared by every table in this report."""
    return [
        ("FONTSIZE",      (0, 0), (-1, -1), 7),
        ("GRID",          (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]


# ── public API ────────────────────────────────────────────────────────────────

def render_monthly_sales_pdf(data: dict) -> bytes:
    """
    Render the Monthly Sales report as a PDF and return raw bytes.

    Parameters
    ----------
    data : dict returned by monthly_sales.compute_monthly_sales()

    Returns
    -------
    bytes : raw PDF content suitable for attaching to an SES email
    """
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=0.8 * cm,
        bottomMargin=1.4 * cm,      # footer draws at 0.4-0.7 cm; 1.4 cm leaves clearance
        title=f"IAL Monthly Sales - {data['month_label']}",
        author="IRAVI AGRO LIFE LLP",
    )

    # ── Paragraph styles ──────────────────────────────────────────────────────
    subtitle_style = _ps("IALSubtitle", "Helvetica-Bold", 11, TA_CENTER, leading=13)
    right_style = _ps("IALRight", "Helvetica", 7, TA_RIGHT, leading=9)
    section_style = ParagraphStyle(
        "IALSection",
        fontName="Helvetica-Bold",
        fontSize=8,
        alignment=TA_CENTER,
        spaceBefore=8,
        spaceAfter=3,
    )
    sub_section_style = ParagraphStyle(
        "IALSubSection",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        alignment=TA_CENTER,
        spaceBefore=2,
        spaceAfter=2,
    )

    hdr_c = _ps("IALHdrC", "Helvetica-Bold", 6.5, TA_CENTER, color=colors.white)
    hdr_l = _ps("IALHdrL", "Helvetica-Bold", 6.5, TA_LEFT,   color=colors.white)

    dat_c = _ps("IALDatC", "Helvetica", 7, TA_CENTER)
    dat_l = _ps("IALDatL", "Helvetica", 7, TA_LEFT)

    tot_c = _ps("IALTotC", "Helvetica-Bold", 7, TA_CENTER)
    tot_l = _ps("IALTotL", "Helvetica-Bold", 7, TA_LEFT)

    gtot_c = _ps("IALGTotC", "Helvetica-Bold", 7, TA_CENTER, color=colors.white)
    gtot_l = _ps("IALGTotL", "Helvetica-Bold", 7, TA_LEFT,   color=colors.white)

    # ── Shared letterhead + report title row ──────────────────────────────────
    today_str = _date.today().strftime('%d-%m-%Y')

    right_col_w = 2.8 * cm

    title_tbl = Table(
        [[
            "",
            Paragraph(
                f"STATE WISE NET SALES (WITHOUT TAX) FOR THE MONTH OF {data['month_label']}",
                subtitle_style,
            ),
            [
                Paragraph(f"Date: {today_str}", right_style),
                Paragraph("(Value In Lakhs)", right_style),
            ],
        ]],
        colWidths=[right_col_w, _CONTENT_W - 2 * right_col_w, right_col_w],
    )
    title_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    elements: list = list(letterhead.build_header(_CONTENT_W)) + [title_tbl, Spacer(1, 5)]

    # ── Section 2: DAILY NET SALES ───────────────────────────────────────────
    as_on = data["as_on_date"]
    gt    = data["grand_total"]
    proj  = data["projections"]
    es    = data["excess_short"]

    elements.append(Paragraph("DAILY NET SALES", section_style))

    date_col_w  = 2.8 * cm
    val_col_w   = (_CONTENT_W - date_col_w) / 3
    daily_col_w = [date_col_w, val_col_w, val_col_w, val_col_w]

    day_rows: list[list] = [[
        Paragraph("DATE", hdr_c), Paragraph("AP", hdr_c),
        Paragraph("TS", hdr_c), Paragraph("SUB TOTAL", hdr_c),
    ]]

    proj_row_idx = len(day_rows)
    day_rows.append([
        Paragraph("PROJECTIONS", tot_l),
        Paragraph(_cell_plain(proj["andhra"]), tot_c),
        Paragraph(_cell_plain(proj["telangana"]), tot_c),
        Paragraph(_cell_plain(proj["total"]), tot_c),
    ])

    day_start_idx = len(day_rows)
    for day in data["days"]:
        d_str = day["date"]
        day_rows.append([
            Paragraph(_fmt_date(d_str), dat_c),
            Paragraph(_cell_daily(d_str, day["andhra"],    as_on), dat_c),
            Paragraph(_cell_daily(d_str, day["telangana"], as_on), dat_c),
            Paragraph(_cell_daily(d_str, day["total"],     as_on), dat_c),
        ])
    day_end_idx = len(day_rows) - 1

    gtotal_row_idx = len(day_rows)
    day_rows.append([
        Paragraph("G. TOTAL", gtot_l),
        Paragraph(_cell_plain(gt["andhra"]), gtot_c),
        Paragraph(_cell_plain(gt["telangana"]), gtot_c),
        Paragraph(_cell_plain(gt["total"]), gtot_c),
    ])

    es_row_idx = len(day_rows)
    day_rows.append([
        Paragraph("EXCESS / SHORT", tot_l),
        Paragraph(_cell_plain(es["andhra"]), tot_c),
        Paragraph(_cell_plain(es["telangana"]), tot_c),
        Paragraph(_cell_plain(es["total"]), tot_c),
    ])

    day_cmds = _base_tbl_cmds() + [
        ("BACKGROUND", (0, 0), (-1, 0), letterhead.GREEN),
        ("BACKGROUND", (0, proj_row_idx), (-1, proj_row_idx), _TOTAL_BG_COLOR),
        ("ROWBACKGROUNDS", (0, day_start_idx), (-1, day_end_idx), [colors.white, _ALT_ROW_COLOR]),
        ("BACKGROUND", (0, gtotal_row_idx), (-1, gtotal_row_idx), letterhead.GREEN),
        ("BACKGROUND", (0, es_row_idx), (-1, es_row_idx), _TOTAL_BG_COLOR),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]

    day_tbl = Table(day_rows, colWidths=daily_col_w, repeatRows=1)
    day_tbl.setStyle(TableStyle(day_cmds))
    elements.append(day_tbl)

    # ── Section 3: ANNUAL POSITION & CUMULATIVE SALES ────────────────────────
    ap = data["annual_position"]
    prev_fy_label = ap["prev_fy_label"]
    cur_fy_label  = ap["cur_fy_label"]
    prev_m_full   = ap["prev_month_label_full"]
    actual_prev   = ap["actual_sales_prev_fy"]
    target_cur    = ap["annual_target_cur_fy"]
    utp           = ap["upto_prev_month"]

    elements.append(Paragraph(
        f"ANNUAL POSITION &amp; CUMULATIVE SALES (UP TO {prev_m_full})", section_style,
    ))

    state_w = 1.8 * cm
    ap_val_w = (_CONTENT_W - state_w) / 6
    ap_col_w = [state_w] + [ap_val_w] * 6

    ap_row0 = [
        Paragraph("STATE", hdr_c),
        Paragraph(f"Actual Sales {prev_fy_label}", hdr_c),
        Paragraph(f"Annual Target {cur_fy_label}", hdr_c),
        Paragraph(f"UP TO {prev_m_full}", hdr_c),
        "", "", "",
    ]
    ap_row1 = [
        "", "", "",
        Paragraph(prev_fy_label, hdr_c),
        Paragraph(cur_fy_label, hdr_c),
        Paragraph("DIFF", hdr_c),
        Paragraph("GROWTH %", hdr_c),
    ]

    def _ap_data_row(label, actual_v, target_v, utp_prev_v, utp_cur_v, diff_v, growth_v):
        return [
            Paragraph(label, tot_l if label == "SUB TOT" else dat_l),
            Paragraph(_cell_plain(actual_v),  tot_c if label == "SUB TOT" else dat_c),
            Paragraph(_cell_plain(target_v),  tot_c if label == "SUB TOT" else dat_c),
            Paragraph(_cell_plain(utp_prev_v), tot_c if label == "SUB TOT" else dat_c),
            Paragraph(_cell_plain(utp_cur_v),  tot_c if label == "SUB TOT" else dat_c),
            Paragraph(_cell_plain(diff_v),     tot_c if label == "SUB TOT" else dat_c),
            Paragraph(_cell_growth(growth_v),  tot_c if label == "SUB TOT" else dat_c),
        ]

    ap_rows = [ap_row0, ap_row1]
    ap_rows.append(_ap_data_row(
        "AP", actual_prev["andhra"], target_cur["andhra"],
        utp["prev_fy"]["andhra"], utp["cur_fy"]["andhra"],
        utp["diff"]["andhra"], utp["growth_pct"]["andhra"],
    ))
    ap_rows.append(_ap_data_row(
        "TS", actual_prev["telangana"], target_cur["telangana"],
        utp["prev_fy"]["telangana"], utp["cur_fy"]["telangana"],
        utp["diff"]["telangana"], utp["growth_pct"]["telangana"],
    ))
    ap_subtot_idx = len(ap_rows)
    ap_rows.append(_ap_data_row(
        "SUB TOT", actual_prev["total"], target_cur["total"],
        utp["prev_fy"]["total"], utp["cur_fy"]["total"],
        utp["diff"]["total"], utp["growth_pct"]["total"],
    ))

    ap_cmds = _base_tbl_cmds() + [
        ("SPAN", (0, 0), (0, 1)),
        ("SPAN", (1, 0), (1, 1)),
        ("SPAN", (2, 0), (2, 1)),
        ("SPAN", (3, 0), (6, 0)),
        ("BACKGROUND", (0, 0), (-1, 1), letterhead.GREEN),
        ("BACKGROUND", (0, ap_subtot_idx), (-1, ap_subtot_idx), _TOTAL_BG_COLOR),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 2), (0, -1), "LEFT"),
    ]

    ap_tbl = Table(ap_rows, colWidths=ap_col_w, repeatRows=2)
    ap_tbl.setStyle(TableStyle(ap_cmds))
    elements.append(ap_tbl)

    # ── Section 4: two small side-by-side tables ─────────────────────────────
    mo  = data["month_only"]
    cao = data["cumulative_as_on"]

    def _small_table(header_cells, rows_data, subtot_label="SUB TOT"):
        n_data_rows = len(rows_data)
        subtot_row_idx = n_data_rows  # header is row 0
        tbl_rows = [[Paragraph(h, hdr_c) for h in header_cells]]
        for i, (label, v0, v1, v2) in enumerate(rows_data):
            is_tot = (label == subtot_label)
            style_l = tot_l if is_tot else dat_l
            style_c = tot_c if is_tot else dat_c
            tbl_rows.append([
                Paragraph(label, style_l),
                Paragraph(_cell_plain(v0), style_c),
                Paragraph(_cell_plain(v1), style_c),
                Paragraph(_cell_plain(v2), style_c),
            ])
        cmds = _base_tbl_cmds() + [
            ("BACKGROUND", (0, 0), (-1, 0), letterhead.GREEN),
            ("BACKGROUND", (0, subtot_row_idx), (-1, subtot_row_idx), _TOTAL_BG_COLOR),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ]
        half_w = (_CONTENT_W - 0.4 * cm) / 2
        st_w   = half_w * 0.28
        val_w  = (half_w - st_w) / 3
        tbl = Table(tbl_rows, colWidths=[st_w, val_w, val_w, val_w])
        tbl.setStyle(TableStyle(cmds))
        return tbl

    mo_tbl = _small_table(
        ["STATE", prev_fy_label, cur_fy_label, "DIFF"],
        [
            ("AP", mo["prev_fy"]["andhra"], mo["cur_fy"]["andhra"], mo["diff"]["andhra"]),
            ("TS", mo["prev_fy"]["telangana"], mo["cur_fy"]["telangana"], mo["diff"]["telangana"]),
            ("SUB TOT", mo["prev_fy"]["total"], mo["cur_fy"]["total"], mo["diff"]["total"]),
        ],
    )

    cao_tbl = _small_table(
        ["STATE", f'UPTO {cao["month_abbr"]} {cao["prev_fy_label"]}',
         f'AS ON DATE {cao["month_abbr"]} {cao["cur_fy_label"]}', "DIFF"],
        [
            ("AP", cao["prev_fy_upto"]["andhra"], cao["cur_fy_as_on"]["andhra"], cao["diff"]["andhra"]),
            ("TS", cao["prev_fy_upto"]["telangana"], cao["cur_fy_as_on"]["telangana"], cao["diff"]["telangana"]),
            ("SUB TOT", cao["prev_fy_upto"]["total"], cao["cur_fy_as_on"]["total"], cao["diff"]["total"]),
        ],
    )

    mo_heading  = Paragraph(f'{mo["month_name"]} MONTH ONLY', sub_section_style)
    cao_heading = Paragraph("CUMULATIVE &mdash; UP TO / AS ON DATE", sub_section_style)

    half_col_w = _CONTENT_W / 2
    outer_tbl = Table(
        [[[mo_heading, mo_tbl], [cao_heading, cao_tbl]]],
        colWidths=[half_col_w, half_col_w],
    )
    outer_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(outer_tbl)

    # ── Build PDF with footer on every page ───────────────────────────────────
    doc.build(elements, onFirstPage=letterhead.draw_footer, onLaterPages=letterhead.draw_footer)
    return buffer.getvalue()
