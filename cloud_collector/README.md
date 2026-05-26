# cloud_collector Lambda

Triggered by EventBridge every **6 hours** (main.tf `CHANGE 5`).

## What it does

1. Connects to `cloud_db` **primary** via `DATABASE_HOST`.
2. Fetches all active `CuentaCloud` records.
3. For each account, dispatches to the matching collector:
   - `AWS` → `collectors/aws_collector.py` (stub — real CE integration next sprint)
   - `GCP` → `collectors/gcp_collector.py` (stub — returns empty, logs warning)
4. Upserts `MetricaConsumo` rows (`INSERT … ON CONFLICT DO UPDATE`).
5. Invalidates `cloud:dashboard:*` Redis keys in DB 1.
6. Returns `{statusCode, processed, errors, cached_keys_invalidated}` to CloudWatch.

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_HOST` | Primary RDS endpoint (write path) |
| `DATABASE_PORT` | Default `5432` |
| `DATABASE_NAME` | `cloud_db` |
| `DATABASE_USER` | DB user |
| `DATABASE_PASSWORD` | DB password |
| `REDIS_URL` | Redis URL, DB index 1 (e.g. `redis://…:6379/1`) |

## Deployment

Packaged as a ZIP by Terraform `archive_file` data source:

```bash
# From repo root
zip -r cloud_collector.zip cloud_collector/
```

Terraform then uploads via `aws_lambda_function.cloud_collector` (see `main.tf`).

## Local testing

```bash
cd cloud_collector
pip install -r requirements.txt
python -c "from handler import lambda_handler; print(lambda_handler({}, None))"
```

## Next sprint

- Replace `aws_collector.py` stub with real `boto3 ce.get_cost_and_usage()` call.
- Add `AZURE` collector.
