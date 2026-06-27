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
    ├── whatsapp_notifier/    ← S3 trigger on notifications/pending/ → phase 1 moves to notifications/processed/ → phase 2 sends WhatsApp [PHASE 1 COMPLETE]
    │   └── handler.py
    ├── redis_updater/        ← Cache: RDS → ElastiCache Redis (stocks + ledger range done)
    │   ├── handler.py
    │   └── requirements.txt
    ├── api/                  ← API: dashboard reads + POST /notify + RBAC auth/admin + alerts admin API [COMPLETE]
    │   ├── handler.py        ← routing, data endpoints, /auth/* + /admin/* + /alerts/* handlers
    │   ├── auth.py           ← PBKDF2 password hashing + HS256 JWT (stdlib only)
    │   ├── alerts_eval.py    ← SHARED: balances evaluation + FIFO aging + field catalog + validation
    │   └── requirements.txt
    └── alerts_evaluator/     ← EventBridge-triggered nightly alert evaluator (sends SES emails) [COMPLETE]
        ├── handler.py        ← lambda_handler: load due alerts → evaluate → SES send → alert_runs write
        ├── alerts_eval.py    ← copy of shared module (same source, duplicated per package)
        └── requirements.txt  ← psycopg2-binary==2.9.9 (boto3 from runtime)
```

---

## Lambda Deployment

Lambdas are packaged by Terraform using the `archive_file` data source — no separate build step.

Terraform configs live in:
```
D:\Projects\Iravi\IaC\terraform\environments\production\
├── lambda_etl_stocks.tf
├── lambda_etl_sales.tf
├── lambda_etl_customer_ledger.tf
├── lambda_etl_customer_accounts.tf
├── lambda_redis_updater.tf
└── lambda_api.tf
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
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3 |
| alerts_evaluator | Python 3.12 | psycopg2-binary (boto3/ses from runtime) |

---

## Environment Variables (per Lambda)

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, redis_updater, api |
| `DATA_BUCKET` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11, etl_supplier_accounts, api, whatsapp_notifier |
| `RAW_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11, etl_supplier_accounts (default: `raw/`) |
| `PROCESSED_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11, etl_supplier_accounts (default: `processed/`) |
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

**Parse rules (rows 6+):**
- Skip if `transaction_date` is None
- Skip if `account_name` is empty
- Skip if `voucher_no == 'Brought Forward'`
- Skip if `debit == 0 and credit == 0` (evaluated AFTER sign normalization below)
- Skip if `contra_account == 'Default Purchase Account'`

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

**Column mapping (0-indexed):** `[0]=date, [1]=voucher_no, [2]=transaction_name, [4]=account_name, [5]=contra_account, [6]=debit, [7]=credit`

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
1. `General` sheet — party-code lookup (col[0]=Name, col[2]=Code, e.g. `ANK001`). Read first to build an uppercase-name → code dict.
2. `Delivery Address` sheet (the workbook's active sheet) — address/contact data. Column mapping (0-indexed, header row 1, data from row 2): `[0]=Name, [3]=DLAddress3 (district), [4]=DLCity, [5]=DLState, [7]=DLPIN, [9]=DLMobileNo`

**Column mapping — General sheet (0-indexed, header row 1, data from row 2):**
`[0]=Name, [2]=Code (party code)`

**Transformations:**
- `customer_name` — uppercased (used as join key between both sheets)
- `district`, `city` — title-cased
- `state` — mapped: `37-Andhra Pradesh` → `AP`, `36-Telangana` → `TG`
- `mobile_no` — numeric values cast to string; string values stripped of whitespace
- `customer_code` — cast to string and stripped; blank/None stored as NULL; looked up by uppercased name from the `General` sheet; if no match the customer row is still inserted with `customer_code = NULL`

**Upsert strategy:** `INSERT ... ON CONFLICT (customer_name) DO UPDATE SET ...` — simple dimension upsert, no milestoning. `updated_at` refreshed on each update.

**Target table:** `customer_details` — `(customer_name, district, city, state, pin, mobile_no, customer_code, updated_at)`

**Migration dependency:** `customer_code VARCHAR(20)` column added by IaC migration `011`. Apply migration before running this Lambda. Re-running the ETL on any existing file will backfill `customer_code` for all existing rows via the `ON CONFLICT DO UPDATE` clause.

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

**Status: stocks complete; reports/customer-balances-fy complete; sales stub**

| Endpoint | Redis key | Status |
|---|---|---|
| `GET /stocks/summary` | `iravi:stocks:summary` | Complete |
| `GET /stocks/current` | `iravi:stocks:current` | Complete |
| `GET /sales` | — | Stub (returns empty array) |
| `GET /reports/customer-balances-fy` | `iravi:reports:customer_balances_fy:{fy_count}` | Complete |
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
   {"key":"net_sales_prev_day",     "label":"Net customer sales — previous day (₹)",             "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_prev_week",    "label":"Net customer sales — previous week (₹)",             "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_last_month",   "label":"Net customer sales — last month (₹)",               "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_prev_quarter", "label":"Net customer sales — previous fiscal quarter (₹)",  "type":"currency","ops":["gt","gte","lt","lte","eq","between"]},
   {"key":"net_sales_fy",           "label":"Net customer sales — FY to date (₹)",               "type":"currency","ops":["gt","gte","lt","lte","eq","between"]}
 ],
 "match_types":["all","any"],
 "frequencies":["daily","weekly","monthly"],
 "branch_scoped":true}
```

**`?category=sale_returns`** (parallel to `sales`, labels "Customer sale returns — …"):
- Keys: `sale_returns_prev_day`, `sale_returns_prev_week`, `sale_returns_last_month`, `sale_returns_prev_quarter`, `sale_returns_fy`
- Same type/ops/match_types/frequencies/branch_scoped structure as `sales`.

### Time windows (IST, relative to run_date = today)

| Window key | Date range |
|---|---|
| `prev_day` | yesterday |
| `prev_week` | Mon–Sun of the completed calendar week immediately before the current week |
| `last_month` | 1st–last of the previous calendar month |
| `prev_quarter` | Previous fiscal quarter (FY Apr–Mar: Q1=Apr–Jun, Q2=Jul–Sep, Q3=Oct–Dec, Q4=Jan–Mar) |
| `fy` | April 1 of the current FY through yesterday (empty range if run_date is April 1) |

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
- `FIELD_CATALOG_SALES`, `FIELD_CATALOG_SALE_RETURNS` — new aggregate catalogs
- `FIELD_CATALOGS` — dict mapping category → catalog
- `compute_window_dates(run_date)` — returns `{window_key: (start, end)}` for all 5 windows
- `evaluate_balances(conn, conditions, match_type, today)` — per-customer balances eval (unchanged)
- `evaluate_aggregate(conn, alert, today)` — aggregate eval for sales/sale_returns
- `_query_aggregate_metrics(conn, category, branch, windows, windows_needed)` — internal SQL helper
- `validate_alert(body)` — validates all three categories; field keys are per-category
- `is_alert_due_today(frequency, schedule_day, today)` — scheduling helper (unchanged)

### alerts_evaluator Lambda (`lambda/alerts_evaluator/handler.py`)

All existing gating is unchanged (due-today, time-reached, success-dedupe, 5/day failed cap).

**Category dispatch in the evaluation loop:**

- `balances` → `evaluate_balances()` → HTML customer-table email (unchanged path)
  - Subject: `[IRAVI Alert] <alert_name> — <date>`
  - `alert_runs.matched` = count of matched customers
- `sales` / `sale_returns` → `evaluate_aggregate()` → metrics-summary email if `matched=True`
  - Subject: `[IRAVI Alert] Sales — <date>` or `[IRAVI Alert] Sale Returns — <date>`
  - Email: two HTML tables — Conditions (field label, op, threshold, actual, breached?) + Window Metrics (metric label, value ₹)
  - `alert_runs.matched` = 1 if fired, 0 if not
  - `status` = `sent` if fired, `no_match` if conditions did not fire, `failed` on exception

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
- [x] etl_customer_ledger `known_customers` filter — loads customer set from `customer_details` once per invocation; skips any ledger row whose `account_name` is not in the set
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
- [ ] **Flush report cache after deploy** — `POST /admin/cache/flush` to clear stale `iravi:reports:customer_balances_fy:*` AND `iravi:ledger:statement:*` entries; required after the `code` field was added, after the `credit_notes` split (2026-06-23), and after the per-voucher netting fix (2026-06-23); no re-ingest needed
- [ ] **IaC slice for `/reports/customer-balances-fy`** — add API Gateway route `GET /reports/customer-balances-fy` + CORS allow-method in `lambda_api.tf` (iravi-dashboard-iac)
- [ ] **UI slice for `/reports/customer-balances-fy`** — add `getCustomerBalancesFy(fyCount)` client method in `src/api/client.ts`; add RBAC screen key `reports.customer_balances_fy` to `app_screens` (IaC migration) and wire the screen in the UI router
- [ ] **Run DB migrations** — apply `003`, `004`, `005`, `006`, `007`, `008` migrations via bastion SSM port-forward
- [ ] **whatsapp_notifier phase 2** — once WhatsApp Business approved: add `iravi/dashboard/whatsapp` secret (bearer_token, phone_number_id), add DB + Secrets Manager IAM to Lambda, implement `_send_whatsapp()` in handler
- [ ] **Run cleanup SQL** — close bad `customer_ledger` rows: `UPDATE customer_ledger SET out_z = NOW() WHERE out_z IS NULL AND account_name NOT IN (SELECT customer_name FROM customer_details)`
- [x] **Add Terraform resource** — `lambda_etl_appendix_b_x11.tf` + S3 trigger on `raw/Barcodes` in `lambda_etl_sales.tf` + layer build step in `terraform.yml`
- [ ] **RE-INGEST customer_ledger after category-by-column fix** — the Sales Invoice Returns roundoff fix changes `category` from `Cr` → `Db` for affected rows. Because `category` is part of the milestoning natural key `(transaction_date, voucher_no, account_name, category, sub_category)`, a plain re-ingest will NOT close the old `Cr` row (key mismatch); it will INSERT a duplicate `Db` row alongside the wrong `Cr` row. Required procedure: (1) **close** all open roundoff rows for affected vouchers manually (SQL: `UPDATE customer_ledger SET out_z = NOW() WHERE out_z IS NULL AND sub_category = 'Roundoff A/C' AND category = 'Cr' AND voucher_no IN (<affected voucher list>)`), then (2) re-upload the ledger xlsx to S3 so the ETL inserts corrected `Db` rows. Then flush Redis (`POST /admin/cache/flush`). Affected customers confirmed: EKR INDUSTRIES (POSRT2526-7, excess Cr 0.48), SRI VENKATESWARA COFFEE AND GENERAL STORES Podili (excess Cr 0.20). Also requires per-voucher netting deploy in the API (already implemented, pending cache flush).
- [ ] **Test etl_customer_ledger end-to-end** — upload ledger xlsx to S3, verify only valid customer rows inserted, verify `iravi:ledger:range` Redis key
- [ ] **Test etl_appendix_b_x11 end-to-end** — upload `Barcodes Masters*.xlsx` to S3 `raw/`, verify `appendix_b_x11_stock` rows and milestoning
- [ ] **Test etl_stocks end-to-end** — verify milestoning works across days, verify `snapshot_stock` rows, Redis keys, API responses
- [ ] **Implement etl_sales** — full handler: xlsx parse → `fact_sales`/`dim_customers` upsert → emit `ETLSalesSuccess` → archive
- [ ] **Implement `_update_sales_cache()`** in redis_updater once etl_sales is verified
- [ ] **Cognito + JWT authoriser** — add to API Gateway once Cognito Terraform is provisioned
