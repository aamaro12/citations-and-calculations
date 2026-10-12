# Bundle format, version 1

A bundle is JSON. Use decimal strings for numerical values. IDs start with a letter and contain letters, digits, `_` or `-`. Source IDs come from the recorder, never from the model.

```json
{
  "schema_version": 1,
  "facts": [
    {
      "id": "revenue_current",
      "kind": "reported",
      "label": "Revenue",
      "value": "1250",
      "unit": "USD",
      "scale": "1000000",
      "places": 0,
      "entity": "Northstar",
      "period": "FY2025",
      "citations": [{"source_id": "<recorder-returned-id>", "locator": "cell:R3C3", "excerpt": "1250"}],
      "review": {"verdict": "supports", "reviewer": "codex", "rationale": "The source row identifies Revenue, FY2025, USD and a scale of one million."}
    }
  ],
  "calculations": [],
  "claims": [
    {
      "id": "revenue_claim",
      "kind": "fact",
      "text": "Revenue was {{fact:revenue_current}}.",
      "review": {"verdict": "supports", "reviewer": "codex", "rationale": "The reported input supports this statement for the declared company and period."}
    }
  ]
}
```

The placeholder ID above is explanatory: replace it with an actual source ID before running. The CLI demo writes a complete working bundle with actual snapshot IDs.

## Facts

`kind` is `reported` or `assumption`. Reported facts need one or more citations, each with an exact locator and excerpt. The printed numerical value and sign must occur in every excerpt. Explicit CSV/JSON labels for unit, scale, period, company and metric must match. Workbook fiscal-year column headers are checked when unambiguous. Unit interpretations in prose and PDFs are reviewed, not inferred automatically.

Assumptions need `reason`; they have no fabricated source citation. All numerical inputs require `label`, `entity`, `period`, `unit` and `value`. Units are `%`, `pp`, `1` or named quantities such as `USD`, `EUR`, `shares`, `customers`, or `USD/shares`. Scale defaults to `1`. `%` and `pp` values are divided by 100 internally. A currency in millions uses `unit: USD`, `scale: 1000000`; cents use `scale: 0.01`. Numerical facts use English digit and thousands formats. JSON numeric scalars are parsed as Decimal without binary-float rounding. Written-out numbers, locale-specific decimal commas and scientific notation in source excerpts are not supported.

## Calculations

```json
{
  "id": "growth",
  "formula": "current / prior - 1",
  "inputs": {"current": "revenue_current", "prior": "revenue_prior"},
  "unit": "%",
  "places": 1,
  "period_policy": "compare",
  "comparison_reason": "Compare the same company's revenue in consecutive fiscal years."
}
```

Inputs can reference facts or earlier/later calculations; cycles are refused. Permitted formulas use `+ - * / **`, unary signs and `sum(a,b)`, `mean(a,b)`, `min(a,b)`, `max(a,b)`, `abs(a)`. They cannot execute Python. Every input must be used. Constants other than `0`, `1` and `100` must be declared assumptions. Exponents are bounded integers. Decimal arithmetic uses precision 60; display precision is 0 to 8 places. Units must be compatible and the output unit must follow the formula. Output scale defaults to 1. To compare different entities, specify `entity_policy: compare` and a `comparison_reason`; the same applies to mixed periods.

Optional `reported_result` checks an existing draft's displayed figure against the recomputed rounded result; omit it when writing a new answer. No previous result or verdict is trusted. Formula choice and comparison rationale remain analytical judgements.

For complex Python analysis, use the host's code tools on captured data, then record its exact results as a tool capture with the code and input snapshots. That records provenance, not independent replay: express the key final calculations in this bounded formula graph when possible, and disclose any calculation that was only captured rather than recomputed.

## Claims

`kind` is `fact`, `calculated`, `inference` or `assumption`. Use `{{fact:id}}` and `{{calc:id}}` wherever a figure appears. Bare digits are refused, including dates or version numbers in claim prose; put known periods/entities in the input context or source-backed figure markers. Unknown markers and invalid inputs block the claim.

Reported claims cannot call assumptions facts. Calculated claims must reference a recomputed calculation. Inferences need a support review and cited premises or typed inputs, but retain their inference label. Assumption claims need a reason. Other claims and reported facts need a review with verdict `supports`, `contradicts` or `unclear`, reviewer `codex` or `human`, and rationale. Only `supports` passes; the package checks the review is present, not whether the reviewer is right.

Optional top-level `as_of` is an ISO timestamp with a timezone. A source whose declared publication is later is refused. Undated sources remain explicitly unproven against that cutoff. Publication dates supplied at ingestion are caller declarations, not automatically authenticated dates.
