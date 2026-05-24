# BondRadar Issuer Identity Enrichment

Issuer identity cleanup is the review step before collecting real issuer
financial reports. It turns generated names such as `Unknown issuer for
RU000...` into reviewed legal issuer metadata without guessing silently.

This workflow does not scrape external websites, merge companies, change bonds,
activate schedules, or run paper execution.

## Why Identity Comes First

Corporate bonds may involve several entities:

- legal issuer;
- finance subsidiary;
- SPV;
- operating company;
- parent group;
- guarantor.

These are not automatically the same. BondRadar stores legal issuer identity
separately from issuer group/parent identity. Financial reports should be linked
to the legal issuer only after operator review, unless a later explicit mapping
task says otherwise.

## Safe Workflow

```text
diagnostics -> target export -> manual review -> preview -> apply -> coverage recheck
```

Local path policy:

```text
docs/examples/issuer_identity/     = synthetic examples safe for git
data/issuer_identity/private/      = real operator data, ignored by git
data/issuer_identity/staging/      = in-progress review files, ignored by git
data/issuer_identity/reviewed/     = reviewed real data, ignored by git
logs/issuer_identity/              = generated reports, ignored by git
```

Do not commit real reviewed identity CSV files, generated VDS/local logs, smoke
test reports, or private operator files.

Start with diagnostics:

```bash
curl -s "http://127.0.0.1:8000/api/companies/identity/diagnostics?active_only=true&limit=20" \
  -o logs/issuer_identity/diagnostics.json
```

Export cleanup targets:

```bash
python scripts/issuer_identity_target_export.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 20 \
  --json-output logs/issuer_identity/targets.json \
  --csv-output logs/issuer_identity/targets.csv \
  --markdown-output logs/issuer_identity/targets.md
```

Fill the identity template from official issuer evidence:

```text
docs/examples/issuer_identity/issuer_identity_template.csv
docs/examples/issuer_identity/issuer_identity_template.json
```

Batch rehearsal can generate a target list and review template before any
reviewed file exists:

```bash
python scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 20 \
  --review-template-output logs/issuer_identity/priority_identity_review_template_task81.csv \
  --json-output logs/issuer_identity/batch_rehearsal_task81.json \
  --markdown-output logs/issuer_identity/batch_rehearsal_task81.md
```

Expected behavior:

- diagnostics before is fetched;
- targets are exported;
- review template is generated;
- preview/apply is not attempted without reviewed input;
- report status is `passed` or `warning`;
- next steps explain that the operator must fill the reviewed CSV manually.

Dry-run and preview:

```bash
python scripts/issuer_identity_import.py \
  --input data/issuer_identity/staging/issuer_identity_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/issuer_identity/import_preview.json \
  --markdown-output logs/issuer_identity/import_preview.md
```

Confirmed apply is non-default:

```bash
python scripts/issuer_identity_import.py \
  --input data/issuer_identity/reviewed/issuer_identity_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --execute yes \
  --confirm-apply yes \
  --json-output logs/issuer_identity/import_apply.json \
  --markdown-output logs/issuer_identity/import_apply.md
```

Batch preview without apply:

```bash
python scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/issuer_identity/private/priority_identity_review.csv \
  --format csv \
  --execute-apply no \
  --json-output logs/issuer_identity/batch_preview_task81.json \
  --markdown-output logs/issuer_identity/batch_preview_task81.md
```

Confirmed batch apply requires both explicit flags:

```bash
python scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/issuer_identity/reviewed/priority_identity_review.csv \
  --format csv \
  --execute-apply yes \
  --confirm-apply yes \
  --json-output logs/issuer_identity/batch_apply_task81.json \
  --markdown-output logs/issuer_identity/batch_apply_task81.md
```

Create a PostgreSQL backup before confirmed apply. The script does not perform
automatic rollback.

## MOEX Metadata Preview

MOEX ISS metadata can sometimes provide issuer name or INN for a bond secid.
Use it only as a preview source:

```bash
python scripts/issuer_identity_moex_enrich.py \
  --backend-url http://127.0.0.1:8000 \
  --limit 20 \
  --json-output logs/issuer_identity/moex_preview.json \
  --markdown-output logs/issuer_identity/moex_preview.md
```

This script does not apply changes unless `--execute yes --confirm-apply yes`
is provided.

## Conflict Policy

Preview reports hard conflicts, including:

- same INN linked to another company;
- same OGRN linked to another company;
- incoming INN/OGRN differs from a verified profile;
- incoming legal name differs from a verified profile.

Apply blocks hard conflicts by default. `--allow-conflicts` records conflict
status and notes, but still does not merge companies.

## Duplicate / Same Issuer Review

Duplicate company rows happen when the bond universe contains weak issuer
metadata and BondRadar creates generated placeholders such as `Unknown issuer
for RU000...`. A later reviewed identity can reveal that several company rows
probably refer to the same legal issuer or the same issuer group.

Duplicate review does not merge companies. It only records a reviewed candidate
relationship in `company_identity_duplicate_candidates`. Actual merge or bond
consolidation is a separate future task.

Keep these cases separate during review:

- duplicate legal issuer;
- same issuer group;
- SPV or finance subsidiary;
- parent group or guarantor.

Safe workflow:

```text
diagnostics -> duplicate export -> manual review -> preview -> confirmed apply
```

Duplicate diagnostics:

```bash
curl -sS "http://127.0.0.1:8000/api/companies/identity/duplicates/diagnostics?active_only=true&limit=50&min_score=0.50" \
  -o logs/issuer_identity/duplicate_diagnostics.json
```

Export duplicate candidates:

```bash
python scripts/issuer_identity_duplicate_export.py \
  --backend-url http://127.0.0.1:8000 \
  --limit 50 \
  --min-score 0.50 \
  --json-output logs/issuer_identity/duplicate_candidates.json \
  --csv-output logs/issuer_identity/duplicate_candidates.csv \
  --markdown-output logs/issuer_identity/duplicate_candidates.md
```

Use `--exclude-accepted` when you want only unresolved candidates. The default
export keeps accepted/reviewed rows visible so the operator can see the current
duplicate map.

Preview reviewed duplicate decisions:

```bash
python scripts/issuer_identity_duplicate_review.py \
  --input data/issuer_identity/private/duplicate_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/issuer_identity/duplicate_preview.json \
  --markdown-output logs/issuer_identity/duplicate_preview.md
```

Confirmed apply records only the non-destructive review decision:

```bash
python scripts/issuer_identity_duplicate_review.py \
  --input data/issuer_identity/private/duplicate_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --execute-apply yes \
  --confirm-apply yes \
  --json-output logs/issuer_identity/duplicate_apply.json \
  --markdown-output logs/issuer_identity/duplicate_apply.md
```

The duplicate workflow never moves bonds, deletes companies, overwrites verified
identity, or applies legal-name guesses automatically.

## Using Duplicate Mapping In Target Exports

Accepted/reviewed duplicate decisions can be used as a read-only canonical
issuer layer for operator reports. This does not merge companies and does not
move bonds; it only rolls duplicate rows up in generated CSV/JSON/Markdown.

Identity targets with canonical rollup:

```bash
python scripts/issuer_identity_target_export.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --compare-rollup \
  --json-output logs/issuer_identity/canonical_identity_targets_task83.json \
  --csv-output logs/issuer_identity/canonical_identity_targets_task83.csv \
  --markdown-output logs/issuer_identity/canonical_identity_targets_task83.md
```

Use this before applying more identity rows so reviewed duplicate candidates are
not handled as separate legal issuers by mistake.

## Uncertain Cases

If evidence is unclear:

- leave identity as `weak` or `unknown`;
- do not fake INN, OGRN, or group identity;
- add review notes;
- collect more source evidence before importing financial reports.

Identity cleanup is an operator review aid. It is not a credit-risk override and
does not change virtual paper schedules.

## Manual Review Quality Checklist

For each reviewed row:

- Did I identify the legal issuer, not just the brand?
- Did I verify INN/OGRN from a reliable source?
- Did I record `source_url` or `source_file_name`?
- Did I avoid using bond short name as legal name?
- If issuer is SPV/subsidiary, did I mark `issuer_role` correctly?
- If parent/group differs from legal issuer, did I fill `issuer_group_name`
  separately?
- If uncertain, did I leave `identity_status` weak/unknown instead of guessing?

## Identity-First Financial Collection Queue

After the financial collection priority queue is generated, run the
identity-first queue before collecting official issuer reports. The priority
queue ranks how valuable a report would be; the identity-first queue decides
whether the issuer identity is strong enough to start financial collection.

```text
Known corporate issuers go to collection_ready.
Unknown issuer rows go to identity_review_required.
Government-like issuers remain excluded/deprioritized.
Already covered issuers like TMK stay in already_covered with missing fields.
```

This step is planning/export only. It does not import identity profiles, apply
duplicate mappings, import financial reports, mutate scores, update
predictions, activate schedules, or call paper-trading endpoints.

Run on VDS:

```bash
python3 scripts/identity_first_collection_queue.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --include-covered \
  --exclude-government-like \
  --json-output logs/financial_reports/identity_first_collection_queue_task94_vds.json \
  --markdown-output logs/financial_reports/identity_first_collection_queue_task94_vds.md \
  --identity-review-csv-output logs/financial_reports/identity_review_required_task94_vds.csv \
  --collection-ready-csv-output logs/financial_reports/collection_ready_task94_vds.csv
```

Review `identity_review_required_task94_vds.csv` first. Rows named
`Unknown issuer for ...`, rows with low classification confidence, weak
identity status, missing legal name, or missing INN/OGRN must go through the
normal identity review workflow before any financial report collection. Use
`collection_ready_task94_vds.csv` for issuers that are known corporate issuers
and ready for official-source report collection.

## Official-Source Collection Pack for Collection-Ready Issuers

After identity-first review, use the official-source collection pack to create
financial collection templates only for `collection_ready` issuers. This keeps
unknown issuers in identity review and prevents report collection for the wrong
legal entity.

```text
Identity-first queue decides who is safe to collect.
Official-source collection pack creates templates/checklists for those issuers.
No financial values are invented.
Real financial values must come from official issuer report/disclosure/auditor report.
Preview must pass before any future import.
```

Run on VDS:

```bash
python3 scripts/financial_official_collection_pack.py \
  --mode template \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --include-covered \
  --exclude-government-like \
  --period-year 2025 \
  --period-quarter 0 \
  --report-type annual \
  --currency RUB \
  --accounting-standard IFRS \
  --consolidation-scope consolidated \
  --value-scale million \
  --max-issuers 2 \
  --financial-template-output data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --evidence-template-output data/financial_reports/private/official_source_evidence_template_task95.json \
  --source-checklist-output logs/financial_reports/official_source_checklist_task95.csv \
  --json-output logs/financial_reports/official_collection_pack_task95.json \
  --markdown-output logs/financial_reports/official_collection_pack_task95.md
```

The generated files are operator artifacts. Keep filled financial values in
ignored private/log paths and run preview mode before any future import task.
Future live BondRadar cycles may refresh market/risk/paper state every 1-2
hours while the exchange is open, but this workflow does not implement or
activate that scheduler.

## Official-Source Evidence Assistant

Once issuers are `collection_ready`, use the official-source evidence assistant
to separate source discovery and evidence-backed value entry from any future
import. The assistant helps operators keep the chain explicit:

```text
known issuer identity
official source candidate
field evidence with page/table/note
candidate file
preview-only validation
separate controlled import later
```

Unknown issuer rows must stay in identity review. Do not collect financial
values for them until the legal issuer, INN/OGRN, and official source are
confirmed.

Create source intake from the Task 95 financial template and checklist:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-template \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --evidence-template-input data/financial_reports/private/official_source_evidence_template_task95.json \
  --source-checklist-input logs/financial_reports/official_source_checklist_task95.csv \
  --source-intake-output data/financial_reports/private/official_source_intake_task96.json \
  --json-output logs/financial_reports/official_source_intake_task96.json \
  --markdown-output logs/financial_reports/official_source_intake_task96.md
```

Validate source URLs before entering values:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-validate \
  --source-intake-input data/financial_reports/private/official_source_intake_task96.json \
  --json-output logs/financial_reports/official_source_validation_task96.json \
  --markdown-output logs/financial_reports/official_source_validation_task96.md
```

Fill a candidate file from manual evidence only:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode candidate-fill \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --source-intake-input data/financial_reports/private/official_source_intake_task96.json \
  --manual-values-json data/financial_reports/private/manual_values_task96.json \
  --candidate-output data/financial_reports/private/collection_ready_financial_candidate_task96.csv \
  --candidate-format csv \
  --evidence-output logs/financial_reports/official_source_evidence_task96.json \
  --json-output logs/financial_reports/official_source_candidate_fill_task96.json \
  --markdown-output logs/financial_reports/official_source_candidate_fill_task96.md
```

Preview without import:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --candidate-input data/financial_reports/private/collection_ready_financial_candidate_task96.csv \
  --format csv \
  --json-output logs/financial_reports/official_source_candidate_preview_task96.json \
  --markdown-output logs/financial_reports/official_source_candidate_preview_task96.md
```

The assistant blocks Wikipedia/wiki, blogs, forums, social media, random
aggregator sources, market capitalization as equity, coupon payments as
interest expense, and suspicious placeholder-zero rows. It does not import or
apply financial reports or identity profiles.

## VDS Smoke Checklist

Health:

```bash
curl -i http://127.0.0.1:8000/api/health
```

Diagnostics:

```bash
curl -sS "http://127.0.0.1:8000/api/companies/identity/diagnostics?active_only=true&limit=20" \
  -o logs/issuer_identity/diagnostics_task81_vds.json
```

Batch rehearsal target/template generation only:

```bash
python3 scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 20 \
  --review-template-output logs/issuer_identity/priority_identity_review_template_task81_vds.csv \
  --json-output logs/issuer_identity/batch_rehearsal_task81_vds.json \
  --markdown-output logs/issuer_identity/batch_rehearsal_task81_vds.md
```

Confirm no schedule was activated:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select
  id,
  name,
  status,
  use_current_date_as_of_date,
  next_run_at,
  last_run_at,
  last_cycle_run_id,
  run_count
from paper_live_schedules
order by id desc
limit 5;
"
```

Expected:

```text
/api/health = 200 OK
identity diagnostics returns JSON
batch rehearsal returns warning/passed, not failed
review template file is generated
paper schedule remains paused
identity apply is not executed
```

## VDS Duplicate Smoke Checklist

Health:

```bash
curl -i http://127.0.0.1:8000/api/health
```

Duplicate diagnostics:

```bash
curl -sS "http://127.0.0.1:8000/api/companies/identity/duplicates/diagnostics?active_only=true&limit=20&min_score=0.50" \
  -o logs/issuer_identity/duplicates_diagnostics_task82_vds.json
```

Export duplicate candidates:

```bash
python3 scripts/issuer_identity_duplicate_export.py \
  --backend-url http://127.0.0.1:8000 \
  --limit 20 \
  --min-score 0.50 \
  --json-output logs/issuer_identity/duplicate_candidates_task82_vds.json \
  --csv-output logs/issuer_identity/duplicate_candidates_task82_vds.csv \
  --markdown-output logs/issuer_identity/duplicate_candidates_task82_vds.md
```

Confirm the schedule remains paused:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select id, name, status, use_current_date_as_of_date, next_run_at, last_run_at, last_cycle_run_id, run_count
from paper_live_schedules
order by id desc
limit 5;
"
```

Expected:

```text
health = 200 OK
duplicate diagnostics returns JSON
duplicate export writes reports
no apply executed
schedule remains paused
```

## VDS Canonical Rollup Smoke Checklist

Health:

```bash
curl -i http://127.0.0.1:8000/api/health
```

Identity targets with duplicate rollup:

```bash
python3 scripts/issuer_identity_target_export.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --compare-rollup \
  --json-output logs/issuer_identity/canonical_identity_targets_task83_vds.json \
  --csv-output logs/issuer_identity/canonical_identity_targets_task83_vds.csv \
  --markdown-output logs/issuer_identity/canonical_identity_targets_task83_vds.md
```

Schedule safety:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select id, name, status, use_current_date_as_of_date, next_run_at, last_run_at, last_cycle_run_id, run_count
from paper_live_schedules
order by id desc
limit 5;
"
```

Expected:

```text
health = 200 OK
canonical identity target export is written
accepted duplicate members appear under canonical issuers
no apply executed
schedule remains paused
```
