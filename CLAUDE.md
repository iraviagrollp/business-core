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
    ├── whatsapp_notifier/    ← S3 trigger on notifications/pending/ → phase 1 moves to notifications/processed/ → phase 2 sends WhatsApp [PHASE 1 COMPLETE]
    │   └── handler.py
    ├── redis_updater/        ← Cache: RDS → ElastiCache Redis (stocks + ledger range done)
    │   ├── handler.py
    │   └── requirements.txt
    └── api/                  ← API: dashboard reads + POST /notify + RBAC auth/admin
        ├── handler.py        ← routing, data endpoints, /auth/* + /admin/* handlers
        ├── auth.py           ← PBKDF2 password hashing + HS256 JWT (stdlib only)
        └── requirements.txt
        ├── handler.py
        └── requirements.txt
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
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3 |

---

## Environment Variables (per Lambda)

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, redis_updater, api |
| `DATA_BUCKET` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11, api, whatsapp_notifier |
| `RAW_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11 (default: `raw/`) |
| `PROCESSED_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11 (default: `processed/`) |
| `EVENT_BUS_NAME` | Terraform | etl_stocks, etl_sales, etl_customer_ledger (default: `default`) |
| `REDIS_HOST` | Terraform | redis_updater, api |
| `JWT_SECRET_ARN` | Terraform | api (RBAC token signing key) |
| `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` | Terraform | api (first-login admin bootstrap) |

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
