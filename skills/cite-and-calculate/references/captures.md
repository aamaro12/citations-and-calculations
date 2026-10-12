# Capturing host tools

Codex keeps control of connector discovery, permissions and calls. This package records evidence after the call; it neither opens Gmail/Drive connections nor shares their credentials. It does not automatically intercept tool calls.

Preserve the returned result using a filesystem-capable orchestration tool or the connector's export operation. Save the actual return value without reconstructing its prose or numerical values. Then write this envelope around it:

```json
{
  "title": "Descriptive source title",
  "tool_name": "<actual exposed tool name>",
  "tool_args": {"query": "<actual evidence query; omit credentials>"},
  "tool_call_id": "<actual call ID when available>",
  "retrieved_at": "2026-10-12T12:00:00Z",
  "source_url": "https://example.org/original-source",
  "completeness": "partial",
  "next_cursor": "<actual remaining-page cursor when present>",
  "tool_result": {"<actual structured or text result>": "<unmodified returned value>"}
}
```

Placeholders describe the protocol, not a runnable connector. `source_url`, `retrieved_at` and `tool_call_id` are optional; do not invent them. Completeness defaults to `unknown`. Recognised pagination/truncation markers anywhere in the structured envelope force `partial`. A source title can identify a search snippet, OCR response or scoped export. `tool_args` are provenance metadata and are never citable evidence; only `tool_result` receives citation locators.

Never capture environment files, authentication headers or credentials. The recorder refuses credential fields and credential-bearing source URLs. Preserve only the evidence portion where a tool response otherwise contains authentication data, and record that scoped export rather than claiming to retain the full wire response.

For Drive, Gmail, GitHub or another service, preserve stable file/message/commit IDs and source links in the response. Record pagination status, inaccessible attachments, truncated bodies and the query's scope. A response hash checks later changes to the stored response; it does not attest that the service returned it. Negative claims such as 'no other messages exist' require complete coverage, not just a successful search.

For a document-reading or OCR tool, keep page/paragraph/cell fields when they exist. The JSON locator identifies the captured field; it is not a fabricated original-document page coordinate. Use `ingest` for exported originals whenever possible so the evidence can be independently extracted too.

For web search tools, a snippet capture only supports what was visible in that snippet. Fetch the source page for important claims. The public `fetch` command follows at most 5 redirects, validates and pins public IPs for each request, refuses credential URLs, imposes a 20 MiB limit and never executes page scripts. Refused or dynamic pages need another authorised host tool, with its limitations retained.
