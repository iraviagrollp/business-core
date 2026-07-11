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
    ├── etl_stocks/           ← ETL: parse stock balance xlsx → RDS snapshot_stock [COMPLETE]
    │   ├── handler.py        ← Lambda entry point (S3 trigger)
    │   ├── process.py        ← core transform logic (no S3/Lambda deps)
    │   ├── run_local.py      ← local test runner
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
    │   └── requirements.txt
    └── alerts_evaluator/     ← EventBridge-triggered nightly alert evaluator (sends SES emails) [COMPLETE]
        ├── handler.py        ← lambda_handler: load due alerts → evaluate → SES send → alert_runs write
        │                        Added 2026-07-06: customer_balances_fy branch (always fires → PDF attachment)
        │                        Added 2026-07-06: supplier_balances_fy branch (always fires → PDF attachment;
        │                        no code/credit_notes; supplier footer legend differs from customer)
        ├── alerts_eval.py    ← copy of shared module (same source, duplicated per package)
        │                        Added 2026-07-06: FIELD_CATALOG_CUSTOMER_BALANCES_FY + FIELD_CATALOGS entry
        │                        Added 2026-07-06: FIELD_CATALOG_SUPPLIER_BALANCES_FY + FIELD_CATALOGS entry
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
        ├── customer_balances_fy.py ← SHARED: byte-identical copy of api/customer_balances_fy.py
        │                        compute_customer_balances_fy(conn, fy_count) → dict
        │                        (added 2026-07-06)
        ├── customer_balances_fy_pdf.py ← PDF renderer for Customer Balances (FY), landscape A4
        │                        render_customer_balances_fy_pdf(data) → bytes
        │                        Uses DejaVuSans TTFont (via pdf_fonts.register_fonts()) so ₹/— render
        │                        without KeyError. Two-row header (repeatRows=2), #1a3c2b headers,
        │                        #f0f0f0 TOTAL row, always-visible Credit Notes column.
        │                        Indian-grouped rupee amounts (₹12,34,567.00). Landscape 1cm margins.
        │                        (added 2026-07-06)
        │                        Dr/Cr balance coloring added 2026-07-01: per-FY Balance(₹),
        │                        Balance Dr, Balance Cr cells colored in data + TOTAL rows.
        │                        Customer: Dr → RED (#cc0000), Cr → GREEN (#1a6e35).
        ├── supplier_balances_fy.py ← SHARED: byte-identical copy of api/supplier_balances_fy.py
        │                        compute_supplier_balances_fy(conn, fy_count) → dict
        │                        No code field; no credit_notes field. Sort by party name ascending.
        │                        (added 2026-07-06)
        ├── supplier_balances_fy_pdf.py ← PDF renderer for Supplier Balances (FY), landscape A4
        │                        render_supplier_balances_fy_pdf(data) → bytes
        │                        Uses DejaVuSans TTFont (via pdf_fonts.register_fonts()) so ₹/— render
        │                        without KeyError. Two-row header (repeatRows=2), #1a3c2b headers,
        │                        #f0f0f0 TOTAL row. No Code column; no Credit Notes column (3 sub-cols
        │                        per FY: Debit/Credit/Balance). Supplier footer legend:
        │                        'Dr = Debit (payable); Cr = Credit (advance/overpayment).'
        │                        Indian-grouped rupee amounts (₹12,34,567.00). Landscape 1cm margins.
        │                        (added 2026-07-06)
        │                        Dr/Cr balance coloring added 2026-07-01: per-FY Balance(₹),
        │                        Balance Dr, Balance Cr cells colored in data + TOTAL rows.
        │                        Supplier SWAPPED: Dr → GREEN (#1a6e35), Cr → RED (#cc0000).
        ├── pdf_fonts.py      ← SHARED: idempotent register_fonts() — registers DejaVuSans and
        │                        DejaVuSans-Bold with reportlab pdfmetrics; falls back to Helvetica
        │                        with warning on failure. Fixes ₹ (U+20B9) + — (U+2014) KeyError
        │                        that crashes Helvetica-based doc.build() on Lambda.
        │                        Reused by both Customer and Supplier Balances (FY) PDF renderers.
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
| api | Python 3.12 | psycopg2-binary, redis, boto3 |
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

Source file pattern: `Current Stock Balances*.xlsx` (S3 prefix filter: `raw/Current`)

**Product string parsing** (`Technical - Brand - Packing Size [- Packing Spec]`):
- Brand column used as anchor to locate split point in product string
- Handles embedded brand+size in one segment (IMIX pattern: `...WP - IMIX 8 GMS TIN`)
- Handles multi-segment technical names containing ` - ` (VIVAYA PLUS)
- Handles optional packing spec segment (BOX, TIN, POUCH S, POUCH L)

**Unit conversion** (always normalised to grams or ml):
- `GMS`, `GM` → `gms` (no conversion)
- `KG` → `gms` × 1000
- `ML` → `ml` (no conversion)
- `LT`, `LTR`, `L` → `ml` × 1000

**`available_qty` in DB:** stored as kg or L (divided by 1000 on INSERT). The in-memory dict and Excel output retain the original gram/ml value. The API and redis_updater accumulate the raw DB value directly — no further division.

**Row merging:** rows sharing the same (Brand, Technical, Packing Size, Packing Configuration, Branch, Special Packing Mention) are collapsed into one — `Available Nos` is summed, `Available Cases` and `Available Qty` are recalculated from the total.

**Rate lookup:** `process_stock_file` accepts optional `rates_path` pointing to a `Product Masters With Rates*.xlsx` file. Rates are joined on the raw product string, filtered to `Purchase Price List` only.

**Lambda handler (`handler.py`):**
- Trigger: S3 `ObjectCreated` on `{RAW_PREFIX}Current Stock Balances*.xlsx`
- Finds latest `{RAW_PREFIX}Product Masters With Rates*.xlsx` automatically (by LastModified)
- Downloads both to `/tmp/`, calls `process_stock_file`, uploads output to `{PROCESSED_PREFIX}Stock - Processed <date_suffix>.xlsx`
- Archives source to `{PROCESSED_PREFIX}raw/<original_filename>`
- Upserts into `snapshot_stock` (unitemporal milestoning)
- Emits `ETLStocksSuccess` EventBridge event on success

**Snapshot replace:** `Current Stock Balances*.xlsx` is a full snapshot, not an incremental feed. Each run closes **every** currently active row (`UPDATE snapshot_stock SET out_z = NOW() WHERE out_z IS NULL`) before inserting the new rows — not just rows matching a natural key in the new file. This ensures products/packings that drop out of stock (absent from the new file) are correctly marked superseded instead of remaining `out_z IS NULL` forever, which would otherwise inflate `iravi:stocks:current` and the summary tiles.

---

## etl_customer_ledger — Customer Ledger Processing

**Status: complete**

Source file pattern: `Ledger All Accounts*.xlsx` (S3 prefix filter: `raw/Ledger`)

**Customer row selection (col[10] — Account Group, 2026-06-27):**
- Identifies customer rows directly from the ledger file: `account_group = str(row[10] or '').strip()`.
- Keeps a row only if `account_group.lower() == 'all customer accounts'`. Case-insensitive comparison for safety. Distinct groups in the file include "All Customer Accounts" (341 rows in sample file), "All Supplier Accounts" (114), "All Sales Accounts", "All Bank Accounts", blank (GL/GST contra-leg rows), etc.
- Explicit IRAVI exclusion: after the account group check, rows where `'iravi' in account_name.lower()` are dropped. IRAVI own-company accounts appear under "All Customer Accounts" in the ledger and must not land in `customer_ledger`.
- **Does NOT read `customer_details` at all.** No DB read is required for filtering. This means customers who have ledger rows but no `customer_details` master record (no party code) are now correctly included. The API's `_handle_customer_balances_fy` still LEFT-joins `customer_details` for code/city; customers without a master record show null code and null city (UI renders as a dash).
- Sample file (2026-06-27): 341 raw "All Customer Accounts" rows → 2 IRAVI rows dropped → remaining skip rules (Brought Forward 213, Default Purchase Account 27, no date 2) → **268 rows written** from 36 distinct customer parties.

**Parse rules (rows 6+):**
- Skip if `transaction_date` is None
- Skip if `account_name` is empty
- Skip if `voucher_no == 'Brought Forward'`
- Skip if `debit == 0 and credit == 0` (evaluated AFTER sign normalization below)
- Skip if `contra_account == 'Default Purchase Account'`
- Skip if `account_group != 'All Customer Accounts'` (case-insensitive, col[10])
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

**Column mapping (0-indexed):** `[0]=date, [1]=voucher_no, [2]=transaction_name, [4]=account_name (Account field), [5]=contra_account, [6]=debit, [7]=credit, [10]=account_group`

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

**Sheets read (both from the same workbook):**
1. `General` sheet — **authoritative customer list** (col[0]=Name, col[2]=Code, e.g. `ANK001`). Every account in the General sheet produces a row in `customer_details` with its party code. Read by `_build_code_lookup(wb)` → `{UPPER_NAME -> code|None}`.
2. `Delivery Address` sheet (the workbook's active sheet) — address/contact enrichment. Read by `_build_delivery_lookup(wb)` → `{UPPER_NAME -> {district, city, state, pin, mobile_no}}`. First occurrence of a name wins when duplicates exist. Column mapping (0-indexed, header row 1, data from row 2): `[0]=Name, [3]=DLAddress3 (district), [4]=DLCity, [5]=DLState, [7]=DLPIN, [9]=DLMobileNo`.

**Row source (changed 2026-06-27):**
The master customer list is now the **union** of `General` and `Delivery Address` names
(`all_names = set(code_lookup) | set(delivery_lookup)`). Previously the row source was
only the `Delivery Address` sheet, which caused customers without a delivery address to be
missing from `customer_details` entirely — and therefore to show no code in the Customer
Balances FY report. Now:
- **General-only customers** — inserted WITH their code; address fields all NULL.
- **Delivery-Address-only customers** — inserted with address fields; `customer_code = NULL`.
- **Customers in both sheets** — inserted WITH their code AND their address fields.

No customer is lost relative to the previous behaviour; the union strictly adds rows.

**Column mapping — General sheet (0-indexed, header row 1, data from row 2):**
`[0]=Name, [2]=Code (party code)`

**Transformations:**
- `customer_name` — uppercased (used as join key between both sheets)
- `district`, `city` — title-cased
- `state` — mapped: `37-Andhra Pradesh` → `AP`, `36-Telangana` → `TG`
- `mobile_no` — numeric values cast to string; string values stripped of whitespace; last 10 digits used if > 10
- `customer_code` — cast to string and stripped; blank/None stored as NULL; looked up by uppercased name from the `General` sheet; if no match the customer row is still inserted with `customer_code = NULL`

**Upsert strategy:** `INSERT ... ON CONFLICT (customer_name) DO UPDATE SET ...` — simple dimension upsert, no milestoning. `updated_at` refreshed on each update.

**Target table:** `customer_details` — `(customer_name, district, city, state, pin, mobile_no, customer_code, updated_at)`

**Migration dependency:** `customer_code VARCHAR(20)` column added by IaC migration `011`. Apply migration before running this Lambda. Re-running the ETL on any existing file will backfill `customer_code` for all existing rows via the `ON CONFLICT DO UPDATE` clause.

**Re-ingest required after 2026-06-27 deploy:** the `Customer Accounts Export File*.xlsx` must be re-uploaded to S3 `raw/` so the updated Lambda can insert General-only customers and backfill codes for existing rows. After re-ingest, run `POST /admin/cache/flush` to clear stale `iravi:reports:customer_balances_fy:*` entries from Redis.

---

## etl_supplier_accounts — Supplier Master Processing

**Status: complete**

Source file pattern: `Supplier Accounts Export File*.xlsx` (S3 prefix filter: `raw/Supplier`)

**Workbook layout:** Sheet `General` (header row 1, data from row 2). A second empty `Sheet1` exists — it is ignored.

**Column mapping (0-indexed):**
`[0]=Name, [6]=GST, [7]=GSTValid, [12]=City, [13]=State`

**Transforms (applied in order per row):**
- `name` = `str(row[0] or '').strip()` — blank row → SKIP.
- IRAVI FILTER: `'iravi' in name.lower()` → SKIP (drops "IRAVI AGRO LIFE HYD" and "IRAVI AGRO LIFE LLP - GNT").
- `gst` = `str(row[6] or '').strip() or None`
- `gst_valid`: `row[7] is None` → `None` (NULL); else `bool(int(row[7]))` — 1 → `True`, 0 → `False`. None and 0 are distinct: None = no GST registered; False = GST present but invalid.
- `city` = `str(row[12] or '').strip().title() or None` (source casing inconsistent; normalised to title case).
- `state`: raw = `str(row[13] or '').strip()`; if `'-'` in raw → take the part after the first `'-'` (e.g. `"36-Telangana"` → `"Telangana"`); else keep raw; blank → `None`.

**Milestoning upsert (uni-temporal, close-then-insert):**
Natural key = `name`. Partial unique index on `(name) WHERE out_z IS NULL`.
For each parsed row:
```sql
UPDATE supplier_accounts SET out_z = NOW() WHERE name = %s AND out_z IS NULL;
INSERT INTO supplier_accounts (name, gst, gst_valid, city, state) VALUES (%s,%s,%s,%s,%s);
```
All rows written in a single DB transaction; committed once after the loop.

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

Source file: same `Ledger All Accounts*.xlsx` used by `etl_customer_ledger`. Reads `wb.active` (sheet named "Invoice"); header row 5; data from `min_row=6`.

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

**Supplier filter (col[10] — Account Group):**
- Identifies supplier rows directly from the ledger file: `account_group = str(row[10] or '').strip()`.
- Keeps a row only if `account_group.lower() == 'all supplier accounts'`. Case-insensitive comparison for safety. Distinct groups in the file include "All Customer Accounts", "All Supplier Accounts", "All Sales Accounts", "All Bank Accounts", etc.
- Explicit IRAVI exclusion: after the account group check, rows where `'iravi' in account_name.lower()` are dropped. IRAVI own-company accounts ("IRAVI AGRO LIFE HYD", "IRAVI AGRO LIFE LLP - GNT") appear under "All Supplier Accounts" in the ledger and must not land in `supplier_ledger`.
- **Does NOT read `supplier_accounts` at all.** No DB read is required for filtering.
- Sample file (2026-06-27): 114 raw "All Supplier Accounts" rows → 10 IRAVI rows dropped → remaining skip rules (Brought Forward, zero-value, null-date) → **83 rows written** from 7 distinct suppliers including JAGRUTHI AGRO CHEMICALS (36 rows).

**Column mapping (0-indexed):** `[0]=transaction_date, [1]=voucher_no, [2]=transaction_name, [4]=account_name (Account field), [5]=contra_account, [6]=debit, [7]=credit, [10]=account_group`

**Parse / skip rules (identical mechanics to etl_customer_ledger):**
- Sign normalization applied first: negative debit → add to credit & zero; negative credit → add to debit & zero.
- Skip if: `transaction_date_raw is None`; `account_name` empty; `voucher_no == 'Brought Forward'`; `debit == 0 and credit == 0` (after normalization); `account_group.lower() != 'all supplier accounts'`; `'iravi' in account_name.lower()`.
- Date parsed with multi-format `_parse_date` (datetime / date / `%Y-%m-%d` / `%d-%m-%Y` / `%d/%m/%Y`). Unparseable → log warning and skip.
- **No sales-transaction exclusion.** Sales made TO a supplier (where the supplier is classified under `All Supplier Accounts` in FUSIL but is also an IRAVI customer) are legitimate and must appear in `supplier_ledger` so they show in Supplier Balances FY. The `Default Sales Account` skip and the `transaction_name.startswith('sales')` skip that were added 2026-06-27 have been reverted (2026-06-27).

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

**Header:** row 5. **Data:** row 6+. Skip if `purchase_date` is None, `iravi_voucher` is empty, or `product`/`technical_name` is empty.

**Column mapping (0-indexed):**
`[0]=Date→purchase_date, [1]=Voucher No→iravi_voucher/voucher_no, [2]=Branch→branch, [5]=Party→party, [6]=Ref BillNo→supplier_voucher/ref_bill_no, [7]=Ref BillDate→ref_bill_date, [9]=Product→technical_name/product, [10]=Qty→qty, [11]=Rate→rate, [12]=Gross→gross, [17]=AV→av, [25]=Barcodes→barcode/barcodes, [26]=Narration→narration`

**Transformations:**
- `product`/`technical_name` — strip all commas from the product string
- `barcode` — strip trailing comma, split by `,`; rows with multiple barcodes are skipped **for the ledger table only** (the `purchases` table gets every parsed row regardless of barcode count)
- `in_out` — hardcoded `'In'` (purchase report)
- `mdf_date` / `exp_date` — looked up from `appendix_b_x11_stock WHERE (technical_name, barcode) AND out_z IS NULL`; NULL if no match
- `purchase_return` — hardcoded `'N'`

**Writes to two tables per row:**
1. `appendix_b_x11_stock_ledger` — milestoning natural key `(purchase_date, iravi_voucher, technical_name, barcode)`; only rows with exactly 1 barcode (DB migration `006_create_appendix_b_x11_stock_ledger.sql`)
2. `purchases` — milestoning natural key/PK `(purchase_date, voucher_no, branch, party, product)`; every parsed row (DB migration `007_create_purchases.sql`)

---

## purchases — Purchase Line-Item Table

**Status: complete**

Line-item purchase ledger populated by both `etl_appendix_b_x11_purchase` (`AppendixPurchaseReport*.xlsx`, `purchase_return='N'`) and `etl_appendix_b_x11_purchase_return` (`AppendixPurReturn*.xlsx`, `purchase_return='Y'`).

**Columns:** `purchase_date, voucher_no, branch, party, ref_bill_no, ref_bill_date, product, qty, rate, gross, av, barcodes, narration, purchase_return, in_z, out_z`

**Milestoning natural key / PK:** `(purchase_date, voucher_no, branch, party, product)` — UPDATE closes any open record matching all five, then INSERT adds the new row. `purchase_return` is not part of the key.

**Target table:** `purchases` (DB migration `007_create_purchases.sql`)

---

## etl_appendix_b_x11_sale / etl_appendix_b_x11_sale_return — Sales Ledger Processing

**Status: complete**

Source file patterns: `AppendixSale*.xlsx` (S3 prefix filter: `raw/AppendixSale`, `sales_return='N'`, `in_out='Out'`) and `AppendixRetSales*.xlsx` (S3 prefix filter: `raw/AppendixRetSales`, `sales_return='Y'`, `in_out='In'`).

**Header:** row 5. **Data:** row 6+. Skip if `purchase_date` is None, `iravi_voucher` is empty, or `product`/`technical_name` is empty.

**Column mapping (0-indexed, identical for both files):**
`[0]=Date→purchase_date, [1]=Voucher No→iravi_voucher/voucher_no, [2]=Branch→branch, [3]=Party→party, [4]=Ref BillNo→ref_bill_no, [5]=Ref BillDate→ref_bill_date, [6]=Product→technical_name/product, [7]=Qty→qty, [8]=Rate→rate, [9]=Gross→gross, [14]=AV→av, [22]=Barcodes→barcode/barcodes`

**Transformations:**
- `product`/`technical_name` — strip all commas from the product string
- `barcode` — strip trailing comma, split by `,`; rows with multiple barcodes are skipped **for the ledger table only** (the `sales` table gets every parsed row regardless of barcode count)
- `mdf_date` / `exp_date` — looked up from `appendix_b_x11_stock WHERE (technical_name, barcode) AND out_z IS NULL`; NULL if no match
- `narration` — always NULL (neither source file has a Narration column)

**Writes to two tables per row:**
1. `appendix_b_x11_stock_ledger` — milestoning natural key `(purchase_date, iravi_voucher, technical_name, barcode)`; only rows with exactly 1 barcode (DB migration `006_create_appendix_b_x11_stock_ledger.sql`)
2. `sales` — milestoning natural key/PK `(purchase_date, voucher_no, branch, party, product)`; every parsed row (DB migration `008_create_sales.sql`)

---

## sales — Sales Line-Item Table

**Status: complete**

Line-item sales ledger populated by both `etl_appendix_b_x11_sale` (`AppendixSale*.xlsx`, `sales_return='N'`) and `etl_appendix_b_x11_sale_return` (`AppendixRetSales*.xlsx`, `sales_return='Y'`).

**Columns:** `purchase_date, voucher_no, branch, party, ref_bill_no, ref_bill_date, product, qty, rate, gross, av, barcodes, narration, sales_return, in_z, out_z` — same shape as `purchases`, with `purchase_return` renamed to `sales_return`. `narration` is always NULL (no source column).

**Milestoning natural key / PK:** `(purchase_date, voucher_no, branch, party, product)` — UPDATE closes any open record matching all five, then INSERT adds the new row. `sales_return` is not part of the key.

**Target table:** `sales` (DB migration `008_create_sales.sql`)

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
| `ETLStocksSuccess` | `_update_stocks_cache()` | `iravi:stocks:summary`, `iravi:stocks:current` |
| `ETLCustomerLedgerSuccess` | `_update_ledger_range_cache()` | `iravi:ledger:range` |
| `ETLSalesSuccess` | `_update_sales_cache()` | *(stub — not yet implemented)* |

**`iravi:ledger:range`:** `{min_date, max_date}` — MIN/MAX of `transaction_date WHERE out_z IS NULL` in `customer_ledger`. 24h TTL.

---

## api — API Layer

**Status: stocks complete; reports/customer-balances-fy complete; reports/supplier-balances-fy complete; reports/monthly-sales complete; sales stub**

| Endpoint | Redis key | Status |
|---|---|---|
| `GET /stocks/summary` | `iravi:stocks:summary` | Complete |
| `GET /stocks/current` | `iravi:stocks:current` | Complete |
| `GET /sales` | — | Stub (returns empty array) |
| `GET /reports/customer-balances-fy` | `iravi:reports:customer_balances_fy:{fy_count}` | Complete |
| `GET /reports/supplier-balances-fy` | `iravi:reports:supplier_balances_fy:{fy_count}` | Complete |
| `GET /reports/monthly-sales` | `iravi:reports:monthly_sales:{month}` | Complete |
| `GET /ledger/statement` | `iravi:ledger:statement:{account}:{from}:{to}` | Complete |

Cache-aside pattern: Redis first → RDS fallback → populate Redis.

**Ledger statement — per-voucher netting (added 2026-06-23):**
`GET /ledger/statement` already groups rows by voucher before returning. Each voucher's `debit` and `credit` fields are now netted: `net = raw_debit − raw_credit`; if `net >= 0` then `debit=net, credit=0.0`; else `debit=0.0, credit=-net`. This absorbs roundoff/GST sub-components so no phantom opposite-side paise appear on the statement. `total_debit`/`total_credit` are summed from the netted values. `closing_balance` is unchanged because `net = raw_debit − raw_credit`, so `Σ(netted_debit − netted_credit) = Σ(raw_debit − raw_credit)`. Cache flush required after deploy (no re-ingest needed).

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

**Source tables:** `supplier_ledger` (`out_z IS NULL`, `LOWER(account_name) NOT LIKE '%%iravi%%'`) and `supplier_accounts` (city lookup only — `SELECT UPPER(name), city FROM supplier_accounts`).

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

**Redis key:** `iravi:reports:monthly_sales:{month}` (e.g. `iravi:reports:monthly_sales:2026-06`). TTL: `_LEDGER_TTL` (1 hour). Cleared by `POST /admin/cache/flush`.

**RBAC screen key (IaC + UI must register):** `reports.monthly_sales`

**IaC requirements:** API Gateway route `GET /reports/monthly-sales` + CORS in `lambda_api.tf`; `app_screens` seed migration to insert `reports.monthly_sales`. No new DB table or column needed — reads from existing `sales` and `customer_details` tables.

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
