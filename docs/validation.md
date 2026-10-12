# Validation

Checks recorded on 12 October 2026.

## Version 0.3.0

| Check | Result |
|---|---|
| Tests | 79 offline tests pass on Python 3.11 and 3.12 with the PDF extra installed. They cover text and blank PDFs, encrypted PDFs, corrupt Word and Excel files, text that is not UTF-8, hidden web page text, chat delivery and Markdown escaping. Every HTTP call is mocked. |
| Safeguards | `check_guards.py` removes 25 selected safeguards, one at a time, in isolated copies, and a test catches each removal. An unmodified copy must pass first. This covers chosen safeguards, not every possible fault. |
| Demo | 4 synthetic claims combine a CSV file, an HTML page and a captured tool response. Growth recomputes to 25.0% and margin to 20.0%, with no blocked claim. |
| Report | `check_report.py` confirms that the interactive report's links and anchors resolve, and its search, filters, disclosures and print button pass a mocked DOM test. It was not checked in a browser. |
| Package | The wheel builds, and a clean install with no dependencies runs the demo and `answer` outside the repository. No credentials, personal paths, runs or environments are in the published files. |

## Live checks, version 0.2.2

| Check | Result |
|---|---|
| Web fetch | A public OpenAI documentation page was fetched with certificate checks on, saved and split into 1,007 passages. |
| Connector capture | A GitHub connector's response for a public repository file was saved as evidence and audited, with its coverage marked partial. No connector credentials were read or stored. |
| Live answer | 2 claims from the web page and the connector capture pass their checks, with their caveats kept. This does not test the judgement in the support reviews. |
| Chat links | The source, fact and calculation links in the printed answer all resolve, including paths with spaces. Clicking through inside the Codex app was not tested. |
| Plugin | Both manifests, the files they reference and the skill's frontmatter validate. No global installation was attempted. |

These checks cover the recorded evidence and the arithmetic. They do not prove that a source is authentic, complete or correctly dated, that it means what a claim says, or that a formula is the right one.
