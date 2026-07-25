---
name: bondradar-ratio-methodology
description: Review BondRadar financial metric lineage, ratio formulas, period alignment, units, and methodology gates. Do not use for investment recommendations or trading.
---

# BondRadar Ratio Methodology Skill

## Purpose

Review whether ratios can be calculated from controlled and compatible inputs.

## Check

```txt
metric lineage
statement standard
period alignment
unit alignment
scale normalization
numerator definition
denominator definition
sign convention
missing values
division-by-zero behavior
issuer comparability
```

## Block ratio if

```txt
source-backed inputs are missing
periods differ
standards are mixed without methodology
units are unclear
metric definition is ambiguous
required methodology action remains open
```

Do not infer scoring readiness from ratio availability.
