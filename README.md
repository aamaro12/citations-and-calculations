# Citations & Calculations

A Codex skill and plugin that makes the claims in an answer checkable. Each quoted fact links to the exact passage it came from, and each computed figure links to its inputs and formula, recomputed in Python. Codex still answers in chat; the links open small local pages with the evidence.

It works with uploaded files, public web pages and responses from tools already connected to Codex. Codex finds the evidence and reviews the claims. Python saves a copy of each source, checks every quote against it and redoes the arithmetic. It needs no extra model or API key.

```text
Upload, web page or connected tool
          ↓
Saved copy of the source, with exact locations: paragraph, page, cell or JSON field
          ↓
Facts with their units, scale and period, plus any stated assumptions
          ↓
Calculations recomputed in Python
          ↓
An answer in chat, with a link behind every figure
```

A citation that checks out shows that the quote is in the source, not that the claim is true. The report keeps 3 things apart: whether the quote matches, the recorded judgement that it supports the claim, and the recomputed arithmetic.

## Try it

Python 3.11 or later. The core uses only the standard library; reading PDFs needs an optional extra.

```sh
python3 scripts/citecalc.py demo --output runs/demo
```

The demo answers a question from 3 invented sources: a CSV of financials, a company web page and a CRM tool response. It returns the checked answer, a revenue growth of 25.0% and an operating margin of 20.0%, with a link behind each figure. The example is synthetic and says nothing about any actual company.

To audit the demo again and print its answer:

```sh
python3 scripts/citecalc.py answer --workspace runs/demo --bundle runs/demo/input.json --output runs/demo/report
```

`runs/demo/report/review.html` is an optional interactive report, with filters and downloads.

To install the `citecalc` command with PDF support:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[pdf]'
.venv/bin/citecalc demo --output runs/demo
```

If your Python has no trusted certificate store, point `SSL_CERT_FILE` at your system's certificate bundle before fetching web pages. Certificate checks are never turned off.

## Use it with Codex

The repository is a local Codex plugin. `plugin.json` is the manifest, `.codex-plugin/plugin.json` is a compatibility copy, and `skills/cite-and-calculate/SKILL.md` describes the workflow. It follows the [OpenAI plugin format](https://developers.openai.com/plugins/build/plugins). Nothing installs itself or changes your Codex settings.

Point a Codex task at the skill, for example:

> Use the cite-and-calculate skill at `skills/cite-and-calculate/SKILL.md` to compare the uploaded reports. Cite the exact rows, calculate the differences in Python, and show the checked answer here with links to the sources and calculations.

Once the plugin is installed, call it with `$cite-and-calculate`. It uses the web and connector tools Codex already has, and holds no credentials of its own. [Capture conventions](skills/cite-and-calculate/references/captures.md) explains how to save a tool's response as evidence.

## Commands

| Command | What it does |
|---|---|
| `ingest --workspace runs/task --file report.pdf` | Saves a copy of a file and indexes its passages. Reads text, Markdown, CSV, JSON, HTML, Word, Excel and text-based PDF files. |
| `fetch --workspace runs/task --url https://…` | Saves a public web page or document. Only public addresses on ports 80 and 443 are allowed, checked again at every redirect. |
| `capture --workspace runs/task --file tool-response.json` | Saves a connected tool's response, with its query and whether it was complete. |
| `read --workspace runs/task --source <source-id> --find revenue` | Lists the matching passages with their exact locations. |
| `audit --workspace runs/task --bundle answer-input.json --output runs/task/report` | Rechecks every quote and recomputes every figure, then returns the chat answer and the optional reports. |
| `answer --workspace runs/task --bundle answer-input.json --output runs/task/report` | Runs the same audit and prints the chat answer. |
| `demo --output runs/demo` | Runs the whole workflow offline on the synthetic example. |

Run them with `python3 scripts/citecalc.py`, or as `citecalc` once installed. [The bundle format](skills/cite-and-calculate/references/bundle-format.md) describes the facts, citations, calculations and claims an answer is built from, and the demo writes a working example to `runs/demo/input.json`.

## What gets checked

- Each saved source is unchanged, and each quote appears at its stated location.
- Each number matches the source, sign included. Where a source labels a value's metric, period, company, unit or scale, the fact must carry the same label.
- Calculations use compatible units and scales. Comparisons across periods or companies need a stated reason, and missing, circular or unused inputs are refused.
- Every figure in an answer comes from a cited fact or a recomputed calculation. To enforce this, claim text can contain no digits outside its figure markers, and that includes years and labels such as Q3.
- Each claim needs a recorded review, and a review of `contradicts` or `unclear` blocks it. Inferences and assumptions keep their labels.
- Nothing is reused from an earlier audit: every audit rechecks the sources and recomputes every figure.

`checks_passed` means these checks passed, not that the answer is true. `checks_passed_with_caveats` adds caveats about coverage, extraction or assumptions. `blocked` keeps failed claims visible, marks the answer as a draft and exits with code 2.

## Limits

- This is a workflow Codex follows when asked, not a hook on every answer. Text outside the audited answer, and connector calls nobody saved, are not checked. Python analysis is recomputed only when it is written as a formula in the bundle, and no model inside the package judges meaning.
- PDF passages are located by page and line, without positions on the page, and scanned pages need OCR first. Word files are read without headers, footers or tracked changes. Excel formula results are shown as context but cannot be used as inputs until recomputed. Web page text hidden inline or by attribute is left out, but text hidden by a stylesheet is not detected. Numbers are read in English formats.
- Sources and reports stay on your machine, in the ignored `runs/` folder. The report makes no network calls and loads no third-party code. Hashes show that saved evidence has not changed; they cannot detect someone rewriting both the evidence and its references. Publication dates and tool provenance are recorded as supplied.

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests
python3 scripts/citecalc.py demo --output runs/demo
python3 scripts/check_report.py runs/demo/report/review.html
ruff check src tests scripts
ruff format --check src tests scripts
python3 scripts/check_package.py
python3 scripts/check_guards.py
```

The tests deliberately alter sources, columns, periods, scales, units, calculations, reviews and answer figures. `check_guards.py` removes selected safeguards in isolated copies and requires a test to catch each removal; it covers chosen safeguards, not every possible fault. CI runs all of this on Python 3.11 and 3.12. [Design decisions](docs/design.md) explains the choices, and [validation](docs/validation.md) records the latest checks.

## Licence

MIT. A research prototype by Alessandro Amaro.
