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
    ├── etl_appendix_b_x11_sale/ ← ETL: parse AppendixSale xlsx → RDS appendix_b_x11_stock_ledger (in_out=Out) [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── etl_appendix_b_x11_sale_return/ ← ETL: parse AppendixRetSales xlsx → RDS appendix_b_x11_stock_ledger (in_out=In) [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── redis_updater/        ← Cache: RDS → ElastiCache Redis (stocks + ledger range done)
    │   ├── handler.py
    │   └── requirements.txt
    └── api/                  ← API: serves dashboard requests via API Gateway
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
| `DATA_BUCKET` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11 |
| `RAW_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11 (default: `raw/`) |
| `PROCESSED_PREFIX` | Terraform | etl_stocks, etl_customer_ledger, etl_customer_accounts, etl_appendix_b_x11 (default: `processed/`) |
| `EVENT_BUS_NAME` | Terraform | etl_stocks, etl_sales, etl_customer_ledger (default: `default`) |
| `REDIS_HOST` | Terraform | redis_updater, api |

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

**Milestoning natural key:** `(brand, technical, packing_size, packing_configuration, branch, special_packing_mention)` — `entry_date` is NOT in the UPDATE predicate (it's temporal data, not a business key).

---

## etl_customer_ledger — Customer Ledger Processing

**Status: complete**

Source file pattern: `Ledger All Accounts*.xlsx` (S3 prefix filter: `raw/Ledger`)

**Parse rules (rows 6+):**
- Skip if `transaction_date` is None
- Skip if `account_name` is empty
- Skip if `voucher_no == 'Brought Forward'`
- Skip if `debit == 0 and credit == 0`
- Skip if `contra_account == 'Default Purchase Account'`

**Column mapping (0-indexed):** `[0]=date, [1]=voucher_no, [2]=transaction_name, [4]=account_name, [5]=contra_account, [6]=debit, [7]=credit`

**Category & sub-category logic:**
| Transaction Name | Category | Sub-category (from Contra Account) |
|---|---|---|
| Sales Invoice | `Db` | CGST Output A/C → CGST, SGST Output A/C → SGST, IGST Output A/C → IGST, Default Sales Account → Sale, Roundoff A/C → Roundoff |
| Sales Invoice Returns | `Cr` | CGST Input A/C → CGST, SGST Input A/C → SGST, IGST Input A/C → IGST, Default SalesReturn Account → Sales Return |
| Bank Receipts | `Cr` | Bank Receipt |
| Cash Receipts | `Cr` | Cash Receipt |
| *(any other)* | `Cr`/`Db` from credit/debit col | transaction_name (fallback) |

**Milestoning natural key:** `(transaction_date, voucher_no, account_name, category, sub_category)` — UPDATE closes open record matching all five, then INSERT adds new row.

**On success:** archives source to `processed/raw/`, emits `ETLCustomerLedgerSuccess` EventBridge event → triggers redis_updater to write `iravi:ledger:range`.

---

## etl_customer_accounts — Customer Details Processing

**Status: complete**

Source file pattern: `Customer Accounts Export File*.xlsx` (S3 prefix filter: `raw/Customer`)

**Column mapping (0-indexed, header row 1, data from row 2):**
`[0]=Name, [3]=DLAddress3 (district), [4]=DLCity, [5]=DLState, [7]=DLPIN, [9]=DLMobileNo`

**Transformations:**
- `customer_name` — uppercased
- `district`, `city` — title-cased
- `state` — mapped: `37-Andhra Pradesh` → `AP`, `36-Telangana` → `TG`
- `mobile_no` — numeric values cast to string; string values stripped of whitespace

**Upsert strategy:** `INSERT ... ON CONFLICT (customer_name) DO UPDATE SET ...` — simple dimension upsert, no milestoning. `updated_at` refreshed on each update.

**Target table:** `customer_details` — `(customer_name, district, city, state, pin, mobile_no, updated_at)`

---

## etl_appendix_b_x11_purchase — Purchase Ledger Processing

**Status: complete**

Source file pattern: `AppendixPurchaseReport*.xlsx` (S3 prefix filter: `raw/AppendixPurchase`)

**Header:** row 5. **Data:** row 6+. Skip if `purchase_date` is None or `iravi_voucher` is empty.

**Column mapping (0-indexed):**
`[0]=Date→purchase_date, [1]=Voucher No→iravi_voucher, [2]=Branch→branch, [5]=Party→party, [6]=Ref BillNo→supplier_voucher, [9]=Product→technical_name, [10]=Qty→qty, [25]=Barcodes→barcode`

**Transformations:**
- `technical_name` — strip all commas from the product string
- `barcode` — strip trailing comma, split by `,`; rows with multiple barcodes are skipped
- `in_out` — hardcoded `'In'` (purchase report)
- `mdf_date` / `exp_date` — looked up from `appendix_b_x11_stock WHERE (technical_name, barcode) AND out_z IS NULL`; NULL if no match

**Milestoning natural key:** `(purchase_date, iravi_voucher, technical_name, barcode)`

**Target table:** `appendix_b_x11_stock_ledger` (DB migration `006_create_appendix_b_x11_stock_ledger.sql`)

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

**Status: stocks complete; sales stub**

| Endpoint | Redis key | Status |
|---|---|---|
| `GET /stocks/summary` | `iravi:stocks:summary` | Complete |
| `GET /stocks/current` | `iravi:stocks:current` | Complete |
| `GET /sales` | — | Stub (returns empty array) |

Cache-aside pattern: Redis first → RDS fallback → populate Redis.

---

## What Is Built

- [x] Project structure created
- [x] etl_stocks core logic (`process.py`, `run_local.py`)
- [x] etl_stocks Lambda handler — S3 trigger, rates lookup, processed upload, source archive, DB upsert (`snapshot_stock`), `ETLStocksSuccess` event
- [x] etl_stocks `available_qty` fix — stored in DB as kg/L (÷1000 on INSERT only)
- [x] etl_stocks milestoning fix — `entry_date` removed from UPDATE predicate; only business key used
- [x] etl_customer_ledger Lambda — full handler: parse `Ledger All Accounts*.xlsx`, category/sub-category mapping, unitemporal upsert into `customer_ledger`, archive source, emit `ETLCustomerLedgerSuccess`
- [x] etl_customer_accounts Lambda — full handler: parse `Customer Accounts Export File*.xlsx`, normalise case + state codes, upsert into `customer_details`
- [x] etl_customer_accounts mobile_no normalization — strip spaces, take last 10 digits if > 10
- [x] etl_customer_ledger `known_customers` filter — loads customer set from `customer_details` once per invocation; skips any ledger row whose `account_name` is not in the set
- [x] etl_appendix_b_x11 Lambda — full handler: parse `Barcodes Masters*.xlsx`, normalize barcodes + dates, unitemporal upsert into `appendix_b_x11_stock`
- [x] DB migration `005_create_appendix_b_x11_stock.sql` — `appendix_b_x11_stock` table with (barcode, technical_name, vendor) milestoning
- [x] etl_appendix_b_x11_purchase Lambda — full handler: parse `AppendixPurchaseReport*.xlsx`, skip multi-barcode rows, look up mdf_date/exp_date from `appendix_b_x11_stock`, upsert into `appendix_b_x11_stock_ledger` (in_out=In)
- [x] etl_appendix_b_x11_purchase_return Lambda — same as purchase but `AppendixPurReturn*.xlsx`, different column layout ([3]=Party, [4]=Ref BillNo, [6]=Product, [7]=Qty, [22]=Barcodes), in_out=Out
- [x] etl_appendix_b_x11_sale Lambda — parse `AppendixSale*.xlsx`, column layout [0]=Date, [1]=VoucherNo, [2]=Branch, [3]=Party, [4]=RefBillNo, [6]=Product, [7]=Qty, [22]=Barcodes; in_out=Out; inserts into appendix_b_x11_stock_ledger
- [x] etl_appendix_b_x11_sale_return Lambda — same layout, `AppendixRetSales*.xlsx`, in_out=In
- [x] DB migration `006_create_appendix_b_x11_stock_ledger.sql` — `appendix_b_x11_stock_ledger` table with (purchase_date, iravi_voucher, technical_name, barcode) milestoning
- [x] Terraform + S3 trigger + GitHub Actions layer build for `etl_appendix_b_x11_purchase`
- [x] etl_sales scaffold (`handler.py`, `requirements.txt`) — parse/upsert logic TODO
- [x] redis_updater — `ETLStocksSuccess` handler: writes `iravi:stocks:summary` + `iravi:stocks:current`
- [x] redis_updater — `ETLCustomerLedgerSuccess` handler: writes `iravi:ledger:range`
- [x] api — `GET /stocks/summary` + `GET /stocks/current` with cache-aside; `GET /sales` stub
- [x] Terraform resources: `lambda_etl_stocks.tf`, `lambda_etl_sales.tf`, `lambda_etl_customer_ledger.tf`, `lambda_etl_customer_accounts.tf`, `lambda_redis_updater.tf`, `lambda_api.tf`
- [x] GitHub Actions layer build steps for all Lambdas (plan + apply jobs)

## What Is Next (build in this order)

- [ ] **Run DB migrations** — apply `003`, `004`, `005`, `006` migrations via bastion SSM port-forward
- [ ] **Run cleanup SQL** — close bad `customer_ledger` rows: `UPDATE customer_ledger SET out_z = NOW() WHERE out_z IS NULL AND account_name NOT IN (SELECT customer_name FROM customer_details)`
- [x] **Add Terraform resource** — `lambda_etl_appendix_b_x11.tf` + S3 trigger on `raw/Barcodes` in `lambda_etl_sales.tf` + layer build step in `terraform.yml`
- [ ] **Test etl_customer_ledger end-to-end** — upload ledger xlsx to S3, verify only valid customer rows inserted, verify `iravi:ledger:range` Redis key
- [ ] **Test etl_appendix_b_x11 end-to-end** — upload `Barcodes Masters*.xlsx` to S3 `raw/`, verify `appendix_b_x11_stock` rows and milestoning
- [ ] **Test etl_stocks end-to-end** — verify milestoning works across days, verify `snapshot_stock` rows, Redis keys, API responses
- [ ] **Implement etl_sales** — full handler: xlsx parse → `fact_sales`/`dim_customers` upsert → emit `ETLSalesSuccess` → archive
- [ ] **Implement `_update_sales_cache()`** in redis_updater once etl_sales is verified
- [ ] **Cognito + JWT authoriser** — add to API Gateway once Cognito Terraform is provisioned
