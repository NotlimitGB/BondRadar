# BondRadar Financial Report Import

This guide describes the controlled operator workflow for importing company
financial reports into BondRadar. It is file-based, explicit, and auditable.

It does not scrape external websites, does not run paper execution, does not
activate schedules, and does not deploy anything.

## Supported File Formats

Use CSV or JSON with these fields:

```text
company_id
company_ticker
company_inn
period_year
period_quarter
period_start_date
period_end_date
published_at
document_date
currency
source
source_url
source_file_name
report_type
revenue
ebitda
net_debt
total_debt
cash
equity
short_term_debt
operating_cash_flow
net_profit
interest_expense
debt_to_ebitda
interest_coverage
```

At least one company identifier is required:

- `company_id`;
- `company_ticker`;
- `company_inn`.

`period_quarter=0` means annual report. This follows the existing project
convention where annual reports have priority over Q4 when both are available
for the same period.

Recommended source values for operator imports:

- `operator_csv`;
- `operator_json`;
- `issuer_manual`.

Money values should be normalized to RUB unless `currency` is explicitly
different. Decimal strings are accepted. Missing values should be empty or
`null`, not fake zero values.

Use `source_url` for the original issuer or report page when available. Use
`source_file_name` for the local or original report file name when available.

Synthetic examples live in:

```text
docs/examples/financial_reports/
```

The small example files are synthetic workflow fixtures, not real issuer
financials.

## Dry-run Validation

Run local validation without calling the ingest endpoint:

```bash
python scripts/financial_report_import.py \
  --input docs/examples/financial_reports/financial_reports_example_small.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/financial_reports/import_dry_run.json \
  --markdown-output logs/financial_reports/import_dry_run.md
```

To also preview company matching through the backend without mutating the
database:

```bash
python scripts/financial_report_import.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --validate-companies \
  --json-output logs/financial_reports/import_preview.json \
  --markdown-output logs/financial_reports/import_preview.md
```

The preview endpoint is:

```text
POST /api/financial-reports/preview
```

It accepts the same payload shape as `POST /api/financial-reports/ingest` and
returns row-level company matching, likely action, warnings, and errors.

## Confirmed Import

Real import is non-default and requires explicit confirmation:

```bash
python scripts/financial_report_import.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --execute yes \
  --confirm-import yes \
  --rebuild-existing \
  --validate-companies \
  --json-output logs/financial_reports/import_run.json \
  --markdown-output logs/financial_reports/import_run.md
```

The import records:

- `financial_reports`;
- `financial_report_source_documents`;
- `financial_report_import_runs`.

Inspect import runs:

```bash
curl -s http://127.0.0.1:8000/api/financial-reports/import-runs
curl -s http://127.0.0.1:8000/api/financial-reports/source-documents
```

## Coverage Before And After

Use the rehearsal script to capture coverage before and after the import flow:

```bash
python scripts/financial_report_import_rehearsal.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --stale-after-days 540 \
  --execute no \
  --json-output logs/financial_reports/rehearsal.json \
  --markdown-output logs/financial_reports/rehearsal.md
```

For a confirmed import through the rehearsal script:

```bash
python scripts/financial_report_import_rehearsal.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --execute yes \
  --confirm-import yes \
  --json-output logs/financial_reports/rehearsal_execute.json \
  --markdown-output logs/financial_reports/rehearsal_execute.md
```

Coverage endpoint:

```bash
curl -s "http://127.0.0.1:8000/api/data-readiness/financial-reports/coverage?as_of_date=2026-05-19&active_only=true&stale_after_days=540"
```

Feature ratio coverage remains low until feature snapshots are rebuilt after
the reports are imported.

## Post-Ingest Rebuild Plan

Render the post-ingest plan:

```bash
python scripts/financial_report_post_ingest_rebuild.py \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date-from 2026-05-13 \
  --as-of-date-to 2026-05-19 \
  --dry-run \
  --json-output logs/financial_reports/post_ingest_rebuild_plan.json \
  --markdown-output logs/financial_reports/post_ingest_rebuild_plan.md
```

Recommended sequence:

```text
1. coverage before
2. ingest reports
3. rebuild company credit health snapshots
4. rebuild bond risk assessments
5. rebuild feature snapshots
6. optionally retrain model
7. optionally regenerate predictions
8. run quality gate / readiness
9. only then consider paper dry-run
```

Keep paper schedules paused until coverage, rebuild, readiness, and manual
review are complete. Risk override remains paper-only, explicit, and guarded.

## First Real Data Pack Workflow

Real issuer data should be collected by the operator from official reports and
stored outside git-tracked example files.

Use these ignored local directories:

```text
data/financial_reports/private/
data/financial_reports/staging/
```

The first data pack should be small and reviewed. Do not invent real issuer
values. Do not enter fake zeros.

### 1. Export Target Issuers

```bash
python scripts/financial_report_target_issuers.py \
  --source mixed \
  --backend-url http://127.0.0.1:8000 \
  --limit 50 \
  --json-output logs/financial_reports/target_issuers.json \
  --csv-output logs/financial_reports/target_issuers.csv \
  --markdown-output logs/financial_reports/target_issuers.md
```

Supported target sources:

- `paper-positions`;
- `top-predictions`;
- `bond-universe`;
- `mixed`.

The export is a collection target list only. It is not an issuer allowlist and
does not change risk logic.

### 2. Fill Collection CSV Manually

Start from:

```text
docs/examples/financial_reports/financial_reports_collection_template.csv
docs/examples/financial_reports/financial_reports_collection_template.json
```

Real working files should live under:

```text
data/financial_reports/staging/
data/financial_reports/private/
```

### 3. Normalize Collection File

```bash
python scripts/financial_report_collection_normalize.py \
  --input data/financial_reports/staging/company_reports_2025_collection.csv \
  --format csv \
  --output logs/financial_reports/normalized_collection.csv \
  --output-format csv \
  --json-report logs/financial_reports/normalized_collection.json \
  --markdown-report logs/financial_reports/normalized_collection.md
```

The normalizer converts `value_scale`:

```text
raw -> unchanged
thousand -> multiply by 1000
million -> multiply by 1000000
billion -> multiply by 1000000000
```

It computes `debt_to_ebitda` and `interest_coverage` only when the ratio is
missing and all required values are present.

### 4. Dry-run Import

```bash
python scripts/financial_report_import.py \
  --input logs/financial_reports/normalized_collection.csv \
  --format csv \
  --source operator_collection \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/financial_reports/import_dry_run_collection.json \
  --markdown-output logs/financial_reports/import_dry_run_collection.md
```

### 5. Preview Backend Matching

```bash
python scripts/financial_report_import.py \
  --input logs/financial_reports/normalized_collection.csv \
  --format csv \
  --source operator_collection \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --validate-companies \
  --json-output logs/financial_reports/import_preview_collection.json \
  --markdown-output logs/financial_reports/import_preview_collection.md
```

### 6. Rehearsal With Coverage

```bash
python scripts/financial_report_data_pack_rehearsal.py \
  --input data/financial_reports/staging/company_reports_2025_collection.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --normalized-output logs/financial_reports/normalized_data_pack.csv \
  --execute-import no \
  --json-output logs/financial_reports/data_pack_rehearsal.json \
  --markdown-output logs/financial_reports/data_pack_rehearsal.md
```

### 7. Confirmed Import Only After Review

```bash
python scripts/financial_report_data_pack_rehearsal.py \
  --input data/financial_reports/staging/company_reports_2025_collection.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --normalized-output logs/financial_reports/normalized_data_pack.csv \
  --execute-import yes \
  --confirm-import yes \
  --rebuild-existing \
  --json-output logs/financial_reports/data_pack_import.json \
  --markdown-output logs/financial_reports/data_pack_import.md
```

### 8. Render Post-Ingest Rebuild Plan

```bash
python scripts/financial_report_post_ingest_rebuild.py \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date-from 2026-05-13 \
  --as-of-date-to 2026-05-19 \
  --dry-run \
  --json-output logs/financial_reports/post_ingest_rebuild_plan.json \
  --markdown-output logs/financial_reports/post_ingest_rebuild_plan.md
```

Do not use the imported data in paper pilot review until downstream rebuild and
readiness checks have been reviewed.

## Source Quality Guidance

Preferred sources:

1. consolidated IFRS annual report;
2. consolidated IFRS quarterly or interim report;
3. RAS standalone report only if IFRS is unavailable;
4. management presentation only when explicitly labeled and sourced.

Record source quality fields:

```text
source_url
source_file_name
source_page
source_table
accounting_standard
consolidation_scope
currency
value_scale
```

Operator checks:

- do not mix million RUB and billion RUB without `value_scale`;
- do not enter dashes as zero;
- do not use market capitalization as equity;
- do not use coupon payments as interest expense;
- do not use revenue instead of EBITDA;
- do not use net debt if the report only provides total debt unless it is
  clearly calculated and documented.
