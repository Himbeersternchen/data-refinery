# data-refinery

**English** · [中文](README.zh-CN.md)

Turn scattered public reports into one ranked table where every number has a source.

You ask a question ("Which EU countries are moving fastest on renewables?"). Evidence is collected into a
YAML file: what each source says about each item (↑ / ↓), plus numeric metrics from open data APIs.
A small script scores, ranks and writes a CSV or a web page.

```
question → evidence.yaml ─┬─ signals  (source says ↑ / ↓, with link and trust tier)
                          └─ metrics  (typed in, or fetched from an open API with --live)
                                │
                                ▼
                     score = ↑ count − ↓ count  →  ranked CSV / HTML
```

The repo is also a three-step tutorial on adding an "AI brain" to a data pipeline:

| Step | What decides | Status |
|---|---|---|
| 1. Script | You write the evidence file by hand; code ranks it | ✅ done |
| 2. Workflow | Fixed steps; Claude reads a report and proposes signals for one question, you review | ✅ this release |
| 3. Agent | Claude chooses which sources to search and read (Claude Code subagent, or Claude Agent SDK) | planned |

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m refinery check examples/renewables-eu/evidence.yaml          # validate only
python -m refinery build examples/renewables-eu/evidence.yaml          # → out/renewables-eu_<date>.csv
python -m refinery build examples/renewables-eu/evidence.yaml --html   # → out/renewables-eu_<date>.html
python -m refinery build examples/renewables-eu/evidence.yaml --live   # refresh metrics from Eurostat first
python -m refinery build examples/job-shortage-de/evidence.yaml --sort salary_year

python tests/test_offline.py                                           # offline tests
```

The CSV comes with a `.columns.csv` file explaining every column. The web page works on desktop (table),
tablet and phone (cards); the column guide and the source list are folded away by default.

## Let Claude read a report (step 2)

```
report (--url or --file) → extract → evidence.proposed.yaml → you mark the wrong ones → merge → evidence.yaml → build
```

```bash
# 1. Claude proposes signals into a draft. The evidence file is not touched.
python -m refinery extract examples/job-shortage-de/evidence.yaml --url https://example.org/report.pdf

# 2. Open evidence.proposed.yaml and set the wrong ones to `decision: reject`.

# 3. Write the rest into the evidence file, then build as usual.
python -m refinery merge examples/job-shortage-de/evidence.yaml --accept-rest
python -m refinery build examples/job-shortage-de/evidence.yaml --html
```

For a new question, name an evidence file that does not exist yet; `merge` creates it:

```bash
python -m refinery extract my-question/evidence.yaml --question "Which trades pay best?" --lang en \
  --file report.pdf --source-url https://example.org/report.pdf
```

**You need** [Claude Code](https://code.claude.com/docs) 2.1.280 or newer, logged in. `extract` runs the `claude`
command, so a subscription is enough and no API key is needed; it uses up your plan's allowance, and a long
report takes a lot of it. The default model is `claude-opus-5-5`: change it for one run with `--model`, or for
good with `DEFAULT_MODEL` in `refinery/extract.py`.

**How it is kept honest**

- Claude runs with no tools, so instructions hidden in a report can at worst put wrong text in the draft.
- `quote_check`: the program looks for every quote in the report text (`found`, `not_found`, `too_short`).
- `claim_numbers_missing`: numbers in the paraphrase that are not in the quote.
- `--accept-rest` never accepts a flagged proposal. Accept a `not_found` quote yourself with
  `decision: accept_unverified`, after checking the original.
- A real quote does not prove the rest: **check the direction and the item**. In the first test run, every quote
  was real, yet a report about engineers had been filed under a skilled trade.

Asking a new question about the same report needs no new download: the text is cached in `.cache/reports/`, which
is not committed. Use `--refresh` to download it again. Running `extract` again adds to the draft and keeps the
decisions you have made. Without `--accept-rest`, `merge` writes only what you marked `accept` and leaves
`pending` proposals in the draft.

## Examples

| Folder | Question | Language |
|---|---|---|
| `examples/renewables-eu` | Which EU countries are moving fastest on renewable energy? Metrics from the Eurostat API | English |
| `examples/job-shortage-de` | Which occupations in Germany are short of workers, and what do they pay? | Chinese |

## Evidence file

```yaml
title: Which EU countries are moving fastest on renewable energy?
lang: en                       # en or zh — the language of the report UI
default_sort: change_10y       # score, or any metric id
trend_labels: {up: leading, down: lagging}

sources:
  ember-eer-2026:
    name: "Ember: European Electricity Review 2026"
    url: https://ember-energy.org/...
    date: "2026-01-22"
    tier: research             # official | research | industry | media | secondary

metrics:
  share_2024:
    label: Share 2024
    unit: "%"
    source: eurostat-nrg-ind-ren
    fetch: {provider: eurostat, dataset: nrg_ind_ren, params: {nrg_bal: REN, unit: PC, time: "2024"}}
  change_10y:
    label: Change 2014→2024
    derive: {op: subtract, a: share_2024, b: share_2014}

items:
  - id: NL
    name: Netherlands
    keys: {eurostat: NL}       # code used by the fetcher
    metrics: {}                # or type values in: {share_2024: 20.2}
    signals:
      - {source: ember-eer-2026, trend: up, claim: "Solar supplied more than a fifth of electricity in 2025"}
```

`claim` is your paraphrase; add `quote:` for the exact original sentence. `check` lists every problem at once
(missing source, unknown tier, metric that is not a number, …).

### Optional fields

| Where | Field | Meaning |
|---|---|---|
| item | `subtitle`, `tags` | Original-language name; free-form labels |
| item | `keys` | Codes used by fetchers, e.g. `{eurostat: DK}` |
| item metric value | `{value, url, note}` | A number, or a mapping with a link and a note shown next to it |
| signal | `trend: mixed` | Shown, but not scored |
| signal | `quote` | The exact original sentence, next to your `claim` |
| metric | `better: lower` | Smaller is better when sorting by this metric |
| metric | `show: false` | Hidden from the table (still usable in `derive` and `--sort`) |
| metric | `description` | Longer text for the column guide |

Where metric values come from, in order: typed into the evidence file → the `--live` snapshot →
`derive`. `--live` overwrites `evidence.metrics.json`; if any request fails, the old snapshot is kept.

**Trust tiers** — `official` statistics offices, ministries · `research` institutes, think tanks ·
`industry` company or platform reports · `media` news coverage · `secondary` a re-post of someone else's
figure. Prefer the original; when you only have a re-post, say so with `secondary`.

## Ground rules

- **Sources are your choice, and so are their terms.** `extract` reads whatever report you point it at; checking
  that a site's terms allow it is up to you. If a site refuses, the command stops and says so, and you can download
  the report yourself and use `--file`. `--live` metrics come only from open APIs whose terms permit automated
  access (Eurostat: CC BY 4.0).
- **No personal data.** Rank countries, sectors, products, occupations — not people.
- **A human decides.** In steps 2 and 3 the AI only proposes; nothing enters an evidence file until you run
  `merge`. With `--accept-rest` you are accepting everything you did not mark as wrong.
- Scores only count which side a signal takes. Sources measure different things, so their numbers are not compared
  with each other. Every signal counts once and nothing is deduplicated, so several signals from one report, or from
  one publisher, each add to the score.

## Layout

```
refinery/
  evidence.py      load, validate, score, rank
  fetch/           open-data fetchers (eurostat.py) and snapshot writing
  output.py        CSV + column guide, HTML page
  report.py        read a report (link or file) into plain text
  extract.py       ask Claude, check the quotes, write the draft
  merge.py         write accepted proposals into the evidence file
examples/<name>/
  evidence.yaml           the evidence
  evidence.metrics.json   snapshot written by --live (committed, so builds work offline)
  evidence.proposed.yaml  draft waiting for review (not committed)
tests/test_offline.py
```

## License

[MIT](LICENSE). Example data belongs to its publishers; see each source's link and terms.
