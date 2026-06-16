# IRAVI AGRO LIFE LLP — Business Core

Processing logic for the IRAVI Dashboard. Contains all Lambda functions that power the nightly data pipeline and the API layer.

## Related Projects

| Project | Path |
|---|---|
| Infrastructure (Terraform) | `D:\Projects\Iravi\IaC\` |
| File Sync Agent | `D:\Projects\Iravi\FileSyncAgent\` |

---

## Repository Layout

```
business-core/
├── CLAUDE.md
├── README.md
├── .gitignore
└── lambda/
    ├── etl_stocks/                       ← ETL: stock balance xlsx → snapshot_stock [COMPLETE]
    │   ├── handler.py · process.py · run_local.py · requirements.txt
    ├── etl_sales/                        ← ETL: sales xlsx → fact_sales [STUB]
    ├── etl_customer_ledger/              ← ETL: Ledger All Accounts → customer_ledger [COMPLETE]
    ├── etl_customer_accounts/            ← ETL: Customer Accounts Export → customer_details [COMPLETE]
    ├── etl_appendix_b_x11/               ← ETL: Barcodes Masters → appendix_b_x11_stock [COMPLETE]
    ├── etl_appendix_b_x11_purchase/      ← ETL: AppendixPurchaseReport → stock_ledger + purchases (N) [COMPLETE]
    ├── etl_appendix_b_x11_purchase_return/ ← ETL: AppendixPurReturn → stock_ledger + purchases (Y) [COMPLETE]
    ├── etl_appendix_b_x11_sale/          ← ETL: AppendixSale → stock_ledger + sales (N) [COMPLETE]
    ├── etl_appendix_b_x11_sale_return/   ← ETL: AppendixRetSales → stock_ledger + sales (Y) [COMPLETE]
    ├── whatsapp_notifier/                ← S3 notifications/pending → processed; WhatsApp send phase 2 [PHASE 1]
    ├── redis_updater/                    ← Cache: RDS → ElastiCache Redis (EventBridge-triggered)
    └── api/                              ← API: dashboard reads via API Gateway + POST /notify
```

Each Lambda directory contains `handler.py` + `requirements.txt` (plus `process.py` / `run_local.py` for `etl_stocks`, `sample_data/` for `etl_sales`).

---

## Lambda Overview

| Lambda | Trigger | Purpose | Status |
|---|---|---|---|
| `etl_stocks` | S3 `raw/Current*.xlsx` | Stock balances → `snapshot_stock` (unitemporal) → emit `ETLStocksSuccess` | Complete |
| `etl_sales` | S3 `raw/RGF Sales Book*.xlsx` | Sales → `fact_sales` + `dim_customers` | Stub |
| `etl_customer_ledger` | S3 `raw/Ledger*.xlsx` | Ledger entries → `customer_ledger` (unitemporal) → emit `ETLCustomerLedgerSuccess` | Complete |
| `etl_customer_accounts` | S3 `raw/Customer*.xlsx` | Customer accounts → `customer_details` (upsert) | Complete |
| `etl_appendix_b_x11` | S3 `raw/Barcodes*.xlsx` | Barcodes Masters → `appendix_b_x11_stock` (unitemporal) | Complete |
| `etl_appendix_b_x11_purchase` | S3 `raw/AppendixPurchase*.xlsx` | → `appendix_b_x11_stock_ledger` (In) + `purchases` (N) | Complete |
| `etl_appendix_b_x11_purchase_return` | S3 `raw/AppendixPurReturn*.xlsx` | → `stock_ledger` (Out) + `purchases` (Y) | Complete |
| `etl_appendix_b_x11_sale` | S3 `raw/AppendixSale*.xlsx` | → `stock_ledger` (Out) + `sales` (N) | Complete |
| `etl_appendix_b_x11_sale_return` | S3 `raw/AppendixRetSales*.xlsx` | → `stock_ledger` (In) + `sales` (Y) | Complete |
| `whatsapp_notifier` | S3 `notifications/pending/*` | Move to `processed/`; send WhatsApp (phase 2) | Phase 1 |
| `redis_updater` | EventBridge (ETL success events) | Pull RDS → write Redis cache | Stocks + ledger done |
| `api` | API Gateway HTTP v2 | Cache-aside JSON responses + `POST /notify` + RBAC `/auth/*` & `/admin/*` | Data + RBAC done |

> **RBAC:** `api/auth.py` (stdlib PBKDF2 + HS256 JWT, key in Secrets Manager `iravi/dashboard/jwt`) backs `POST /auth/login`, `GET /auth/me`, and the admin role/user management endpoints (`/admin/roles`, `/admin/users`, `/admin/screens`). Login + `/admin/*` are enforced server-side; the data endpoints are UI-only gated for now (backlog: per-route authorization).

> **S3 fan-out:** all ETL Lambdas share a single `aws_s3_bucket_notification` (in `lambda_etl_sales.tf`) keyed by non-overlapping `raw/` prefixes. Prefixes stop before the first space (S3 filters silently fail on spaces); each handler URL-decodes and re-checks the full filename.

---

## Runtime & Dependencies

| Lambda | Runtime | Key packages |
|---|---|---|
| etl_stocks | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_sales | Python 3.12 | psycopg2-binary, openpyxl, boto3 |
| etl_customer_ledger | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_customer_accounts | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_appendix_b_x11 (×5) | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| whatsapp_notifier | Python 3.12 | boto3 (+ requests in phase 2) |
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3 |

> `api` and `redis_updater` share the `api_deps` Lambda layer (linux-wheel psycopg2-binary + redis-py). All dependency layers are built by the GitHub Actions workflow before `terraform plan/apply`.

---

## Deployment

Lambdas are packaged by Terraform using the `archive_file` data source — no separate build step.

Terraform configs live in `D:\Projects\Iravi\IaC\terraform\environments\production\`.

**Deployment order:** Commit + push `business-core` first (IaC GitHub Actions checks it out and references `business-core/lambda/` during plan/apply), then merge IaC to main to trigger `terraform apply`.

---

## Environment Variables

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | all ETL Lambdas, redis_updater, api |
| `DATA_BUCKET` | Terraform | all ETL Lambdas, api, whatsapp_notifier |
| `RAW_PREFIX` | Terraform | ETL Lambdas (default: `raw/`) |
| `PROCESSED_PREFIX` | Terraform | ETL Lambdas (default: `processed/`) |
| `EVENT_BUS_NAME` | Terraform | etl_stocks, etl_sales, etl_customer_ledger (default: `default`) |
| `REDIS_HOST` | Terraform | redis_updater, api |
| `JWT_SECRET_ARN` | Terraform | api (RBAC token signing key) |
| `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` | Terraform | api (first-login admin bootstrap) |

---

## Redis Keys

| Key | Written by | TTL | Contents |
|---|---|---|---|
| `iravi:stocks:summary` | redis_updater on `ETLStocksSuccess` | 24h | `{total_kgs, total_vols, stock_valuation, total_products, as_of, updated_at}` |
| `iravi:stocks:current` | redis_updater on `ETLStocksSuccess` | 24h | JSON array of all current `snapshot_stock` rows |
| `iravi:ledger:range` | redis_updater on `ETLCustomerLedgerSuccess` | 24h | `{min_date, max_date}` of active `customer_ledger` rows |
| `iravi:ledger:data:{from}:{to}` | api (cache-aside) | 1h | `LedgerRow[]` |
| `iravi:sales:*`, `iravi:purchases:summary:*` | api (cache-aside) | 15 min | meta / list / summary results |

> Keys written on a cache miss by the `api` Lambda are populated on demand; keys written by `redis_updater` are refreshed nightly after the ETL success events.
