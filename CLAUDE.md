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
├── .gitignore
└── lambda/
    ├── etl_sales/          ← ETL: parse sales xlsx → RDS (Phase 1 active)
    │   ├── handler.py
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

---

## Runtime & Dependencies

| Lambda | Runtime | Key packages |
|---|---|---|
| etl_sales | Python 3.12 | psycopg2-binary, openpyxl, boto3 |
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3 |

---

## Environment Variables (per Lambda)

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | etl_sales, redis_updater, api |
| `DATA_BUCKET` | Terraform | etl_sales |
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

## What Is Built

- [x] Project structure created
- [x] etl_sales scaffold (`handler.py`, `requirements.txt`)
- [x] redis_updater scaffold
- [x] api scaffold
- [x] Terraform resources in IaC (`lambda_etl_sales.tf`, `lambda_redis_updater.tf`, `lambda_api.tf`)

## What Is Next (build in this order)

- [ ] **Implement etl_sales** — full handler: S3 download → xlsx parse → DB upsert → EventBridge event → move to processed/
- [ ] **Test etl_sales** — upload a real sales xlsx to `raw/` in S3, verify `fact_sales` rows in RDS
- [ ] **Add ElastiCache Terraform** (`elasticache.tf` in IaC) — prerequisite for redis_updater and api
- [ ] **Implement redis_updater** — query RDS sales metrics → write to Redis with 7-day TTL
- [ ] **Implement api** — `/sales` endpoint with cache-aside pattern
- [ ] **Cognito + JWT authoriser** — add to API Gateway once Cognito Terraform is provisioned
- [ ] **Phase 2: expand to all 8 file types** — after sales flow is verified end-to-end
