# Cloud-Native Security · Database Audit Log Serverless Collection Solution
[中文版](README.cn.md)

![License: MIT No Attribution](https://img.shields.io/badge/License-MIT_No_Attribution-blue.svg)
![Huawei Cloud](https://img.shields.io/badge/Cloud-Huawei_Cloud-red.svg)
![Terraform](https://img.shields.io/badge/IaC-Terraform-purple.svg)

## Overview

This solution deploys a **serverless audit log collection and querying system for RDS MySQL / GaussDB MySQL** on Huawei Cloud. It automatically discovers database instances with audit logging enabled, periodically fetches audit logs and archives them to OBS, and provides SQL query capability through DLI (Data Lake Insight) — fully serverless, pay-per-use.

Suitable for database audit compliance, operation tracing, and security analysis scenarios.

## Table of Contents

- [Architecture](#architecture)
- [Highlights](#highlights)
- [Cloud Services Used](#cloud-services-used)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Step-by-Step Deployment](#step-by-step-deployment)
- [Usage](#usage)
- [Cleanup](#cleanup)
- [Configuration](#configuration)
- [FAQ](#faq)
- [Dependencies](#dependencies)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

## Architecture

```
                    ┌─────────────────────┐
                    │  FunctionGraph      │
  TIMER (daily 02)  │  Discovery Function │
                    │  Auto-discover      │
                    └────────┬────────────┘
                             │ Update cluster_config.json
                             ▼
                    ┌─────────────────────┐
                    │  OBS Bucket         │
                    │  cluster_config.json│
                    └────────┬────────────┘
                             │
                    ┌────────▼────────────┐
                    │  FunctionGraph      │
  TIMER (5 min)     │  Dispatcher Function│
                    │  Read config → fan  │
                    └────────┬────────────┘
                             │ Async invoke × N
                    ┌────────▼────────────┐
                    │  FunctionGraph      │
                    │  Worker Function × N│
                    │  Fetch audit logs   │
                    └────────┬────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │  RDS API   │  │  OBS Archive│ │  GeminiDB  │
     │  Audit log │  │  Audit log  │ │  State     │
     └────────────┘  └────────────┘  └────────────┘
                             │
                    ┌────────▼────────────┐
                    │  DLI                │
                    │  SQL Query          │
                    └─────────────────────┘
```

## Highlights

- **Fully Serverless**: No server management; FunctionGraph scales per invocation
- **Auto-Discovery**: Daily scan identifies newly audit-enabled database instances
- **Checkpoint Resume**: GeminiDB tracks collection state; Worker resumes from last checkpoint
- **Elastic Fan-out**: Dispatcher concurrently invokes Workers for multi-instance collection
- **SQL Query**: DLI queries audit logs on OBS directly, no additional ETL needed
- **gzip Compression**: Audit logs compressed before archiving to save storage

## Cloud Services Used

| Service | Purpose |
|---------|---------|
| FunctionGraph | Serverless runtime (Worker / Dispatcher / Discovery) |
| OBS | Audit log archive + cluster config + function code packages |
| GeminiDB | DynamoDB-compatible interface for collection state |
| RDS | Audited MySQL instances (user-existing, not created by this solution) |
| DLI | Data Lake Insight for SQL queries on OBS audit logs |
| IAM | Agency authorization for FunctionGraph |
| VPC | Network for GeminiDB instance |

Cleanup: Run `terraform destroy` to release all resources created by this solution.

## Prerequisites

- Huawei Cloud account with FunctionGraph / OBS / GeminiDB / DLI / RDS enabled
- Terraform >= 1.5 installed
- hcloud CLI installed (built into FunctionGraph runtime; install locally for debugging)
- Access keys configured: `export HW_ACCESS_KEY="..."` and `export HW_SECRET_KEY="..."`
- Huawei Cloud account domain name (for IAM agency)
- Target RDS MySQL instances with audit logging parameter enabled

## Quick Start

```bash
git clone <repo-url>
cd cloud-native-security-db-audit-log-serverless

export HW_ACCESS_KEY="your-access-key"
export HW_SECRET_KEY="your-secret-key"

# Update domain_name variable in infra/main.tf
cd infra
terraform init
terraform plan
terraform apply

# Create DLI tables (run scripts/create_dli_tables.sql in DLI console)
```

## Step-by-Step Deployment

### Step 1: Initialize Terraform

```bash
cd infra
terraform init
```

### Step 2: Review the plan

```bash
terraform plan
```

### Step 3: Deploy

```bash
terraform apply
```

### Step 4: Upload function code packages

```bash
cd src/functions
zip worker.zip worker_lambda.py cluster_config.py
zip dispatcher.zip dispatcher_lambda.py cluster_config.py
zip discovery.zip discovery_lambda.py cluster_config.py

obsutil cp worker.zip obs://<code-bucket>/function-code/worker.zip
obsutil cp dispatcher.zip obs://<code-bucket>/function-code/dispatcher.zip
obsutil cp discovery.zip obs://<code-bucket>/function-code/discovery.zip
```

### Step 5: Create DLI query tables

Execute `scripts/create_dli_tables.sql` in the DLI console SQL editor, replacing `<OBS_BUCKET_NAME>` with the bucket name from step 3 output.

## Usage

### Verify Discovery Function

Manually trigger `db-audit-log-serverless-discovery` in the FunctionGraph console:

```json
{"discovered": 2, "manual": 0, "total": 2}
```

### Verify Dispatcher + Worker

Manually trigger `db-audit-log-serverless-dispatcher`:

```json
{"total_clusters": 2, "invoked": 2, "failed": 0}
```

### Query Audit Logs

Execute SQL in the DLI console:

```sql
SELECT * FROM audit_log_db.audit_logs
ORDER BY `timestamp` DESC
LIMIT 10;

SELECT * FROM audit_log_db.audit_logs
WHERE operation = 'DELETE' AND username = 'admin'
ORDER BY `timestamp` DESC;
```

## Cleanup

```bash
cd infra
terraform destroy
```

## Configuration

| Parameter | Location | Default | Description |
|-----------|----------|---------|-------------|
| `dispatcher_schedule` | infra/main.tf | `0 */5 * * * ?` | Dispatcher trigger frequency |
| `discovery_schedule` | infra/main.tf | `0 0 2 * * ?` | Discovery trigger time |
| `enable_gzip` | infra/main.tf | `true` | Audit log compression |
| `worker_memory` | infra/main.tf | `512` | Worker memory (MB) |
| `worker_timeout` | infra/main.tf | `300` | Worker timeout (s) |

## FAQ

**Q: Why GeminiDB instead of RDS for state storage?**
A: GeminiDB provides a DynamoDB-compatible interface, making it the natural choice for state tracking without rewriting data access logic.

**Q: Why DLI instead of GaussDB(DWS)?**
A: DLI is a serverless SQL query engine that queries files on OBS directly, requiring no database instance maintenance.

**Q: How to add manually managed instances?**
A: Add an entry to `cluster_config.json` on OBS with `auto_discovered: false`. The Discovery function will not overwrite it.

## Dependencies

- Terraform >= 1.5
- huaweicloud Provider ~> 1.70
- Python 3.9 (FunctionGraph runtime)
- esdk-obs-python (OBS SDK, built into runtime)
- boto3 (GeminiDB DynamoDB-compatible interface)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project uses the MIT No Attribution license. See [LICENSE](LICENSE).

## Contact

- Maintainer: `<github-username>`
- Issues: [GitHub Issues](https://github.com/<github-username>/cloud-native-security-db-audit-log-serverless/issues)
