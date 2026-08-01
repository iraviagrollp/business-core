# IRAVI AGRO LIFE LLP — Business Core

## Instructions for Claude

- After every conversation where decisions are made, code is written, or plans change — update this file.
- Keep **What Is Built** accurate: tick items as completed.
- Keep **What Is Next** current: remove completed items, add newly discovered tasks.
- If a technical decision changes, update the relevant section immediately.
- This file is the single source of truth for business-core across sessions.
- **After every code change** update this file before closing the task.
- **Cross-project sync:** When a Lambda reaches a milestone, also tick the corresponding checkbox in `D:\Projects\Iravi\IaC\CLAUDE.md`.

---

## Project Overview

Processing logic for the IRAVI Dashboard. Contains all Lambda functions that power the nightly data pipeline and the API layer.

**Related projects:**
- Infrastructure (Terraform) → `D:\Projects\Iravi\IaC\`
- File Sync Agent → `D:\Projects\Iravi\FileSyncAgent\`

---

## Repository Layout

```
business-core/
├── CLAUDE.md
├── README.md
├── .gitignore
└── lambda/
    ├── etl_stocks/           ← ETL: parse StockReport csv (+ rates xlsx) → RDS snapshot_stock [COMPLETE]
    │   ├── handler.py        ← Lambda entry point (S3 trigger)
    │   ├── process.py        ← core transform logic (no S3/Lambda deps)
    │   ├── run_local.py      ← local test runner (STALE — still points at old xlsx sample, see etl_stocks section)
    │   ├── tests/            ← test_process_csv.py + gen_fixture.py + fixtures/ (added 2026-07-16)
    │   └── requirements.txt
    ├── etl_sales/            ← ETL: parse sales xlsx → RDS fact_sales [STUB]
    │   ├── handler.py
    │   ├── requirements.txt
    │   └── sample_data/      ← test xlsx files
    ├── etl_customer_ledger/  ← ETL: parse Ledger All Accounts xlsx → RDS customer_ledger [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_customer_accounts/ ← ETL: parse Customer Accounts Export xlsx → RDS customer_details [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_appendix_b_x11/  ← ETL: parse Barcodes Masters xlsx → RDS appendix_b_x11_stock [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_appendix_b_x11_purchase/ ← ETL: parse AppendixPurchaseReport xlsx → RDS appendix_b_x11_stock_ledger (in_out=In) [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_appendix_b_x11_purchase_return/ ← ETL: parse AppendixPurReturn xlsx → RDS appendix_b_x11_stock_ledger (in_out=Out) [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_appendix_b_x11_sale/ ← ETL: parse AppendixSale xlsx → RDS appendix_b_x11_stock_ledger (in_out=Out) + sales (sales_return=N) [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_appendix_b_x11_sale_return/ ← ETL: parse AppendixRetSales xlsx → RDS appendix_b_x11_stock_ledger (in_out=In) + sales (sales_return=Y) [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_supplier_accounts/ ← ETL: parse Supplier Accounts Export File xlsx → RDS supplier_accounts [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_supplier_ledger/  ← ETL: parse Ledger All Accounts xlsx (supplier rows) → RDS supplier_ledger [COMPLETE]
    │   ├── handler.py        ← EventBridge-triggered; read-only on S3; fallback to processed/raw/ if raw gone
    │   └── requirements.txt
    ├── whatsapp_notifier/    ← S3 trigger on notifications/pending/ → phase 1 moves to notifications/processed/ → phase 2 sends WhatsApp [PHASE 1 COMPLETE]
    │   └── handler.py
    ├── redis_updater/        ← Cache: RDS → ElastiCache Redis (stocks + ledger range done)
    │   ├── handler.py
    │   └── requirements.txt
    ├── api/                  ← API: dashboard reads + POST /notify + RBAC auth/admin + alerts admin API [COMPLETE]
    │   ├── handler.py        ← routing, data endpoints, /auth/* + /admin/* + /alerts/* handlers
    │   │                        _handle_customer_balances_fy refactored (2026-07-06): now delegates to
    │   │                        customer_balances_fy.compute_customer_balances_fy (cache-aside unchanged;
    │   │                        endpoint JSON shape unchanged)
    │   │                        _handle_supplier_balances_fy refactored (2026-07-06): now delegates to
    │   │                        supplier_balances_fy.compute_supplier_balances_fy (cache-aside unchanged;
    │   │                        endpoint JSON shape unchanged; no code/credit_notes fields)
    │   │                        _handle_ledger_statement / _handle_supplier_ledger_statement refactored
    │   │                        (2026-07-20): now delegate to ledger_statement.compute_ledger_statement /
    │   │                        supplier_ledger_statement.compute_supplier_ledger_statement (cache-aside
    │   │                        unchanged; endpoint JSON shape unchanged). Added _pdf_response() (copied
    │   │                        from procurement_api/handler.py) + _safe_filename_part() + 6 new GET
    │   │                        .../pdf routes, each with a LOCAL `import <module>_pdf` — see
    │   │                        "api — Server-side PDF exports" section below for full detail.
    │   ├── auth.py           ← PBKDF2 password hashing + HS256 JWT (stdlib only)
    │   ├── alerts_eval.py    ← SHARED: balances evaluation + FIFO aging + field catalog + validation
    │   │                        Added 2026-07-06: FIELD_CATALOG_CUSTOMER_BALANCES_FY (fields=[], not
    │   │                        branch-scoped, accepts 0 conditions) + registered in FIELD_CATALOGS
    │   │                        Added 2026-07-06: FIELD_CATALOG_SUPPLIER_BALANCES_FY (fields=[], not
    │   │                        branch-scoped, accepts 0 conditions) + registered in FIELD_CATALOGS
    │   ├── customer_balances_fy.py ← SHARED: compute_customer_balances_fy(conn, fy_count) → dict
    │   │                        Extracted from _handle_customer_balances_fy; byte-identical copy in
    │   │                        alerts_evaluator/. (added 2026-07-06)
    │   ├── supplier_balances_fy.py ← SHARED: compute_supplier_balances_fy(conn, fy_count) → dict
    │   │                        Extracted from _handle_supplier_balances_fy; byte-identical copy in
    │   │                        alerts_evaluator/. No code field; no credit_notes field.
    │   │                        From-beginning (fy_count='all') used by alert evaluator PDF path.
    │   │                        (added 2026-07-06)
    │   │                        Active-supplier-only filter (2026-07-12): rows now restricted to
    │   │                        parties present as active (out_z IS NULL) rows in supplier_accounts
    │   │                        — `active_supplier_names = set(city_map.keys())`, party skipped via
    │   │                        early `continue` (before totals accumulate) if
    │   │                        `party.upper() not in active_supplier_names`. Exact UPPER(name) match,
    │   │                        no fuzzy matching. Fixed alerts_evaluator's city_map query in the same
    │   │                        change — it was missing `WHERE out_z IS NULL` (api/ copy had it; the
    │   │                        two files were not byte-identical before this fix).
    │   ├── monthly_sales.py / monthly_collection.py ← SHARED compute modules (unchanged by this task)
    │   ├── ledger_statement.py ← SHARED (added 2026-07-20): compute_ledger_statement(conn, account_name,
    │   │                        from_date, to_date) → dict. Extracted verbatim from
    │   │                        _handle_ledger_statement (per-voucher netting SQL unchanged).
    │   ├── supplier_ledger_statement.py ← SHARED (added 2026-07-20): compute_supplier_ledger_statement(...)
    │   │                        → dict. Extracted verbatim from _handle_supplier_ledger_statement.
    │   │                        Gained a `city` field (2026-07-21) via a new LEFT-JOIN-style lookup on
    │   │                        `supplier_accounts` (`UPPER(name)=UPPER(account_name) AND out_z IS NULL`)
    │   │                        — same treatment as `ledger_statement.py`'s customer_details lookup; see
    │   │                        "Supplier Ledger Statement PDF" entry below.
    │   ├── stocks_expiry_pdf.py ← PDF renderer for the new Stock Expiry report (added 2026-08-01),
    │   │                        landscape A4, single-header-row flat listing table (Brand, Technical,
    │   │                        Packing, Branch, Special Packing, Available Nos, Conversion Factor,
    │   │                        Available Cases, Available Qty, Entry Date, Expiry Date — no rate/
    │   │                        valuation), shared letterhead header/footer. Own module, not shared
    │   │                        with alerts_evaluator (no alert type for this report).
    │   ├── letterhead.py / pdf_fonts.py / DejaVuSans.ttf / DejaVuSans-Bold.ttf / ial-logo.png
    │   │                        ← bundled 2026-07-20: byte-identical copies from alerts_evaluator/ (see
    │   │                        that package's entries below for full docs — keep both copies in sync).
    │   │                        letterhead.py gained `draw_header(canvas, doc)` + `HEADER_TOP_PAD`/
    │   │                        `HEADER_HEIGHT` constants on 2026-07-21 — see "Customer Ledger Statement
    │   │                        PDF" entry below. NOT byte-identical to alerts_evaluator/letterhead.py
    │   │                        after this addition (additive-only; `build_header`/`draw_footer`
    │   │                        unchanged, so every OTHER consumer of this file is unaffected) — flagged
    │   │                        so a future "keep both copies in sync" pass doesn't silently drop it.
    │   ├── customer_balances_fy_pdf.py / supplier_balances_fy_pdf.py / monthly_sales_pdf.py /
    │   │   monthly_collection_pdf.py ← bundled 2026-07-20 as byte-identical copies from
    │   │                        alerts_evaluator/ (renderers themselves unmodified — same SES email
    │   │                        attachments). **NO LONGER byte-identical to their alerts_evaluator/
    │   │                        counterparts as of 2026-07-21** — each of these 4 files in `api/` (ONLY
    │   │                        `api/`, not `alerts_evaluator/`) had its letterhead header switched from
    │   │                        a page-1-only flowable (`list(letterhead.build_header(_CONTENT_W)) +
    │   │                        [...]`) to a canvas callback that repeats on every page
    │   │                        (`letterhead.draw_header` via a new per-file `_draw_header_footer(canvas,
    │   │                        doc)`, passed as both `onFirstPage`/`onLaterPages` to `doc.build`), plus
    │   │                        `topMargin` bumped from `0.6*cm`/`0.8*cm` to
    │   │                        `letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3*cm` — see
    │   │                        "Every-page letterhead header for all api/ report PDFs" entry below.
    │   │                        `alerts_evaluator/`'s 4 copies (SES email attachments) intentionally kept
    │   │                        on the OLD page-1-only flowable header — out of scope for this change,
    │   │                        flagged here so a future "keep both copies in sync" pass doesn't
    │   │                        overwrite this intentional divergence.
    │   ├── ledger_statement_pdf.py ← REWORKED (2026-07-21) to the client-approved "account statement"
    │   │                        design — see "Customer Ledger Statement PDF — client-approved redesign"
    │   │                        below for full detail (was: 2026-07-20 flat-table Dr=RED/Cr=GREEN layout,
    │   │                        now: per-FY tables, centered title, Location line, always-black balance,
    │   │                        closing-balance banner, Bank Particulars block, letterhead header
    │   │                        repeating on every page).
    │   ├── supplier_ledger_statement_pdf.py ← REWORKED (2026-07-21, same session as the every-page-header
    │   │                        pass above) from the 2026-07-20 flat-table Dr/Cr-swapped duplicate of
    │   │                        ledger_statement_pdf.py to mirror ledger_statement_pdf.py's 2026-07-21
    │   │                        "account statement" redesign, adapted for supplier/payable semantics — see
    │   │                        "Supplier Ledger Statement PDF — mirrors the customer redesign" entry
    │   │                        below. Still fully self-contained (no cross-import from
    │   │                        ledger_statement_pdf.py), matching this package's existing convention.
    │   └── requirements.txt  ← added reportlab==4.2.2 (2026-07-20), matches alerts_evaluator's convention
    └── alerts_evaluator/     ← EventBridge-triggered nightly alert evaluator (sends SES emails) [COMPLETE]
        ├── handler.py        ← lambda_handler: load due alerts → evaluate → SES send → alert_runs write
        │                        Added 2026-07-06: customer_balances_fy branch (always fires → PDF attachment)
        │                        Added 2026-07-06: supplier_balances_fy branch (always fires → PDF attachment;
        │                        no code/credit_notes; supplier footer legend differs from customer)
        │                        Added 2026-07-12: monthly_collection branch (always fires → PDF
        │                        attachment; clones the customer_balances_fy/supplier_balances_fy
        │                        unconditional pattern, NOT the conditional sales pattern — no
        │                        conditions/threshold logic; computes the CURRENT month via
        │                        `today.strftime('%Y-%m')` each run, unlike the FY branches which
        │                        always request 'all')
        ├── alerts_eval.py    ← copy of shared module (same source, duplicated per package)
        │                        Added 2026-07-06: FIELD_CATALOG_CUSTOMER_BALANCES_FY + FIELD_CATALOGS entry
        │                        Added 2026-07-06: FIELD_CATALOG_SUPPLIER_BALANCES_FY + FIELD_CATALOGS entry
        │                        Added 2026-07-12: FIELD_CATALOG_MONTHLY_COLLECTION (fields=[], not
        │                        branch-scoped, accepts 0 conditions) + FIELD_CATALOGS entry —
        │                        identical addition applied to api/alerts_eval.py to keep both in sync
        ├── monthly_collection.py ← SHARED: byte-identical copy of api/monthly_collection.py
        │                        compute_monthly_collection(conn, month_str) → dict (added 2026-07-12;
        │                        previously api-only per that module's docstring — now also consumed
        │                        by the monthly_collection alert-PDF path; keep both copies byte-identical)
        ├── monthly_collection_pdf.py ← PDF renderer for Monthly Collection, portrait A4
        │                        render_monthly_collection_pdf(data) → bytes. Modeled closely on
        │                        monthly_sales_pdf.py's 2026-07-11 layout (DAILY NET COLLECTION /
        │                        ANNUAL POSITION & CUMULATIVE COLLECTION / MONTH ONLY + CUMULATIVE
        │                        side-by-side tables), but keyed off compute_monthly_collection's
        │                        'ap'/'ts' bucket names (not monthly_sales's 'andhra'/'telangana') and
        │                        the 'actual_collections_prev_fy' annual_position sub-key. No ₹ symbol
        │                        used (Helvetica fonts, "Rs." in footer, same as monthly_sales_pdf.py);
        │                        calls pdf_fonts.register_fonts() defensively though not required for
        │                        the current field set. (added 2026-07-12)
        │                        Restyled 2026-07-20 (see letterhead.py below): shared
        │                        letterhead.build_header()/draw_footer() replace the old ad-hoc
        │                        logo+company header row and Kukatpally/AP-TS-legend footer; header
        │                        band switched #1a3c2b → letterhead.GREEN (#17452f). Report's own
        │                        subtitle/date/"(Value In Lakhs)" row now sits in its own title_tbl
        │                        directly under the shared letterhead. No ₹/em-dash glyph work needed
        │                        (already Helvetica, already no ₹ in body text).
        ├── monthly_sales.py  ← copy of shared module (byte-identical to api/monthly_sales.py)
        ├── monthly_sales_pdf.py ← PDF renderer using reportlab (evaluator-only; not in api package)
        │                        Restyled 2026-07-05: dark-green #1a3c2b headers (white text), #f0f0f0
        │                        total rows, #fafafa zebra, DD-Mon-YYYY dates, IAL logo top-left
        │                        (ial-logo.png bundled in this dir; graceful fallback if absent),
        │                        IRAVI AGRO LIFE LLP centered bold, date/value-note right, centered
        │                        bold underlined SALES ANALYSIS / MONTH ONLY section headings,
        │                        Kukatpally footer (two lines), compact 7pt / 1cm margins → single A4 page.
        │                        Bug-fixed 2026-07-06: _FOOTER_LINE2 "₹" (U+20B9) replaced with "Rs."
        │                        (Helvetica/WinAnsiEncoding cannot encode U+20B9; canvas._escape raises
        │                        KeyError: 8377 for chars outside Latin-1 when called in certain paths).
        │                        SimpleDocTemplate title em-dash "—" also replaced with ASCII "-".
        │                        Rebranded 2026-07-11: new layout — DAILY NET SALES (DATE|AP|TS|SUB
        │                        TOTAL, PROJECTIONS row, DD-MM-YYYY dates, dark-green G. TOTAL band,
        │                        EXCESS/SHORT row) + ANNUAL POSITION & CUMULATIVE SALES (two-row
        │                        spanning header, AP/TS/SUB TOT) + side-by-side MONTH ONLY /
        │                        CUMULATIVE small tables. Consumes the new targets/YoY keys added to
        │                        monthly_sales.compute_monthly_sales() the same day. See "api —
        │                        GET /reports/monthly-sales" section for the full field list.
        │                        Restyled 2026-07-20 (see letterhead.py below): shared
        │                        letterhead.build_header()/draw_footer() replace the old ad-hoc
        │                        logo+company header row and Kukatpally/AP-TS-legend footer; header
        │                        band switched #1a3c2b → letterhead.GREEN (#17452f). Report's own
        │                        subtitle/date/"(Value In Lakhs)" row now sits in its own title_tbl
        │                        directly under the shared letterhead. No glyph work needed — this
        │                        renderer was already Helvetica-primary with no ₹ in body text; the
        │                        one existing `&mdash;` sub-heading entity is safe under
        │                        Helvetica/WinAnsiEncoding (cp1252 0x97) and was left as-is.
        ├── customer_balances_fy.py ← SHARED: byte-identical copy of api/customer_balances_fy.py
        │                        compute_customer_balances_fy(conn, fy_count) → dict
        │                        (added 2026-07-06)
        ├── customer_balances_fy_pdf.py ← PDF renderer for Customer Balances (FY), landscape A4
        │                        render_customer_balances_fy_pdf(data) → bytes
        │                        Two-row header (repeatRows=2), always-visible Credit Notes column.
        │                        Indian-grouped rupee amounts (₹12,34,567.00). Landscape 1cm margins.
        │                        (added 2026-07-06)
        │                        Dr/Cr balance coloring added 2026-07-01: per-FY Balance(₹),
        │                        Balance Dr, Balance Cr cells colored in data + TOTAL rows.
        │                        Customer: Dr → RED (#cc0000), Cr → GREEN (#1a6e35). UNCHANGED by the
        │                        2026-07-20 restyle below.
        │                        Restyled 2026-07-20 to match the PO house design (see letterhead.py):
        │                        PRIMARY font switched DejaVuSans → Helvetica/Helvetica-Bold (matches
        │                        po_pdf.py); DejaVuSans is now registered ONLY for the rupee glyph, via
        │                        `_RS = letterhead.register_fonts()` — an inline
        │                        `<font name="DejaVuSans">₹</font>` Paragraph-markup token used
        │                        everywhere a bare '₹' character used to be concatenated (in `_fmt_inr`
        │                        and the 'Debit (₹)' etc. header sub-labels) — a raw ₹ char in a
        │                        Helvetica Paragraph can KeyError. The em-dash '—' zero/blank
        │                        placeholder (code/city/data cells) was replaced with a plain hyphen
        │                        '-' (cosmetic only, not a data change) to avoid depending on
        │                        Helvetica/WinAnsiEncoding's em-dash handling for a placeholder glyph.
        │                        Header band #1a3c2b → letterhead.GREEN (#17452f); old ad-hoc
        │                        logo+company+date header row and Kukatpally footer replaced by
        │                        letterhead.build_header()/draw_footer(); 'CUSTOMER BALANCES' + Date
        │                        now their own title row under the shared letterhead (not part of it).
        ├── supplier_balances_fy.py ← SHARED: byte-identical copy of api/supplier_balances_fy.py
        │                        compute_supplier_balances_fy(conn, fy_count) → dict
        │                        No code field; no credit_notes field. Sort by party name ascending.
        │                        (added 2026-07-06)
        │                        Active-supplier-only filter (2026-07-12) — see api/supplier_balances_fy.py
        │                        entry above; identical change applied here to keep both byte-identical.
        ├── supplier_balances_fy_pdf.py ← PDF renderer for Supplier Balances (FY), landscape A4
        │                        render_supplier_balances_fy_pdf(data) → bytes
        │                        Two-row header (repeatRows=2). No Code column; no Credit Notes column
        │                        (3 sub-cols per FY: Debit/Credit/Balance). Supplier footer legend text
        │                        ('Dr = Debit (payable); Cr = Credit (advance/overpayment).') was
        │                        DROPPED by the 2026-07-20 footer restyle (see below) — the Dr/Cr COLOR
        │                        semantics themselves are unchanged, only the textual footer legend.
        │                        Indian-grouped rupee amounts (₹12,34,567.00). Landscape 1cm margins.
        │                        (added 2026-07-06)
        │                        Dr/Cr balance coloring added 2026-07-01: per-FY Balance(₹),
        │                        Balance Dr, Balance Cr cells colored in data + TOTAL rows.
        │                        Supplier SWAPPED: Dr → GREEN (#1a6e35), Cr → RED (#cc0000). UNCHANGED
        │                        by the 2026-07-20 restyle below.
        │                        Restyled 2026-07-20 — identical treatment to
        │                        customer_balances_fy_pdf.py's 2026-07-20 entry above: PRIMARY font
        │                        Helvetica/Helvetica-Bold, `_RS` inline-font rupee token from
        │                        letterhead.register_fonts(), em-dash placeholders → hyphen, header
        │                        band → letterhead.GREEN, shared letterhead.build_header()/
        │                        draw_footer(), 'SUPPLIER BALANCES' + Date as its own title row.
        ├── letterhead.py     ← SHARED (added 2026-07-20): IAL letterhead (header + footer) for all 4
        │                        report PDF renderers above, ported from
        │                        procurement_api/po_pdf.py's _header()/_draw_footer()/_styles() so
        │                        every emailed report PDF matches the Purchase Order house design
        │                        (po_pdf.py itself is untouched — no cross-Lambda import; this is an
        │                        independent adaptation living in this package). Exposes: palette
        │                        constants GREEN(#17452f)/GREEN2(#2d5c44)/ORANGE(#c8641e)/MUTED(#555555)/
        │                        RULE(#c9c9c9)/BODY(#1c1c1c); BASE_FONT/BOLD_FONT = 'Helvetica'/
        │                        'Helvetica-Bold'; register_fonts() → rupee inline-font token (delegates
        │                        actual TTFont registration to pdf_fonts.register_fonts()); build_header(dw)
        │                        → logo + centered company name/tagline/identity-line + green/orange
        │                        double-rule flowables (callers append their own report title/date row
        │                        immediately after — not part of the shared letterhead); draw_footer(canvas,
        │                        doc) → 0.6pt rule + registered-office line + "This document is
        │                        computer-generated and is valid without signature." (reworded from
        │                        po_pdf.py's PO-specific wording), reads doc.pagesize/leftMargin/
        │                        rightMargin so it works on both portrait and landscape documents.
        ├── pdf_fonts.py      ← SHARED: idempotent register_fonts() — registers DejaVuSans and
        │                        DejaVuSans-Bold with reportlab pdfmetrics; falls back to Helvetica
        │                        with warning on failure. Fixes ₹ (U+20B9) + — (U+2014) KeyError
        │                        that crashes Helvetica-based doc.build() on Lambda.
        │                        Reused by both Customer and Supplier Balances (FY) PDF renderers,
        │                        and (2026-07-20) by letterhead.py (for the rupee-glyph token used by
        │                        all 4 report PDF renderers).
        │                        (added 2026-07-06)
        ├── DejaVuSans.ttf    ← bundled Unicode TTF (738 KB); source: matplotlib mpl-data/fonts/ttf
        ├── DejaVuSans-Bold.ttf ← bundled Unicode TTF-Bold (688 KB); same source
        ├── ial-logo.png      ← bundled logo asset (copy of iravi-ui/public/ial-logo.png); ships in
        │                        the archive_file zip; no IaC change required
        └── requirements.txt  ← psycopg2-binary==2.9.9 + reportlab==4.2.2 (boto3 from runtime)
```

---

## Lambda Deployment

Lambdas are packaged by Terraform using the `archive_file` data source — no separate build step.

Terraform configs live in:
```
D:\Projects\Iravi\IaC\terraform\environments\production\
├── lambda_etl_sales.tf                      ← etl_sales + the SHARED S3 bucket notification (fans out by prefix; also enables EventBridge)
├── lambda_etl_stocks.tf
├── lambda_etl_customer_ledger.tf
├── lambda_etl_customer_accounts.tf
├── lambda_etl_appendix_b_x11.tf
├── lambda_etl_appendix_b_x11_purchase.tf
├── lambda_etl_appendix_b_x11_purchase_return.tf
├── lambda_etl_appendix_b_x11_sale.tf
├── lambda_etl_appendix_b_x11_sale_return.tf
├── lambda_etl_supplier_accounts.tf
├── lambda_etl_supplier_ledger.tf            ← EventBridge trigger on raw/Ledger (read-only S3)
├── lambda_whatsapp_notifier.tf
├── lambda_alerts_evaluator.tf               ← EventBridge rate(15m); reportlab layer; SES send
├── lambda_redis_updater.tf
├── lambda_api.tf                            ← + /reports/* routes, /alerts* routes, POST /admin/cache/flush
└── ses.tf                                   ← SES domain identity + DKIM for alert emails
```

Deploy via the GitHub Actions pipeline (merge to main → apply runs automatically).

**Dependencies are packaged automatically** — The GitHub Actions workflow runs `pip install` into `.lambda_layers/<lambda>/python/` before `terraform plan/apply`. Terraform then zips that directory into a Lambda Layer. No local `pip install` step needed.

**Deployment order:** Commit + push `business-core` first (IaC GitHub Actions checks it out during plan/apply — the source directory must exist in the remote repo before Terraform runs).

---

## Runtime & Dependencies

| Lambda | Runtime | Key packages |
|---|---|---|
| etl_stocks | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_sales | Python 3.12 | psycopg2-binary, openpyxl, boto3 |
| etl_customer_ledger | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_customer_accounts | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_appendix_b_x11 | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_appendix_b_x11_purchase | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_appendix_b_x11_purchase_return | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_appendix_b_x11_sale | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_appendix_b_x11_sale_return | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_supplier_accounts | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_supplier_ledger | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3, reportlab (added 2026-07-20 for PDF exports) |
| alerts_evaluator | Python 3.12 | psycopg2-binary, reportlab (boto3/ses from runtime) |

---

## Environment Variables (per Lambda)

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, etl_supplier_ledger, redis_updater, api |
| `DATA_BUCKET` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11, etl_supplier_accounts, etl_supplier_ledger, api, whatsapp_notifier |
| `RAW_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11, etl_supplier_accounts, etl_supplier_ledger (default: `raw/`) |
| `PROCESSED_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11, etl_supplier_accounts, etl_supplier_ledger (default: `processed/`) |
| `EVENT_BUS_NAME` | Terraform | etl_stocks, etl_sales, etl_customer_ledger (default: `default`) |
| `REDIS_HOST` | Terraform | redis_updater, api |
| `JWT_SECRET_ARN` | Terraform | api (RBAC token signing key) |
| `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` | Terraform | api (first-login admin bootstrap) |
| `ALERTS_SENDER_EMAIL` | Terraform | alerts_evaluator (verified SES sender address, e.g. alerts@iravi.in) |

---

## etl_stocks — Stock Balance Processing

**Status: complete**

Source file pattern: `StockReport_<YYYYMMDD>_<HHMMSS>.csv` (S3 prefix filter: `raw/Current`; `_STOCK_PREFIX = 'StockReport'`).
**Changed 2026-07-16:** the upstream daily stock export switched from `Current Stock Balances*.xlsx` to this CSV format. The stock file is now read with `csv.DictReader` (`encoding='utf-8-sig'` — UTF-8 with BOM, every value quoted/stringified, no title/footer rows, header on row 1, data from row 2, 43 columns) mapped by header **name**, not position: `branch=BranchId, product=ProductId, brand=BrandId, qty=Qty (module constant `_QTY_COLUMN`, switchable to `BalQty` — identical in samples), cf=CF`. Numerics are cast explicitly (`float(v) if v not in (None, '') else 0`) since CSV values are strings. `BranchId`/`ProductId`/`BrandId` still hold the same human-readable text the old `Branch`/`Product`/`Brand` xlsx columns held (including the literal `&amp;` HTML entity in some branch names, passed through unchanged). The separate **rates** file (`Product Masters With Rates*.xlsx`) is UNCHANGED — still read via `openpyxl`, `min_row=6`.

**Product string parsing** (`Technical - Brand - Packing Size [- Packing Spec]`):
- Brand column used as anchor to locate split point in product string
- Handles embedded brand+size in one segment (IMIX pattern: `...WP - IMIX 8 GMS TIN`)
- Handles multi-segment technical names containing ` - ` (VIVAYA PLUS)
- Handles optional packing spec segment (BOX, TIN, POUCH S, POUCH L)
- Unchanged by the CSV migration — `_parse_product()` still receives the same `ProductId` string format as the old `Product` column.

**Unit conversion** (always normalised to grams or ml):
- `GMS`, `GM` → `gms` (no conversion)
- `KG` → `gms` × 1000
- `ML` → `ml` (no conversion)
- `LT`, `LTR`, `L` → `ml` × 1000

**`available_qty` in DB:** stored as kg or L (divided by 1000 on INSERT). The in-memory dict and Excel output retain the original gram/ml value. The API and redis_updater accumulate the raw DB value directly — no further division.

**Row merging:** rows sharing the same (Brand, Technical, Packing Size, Packing Configuration, Branch, Special Packing Mention) are collapsed into one — `Available Nos` is summed, `Available Cases` and `Available Qty` are recalculated from the total. Unchanged by the CSV migration.

**Rate lookup:** `process_stock_file` accepts optional `rates_path` pointing to a `Product Masters With Rates*.xlsx` file (still openpyxl). Rates are joined on the raw product string, filtered to `Purchase Price List` only.

**Lambda handler (`handler.py`):**
- Trigger: S3 `ObjectCreated` on `{RAW_PREFIX}StockReport*.csv`
- Finds latest `{RAW_PREFIX}Product Masters With Rates*.xlsx` automatically (by LastModified) — unchanged
- Downloads both to `/tmp/` (stock file as `stock.csv`), calls `process_stock_file`, uploads output to `{PROCESSED_PREFIX}Stock - Processed <date_suffix>.xlsx` — the processed output stays an xlsx workbook, so `_process()` swaps the source's `.csv` suffix for `.xlsx` when building `out_filename` (the source filename's date suffix now starts with `_` not a bare date, e.g. `StockReport_20260715_194634.csv` → suffix `_20260715_194634.csv` → `.xlsx`)
- Archives source (the raw `.csv`) to `{PROCESSED_PREFIX}raw/<original_filename>`
- Upserts into `snapshot_stock` (unitemporal milestoning)
- Emits `ETLStocksSuccess` EventBridge event on success

**Snapshot replace:** the stock file is a full snapshot, not an incremental feed. Each run closes **every** currently active row (`UPDATE snapshot_stock SET out_z = NOW() WHERE out_z IS NULL`) before inserting the new rows — not just rows matching a natural key in the new file. This ensures products/packings that drop out of stock (absent from the new file) are correctly marked superseded instead of remaining `out_z IS NULL` forever, which would otherwise inflate `iravi:stocks:current` and the summary tiles.

**Tests:** `tests/test_process_csv.py` (run: `python test_process_csv.py` from `lambda/etl_stocks/tests/`) — exercises `process_stock_file()` against a synthetic fixture (`tests/fixtures/StockReport_20260715_194634.csv`, generated by `tests/gen_fixture.py`, mirrors the real format: BOM, quoted values, 44 headers incl. `ExpiryDate`) covering header-name column mapping, blank-brand skip, `_parse_product` size/unit parsing, `float(Qty)`/`float(CF)` casts, row-merge summing, and (2026-08-01) `ExpiryDate` parsing/None-tolerance/no-merge-across-expiry (see below). 26/26 assertions pass.

**Known stale reference (not yet fixed — flagged 2026-07-16):** `run_local.py` still points at the old `Current Stock Balances*.xlsx` sample under `../etl_sales/sample_data/` and will fail against `process_stock_file`'s new CSV-only reader. It was out of scope for the CSV migration task; update it (or add a CSV sample there) before next using it for local end-to-end testing.

**Stock Expiry tracking (added 2026-08-01) — upstream CSV gained a 44th trailing column `ExpiryDate` (format `DD-MM-YYYY HH:MM:SS`, time always `00:00:00`, blanks tolerated):**
- `process.py`: new `_parse_date(val) -> date | None` helper (copied pattern from `etl_appendix_b_x11_purchase/handler.py`'s `_parse_date`, format list `'%d-%m-%Y %H:%M:%S', '%d-%m-%Y', '%Y-%m-%d'`, warn-and-return-None on unparseable). `row.get('ExpiryDate')` is parsed per CSV row and appended LAST to the row-merge key tuple: `(brand, technical, packing_size, packing_config, branch, spec, expiry_date)` — rows with different expiry dates for the same product/branch/packing no longer collapse together. A missing/unparseable `ExpiryDate` still produces a row (`expiry_date=None`), never dropped. `'Expiry Date'` added to `_OUTPUT_HEADERS` (between `Entry Date` and `Rate`) and to the processed xlsx row.
- **Design decision (finer grain, but existing endpoints unchanged):** `snapshot_stock` is now ONE ROW PER DISTINCT EXPIRY DATE — strictly finer-grained than before (expiry is part of the natural key; see IaC migration `049`, which adds the `expiry_date DATE` column and rebuilds `uix_stock_active` to include `COALESCE(expiry_date, '9999-12-31')`). `handler.py`'s `_upsert_snapshot_stock` INSERT gained `expiry_date` in the column list/values tuple; the close-all-then-insert-all milestoning pattern is otherwise unchanged.
- `GET /stocks/current` and `GET /stocks/summary` (api Lambda) and `redis_updater._update_stocks_cache()` all GROUP BY back down to the pre-expiry grain (`brand, technical, packing_size, packing_configuration, branch, special_packing_mention, entry_date`, with `SUM()` on every quantity/valuation column and `MAX()` on `conversion_factor`/`rate` — both per-product constants) so these three JSON/cache shapes are **byte-identical in shape and totals** to before the expiry change. `_handle_stocks_summary` needed NO code change — its Python-side aggregation already dedupes on `(brand, technical, packing_size, packing_config)` (no branch, no expiry), so summing raw rows already produces the same totals regardless of grain.
- **New `GET /stocks/expiry`** (api Lambda) — un-aggregated `snapshot_stock` rows (`WHERE out_z IS NULL`, one row per distinct expiry date), cache key `iravi:stocks:expiry` (24h TTL, standard cache-aside), ordered `expiry_date ASC NULLS LAST, brand, technical, branch`. Excludes `rate`/`stock_valuation` entirely. Shape: `{brand, technical, packing_size, packing_configuration, packing_display, available_nos, conversion_factor, available_cases, available_qty, branch, special_packing_mention, entry_date, expiry_date}` (dates as `YYYY-MM-DD` or `null`).
- **New `GET /stocks/expiry/pdf`** (api Lambda, `lambda/api/stocks_expiry_pdf.py`) — computed fresh (no Redis cache, matches the other report-PDF routes' convention), landscape A4, shared `letterhead.py` header/footer repeating every page. Query params: `brand` (case-insensitive substring, `ILIKE '%…%'`) and `expires_before_months` (`3`/`6`/`9`/`12`, else no filter). Month filter computed via a small `_add_months(date, months)` helper in `handler.py` (calendar-safe day clamping, e.g. 31-Jan + 1 → 28/29-Feb) — `expiry_date <= today + N months`; rows with NULL expiry are EXCLUDED when the month filter is active, INCLUDED when it is absent. Columns: Brand, Technical, Packing, Branch, Special Packing, Available Nos, Conversion Factor, Available Cases, Available Qty, Entry Date, Expiry Date — NO rate/valuation. Subtitle line shows active filters, e.g. `Brand: GULFONID · Expiring before 21-11-2026`, or `All Stock` when none.
- `redis_updater._update_stocks_cache()` also now populates `iravi:stocks:expiry` (same 86400s TTL) in the same pipeline, with the identical un-aggregated shape as the API route (second SQL query against `snapshot_stock`, same ORDER BY).
- **Fixture rebuilt** (`tests/gen_fixture.py` + `tests/fixtures/StockReport_20260715_194634.csv`, 44 headers) — added a `RIVAL` product with two rows sharing every key column except `ExpiryDate` (proves no-merge-across-expiry, nos 100 and 200 stay separate) and a `ZERION` row with a blank `ExpiryDate` (proves None-tolerance, row not dropped). Existing GULFONID/VIVAYA PLUS rows kept, now carrying explicit (matching) expiry dates so the original merge assertion still holds.
- **Pending on the IaC side (this Lambda cannot deploy correctly without it):** migration `049` (`ALTER TABLE snapshot_stock ADD COLUMN expiry_date DATE`, rebuild `uix_stock_active`) must be applied to RDS BEFORE this Lambda's next stock-file run, and `lambda_api.tf` needs new routes `GET /stocks/expiry` + `GET /stocks/expiry/pdf` (+ CORS). No UI page exists yet for this feature (backlog).

---

## etl_customer_ledger — Customer Ledger Processing

**Status: complete**

Source file pattern: `Ledger All Accounts*.xlsx` (S3 prefix filter: `raw/Ledger`)

**Changed 2026-07-22:** the upstream ledger feed switched from a real xlsx workbook (header row 5,
data from `min_row=6`) to **single-sheet CSV content** while keeping the same filename pattern
(`Ledger All Accounts*.xlsx`) and the same S3 prefix — the filename gate (`_FILE_PREFIX` + `.xlsx`
suffix check) is unchanged, but the downloaded bytes are now comma-delimited CSV text, never
openpyxl-readable. `openpyxl` and the old `wb.active` / `ws.iter_rows(min_row=6, values_only=True)`
positional-index reader are removed from this handler. The file is read with `csv.DictReader`
(`encoding='utf-8-sig'`, `newline=''`, same idiom as `etl_appendix_b_x11_purchase` /
`etl_customer_accounts`), header on line 1, data from line 2 — no leading metadata rows — 28
columns, mapped by header **name** (not position). A new `_to_amount(v)` helper parses the
`Debit`/`Credit` string cells to float, treating `None`/`''`/whitespace-only as `0.0` (CSV has no
"empty cell" sentinel other than an empty string, unlike the old openpyxl reader which returned
`None` for a truly blank cell). Every other rule below (skip rules, sign normalization, category/
sub-category logic, milestoning) is unchanged — only the reader/extraction layer changed.

**Customer row selection (AccountGroup column, originally 2026-06-27; column now selected by
header name after the 2026-07-22 CSV conversion):**
- Identifies customer rows directly from the ledger file: `account_group = str(row.get('AccountGroup') or '').strip()`.
- Keeps a row only if `account_group.lower() == 'all customer accounts'`. Case-insensitive comparison for safety. Distinct groups in the file include "All Customer Accounts" (341 rows in sample file), "All Supplier Accounts" (114), "All Sales Accounts", "All Bank Accounts", blank (GL/GST contra-leg rows), etc.
- Explicit IRAVI exclusion: after the account group check, rows where `'iravi' in account_name.lower()` are dropped. IRAVI own-company accounts appear under "All Customer Accounts" in the ledger and must not land in `customer_ledger`.
- **Does NOT read `customer_details` at all.** No DB read is required for filtering. This means customers who have ledger rows but no `customer_details` master record (no party code) are now correctly included. The API's `_handle_customer_balances_fy` still LEFT-joins `customer_details` for code/city; customers without a master record show null code and null city (UI renders as a dash).
- Sample file (2026-06-27, pre-CSV): 341 raw "All Customer Accounts" rows → 2 IRAVI rows dropped → remaining skip rules (Brought Forward 213, Default Purchase Account 27, no date 2) → **268 rows written** from 36 distinct customer parties.

**Parse rules (CSV rows — header on line 1, data from line 2):**
- Skip if `Date` is blank/unparseable
- Skip if `account_name` (from `ACCOUNT`) is empty
- Skip if `voucher_no` (from `VoucherNo`) `== 'Brought Forward'`
- Skip if `debit == 0 and credit == 0` (evaluated AFTER sign normalization below)
- Skip if `contra_account` (from `ContraAccount`) `== 'Default Purchase Account'`
- Skip if `account_group` (from `AccountGroup`) `!= 'All Customer Accounts'` (case-insensitive)
- Skip if `'iravi' in account_name.lower()` (explicit IRAVI own-company exclusion)

**Sign normalization (applied immediately after reading debit/credit, before the skip check):**
FUSIL writes some adjustments (e.g. `Roundoff A/C`) as a negative value on one side of the ledger.
A negative debit is economically a credit of its magnitude, and vice-versa. Two independent `if`s
normalize both sides so the rest of the logic always receives non-negative values:
```python
if debit < 0:
    credit += -debit
    debit = 0.0
if credit < 0:
    debit += -credit
    credit = 0.0
```
Example: EKR INDUSTRIES voucher POSRT2526-7 `Roundoff A/C` row arrives as `debit=-0.48, credit=0`.
After normalization: `debit=0.0, credit=0.48` → stored as `category='Cr', sub_category='Roundoff A/C', amount=0.48` (correct — reduces the Dr balance). Before this fix, `amount=-0.48` was stored with `category='Cr'`, which ADDED 0.48 to the balance instead of subtracting it (0.48 reconciliation error).

**Header-name column mapping (2026-07-22, replaces the old 0-indexed positional mapping):**
`Date → transaction_date, VoucherNo → voucher_no, TransactionName → transaction_name,
ACCOUNT → account_name, ContraAccount → contra_account, Debit → debit, Credit → credit,
AccountGroup → account_group`. The CSV has 28 columns total; only the above are consumed, the rest
are ignored.

**Re-ingest required after 2026-07-22 CSV-conversion deploy:** the `Ledger All Accounts*.xlsx` file
must be re-uploaded to S3 `raw/` — as real CSV content — so the updated single-sheet Lambda can
parse it (the old openpyxl reader would fail/misread CSV bytes, and vice versa). This affects both
`etl_customer_ledger` and `etl_supplier_ledger` (same source file). After re-ingest, run
`POST /admin/cache/flush` to clear stale `iravi:reports:customer_balances_fy:*` /
`iravi:reports:supplier_balances_fy:*` / `iravi:ledger:*` entries from Redis.

**Verified 2026-07-22:** a synthetic 28-column CSV fixture (built and deleted in this Lambda's own
directory — `__pycache__` cleaned up afterward) confirmed `_parse` produces the expected
`transaction_date/voucher_no/account_name/category/sub_category/amount` for a matching
`AccountGroup="All Customer Accounts"` row (`category='Db'`, `sub_category='Sale'` for a
`ContraAccount="Default Sales Account"` Sales Invoice row), while a `"All Bank Accounts"` row, a
`VoucherNo="Brought Forward"` row, and an `ACCOUNT` containing "IRAVI" were all correctly excluded.
`python -m py_compile handler.py` clean. No downstream change (sign normalization, category/
sub-category maps, milestoning upsert, S3 archive/delete, `ETLCustomerLedgerSuccess` emission) —
only the parsing/extraction layer changed.

**Category & sub-category logic:**
| Transaction Name | Category | Sub-category (from Contra Account) |
|---|---|---|
| Sales Invoice | `Db` | CGST Output A/C → CGST, SGST Output A/C → SGST, IGST Output A/C → IGST, Default Sales Account → Sale, Roundoff A/C → Roundoff |
| Sales Invoice Returns | `Cr` if `credit > 0` after normalization, else `Db` | CGST Input A/C → CGST, SGST Input A/C → SGST, IGST Input A/C → IGST, Default SalesReturn Account → Sales Return |
| Bank Receipts | `Cr` | Bank Receipt |
| Cash Receipts | `Cr` | Cash Receipt |
| *(any other)* | `Cr`/`Db` from credit/debit col | transaction_name (fallback) |

**Sales Invoice Returns — roundoff classification fix (2026-06-23):**
Previously the Sales Invoice Returns branch hardcoded `category = 'Cr'` regardless of which column held the value after sign-normalization. A Roundoff A/C row whose raw value is a negative credit (e.g. `credit = -0.20`) gets normalized to `debit = 0.20, credit = 0`; the hardcode then stored it as `Cr, 0.20` — a phantom credit — instead of `Db, 0.20`. The fix classifies by column after normalization: `category = 'Cr' if credit > 0 else 'Db'`. Normal return rows (positive credit) remain `Cr` unchanged; roundoff rows that land on the debit side after normalization are now stored as `Db`. This matches the observed symptom: EKR INDUSTRIES (excess `Cr 0.48`) and SRI VENKATESWARA COFFEE AND GENERAL STORES (excess `Cr 0.20`) both caused by the same mis-classification. With per-voucher netting already deployed in the API, the Db roundoff nets against the return's Cr rows, producing the clean credit total (`59,059.00` instead of `59,059.20`).

**Milestoning natural key:** `(transaction_date, voucher_no, account_name, category, sub_category)` — UPDATE closes open record matching all five, then INSERT adds new row.

**On success:** archives source to `processed/raw/`, emits `ETLCustomerLedgerSuccess` EventBridge event → triggers redis_updater to write `iravi:ledger:range`.

---

## etl_customer_accounts — Customer Details Processing

**Status: complete**

Source file pattern: `Customer Accounts Export File*.xlsx` (S3 prefix filter: `raw/Customer`)

**Changed 2026-07-22:** the upstream feed switched from a real two-sheet xlsx workbook to
**single-sheet CSV content** while keeping the same filename pattern
(`Customer Accounts Export File*.xlsx`) — the filename gate (`_FILE_PREFIX` + `.xlsx` suffix
check) is unchanged, but the downloaded bytes are now comma-delimited CSV text (header on
line 1, data from line 2, one row per customer), never openpyxl-readable. The old
`General` / `Delivery Address` two-sheet merge (`_build_code_lookup` + `_build_delivery_lookup`
+ name-set union) is **gone** — `openpyxl` is no longer imported. The file is read with
`csv.DictReader` (`encoding='utf-8-sig'`, `newline=''`, same idiom as
`etl_appendix_b_x11_purchase`), 27 columns (`MstId` appears twice — harmless, never read),
mapped by header **name** (not position), via a standalone `_extract_customer_row(row: dict)
-> dict | None` (skip a row if `Name` is blank). Extraction and parsing (`_parse`) are now a
single pass — no lookup union.

**Header-name column mapping (2026-07-22):**
`Name → customer_name, Code → customer_code, Address3 → district, City → city,
StateName → state (NOT the plain State column, which is blank in this feed), PIN → pin,
MobileNo → mobile_no`.

**Transformations (all preserved byte-for-byte from the pre-CSV two-sheet version, just fed
from CSV dict rows instead of positional openpyxl tuples):**
- `customer_name` — uppercased
- `district` (from `Address3`), `city` (from `City`) — title-cased if non-blank, else `None`
- `state` (from `StateName`) — mapped via `_STATE_MAP`: `37-Andhra Pradesh` → `AP`,
  `36-Telangana` → `TG` (plus TN/OR entries flagged unverified); no match → `None`
- `pin` — stripped string; blank → `None`
- `mobile_no` — spaces stripped (CSV values are always strings — no more int/float source
  branch); last 10 digits kept if > 10; blank → `None`
- `customer_code` (from `Code`) — cast to string and stripped; blank → `None`

**Duplicate names:** first occurrence wins (matches the old two-sheet lookup behavior) — `_parse`
keeps a `dict` keyed by uppercased `Name`, skipping any row whose name was already seen.

**Row source:** every row in the single CSV (one row per customer) produces one
`customer_details` row — there is no longer a two-sheet union; blank cells (`''`, not `None`
from `csv.DictReader`) are converted to `None` where the old code produced `NULL`.

**Upsert strategy (changed 2026-07-12): uni-temporal milestoning (close-then-insert), replacing the previous `ON CONFLICT` dimension upsert.**
Natural key = `customer_name`. Partial unique index `uix_customer_details_active ON customer_details (customer_name) WHERE out_z IS NULL` (IaC migration TBD, same migration that added `id BIGSERIAL`/`in_z`/`out_z` to `customer_details`) ensures at most one active row per customer at any time. For each parsed row:
```sql
UPDATE customer_details SET out_z = NOW() WHERE customer_name = %s AND out_z IS NULL;
INSERT INTO customer_details (customer_name, district, city, state, pin, mobile_no, customer_code)
VALUES (%s,%s,%s,%s,%s,%s,%s);
```
`id`, `in_z`, `out_z` are handled by column defaults — never set explicitly.

**Retire-absent (full-snapshot semantics, added 2026-07-12):** each export is treated as the authoritative full customer list. After the per-row close+insert loop, any still-active row whose `customer_name` is NOT present in this file's parsed name list is retired:
```sql
UPDATE customer_details SET out_z = NOW() WHERE out_z IS NULL AND NOT (customer_name = ANY(%s));
```
Names passed are exactly the uppercased values produced by `_parse()`. Freshly-inserted rows for present names have `out_z IS NULL` and ARE in the list, so they survive; only truly-absent names get closed — they disappear from `/customers/names` and `/customers/details` (both now filter `WHERE out_z IS NULL`) and from the Customer Balances (FY) city lookup going forward, without losing their historical ledger rows.

**Empty-file guard (added 2026-07-12):** if `rows` is empty, the upsert loop AND the retire step are both skipped — a warning is logged (`"customer accounts: 0 rows parsed — skipping upsert/retire to avoid wiping the table"`) and the function returns. This prevents a corrupt/empty export from closing every customer. `conn.commit()` is called once at the end, only when rows were processed.

**Target table:** `customer_details` — `(id, customer_name, district, city, state, pin, mobile_no, customer_code, in_z, out_z)`

**Migration dependency:** `customer_code VARCHAR(20)` column added by IaC migration `011`. The milestoning columns (`id BIGSERIAL`, `in_z`, `out_z`) and the partial unique index `uix_customer_details_active` are added by a separate IaC migration (2026-07-12) — must be applied before deploying this handler version.

**Re-ingest required after 2026-07-22 CSV-conversion deploy:** the `Customer Accounts Export File*.xlsx` must be re-uploaded to S3 `raw/` — as real CSV content — so the updated single-sheet Lambda can parse it (the old openpyxl reader would fail/misread CSV bytes, and vice versa). After re-ingest, run `POST /admin/cache/flush` to clear stale `iravi:reports:customer_balances_fy:*` entries from Redis.

**Verified 2026-07-22:** a synthetic single-sheet CSV fixture (real 27-column header row + one
real sample data row, built and deleted in this Lambda's own directory — `__pycache__` cleaned
up afterward) confirmed `_extract_customer_row`/`_parse` produce `customer_name="ADITYA AGRO
CHEMICALS"`, `customer_code="PAL004"`, `district="Palnadu"`, `city="Gurazala"`, `state="AP"`,
`pin="522415"`, `mobile_no="7013263948"` — matching the pre-CSV two-sheet transformations
exactly. `python -m py_compile handler.py` clean. No downstream change (milestoning upsert,
retire-absent, empty-file guard, S3 archive/delete) — only the parsing/extraction layer
changed, mirroring the purchase-side handlers' 2026-07-21 CSV conversion.

---

## etl_supplier_accounts — Supplier Master Processing

**Status: complete**

Source file pattern: `Supplier Accounts Export File*.xlsx` (S3 prefix filter: `raw/Supplier`)

**Changed 2026-07-22:** the upstream feed switched from a real two-sheet xlsx workbook
(`General` + empty `Sheet1`) to **single-sheet CSV content** while keeping the same filename
pattern (`Supplier Accounts Export File*.xlsx`) — the filename gate (`_FILE_PREFIX` + `.xlsx`
suffix check) is unchanged, but the downloaded bytes are now comma-delimited CSV text (header
on line 1, data from line 2, one row per supplier), never openpyxl-readable. `openpyxl` and the
old `wb['General']` / `ws.iter_rows(min_row=2, values_only=True)` positional-index reader are
removed from this handler. The file is read with `csv.DictReader`
(`encoding='utf-8-sig'`, `newline=''`, same idiom as `etl_customer_accounts` /
`etl_appendix_b_x11_purchase`), 46 columns, mapped by header **name** (not position), via a
standalone `_extract_supplier_row(row: dict) -> dict | None` helper (returns `None` if `Name`
is blank after the prefix strip below, or if the row is an IRAVI own-company row). Extraction
and parsing (`_parse`) are a single pass, first-occurrence-wins on duplicate names (mirrors
`etl_customer_accounts`'s `_parse`).

**Header-name column mapping (2026-07-22):**
`Name → name (prefix-stripped — see below), GST → gst, GSTValid → gst_valid,
City → city, StateName → state (NOT the plain State column, which holds a numeric master id)`.
Several headers in the 46-column feed are DUPLICATED (`MstId` ×4, `EntityId`/`MenuItemId`/
`TransId` ×2) — `csv.DictReader` collapses duplicates to last-wins, which is harmless since
none of the 5 mapped fields are duplicated.

**New transform — numeric name-prefix strip (2026-07-22, user-approved):** some names carry a
stray leading `"<digits> - "` prefix, e.g. `"29 - CLICKTECH RETAIL PRIVATE LIMITED"` →
`"CLICKTECH RETAIL PRIVATE LIMITED"`. `_NAME_PREFIX_RE = re.compile(r'^\s*\d+\s*-\s*')` strips
that pattern from `Name` BEFORE it's used as the natural key (`_NAME_PREFIX_RE.sub('',
name_raw).strip()`); names without the pattern (e.g. `"AGROKING PESTICIDES PVT. LTD."`) are
left unchanged. Blank after stripping → skip row. The `'iravi' in name.lower()` self-filter is
applied AFTER the prefix strip (unchanged filter semantics, just re-ordered to run on the
cleaned name).

**Transforms (applied in order per row, inside `_extract_supplier_row`):**
- `name` — leading numeric prefix stripped (above), then `.strip()`'d; blank → SKIP.
- IRAVI FILTER: `'iravi' in name.lower()` (post-strip) → SKIP (drops "IRAVI AGRO LIFE HYD" and
  "IRAVI AGRO LIFE LLP - GNT").
- `gst` — stripped string from `GST`; blank or the literal string `"NULL"` (case-insensitive)
  → `None`.
- `gst_valid` — tri-state from `GSTValid`: blank or literal `"NULL"` → `None`; else
  `bool(int(...))` — `"1"`/`1` → `True`, `"0"`/`0` → `False`. `None` and `False` are distinct:
  `None` = no GST registered; `False` = GST present but invalid. (CSV values are strings, cast
  via `int()` after the blank/NULL check — unlike the old openpyxl reader, which received the
  cell as `None` or a numeric type directly.)
- `city` — stripped string from `City`, title-cased if non-blank else `None` (source casing
  inconsistent; normalised to title case — unchanged transform, now CSV-sourced).
- `state` — from `StateName` (NOT the plain `State` column, which holds a numeric master id):
  blank or literal `"NULL"` → `None`; else if `'-'` in the value → take the part after the
  FIRST `'-'` (e.g. `"29-Karnataka"` → `"Karnataka"`); else keep the value as-is (e.g. bare
  `"Karnataka"` stays `"Karnataka"`). Same split logic as before the CSV conversion, now with
  an added `"NULL"`-string guard (blank cells from `csv.DictReader` are `''`, never `None`).

**Verified 2026-07-22:** a synthetic single-sheet CSV fixture (real 46-column header row +
two data rows, built and deleted in this Lambda's own directory — `__pycache__` cleaned up
afterward) confirmed `_extract_supplier_row`/`_parse` produce, for
`Name="29 - CLICKTECH RETAIL PRIVATE LIMITED", City="Bengaluru", StateName="29-Karnataka",
GST="29AAJCC9783E1Z3", GSTValid="1"`: `name="CLICKTECH RETAIL PRIVATE LIMITED"` (prefix
stripped), `gst="29AAJCC9783E1Z3"`, `gst_valid=True`, `city="Bengaluru"`,
`state="Karnataka"`; and for `Name="BB POLYMERS", City="Somewhere", StateName="NULL",
GST="", GSTValid="0"`: `name="BB POLYMERS"` (no prefix to strip), `gst=None`,
`gst_valid=False`, `city="Somewhere"`, `state=None` (`"NULL"` treated as `None`).
`python -m py_compile handler.py` clean. No downstream change (milestoning upsert,
retire-absent, empty-file guard, S3 archive/delete, no EventBridge emission) — only the
parsing/extraction layer changed, mirroring `etl_customer_accounts`'s 2026-07-22 CSV
conversion.

**Milestoning upsert (uni-temporal, close-then-insert):**
Natural key = `name`. Partial unique index on `(name) WHERE out_z IS NULL`.
For each parsed row:
```sql
UPDATE supplier_accounts SET out_z = NOW() WHERE name = %s AND out_z IS NULL;
INSERT INTO supplier_accounts (name, gst, gst_valid, city, state) VALUES (%s,%s,%s,%s,%s);
```

**Retire-absent (full-snapshot semantics, added 2026-07-12):** each export is treated as the authoritative full supplier list. After the per-row close+insert loop, any still-active row whose `name` is NOT present in this file's parsed name list is retired:
```sql
UPDATE supplier_accounts SET out_z = NOW() WHERE out_z IS NULL AND NOT (name = ANY(%s));
```
Freshly-inserted rows for present names have `out_z IS NULL` and ARE in the list, so they survive; only truly-absent names get closed — they disappear from the Supplier Balances (FY) city lookup (`api/supplier_balances_fy.py` now filters `WHERE out_z IS NULL`) going forward.

**Empty-file guard (added 2026-07-12):** if `rows` is empty, the upsert loop AND the retire step are both skipped — a warning is logged (`"supplier accounts: 0 rows parsed — skipping upsert/retire to avoid wiping the table"`) and the function returns. This prevents a corrupt/empty export from closing every supplier.

All rows written in a single DB transaction; committed once at the end, only when rows were processed.

**On success:** archives source to `processed/raw/`. No EventBridge event (supplier master data; no redis cache step required).

**Target table:** `supplier_accounts` (IaC migration 016). Schema:
```
id BIGSERIAL PK, name VARCHAR(255) NOT NULL, gst VARCHAR(20), gst_valid BOOLEAN,
city VARCHAR(120), state VARCHAR(100), in_z TIMESTAMPTZ NOT NULL DEFAULT NOW(), out_z TIMESTAMPTZ NULL.
Partial unique index on (name) WHERE out_z IS NULL.
```

**Migration dependency:** IaC migration 016 must be applied before running this Lambda.

---

## etl_supplier_ledger — Supplier Ledger Processing

**Status: complete**

Source file: same `Ledger All Accounts*.xlsx` used by `etl_customer_ledger`.

**Changed 2026-07-22:** the upstream feed switched from a real xlsx workbook (`wb.active`, sheet
named "Invoice", header row 5, data from `min_row=6`) to **single-sheet CSV content** while keeping
the same filename pattern (`Ledger All Accounts*.xlsx`) — the filename gate (`_FILE_PREFIX` + `.xlsx`
suffix check) is unchanged, but the downloaded bytes are now comma-delimited CSV text, never
openpyxl-readable. `openpyxl` and the old positional-index `ws.iter_rows(min_row=6, ...)` reader are
removed from this handler (mirrors `etl_customer_ledger`'s 2026-07-22 conversion of the same source
file — read via `_download_with_fallback`, same as before). The file is read with `csv.DictReader`
(`encoding='utf-8-sig'`, `newline=''`), header on line 1, data from line 2 — no leading metadata
rows — 28 columns, mapped by header **name** (not position). The same `_to_amount(v)` helper as
`etl_customer_ledger` parses `Debit`/`Credit` string cells to float (`None`/`''`/whitespace-only →
`0.0`). Every other rule below (skip rules, sign normalization, category/sub-category logic,
milestoning) is unchanged — only the reader/extraction layer changed.

**Trigger:** EventBridge "Object Created" rule (NOT an S3 Records event). Event shape:
```
bucket = event['detail']['bucket']['name']
key    = urllib.parse.unquote(event['detail']['object']['key'])   # %20 not '+'; use unquote not unquote_plus
```

**S3 behaviour — strictly read-only:**
- Downloads the source file. If the primary key returns 404/NoSuchKey (because `etl_customer_ledger` may have already archived it), falls back to downloading `{PROCESSED_PREFIX}raw/{filename}`.
- Does NOT copy, move, or delete any S3 object.
- Does NOT emit any EventBridge event.
- `etl_customer_ledger` owns the file lifecycle entirely.

**Supplier filter (AccountGroup column, originally 2026-06-27; column now selected by header name
after the 2026-07-22 CSV conversion):**
- Identifies supplier rows directly from the ledger file: `account_group = str(row.get('AccountGroup') or '').strip()`.
- Keeps a row only if `account_group.lower() == 'all supplier accounts'`. Case-insensitive comparison for safety. Distinct groups in the file include "All Customer Accounts", "All Supplier Accounts", "All Sales Accounts", "All Bank Accounts", etc.
- Explicit IRAVI exclusion: after the account group check, rows where `'iravi' in account_name.lower()` are dropped. IRAVI own-company accounts ("IRAVI AGRO LIFE HYD", "IRAVI AGRO LIFE LLP - GNT") appear under "All Supplier Accounts" in the ledger and must not land in `supplier_ledger`.
- **Does NOT read `supplier_accounts` at all.** No DB read is required for filtering.
- Sample file (2026-06-27, pre-CSV): 114 raw "All Supplier Accounts" rows → 10 IRAVI rows dropped → remaining skip rules (Brought Forward, zero-value, null-date) → **83 rows written** from 7 distinct suppliers including JAGRUTHI AGRO CHEMICALS (36 rows).

**Header-name column mapping (2026-07-22, replaces the old 0-indexed positional mapping):**
`Date → transaction_date, VoucherNo → voucher_no, TransactionName → transaction_name,
ACCOUNT → account_name, ContraAccount → contra_account, Debit → debit, Credit → credit,
AccountGroup → account_group`. The CSV has 28 columns total; only the above are consumed, the rest
are ignored. Identical mapping to `etl_customer_ledger` (same source file).

**Parse / skip rules (identical mechanics to etl_customer_ledger):**
- Sign normalization applied first: negative debit → add to credit & zero; negative credit → add to debit & zero.
- Skip if: `Date` blank/unparseable; `account_name` (from `ACCOUNT`) empty; `voucher_no` (from `VoucherNo`) `== 'Brought Forward'`; `debit == 0 and credit == 0` (after normalization); `account_group.lower() != 'all supplier accounts'`; `'iravi' in account_name.lower()`.
- Date parsed with multi-format `_parse_date` (datetime / date / `%Y-%m-%d` / `%d-%m-%Y` / `%d/%m/%Y`). Unparseable → log warning and skip.
- **No sales-transaction exclusion.** Sales made TO a supplier (where the supplier is classified under `All Supplier Accounts` in FUSIL but is also an IRAVI customer) are legitimate and must appear in `supplier_ledger` so they show in Supplier Balances FY. The `Default Sales Account` skip and the `transaction_name.startswith('sales')` skip that were added 2026-06-27 have been reverted (2026-06-27).

**Re-ingest required after 2026-07-22 CSV-conversion deploy:** see the `etl_customer_ledger`
section above — same source file, same re-ingest + `POST /admin/cache/flush` procedure required
for both Lambdas together.

**Verified 2026-07-22:** a synthetic 28-column CSV fixture (built and deleted in this Lambda's own
directory — `__pycache__` cleaned up afterward) confirmed `_parse` produces the expected
`transaction_date/voucher_no/account_name/category/sub_category/amount` for a matching
`AccountGroup="All Supplier Accounts"` row (`category='Cr'`, `sub_category='Purchase'` for a
`ContraAccount="Default Purchase Account"` Purchase Voucher row), while a `"All Bank Accounts"`
row, a `VoucherNo="Brought Forward"` row, and an `ACCOUNT` containing "IRAVI" were all correctly
excluded. `python -m py_compile handler.py` clean. No downstream change (sign normalization,
category/sub-category maps, milestoning upsert, read-only S3 behavior, no EventBridge emission) —
only the parsing/extraction layer changed.

**Sales-side transactions in supplier_ledger (revised 2026-06-27):**
- MERCO ENERGY SOLUTIONS PRIVATE LIMITED (`All Supplier Accounts`) has Sales Invoice SIA2627-1 with three Debit legs: `Default Sales Account` 1,251,250.00, `CGST Output A/C` 112,612.50, `SGST Output A/C` 112,612.50 (total 1,476,475.00 Db).
- The expanded `_CONTRA_SUBCATEGORY` map resolves these to `sub_category = Sale / CGST / SGST` → three distinct natural keys → no milestoning collision → all three captured.
- Previously: (a) the `Default Sales Account` leg was dropped by a defensive skip; (b) both GST-output legs fell through to `sub_category = 'Sales Invoice'` (the transaction name fallback) → identical natural key → collision on every re-ingest. Both bugs are now fixed.
- Verified: FILE 1 (`Ledger All Accounts27-6-2026(12.19.10).xlsx`, April 2026) — 40 total supplier rows; MERCO yields exactly 3 rows: (Db, Sale, 1,251,250.00), (Db, CGST, 112,612.50), (Db, SGST, 112,612.50); total 1,476,475.00; all distinct natural keys.
- Verified: FILE 2 (`Ledger All Accounts27-6-2026(12.20.22).xlsx`, June 2026) — 83 total supplier rows; JAGRUTHI AGRO CHEMICALS 36 rows (`sub_categories: {'Roundoff','IGST','Bank Payment','Purchase'}`); all 7 purchase suppliers intact and unaffected.
- **One-time DB cleanup required** (stale rows from the previous exclusion deploy): `UPDATE supplier_ledger SET out_z = NOW() WHERE out_z IS NULL AND sub_category IN ('Sales Invoice','Sales Invoice Returns');`

**Category / sub-category (purchase-side AND sales-side combined):**
```python
_CONTRA_SUBCATEGORY = {
    # Purchase-side (supplier credited)
    'CGST Input A/C':             'CGST',
    'SGST Input A/C':             'SGST',
    'IGST Input A/C':             'IGST',
    'Default Purchase Account':   'Purchase',
    'Default PurchaseReturn Account': 'Purchase Return',
    # Sales-side (supplier debited — we also sell to this supplier)
    'CGST Output A/C':            'CGST',
    'SGST Output A/C':            'SGST',
    'IGST Output A/C':            'IGST',
    'Default Sales Account':      'Sale',
    'Default SalesReturn Account': 'Sales Return',
    # Shared
    'Roundoff A/C':               'Roundoff',
}
_TXN_SUBCATEGORY = {'Bank Payments':'Bank Payment','Cash Payments':'Cash Payment','Bank Receipts':'Bank Receipt','Cash Receipts':'Cash Receipt'}

category    = 'Cr' if credit > 0 else 'Db'
amount      = credit if credit > 0 else debit
sub_category = _CONTRA_SUBCATEGORY.get(contra_account)
               or _TXN_SUBCATEGORY.get(transaction_name)
               or transaction_name or contra_account
```
Purchase Vouchers credit the supplier (`Cr`); Bank/Cash Payments debit the supplier (`Db`); Sales Invoices TO the supplier debit the supplier (`Db`).

**Milestoning upsert:** identical close-then-insert into `supplier_ledger`.
Natural key = `(transaction_date, voucher_no, account_name, category, sub_category)`.
All rows in a single transaction, committed once.

**Target table:** `supplier_ledger` (IaC migration 017). Schema:
```
id SERIAL PK, transaction_date DATE NOT NULL, voucher_no VARCHAR(50) NOT NULL,
account_name VARCHAR(200) NOT NULL, category VARCHAR(10) NOT NULL,
sub_category VARCHAR(100) NOT NULL, amount NUMERIC(15,4) NOT NULL,
in_z TIMESTAMPTZ NOT NULL DEFAULT NOW(), out_z TIMESTAMPTZ.
Partial unique index on (transaction_date, voucher_no, account_name, category, sub_category) WHERE out_z IS NULL.
```

**IaC requirements (report to orchestrator):**
- Migration 017 — create `supplier_ledger` table + partial unique index (schema above).
- `lambda_etl_supplier_ledger.tf` — EventBridge "Object Created" rule on key prefix `raw/Ledger`; IAM: `s3:GetObject` on `DATA_BUCKET` (read-only, no PutObject/DeleteObject); Secrets Manager `secretsmanager:GetSecretValue` on `DB_SECRET_ARN`; env vars: `DATA_BUCKET`, `DB_SECRET_ARN`, `RAW_PREFIX`, `PROCESSED_PREFIX`; handler = `handler.lambda_handler`; runtime python3.12. **No `supplier_accounts` read at runtime** — the IAM policy does not need DB read for that table.
- S3 bucket EventBridge notifications must be enabled (already enabled if etl_customer_ledger's rule is active on the same bucket).
- CI layer build step for this Lambda (openpyxl + psycopg2-binary layer in `terraform.yml`).

---

## etl_appendix_b_x11_purchase — Purchase Ledger Processing

**Status: complete**

Source file pattern: `AppendixPurchaseReport*.xlsx` (S3 prefix filter: `raw/AppendixPurchase`)

**Changed 2026-07-21:** the upstream purchase report switched from a real xlsx workbook to
**CSV content** while keeping the same filename pattern (`AppendixPurchaseReport*.xlsx`) and the
same S3 prefix — the filename gate (`_FILE_PREFIX` + `.xlsx` suffix check) is unchanged, but the
downloaded bytes are now comma-delimited CSV text, never openpyxl-readable. The file is read with
`csv.DictReader` (`encoding='utf-8-sig'` — handles a possible UTF-8 BOM, same idiom as
`etl_stocks`), header on line 1, data from line 2, 32 columns, mapped by header **name** (not
position). `etl_appendix_b_x11_purchase_return` (the returns report) got the **same** CSV-content
treatment on 2026-07-21 (see its own section below) — both purchase-side ETLs are now CSV/openpyxl-free.

**Header-name column mapping (2026-07-21):**
`ProductId→product/technical_name, Qty→qty, Rate→rate, Gross→gross, AV→av, Barcodes→barcode/barcodes, Narration→narration, Date→purchase_date, BranchId→branch, AccountId→party, RefBillNo→ref_bill_no, RefBillDate→ref_bill_date, VoucherNo→iravi_voucher/voucher_no`
(the CSV has 32 columns total; only the above are consumed, the rest are ignored). Extraction is
in a standalone `_extract_purchase_row(row: dict) -> dict | None` function for testability. Skip
(return `None`) if `Date` is blank, `VoucherNo` is blank, or `ProductId` is blank. All CSV cells
arrive as strings — blank is `''`, never `None`; numeric casts (`qty`/`rate`/`gross`/`av`) go
through a `_to_float()` helper that treats `None` and `''`/whitespace-only as blank → `None`.
String fields are `.strip()`ped. Dates (`Date`, `RefBillDate`) are `DD-MM-YYYY` text, parsed by
the pre-existing `_parse_date()` (unchanged — already supported that format).

**Transformations:**
- `product`/`technical_name` — `.strip()` only (the old xlsx-era comma-stripping hack is no
  longer needed/applied — real CSV quoting already protects embedded commas, so `ProductId` is
  taken verbatim, matching how `etl_appendix_b_x11`'s barcode-master product names are stored)
- `barcode` — `Barcodes` column value (e.g. `"1000000023,"`), strip trailing comma, split by `,`;
  rows with multiple barcodes are skipped **for the ledger table only** (the `purchases` table
  gets every parsed row regardless of barcode count)
- `in_out` — hardcoded `'In'` (purchase report)
- `mdf_date` / `exp_date` — looked up from `appendix_b_x11_stock WHERE (technical_name, barcode) AND out_z IS NULL`; NULL if no match
- `purchase_return` — hardcoded `'N'`

**Writes to two tables per row:**
1. `appendix_b_x11_stock_ledger` — milestoning natural key `(purchase_date, iravi_voucher, technical_name, barcode)`; only rows with exactly 1 barcode (DB migration `006_create_appendix_b_x11_stock_ledger.sql`)
2. `purchases` — milestoning natural key/PK `(purchase_date, voucher_no, branch, party, product)`; every parsed row (DB migration `007_create_purchases.sql`)

**Verified 2026-07-21:** a synthetic CSV fixture (real header row + one real sample data row,
built in scratch and deleted afterward) confirmed `_extract_purchase_row` produces the expected
`product`, `qty`, `rate`, `gross`, `av`, `purchase_date`, `branch`, `party`, `ref_bill_no`,
`ref_bill_date`, `voucher_no` values, and that the full `_parse()` path correctly splits the
trailing-comma `Barcodes` string down to a single ledger barcode. `python -m py_compile
handler.py` clean. No downstream change (barcode lookup, milestoning upserts, S3
archive/delete, no EventBridge emission for this Lambda) — only the parsing/extraction layer
changed.

---

## etl_appendix_b_x11_purchase_return — Purchase Returns Ledger Processing

**Status: complete**

Source file pattern: `AppendixPurReturn*.xlsx` (S3 prefix filter: `raw/AppendixPurReturn`)

**Changed 2026-07-21:** the upstream purchase-returns feed switched from a real xlsx workbook to
**CSV content** while keeping the same filename pattern (`AppendixPurReturn*.xlsx`) and the same
S3 prefix — the filename gate (`_FILE_PREFIX = 'AppendixPurReturn'` + `.xlsx` suffix check) is
unchanged, but the downloaded bytes are now comma-delimited CSV text, never openpyxl-readable.
Mirrors the `etl_appendix_b_x11_purchase` (base purchase, flag `N`) 2026-07-21 conversion closely:
`csv.DictReader` (`encoding='utf-8-sig'`, `newline=''`), header on line 1, data from line 2 (no
leading blank/metadata rows), 29 columns, mapped by header **name** (not position). `openpyxl` and
the old `ws.iter_rows(min_row=6, ...)` positional-index reader are removed from this handler.

**Header-name column mapping (2026-07-21):**
`ProductId→product/technical_name, Qty→qty, Rate→rate, Gross→gross, AV→av, Barcodes→barcode/barcodes, Narration→narration, Date→purchase_date, BranchId→branch, AccountId→party, RefBillNo→ref_bill_no, RefBillDate→ref_bill_date, VoucherNo→iravi_voucher/voucher_no`
(the CSV has 29 columns total; only the above are consumed, the rest are ignored). Extraction is
in a standalone `_extract_purchase_row(row: dict) -> dict | None` function (same shape as the base
purchase handler) for testability. Skip (return `None`) if `Date` is blank, `VoucherNo` is blank,
or `ProductId` is blank. All CSV cells arrive as strings — blank is `''`, never `None`; numeric
casts (`qty`/`rate`/`gross`/`av`) go through a `_to_float()` helper that treats `None` and
`''`/whitespace-only as blank → `None`. String fields are `.strip()`ped via
`str(row.get(...) or '').strip()`. Dates (`Date`, `RefBillDate`) are `DD-MM-YYYY` text, parsed by
the pre-existing `_parse_date()` (unchanged — already supported that format).

**Transformations:**
- `product`/`technical_name` — `.strip()` only (the old xlsx-era comma-stripping hack is gone —
  real CSV quoting already protects embedded commas)
- `barcode` — `Barcodes` column value (trailing-comma quoted single-barcode string, e.g.
  `"AK-01/IAL,"`), strip trailing comma, split by `,`; rows with multiple barcodes are skipped
  **for the ledger table only** (the `purchases` table gets every parsed row regardless of
  barcode count)
- `in_out` — hardcoded `'Out'` (this is the RETURNS report — goods leaving/returning to supplier)
- `mdf_date` / `exp_date` — looked up from `appendix_b_x11_stock WHERE (technical_name, barcode) AND out_z IS NULL`; NULL if no match
- `purchase_return` — hardcoded `'Y'` (unchanged from before the CSV conversion — this is still the returns handler)

**Writes to two tables per row (unchanged by the CSV conversion):**
1. `appendix_b_x11_stock_ledger` — milestoning natural key `(purchase_date, iravi_voucher, technical_name, barcode)`; only rows with exactly 1 barcode (DB migration `006_create_appendix_b_x11_stock_ledger.sql`)
2. `purchases` — milestoning natural key/PK `(purchase_date, voucher_no, branch, party, product, COALESCE(barcodes,''))`; every parsed row, `purchase_return='Y'` (DB migration `007_create_purchases.sql`, key widened by `031_add_barcodes_to_purchases_sales_key.sql`)

**Verified 2026-07-21:** a synthetic CSV fixture (real header row + one real sample data row,
built and deleted in this Lambda's own directory — `__pycache__` cleaned up afterward) confirmed
`_extract_purchase_row` produces the expected `product`, `qty`, `rate`, `gross`, `av`,
`purchase_date`, `branch`, `party`, `ref_bill_no` (blank → `None`), `ref_bill_date`, `voucher_no`,
and `purchase_return='Y'` values, and that the full `_parse()` path correctly splits the
trailing-comma `Barcodes` string down to a single ledger barcode with `in_out='Out'`. `python -m
py_compile handler.py` clean. No downstream change (barcode lookup, milestoning upserts, S3
archive/delete, no EventBridge emission for this Lambda) — only the parsing/extraction layer
changed, exactly mirroring the base purchase handler's conversion.

---

## purchases — Purchase Line-Item Table

**Status: complete**

Line-item purchase ledger populated by both `etl_appendix_b_x11_purchase` (`AppendixPurchaseReport*.xlsx`, `purchase_return='N'`) and `etl_appendix_b_x11_purchase_return` (`AppendixPurReturn*.xlsx`, `purchase_return='Y'`).

**Columns:** `purchase_date, voucher_no, branch, party, ref_bill_no, ref_bill_date, product, qty, rate, gross, av, barcodes, narration, purchase_return, in_z, out_z`

**Milestoning natural key:** `(purchase_date, voucher_no, branch, party, product, COALESCE(barcodes,''))` — UPDATE closes any open record matching all six (barcodes compared via `COALESCE(barcodes,'') = COALESCE(%s,'')`), then INSERT adds the new row. `purchase_return` is not part of the key. **`barcodes` added to the key 2026-07-14** (IaC migration 031) — a single voucher legitimately carries the same product on multiple batch/barcode lines; the previous 5-column key collapsed those into one row (last-writer-wins). Requires a re-ingest of the Appendix purchase files to backfill previously-collapsed rows.

**Target table:** `purchases` (DB migration `007_create_purchases.sql`; key widened by `031_add_barcodes_to_purchases_sales_key.sql`)

---

## etl_appendix_b_x11_sale / etl_appendix_b_x11_sale_return — Sales Ledger Processing

**Status: complete**

Source file patterns: `AppendixSale*.xlsx` (S3 prefix filter: `raw/AppendixSale`, `sales_return='N'`, `in_out='Out'`) and `AppendixRetSales*.xlsx` (S3 prefix filter: `raw/AppendixRetSales`, `sales_return='Y'`, `in_out='In'`).

**Changed 2026-07-22:** both sale-side feeds switched from real xlsx workbooks to **CSV
content** while keeping the same filename patterns (`AppendixSale*.xlsx` /
`AppendixRetSales*.xlsx`) and the same S3 prefixes — the filename gates (`_FILE_PREFIX` +
`.xlsx` suffix check) are unchanged, but the downloaded bytes are now comma-delimited CSV
text, never openpyxl-readable. Mirrors the `etl_appendix_b_x11_purchase` /
`etl_appendix_b_x11_purchase_return` 2026-07-21 conversion closely: `csv.DictReader`
(`encoding='utf-8-sig'`, `newline=''`), header on line 1, data from line 2 (no leading
blank/metadata rows), mapped by header **name** (not position). `openpyxl` and the old
`ws.iter_rows(min_row=6, ...)` positional-index reader are removed from both handlers.

**Header-name column mapping (2026-07-22, both feeds):**
`ProductId→product/technical_name, Qty→qty, Rate→rate, Gross→gross, AV→av,
Barcodes→barcode/barcodes, Date→purchase_date, BranchId→branch, AccountId→party,
RefBillNo→ref_bill_no, RefBillDate→ref_bill_date, VoucherNo→iravi_voucher/voucher_no`.
`AppendixSale` has 29 columns, `AppendixRetSales` has 28 (missing `PriceListId`, which
isn't consumed here anyway) — only the above are consumed from either, the rest are
ignored, so the column-count difference doesn't affect header-name mapping. **Neither feed
has a Narration column** — `narration` is always hardcoded `None` in both handlers (same
as before the CSV conversion). Extraction is in a standalone `_extract_sale_row(row: dict)
-> dict | None` function (same shape as `etl_appendix_b_x11_purchase`'s
`_extract_purchase_row`) for testability. Skip (return `None`) if `Date` is blank,
`VoucherNo` is blank, or `ProductId` is blank. All CSV cells arrive as strings — blank is
`''`, never `None`; numeric casts (`qty`/`rate`/`gross`/`av`) go through a `_to_float()`
helper that treats `None` and `''`/whitespace-only as blank → `None`. String fields are
`.strip()`ped via `str(row.get(...) or '').strip()`. Dates (`Date`, `RefBillDate`) are
`DD-MM-YYYY` text, parsed by the pre-existing `_parse_date()` (unchanged — already
supported that format).

**Transformations:**
- `product`/`technical_name` — `.strip()` only (the old xlsx-era comma-stripping hack is
  gone — real CSV quoting already protects embedded commas, matching the purchase-side
  handlers' 2026-07-21 conversion)
- `barcode` — `Barcodes` column value (trailing-comma quoted single-barcode string, e.g.
  `"BC001,"`), strip trailing comma, split by `,`; rows with multiple barcodes are skipped
  **for the ledger table only** (the `sales` table gets every parsed row regardless of
  barcode count)
- `mdf_date` / `exp_date` — looked up from `appendix_b_x11_stock WHERE (technical_name, barcode) AND out_z IS NULL`; NULL if no match
- `narration` — always NULL (neither source file has a Narration column; unchanged by the CSV conversion)
- `in_out` — `'Out'` for `etl_appendix_b_x11_sale`, `'In'` for `etl_appendix_b_x11_sale_return` (unchanged)
- `sales_return` — `'N'` for `etl_appendix_b_x11_sale`, `'Y'` for `etl_appendix_b_x11_sale_return` (unchanged)

**Writes to two tables per row:**
1. `appendix_b_x11_stock_ledger` — milestoning natural key `(purchase_date, iravi_voucher, technical_name, barcode)`; only rows with exactly 1 barcode (DB migration `006_create_appendix_b_x11_stock_ledger.sql`)
2. `sales` — milestoning natural key/PK `(purchase_date, voucher_no, branch, party, product, COALESCE(barcodes,''))`; every parsed row (DB migration `008_create_sales.sql`; key widened by `031_add_barcodes_to_purchases_sales_key.sql`)

**Verified 2026-07-22:** synthetic CSV fixtures (real header row + one real sample data
row per feed, built and deleted in each Lambda's own directory — `__pycache__` cleaned up
afterward) confirmed `_extract_sale_row` produces the expected `product`, `qty`, `rate`,
`gross`, `av`, `purchase_date`, `branch`, `party`, `ref_bill_no` (blank → `None` for the
return feed), `ref_bill_date`, `voucher_no`, `narration=None`, and `sales_return` values
for both feeds, and that the full `_parse()` path correctly splits the trailing-comma
`Barcodes` string down to a single ledger barcode with the correct `in_out` per feed
(`Out` for base sale, `In` for sale return). `python -m py_compile handler.py` clean on
both. No downstream change (barcode lookup, milestoning upserts, S3 archive/delete, no
EventBridge emission for either Lambda) — only the parsing/extraction layer changed,
exactly mirroring the purchase-side handlers' conversion.

---

## sales — Sales Line-Item Table

**Status: complete**

Line-item sales ledger populated by both `etl_appendix_b_x11_sale` (`AppendixSale*.xlsx`, `sales_return='N'`) and `etl_appendix_b_x11_sale_return` (`AppendixRetSales*.xlsx`, `sales_return='Y'`).

**Columns:** `purchase_date, voucher_no, branch, party, ref_bill_no, ref_bill_date, product, qty, rate, gross, av, barcodes, narration, sales_return, in_z, out_z` — same shape as `purchases`, with `purchase_return` renamed to `sales_return`. `narration` is always NULL (no source column).

**Milestoning natural key:** `(purchase_date, voucher_no, branch, party, product, COALESCE(barcodes,''))` — UPDATE closes any open record matching all six (barcodes compared via `COALESCE(barcodes,'') = COALESCE(%s,'')`), then INSERT adds the new row. `sales_return` is not part of the key. **`barcodes` added to the key 2026-07-14** (IaC migration 031) — same fix as `purchases` (batch/barcode lines of the same product on one voucher were being collapsed).

**Target table:** `sales` (DB migration `008_create_sales.sql`; key widened by `031_add_barcodes_to_purchases_sales_key.sql`)

---

## etl_appendix_b_x11 — Barcodes Master Processing

**Status: complete**

Source file pattern: `Barcodes Masters*.xlsx` (S3 prefix filter: `raw/Barcodes`)

**Column mapping (0-indexed, header row 1, data from row 2):**
`[0]=Barcodes→barcode, [1]=ProductId→technical_name, [13]=PartNo→mdf_date, [16]=VendorId→vendor, [22]=Expiry Date→exp_date`

**Transformations:**
- `barcode` — int/float values cast to string (no decimal), string values stripped of whitespace
- `mdf_date` / `exp_date` — handles `datetime` objects, `'DD-MM-YYYY HH:MM:SS'`, `'DD-MM-YYYY'`, `'YYYY-MM-DD'` formats; ERP sentinel `01-01-1800` stored as NULL

**Milestoning natural key:** `(barcode, technical_name, vendor)` — UPDATE closes open record, then INSERT adds new row.

**Target table:** `appendix_b_x11_stock` (DB migration `005_create_appendix_b_x11_stock.sql`)

---

## whatsapp_notifier — Payment Reminder Notification

**Status: phase 1 complete (file move); phase 2 pending (WhatsApp send)**

Trigger: S3 `ObjectCreated` on `notifications/pending/*.html`.

**Phase 1 (complete):** Copies file to `notifications/processed/`, deletes from `notifications/pending/`. Validates the end-to-end flow from UI → API → S3 → Lambda.

**Phase 2 (pending — WhatsApp account under review):**
- `s3.head_object` to read `customer_name` from object metadata
- Query `customer_details` for `mobile_no`, prepend `'91'` for India dial code
- Fetch bearer token + phone number ID from Secrets Manager (`iravi/dashboard/whatsapp`)
- Call Meta WhatsApp Cloud API to send HTML as document message

**S3 layout:**
- `notifications/pending/{YYYYMMDD_HHMMSS}_{safe_customer}.html` — uploaded by API Lambda on `POST /notify`
- `notifications/processed/{filename}` — archived by this Lambda

**Customer name** is stored in S3 object metadata key `customer_name` (set by API Lambda on `put_object`).

---

## api — POST /notify Endpoint

**Added to existing API Lambda.** Accepts `POST /notify` with JSON body `{customer_name, html_content}`.
- Sanitises customer name for S3 key (alphanumeric + hyphen + underscore, max 80 chars)
- Puts HTML to `notifications/pending/{timestamp}_{safe_name}.html` with `ContentType: text/html` and `Metadata: {customer_name}`
- Returns `{key, message: "Notification queued"}`
- Requires `DATA_BUCKET` env var + `s3:PutObject` IAM on `notifications/*`

---

## etl_sales — Sales Processing

**Status: stub — core logic not yet implemented**

Source file pattern: `RGF Sales Book*.xlsx` (S3 prefix filter: `raw/RGF Sales Book`)

- Parses: rows 6+ only (skip rows 1–5 header, detect/skip total rows)
- Columns: `Date, Voucher No, Branch, Party, Party GSTN, Qty, Gross, Disc, AV, CGST, SGST, IGST, Net, BillValue`
- Upserts: `dim_customers` (on `customer_name`), then `fact_sales` (on `voucher_no, transaction_date`)
- On success: writes `etl_runs` row, emits `ETLSalesSuccess` EventBridge event, moves file to `processed/`
- On failure: writes `etl_runs` row with `status=failed`, raises exception (CloudWatch alarm fires)

---

## redis_updater — Cache Population

**Status: stocks + ledger range complete; sales stub**

Triggered by EventBridge. Routes on `detail-type`:

| Event | Handler | Redis key written |
|---|---|---|
| `ETLStocksSuccess` | `_update_stocks_cache()` | `iravi:stocks:summary`, `iravi:stocks:current`, `iravi:stocks:expiry` (added 2026-08-01) |
| `ETLCustomerLedgerSuccess` | `_update_ledger_range_cache()` | `iravi:ledger:range` |
| `ETLSalesSuccess` | `_update_sales_cache()` | *(stub — not yet implemented)* |

**`iravi:ledger:range`:** `{min_date, max_date}` — MIN/MAX of `transaction_date WHERE out_z IS NULL` in `customer_ledger`. 24h TTL.

---

## api — API Layer

**Status: stocks complete; reports/customer-balances-fy complete; reports/supplier-balances-fy complete; reports/monthly-sales complete; sales stub**

| Endpoint | Redis key | Status |
|---|---|---|
| `GET /stocks/summary` | `iravi:stocks:summary` | Complete |
| `GET /stocks/current` | `iravi:stocks:current` | Complete (GROUP BY aggregation added 2026-08-01 — see "Stock Expiry tracking" in the etl_stocks section) |
| `GET /stocks/expiry` | `iravi:stocks:expiry` | Complete (added 2026-08-01) |
| `GET /stocks/expiry/pdf` | — (no cache; always fresh) | Complete (added 2026-08-01) |
| `GET /sales` | — | Stub (returns empty array) |
| `GET /reports/customer-balances-fy` | `iravi:reports:customer_balances_fy:{fy_count}` | Complete |
| `GET /reports/supplier-balances-fy` | `iravi:reports:supplier_balances_fy:{fy_count}` | Complete |
| `GET /reports/monthly-sales` | `iravi:reports:monthly_sales:v2:{month}` | Complete (v2 payload — targets + YoY, 2026-07-11) |
| `GET /ledger/statement` | `iravi:ledger:statement:{account}:{from}:{to}` | Complete |
| `GET /reports/customer-balances-fy/pdf` | — (no cache; always fresh) | Complete (added 2026-07-20) |
| `GET /reports/supplier-balances-fy/pdf` | — (no cache; always fresh) | Complete (added 2026-07-20) |
| `GET /reports/monthly-sales/pdf` | — (no cache; always fresh) | Complete (added 2026-07-20) |
| `GET /reports/monthly-collection/pdf` | — (no cache; always fresh) | Complete (added 2026-07-20) |
| `GET /ledger/statement/pdf` | — (no cache; always fresh) | Complete (added 2026-07-20) |
| `GET /supplier-ledger/statement/pdf` | — (no cache; always fresh) | Complete (added 2026-07-20) |

Cache-aside pattern: Redis first → RDS fallback → populate Redis.

**Ledger statement — per-voucher netting (added 2026-06-23):**
`GET /ledger/statement` already groups rows by voucher before returning. Each voucher's `debit` and `credit` fields are now netted: `net = raw_debit − raw_credit`; if `net >= 0` then `debit=net, credit=0.0`; else `debit=0.0, credit=-net`. This absorbs roundoff/GST sub-components so no phantom opposite-side paise appear on the statement. `total_debit`/`total_credit` are summed from the netted values. `closing_balance` is unchanged because `net = raw_debit − raw_credit`, so `Σ(netted_debit − netted_credit) = Σ(raw_debit − raw_credit)`. Cache flush required after deploy (no re-ingest needed).

---

## api — Server-side PDF exports for 6 dashboard reports (added 2026-07-20)

**Status: complete.** All 6 PDFs use the shared IAL/PO-house letterhead (`letterhead.py`) and never touch the existing JSON endpoints' output — every JSON handler that now backs a PDF route was refactored to delegate to a shared `compute_*` function first (Redis cache-aside + `_response` wrapping stayed in the handler), then the PDF route reuses the same `compute_*` function with a fresh (uncached) DB connection.

**Bundled rendering assets (byte-identical copies from `lambda/alerts_evaluator/`, verified via `filecmp.cmp(..., shallow=False)`):** `letterhead.py`, `pdf_fonts.py`, `DejaVuSans.ttf`, `DejaVuSans-Bold.ttf`, `ial-logo.png`, `customer_balances_fy_pdf.py`, `supplier_balances_fy_pdf.py`, `monthly_sales_pdf.py`, `monthly_collection_pdf.py`. These 4 renderers are unmodified from the alerts_evaluator copies — same house design, same `_RS` inline-font rupee token. Keep both copies byte-identical when either is edited (matches this repo's existing cross-Lambda-shared-file convention — see the `customer_balances_fy.py` / `supplier_balances_fy.py` entries above).

**2 new shared compute modules (extracted from previously-inline handler SQL):**
- `api/ledger_statement.py::compute_ledger_statement(conn, account_name, from_date, to_date) -> dict` — moved verbatim out of `_handle_ledger_statement`.
- `api/supplier_ledger_statement.py::compute_supplier_ledger_statement(conn, account_name, from_date, to_date) -> dict` — moved verbatim out of `_handle_supplier_ledger_statement`.
- `_handle_ledger_statement` / `_handle_supplier_ledger_statement` now just validate params, check Redis, call the `compute_*` function, cache, and return — same cache keys/TTL/log lines as before; JSON shape unchanged (verified by re-reading both handlers post-refactor).

**2 new renderers (portrait A4, styled exactly like the other 4 report PDFs via `letterhead.py`):**
- `api/ledger_statement_pdf.py::render_ledger_statement_pdf(data) -> bytes` — title `CUSTOMER LEDGER STATEMENT`; Dr → RED (`#cc0000`), Cr → GREEN (`#1a6e35`) — same semantics as `customer_balances_fy_pdf.py`.
- `api/supplier_ledger_statement_pdf.py::render_supplier_ledger_statement_pdf(data) -> bytes` — title `SUPPLIER LEDGER STATEMENT`; Dr/Cr colors SWAPPED (Dr → GREEN, Cr → RED) — same semantics as `supplier_balances_fy_pdf.py`. Fully self-contained (no cross-import from `ledger_statement_pdf.py`), matching this package's existing convention of NOT sharing code between the paired customer/supplier renderers (see `customer_balances_fy_pdf.py` vs `supplier_balances_fy_pdf.py`).
- Both: one-row repeating header (`repeatRows=1`) — Date | Voucher No | Type | Debit (₹) | Credit (₹) | Balance (₹); a synthetic "Opening Balance" row first, then one row per statement entry with a running balance (`running += debit − credit`, rounded to 2dp per row), then a "TOTAL / Closing Balance" row. Long statements paginate automatically (the reportlab `Table` flowable splits across pages on its own; verified 400-row stub → 9 pages); `letterhead.draw_footer` is registered as both `onFirstPage` and `onLaterPages` so the footer reprints on every page.
- ₹ handling: every amount routes through `_RS` (`letterhead.register_fonts()`'s `<font name="DejaVuSans">₹</font>` token), including header sub-labels (`Debit (₹)` etc.) — never a bare `₹` char in a Helvetica Paragraph. Zero/blank placeholders use a plain hyphen `-`, not an em-dash.

**`_pdf_response(pdf_bytes, filename)`** added to `api/handler.py` (copied from `procurement_api/handler.py`) — base64-encodes the PDF body and sets `isBase64Encoded: true` + `Content-Type: application/pdf` + `Content-Disposition: attachment; filename="..."`. A new `_safe_filename_part(value)` helper (regex `[^A-Za-z0-9._-]+` → `_`) sanitizes account names/dates before they go into a `Content-Disposition` filename.

**6 new GET routes**, all in the existing GET-only routing block, each with a **local** `import <module>_pdf` inside its handler function (so a reportlab problem can never break the JSON routes) and **no Redis caching** (PDFs are always computed fresh from a new DB connection — cheap enough at current volume; revisit if PDF traffic grows):
- `GET /reports/customer-balances-fy/pdf?fy_count=all|2|3|4` → `customer_balances_fy.pdf`
- `GET /reports/supplier-balances-fy/pdf?fy_count=all|2|3|4` → `supplier_balances_fy.pdf`
- `GET /reports/monthly-sales/pdf?month=YYYY-MM` → `monthly_sales_<month>.pdf`
- `GET /reports/monthly-collection/pdf?month=YYYY-MM` → `monthly_collection_<month>.pdf`
- `GET /ledger/statement/pdf?account_name=&from_date=&to_date=` → `customer_ledger_statement_<account>_<from>_<to>.pdf` (each part sanitized via `_safe_filename_part`)
- `GET /supplier-ledger/statement/pdf?account_name=&from_date=&to_date=` → `supplier_ledger_statement_<account>_<from>_<to>.pdf` (sanitized)

**`requirements.txt`:** added `reportlab==4.2.2` (matches `alerts_evaluator/requirements.txt`'s convention — the GitHub Actions pipeline `pip install`s this into the Lambda Layer; no local install needed). Also flagged for the iac agent: the api Lambda's Terraform config (`lambda_api.tf`) needs the reportlab layer attached (same layer `alerts_evaluator` already uses) plus these 6 routes registered on the API Gateway HTTP API.

**Verification (2026-07-20):** `python -m py_compile` clean on all 11 changed/added files. All 6 `render_*` functions smoke-tested with representative stub dicts (₹ amounts, negatives, missing city/code/type, a 400-row multi-page statement, a zero-activity edge case) — every call returned valid `%PDF` bytes with no exceptions; the long ledger statement produced 9 pages (`pypdf` page count); `letterhead.register_fonts()` confirmed to return the real DejaVuSans inline-font token, not the `Rs.` fallback (i.e. the bundled TTF loaded correctly). `__pycache__` cleaned up after testing.

---

## api — GET /reports/customer-balances-fy

**Route:** `GET /reports/customer-balances-fy?fy_count=all|2|3|4`

**Query param:**
- `fy_count=all` (default) — every FY present in `customer_ledger`; opening balance = 0 for every party.
- `fy_count=2|3|4` — most recent N financial years; the first shown FY gets an opening balance brought forward from all transactions strictly before that FY's April 1 start.
- Any missing or invalid value defaults to `all`.

**Source tables:** `customer_ledger` (joined to `customer_details` for city and customer_code via `UPPER(customer_name) = UPPER(account_name)` match).

**FY definition:** April 1 → March 31. Label format: `FY YY-YY` (e.g. `FY 25-26`).

**Ledger fields used:** `transaction_date`, `account_name`, `category` (`Db`/`Cr`), `amount`. Filter: `out_z IS NULL AND LOWER(account_name) NOT LIKE '%%iravi%%'`.

**Row sort order:** rows are sorted by `code` ascending (zero-padded codes sort correctly as plain strings, e.g. `ANM001 < ANM002`), with NULL/blank-code rows placed last. Tie-breaker within each group is party name ascending.

**Per-FY credit split (added 2026-06-23):**
- `credit` = sum(Cr) where `sub_category != 'Customer Credit Notes'` — excludes credit notes (NO double-count).
- `credit_notes` = sum(Cr) where `sub_category = 'Customer Credit Notes'` — NEW separate bucket.
- `balance` = `opening + cumulative(debit − credit − credit_notes)` — numerically identical to the previous value because `credit + credit_notes` equals the old total Cr.
- The credit-note sub-category string is defined as `_CREDIT_NOTE_SUBCATEGORY` in `handler.py` for easy adjustment.
- `opening`, `balance_dr`, `balance_cr` are unchanged: the opening balance still uses ALL Cr (credit notes reduce it as before).

**Per-voucher netting (roundoff absorption, added 2026-06-23):**
- Aggregation now groups by `(party, voucher_no, fy_label)` before bucketing.
- For each voucher: `net = sum(Db rows) − sum(Cr rows)`. Roundoff and GST sub-components on the opposite side are absorbed, eliminating phantom paise credits/debits (e.g. BHAGHAVAN-style ₹0.20 phantom credit disappears; ₹3,35,226.20 Db + ₹0.20 Cr → net ₹3,35,226.00 debit only).
- Bucketing: `is_cn` vouchers → `credit_notes += -net`; `net > 0` → `debit += net`; `net < 0` → `credit += -net`.
- The sum of all voucher nets equals the old `sum(Db) − sum(Cr)`, so `balance`, `opening`, `balance_dr`, and `balance_cr` are numerically unchanged.

**Cache flush required after deploy:** the aggregation logic changed (per-voucher netting). Run `POST /admin/cache/flush` immediately after deploying to clear stale `iravi:reports:customer_balances_fy:*` entries from Redis. No re-ingest is needed (read-side only).

**Response shape:**
```jsonc
{
  "fys": ["FY 24-25", "FY 25-26"],           // shown FYs, oldest → newest
  "rows": [
    {
      "party": "NEW BHARAT TRADERS",
      "code": "ANK002",                       // party code from customer_details; null if no match or column not yet populated
      "city": "ANAKAPALLE",                   // null if no match in customer_details
      "opening": 0.0,                         // brought-forward balance; 0 when fy_count=all
      "per_fy": [
        { "fy": "FY 24-25", "debit": 0.0, "credit": 0.0, "credit_notes": 0.0, "balance": 0.0 },
        { "fy": "FY 25-26", "debit": 140047.0, "credit": 90000.0, "credit_notes": 10000.0, "balance": 40047.0 }
      ],
      "balance_dr": 40047.0,   // final running balance if > 0, else 0
      "balance_cr": 0.0        // final running balance (negative) if < 0, else 0
    }
  ],
  "totals": {
    "per_fy": [ { "fy": "FY 25-26", "debit": ..., "credit": ..., "credit_notes": ..., "balance": ... } ],
    "balance_dr": ...,
    "balance_cr": ...
  }
}
```

**Redis key:** `iravi:reports:customer_balances_fy:{fy_count}` (e.g. `iravi:reports:customer_balances_fy:all`). TTL: `_LEDGER_TTL` (1 hour). Cleared by `POST /admin/cache/flush`.

---

## api — GET /reports/supplier-balances-fy

**Route:** `GET /reports/supplier-balances-fy?fy_count=all|2|3|4`

**Query param:** identical semantics to `GET /reports/customer-balances-fy` — `fy_count=all` (default) shows all FYs with zero opening; integer values show the most recent N FYs with a brought-forward opening.

**Source tables:** `supplier_ledger` (`out_z IS NULL`, `LOWER(account_name) NOT LIKE '%%iravi%%'`) and `supplier_accounts` (city lookup only — `SELECT UPPER(name), city FROM supplier_accounts WHERE out_z IS NULL`, added 2026-07-12 so retired suppliers' cities are never used).

**Key differences from customer-balances-fy:**

| Aspect | Customer | Supplier |
|---|---|---|
| Ledger table | `customer_ledger` | `supplier_ledger` |
| Lookup table | `customer_details` | `supplier_accounts` |
| Party code | Yes (`code` in response) | No (`supplier_accounts` has no code column; omitted from response) |
| Credit notes | Yes (`credit_notes` bucket, `_CREDIT_NOTE_SUBCATEGORY`) | No — all Cr rows treated as credit |
| Per-voucher netting | `net > 0 → debit; net < 0 → credit; is_cn → credit_notes` | `net > 0 → debit; net < 0 → credit; net == 0 → nothing` |
| Balance formula | `running + debit − credit − credit_notes` | `running + debit − credit` |
| Sort order | Code ascending (NULLs last), then party name | Party name ascending (`(p.upper(), p)`) |
| RBAC screen key | `reports.customer_balances_fy` | `reports.supplier_balances_fy` |

**FY definition:** April 1 → March 31. Label format: `FY YY-YY` (e.g. `FY 25-26`). Identical logic to customer handler.

**Per-voucher netting:** rows are grouped by `(account_name, voucher_no, fy_label)`. `net = sum(Db rows) − sum(Cr rows)`. `net > 0 → debit += net`; `net < 0 → credit += -net`; `net == 0 → nothing`. Running balance per FY: `running = round(running + debit − credit, 2)`.

**Zero-activity skip:** parties (suppliers) with zero opening AND all-zero debit/credit across every shown FY are excluded from the response.

**Active-supplier-master filter (added 2026-07-12):** a party is included ONLY IF it exists as an active (`out_z IS NULL`) row in `supplier_accounts` — exact match on `UPPER(account_name) == UPPER(name)`, no fuzzy/substring matching. Implemented as `active_supplier_names = set(city_map.keys())` (the city lookup query already selects `UPPER(name) WHERE out_z IS NULL`, so its keys are exactly the active-master name set) and an early `continue` at the top of the per-party loop — this runs BEFORE `totals_per_fy`/`total_balance_dr`/`total_balance_cr` accumulate, so totals reflect only included parties. Ledger queries (`supplier_ledger WHERE out_z IS NULL AND account_name NOT LIKE '%iravi%'`) are unchanged; only output inclusion is affected. Does not apply to `customer_balances_fy.py` (customer report is unchanged).

**Response shape:**
```jsonc
{
  "fys": ["FY 24-25", "FY 25-26"],
  "rows": [
    {
      "party": "JAGRUTHI AGRO CHEMICALS",
      "city": "Hyderabad",                      // null if no match in supplier_accounts
      "opening": 0.0,                           // brought-forward balance; 0 when fy_count=all
      "per_fy": [
        { "fy": "FY 25-26", "debit": 0.0, "credit": 860000.0, "balance": -860000.0 }
      ],
      "balance_dr": 0.0,    // final running balance if > 0, else 0
      "balance_cr": -860000.0  // final running balance (negative) if < 0, else 0
    }
  ],
  "totals": {
    "per_fy": [ { "fy": "FY 25-26", "debit": ..., "credit": ..., "balance": ... } ],
    "balance_dr": ...,
    "balance_cr": ...
  }
}
```

Note: `balance_cr` is returned as a negative number when the supplier has a net credit balance (mirrors `_handle_customer_balances_fy` exactly: `round(running, 2) if running < 0 else 0.0`).

Empty-data case returns `{'fys': [], 'rows': [], 'totals': {'per_fy': [], 'balance_dr': 0.0, 'balance_cr': 0.0}}`.

**Redis key:** `iravi:reports:supplier_balances_fy:{fy_count}` (e.g. `iravi:reports:supplier_balances_fy:all`). TTL: `_LEDGER_TTL` (1 hour). Cleared by `POST /admin/cache/flush` (deletes all `iravi:*`).

**RBAC screen key (IaC + UI must register):** `reports.supplier_balances_fy`

**Runtime dependency:** `supplier_ledger` (IaC migration 017) must exist. `supplier_accounts` (IaC migration 016) is joined for city lookup only (LEFT JOIN — unmatched suppliers show null city). `etl_supplier_ledger` no longer reads `supplier_accounts` at all; it identifies supplier rows via col[10] of the ledger file.

---

## api — Supplier Aging endpoints (GET /supplier-ledger, /supplier-ledger/range, /suppliers/details)

**Added 2026-07-11.** Three read endpoints that feed the UI's client-side **Supplier
Balances (aging)** screen — the supplier-side counterpart to the customer aging screen,
which consumes `/ledger`, `/ledger/range`, and `/customers/details`. The FY report
(`/reports/supplier-balances-fy`) aggregates by FY and cannot drive date-bucketed aging,
so these expose the raw `supplier_ledger` transactions instead.

| Route | Source | Redis key | Handler |
|---|---|---|---|
| `GET /supplier-ledger/range` | `supplier_ledger` MIN/MAX `transaction_date` | `iravi:supplier_ledger:range` (24h) | `_handle_supplier_ledger_range` |
| `GET /supplier-ledger?from_date=&to_date=` | `supplier_ledger` rows in range | `iravi:supplier_ledger:data:{from}:{to}` (1h) | `_handle_supplier_ledger_data` |
| `GET /suppliers/details` | `supplier_accounts` (name, city) | `iravi:suppliers:details` (15m) | `_handle_supplier_details` |
| `GET /supplier-ledger/statement?account_name=&from_date=&to_date=` | `supplier_ledger` (one account) | `iravi:supplier_ledger:statement:{acct}:{from}:{to}` (1h) | `_handle_supplier_ledger_statement` |

- Exact mirrors of `_handle_ledger_range` / `_handle_ledger_data` / `_handle_customer_details`
  on the supplier tables. Same row shape (`transaction_date, voucher_no, account_name,
  category, sub_category, amount`) so the browser aging engine is a near-clone of the customer one.
- Both ledger endpoints filter `out_z IS NULL AND LOWER(account_name) NOT LIKE '%%iravi%%'`
  (the ETL already excludes IRAVI; the filter is belt-and-suspenders). `/suppliers/details`
  filters `out_z IS NULL` (supplier_accounts is milestoned).
- **No redis_updater step** pre-populates `iravi:supplier_ledger:range` (unlike the customer
  range, which redis_updater writes on `ETLCustomerLedgerSuccess`), so its first read always
  falls through to RDS then caches.
- **Aging semantics (client-side, in iravi-ui):** ages the **credit** side (unpaid purchase
  invoices = payables), `net = Σ Cr − Σ Db`, tracks the last Bank/Cash **Payment** — the
  inverse of the customer screen (which ages Db and tracks receipts). No server-side change
  needed for that; the endpoints just serve raw rows.

`_handle_supplier_ledger_statement` is an exact mirror of `_handle_ledger_statement`
(customer) on `supplier_ledger`: opening balance = Σ(Db − Cr) before `from_date`;
period rows grouped by voucher with the two sides netted (roundoff/GST absorbed);
running balance = Σ(Db − Cr). Same raw-ledger sign convention (Db positive) and same
response shape as `/ledger/statement` — the UI applies the supplier Dr/Cr **color swap**
(Dr green, Cr red) and drops the customer statement's "bank particulars for payment" block.
It feeds the Reports → **Supplier Ledger** statement screen.

**IaC:** API Gateway routes `GET /supplier-ledger`, `GET /supplier-ledger/range`,
`GET /supplier-ledger/statement`, `GET /suppliers/details` + CORS (all covered by the
existing GET CORS block); RBAC screen seeds `supplier_balances` (migration 020) and
`reports.supplier_ledger_statement` (migration 021). Cleared by `POST /admin/cache/flush`.

---

## api — GET /reports/monthly-sales

**Route:** `GET /reports/monthly-sales?month=YYYY-MM`

**Query param:** `month` in `YYYY-MM` format (e.g. `2026-06`). Absent or invalid → default to the current calendar month in IST.

**Source table:** `sales`, `out_z IS NULL`. Date column: `purchase_date`. Money column: `av`.

**Customer restriction (same as alerts Sales metric):**
`UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details) AND party NOT ILIKE '%iravi%'`

**Net sales per (day, state):** `SUM(av WHERE sales_return='N') − SUM(av WHERE sales_return='Y')`

**State mapping by branch:**

| Branch | State |
|---|---|
| `'Guntur C & F'` | `andhra` |
| `'Auto Nagar'` | `telangana` |
| *(any other)* | excluded from both buckets AND from `total`; branch name collected in `unmapped_branches`, warning logged |

**`total`** = andhra + telangana only (unmapped branches excluded).

**Values:** raw rupees (Python float, 2 dp). UI handles conversion to lakhs.

**`as_on_date`:** `min(today IST, last calendar day of selected month)`, formatted `YYYY-MM-DD`.

**`days`:** one entry per calendar day of the month (day 1 through last day), each `{date, andhra, telangana, total}` in raw rupees (0.0 where no data). All days included — future days are 0.0; the UI blanks them after `as_on_date`.

**`grand_total`:** sum over the full month per state (future days naturally contribute 0).

**FY definition:** April 1 → March 31. `fy_label` format: `"YYYY-YY"` (e.g. `"2026-27"`) for the FY containing the selected month. Months Jan–Mar belong to the FY whose April is in the previous calendar year.

**`analysis.up_to_prev_month`:** net sales per state from FY-start (April 1 of the containing FY) through the last day of the month before the selected month. If the selected month is April, this range is empty → all zeros.

**`analysis.prev_month_label`:** abbreviated month name (Python `%b`) of the month before the selected month, e.g. `"May"`, `"Jun"`.

**`analysis.as_on_date`:** equals `grand_total` (selected-month net per state); explicit copy for the UI's Sales Analysis "as on Date" column.

**`month_label`:** uppercase month + year, e.g. `"JUNE 2026"`.

**Response shape:**
```jsonc
{
  "month": "2026-06",
  "month_label": "JUNE 2026",
  "fy_label": "2026-27",
  "as_on_date": "2026-06-14",
  "days": [
    { "date": "2026-06-01", "andhra": 0.0, "telangana": 0.0, "total": 0.0 },
    ...
  ],
  "grand_total": { "andhra": 3776000.0, "telangana": 403000.0, "total": 4179000.0 },
  "analysis": {
    "prev_month_label": "May",
    "up_to_prev_month": { "andhra": 0.0, "telangana": 0.0, "total": 0.0 },
    "as_on_date":       { "andhra": 3776000.0, "telangana": 403000.0, "total": 4179000.0 }
  },
  "unmapped_branches": []
}
```

**Redis key:** `iravi:reports:monthly_sales:v2:{month}` (e.g. `iravi:reports:monthly_sales:v2:2026-06`). TTL: `_LEDGER_TTL` (1 hour). Cleared by `POST /admin/cache/flush`. Bumped `v2` on 2026-07-11 when the targets/YoY keys below were added, so stale old-shape cache entries never collide with the new payload shape.

**RBAC screen key (IaC + UI must register):** `reports.monthly_sales`

**IaC requirements:** API Gateway route `GET /reports/monthly-sales` + CORS in `lambda_api.tf`; `app_screens` seed migration to insert `reports.monthly_sales`. No new DB table or column needed for the base payload — reads from existing `sales` and `customer_details` tables. The targets/YoY extension (below) additionally reads `monthly_sale_targets` (already created by the `/config/monthly-targets` admin endpoint, migration TBD by IaC — see "What Is Built").

---

### Targets / year-over-year extension (added 2026-07-11)

`compute_monthly_sales` (both `lambda/api/monthly_sales.py` and the byte-identical
`lambda/alerts_evaluator/monthly_sales.py`) now also returns, alongside all the keys
above:

- **`projections`** `{andhra, telangana, total}` — the current month's target from
  `monthly_sale_targets` (state `AP`/`TG`, `month`/`yr` match), converted from lakhs
  to raw rupees (`× 100000`). `0.0` if no row / table absent.
- **`excess_short`** `{andhra, telangana, total}` — `grand_total − projections`, per state.
- **`targets_available`** `bool` — `False` (with all target/projection figures forced to
  `0.0`) if `monthly_sale_targets` does not exist yet (`to_regclass` check) — degrades
  gracefully, never raises.
- **`annual_position`** — `{prev_fy_label, cur_fy_label, prev_month_label_full,
  actual_sales_prev_fy, annual_target_cur_fy, upto_prev_month: {prev_fy, cur_fy, diff,
  growth_pct}}`. `actual_sales_prev_fy` = previous FY's full annual actual net sales
  (Apr 1 → Mar 31). `annual_target_cur_fy` = current FY's summed monthly targets
  (`monthly_sale_targets` rows Apr–Mar). `upto_prev_month.cur_fy` is the same value
  as `analysis.up_to_prev_month`. `growth_pct` is `None` when the prior-year figure is
  zero (avoids divide-by-zero), else `round((cur-prev)/prev*100, 2)`.
- **`month_only`** — `{month_name, prev_fy, cur_fy, diff}` — same calendar month,
  prior FY vs current FY (`cur_fy` == `grand_total`).
- **`cumulative_as_on`** — `{month_abbr, prev_fy_label, cur_fy_label, prev_fy_upto,
  cur_fy_as_on, diff}` — cumulative FY-to-date as of the report month: prior FY
  (FY-start → same month-end last year) vs current FY (`up_to_prev_month + grand_total`,
  i.e. FY-start through the as-on date of the selected month).

All new money fields are raw rupees, 2 dp, same convention as the rest of the payload.
Implemented via a shared helper `_net_sales_by_state(cur, start_date, end_date,
unmapped_branches)` that reuses the existing branch→state net-sales query across every
new date range (adds unmapped branches to the same shared set already used by the base
computation). Four extra queries (prevFY same-month, prevFY up-to-prev-month, prevFY
cumulative, prevFY full-annual) plus up to two targets queries — acceptable given the
1-hour Redis TTL.

**`monthly_sales_pdf.py` rebrand (2026-07-11):** `render_monthly_sales_pdf` restructured
to: (1) letterhead (bold company name, subtitle, date/lakhs-note — unchanged structure);
(2) **DAILY NET SALES** table (`DATE | AP | TS | SUB TOTAL`) with a shaded PROJECTIONS
row, 31 `DD-MM-YYYY` day rows (future blank, zero `-`, negative in parens), a dark-green
**G. TOTAL** band, and a shaded **EXCESS / SHORT** row (leading-minus negatives);
(3) **ANNUAL POSITION & CUMULATIVE SALES (UP TO {prev_month_label_full})** — two-row
spanning header (`STATE | Actual Sales {prevFY} | Annual Target {curFY} | UP TO
{prevMonth}` spanning `{prevFY}|{curFY}|DIFF|GROWTH %`), rows AP/TS/shaded SUB TOT;
(4) two side-by-side small tables — `"{MONTH} MONTH ONLY"` and `"CUMULATIVE — UP TO /
AS ON DATE"`, each `STATE | col | col | DIFF`, rows AP/TS/SUB TOT. All money in lakhs
(raw/100000) 2dp with Indian-style thousands grouping; `_HEADER_COLOR`/
`_TOTAL_BG_COLOR`/`_ALT_ROW_COLOR`/logo/`_draw_footer` unchanged. Verified by rendering
a PDF from a mocked payload and visually inspecting the rasterized page (reportlab +
PyMuPDF, scratch-only, not added to `requirements.txt`).

---

## alerts — Scheduled Alerts (admin-only)

**Status: complete (API + evaluator Lambda). Migration 015 adds `branch` column.**

### Database tables (created by IaC migration 013; migration 014 adds schedule_time; migration 015 adds branch)

| Table | Key columns |
|---|---|
| `alerts` | `id, name, category, frequency, schedule_day, schedule_time, match_type, is_active, created_by, created_at, updated_at, branch` |
| `alert_conditions` | `id, alert_id, field, op, value, value2` |
| `alert_recipients` | `id, alert_id, channel, address` |
| `alert_runs` | `id, alert_id, run_at, matched, status, error` |

**Migration 014** (apply manually via psql/SSM):
```sql
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS schedule_time TIME NOT NULL DEFAULT '11:00';
```
**Migration 015** (apply manually via psql/SSM before deploying this version):
```sql
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS branch VARCHAR(100);
```

### Alert categories

| Category | Description | Branch scoped? |
|---|---|---|
| `balances` | Per-customer outstanding balance evaluation (FIFO aging) | No |
| `sales` | Aggregate net customer sales over time windows | Yes |
| `sale_returns` | Aggregate customer sale returns over time windows | Yes |
| `customer_balances_fy` | Scheduled Customer Balances (FY) PDF report — always fires, no conditions | No |
| `supplier_balances_fy` | Scheduled Supplier Balances (FY) PDF report — always fires, no conditions | No |

### API endpoints (in `lambda/api/handler.py`) — ALL admin-only

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/alerts/fields?category=<cat>` | Field catalog for a category (balances/sales/sale_returns) |
| `GET` | `/alerts` | List alerts with nested `conditions[]` and `recipients[]` |
| `POST` | `/alerts` | Create alert + children in a transaction |
| `GET` | `/alerts/{id}` | Single alert |
| `PUT` | `/alerts/{id}` | Replace alert + conditions + recipients |
| `DELETE` | `/alerts/{id}` | Delete (cascade) |
| `POST` | `/alerts/{id}/test` | Dry-run evaluate NOW; response shape differs by category |

All routes use `_require_admin()` (recomputes `is_admin` from DB; rejects non-admins with 403).

### Field catalogs (`GET /alerts/fields?category=`)

**`?category=balances`** (unchanged):
```json
{"category":"balances",
 "fields":[{"key":"amount","label":"Outstanding amount (₹)","type":"currency","ops":["gt","gte","lt","lte","between"]},
           {"key":"age_days","label":"Age (days)","type":"integer","ops":["gt","gte","lt","lte","between"]},
           {"key":"days_since_last_receipt","label":"Days since last receipt","type":"integer","ops":["gt","gte","lt","lte","between"]}],
 "match_types":["all","any"],
 "frequencies":["daily","weekly","monthly"]}
```

**`?category=sales`**:
```json
{"category":"sales",
 "fields":[
   {"key":"net_sales_prev_day",        "label":"Net customer sales — previous day (₹)",                   "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_prev_week",       "label":"Net customer sales — previous week (₹)",                  "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_last_month",      "label":"Net customer sales — last month (₹)",                    "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_prev_quarter",    "label":"Net customer sales — previous fiscal quarter (₹)",       "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_fy",              "label":"Net customer sales — FY to date (₹)",                    "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_current_month",   "label":"Net customer sales — current month to date (₹)",         "type":"currency","ops":["gt","gte","lt","lte","eq","between"]}
 ],
 "match_types":["all","any"],
 "frequencies":["daily","weekly","monthly"],
 "branch_scoped":true}
```

**`?category=sale_returns`** (parallel to `sales`, labels "Customer sale returns — …"):
- Keys: `sale_returns_prev_day`, `sale_returns_prev_week`, `sale_returns_last_month`, `sale_returns_prev_quarter`, `sale_returns_fy`, `sale_returns_current_month`
- Same type/ops/match_types/frequencies/branch_scoped structure as `sales`.

**`?category=customer_balances_fy`** (added 2026-07-06):
```json
{"category":"customer_balances_fy","fields":[],"match_types":["all","any"],"frequencies":["daily","weekly","monthly"]}
```
- `fields` is empty — no conditions are configurable; the alert always fires on schedule.
- Not `branch_scoped` — no branch filter applied.
- `validate_alert` accepts `conditions: []` for this category (same as `sales`/`sale_returns`).

**`?category=supplier_balances_fy`** (added 2026-07-06):
```json
{"category":"supplier_balances_fy","fields":[],"match_types":["all","any"],"frequencies":["daily","weekly","monthly"]}
```
- `fields` is empty — no conditions are configurable; the alert always fires on schedule.
- Not `branch_scoped` — no branch filter applied.
- `validate_alert` accepts `conditions: []` for this category.
- Evaluator: calls `compute_supplier_balances_fy(conn, 'all')` → `render_supplier_balances_fy_pdf(data)`.
  Subject: `"IRAVI — Supplier Balances (FY) — DD Mon YYYY"`.
  Filename: `IAL_Supplier_Balances_FY_DD-Mon-YYYY.pdf`.
  PDF: landscape A4, DejaVuSans, NO Code column, NO Credit Notes column (3 sub-cols per FY:
  Debit/Credit/Balance). Footer legend: 'Dr = Debit (payable); Cr = Credit (advance/overpayment).'

### Time windows (IST, relative to run_date = today)

| Window key | Date range |
|---|---|
| `prev_day` | yesterday |
| `prev_week` | Mon–Sun of the completed calendar week immediately before the current week |
| `last_month` | 1st–last of the previous calendar month |
| `prev_quarter` | Previous fiscal quarter (FY Apr–Mar: Q1=Apr–Jun, Q2=Jul–Sep, Q3=Oct–Dec, Q4=Jan–Mar) |
| `fy` | April 1 of the current FY through yesterday (empty range if run_date is April 1) |
| `current_month` | First day of the current calendar month through yesterday (MTD). Empty range if run_date is the 1st of the month (no completed day yet). |

### Metric definition — sales and sale_returns categories

Source table: `sales`, `out_z IS NULL`.
Customer restriction: `UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details) AND party NOT ILIKE '%iravi%'`
Branch restriction: if `alert.branch` is set and not `'ALL'`/NULL, adds `AND branch = <alert.branch>`.
Money column: `av`.

- `sales` metric for a window = `SUM(av WHERE sales_return='N') − SUM(av WHERE sales_return='Y')` (rounded to 2 dp)
- `sale_returns` metric for a window = `SUM(av WHERE sales_return='Y')` (rounded to 2 dp)
- NULL sums treated as 0.

This matches the Overview's net-sales calculation.

### alerts.branch column (migration 015)

`branch VARCHAR(100)`, nullable. Accept/return `branch` in all alert CRUD endpoints.
- `sales`/`sale_returns`: branch filters the metric query. NULL or `'ALL'` = all branches.
- `balances`: branch accepted and stored but ignored in evaluation.

### Request body (POST /alerts, PUT /alerts/{id})
```jsonc
{
  "name": "Daily Net Sales Check",
  "category": "sales",          // balances | sales | sale_returns
  "frequency": "daily",
  "schedule_day": null,
  "schedule_time": "08:00",
  "match_type": "all",
  "is_active": true,
  "branch": "RAJAHMUNDRY",      // optional; null/'ALL' = all branches
  "conditions": [
    {"field": "net_sales_prev_day", "op": "lt", "value": 50000, "value2": null}
  ],
  "recipients": ["ops@iravi.in"]
}
```

### Response shape (GET /alerts, GET /alerts/{id}, POST /alerts, PUT /alerts/{id})
`recipients` is a **flat array of email-address strings** — NOT objects.
```jsonc
{
  "id": 1,
  "name": "Daily Net Sales Check",
  "category": "sales",
  "frequency": "daily",
  "schedule_day": null,
  "schedule_time": "08:00",
  "match_type": "all",
  "is_active": true,
  "created_by": "admin",
  "created_at": "2026-06-26T10:00:00",
  "updated_at": "2026-06-26T10:00:00",
  "branch": "RAJAHMUNDRY",
  "conditions": [
    {"id": 1, "field": "net_sales_prev_day", "op": "lt", "value": 50000.0, "value2": null}
  ],
  "recipients": ["ops@iravi.in"]
}
```

### POST /alerts/{id}/test — response shapes

**`balances` category (unchanged):**
```json
{"matched": 3, "sample": [{...customer dict...}]}
```

**`sales` / `sale_returns` categories — aggregate shape:**
```jsonc
{
  "category": "sales",
  "matched": true,
  "metrics": {
    "net_sales_prev_day": 42000.0
  },
  "conditions": [
    {
      "field": "net_sales_prev_day",
      "op": "lt",
      "value": 50000.0,
      "value2": null,
      "actual": 42000.0,
      "breached": true
    }
  ]
}
```
`metrics` contains only the windows referenced by the alert's conditions.

### Shared evaluation module (`lambda/api/alerts_eval.py`)

The module is imported by both the API Lambda and the evaluator Lambda.
Each Lambda package includes a copy; `lambda/api/alerts_eval.py` is the source of truth.
`lambda/alerts_evaluator/alerts_eval.py` is an identical copy (maintained with `cp`).

**Public surface:**
- `FIELD_CATALOG` — balances catalog (backward compat)
- `FIELD_CATALOG_SALES`, `FIELD_CATALOG_SALE_RETURNS` — aggregate catalogs (6 fields each, including `net_sales_current_month` / `sale_returns_current_month`)
- `FIELD_CATALOGS` — dict mapping category → catalog
- `compute_window_dates(run_date)` — returns `{window_key: (start, end)}` for all 6 windows (prev_day, prev_week, last_month, prev_quarter, fy, current_month)
- `evaluate_balances(conn, conditions, match_type, today)` — per-customer balances eval (unchanged)
- `evaluate_aggregate(conn, alert, today)` — aggregate eval for sales/sale_returns
- `_query_aggregate_metrics(conn, category, branch, windows, windows_needed)` — internal SQL helper
- `validate_alert(body)` — validates all three categories; field keys are per-category; auto-accepts `net_sales_current_month` / `sale_returns_current_month` via catalog-driven validation
- `is_alert_due_today(frequency, schedule_day, today)` — scheduling helper (unchanged)

### alerts_evaluator Lambda (`lambda/alerts_evaluator/handler.py`)

All existing gating is unchanged (due-today, time-reached, success-dedupe, 5/day failed cap).

**Category dispatch in the evaluation loop:**

- `balances` → `evaluate_balances()` → HTML customer-table email (unchanged path)
  - Subject: `[IRAVI Alert] <alert_name> — <date>`
  - `alert_runs.matched` = count of matched customers
- `customer_balances_fy` → **always fires** (unconditional) → PDF attachment email (added 2026-07-06)
  - Calls `customer_balances_fy.compute_customer_balances_fy(conn, 'all')` (from-beginning, all FYs,
    with credit notes) then `customer_balances_fy_pdf.render_customer_balances_fy_pdf(data)`.
  - Uses DejaVuSans TTFont (bundled, registered via `pdf_fonts.register_fonts()`) so ₹/— render.
  - Subject: `IRAVI — Customer Balances (FY) — <DD Mon YYYY>`
  - Body: `"Attached is the Customer Balances (FY) report."` (minimal HTML).
  - Attachment: `IAL_Customer_Balances_FY_<DD-Mon-YYYY>.pdf`
  - Sent via `_send_ses_email_with_pdf()`.
  - `alert_runs.matched` = 1 (always); `status` = `sent` or `failed`.
  - Does NOT call `evaluate_aggregate()` — has no conditions.
- `sales` → `evaluate_aggregate()` → **PDF attachment email** if `matched=True` (wired 2026-07-05)
  - Subject: `IRAVI — Daily Net Sales Report — <DD Mon YYYY>`
  - Body: minimal HTML paragraph "Attached is the Daily Net Sales Report" + "do not reply" footer. No Conditions table, no Window Metrics table.
  - Attachment: `IAL_Daily_Net_Sales_<DD-Mon-YYYY>.pdf` — built by calling `monthly_sales.compute_monthly_sales(conn, current_month_YYYY-MM)` then `monthly_sales_pdf.render_monthly_sales_pdf(data)`.
  - Sent via `_send_ses_email_with_pdf()` (SES `SendRawEmail` with MIME multipart).
  - `alert_runs.matched` = 1 if fired, 0 if not; PDF build/send wrapped in the same per-alert try/except so failures are recorded as `status='failed'` and do not abort other alerts.
- `sale_returns` → `evaluate_aggregate()` → metrics-summary email if `matched=True` (unchanged path)
  - Subject: `[IRAVI Alert] Sale Returns — <date>`
  - Email: two HTML tables — Conditions + Window Metrics
  - `alert_runs.matched` = 1 if fired, 0 if not
  - `status` = `sent` if fired, `no_match` if conditions did not fire, `failed` on exception

**`_send_ses_email_with_pdf(subject, recipients, html_body, pdf_bytes, pdf_filename)`** — stdlib MIME only (`email.mime.multipart.MIMEMultipart` + `MIMEText(html_body,'html')` + `MIMEApplication(pdf_bytes, _subtype='pdf')` with `Content-Disposition: attachment; filename=…`); sends via `ses.send_raw_email(Source, Destinations, RawMessage)`. No new AWS dependency — reuses the existing `ses` client.

`_send_ses_email` signature changed from `(alert_name, recipients, html_body, today)` to `(subject, recipients, html_body)` — subject is now built by the caller.

**`days_since_last_receipt` semantics (balances only, unchanged):**
- Value = (today − last_receipt_date) in days.
- NULL last_receipt_date → sentinel `10**9` flags "never paid" customers for `> threshold` rules.
- Displayed as `"Never"` in the email.

---

## auth — RBAC (login + admin management)

**Status: complete (phase 1).** New module `auth.py` (standard library only — no new layer deps):
PBKDF2-HMAC-SHA256 password hashing + compact HS256 JWT signed with the key from
Secrets Manager `iravi/dashboard/jwt` (`JWT_SECRET_ARN`).

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /auth/login` | public | Verify a DB user; **bootstrap** the admin from `BOOTSTRAP_ADMIN_*` on first login if no admin exists; return JWT + `{username, role_name, is_admin, screens}` |
| `GET /auth/me` | bearer | Re-read the caller's role + screens (so changes apply on refresh) |
| `GET /admin/screens` | admin | List mappable screens |
| `GET\|POST /admin/roles`, `PUT\|DELETE /admin/roles/{role_id}` | admin | Role CRUD (PUT replaces screen mappings; Administrator role protected) |
| `GET\|POST /admin/users`, `PUT\|DELETE /admin/users/{user_id}` | admin | User CRUD (password hashed; last-active-admin protected) |
| `POST /admin/cache/flush` | admin | Clear the dashboard Redis cache — `SCAN`s and deletes all `iravi:*` keys in batches of 500, returns `{deleted}`. Namespace-scoped (not `FLUSHDB`) so a shared Redis is left untouched; keys rehydrate from RDS via the existing cache-aside path on next request. UI: admin-only button left of the dark-mode toggle in the navbar. |

The admin guard recomputes `is_admin` from the DB (not the token). Tables: `app_users`,
`app_roles`, `app_role_screens`, `app_screens` (IaC migration 009).

**Enforcement scope (phase 1):** login + `/admin/*` are server-side enforced. The data
endpoints above are NOT yet per-role authorized — UI-only gating. **Backlog:** add an
`ENDPOINT_SCREENS` authorization check to every data route.

---

## What Is Built

- [x] **Supplier Ledger Statement PDF — mirrors the customer redesign, supplier semantics, no
  Bank Particulars block (2026-07-21):** follow-up to the Customer Ledger Statement redesign
  below — applies the SAME "account statement" layout to `supplier_ledger_statement_pdf.py`,
  adapted for payable/supplier semantics, per explicit client confirmation that the Bank
  Particulars block should be OMITTED for suppliers (suppliers don't pay into Iravi's account).
  - `supplier_ledger_statement.py::compute_supplier_ledger_statement` — added a `city` field via
    a new lookup on `supplier_accounts` (`WHERE UPPER(name) = UPPER(account_name) AND out_z IS
    NULL`, mirroring `ledger_statement.py`'s `customer_details` lookup and
    `supplier_balances_fy.py`'s existing city-lookup pattern). Cache key, opening/closing/
    per-voucher-netting logic, and every other JSON field are unchanged — additive only.
  - `supplier_ledger_statement_pdf.py::render_supplier_ledger_statement_pdf` — full rewrite,
    same structure as `ledger_statement_pdf.py`'s redesign (centered title
    `'{ACCOUNT NAME} ACCOUNT STATEMENT'`; `Location: {city or '-'}` / `Statement Date:
    DD-MM-YYYY` row; centered `Statement Period: ...` line with the identical same-FY/multi-FY
    logic; one `KeepTogether`-wrapped table per financial year with `Opening Balance` →
    `Brought Forward` carry-forward and a bold `Totals` row each, `repeatRows=1`; Balance column
    always plain black — `_RED`/`_GREEN` kept defined but unused; letterhead header repeating on
    every page via `letterhead.draw_header`/a per-file `_draw_header_footer`, `topMargin` bumped
    to reserve `letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3*cm`), with two
    supplier-specific differences:
    1. **Closing-balance banner label uses supplier (payable) semantics**, not the customer
       receivable/payable wording: `closing_balance > 0` (Dr) → `'Closing Balance Payable'`
       (Iravi owes the supplier — matches this codebase's existing convention, e.g.
       `supplier_balances_fy_pdf.py`'s "Dr → GREEN (payable, normal)"); `closing_balance < 0`
       (Cr) → `'Closing Balance Receivable'` (the supplier owes Iravi — an advance/
       overpayment); `0` → `'Closing Balance'`. Same white-on-`letterhead.GREEN` banner
       styling (11pt bold label left / 13pt bold right-aligned `_bal(closing_balance)`) as the
       customer statement.
    2. **NO Bank Particulars for Payment block** — the banner is wrapped in its own
       `KeepTogether([closing_banner])` with nothing else in the group (no bordered bank-details
       table, no disclaimer line) after the last FY table's own separate `KeepTogether`.
  - Verified: `python -m py_compile lambda/api/supplier_ledger_statement.py
    lambda/api/supplier_ledger_statement_pdf.py` clean. Smoke-tested (temp script in
    `lambda/api/`, deleted afterward with `__pycache__`) a ~160-row two-FY dataset sized to
    force pagination — `pypdf` confirmed **6 pages**, `'IRAVI AGRO LIFE LLP'` present on both
    page 1 and the last page (header repeats), `'Closing Balance Payable'` present for a
    positive closing balance, and confirmed `'Bank Particulars for Payment'` / `'UTIB0001922'`
    are ABSENT from the rendered text (no bank block leaked in). A second case (negative
    closing balance, zero rows, no city) confirmed `'Closing Balance Receivable'` and
    `'Location: -'` render correctly on a single page.
  - No IaC/DB/UI change required — same route (`GET /supplier-ledger/statement/pdf`), same
    cache key/params, additive JSON field only.

- [x] **Every-page letterhead header for all `api/` report PDFs (2026-07-21):** applied the
  canvas-drawn repeating-header treatment (`letterhead.draw_header`, introduced for the Customer
  Ledger Statement redesign below) to the four other `lambda/api/*_pdf.py` report renderers —
  previously each showed the IAL letterhead only on page 1 (`letterhead.build_header()` added
  once as a leading flowable); multi-page statements/reports had no header on continuation
  pages. Scope: **`lambda/api/` only** — the byte-identical `lambda/alerts_evaluator/` copies of
  these 4 renderers (used for SES email attachments) were intentionally left unchanged
  (page-1-only flowable header), so the two directories' copies are no longer byte-identical for
  these files — flagged in the file-tree note above so a future sync pass doesn't silently
  overwrite this divergence. `procurement_api/po_pdf.py` is out of scope (doesn't import
  `letterhead.py` at all — independent design). Files changed, each identically:
  `customer_balances_fy_pdf.py`, `supplier_balances_fy_pdf.py` (both landscape A4),
  `monthly_sales_pdf.py`, `monthly_collection_pdf.py` (both portrait A4).
  - Per file: removed `letterhead.build_header(_CONTENT_W)` from the flowable `elements` list
    (would otherwise double-render the header on page 1 once the canvas callback also draws
    it); `SimpleDocTemplate(topMargin=...)` changed from a fixed `0.6*cm`/`0.8*cm` to
    `letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3*cm` so flowing content starts
    below the header band (landscape's wider content width doesn't change the header's
    height, so the same margin works for both orientations); new per-file
    `_draw_header_footer(canvas, doc)` calling `letterhead.draw_header` then
    `letterhead.draw_footer`, passed as both `onFirstPage`/`onLaterPages` to `doc.build`
    (previously `letterhead.draw_footer` alone).
  - No changes to `letterhead.py` itself in this pass (already gained `draw_header`/
    `HEADER_TOP_PAD`/`HEADER_HEIGHT` in the Customer Ledger Statement redesign below) — this
    was purely wiring 4 more callers onto the existing mechanism.
  - Verified: `python -m py_compile` clean on all 4 files. Smoke-tested (temp script in
    `lambda/api/`, deleted afterward with `__pycache__`): `customer_balances_fy_pdf.py`
    rendered a 40-row synthetic dataset → 2 pages, `'IRAVI AGRO LIFE LLP'` confirmed present on
    both page 1 and the last page via `pypdf` text extraction (header repeats; landscape
    content width doesn't clip it). `supplier_balances_fy_pdf.py`, `monthly_sales_pdf.py`, and
    `monthly_collection_pdf.py` were each smoke-tested with a representative single-page
    payload — all three returned valid `%PDF`-prefixed bytes with no exceptions (no
    LayoutError from the increased topMargin).
  - No IaC/DB/UI change required — same routes, same cache keys/params, presentation-only.

- [x] **Customer Ledger Statement PDF — client-approved redesign (2026-07-21):** reworked
  `ledger_statement.py` + `ledger_statement_pdf.py` only.
  - `ledger_statement.py::compute_ledger_statement` — added a `city` field to the returned
    dict via a new LEFT-JOIN-style lookup on `customer_details`
    (`WHERE UPPER(customer_name) = UPPER(account_name) AND out_z IS NULL`, same
    case-insensitive match pattern as `customer_balances_fy.py`). Existing cache key,
    opening/closing/per-voucher-netting logic, and JSON response shape for every other key
    are unchanged — the JSON `/ledger/statement` endpoint now also returns `city` (harmless
    additive field, no consumer breaks).
  - `ledger_statement_pdf.py::render_ledger_statement_pdf` — full layout rewrite (kept
    portrait A4, `_RS`/Paragraph ₹ handling, `_fmt_inr`/`_bal`/`_fmt_date` helpers; letterhead
    usage changed — see point 7 below):
    1. Centered bold green title `'{ACCOUNT NAME} ACCOUNT STATEMENT'` (was a left-aligned
       fixed `'CUSTOMER LEDGER STATEMENT'` title).
    2. New `Location: {city or '-'}` (left, bold) / `Statement Date: DD-MM-YYYY` (right,
       muted) row directly under the title.
    3. New centered `Statement Period: ...` line — full Indian-FY boundaries
       (`FY DD-MM-YYYY to DD-MM-YYYY`) when `from_date`/`to_date` share an FY (Apr 1 → Mar
       31), else `DD-MM-YYYY to DD-MM-YYYY` snapping only the start to that FY's April 1.
    4. The statement is now split into **one table per financial year** (ascending), each
       wrapped in `KeepTogether` (heading + table never split across a page boundary unless
       the table itself exceeds a full page — `repeatRows=1` keeps the header visible on any
       such continuation). First FY's synthetic first row = `Opening Balance`; every later
       FY's first row = `Brought Forward` (carries the prior FY's closing balance); each FY
       ends with a bold `Totals` row (that FY's Σ debit/credit + closing balance). Replaces
       the previous single flat table (opening → all rows → one grand total).
    5. **Balance column is now always plain black** — the Dr=RED(`#cc0000`)/Cr=GREEN(`#1a6e35`)
       coloring (`dat_r_dr`/`dat_r_cr`/`open_r_*`/`tot_r_*` styles + `color_cmds` TEXTCOLOR
       list) was removed entirely; `_bal()`'s Dr/Cr-suffixed text is unchanged, just rendered
       uncolored. `_RED`/`_GREEN` constants are kept defined (unused) per the design brief.
    6. New **closing-balance banner** (white-on-`letterhead.GREEN`) placed directly above the
       Bank Particulars block, after the LAST FY table only — a single-row 2-cell `Table`:
       left cell white-bold-11pt label (`'Closing Balance Receivable'` if
       `data['closing_balance'] > 0`, `'Closing Balance Payable'` if `< 0`, else
       `'Closing Balance'`), right cell white-bold-13pt right-aligned `_bal(closing_balance)`
       (e.g. `₹6,83,976.00 Dr`). Explicit, documented exception to point 5's
       "balance is always black" rule — that rule only governs the per-row Balance column
       inside the FY tables, not this banner.
    7. New "Bank Particulars for Payment" block right after the banner — bordered key/value
       table with hardcoded IAL account details (new module constants
       `_BANK_ACCOUNT_NAME='IRAVI AGRO LIFE LLP'`, `_BANK_ACCOUNT_NO='925020021374991'`,
       `_BANK_NAME='Axis Bank, Moti Nagar, Hyderabad'`, `_BANK_IFSC='UTIB0001922'`, mirroring
       how `letterhead.py` hardcodes GSTIN/LLPIN/etc.) plus a muted italic disclaimer line
       (`"Should the payment have already been made, kindly disregard this notice."`). The
       **banner + heading + bank table + disclaimer are all one `KeepTogether` unit** (not
       separate flowables) so the whole group moves to the next page together if it doesn't
       fit in the remaining space — the banner can never be stranded from its bank details by
       a page break, and this group is a SEPARATE `KeepTogether` from the last FY table's own
       (per point 4), not nested inside it.
    8. **Letterhead header now repeats on every page** — previously `letterhead.build_header()`
       was added once as a leading flowable (page 1 only; later pages of a multi-page
       statement had no header). `letterhead.py` gained a new `draw_header(canvas, doc)`
       page-callback function (additive-only — does NOT touch `build_header()`/`draw_footer()`,
       so every other consumer of `letterhead.py` — `customer_balances_fy_pdf.py`,
       `supplier_balances_fy_pdf.py`, `monthly_sales_pdf.py`, `monthly_collection_pdf.py`,
       `supplier_ledger_statement_pdf.py` — is unaffected) plus two new constants
       `HEADER_TOP_PAD = 0.35*cm` / `HEADER_HEIGHT = 3.0*cm`. `draw_header` renders
       `build_header()`'s own flowables into a throwaway `reportlab.platypus.Frame` positioned
       at the top of the current page — guarantees pixel-identical output to the flowable
       version (no re-implementation/drift risk) while running on every page via the canvas
       callback. `ledger_statement_pdf.py`: removed `letterhead.build_header(_CONTENT_W)` from
       the flowable `elements` list (would otherwise double-render the header on page 1);
       `SimpleDocTemplate(topMargin=...)` changed from a fixed `0.6*cm` to
       `letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3*cm` so flowing content
       (title/location/period/tables) starts below the header band and never overlaps it; new
       module-level `_draw_header_footer(canvas, doc)` calls `letterhead.draw_header` then
       `letterhead.draw_footer`, passed as both `onFirstPage`/`onLaterPages` to `doc.build`
       (was `letterhead.draw_footer` alone).
    - Edge case handled: a period with zero transactions still renders one FY table (the
      period's starting FY) carrying just the opening/closing position, so the PDF is never
      blank.
  - Verified: `python -m py_compile lambda/api/ledger_statement.py
    lambda/api/ledger_statement_pdf.py lambda/api/letterhead.py` clean. Smoke-tested
    `render_ledger_statement_pdf` (temp scripts in `lambda/api/`, deleted afterward along with
    `__pycache__`) against (a) a synthetic multi-FY dataset (opening balance, rows in FY
    2025-26 and FY 2026-27, a same-FY period, and a zero-rows period) — every call returned
    valid `%PDF`-prefixed bytes (~237 KB) with no exceptions; (b) a ~160-row two-FY dataset
    sized to force pagination — `pypdf` confirmed **6 pages**, with `'IRAVI AGRO LIFE LLP'`
    (the letterhead company name) present in the extracted text of BOTH page 1 and the last
    page, confirming the header now repeats; a negative-closing-balance / zero-row single-page
    variant confirmed the banner text `'Closing Balance Payable'` renders correctly for a Cr
    closing balance. Also re-`py_compile`d the five other `letterhead.py`-consuming PDF
    renderers (`customer_balances_fy_pdf.py`, `supplier_balances_fy_pdf.py`,
    `monthly_sales_pdf.py`, `monthly_collection_pdf.py`, `supplier_ledger_statement_pdf.py`) —
    all clean, confirming the additive `letterhead.py` change (new `Frame` import, new
    `draw_header`, two new constants; `build_header`/`draw_footer` untouched) doesn't break
    them.
  - No IaC/DB/UI change required — same route (`GET /ledger/statement/pdf`), same cache
    key/params, additive JSON field only.

- [x] **Bug fix: Job Work PO showed ₹0.00 total on the PO list (2026-07-20):** root cause —
  `amount`/`gst_amount`/`total_value` in `_PO_SELECT` are computed from the header
  `quantity * rate`, but for `po_type == 'JOB_WORK'` the header `rate` is always 0 (all
  pricing lives on `purchase_order_items` line items), so those three fields evaluated to
  0 for every Job Work PO in both `GET /purchase-orders` (list) and the detail response.
  BULK POs were unaffected (header `rate` is real for BULK). Fix: new helper
  `_po_apply_job_work_totals(po)` in `handler.py` — for JOB_WORK rows, once `items[]` is
  attached, overrides `amount = round(Σ item.amount, 2)`, `gst_amount = round(amount *
  gst_rate / 100, 2)`, `total_value = round(amount + gst_amount, 2)` (same rounding
  convention as the BULK SQL). Called from both `_po_list` (after `_po_items_for_many`)
  and `_po_get_one` (after `_po_items_for`), so list and detail always agree; BULK path
  untouched (helper is only invoked when `po_type == 'JOB_WORK'`). Note:
  `po_pdf.py`'s `_render_job_work_po_pdf` was already computing its own Σ-of-items totals
  independently for the PDF — only the JSON API response was wrong, so no PDF change was
  needed. Verified: `python -m py_compile handler.py po_pdf.py` clean; a logic-level check
  (boto3/psycopg2/auth stubbed, run via a temp script then deleted) confirmed a JOB_WORK
  PO with items 3000@250 and 2000@300 (taxable 750,000 + 600,000 = 1,350,000) at
  `gst_rate=18` yields `amount=1,350,000.0`, `gst_amount=243,000.0`,
  `total_value=1,593,000.0`; a simulated BULK PO's fields were confirmed byte-for-byte
  unchanged by the same helper (no-op — only called for JOB_WORK). No IaC/DB/UI change
  needed — same response shape, just corrected values.

- [x] **`barcodes` added to the `purchases`/`sales` milestoning key (2026-07-14):** the 5-column
  natural key `(purchase_date, voucher_no, branch, party, product)` did not uniquely identify a
  line item — a single voucher legitimately carries the same product on multiple batch/barcode
  lines, and the close-then-insert loop collapsed them (last-writer-wins). Observed in
  `AppendixPurchaseReport.xlsx`: 785 parsed rows → 770 stored (15 real rows lost across 7 vouchers;
  worst was PVA2526-48 / SUNITHA GRAPHICS collapsing 7 lines → 1). Fix: added
  `COALESCE(barcodes,'') = COALESCE(%s,'')` to the UPDATE predicate in all four ETL handlers
  (`etl_appendix_b_x11_purchase`, `_purchase_return`, `_sale`, `_sale_return` — `_upsert_purchases`
  / `_upsert_sales`) and widened both partial unique indexes to include `COALESCE(barcodes,'')`
  (IaC migration `031_add_barcodes_to_purchases_sales_key.sql`). `COALESCE` keeps NULL/'' barcodes
  deduping as one value (a bare nullable column in a UNIQUE index would let multiple NULL rows stay
  active). Verified: `py_compile` clean on all four handlers; in-memory simulation of the new-key
  load against the sample file → 785 parsed / 785 active / 0 lost. **IaC needed:** apply migration
  031 via psql/SSM. **Re-ingest required after migration lands:** re-upload the Appendix
  purchase/sale/return files to S3 `raw/` so the previously-collapsed batch lines are backfilled
  (the new UPDATE only closes the exact-barcode match, so the survivor re-versions cleanly and the
  missing lines insert fresh — no duplicates). No Redis change (purchases/sales are read fresh; run
  `POST /admin/cache/flush` if any sales-derived report cache needs refreshing).

- [x] **`procurement_api` Lambda — Procurement dashboard CRUD backend (2026-07-13):** new folder
  `lambda/procurement_api/` powering `procurement.iraviagrolife.com`. Standalone Lambda (its own
  API Gateway, see IaC `production/procurement/` module) reusing the shared RBAC — `auth.py` is a
  copy of `api/auth.py` (PBKDF2 + HS256 JWT), and login validates against the SAME `app_users` /
  `app_roles` / `app_screens` tables using the SAME `JWT_SECRET_ARN`, so users are managed once in
  the dashboard's Access Control. `POST /auth/login` + `GET /auth/me` are public/token; every other
  route requires a valid bearer token (any authenticated user — per-screen authz is UI-only, phase 1).
  CRUD over the `procurement.*` schema (migration 026): `GET/POST /technicals`, `/packaging-meta`,
  `/packagings`, `/signatory-authorities`, `/supplier-companies`, `/suppliers`, `/enquiries`, `/pdc`,
  `/purchase-orders` + `PUT/DELETE /<resource>/{id}`, plus `GET /purchase-orders/{id}/pdf`.
  **No Redis** (low-volume write-heavy config data → straight to RDS). Env: `DB_SECRET_ARN`,
  `JWT_SECRET_ARN`. `requirements.txt` = psycopg2-binary (reuses the existing `api_deps` layer via
  IaC — no new CI layer step). ForeignKeyViolation on delete → 409 "in use"; UniqueViolation → 409.
  Dates/Decimals JSON-serialized via a custom default. `py_compile` clean.
  DB: migrations `026_create_procurement_schema.sql` (schema + 5 tables), `027_add_procurement_screens.sql`
  (RBAC screen seeds `procurement.*`), `028_seed_procurement_data.sql` (seed from `IAL Enquiry.xlsx`).
  **Supplier-company address extension (2026-07-16):** `_companies_list/create/update` now also
  read/write `address_line1`, `address_line2`, `address_line3`, `state`, `pin_code`, `gstin`
  (all nullable, via shared `_COMPANY_COLS`); legacy `location` retained in SELECT/INSERT/UPDATE
  for back-compat. Requires **IaC migration `032_add_supplier_company_address.sql`** (additive
  `ALTER TABLE procurement.supplier_companies ADD COLUMN ...`) applied via psql before deploy.
  **Packaging Meta + Packagings CRUD (2026-07-16):** two resources.
  `/packaging-meta` (`_packaging_meta_list/create/update/delete`) is the master size list per unit
  type — `unit_type` in (`KG`,`LTR`), `label` (verbatim report string), `sort_order`, `is_active`;
  server validates `unit_type`. `/packagings` (`_packagings_list/get_one/create/update/delete`)
  assigns a size to a brand: rows FK `technical_id` → technicals (brand carrier) + `packaging_meta_id`
  → packaging_meta; the list JOINs both to return `brand_name`, `technical_name`, `unit_type`, and
  `packaging` (the meta label). Routes for both added to `routes`/`item_routes`. Requires **IaC
  migrations** `033_create_procurement_packaging_meta.sql`, `034_seed_procurement_packaging_meta.sql`
  (KG+LTR sizes from the stock PDF), `035_create_procurement_packagings.sql` (FK technicals CASCADE +
  packaging_meta RESTRICT, unique `(technical_id, packaging_meta_id)`), `036_add_procurement_packaging_
  screens.sql` (screens `procurement.packaging_meta` + `procurement.packagings`) applied via psql,
  plus the 8 API Gateway routes in the `production/procurement/` module.
  **Signatory Authorities CRUD (2026-07-16):** new `/signatory-authorities` resource
  (`_signatories_list/create/update/delete`) — flat master (name required; title, department
  nullable via shared `_SIGNATORY_COLS`; unique name). Routes added to `routes`/`item_routes`.
  Requires **IaC migration `037_create_procurement_signatory_authorities.sql`** +
  `038_add_procurement_signatory_authority_screen.sql` (RBAC screen `procurement.signatory_authorities`)
  applied via psql, plus the 4 API Gateway routes in the `production/procurement/` module.
  **Purchase Orders (Bulk) + PDF export (2026-07-16):** new `/purchase-orders` resource
  (`_po_list/get_one/create/update/delete`) + `GET /purchase-orders/{id}/pdf`. `_PO_SELECT` joins
  supplier / bill-to / ship-to `supplier_companies` (renders their address+GSTIN), the product
  `technicals`, and `signatory_authorities`. **PO number** generated server-side as
  `IAL/{fy}/{po_seq}` where `fy` is the 4-digit financial-year code (`_fy_code`, Apr-Mar → e.g. `2627`
  for FY 2026-27) — `_po_create` computes `MAX(po_seq)+1` **for that FY** and inserts atomically,
  retrying on `UniqueViolation` (guarded by unique `(fy, po_seq)` + unique `po_no`); serial resets per FY;
  po_no/po_date/po_seq are immutable on update. **Amounts are computed:** the PO stores numeric
  `rate` (₹/unit) + `gst_rate` (%), and `_PO_SELECT` returns `amount` (= qty×rate), `gst_amount`,
  and `total_value` (rounded) — `_po_validate` was updated from the old free-text `price`/`gst` to
  numeric `rate`/`gst_rate`.
  **`po_pdf.py` — formal IAL house design (rewritten 2026-07-16 to match `IAL_PO_..._formal_2.pdf`):**
  single-page A4 (spacing-tuned to fit, with a boxed VENDOR/SUPPLIER block and a fluorescent-
  highlighted note) — a two-tone letterhead (IAL logo + centered "IRAVI AGRO LIFE LLP" + orange
  tagline "Nurturing Life, Protecting the Harvest" + GSTIN/LLPIN/email/website line, green+orange
  rules), a "PURCHASE ORDER" title with a PO Number/Date box, green **ORDER DETAILS** goods table
  (SL/DESCRIPTION/QUANTITY/UOM/RATE ₹/AMOUNT ₹), a Taxable/GST/Total block + **amount-in-words**
  (Indian numbering, computed in-module), BILL TO / SHIP TO, a **COMMERCIAL TERMS** table, seven
  standard **TERMS & CONDITIONS**, a peach highlighted note, signature block and registered-office
  footer. Palette green `#17452f` / orange `#c8641e`. **Base font is built-in Helvetica** (Arial-metric
  — matches the template's Liberation Sans; font sizes lifted directly from the template so it reads as
  the professional original, not the earlier clunky DejaVu render). **DejaVuSans (regular) is bundled**
  in this dir ONLY to render the **₹** glyph (Helvetica lacks it) via an inline `<font>` span; ₹ degrades
  to "Rs." if the TTF is absent. `ial-logo.png` also bundled. Fits one A4 page. Binary response via new
  `_pdf_response` helper (`isBase64Encoded`).
  `requirements.txt` adds `reportlab==4.2.2` (provided at runtime by the shared reportlab layer —
  IaC reuses `alerts_evaluator_deps`; not packaged from procurement's requirements). Requires **IaC
  migrations** `039_create_procurement_purchase_orders.sql` + `040_add_procurement_purchase_order_screen.sql`,
  the 5 API Gateway routes, and the reportlab layer attached to the procurement Lambda (all in the
  `production/procurement/` module). Verified: `py_compile` clean; `render_po_pdf` smoke-tested against
  a representative row (rasterized page visually matches the reference PO).
  UI: `procurement-ui` repo. **IaC needed (done):** `production/procurement/` module (Lambda + API GW +
  Amplify). **Manual:** apply 026→027→028 via psql; admins grant `procurement.*` screens to procurement
  roles in Access Control.

- [x] **Purchase Orders — Job Work PO type (JOB_WORK) added alongside Bulk (2026-07-20):**
  extends `_po_validate`/`_po_create`/`_po_update`/`_PO_SELECT`/`po_pdf.py` to support a second,
  multi-line PO type on the SAME `purchase_orders` row (`po_type` discriminator, already a column)
  plus N child rows in **`procurement.purchase_order_items`** (new table — IaC migration, assumed
  present; not created here). BULK is untouched (same single-line shape, same numbering, same PDF
  bytes via a renamed `_render_bulk_po_pdf` helper).
  - `handler.py`: `_po_validate` now branches on `po_type` ('BULK' default | 'JOB_WORK'). BULK keeps
    `quantity_unit` in `KGS|LTRS` exactly as before. JOB_WORK accepts `KGS|TONNE|LTRS|KL` and
    **requires** a non-empty `items[]` (`{technical_id, packaging_id?, quantity, rate}`); each item's
    `amount` is computed server-side (`quantity*rate`, never trusted from the client). New
    `_po_validate_items` enforces the **reconciliation guard**: Σ item quantities (each already in
    its base unit — KGS or LTRS) must equal the header quantity converted to that base unit
    (TONNE→×1000 KGS, KL→×1000 LTRS, KGS/LTRS→as-is), tolerance `< 0.01`; violation → 400 with
    message `"Item quantities total {X} {BASE}, but header quantity is {Y} {UNIT} ({Z} {BASE}).
    They must match."`.
  - `_po_create`/`_po_update`: for JOB_WORK, the header write and the item replace
    (`DELETE FROM purchase_order_items WHERE po_id=...` then re-insert with sequential `sl_no`
    starting at 1) happen on the **same cursor/transaction** so either both commit or both roll
    back. `_po_update`'s JOB_WORK path no longer reuses the generic `_write()` helper (which opens
    and closes its own connection) — it does its own `cur.execute`/`conn.commit()` with the same
    ForeignKeyViolation→409/UniqueViolation→409 handling `_write()` provides; the BULK branch of
    `_po_update` is unchanged (still via `_write()`). PO numbering (`IAL/{fy}/{seq}`, retry-on-race)
    is untouched and shared by both types.
  - `_PO_SELECT` already exposed `po_type`; added `_po_get_one`/`_po_list` now attach an `items[]`
    array (empty `[]` for BULK). List avoids N+1 via one extra query keyed by `po_id = ANY(%s)`
    for all JOB_WORK ids on the page (`_po_items_for_many`), grouped in Python. New
    `_PO_ITEMS_SELECT_BASE` joins `technicals` (name/brand) and `packagings → packaging_meta`
    (same join `_PACKAGING_SELECT` uses) to expose `packaging` (the size label). Item element shape:
    `{sl_no, technical_id, technical_name, brand_name, packaging_id, packaging, quantity, rate,
    amount}`.
  - `po_pdf.py`: `render_po_pdf(po)` now dispatches on `po['po_type']` — `_render_bulk_po_pdf`
    (renamed from the old `render_po_pdf`, body byte-for-byte unchanged) vs new
    `_render_job_work_po_pdf`. JOB_WORK layout reuses all shared styling/helpers (`_header`,
    `_section_label`, `_addr_para`, `_po_box`, `_TERMS`, amount-in-words, footer): title "JOB WORK
    PURCHASE ORDER"; a tinted "PRODUCT" box (Product/Brand/header Quantity+unit) below the "JOB
    WORKER" (renamed vendor) box; ORDER DETAILS renders a multi-row grid (SL / PARTICULARS /
    QUANTITY / RATE ₹ / AMOUNT ₹, one row per item, `PARTICULARS = "{technical_name} - {brand_name}
    - {packaging}"` with missing brand/packaging omitted, plus a TOTAL row) whose Σ amount feeds
    the same green Taxable/GST/Total band + amount-in-words (NOT the header `quantity*rate` SQL
    field — items carry the real per-line economics); BILL TO/SHIP TO relabeled "TO BE BILLED ON"
    / "DELIVERED AT" (same `_addr_para()`, unchanged addressing logic); "Thanking you," / "Yours
    faithfully," (already present in the BULK signature block) reused as-is. Single A4 page, no
    pagination. Verified: `py_compile` clean on both files; `_po_validate` logic-tested (reconcile
    pass/fail incl. tolerance, BULK unit unaffected, JOB_WORK missing-items 400) with boto3/psycopg2
    stubbed out; `render_po_pdf` smoke-tested for both BULK (239 KB) and JOB_WORK (240 KB, incl. a
    defensive empty-items edge case) — no exceptions, non-trivial byte counts.
  - **Not done here (IaC/UI follow-up):** the `procurement.purchase_order_items` table + its
    migration (FK `po_id → purchase_orders ON DELETE CASCADE`, FK `technical_id → technicals`, FK
    `packaging_id → packagings` nullable, UNIQUE `(po_id, sl_no)`) is owned by the `iac` agent —
    this handler assumes it exists. `procurement-ui` needs a JOB_WORK create/edit form (items grid
    with add/remove rows, client-side mirror of the reconciliation guard) and to send `po_type` +
    `items[]` in the create/update body; `GET`/list responses now always include `items` (empty for
    BULK) so the UI can render either type from one PO list.

- [x] **Purchase Order PDF fixes + `include_terms` flag (2026-07-20):** review pass on the JOB_WORK
  PDF; four `po_pdf.py` fixes applied to BOTH BULK and JOB_WORK, plus one `handler.py` field.
  - **Title unified:** JOB_WORK no longer renders "JOB WORK PURCHASE ORDER" — both PO types now show
    the identical letter-spaced "PURCHASE ORDER" banner via a new shared `_po_title_cell(st)` helper.
  - **Supplier GSTIN inline:** the supplier/vendor box (new shared `_vendor_box(po, st, dw)` helper,
    used by both BULK's "VENDOR / SUPPLIER" and JOB_WORK's "JOB WORKER" sections) now appends
    `GSTIN: <gstin>` to the same paragraph as the last address line (`&nbsp;&nbsp;&nbsp;` separator)
    instead of rendering it as its own line.
  - **Product summary centered:** JOB_WORK's `{technical} — Brand: X — Quantity: Y` line now uses a
    new centered style `st['bodyc']` (was `st['body']`, left-aligned).
  - **`include_terms` flag:** `po_pdf.py` reads `po.get('include_terms', True)`; when falsy the
    Terms & Conditions section is omitted entirely (both PO types). `handler.py` accepts the new
    boolean body field `include_terms` (default `True`, coerced via `bool()`) in `_po_validate`,
    persists it in `_po_create`/`_po_update` (new `procurement.purchase_orders.include_terms` column,
    **IaC migration owned by the `iac` agent — assumed present, not created here**), and returns it
    in `_PO_SELECT` (so the PDF renderer and the UI both see it).
  - **One-page-else-terms-on-fresh-page layout (reworked twice during this session — final approach
    below):** the flow order was changed so Note + Signature (new shared `_note_flow` /
    `_signature_flow` helpers) render immediately after Commercial Terms, and Terms & Conditions
    (new shared `_terms_flow` helper) is now the LAST section — previously it sat between Commercial
    Terms and Note. New `_build_pdf(core_flow, terms_flow, title)` does a **single** `doc.build()`
    call, wrapping `terms_flow` in one `reportlab.platypus.KeepTogether`: if it fits in the space
    remaining on the current page it stays there (no forced blank page); if not, the whole section
    (never split mid-list) moves to a fresh page. `_signature_flow`'s block is ALSO wrapped in its
    own `KeepTogether` — without it, the trailing signatory name/title/department paragraphs could
    separate from the "Yours faithfully / For IAL / signature line" above them and strand on their
    own near-empty page (found via the page-content smoke test below). **Two earlier approaches were
    tried and abandoned:** (1) a naive two-pass "measure page count, then re-add a `PageBreak()`
    before terms" — broke because reportlab flowables are STATEFUL (`wrap()`/`split()` mutate
    internal caches during `doc.build()`), so reusing the same Paragraph/Table instances across a
    second `.build()` call corrupted layout and raised spurious `LayoutError`s; (2) making the
    two-pass rebuild flowables fresh via a builder callable fixed the crash but still forced an
    unnecessary near-blank page whenever the core content itself (large item grids) already spilled
    past page 1 for reasons unrelated to terms. The final single-pass `KeepTogether`-only approach
    has neither problem and needs no page-count measurement at all.
  - Verified: `python -m py_compile handler.py po_pdf.py` clean. Smoke-tested `render_po_pdf` (via a
    deleted temp script) for BULK (1 page, terms on/off), JOB_WORK 2-row (2 pages terms-on, 1 page
    terms-off), and JOB_WORK at 5/6/7/8/9/10/15/20/25/30/40 rows terms-on (2 pages through 25 rows,
    3 pages at 30+ once the item grid itself needs 2 pages) — Terms & Conditions and the signature
    block were confirmed (via per-page `pypdf` text extraction) to always land intact on a single
    page, never split across the boundary, and no `LayoutError`s at any row count.

- [x] **Job Work PO PDF density fix — core content now fits 1 page through 7 line items
  (2026-07-20):** the previous round's `include_terms` layout still let a JOB_WORK PO's "core"
  (everything except Terms & Conditions) overflow onto page 2 once there were more than ~2 items,
  which meant `terms=False` wasn't producing a true single page and `terms=True` wasn't
  deterministically "core p1 / terms p2" — sometimes 2 pages, sometimes (with very short item text)
  1 page, sometimes 3 once the grid itself spilled. Two changes in `po_pdf.py`:
  - **Tightened vertical spacing across the shared core helpers** (apply to BOTH BULK and
    JOB_WORK, since they're shared): `_section_label` HRFlowable `spaceBefore`/`spaceAfter` 2/4 →
    1.5/2.5; `_po_box` (PO Number/Date) row `TOPPADDING`/`BOTTOMPADDING` 5 → 4; `_vendor_box`
    (VENDOR/SUPPLIER + JOB WORKER) 6 → 4; `_note_flow` note-band padding 6 → 4 and its leading
    `Spacer` 4 → 3; `_signature_flow` internal spacers 3/6/30 → 2/4/20 (the 30pt physical-signature
    gap was the single biggest line item) and its `HRFlowable spaceAfter` 4 → 3. Plus
    JOB_WORK-specific: `PRODUCT` box padding 6 → 4; salutation `Spacer` 11 → 6; particulars grid
    (`gtab`) row `TOPPADDING`/`BOTTOMPADDING` 3 → 2 (biggest lever — scales with item count);
    totals band (`right`/`tot` tables) paddings 4/5 → 2.5/4; `Spacer` before the totals band 4 → 3;
    "TO BE BILLED ON / DELIVERED AT" (`bs`) row padding 3 → 2 and its leading `Spacer` 3 → 2;
    Commercial Terms (`ctab`) row padding 2.5 → 1.5. One modest font-size reduction: the grid's
    `cell`/`cellc`/`cellr` styles (also reused by BULK's single-row grid) 8.7pt → 8.5pt.
  - **Deterministic terms-on page break:** `_render_job_work_po_pdf` now appends an explicit
    `reportlab.platypus.PageBreak()` before the terms `KeepTogether` block whenever
    `include_terms` is true, instead of relying on however much room happens to be left after the
    signature block. This makes "core on page 1 / terms starts fresh on page 2" hold for every item
    count (not just the ones where core spacing happens to leave too little room for terms to
    sneak onto page 1) — still a single `doc.build()` pass, still no page-count
    measurement/rebuild, still uses `KeepTogether` so terms is never split mid-list. Scoped to
    `_render_job_work_po_pdf` only — `_render_bulk_po_pdf` is unchanged (still relies purely on
    leftover space, matching the previous round's design; BULK stays 1 page with terms on/off
    either way since its single-row grid never comes close to filling a page).
  - Verified with `python -m py_compile handler.py po_pdf.py` (clean) and a temp `pypdf`
    page-count script (written, run, then deleted along with `__pycache__`) using realistic
    single-line item text (`"IMIDACLOPRID 17.8% SL - CONFIDOR - 500 ML BOTTLE"`-style particulars):
    JOB_WORK `include_terms=False` → 1 page for 1–7 items (breaks to 2 pages at 8, so ~7-item
    headroom as targeted); `include_terms=True` → exactly 2 pages for 1–7 items (core p1, terms
    p2), 3 pages at 8+ once the grid itself needs a second page. Terms & Conditions content
    (opening + closing clause text) and the "TERMS & CONDITIONS" heading were confirmed present
    together on the last page at every tested row count via per-page `pypdf` text extraction —
    never split. BULK unaffected: 1 page for both `include_terms=True` and `include_terms=False`.
    Note: with much longer (wrapping) particulars text the break point moves earlier than 7 — the
    fit numbers above assume item descriptions of realistic length (technical + brand + packaging
    on one line at 8.5pt in the ~7.85cm particulars column); pathologically long combined names
    will still wrap and consume more vertical space, same as before this change.

- [x] **Purchase Orders — third PO type GENERIC added alongside BULK and JOB_WORK (2026-07-20):**
  a free-form, NON-priced PO with a user-configurable table (arbitrary columns/rows, free text),
  configurable subject/body, and the usual supplier + bill-to + ship-to + signatory — no
  product/quantity/rate/GST/amount/total math, no line-items table. All Generic-specific content
  lives in one new JSONB column, `procurement.purchase_orders.generic_config`
  (`{subject, body, columns: [str,...], rows: [[str,...],...]}`) — **IaC migration owned by the
  `iac` agent, assumed present, not created here**.
  - `handler.py`: `_VALID_PO_TYPES` gains `'GENERIC'`. `_po_validate` restructured — a `common`
    dict (supplier/bill-to/ship-to/signatory/note/po_type/include_terms) is now shared by all
    three types; `supplier_company_id` is required for every type (moved out of the BULK/JOB_WORK-only
    block). For GENERIC it returns early with `product_technical_id/quantity/quantity_unit/rate/
    gst_rate/terms/dispatch/transport/items = None` and a new `generic_config` (validated by new
    `_po_validate_generic_config`) — no reconciliation guard, no items requirement. New helper
    coerces `columns` (non-empty array, required) and defensively pads/truncates each `rows[i]` to
    `len(columns)`, coercing every cell to a string (free text); `body` defaults to the standard
    text (`_GENERIC_DEFAULT_BODY`) when blank, `subject` defaults to `''`. BULK/JOB_WORK validation
    logic is otherwise byte-identical to before (same required fields, same reconciliation guard),
    just re-indented under the post-`common` branch; both now also return `generic_config: None`.
    `_PO_SELECT`'s `technicals` join changed **INNER → LEFT** (`t` may be NULL for GENERIC rows,
    which have no `product_technical_id`) — BULK/JOB_WORK unaffected since they always set it; added
    `po.generic_config` to the select list (psycopg2 auto-parses jsonb to a dict/`None`, no manual
    `json.loads` needed on read). `_po_create`'s INSERT and `_PO_UPDATE_SQL`/`_po_update_params` both
    gained a `generic_config` column, written via `json.dumps(...)` (or `None`) — Postgres infers the
    `jsonb` cast from the target column, same pattern used elsewhere in this codebase for text params
    against typed columns. `_po_update`'s existing `if p['po_type'] != 'JOB_WORK':` branch already
    covers GENERIC (single-statement `_write()` path, no items) — no change needed there. `_po_list`/
    `_po_get_one` needed no change: `items_by_po`/`_po_apply_job_work_totals` are only invoked for
    `po_type == 'JOB_WORK'` ids, so GENERIC rows naturally get `items: []` and keep their SQL-computed
    `amount`/`gst_amount`/`total_value` as `NULL` (quantity/rate are NULL for GENERIC → the existing
    `ROUND(po.quantity * po.rate, ...)` expressions evaluate to NULL with no code change — UI should
    render these as "—" for GENERIC rows).
  - `po_pdf.py`: new `_render_generic_po_pdf` + `render_po_pdf` dispatch branch. Reuses all shared
    scaffolding — `_header`, `_po_title_cell` ("PURCHASE ORDER" banner), `_po_box` (PO Number/Date,
    number in orange), `_vendor_box` ("VENDOR / SUPPLIER", GSTIN inline), `_addr_para` (Bill To/Ship
    To), `_section_label`, `_note_flow`, `_signature_flow` ("Thanking you," / "Yours faithfully," /
    For IAL / signatory name-title-department — reused as-is rather than re-implementing the
    slightly different capitalization sketched in the task brief, to stay byte-consistent with
    BULK/JOB_WORK's signature block), `_terms_flow`, `_build_pdf`. Body order: title+PO box → VENDOR/
    SUPPLIER box → optional `Subject: <text>` line (bold label) → `Dear Sir,` salutation + the
    configurable body text (defaults to `_GENERIC_DEFAULT_BODY`, a defensive fallback duplicate of
    `handler._GENERIC_DEFAULT_BODY` since the handler already defaults it before persisting) with the
    standard "acknowledge this order / quote `<po_no>`" sentence appended in orange (same treatment
    as BULK/JOB_WORK) → the configurable table (green header band, bordered grid, one row per
    `generic_config.rows[i]`, NO totals row) → BILL TO / SHIP TO → note band → signature block →
    Terms & Conditions (when `include_terms`). New `_generic_col_widths(columns, dw)` gives a narrow
    ~1.1cm width to a leading serial-number-looking column (`_is_serial_col`: S No./Sl./Sr No./No.),
    the widest share (40% of the remainder) to a Particulars/Description/Item/Name-looking column
    (`_is_particulars_col`) if present, else splits evenly; handles the 0/1-column edge cases without
    crashing. The table is a plain `Table` (not wrapped in `KeepTogether`) so it paginates naturally
    across pages for large row counts. Terms & Conditions uses the **JOB_WORK-style deterministic
    approach** (explicit `PageBreak()` before `_terms_flow` when `include_terms`), not BULK's
    leftover-space approach — chosen because the configurable table's row count is unbounded like
    JOB_WORK's item grid, so a forced break keeps "core page(s) / terms starts fresh" predictable
    regardless of how many rows the caller configures.
  - Verified: `python -m py_compile handler.py po_pdf.py` clean. Smoke-tested (temp script, deleted
    together with `__pycache__` afterward) via `render_po_pdf` for GENERIC at 0/1/6/60 rows ×
    `include_terms` True/False (0/1/6 rows: 2 pages terms-on, 1 page terms-off; 60 rows: 4 pages
    terms-on, 3 pages terms-off — core table itself spills before terms even starts), a no-subject
    variant, and a single-column edge case — no exceptions, non-trivial byte counts (~216–224 KB).
    `pypdf` text-extraction confirmed: default body text renders when `body=''`, the subject line and
    PO number (in the ack sentence) appear on page 1, and "TERMS"/"CONDITIONS" appear only on the
    final page (never page 1 when `include_terms=True`). Re-confirmed BULK (1 page either way) and
    JOB_WORK (1/2 items × terms on/off) still render with unchanged page-count behavior — no
    regression.
  - **Not done here (follow-ups):** `iac` agent owns the `generic_config JSONB` column migration
    (assumed present per the task) — confirm it's nullable and doesn't add a `NOT NULL` on
    `product_technical_id`/`quantity`/`quantity_unit`/`rate`/`gst_rate`/`terms`/`dispatch`/
    `transport` (this handler now writes `NULL` into all of those for GENERIC rows; if any of them
    currently has a `NOT NULL` constraint from migration 039, `_po_create`/`_po_update` will raise a
    Postgres `NotNullViolation` for GENERIC — surfaces as an unhandled 500, not a clean 400, since
    only `ForeignKeyViolation`/`UniqueViolation` are caught by `_write()`). `procurement-ui` needs a
    GENERIC create/edit form (subject/body text inputs + an editable columns/rows grid with
    add/remove column and add/remove row) and must send `po_type: 'GENERIC'` + `generic_config` in
    the create/update body; list/detail responses now always include `generic_config` (null for
    BULK/JOB_WORK) and `items: []` for GENERIC, so the UI's PO list/detail can branch on `po_type`
    to render the right summary/preview.

- [x] New alert category `monthly_collection` — unconditional scheduled-PDF report alert (2026-07-12):
  Clones the `customer_balances_fy` / `supplier_balances_fy` unconditional report-alert pattern
  exactly (NOT the conditional `sales` pattern) — fires on every scheduled run, no
  conditions/thresholds. Files: `alerts_evaluator/monthly_collection.py` (byte-identical copy
  of `api/monthly_collection.py`, `compute_monthly_collection(conn, month_str) -> dict`, no
  reportlab dependency); `alerts_evaluator/monthly_collection_pdf.py` (NEW —
  `render_monthly_collection_pdf(data) -> bytes`, portrait A4, modeled on
  `monthly_sales_pdf.py`'s 2026-07-11 layout but keyed off `ap`/`ts` bucket names and the
  `actual_collections_prev_fy` annual_position sub-key; COLLECTION wording throughout). Field
  catalog: `FIELD_CATALOG_MONTHLY_COLLECTION = {category: "monthly_collection", fields: [],
  match_types: ["all","any"], frequencies: ["daily","weekly","monthly"]}` registered in
  `FIELD_CATALOGS` in **both** `api/alerts_eval.py` and `alerts_evaluator/alerts_eval.py`
  (right after `supplier_balances_fy`) — `_VALID_CATEGORIES` / `_VALID_FIELDS_BY_CATEGORY`
  derive from `FIELD_CATALOGS` automatically, so no other change was needed there; empty
  conditions pass `validate_alert` for this category (only `balances` requires ≥1 condition).
  `alerts_evaluator/handler.py`: added `import monthly_collection` / `import
  monthly_collection_pdf`, and a new `elif category == "monthly_collection":` branch
  (alongside the `customer_balances_fy` / `supplier_balances_fy` elifs, before the aggregate
  `else`) that computes the **current** month (`today.strftime('%Y-%m')` — unlike the FY
  branches, which always request `fy_count='all'`), renders the PDF, and sends via
  `_send_ses_email_with_pdf` with `status="sent"`, `matched_count=1`. Verified: `py_compile`
  clean on all four touched/created files; `reportlab` installed locally and
  `render_monthly_collection_pdf()` smoke-tested against a representative `data` dict
  (30-day `days[]`, populated `annual_position`/`month_only`/`cumulative_as_on` blocks) —
  returned non-empty PDF bytes (~217 KB) without error. **No IaC/DB change** — the reportlab
  layer, fonts, and logo already ship with `alerts_evaluator`; the new `.py` files are
  auto-included in the `archive_file` deployment zip. UI/IaC follow-up (not done here,
  out of business-core scope): the AlertBuilder category dropdown and any alert-category
  labels in `iravi-ui` may need a `monthly_collection` entry to let admins create this alert
  type from the UI — flag to the `ui` agent if/when this alert type should be exposed there.

- [x] Monthly Collection restricted to the two operating regions AP + TG (2026-07-12):
  IRAVI operates only in Andhra Pradesh and Telangana, so `monthly_collection.py` was
  reduced from FOUR states (AP/TS/TN/OR) to TWO (AP, TG→ts) — now mirroring
  `monthly_sales.py`'s 2-region shape exactly. `_STATE_TO_BUCKET` = `{'AP':'ap','TG':'ts'}`,
  `_TARGET_STATE_TO_BUCKET` = `{'AP':'ap','TS':'ts'}`; `_pack4`→`_pack` (`{ap,ts,total}`);
  `_collections_by_state` returns `{ap,ts}`; targets queries filter `state IN ('AP','TS')`.
  Collections for non-AP/TG customers (e.g. TN/OR), NULL, or unrecognized state now fold
  into `unmapped_collections_total` (previously TN/OR had their own buckets). Every payload
  block (`days`, `grand_total`, `projections`, `excess_short`, `annual_position.*`,
  `month_only.*`, `cumulative_as_on.*`, `upto_prev_month.growth_pct`) is now `{ap,ts,total}`.
  `handler.py`: Redis cache key bumped `iravi:reports:monthly_collection:v1:{month}` →
  `:v2:` (payload shape changed, so stale v1 entries never collide);
  `_handle_config_monthly_collection_targets_post` state validation tightened from
  `('AP','TS','TN','OR')` → `('AP','TS')`. Verified: `py_compile` clean on both files;
  `compute_monthly_collection` exercised against a mocked psycopg2 conn — AP/TG bucket into
  ap/ts, a TN row folds into `unmapped_collections_total`, all blocks are `{ap,ts,total}`,
  `targets_available=False` degrades gracefully when the table is absent.
  **UI (iravi-ui):** `MonthlyCollection.tsx` + `CollectionProjections.tsx` trimmed to AP/TS,
  `client.ts` `CollectionStateTotals`/`MonthlyCollectionDay`/growth_pct types drop `tn`/`or`
  (tsc clean). **No IaC/DB change** — `monthly_collection_targets` keeps its schema; existing
  TN/OR target rows are simply never read/written. **Post-deploy:** `POST /admin/cache/flush`
  to purge any cached v1 collection payloads (v2 key means this is belt-and-suspenders).

- [x] Retire-absent (full-snapshot) semantics + empty-file guard for `etl_supplier_accounts`
  and `etl_customer_accounts`, plus active-only filters downstream (2026-07-12):
  - `lambda/etl_supplier_accounts/handler.py` — `_upsert()` keeps the existing per-row
    close-then-insert loop, then adds a retire step after the loop (same transaction):
    `UPDATE supplier_accounts SET out_z = NOW() WHERE out_z IS NULL AND NOT (name = ANY(%s))`
    with the list of names parsed from the current file. Empty-file guard: if `rows` is empty,
    the upsert loop AND retire step are both skipped (warning logged, function returns) —
    a corrupt/empty export can never wipe the table. `conn.commit()` moved inside `_upsert`,
    called once at the end only when rows were processed (`_process()` no longer commits).
  - `lambda/etl_customer_accounts/handler.py` — `_upsert()` converted from
    `INSERT ... ON CONFLICT (customer_name) DO UPDATE SET ...` (simple dimension upsert) to
    uni-temporal close-then-insert milestoning, mirroring supplier_accounts: `UPDATE
    customer_details SET out_z = NOW() WHERE customer_name = %s AND out_z IS NULL` then
    `INSERT INTO customer_details (customer_name, district, city, state, pin, mobile_no,
    customer_code) VALUES (...)` — `id`/`in_z`/`out_z` left to column defaults. Same
    retire-absent step and empty-file guard as supplier_accounts, keyed on `customer_name`.
    All parsing (`_build_code_lookup`, `_build_delivery_lookup`, `_parse`), the filename
    guard, and the IRAVI/blank filtering are unchanged.
  - `lambda/api/handler.py` — `/customers/names` and `_handle_customer_details` (backing
    `/customers/details`) both add `WHERE out_z IS NULL` so retired customers stop appearing
    in UI pickers/tickers. Other `party IN (SELECT ... FROM customer_details)` subqueries
    (monthly_sales, reports, etc.) intentionally left unfiltered — historical sales/state
    totals must still resolve retired customers' names via their closed rows.
  - `lambda/api/supplier_balances_fy.py` — city lookup `SELECT UPPER(name), city FROM
    supplier_accounts` gets `WHERE out_z IS NULL` so retired suppliers' cities are never used.
  - Verified: `python -m py_compile` clean on all four files; both `_upsert` guards confirmed
    to skip DB writes entirely when `rows` is empty; `ON CONFLICT` fully removed from
    `etl_customer_accounts/handler.py`.
  - **IaC needed:** migration adding `in_z TIMESTAMPTZ NOT NULL DEFAULT NOW()`,
    `out_z TIMESTAMPTZ`, `id BIGSERIAL PK`, and partial unique index
    `uix_customer_details_active ON customer_details (customer_name) WHERE out_z IS NULL`
    to `customer_details` — must be applied before deploying this handler version (handled
    separately by the IaC agent). `supplier_accounts` already has these columns/index
    (migration 016) — no further IaC change needed there.
  - **Re-ingest required once the migration lands:** re-upload `Customer Accounts Export
    File*.xlsx` and `Supplier Accounts Export File*.xlsx` to S3 `raw/` so the milestoning
    upsert + retire-absent logic actually runs against the new schema; then
    `POST /admin/cache/flush` to clear any stale `iravi:customers:*` /
    `iravi:reports:supplier_balances_fy:*` Redis entries.

- [x] `compute_monthly_sales` extended with targets/YoY comparison data + `monthly_sales_pdf.py`
  rebranded (2026-07-11): both byte-identical copies (`lambda/api/monthly_sales.py`,
  `lambda/alerts_evaluator/monthly_sales.py`) now additionally return `projections`,
  `excess_short`, `targets_available`, `annual_position`, `month_only`, `cumulative_as_on`
  (all existing keys unchanged) — reads `monthly_sale_targets` (see the `/config/monthly-targets`
  entry below), degrades gracefully (`targets_available=False`, all-zero projections) if that
  table doesn't exist yet via `to_regclass`. New shared helper `_net_sales_by_state(cur,
  start_date, end_date, unmapped_branches)`. Full field docs in "api — GET /reports/monthly-sales
  → Targets / year-over-year extension". `lambda/alerts_evaluator/monthly_sales_pdf.py`
  rewritten to the new DAILY NET SALES / ANNUAL POSITION & CUMULATIVE SALES / MONTH ONLY +
  CUMULATIVE side-by-side layout (see file-tree note above). `lambda/api/handler.py` Redis
  cache key bumped `iravi:reports:monthly_sales:{month}` → `iravi:reports:monthly_sales:v2:{month}`
  (read + write) so the new payload shape never collides with a stale cached old-shape entry.
  Verified: `python -m py_compile` clean on all four files; the two `monthly_sales.py` copies
  confirmed byte-identical via `filecmp.cmp`; `compute_monthly_sales` exercised against a mocked
  psycopg2 connection/cursor (no real DB needed) with no exceptions; `render_monthly_sales_pdf`
  rendered against that mocked payload and the output page visually inspected (reportlab +
  PyMuPDF installed to the session scratchpad only, not added to any `requirements.txt`).
  **UI needed:** no UI change requested this task — `MonthlySales.tsx` still consumes the
  original key subset; new keys are additive and optional for the UI to adopt later.

- [x] api Lambda — `GET|POST /config/monthly-targets` endpoints (2026-07-11, admin-only): new
  `monthly_sale_targets` table (unitemporal milestoning; natural key `(state, month, yr)`) —
  `id BIGSERIAL PK, state VARCHAR(10), month SMALLINT, yr SMALLINT, target_lakhs NUMERIC(14,2),
  in_z TIMESTAMPTZ NOT NULL DEFAULT NOW(), out_z TIMESTAMPTZ`. `_route_config(event, method, path)`
  dispatches `/config/monthly-targets`; wired into `lambda_handler` via a new `path.startswith('/config/')`
  prefix block (mirrors the `/alerts` block, placed before the `if method != 'GET'` guard so it can
  serve both verbs). `_handle_config_monthly_targets_get` — `?yr=YYYY`; `years` = distinct active `yr`
  values DESC; `yr` = query param, else most recent year, else `datetime.now().year`; `rows` = active
  rows for that `yr` ordered by `state, month`, `target_lakhs` cast to float.
  `_handle_config_monthly_targets_post` — validates `state in ('AP','TG')`, `month` int 1-12, `yr` int
  2000-2100, `target_lakhs` numeric ≥ 0 (400 on bad input); milestoning upsert
  (`UPDATE ... SET out_z=NOW() WHERE state/month/yr AND out_z IS NULL` then `INSERT`) in one
  transaction. Both handlers call `_require_admin(event, cur)` first inside the cursor block, same
  pattern as the other `/admin/*` and `/alerts*` handlers. Uncached (no Redis), matching the alerts
  handlers. `python -m py_compile handler.py` clean.
  **IaC needed:** DB migration to create `monthly_sale_targets` (schema above, partial unique index
  on `(state, month, yr) WHERE out_z IS NULL`); API Gateway routes `GET /config/monthly-targets` +
  `POST /config/monthly-targets` + CORS in `lambda_api.tf`; optional `app_screens` seed row if this
  becomes a gated UI screen. **UI needed:** client method(s) + admin config page once IaC routes exist.

- [x] Dr/Cr balance coloring added to alert-email PDF renderers (2026-07-01):
  - **`customer_balances_fy_pdf.py`** — Customer semantics: Dr (receivable) → RED `#cc0000`,
    Cr (credit/advance) → GREEN `#1a6e35`. Colored columns: per-FY Balance (₹), Balance Dr,
    Balance Cr in every data row and the TOTAL row. Debit / Credit / Credit Notes columns and
    all text columns (S.No / Party / Code / City) unchanged.
  - **`supplier_balances_fy_pdf.py`** — Supplier semantics SWAPPED (matching SupplierBalancesFY.tsx):
    Dr (payable, normal) → GREEN `#1a6e35`, Cr (advance/overpayment) → RED `#cc0000`.
    Same colored columns: per-FY Balance (₹), Balance Dr, Balance Cr in data + TOTAL rows.
    No Code column; no Credit Notes column (not present in supplier data).
  - **Implementation:** color-specific ParagraphStyle instances (`dat_r_red`, `dat_r_green`,
    `tot_r_red`, `tot_r_green` in customer; `dat_r_green`, `dat_r_red`, `tot_r_green`,
    `tot_r_red` in supplier) for visual rendering. Corresponding per-cell
    `('TEXTCOLOR', (col,row), (col,row), <color>)` TableStyle commands also appended to
    `tbl_cmds` (note: redundant for Paragraph cells in ReportLab but present per spec for
    smoke-test verification; `color_cmds` list is confirmed non-empty for any data with
    Dr or Cr balances).
  - **Balance column index formula:** customer: `bal_col = 4 + fy_idx*4 + 3`; supplier:
    `bal_col = 3 + fy_idx*3 + 2`. Balance Dr = `n_cols-2`; Balance Cr = `n_cols-1`.
  - **Smoke test results:** customer 257239 bytes / 9 TEXTCOLOR commands; supplier 257057
    bytes / 9 TEXTCOLOR commands. Both py_compile clean. No IaC/UI change.

- [x] Customer Balances (FY) alert — daily PDF email (2026-07-06, Slice 1 of 2):
  - **Shared font infra:** `lambda/alerts_evaluator/pdf_fonts.py` — `register_fonts()` registers
    DejaVuSans + DejaVuSans-Bold (bundled TTFs from matplotlib; 738 KB + 688 KB) fixing the
    ₹ (U+20B9) / — (U+2014) `KeyError` that crashes Helvetica-based `doc.build()` on Lambda.
    Idempotent; try/except falls back to Helvetica with warning. Reused by upcoming Supplier slice.
  - **Shared compute:** `lambda/api/customer_balances_fy.py` (and byte-identical copy in
    `lambda/alerts_evaluator/`) — `compute_customer_balances_fy(conn, fy_count) -> dict` extracts
    all SQL/aggregation from `_handle_customer_balances_fy`; per-voucher netting, credit-note split,
    opening balances, party sort by code — all preserved. `GET /reports/customer-balances-fy` JSON
    is unchanged (cache-aside + `_response` wrapper remain in handler.py as thin delegate).
  - **PDF renderer:** `lambda/alerts_evaluator/customer_balances_fy_pdf.py` —
    `render_customer_balances_fy_pdf(data) -> bytes`; landscape A4, 1cm margins, DejaVuSans,
    two-row header (`repeatRows=2`), 4 sub-cols per FY (Debit/Credit/Credit Notes/Balance),
    always includes credit notes (from-beginning), Indian-grouped rupee format (₹12,34,567.00),
    Dr/Cr balance suffixes, — for zero, #1a3c2b header / #f0f0f0 TOTAL / #fafafa zebra,
    Kukatpally footer every page. Smoke test: 256718 bytes; DejaVuSans font embedded in PDF.
    **Dr/Cr balance coloring added 2026-07-01:** `_RED=#cc0000`, `_GREEN=#1a6e35`. Customer
    semantics: Dr (receivable) → RED, Cr (credit/advance) → GREEN. Colored columns: per-FY
    Balance (₹), Balance Dr, Balance Cr (data rows + TOTAL row). Debit/Credit/Credit Notes
    and text columns unchanged. Implemented via color-specific ParagraphStyle instances
    (`dat_r_red/dat_r_green/tot_r_red/tot_r_green`) for visual rendering, plus corresponding
    per-cell `('TEXTCOLOR', (col,row), (col,row), <color>)` commands appended to tbl_cmds.
    Smoke test 257239 bytes; 9 TEXTCOLOR commands verified; py_compile clean.
  - **Alert category:** `customer_balances_fy` added to `FIELD_CATALOG_CUSTOMER_BALANCES_FY` and
    `FIELD_CATALOGS` in `alerts_eval.py` (both copies); `fields=[]`, not branch-scoped, accepts
    `conditions: []`; `validate_alert` already handles zero-condition non-balances categories.
  - **Evaluator branch:** `category == 'customer_balances_fy'` in `alerts_evaluator/handler.py`
    always fires; computes `fy_count='all'`, builds PDF, sends via `_send_ses_email_with_pdf`;
    subject `"IRAVI — Customer Balances (FY) — <DD Mon YYYY>"`; body `"Attached is the Customer
    Balances (FY) report."`; filename `IAL_Customer_Balances_FY_<DD-Mon-YYYY>.pdf`;
    `alert_runs.matched=1, status='sent'`; keeps existing per-alert try/except gating.
    Does NOT touch balances/sales/sale_returns branches.
  - **py_compile clean:** all 8 new/changed files.
  - **Byte-identical:** `api/customer_balances_fy.py` == `alerts_evaluator/customer_balances_fy.py`;
    `api/alerts_eval.py` == `alerts_evaluator/alerts_eval.py`.
  - **No IaC change needed:** `customer_balances_fy` is a free-text category; reportlab layer and
    `/alerts` routes already exist. DejaVuSans TTFs ship in the `archive_file` zip without
    any IaC change (same pattern as `ial-logo.png`).
  - Supplier Balances (FY) alert is a separate upcoming slice; it will reuse `pdf_fonts.py`.

- [x] monthly_sales_pdf.py — ASCII-safety fix (2026-07-06): replaced `₹` (U+20B9) in `_FOOTER_LINE2` with `Rs.` (root cause of `sales` alert failures recorded as `status='failed'`; `canvas._escape` raises `KeyError: 8377` for any char outside Latin-1 when invoked without prior `unicode2T1` pre-conversion). Also replaced the em-dash `—` (U+2014) in the `SimpleDocTemplate title` with an ASCII hyphen `-` for the same safety reason. Footer text now reads "Values are in Lakhs (1 Lakh = Rs. 1,00,000)". `py_compile` clean. No IaC or UI change required.

- [x] alerts_evaluator — sales-alert PDF email path (2026-07-05): `category=='sales'` fires `_send_ses_email_with_pdf` (SES SendRawEmail) with a minimal "Attached is the Daily Net Sales Report" HTML body (no Conditions/Window-Metrics tables) and `IAL_Daily_Net_Sales_<DD-Mon-YYYY>.pdf` attachment built from `monthly_sales.compute_monthly_sales + monthly_sales_pdf.render_monthly_sales_pdf`; `sale_returns` path unchanged (metrics email, no attachment); `_send_ses_email_with_pdf` uses stdlib MIME only; reportlab==4.2.2 added to alerts_evaluator/requirements.txt; monthly_sales.py + monthly_sales_pdf.py imports wired at top of evaluator handler.
- [x] api Lambda — `_handle_monthly_sales` refactored (2026-07-05): now delegates to `monthly_sales.compute_monthly_sales(conn, month_str)` (cache-aside + `_response` wrapper unchanged); inline SQL removed; endpoint JSON shape unchanged; API and PDF share one implementation.
- [x] api Lambda — `GET /reports/monthly-sales` endpoint (2026-07-05): state-wise net customer sales for one calendar month; ?month=YYYY-MM (default current IST month); branch→state mapping (Guntur C & F=andhra, Auto Nagar=telangana; others excluded + logged); all calendar days returned with 0.0 fill; as_on_date=min(today IST, last day of month); FY label (Apr→Mar); analysis block includes up_to_prev_month (FY-start→prev-month-end, empty for April), prev_month_label (%b abbreviated), as_on_date copy of grand_total; unmapped_branches collected; raw rupees 2dp; Redis key `iravi:reports:monthly_sales:{month}` TTL _LEDGER_TTL; cleared by POST /admin/cache/flush. No new DB table/column needed. IaC: needs API Gateway route GET /reports/monthly-sales + app_screens seed for reports.monthly_sales; UI: needs client method + page + screen key.
- [x] alerts `current_month` window + fields (2026-07-05): added `current_month` to `_WINDOW_SUFFIXES` and `compute_window_dates` (start=first day of current month, end=yesterday; empty range if run_date is 1st); added `net_sales_current_month` to FIELD_CATALOG_SALES and `sale_returns_current_month` to FIELD_CATALOG_SALE_RETURNS; window resolution via suffix-endswith (same mechanism as all other windows); `validate_alert` auto-accepts new keys via catalog-driven _VALID_FIELDS_BY_CATEGORY; `_WINDOW_TO_FIELD` auto-includes via _WINDOW_SUFFIXES comprehension; both alerts_eval.py copies synced (byte-identical). No IaC/DB change needed.

- [x] etl_supplier_ledger Lambda (2026-06-27, revised 2026-06-27 x2) — EventBridge-triggered (Object Created), read-only on S3; reads same `Ledger All Accounts*.xlsx` as etl_customer_ledger; identifies supplier rows by col[10] (Account Group) == 'All Supplier Accounts' (case-insensitive); explicit iravi exclusion (`'iravi' in account_name.lower()`); does NOT read supplier_accounts at all; account_name from col[4] (Account field); contra_account from col[5] drives sub_category; combined `_CONTRA_SUBCATEGORY` map covers BOTH purchase-side (Input GST/Default Purchase/Purchase Return/Roundoff → Cr rows) AND sales-side (Output GST/Default Sales/Sales Return → Db rows), eliminating the dropped-principal and natural-key-collision bugs for MERCO-style Sales Invoice rows; `_TXN_SUBCATEGORY` fallback unchanged; close-then-insert milestoning into supplier_ledger; fallback to processed/raw/ if raw key already archived by etl_customer_ledger; no S3 writes, no EventBridge emit; requires IaC migration 017 + lambda_etl_supplier_ledger.tf; one-time cleanup SQL: `UPDATE supplier_ledger SET out_z = NOW() WHERE out_z IS NULL AND sub_category IN ('Sales Invoice','Sales Invoice Returns');`
- [x] etl_supplier_accounts Lambda (2026-06-27) — full handler: parse `Supplier Accounts Export File*.xlsx` (General sheet, header row 1, data from row 2); columns [0]=Name [6]=GST [7]=GSTValid [12]=City [13]=State; IRAVI own-company rows filtered; gst_valid int→bool with None/0 distinction; city title-cased; state prefix-stripped ("36-Telangana" → "Telangana"); uni-temporal milestoning upsert (close-then-insert on name); archives source to processed/raw/; no EventBridge emit; requires IaC migration 016 + lambda_etl_supplier_accounts.tf
- [x] alerts aggregate categories `sales` + `sale_returns` (2026-06-26) — new FIELD_CATALOG_SALES and FIELD_CATALOG_SALE_RETURNS in alerts_eval.py; compute_window_dates() for 5 time windows (prev_day/prev_week/last_month/prev_quarter/fy) with fiscal-quarter + April-FY-boundary handling; _query_aggregate_metrics() builds a single SQL with FILTER clauses per window (sales=net, sale_returns=returns-only); evaluate_aggregate() returns {category,matched,metrics,conditions}; both alerts_eval.py copies updated in sync; validate_alert() now accepts all 3 categories with per-category field validation; IaC migration 015 required for alerts.branch VARCHAR(100)
- [x] alerts.branch column support (2026-06-26) — _ALERT_SELECT, _alert_row_to_dict, _handle_alerts_create INSERT, _handle_alerts_update UPDATE all include branch; _load_active_alerts in evaluator includes branch; branch accepted/returned in all CRUD endpoints; NULL/'ALL'/'' = no branch filter in metric query
- [x] GET /alerts/fields category dispatch (2026-06-26) — reads ?category= query param; serves correct catalog from FIELD_CATALOGS dict; returns 400 for unknown category; defaults to balances if param absent
- [x] POST /alerts/{id}/test category dispatch (2026-06-26) — balances → existing {matched,sample} shape (unchanged); sales/sale_returns → aggregate shape {category,matched,metrics,conditions}
- [x] alerts_evaluator category dispatch (2026-06-26) — balances path unchanged; sales/sale_returns path calls evaluate_aggregate(); fires _render_metrics_email() + _send_ses_email() on match; subject "[IRAVI Alert] Sales|Sale Returns — <date>"; alert_runs.matched=1/0 for aggregate; _send_ses_email signature changed to (subject, recipients, html_body)
- [x] alerts API endpoints (GET/POST/PUT/DELETE /alerts, GET /alerts/{id}, GET /alerts/fields, POST /alerts/{id}/test) in lambda/api/handler.py — all admin-only via _require_admin()
- [x] alerts response contract fix (2026-06-24) — `recipients` in all alert responses (`GET /alerts`, `GET /alerts/{id}`, `POST /alerts`, `PUT /alerts/{id}`) is now a flat array of email-address strings `["a@x.com"]` instead of objects `[{id, channel, address}]`; both serialization sites fixed (`_fetch_alert_with_children` and `_handle_alerts_list`); `_insert_alert_children` request handling unchanged
- [x] alerts_eval.py shared module (lambda/api/alerts_eval.py + copy in lambda/alerts_evaluator/alerts_eval.py) — FIFO aging, condition matching, field catalog, validate_alert(), is_alert_due_today()
- [x] alerts_evaluator Lambda (lambda/alerts_evaluator/handler.py + requirements.txt) — EventBridge-triggered nightly evaluator: load due alerts → evaluate balances → send SES HTML email → write alert_runs; one alert failing does not abort others
- [x] alerts_evaluator monthly_sales_pdf.py restyled (2026-07-05) — renderer now matches iravi-ui Reports PDF: dark-green #1a3c2b headers with white text; #f0f0f0 GRAND TOTAL/Total rows; #fafafa zebra; DD-Mon-YYYY dates (datetime.strptime('%Y-%m-%d').strftime('%d-%b-%Y')); IAL logo bundled as ial-logo.png (ial-logo.png copied from iravi-ui/public, ships in archive_file zip, loads via os.path.dirname(__file__) with try/except fallback — never crashes); IRAVI AGRO LIFE LLP centered bold large; date + (Value In Lakhs) top-right; SALES ANALYSIS and {MONTH} MONTH ONLY centered bold underlined section headings; Kukatpally two-line footer (Reg. Address: Flat No: 102, BVR Plaza… Shanthi Nagar, Kukatpally, Hyderabad, Telangana 500072 + computer-generated note); compact 7pt font + 1cm margins + 2pt cell padding → single A4 portrait page; analysis and month-only tables narrower (70%/45% width) and centered with hAlign='CENTER'; old blue #1a5276 header and Guntur footer removed
- [x] alerts days_since_last_receipt field (2026-06-24) — new evaluable field in both alerts_eval.py copies; NULL last_receipt_date → sentinel 10**9 (flags never-paid customers); field catalog updated; validate_alert() accepts it as valid condition field; _customer_matches() extended; evaluate_balances() returns days_since_last_receipt in matched rows; email table adds Days Since Receipt column (sentinel displayed as "Never")
- [x] alerts schedule_time field (2026-06-24) — `schedule_time TIME` column added to `alerts` table by IaC migration 014; API accepts/returns as HH:MM string (e.g. "14:30"); defaults to "11:00" if omitted on create; validated by _validate_schedule_time() regex; _alert_row_to_dict() normalises psycopg2 timedelta/time → HH:MM; CREATE and UPDATE SQL updated; _ALERT_SELECT updated
- [x] alerts_evaluator 15-minute gating logic (2026-06-24) — evaluator now runs every 15 min (IaC changes cron to rate(15 minutes)); per-alert three-gate check: (1) due today, (2) current IST HH:MM >= schedule_time, (3) no alert_runs row with status sent|no_match for today (IST); failed runs may retry; _already_sent_today() uses AT TIME ZONE SQL to convert run_at UTC→IST date; _load_active_alerts() reads schedule_time column
- [x] alerts_evaluator per-day failed-retry cap (2026-06-25) — module constant _MAX_FAILED_ATTEMPTS_PER_DAY=5; _already_sent_today() replaced by _check_today_runs() which returns (done: bool, failed_count: int) in one query using COUNT(*) FILTER; gate-2+3 block now also skips if failed_today >= 5 and logs INFO "alert <id>: reached 5 failed attempts today, skipping until tomorrow"; success dedupe, due-today logic, time-reached gate, SES send, and alert_runs writing are all unchanged
- [x] Project structure created
- [x] etl_stocks core logic (`process.py`, `run_local.py`)
- [x] etl_stocks Lambda handler — S3 trigger, rates lookup, processed upload, source archive, DB upsert (`snapshot_stock`), `ETLStocksSuccess` event
- [x] etl_stocks `available_qty` fix — stored in DB as kg/L (÷1000 on INSERT only)
- [x] etl_stocks milestoning fix — `entry_date` removed from UPDATE predicate; only business key used
- [x] etl_stocks snapshot-replace fix — `_upsert_snapshot_stock` now closes ALL active `snapshot_stock` rows (`WHERE out_z IS NULL`) before inserting the new snapshot, so products/packings absent from the new file are correctly superseded instead of staying active forever
- [x] etl_customer_ledger Lambda — full handler: parse `Ledger All Accounts*.xlsx`, category/sub-category mapping, unitemporal upsert into `customer_ledger`, archive source, emit `ETLCustomerLedgerSuccess`
- [x] etl_customer_accounts Lambda — full handler: parse `Customer Accounts Export File*.xlsx`, normalise case + state codes, upsert into `customer_details`
- [x] etl_customer_accounts mobile_no normalization — strip spaces, take last 10 digits if > 10
- [x] etl_customer_accounts customer_code — reads `General` sheet (col[2]=Code), builds uppercase-name→code lookup, includes `customer_code` in INSERT and ON CONFLICT UPDATE; requires IaC migration 011
- [x] etl_customer_accounts General-as-master-list fix (2026-06-27) — `_parse` now builds two lookups: `_build_code_lookup(wb)` from General (unchanged) and new `_build_delivery_lookup(wb)` from Delivery Address; row set is the UNION of both name sets so every General-sheet customer is inserted WITH its code even when absent from Delivery Address; General-only customers get address fields NULL; Delivery-only customers get customer_code NULL; no previously-included customer is lost; fixes missing party codes in Customer Balances FY report; re-ingest of `Customer Accounts Export File*.xlsx` + `POST /admin/cache/flush` required after deploy
- [x] etl_customer_ledger Account Group filter (2026-06-27) — customer rows now identified by col[10] (`account_group == 'All Customer Accounts'`, case-insensitive) instead of joining to `customer_details`; explicit `'iravi' in account_name.lower()` exclusion; `_load_known_customers` and `known_customers` parameter removed; no DB read at parse time; customers without a `customer_details` record are now included in `customer_ledger` and appear in Customer Balances FY with null code/city; 268 rows kept from sample file (36 parties); re-ingest required for change to take effect
- [x] etl_customer_ledger sign-normalization fix — negative debit/credit values (e.g. FUSIL Roundoff adjustments) are normalized to the opposite side before category/amount classification; fixes 0.48 reconciliation error on EKR INDUSTRIES POSRT2526-7 and any similar rows
- [x] etl_customer_ledger Sales Invoice Returns category-by-column fix — Sales Invoice Returns branch now classifies `category = 'Cr' if credit > 0 else 'Db'` instead of hardcoding `'Cr'`; fixes roundoff rows that land on the debit side after sign-normalization (e.g. SRI VENKATESWARA COFFEE AND GENERAL STORES excess Cr 0.20, EKR INDUSTRIES excess Cr 0.48); re-ingest with close-then-reload required for affected months
- [x] etl_appendix_b_x11 Lambda — full handler: parse `Barcodes Masters*.xlsx`, normalize barcodes + dates, unitemporal upsert into `appendix_b_x11_stock`
- [x] DB migration `005_create_appendix_b_x11_stock.sql` — `appendix_b_x11_stock` table with (barcode, technical_name, vendor) milestoning
- [x] etl_appendix_b_x11_purchase Lambda — full handler: parse `AppendixPurchaseReport*.xlsx`, skip multi-barcode rows, look up mdf_date/exp_date from `appendix_b_x11_stock`, upsert into `appendix_b_x11_stock_ledger` (in_out=In); also upserts every parsed row into `purchases` (purchase_return=N)
- [x] etl_appendix_b_x11_purchase_return Lambda — same as purchase but `AppendixPurReturn*.xlsx`, different column layout ([3]=Party, [4]=Ref BillNo, [5]=Ref BillDate, [6]=Product, [7]=Qty, [8]=Rate, [9]=Gross, [14]=AV, [22]=Barcodes, [23]=Narration), in_out=Out; also upserts every parsed row into `purchases` (purchase_return=Y)
- [x] DB migration `007_create_purchases.sql` — `purchases` table with (purchase_date, voucher_no, branch, party, product) milestoning; populated by both etl_appendix_b_x11_purchase and etl_appendix_b_x11_purchase_return
- [x] etl_appendix_b_x11_sale Lambda — parse `AppendixSale*.xlsx`, column layout [0]=Date, [1]=VoucherNo, [2]=Branch, [3]=Party, [4]=RefBillNo, [5]=RefBillDate, [6]=Product, [7]=Qty, [8]=Rate, [9]=Gross, [14]=AV, [22]=Barcodes; in_out=Out; upserts into appendix_b_x11_stock_ledger (1-barcode rows only); also upserts every parsed row into `sales` (sales_return=N)
- [x] etl_appendix_b_x11_sale_return Lambda — same layout, `AppendixRetSales*.xlsx`, in_out=In; also upserts every parsed row into `sales` (sales_return=Y)
- [x] DB migration `006_create_appendix_b_x11_stock_ledger.sql` — `appendix_b_x11_stock_ledger` table with (purchase_date, iravi_voucher, technical_name, barcode) milestoning
- [x] DB migration `008_create_sales.sql` — `sales` table with (purchase_date, voucher_no, branch, party, product) milestoning; populated by both etl_appendix_b_x11_sale and etl_appendix_b_x11_sale_return
- [x] whatsapp_notifier Lambda — phase 1: S3 trigger on `notifications/pending/`, moves file to `notifications/processed/`; phase 2 stub for WhatsApp API call
- [x] api Lambda — `POST /notify` endpoint: receives `{customer_name, html_content}`, puts HTML to `notifications/pending/` with customer_name metadata, returns `{key, message}`
- [x] api Lambda — `GET /reports/customer-balances-fy` endpoint: per-customer, multi-FY roll-forward from `customer_ledger`; fy_count=all|2|3|4; cache key `iravi:reports:customer_balances_fy:{fy_count}` (1h TTL); each row now includes `code` (party code from `customer_details.customer_code`); rows sorted by code asc (NULLs last), then party name
- [x] api Lambda — `GET /reports/customer-balances-fy` per-FY credit split: `credit` now EXCLUDES `sub_category='Customer Credit Notes'`; new `credit_notes` field carries those rows separately; balance/opening/balance_dr/balance_cr numerically unchanged; `_CREDIT_NOTE_SUBCATEGORY` module constant controls the match string
- [x] api Lambda — `GET /reports/customer-balances-fy` per-voucher netting: aggregation now groups by (party, voucher_no, fy_label) and nets Db/Cr per voucher before bucketing; roundoff/GST sub-components absorbed, no phantom paise credits; balance/opening/balance_dr/balance_cr numerically unchanged; cache flush required after deploy (no re-ingest)
- [x] api Lambda — `GET /ledger/statement` per-voucher netting: each voucher row's debit/credit is now netted (net = raw_debit − raw_credit; show on one side only); total_debit/total_credit follow the netted values; closing_balance unchanged; cache flush required after deploy (no re-ingest)
- [x] lambda_api.tf — `DATA_BUCKET` env var, `s3:PutObject` IAM on `notifications/*`, CORS `POST`, `POST /notify` API Gateway route
- [x] lambda_whatsapp_notifier.tf — Lambda + IAM + S3 permission; S3 trigger in lambda_etl_sales.tf on `notifications/pending/*.html`
- [x] UI CustomerBalances — "Notify Client" split into "Preview" (opens HTML window) + "Notify" (POST to API, per-row sending/sent/error state); both mobile and desktop views updated
- [x] Terraform + S3 trigger + GitHub Actions layer build for `etl_appendix_b_x11_purchase`
- [x] etl_sales scaffold (`handler.py`, `requirements.txt`) — parse/upsert logic TODO
- [x] redis_updater — `ETLStocksSuccess` handler: writes `iravi:stocks:summary` + `iravi:stocks:current`
- [x] redis_updater — `ETLCustomerLedgerSuccess` handler: writes `iravi:ledger:range`
- [x] api — `GET /stocks/summary` + `GET /stocks/current` with cache-aside; `GET /sales` stub
- [x] Terraform resources: `lambda_etl_stocks.tf`, `lambda_etl_sales.tf`, `lambda_etl_customer_ledger.tf`, `lambda_etl_customer_accounts.tf`, `lambda_redis_updater.tf`, `lambda_api.tf`
- [x] GitHub Actions layer build steps for all Lambdas (plan + apply jobs)
- [x] Stock Expiry tracking (2026-08-01) — `etl_stocks/process.py` parses the new 44th `ExpiryDate` CSV column (`_parse_date`, tolerant of blanks), row-merge key now includes `expiry_date` so `snapshot_stock` is one row per distinct expiry date; `handler.py` INSERTs `expiry_date`; `GET /stocks/current` + `GET /stocks/summary` + `redis_updater._update_stocks_cache()` GROUP BY back to the pre-expiry grain (unchanged shape/totals); new `GET /stocks/expiry` (`iravi:stocks:expiry`, 24h TTL) + `GET /stocks/expiry/pdf` (`lambda/api/stocks_expiry_pdf.py`, brand + expires_before_months filters) + redis_updater now also writes `iravi:stocks:expiry`. Tests: `lambda/etl_stocks/tests/test_process_csv.py`, 26/26 assertions pass.

## What Is Next (build in this order)

> **Reconciliation note (2026-07-11): all of it is LIVE on AWS.** Every code deliverable below is
> deployed — migrations `010`–`019` are applied to RDS; the supplier/alerts/SES Terraform is applied;
> `lambda_api.tf` serves the `/reports/*`, `/alerts*`, and `POST /admin/cache/flush` routes; and the UI
> pages (`MonthlySales.tsx`, `CustomerBalancesFY.tsx`, `SupplierBalancesFY.tsx`, `Alerts/*`) +
> `api.reports`/`api.alerts` clients are shipped on Amplify. The one-time data ops noted below
> (re-ingest, cleanup SQL, `POST /admin/cache/flush`) have been run as part of that rollout. The only
> genuinely-remaining build backlog is **etl_sales** (handler still a stub) + its **`_update_sales_cache()`**,
> the future **Cognito** authoriser, and **whatsapp_notifier phase 2** (pending WhatsApp Business approval).
> The per-item boxes below are retained as a historical record of what was built and how it was deployed.

- [ ] **RE-INGEST `Ledger All Accounts*.xlsx` after the CSV-content conversion (2026-07-22)** —
  the file must be re-uploaded to S3 `raw/` as real CSV text (the old openpyxl reader would
  fail/misread CSV bytes, and vice versa) so both `etl_customer_ledger` (S3-triggered) and
  `etl_supplier_ledger` (EventBridge-triggered, reads the same file) can parse it with their new
  `csv.DictReader` header-name-mapped readers. After re-ingest, run `POST /admin/cache/flush` to
  clear stale `iravi:reports:customer_balances_fy:*`, `iravi:reports:supplier_balances_fy:*`, and
  `iravi:ledger:*` entries from Redis.
- [ ] **RE-INGEST `Supplier Accounts Export File*.xlsx` after the CSV-content conversion
  (2026-07-22)** — the file must be re-uploaded to S3 `raw/` as real CSV text (the old
  openpyxl reader would fail/misread CSV bytes, and vice versa) so the updated
  single-sheet `csv.DictReader` handler parses it, applies the new numeric name-prefix
  strip, and the retire-absent step correctly closes suppliers no longer present. After
  re-ingest, run `POST /admin/cache/flush` to clear any stale
  `iravi:reports:supplier_balances_fy:*` entries from Redis.
- [ ] **IaC: `customer_details` milestoning migration (2026-07-12)** — add `in_z TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `out_z TIMESTAMPTZ`, `id BIGSERIAL PK` (replacing whatever the current PK is), and partial unique index `uix_customer_details_active ON customer_details (customer_name) WHERE out_z IS NULL` to `customer_details`. Must be applied via psql/SSM BEFORE re-deploying `etl_customer_accounts` (its `_upsert` now writes `in_z`/`out_z`-aware milestoning rows and no longer uses `ON CONFLICT`) and BEFORE the API's `/customers/names` / `/customers/details` `WHERE out_z IS NULL` filters will return anything (they'll error/return nothing on the old schema). `supplier_accounts` already has this shape (migration 016) — no equivalent migration needed there.
- [ ] **RE-INGEST after `customer_details` milestoning migration lands** — re-upload `Customer Accounts Export File*.xlsx` and `Supplier Accounts Export File*.xlsx` to S3 `raw/` so the retire-absent logic actually closes rows for customers/suppliers no longer in the export; then `POST /admin/cache/flush` to clear `iravi:customers:*` and `iravi:reports:supplier_balances_fy:*` Redis entries.
- [ ] **IaC: API Gateway route GET /reports/monthly-sales** — add `GET /reports/monthly-sales` route + CORS allow-method in `lambda_api.tf`; add `app_screens` seed migration row for `reports.monthly_sales` (screen_key, label, sort_order). No new Lambda, layer, or DB migration needed — existing `sales` and `customer_details` tables are used.
- [ ] **UI slice for /reports/monthly-sales** — add `getMonthlySales(month?: string)` client method in `src/api/client.ts` with typed response shape matching the JSON contract; add Monthly Sales report page + wire RBAC screen key `reports.monthly_sales` in the UI router.
- [ ] **No IaC/DB change needed for alert fields** — `net_sales_current_month` and `sale_returns_current_month` are served by the existing `GET /alerts/fields` route; no new API Gateway route, no new DB migration, no new Lambda layer. The `current_month` window SQL uses existing date-range FILTER clauses within `_query_aggregate_metrics` — same pattern as all other windows.

- [ ] **POST-DEPLOY: supplier_ledger cleanup + re-ingest (2026-06-27)** — after deploying the revised etl_supplier_ledger (combined _CONTRA_SUBCATEGORY map, sales-side rows kept): (a) re-upload / re-trigger `Ledger All Accounts*.xlsx` to S3 `raw/` so the Lambda re-runs and correctly inserts MERCO's 3 Sales Invoice legs as distinct rows; (b) run the one-time cleanup to close stale malformed rows left by the previous deploy: `UPDATE supplier_ledger SET out_z = NOW() WHERE out_z IS NULL AND sub_category IN ('Sales Invoice','Sales Invoice Returns');`; (c) flush Redis cache: `POST /admin/cache/flush` to purge stale `iravi:reports:supplier_balances_fy:*` entries. All three steps in order.
- [ ] **IaC: migration 017** — create `supplier_ledger` table. Schema: `id SERIAL PK, transaction_date DATE NOT NULL, voucher_no VARCHAR(50) NOT NULL, account_name VARCHAR(200) NOT NULL, category VARCHAR(10) NOT NULL, sub_category VARCHAR(100) NOT NULL, amount NUMERIC(15,4) NOT NULL, in_z TIMESTAMPTZ NOT NULL DEFAULT NOW(), out_z TIMESTAMPTZ`. Partial unique index: `CREATE UNIQUE INDEX ON supplier_ledger (transaction_date, voucher_no, account_name, category, sub_category) WHERE out_z IS NULL`. Apply via psql/SSM before running etl_supplier_ledger. Requires migration 016 (supplier_accounts) to be applied first.
- [ ] **IaC: lambda_etl_supplier_ledger.tf** — Lambda function (source_dir = `lambda/etl_supplier_ledger`, runtime python3.12, handler `handler.lambda_handler`); env vars `DATA_BUCKET`, `DB_SECRET_ARN`, `RAW_PREFIX`, `PROCESSED_PREFIX`; IAM: Secrets Manager `GetSecretValue` on `DB_SECRET_ARN`; `s3:GetObject` on `DATA_BUCKET` (read-only — no PutObject, no DeleteObject); trigger: EventBridge "Object Created" rule on key prefix `raw/Ledger` (same bucket/prefix as etl_customer_ledger's S3 trigger — both Lambdas receive the same event). S3 bucket EventBridge notifications must be enabled (already enabled if etl_customer_ledger is active). Layer: openpyxl + psycopg2-binary; add CI pip-layer build step in `terraform.yml`.
- [ ] **IaC: migration 016** — create `supplier_accounts` table. Schema: `id BIGSERIAL PK, name VARCHAR(255) NOT NULL, gst VARCHAR(20), gst_valid BOOLEAN, city VARCHAR(120), state VARCHAR(100), in_z TIMESTAMPTZ NOT NULL DEFAULT NOW(), out_z TIMESTAMPTZ NULL`. Partial unique index: `CREATE UNIQUE INDEX ON supplier_accounts (name) WHERE out_z IS NULL`. Apply via psql/SSM before running etl_supplier_accounts.
- [ ] **IaC: lambda_etl_supplier_accounts.tf** — Lambda function (source_dir = `lambda/etl_supplier_accounts`, runtime python3.12, handler `handler.lambda_handler`); env vars `DATA_BUCKET`, `DB_SECRET_ARN`, `RAW_PREFIX`, `PROCESSED_PREFIX`; IAM for Secrets Manager + `s3:GetObject` / `s3:PutObject` / `s3:DeleteObject` on `DATA_BUCKET`; S3 ObjectCreated notification with prefix filter `raw/Supplier` (i.e. `raw/Supplier Accounts Export File`). Layer: openpyxl + psycopg2-binary; add CI pip-layer build step in `terraform.yml`.
- [ ] **IaC: migration 015** — `ALTER TABLE alerts ADD COLUMN IF NOT EXISTS branch VARCHAR(100);` Must be applied via psql/SSM BEFORE deploying the updated api and alerts_evaluator Lambdas (both now SELECT branch). Push business-core first, then run migration, then terraform apply.
- [ ] **IaC: API Gateway routes for /alerts/fields with new categories** — the existing route serves /alerts/fields; the new ?category= param requires no additional IaC change (same route, same Lambda). No IaC change needed.
- [ ] **IaC: migration 013** — create `alerts`, `alert_conditions`, `alert_recipients`, `alert_runs` tables with correct FK + cascade rules. Apply via psql/SSM before deploying the alerts_evaluator Lambda or using the /alerts API.
- [ ] **IaC: migration 014** — `ALTER TABLE alerts ADD COLUMN IF NOT EXISTS schedule_time TIME NOT NULL DEFAULT '11:00';` Must be applied via psql/SSM BEFORE deploying the updated api and alerts_evaluator Lambdas (both now SELECT schedule_time). Push business-core first, then run migration, then terraform apply.
- [ ] **IaC: alerts_evaluator EventBridge rule** — change from `cron(30 5 * * ? *)` (daily 11:00 IST) to `rate(15 minutes)` in `lambda_alerts_evaluator.tf`. The per-alert schedule_time + alert_runs deduplication logic in the Lambda handles once-per-day semantics.
- [ ] **IaC: alerts_evaluator Lambda Terraform** — `lambda_alerts_evaluator.tf`: source_dir = `lambda/alerts_evaluator`, runtime python3.12, `DB_SECRET_ARN` + `ALERTS_SENDER_EMAIL` env vars, IAM for Secrets Manager + SES `ses:SendEmail`, EventBridge rule **rate(15 minutes)** (was cron daily — changed for per-alert schedule_time support). Layer: psycopg2-binary 2.9.9. Add layer build step in `terraform.yml`.
- [ ] **IaC: API Gateway routes for /alerts** — add `GET /alerts`, `GET /alerts/fields`, `POST /alerts`, `GET /alerts/{id}`, `PUT /alerts/{id}`, `DELETE /alerts/{id}`, `POST /alerts/{id}/test` routes + CORS in `lambda_api.tf`.
- [ ] **SES sender verification** — verify `ALERTS_SENDER_EMAIL` in AWS SES console (or move SES out of sandbox for production sending). Manual step.
- [ ] **Copy alerts_eval.py on every change** — whenever `lambda/api/alerts_eval.py` is updated, the copy at `lambda/alerts_evaluator/alerts_eval.py` must be kept in sync (cp command).
- [ ] **Apply IaC migration 011** — `customer_code VARCHAR(20)` column on `customer_details`; must be applied via psql/SSM before deploying the updated `etl_customer_accounts` Lambda; re-running the ETL on the existing file will backfill codes for all existing rows
- [ ] **RE-INGEST customer_details after General-as-master-list fix (2026-06-27)** — upload `Customer Accounts Export File*.xlsx` to S3 `raw/` so the updated Lambda inserts all General-sheet customers (with codes) and backfills codes + addresses for existing rows via ON CONFLICT DO UPDATE. Then run `POST /admin/cache/flush` to clear stale `iravi:reports:customer_balances_fy:*` entries. No schema change needed (same 7 columns; no IaC migration required).
- [ ] **Flush report cache after deploy** — `POST /admin/cache/flush` to clear stale `iravi:reports:customer_balances_fy:*` AND `iravi:ledger:statement:*` entries; required after the `code` field was added, after the `credit_notes` split (2026-06-23), and after the per-voucher netting fix (2026-06-23); no re-ingest needed
- [ ] **IaC slice for `/reports/customer-balances-fy`** — add API Gateway route `GET /reports/customer-balances-fy` + CORS allow-method in `lambda_api.tf` (iravi-dashboard-iac)
- [ ] **UI slice for `/reports/customer-balances-fy`** — add `getCustomerBalancesFy(fyCount)` client method in `src/api/client.ts`; add RBAC screen key `reports.customer_balances_fy` to `app_screens` (IaC migration) and wire the screen in the UI router
- [ ] **Run DB migrations** — apply `003`–`019` migrations via bastion SSM port-forward (all written; includes RBAC 009, customer_code 011, alerts 013–015, supplier_accounts 016, supplier_ledger 017, and report screen seeds 010/018/019)
- [ ] **whatsapp_notifier phase 2** — once WhatsApp Business approved: add `iravi/dashboard/whatsapp` secret (bearer_token, phone_number_id), add DB + Secrets Manager IAM to Lambda, implement `_send_whatsapp()` in handler
- [ ] **RE-INGEST customer_ledger after Account Group filter change (2026-06-27)** — the new filter selects by col[10] instead of `customer_details` membership, so previously-dropped customer rows are now included. The Ledger All Accounts file must be re-uploaded to S3 `raw/` to pick up the change. Note: this affects ALL consumers of `customer_ledger` (Customer Balances FY report, ledger statement, alerts balances evaluation, `iravi:ledger:range` redis key) — previously-missing customers will appear everywhere after re-ingest. Flush Redis (`POST /admin/cache/flush`) after re-ingest.
- [x] **Add Terraform resource** — `lambda_etl_appendix_b_x11.tf` + S3 trigger on `raw/Barcodes` in `lambda_etl_sales.tf` + layer build step in `terraform.yml`
- [ ] **RE-INGEST customer_ledger after category-by-column fix** — the Sales Invoice Returns roundoff fix changes `category` from `Cr` → `Db` for affected rows. Because `category` is part of the milestoning natural key `(transaction_date, voucher_no, account_name, category, sub_category)`, a plain re-ingest will NOT close the old `Cr` row (key mismatch); it will INSERT a duplicate `Db` row alongside the wrong `Cr` row. Required procedure: (1) **close** all open roundoff rows for affected vouchers manually (SQL: `UPDATE customer_ledger SET out_z = NOW() WHERE out_z IS NULL AND sub_category = 'Roundoff A/C' AND category = 'Cr' AND voucher_no IN (<affected voucher list>)`), then (2) re-upload the ledger xlsx to S3 so the ETL inserts corrected `Db` rows. Then flush Redis (`POST /admin/cache/flush`). Affected customers confirmed: EKR INDUSTRIES (POSRT2526-7, excess Cr 0.48), SRI VENKATESWARA COFFEE AND GENERAL STORES Podili (excess Cr 0.20). Also requires per-voucher netting deploy in the API (already implemented, pending cache flush).
- [ ] **Test etl_customer_ledger end-to-end** — upload ledger xlsx to S3, verify only valid customer rows inserted, verify `iravi:ledger:range` Redis key
- [ ] **Test etl_appendix_b_x11 end-to-end** — upload `Barcodes Masters*.xlsx` to S3 `raw/`, verify `appendix_b_x11_stock` rows and milestoning
- [ ] **Test etl_stocks end-to-end** — verify milestoning works across days, verify `snapshot_stock` rows, Redis keys, API responses
- [ ] **Implement etl_sales** — full handler: xlsx parse → `fact_sales`/`dim_customers` upsert → emit `ETLSalesSuccess` → archive
- [ ] **Implement `_update_sales_cache()`** in redis_updater once etl_sales is verified
- [ ] **Cognito + JWT authoriser** — add to API Gateway once Cognito Terraform is provisioned
- [ ] **IaC: migration 049** — `ALTER TABLE snapshot_stock ADD COLUMN expiry_date DATE;` + rebuild `uix_stock_active` to include `COALESCE(expiry_date, '9999-12-31')`. Must be applied via psql/SSM BEFORE the next `etl_stocks` run (its INSERT now supplies `expiry_date`) and before `GET /stocks/expiry`/`GET /stocks/expiry/pdf` will return anything (they SELECT `expiry_date`, which won't exist on the old schema).
- [ ] **IaC: API Gateway routes `GET /stocks/expiry` + `GET /stocks/expiry/pdf`** — add both routes + CORS allow-method in `lambda_api.tf`. No new Lambda/layer needed (same `api` Lambda).
- [ ] **RE-INGEST `StockReport*.csv` after the `ExpiryDate`-column deploy** — re-upload/re-trigger the stock CSV so `snapshot_stock` picks up the new finer (per-expiry-date) grain; flush Redis (`POST /admin/cache/flush`) to clear stale `iravi:stocks:current`/`iravi:stocks:summary`/`iravi:stocks:expiry`.
- [ ] **UI: Stock Expiry page** — no UI page exists yet; needs a client method + RBAC screen key + page wired to `GET /stocks/expiry` (list/table) and `GET /stocks/expiry/pdf` (export), with brand + expires_before_months (3/6/9/12/all) filters mirrored client-side to match the PDF.
