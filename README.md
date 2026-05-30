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
    ├── etl_sales/          ← ETL: parse sales xlsx → RDS (Phase 1 active)
    │   ├── handler.py
    │   ├── requirements.txt
    │   └── sample_data/    ← test xlsx files
    ├── etl_stocks/         ← ETL: parse stock balance xlsx → processed xlsx
    │   ├── handler.py      ← Lambda entry point (S3 trigger)
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

## Lambda Overview

| Lambda | Purpose | Status |
|---|---|---|
| `etl_sales` | Parse sales xlsx from S3 → upsert into RDS | Phase 1 active |
| `etl_stocks` | Transform `Current Stock Balances*.xlsx` + rates → processed output xlsx | Handler complete; Terraform pending |
| `redis_updater` | Pull RDS metrics → cache in ElastiCache Redis | Scaffolded |
| `api` | Serve dashboard requests via API Gateway | Scaffolded |

---

## Runtime & Dependencies

| Lambda | Runtime | Key packages |
|---|---|---|
| etl_sales | Python 3.12 | psycopg2-binary, openpyxl, boto3 |
| etl_stocks | Python 3.12 | openpyxl |
| redis_updater | Python 3.12 | psycopg2-binary, redis, boto3 |
| api | Python 3.12 | psycopg2-binary, redis, boto3 |

---

## Deployment

Lambdas are packaged by Terraform using the `archive_file` data source — no separate build step.

Terraform configs live in `D:\Projects\Iravi\IaC\terraform\environments\production\`.

Deploy via GitHub Actions: merge to `main` → pipeline applies Terraform automatically.

---

## Environment Variables

| Variable | Set by | Used in |
|---|---|---|
| `DB_SECRET_ARN` | Terraform | etl_sales, redis_updater, api |
| `DATA_BUCKET` | Terraform | etl_sales |
| `EVENT_BUS_NAME` | Terraform | etl_sales |
| `REDIS_HOST` | Terraform (after elasticache.tf) | redis_updater, api |

---

## Phase 1 — etl_sales Detail

- **Trigger:** S3 `ObjectCreated` on `raw/*.xlsx`, filtered to `RGF Sales Book*.xlsx`
- **Parses:** rows 6+ (rows 1–5 are header; total rows are skipped)
- **Columns:** `Date, Voucher No, Branch, Party, Party GSTN, Qty, Gross, Disc, AV, CGST, SGST, IGST, Net, BillValue`
- **Upserts:** `dim_customers` (on `customer_name`) → `fact_sales` (on `voucher_no, transaction_date`)
- **On success:** writes `etl_runs` row, emits `ETLSalesSuccess` EventBridge event, moves file to `processed/`
- **On failure:** writes `etl_runs` row with `status=failed`, raises exception (triggers CloudWatch alarm)
