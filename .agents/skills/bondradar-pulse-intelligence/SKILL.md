---
name: bondradar-pulse-intelligence
description: Classify T-Investments Pulse posts about corporate bonds, issuers, coupon events, risks, sentiment, or official-source links. Use for intelligence and claim preparation; never use Pulse alone as source-backed financial evidence.
---

# BondRadar Pulse Intelligence Skill

## Purpose

Convert a Pulse post into structured, non-authoritative intelligence.

## Required classification

Classify each post by:

```txt
issuer
instrument
ISIN when present
published_at
author
claim_domain
claim_type
event_type
sentiment
recommendation_detected
official_source_link_present
equity_or_bond_context
verification_required
```

Allowed `claim_domain` values:

```txt
financial_fact_claim
credit_risk_claim
corporate_event
coupon_or_redemption_event
bond_market_observation
equity_valuation
recommendation
sentiment
risk_hypothesis
rumor
official_source_locator
```

## Trust rules

Pulse never directly becomes primary evidence.

A post with an official link may create:

```txt
official_source_candidate
event_candidate
claim_verification_task
```

A post without an official link may create:

```txt
sentiment_signal
risk_hypothesis
attention_signal
unverified_claim
```

## Bond/equity separation

Always distinguish:

```txt
share valuation
dividend expectations
equity upside
credit quality
bond repayment capacity
coupon event
default risk
```

Do not use equity valuation conclusions as bond-credit conclusions.

## Forbidden behavior

Do not:

```txt
write controlled financial values
claim audited values
change issuer scoring
generate a recommendation
rank issuers
rank bonds
trade
call broker APIs
download external documents
verify URLs unless a future task explicitly permits it
```

## Output format

```txt
Pulse classification:
Primary claims:
Official-source candidates:
Unverified claims:
Equity/bond context:
Risk hypotheses:
Recommendation language:
Required verification:
Permitted downstream use:
Forbidden downstream use:
```
