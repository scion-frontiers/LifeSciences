# [Finding Title]

**Role**: [role-name] | **Date**: [date]
**Task ref**: [work-order ID and revision, or orchestrator dispatch reference]

## Summary

2-3 sentence bottom line up front. State the answer to the decision question,
the confidence level, and the single most important implication.

## Key Findings

Narrative with inline references to raw data. Every quantitative claim
links to its source artifact:

> Example: "GENE_X shows pLI 0.95, indicating strong loss-of-function
> intolerance ([raw/genomics/GENE_X.gnomad-constraint.json]), but LOEUF
> 0.48 exceeds the current threshold
> ([raw/genomics/GENE_X.gnomad-constraint.analysis.json])."

Use vertical links for Layer 0 data: `[raw/<category>/<file>]`.
Use lateral links for peer findings: `[findings/<discipline>/<file>]`.

## Implications

What this means for the program direction. Reference the program state
where relevant: `[program-state/decision-log.md]`.

Flag any cross-disciplinary liabilities found. If a liability was
appended to the tracker, reference it here.

## Open Questions

Unresolved items that may require follow-up from this or another role.
Each question should name what evidence would resolve it.

## Caveats & Confidence

- Model confidence metrics and their interpretation
- Data coverage and resolution limitations
- Assumptions made and their sensitivity
- What this finding cannot speak to
