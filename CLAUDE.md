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
    ├── etl_sales/          ← ETL: parse sales xlsx → RDS (Phase 1 active)
    │   ├── handler.py
    │   ├── requirements.txt
    │   └── sample_data/    ← test xlsx files
    ├── etl_stocks/         ← ETL: parse stock balance xlsx → processed xlsx (core logic done)
    │   ├── process.py      ← core transform logic (no S3/Lambda deps)
    │   ├── run_local.py    ← local test runner
    │   └── requirements.txt
    ├── redis_updater/      ← Cache: RDS metrics → ElastiCache Redis
    │   ├── handler.py
    │   └── requirements.txt
    └── api/                ← API: serves dashboard requests via API Gateway
        ├── handler.py
        └── requirements.txt
```

---

## Lambda Deployment

Lambdas are packaged by Terraform using the `archive_file` data source — no separate build step.

Terraform configs live in:
```
D:\Projects\Iravi\IaC\terraform\environments\production\
├── lambda_etl_sales.tf
├── lambda_redis_updater.tf
└── lambda_api.tf
```

Deploy via the GitHub Actions pipeline (merge to main → apply runs automatically).

**Dependencies are packaged automatically** — The GitHub Actions workflow runs `pip install` into `.lambda_layers/<lambda>/python/` before `terraform plan/apply`. Terraform then zips that directory into a Lambda Layer. No local `pip install` step needed.

---

## Runtime & Dependencies

| Lambda | Runtime | Key packages |
|---|---|---|
| etl_sales | Python 3.12 | psycopg2-binary, openpyxl, boto3 |
| etl_stocks | Python 3.12 | openpyxl |
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3 |

---

## Environment Variables (per Lambda)

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | etl_sales, redis_updater, api |
| `DATA_BUCKET` | Terraform | etl_sales, etl_stocks |
| `RAW_PREFIX` | Terraform | etl_stocks (default: `raw/`) |
| `PROCESSED_PREFIX` | Terraform | etl_stocks (default: `processed/`) |
| `EVENT_BUS_NAME` | Terraform | etl_sales |
| `REDIS_HOST` | Terraform (after elasticache.tf added) | redis_updater, api |

---

## Phase 1 Scope — Sales First

**Active build target: `etl_sales`**

- Trigger: S3 `ObjectCreated` on `raw/*.xlsx`, filtered to `RGF Sales Book*.xlsx` in handler
- Parses: rows 6+ only (skip rows 1–5 header, detect/skip total rows)
- Columns: `Date, Voucher No, Branch, Party, Party GSTN, Qty, Gross, Disc, AV, CGST, SGST, IGST, Net, BillValue`
- Upserts: `dim_customers` (on `customer_name`), then `fact_sales` (on `voucher_no, transaction_date`)
- On success: writes `etl_runs` row, emits `ETLSalesSuccess` EventBridge event, moves file to `processed/`
- On failure: writes `etl_runs` row with `status=failed`, raises exception (CloudWatch alarm fires)

`redis_updater` and `api` are scaffolded but not yet implemented — activate after etl_sales is verified.

---

## etl_stocks — Stock Balance Processing

**Status: core processing logic + S3/Lambda handler + DB write complete**

Source file pattern: `Current Stock Balances*.xlsx`

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

**Row merging:** rows sharing the same (Brand, Technical, Packing Size, Packing Configuration, Branch, Special Packing Mention) are collapsed into one — `Available Nos` is summed, `Available Cases` and `Available Qty` are recalculated from the total. Rows with different Branch or Special Packing Mention are always kept separate.

**Rate lookup:** `process_stock_file` accepts optional `rates_path` pointing to a `Product Masters With Rates*.xlsx` file. Rates are joined on the raw product string, filtered to `Purchase Price List` only. Products not found in the master get blank Rate/Stock Valuation.

**Lambda handler (`handler.py`):**
- Trigger: S3 `ObjectCreated` on `{RAW_PREFIX}Current Stock Balances*.xlsx`
- Finds latest `{RAW_PREFIX}Product Masters With Rates*.xlsx` automatically (by LastModified)
- Downloads both to `/tmp/`, calls `process_stock_file`, uploads output to `{PROCESSED_PREFIX}Stock - Processed <date_suffix>.xlsx`
- Archives source to `{PROCESSED_PREFIX}raw/<original_filename>`
- Proceeds without rates if no rates file is found (logs a warning)

**Output columns:** Brand, Technical, Packing Size, Packing Configuration, Available Nos, Conversion Factor, Available Cases, Available Qty, Branch, Special Packing Mention, Entry Date, Rate, Stock Valuation

---

## What Is Built

- [x] Project structure created
- [x] README.md created
- [x] etl_sales scaffold (`handler.py`, `requirements.txt`)
- [x] redis_updater scaffold
- [x] api scaffold
- [x] Terraform resources in IaC (`lambda_etl_sales.tf`, `lambda_redis_updater.tf`, `lambda_api.tf`)
- [x] etl_stocks core logic (`process.py`, `run_local.py`) — transforms `Current Stock Balances*.xlsx` → `Stock - Processed.xlsx`
- [x] etl_stocks Lambda handler (`handler.py`) — S3 trigger, rates lookup, processed upload, source archive, DB upsert into `snapshot_stock` (unitemporal milestoning)

## What Is Next (build in this order)

- [x] **Add Terraform for etl_stocks** — `lambda_etl_stocks.tf` in IaC: S3 trigger fan-out from etl_sales notification, `DATA_BUCKET` env var, IAM for S3 read/write/list/delete
- [ ] **Implement etl_sales** — full handler: S3 download → xlsx parse → DB upsert → EventBridge event → move to processed/
- [ ] **Test etl_sales** — upload a real sales xlsx to `raw/` in S3, verify `fact_sales` rows in RDS
- [ ] **Add ElastiCache Terraform** (`elasticache.tf` in IaC) — prerequisite for redis_updater and api
- [ ] **Implement redis_updater** — query RDS sales metrics → write to Redis with 7-day TTL
- [ ] **Implement api** — `/sales` endpoint with cache-aside pattern
- [ ] **Cognito + JWT authoriser** — add to API Gateway once Cognito Terraform is provisioned
- [ ] **Phase 2: expand to all 8 file types** — after sales flow is verified end-to-end
