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
    ├── etl_stocks/           ← ETL: parse stock balance xlsx → RDS snapshot_stock [COMPLETE]
    │   ├── handler.py        ← Lambda entry point (S3 trigger)
    │   ├── process.py        ← core transform logic (no S3/Lambda deps)
    │   ├── run_local.py      ← local test runner
    │   └── requirements.txt
    ├── etl_sales/            ← ETL: parse sales xlsx → RDS fact_sales [STUB]
    │   ├── handler.py
    │   ├── requirements.txt
    │   └── sample_data/
    ├── etl_customer_ledger/  ← ETL: parse Ledger All Accounts xlsx → RDS customer_ledger [COMPLETE]
    │   ├── handler.py
    │   └── requirements.txt
    ├── redis_updater/        ← Cache: RDS → ElastiCache Redis
    │   ├── handler.py
    │   └── requirements.txt
    └── api/                  ← API: serves dashboard requests via API Gateway
        ├── handler.py
        └── requirements.txt
```

---

## Lambda Overview

| Lambda | Trigger | Purpose | Status |
|---|---|---|---|
| `etl_stocks` | S3 `raw/Current*.xlsx` | Transform stock balances → `snapshot_stock` (unitemporal) | Complete |
| `etl_sales` | S3 `raw/RGF Sales Book*.xlsx` | Parse sales → `fact_sales` + `dim_customers` | Stub |
| `etl_customer_ledger` | S3 `raw/Ledger*.xlsx` | Parse ledger entries → `customer_ledger` (unitemporal) | Complete |
| `redis_updater` | EventBridge (ETL success events) | Pull RDS → write Redis cache | Stocks + ledger done |
| `api` | API Gateway HTTP v2 | Cache-aside JSON responses | Stocks done |

---

## Runtime & Dependencies

| Lambda | Runtime | Key packages |
|---|---|---|
| etl_stocks | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| etl_sales | Python 3.12 | psycopg2-binary, openpyxl, boto3 |
| etl_customer_ledger | Python 3.12 | openpyxl, psycopg2-binary, boto3 |
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3 |

---

## Deployment

Lambdas are packaged by Terraform using the `archive_file` data source — no separate build step.

Terraform configs live in `D:\Projects\Iravi\IaC\terraform\environments\production\`.

**Deployment order:** Commit + push `business-core` first (IaC GitHub Actions checks it out and references `business-core/lambda/` during plan/apply), then merge IaC to main to trigger `terraform apply`.

---

## Environment Variables

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | etl_stocks, etl_sales, etl_customer_ledger, redis_updater, api |
| `DATA_BUCKET` | Terraform | etl_stocks, etl_sales, etl_customer_ledger |
| `RAW_PREFIX` | Terraform | etl_stocks, etl_customer_ledger (default: `raw/`) |
| `PROCESSED_PREFIX` | Terraform | etl_stocks, etl_customer_ledger (default: `processed/`) |
| `EVENT_BUS_NAME` | Terraform | etl_stocks, etl_sales, etl_customer_ledger |
| `REDIS_HOST` | Terraform | redis_updater, api |

---

## Redis Keys (24h TTL)

| Key | Written by | Contents |
|---|---|---|
| `iravi:stocks:summary` | redis_updater on `ETLStocksSuccess` | `{total_kgs, total_vols, stock_valuation, total_products, as_of, updated_at}` |
| `iravi:stocks:current` | redis_updater on `ETLStocksSuccess` | JSON array of all current `snapshot_stock` rows |
| `iravi:ledger:range` | redis_updater on `ETLCustomerLedgerSuccess` | `{min_date, max_date}` of active `customer_ledger` rows |
