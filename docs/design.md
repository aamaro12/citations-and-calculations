# Design decisions

## Sources are captured once, then referenced

`Store.add` writes original bytes, deterministically extracted passages and provenance into a content-addressed source directory. The source ID hashes the metadata, which binds the raw and extraction hashes. Every read and audit checks all 3. There is no mutable source registry whose final item can silently disappear; a claim pins its required snapshot directly.

A page/line locator refers to the PDF extraction, a CSV/XLSX locator to a particular cell, and a JSON locator to a scalar field using RFC 6901 escaping. Context carries explicit labels, adjacent row values, workbook column headings and formulas. Original files are retained for inspection. Captured tool-query arguments are metadata, not evidence units.

Workbook cells are indexed as Excel displays them, using each cell's number format, and the stored value stays beside it and can be cited as well. A format that would hide a sign or every digit is not used for the indexed text. A cell's column headings are the first row's text and, for a number, the nearest labels above it, with merged headings counting for every column they cover. Workbooks are streamed a cell at a time, so one over the passage limit is refused as soon as it passes the limit, and a sheet or range can be read on its own.

A hash proves that the referenced snapshot is unchanged. It does not prove that a service returned that response, that a source is authoritative or that an extraction interpreted a table correctly.

## Calculations operate on typed facts

Facts have printed values, scale, unit, company/entity and period. The verifier checks numerical occurrence and explicit structured labels. Each support review records the remaining interpretive choices. Assumptions are a different fact kind and appear in the result's caveats.

Calculations form a dependency graph. Python evaluates a small arithmetic AST with Decimal precision 60 and dimensional checks; it never evaluates arbitrary Python. Percentages are fractions internally, while percentage-point outputs use the same base with different labelling. Inputs retain all transitive source dependencies. Mixed entities/periods require explicit comparison policies. Output precision is declared and rounding is deterministic.

The verifier can recompute arithmetic without agreeing with the model. An economically unsuitable formula or an unjustified scenario still requires substantive review. An external script's tool-captured output is traceable, but not automatically replay-verified.

## Answers are compiled from claims

Claims distinguish facts, calculations, inferences and assumptions. Source passages and declared support reviews accompany each claim; figure markers are compiled from verified inputs and calculations. Bare digits in claim prose fail the answer check. This restriction is intentional for the first version: it avoids guessing which parts of free-form prose are numbers needing proof.

Audit status is computed afresh. A stored `status`, verdict or result is never a positive control input. Failed claims stay in the report and the generated answer is labelled as a draft; they are not silently dropped. Audits are saved under content-addressed filenames alongside the latest report.

## Codex integration is a skill plus deterministic code

The package uses Codex's existing shell, web, upload and connector tools. It supplies no universal connector bridge, credentials, LLM server or automatic hook. This keeps it usable with the user's existing session and permissions. The skill teaches acquisition, exact-response capture, review and final audit; the script executes the checks. The workflow checks only the bundle and generated answer, not every ordinary Codex message.

The portable plugin manifest and compatibility overlay share an identity and version. No global installation is performed by the repository. A distribution archive contains source, the skill, the scripts and synthetic fixtures, with private runs and environments excluded.

## Chat delivery is compiled after a fresh audit

`audit_file` returns a structured chat payload containing the compiled answer, citation map and fact/calculation targets. The `answer` command prints that text directly; it performs the same fresh verification rather than loading a stored verdict. The skill delivers the compiled answer in chat and keeps Markdown/HTML exports optional. Skill instructions guide the assistant but do not enforce an arbitrary later paraphrase. `check-answer` narrows that gap: it compares the message about to be sent with a fresh audit of the same bundle, requires each figure to keep its link and label and each source link to stay, and refuses numbers the audit did not check. Prose without figures is not checked.

Source and calculation links resolve to small local notes under a content-addressed audit directory. The notes retain original snapshots, exact excerpts, context, units, formula dependencies and assumptions. Exported source hashes are rechecked before chat rendering. Changed existing notes are refused, and later audits create separate targets. Absolute local links support Codex's file panels; relocating the folder requires a fresh audit. No native citation IDs, custom hover interface, MCP server or global hook are claimed.

Citation pages lead with the quote or value and retain only the context needed to read it. Audit metadata, complete extracted units and verifier details stay behind an audit link. `scripts/refresh_notes.py` can restyle existing source, input and calculation pages without retargeting their audit links. It validates the saved evidence, backs up the old pages and changes no audit or source data; it does not create a fresh positive verdict.
