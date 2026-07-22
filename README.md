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
    ├── etl_supplier_accounts/            ← ETL: Supplier Accounts Export → supplier_accounts (unitemporal) [COMPLETE]
    ├── etl_supplier_ledger/              ← ETL: Ledger All Accounts (supplier rows) → supplier_ledger [COMPLETE]
    ├── whatsapp_notifier/                ← S3 notifications/pending → processed; WhatsApp send phase 2 [PHASE 1]
    ├── redis_updater/                    ← Cache: RDS → ElastiCache Redis (EventBridge-triggered)
    ├── alerts_evaluator/                 ← EventBridge rate(15m): evaluate due alerts → SES email (+PDF) [COMPLETE]
    └── api/                              ← API: dashboard reads + POST /notify + RBAC auth/admin + /alerts + /reports/*
```

Each Lambda directory contains `handler.py` + `requirements.txt` (plus `process.py` / `run_local.py` for `etl_stocks`, `sample_data/` for `etl_sales`).

---

## Lambda Overview

| Lambda | Trigger | Purpose | Status |
|---|---|---|---|
| `etl_stocks` | S3 `raw/Current*.xlsx` | Stock balances → `snapshot_stock` (unitemporal) → emit `ETLStocksSuccess` | Complete |
| `etl_sales` | S3 `raw/RGF Sales Book*.xlsx` | Sales → `fact_sales` + `dim_customers` | Stub |
| `etl_customer_ledger` | S3 `raw/Ledger*.xlsx` | Ledger entries → `customer_ledger` (unitemporal) → emit `ETLCustomerLedgerSuccess`. Sales Invoice Returns branch now classifies category by column (`Cr if credit > 0 else Db`) so return roundoffs that land on the debit side after sign-normalization are stored as `Db` (not forced to `Cr`). | Complete |
| `etl_customer_accounts` | S3 `raw/Customer*.xlsx` | Customer accounts → `customer_details` (unitemporal). Source is now single-sheet CSV content (comma-delimited, header row 1, `.xlsx` filename retained), header-name-based mapping (2026-07-22) | Complete |
| `etl_appendix_b_x11` | S3 `raw/Barcodes*.xlsx` | Barcodes Masters → `appendix_b_x11_stock` (unitemporal) | Complete |
| `etl_appendix_b_x11_purchase` | S3 `raw/AppendixPurchase*.xlsx` | → `appendix_b_x11_stock_ledger` (In) + `purchases` (N). Source is now CSV content (comma-delimited, header row 1, `.xlsx` filename retained), header-name-based mapping (2026-07-21) | Complete |
| `etl_appendix_b_x11_purchase_return` | S3 `raw/AppendixPurReturn*.xlsx` | → `stock_ledger` (Out) + `purchases` (Y). Source is now CSV content (comma-delimited, header row 1, `.xlsx` filename retained), header-name-based mapping (2026-07-21) | Complete |
| `etl_appendix_b_x11_sale` | S3 `raw/AppendixSale*.xlsx` | → `stock_ledger` (Out) + `sales` (N). Source is now CSV content (comma-delimited, header row 1, `.xlsx` filename retained), header-name-based mapping (2026-07-22) | Complete |
| `etl_appendix_b_x11_sale_return` | S3 `raw/AppendixRetSales*.xlsx` | → `stock_ledger` (In) + `sales` (Y). Source is now CSV content (comma-delimited, header row 1, `.xlsx` filename retained), header-name-based mapping (2026-07-22) | Complete |
| `etl_supplier_accounts` | S3 `raw/Supplier*.xlsx` | Supplier accounts → `supplier_accounts` (unitemporal). Source is now single-sheet CSV content (comma-delimited, header row 1, `.xlsx` filename retained), header-name-based mapping incl. a numeric-name-prefix strip and blank/"NULL"-aware GST/GSTValid/StateName handling (2026-07-22) | Complete |
| `etl_supplier_ledger` | EventBridge `raw/Ledger*.xlsx` (read-only S3) | Ledger supplier rows → `supplier_ledger` (unitemporal) | Complete |
| `whatsapp_notifier` | S3 `notifications/pending/*` | Move to `processed/`; send WhatsApp (phase 2) | Phase 1 |
| `redis_updater` | EventBridge (ETL success events) | Pull RDS → write Redis cache | Stocks + ledger done |
| `alerts_evaluator` | EventBridge `rate(15 min)` | Evaluate due alerts → send SES email (Monthly Sales / FY reports as PDF) | Complete |
| `api` | API Gateway HTTP v2 | Cache-aside reads + `POST /notify` + RBAC `/auth/*` & `/admin/*` + `/alerts*` + `/reports/*` | Complete |

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
| etl_supplier_accounts | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_supplier_ledger | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| whatsapp_notifier | Python 3.12 | boto3 (+ requests in phase 2) |
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| alerts_evaluator | Python 3.12 | psycopg2-binary, reportlab (boto3/SES from runtime) |
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
| `ALERTS_SENDER_EMAIL` | Terraform | alerts_evaluator (verified SES sender address) |

---

## Redis Keys

| Key | Written by | TTL | Contents |
|---|---|---|---|
| `iravi:stocks:summary` | redis_updater on `ETLStocksSuccess` | 24h | `{total_kgs, total_vols, stock_valuation, total_products, as_of, updated_at}` |
| `iravi:stocks:current` | redis_updater on `ETLStocksSuccess` | 24h | JSON array of all current `snapshot_stock` rows |
| `iravi:ledger:range` | redis_updater on `ETLCustomerLedgerSuccess` | 24h | `{min_date, max_date}` of active `customer_ledger` rows |
| `iravi:ledger:data:{from}:{to}` | api (cache-aside) | 1h | `LedgerRow[]` |
| `iravi:sales:*`, `iravi:purchases:summary:*` | api (cache-aside) | 15 min | meta / list / summary results |
| `iravi:reports:customer_balances_fy:{fy_count}` | api (cache-aside) | 1h | `{fys, rows, totals}` — customer balances FY report |
| `iravi:reports:supplier_balances_fy:{fy_count}` | api (cache-aside) | 1h | `{fys, rows, totals}` — supplier balances FY report |
| `iravi:reports:monthly_sales:{month}` | api (cache-aside) | 1h | state-wise net customer sales for one calendar month |

> Keys written on a cache miss by the `api` Lambda are populated on demand; keys written by `redis_updater` are refreshed nightly after the ETL success events.

---

## API Endpoints

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/stocks/summary` | — | Aggregate stock summary tiles |
| `GET` | `/stocks/current` | — | All active stock rows |
| `GET` | `/ledger/range` | — | Min/max transaction date in ledger |
| `GET` | `/ledger/outstanding` | `to_date` | Cumulative outstanding balance as of date |
| `GET` | `/ledger/statement` | `account_name, from_date, to_date` | Per-voucher ledger statement |
| `GET` | `/ledger` | `from_date, to_date` | Raw ledger rows for a date window |
| `GET` | `/appendix-b/meta` | — | Customers, branches, technical names, date range |
| `GET` | `/appendix-b/report` | `branch, technical_name, from_date, to_date` | Appendix-B roll-forward report |
| `GET` | `/purchases/meta` | — | Branches + date range for purchases |
| `GET` | `/purchases/summary` | `from_date, to_date[, branch, exclude_internal]` | Purchases/returns invoice counts + AV totals |
| `GET` | `/purchases/monthly` | `from_date, to_date[, branch]` | Month-by-month purchases vs returns |
| `GET` | `/purchases/list` | `from_date, to_date[, branch]` | Line-item purchases list |
| `GET` | `/sales/meta` | — | Branches + date range for sales |
| `GET` | `/sales/list` | `from_date, to_date[, branch]` | Line-item sales list |
| `GET` | `/customers/names` | — | Sorted list of customer names |
| `GET` | `/customers/details` | — | `[{customer_name, city}]` |
| `GET` | `/reports/customer-balances-fy` | `fy_count=all\|2\|3\|4` | Per-customer multi-FY roll-forward from `customer_ledger` |
| `GET` | `/reports/supplier-balances-fy` | `fy_count=all\|2\|3\|4` | Per-supplier multi-FY roll-forward from `supplier_ledger` |
| `GET` | `/reports/monthly-sales` | `month=YYYY-MM` | State-wise net customer sales for one calendar month |
| `GET\|POST` | `/alerts` | — | List / create alerts (admin only) |
| `GET` | `/alerts/fields` | `category` | Field catalog for the alert builder (admin only) |
| `GET\|PUT\|DELETE` | `/alerts/{id}` | — | Read / update / delete an alert (admin only) |
| `POST` | `/alerts/{id}/test` | — | Dry-run evaluate an alert now, no email sent (admin only) |
| `POST` | `/notify` | — | Queue a PDF notification to `notifications/pending/` |
| `POST` | `/auth/login` | — | Authenticate; return JWT + user info |
| `GET` | `/auth/me` | — | Re-read caller's role + screens |
| `GET` | `/admin/screens` | — | List RBAC screen keys (admin only) |
| `GET\|POST` | `/admin/roles` | — | List / create roles (admin only) |
| `PUT\|DELETE` | `/admin/roles/{role_id}` | — | Update / delete role (admin only) |
| `GET\|POST` | `/admin/users` | — | List / create users (admin only) |
| `PUT\|DELETE` | `/admin/users/{user_id}` | — | Update / delete user (admin only) |
| `POST` | `/admin/cache/flush` | — | Clear all `iravi:*` Redis keys (admin only) |
