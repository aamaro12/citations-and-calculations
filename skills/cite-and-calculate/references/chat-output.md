# Direct chat output

`audit` returns JSON with a `chat` object:

| Field | Purpose |
|---|---|
| `answer_markdown` | Checked answer for the assistant's final chat message. Markdown is its formatting, not a requirement to open a file. |
| `citations` | Source IDs, exact locators and excerpts, coverage, original URLs when supplied, and clickable snapshot targets. |
| `facts` | Checked input displays and links, with reported/assumption labels and source citation IDs. |
| `calculations` | Recomputed result displays and links, input references and transitive assumptions. |
| `audit_sha256`, `audit_target` | Content hash and local target for this audit. |
| `status`, `boundary`, `delivery` | Check outcome, what was checked, and how to preserve it in delivery. |

`answer` accepts the same workspace, bundle and output arguments. It re-audits and prints `chat.answer_markdown` directly to stdout. Both commands refuse to use cached results as verification. A blocked result stays a draft with failed claims and issues visible, and returns exit code 2.

## Link behaviour

Local Codex targets are absolute file paths with a one-based line suffix. Angle brackets preserve spaces in those targets. Source pages lead with the exact quote, followed by a readable location, relevant financial labels and any coverage limitation. Links open the original source, saved copy or audit details. Timestamps, hashes, complete extracted units, raw context and provenance remain in the audit record, rather than crowding the page.

Input pages show the value, company, period and source; assumptions show their reason instead. Calculation pages lead with the result, then show the formula, linked inputs and any comparison or assumption needed to interpret it. Precision, raw scale and verifier internals remain behind the audit link. Chained calculation inputs link to their own pages.

All notes, source snapshots and audit data live in `chat/a_<audit-sha256>/` within the chosen output folder. Each answer points to its own audit; a subsequent audit does not retarget those links. Do not delete an audit directory while answers still depend on it. If the whole folder moves, run a fresh audit to generate links for its new location. The existing HTML and Markdown exports remain available for portable review.

When the user requests a presentation refresh of existing pages, `scripts/refresh_notes.py <runs-folder>` applies the current layout to saved source, input and calculation notes. It validates audit/source hashes and exact excerpts, backs up every changed page, and preserves audit data and original source bytes. This is a layout migration, not a fresh verification verdict.

The package emits ordinary Markdown links. It does not register built-in web-search citation IDs, add hover UI to Codex messages, install itself, or intercept unrelated tool calls. A future MCP adapter could return this structured object; no MCP server is included in this version.

The skill tells Codex to display the compiled answer without changing its checked figures or links. This instruction does not technically enforce every later assistant message. Prose outside the compiled answer and semantic support judgements remain outside deterministic verification.
