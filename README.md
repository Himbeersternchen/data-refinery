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
| 1. Script | You write the evidence file by hand; code ranks it | ✅ this release |
| 2. Workflow | Fixed steps; Claude reads a report and proposes signals for one question, you review | planned |
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

- **Only use sources that allow it.** Metrics are fetched only from open APIs whose terms permit automated
  access (Eurostat: CC BY 4.0). Do not add scrapers for sites that forbid automated access.
- **No personal data.** Rank countries, sectors, products, occupations — not people.
- **A human signs off.** In steps 2 and 3 the AI proposes evidence; nothing enters an evidence file until
  you have checked it against the source.
- Scores only count which side each source takes. Sources measure different things, so their numbers are
  not compared with each other.

## Layout

```
refinery/
  evidence.py      load, validate, score, rank
  fetch/           open-data fetchers (eurostat.py) and snapshot writing
  output.py        CSV + column guide, HTML page
examples/<name>/
  evidence.yaml           the evidence
  evidence.metrics.json   snapshot written by --live (committed, so builds work offline)
tests/test_offline.py
```

## License

[MIT](LICENSE). Example data belongs to its publishers; see each source's link and terms.
