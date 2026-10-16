# Validation

## Version 0.4.0

Checks recorded on 16 October 2026.

| Check | Result |
|---|---|
| Tests | 119 offline tests pass on Python 3.11, 3.12 and 3.13, and with the oldest supported pypdf, 5.0.0. New tests cover an excerpt that cuts a number short or drops its sign, readable source links, bold and bullets, period and company markers, figures written in words, `check-answer`, scrambled PDF pages, Excel number formats and stored values, headings above cells and merged across them, the size and passage limits, and `--sheet` and `--range`. |
| Safeguards | `check_guards.py` removes 36 selected safeguards, one at a time, in isolated copies, and a test catches each removal. |
| Tampering | 31 changes to the demo's bundle or saved sources, from a flipped sign to a formula that tries to run Python, are all blocked with a stated reason, and a script written into a review shows only as text in the report. |
| Codex | Codex answered 5 tasks with the skill: from the NVIDIA 10-K workbook, from a Workday annual report PDF, checking NVIDIA's press release fetched from the web against the workbook, for a year the workbook does not cover, and from a CSV and web page with planted instructions. All 5 answers were right, the missing year was refused rather than recalled, and every delivered message passed `check-answer`. No answer spelled out a number. After a change to escaping, 2 of the tasks were run again and delivered the checked text unchanged. |
| PDFs | 957 PDFs, from the olmOCR-bench set and public filings, are read without an error. The scrambled-text check flags 158 of the 180 pages with text in NVIDIA's annual review and 1 page in each of 9 other PDFs; each of those 9 pages, checked by eye, was unreadable. |
| Workbooks | Of 374 distinct workbooks on the author's Mac, 345 are read in full, each in under 0.25 s, with the same cells as version 0.3.1. In them 49,652 cells now read as Excel shows them, and each keeps a stored value equal to what version 0.3.1 read. The other 28, which version 0.3.1 refused for size or after up to 5.6 s, are refused in at most 1.3 s with their sheet names, and all 531 of their sheets can be read with `--sheet`. 1 workbook is unreadable in both versions. A synthetic workbook of 1.5 million cells is refused in 0.3 s using 78 MB, against 17.5 s and 1.9 GB before. |
| Report | `check_report.py` passes, and the report was opened in Chrome: source context and input checks read as labelled lists, caveats name their sources, downloads keep their file names, and there were no console errors. |
| Package | The wheel builds, and a clean install with no dependencies runs `demo`, `answer` and `check-answer` outside the repository. |

## Version 0.3.1

Checks recorded on 12 October 2026.

| Check | Result |
|---|---|
| Tests | 80 offline tests pass on Python 3.11 and 3.12 with the PDF extra installed. They cover text and blank PDFs, encrypted PDFs, corrupt Word and Excel files, text that is not UTF-8, hidden web page text (while text hidden only from screen readers stays citable), chat delivery and Markdown escaping. Every HTTP call is mocked. |
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
