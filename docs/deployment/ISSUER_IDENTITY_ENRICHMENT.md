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

Dry-run and preview:

```bash
python scripts/issuer_identity_import.py \
  --input data/financial_reports/staging/issuer_identity_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/issuer_identity/import_preview.json \
  --markdown-output logs/issuer_identity/import_preview.md
```

Confirmed apply is non-default:

```bash
python scripts/issuer_identity_import.py \
  --input data/financial_reports/staging/issuer_identity_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --execute yes \
  --confirm-apply yes \
  --json-output logs/issuer_identity/import_apply.json \
  --markdown-output logs/issuer_identity/import_apply.md
```

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

## Uncertain Cases

If evidence is unclear:

- leave identity as `weak` or `unknown`;
- do not fake INN, OGRN, or group identity;
- add review notes;
- collect more source evidence before importing financial reports.

Identity cleanup is an operator review aid. It is not a credit-risk override and
does not change virtual paper schedules.
