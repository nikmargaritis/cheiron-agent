# ClinicalTrials.gov Query-to-Visualization Agent

Ask a question about clinical trials in plain English and get back a **validated, frontend-ready
visualization spec** built from live [ClinicalTrials.gov v2 API](https://clinicaltrials.gov/data-api/api)
data, with **citations on every data point** that link back to the exact trial records.

**At a glance**
- **6 chart types:** bar, grouped bar, time series, histogram, scatter, network graph.
- **One pipeline for all of them:** the LLM only *plans* the query, and code fetches, counts,
  and builds the chart, so the model can't invent numbers.
- **Deep citations:** every bar, point, node, and edge lists the trials behind it, with the exact field quoted.
- **Graceful non-answers:** vague questions get a clarifying question, and out-of-scope ones
  (efficacy, survival, medical advice) get an explanation instead of a guessed chart.
- **Web UI** with interactive network graphs, **102 offline + 7 live tests**, and a **planner eval
  suite** (96/96).

### Where to find each requirement

| Requirement | Section |
|---|---|
| How to run (install, configure, start) | [How to run](#how-to-run) |
| Request / response schemas | [Request schema](#request-schema--post-v1query), [Response schema](#response-schema) |
| Key design decisions and tradeoffs | [Design decisions and tradeoffs](#design-decisions-and-tradeoffs) |
| Limitations and improvements | [Limitations](#limitations), [With more time](#with-more-time) |
| 3–5 example runs with actual JSON | [Example runs](#example-runs) and [`examples/`](examples/) |
| Demo (UI) | [Web UI](#web-ui) |
| AI tool usage | [How this was built](#how-this-was-built-ai-tool-usage) |

---

## How to run

**1. Install** (Python 3.11+)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**2. Configure:** copy the example env file and set your OpenAI key. All other settings have defaults.
```bash
cp .env.example .env            # then set OPENAI_API_KEY
```

**3. Start**
```bash
uvicorn trialviz.api.app:app --reload
```

Open **http://localhost:8000** for the web UI, or call the API directly:

```bash
curl -s localhost:8000/v1/query -H 'content-type: application/json' \
  -d '{"query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab"}'
```

Other commands:

| Command | What it does |
|---|---|
| `pytest` | Offline test suite (no network) |
| `pytest -m live` | Checks against the real ClinicalTrials.gov API |
| `python scripts/eval_planner.py [--repeat 3]` | Scores the planner on 32 eval questions (no ClinicalTrials.gov calls) |
| `python scripts/run_examples.py` | Regenerates `examples/*.json` with the real planner |
| `python scripts/smoke_live.py [out_dir]` | Runs all six chart types on live data with scripted plans (no LLM tokens) |

---

## How it works

```
            ┌────────── LLM ──────────┐   ┌──────────────── deterministic ────────────────┐
request ──► plan ──► validate ──ok──► fetch ──► build ──► QueryResponse (status=ok)
             ▲          │                 │
             └─ retry ──┤ (1x, with       └─ 0 trials ─┐
                        │  error feedback)             ▼
                        └─ clarify / unsupported ──► respond ──► QueryResponse (status≠ok)
```

| Step | Kind | What it does |
|---|---|---|
| `plan` | LLM (strict structured output) | Question → `QueryPlan`: filters, analysis kind, dimensions. **Never outputs data.** |
| `validate` | code | Checks the plan makes sense; on failure, retries the planner once with the errors. |
| `fetch` | code | Picks a strategy (exact per-bucket counts or capped download) and calls the API. |
| `build` | code | Counts, attaches citations, and builds the chart through validated Pydantic models. |
| `respond` | code | Non-chart outcomes: clarification, unsupported, no results. |

Every question is reduced to **filters × one analysis kind × dimension(s)**, so new questions
don't need new code paths:

| Analysis | Chart | Example question |
|---|---|---|
| `time_trend` | `time_series` | "How many trials started each year for breast cancer?" |
| `distribution` | `bar_chart` | "Which countries have the most recruiting type 2 diabetes trials?" |
| `comparison` | `grouped_bar_chart` (or multi-series `time_series`) | "Compare phases for nivolumab vs ipilimumab" |
| `histogram` | `histogram` | "What's the typical enrollment size for melanoma trials?" |
| `scatter` | `scatter_plot` | "Enrollment vs. start year for glioblastoma trials" |
| `network` | `network_graph` | "Sponsors and drugs for glioblastoma" / "Which drugs are combined in pancreatic cancer trials?" |

**Dimensions** (group by, or network nodes): `phase, status, start_year, country, sponsor,
sponsor_class, drug, intervention, intervention_type, condition, study_type`.
**Numeric fields** (histogram, scatter): `enrollment, start_year`.

```
src/trialviz/
  schemas/      request / plan / response contracts
  ctgov/        API client, filters → query params, dimension registry (the only code that knows the payload shape)
  agent/        LangGraph wiring, the five steps, prompt, LLM wrapper
  analysis/     counting, networks, histograms, citations, chart builders
  api/          FastAPI app (query endpoint, saved examples, serves the UI)
  web/          single-file web UI
  evals/        planner eval cases and grading
```

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/query` | Question → visualization spec |
| `GET` | `/v1/examples` | Saved example runs: `[{name, query}]` |
| `GET` | `/v1/examples/{name}` | One saved run: `{request, response}` |
| `GET` | `/` | Web UI |
| `GET` | `/docs` | Interactive OpenAPI docs |
| `GET` | `/health` | Liveness + version |

Errors: `422` for invalid requests (field-level messages), `502` if ClinicalTrials.gov or the LLM fails.

### Request schema — `POST /v1/query`

| Field | Type | Required | Validation → API mapping |
|---|---|---|---|
| `query` | string | **yes** | 3–1000 chars |
| `drug_name` | string | no | → `query.intr` |
| `condition` | string | no | → `query.cond` |
| `sponsor` | string | no | → `query.spons` |
| `country` | string | no | → `query.locn` |
| `trial_phase` | `Phase[]` | no | `EARLY_PHASE1, PHASE1, PHASE2, PHASE3, PHASE4, NA` → `AREA[Phase](… OR …)` |
| `status` | `OverallStatus[]` | no | API status enum → `filter.overallStatus` |
| `start_year` / `end_year` | int | no | 1900–2100, `start_year ≤ end_year` → `AREA[StartDate]RANGE[…]` |

Unknown fields are rejected (`422`). Structured fields **override** anything the planner reads
from `query`.

### Response schema

Abridged from a real response ([`examples/02_…json`](examples/), "How are breast cancer trials
distributed across phases?"):

```jsonc
{
  "status": "ok",                        // ok | needs_clarification | unsupported | no_results
  "visualization": {
    "type": "bar_chart",                 // bar_chart | grouped_bar_chart | time_series |
                                         // histogram | scatter_plot | network_graph
    "title": "Trials by phase",
    "subtitle": "Condition: breast cancer",
    "orientation": "vertical",
    "encoding": {
      "x": {"field": "phase", "type": "ordinal", "title": "Phase",
            "sort": ["Early Phase 1", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Not applicable"]},
      "y": {"field": "trial_count", "type": "quantitative", "title": "Number of trials", "unit": "trials"}
    },
    "data": [
      {"phase": "Phase 3", "trial_count": 1559,
       "citations": [{"nct_id": "NCT03538171",
                      "field": "protocolSection.designModule.phases",
                      "excerpt": "PHASE3",
                      "title": "Ph3 Study of Exemestane With or Without Entinostat in Chinese Patients With …",
                      "url": "https://clinicaltrials.gov/study/NCT03538171"}, "… up to 5"]},
      "… one row per phase"
    ]
  },
  "meta": {
    "source": "clinicaltrials.gov",
    "query": "How are breast cancer trials distributed across phases?",
    "filters": {"condition": "breast cancer"},
    "plan": { /* the validated QueryPlan */ },
    "total_matching_trials": 16899,
    "trials_analyzed": 16899,
    "count_method": "exact",             // exact | sample
    "time_granularity": null,            // "year" for start-year axes
    "assumptions": [],                   // planner's interpretation choices
    "notes": ["A trial can list several phase values and is counted under each, so bars can sum to more than the number of trials."],
    "api_requests": ["https://clinicaltrials.gov/api/v2/studies?query.cond=breast+cancer&filter.advanced=AREA%5BPhase%5DPHASE3&…", "…"],
    "timings_ms": {"plan": 2736.6, "validate": 0.1, "fetch": 233.7, "build": "…"},
    "generated_at": "2026-10-10T20:06:34Z"
  }
}
```

When there's no chart, `visualization` is absent and `message` explains why:

```json
{"status": "needs_clarification", "message": "Which drug do you want the time trend for?", "meta": {"…": "…"}}
```

**Rendering rules** (guaranteed by server-side validation):
- Switch on `visualization.type`.
- Every key named in `encoding` exists on every `data` row.
- `encoding.x.sort` is the category order for categorical axes, and rows already come in that order.
- `grouped_bar_chart` has every category × series combination (zeros included), keyed by `encoding.color.field`.
- `histogram` rows have `bin_start` (inclusive) and `bin_end` (exclusive). The last bin can be an overflow bin (`"≥ N"`).
- `scatter_plot` has one row per trial, with `nct_id` and `brief_title` for tooltips.
- `network_graph` uses `nodes` / `edges` instead of `data`. Node ids are namespaced (`drug:gemcitabine`), `group` is the dimension, and edges always reference existing nodes.
- Every datum has `trial_count` and up to `MAX_CITATIONS_PER_DATUM` citations. Edge citations come in pairs (one per endpoint) for each supporting trial.

---

## Example runs

Actual outputs from `python scripts/run_examples.py` (real planner, live API), saved in
[`examples/`](examples/). They also open instantly in the web UI from the "saved run" dropdown.

| # | Request | Plan → chart | Result | API calls |
|---|---|---|---|---|
| 1 | "How has the number of trials for this drug changed over time?" + `drug_name: Pembrolizumab` | `time_trend` → `time_series` | 2,974 trials, exact. 1 trial in 2008, rising to 299 in 2022 | 24 |
| 2 | "How are breast cancer trials distributed across phases?" | `distribution` → `bar_chart` | 16,899 trials, exact. Phase 2 = 4,590, Phase 3 = 1,559 | 7 |
| 3 | "Which countries have the most recruiting trials for type 2 diabetes?" | `distribution` → horizontal `bar_chart` | 870 recruiting trials, exact. US 312, China 122, Canada 69 | 1 |
| 4 | "Compare phases for trials involving nivolumab vs ipilimumab." | `comparison` → `grouped_bar_chart` | Phase 2: nivolumab 1,164 vs ipilimumab 542 | 14 |
| 5 | "Show a network of sponsors and drugs for glioblastoma trials." | `network` (sponsor ↔ drug) → `network_graph` | 36 nodes, 56 edges. Strongest link: NCI ↔ temozolomide (30 trials) | 3 |

In #1 the planner resolved "this drug" from the structured field and recorded that in `assumptions`.

---

## Web UI

A single page served at `/` (`src/trialviz/web/index.html`, Chart.js + vis-network from a CDN, no
build step).

- **Ask** in plain English or click a suggestion. Optional filters override the question.
- **Saved runs:** open any file from `examples/` instantly, without an LLM call.
- **Interactive networks:** drag nodes and scroll to zoom. Dot size is trial count, and line
  thickness is shared trials.
- **Click any bar, point, node, or connection** to see the trials behind it, each with a
  ClinicalTrials.gov link and the exact field quoted from the record.
- **Tabs** show data notes, how the question was interpreted, every API call, and the raw JSON.

The UI has no per-question logic and draws everything from the response's `encoding`, which shows
the schema is enough to build a frontend from.

---

## Design decisions and tradeoffs

1. **The LLM plans; code computes.** The only LLM call outputs a `QueryPlan` of enums and filter
   strings. Counts, chart type, and citations are deterministic, so the model has no data values to
   hallucinate.
   *Tradeoff:* questions outside the plan's vocabulary get "unsupported" instead of an improvised answer.
2. **Validate, then retry once with the errors.** Structured outputs guarantee the plan parses, not
   that it makes sense. `validate` catches problems like a comparison with one series and sends
   specific errors back to the planner.
   *Tradeoff:* one extra LLM call when it happens. After a second failure the request ends with an explanation.
3. **The chart type follows from the analysis kind**, so the same kind of question always gets the
   same chart shape.
   *Tradeoff:* users can't ask for a specific chart style (e.g. a pie chart).
4. **Exact counts at any scale.** The API's stats endpoint can't be filtered, so for dimensions with a
   fixed set of values (phase, status, sponsor class, intervention type, study type, start year) the
   service sends one request per bucket with `countTotal=true`. Each response carries the exact count
   *and* the records to cite. A 16,899-trial phase chart takes 7 requests and about 250 ms.
   *Tradeoff:* one request per bucket, so a 30-year trend makes about 30 requests (run concurrently).
5. **Capped download for everything else** (sponsors, countries, drugs, networks, histograms): up to
   `CTGOV_MAX_RECORDS` (3,000) with a minimal field list.
   *Tradeoff:* above the cap, results are a sample. The response says so (`count_method: "sample"`).
6. **Citations come from the same code that counted the trial**, so a citation can't contradict its
   count. `verify_citation` re-checks each one against the raw record.
7. **Dimension registry** (`ctgov/fields.py`) is the only code that knows the API payload shape. Adding
   a dimension is one enum member and one registry entry.
8. **Custom chart schema instead of Vega-Lite.** Vega-Lite has no network graph and a very large spec.
   The encoding borrows its vocabulary (`field` + `nominal|ordinal|quantitative|temporal`), so mapping
   to Vega-Lite, Recharts, or Chart.js is mechanical.
   *Tradeoff:* frontends need a small adapter, like the one in the web UI.
9. **`drug` separate from `intervention`.** The registry's interventions include devices, imaging, and
   biospecimen collection, which dominated the first drug networks. `drug` keeps only drug and biological
   types, and networks drop placebo and standard-of-care arms.
10. **Name cleanup by rules, not MeSH.** The API's MeSH mappings looked useful but dropped drugs
    (capecitabine was missing from a 4-drug regimen) and mis-mapped others. Names are cleaned instead
    by stripping doses, schedules, and salt forms ("Gemcitabine Hydrochloride" → "gemcitabine").
    *Tradeoff:* true synonyms ("5-FU" vs "Fluorouracil") stay separate.
11. **Reproducible:** every API URL called is returned in `meta.api_requests`.

---

## Limitations

- **Synonyms:** "5-FU" and "Fluorouracil" are separate network nodes, as are "Leucovorin" and "Folinic acid".
- **Search is full-text:** `query.intr` / `query.cond` match loosely related trials (a `pembrolizumab`
  search returned a fecal-transplant trial that mentions it).
- **Sampled rankings:** sponsors, countries, drugs, and networks use at most 3,000 trials, so
  "top sponsors for all cancer" is approximate (marked `sample`).
- **One chart per question.** The only numeric fields are enrollment and start year. Trial duration
  and outcome data aren't modeled.
- **Comparisons** count a trial in every series it matches (series totals are in `meta.notes`).
- **Network entities:** drugs, sponsors, conditions, and countries, but not investigators or individual sites.
- **Evals are my own questions:** they show the planner handles the intended question types, not real
  users' wording. Typos and non-English questions aren't covered.
- **No caching:** identical questions re-hit the API.

## With more time

1. **Synonym merging** with an RxNorm lookup (cached), so drug nodes merge correctly.
2. **Investigator and site dimensions** for networks like "which sites run trials with which sponsors".
3. **Caching** of API responses and plans, for speed and to cut repeat LLM costs.
4. **Zero-result recovery:** when filters match nothing, suggest a broader query instead of only "no results".
5. **Evals from real usage:** add logged real questions (typos, vague wording) to the eval suite.
6. **Deployment** with a rate limit, so a public URL can't run up LLM costs.

---

## Testing and evaluation

- **Offline (`pytest`, 102 tests):** schemas, OpenAI strict-mode compatibility, filter → query-param
  mapping, field extraction on real API records, aggregation and networks, HTTP routes, and the full
  pipeline against a mocked API.
- **Live (`pytest -m live`, 7 tests):** citations re-checked against separately fetched records, bucket
  counts must add up to the API's total, and status enums are compared with the API to catch drift.
- **End to end:** `scripts/smoke_live.py` runs all six chart types on live data, and the web UI was tested
  in a browser.

### Planner evals

The planner is the only step that can misread a question, so it has its own eval suite
(`src/trialviz/evals/`): 32 questions, each with the parts of the plan that matter (status, analysis,
group by, filters, series, numeric fields, network dimensions). Filters must match exactly
(case-insensitive, with listed alternatives), and any filter the question didn't ask for counts as a
failure. Only plan → validate runs, so a full pass makes no ClinicalTrials.gov calls and costs well
under a dollar.

Latest run (`gpt-5.4`, every case 3 times):

| Tag | Passed | | Tag | Passed |
|---|---|---|---|---|
| distribution | 36/36 | | comparison | 9/9 |
| trend | 15/15 | | network | 9/9 |
| filters (tricky extraction) | 12/12 | | numeric (histogram, scatter) | 6/6 |
| unsupported | 12/12 | | clarify | 9/9 |
| caller fields | 6/6 | | **total** | **96/96** |

About 1,800 input and 170 output tokens per question, median 2.6 s.

### Bugs found and fixed

- **Found by evals:** the planner didn't know today's date ("last decade" ended at 2025), so the date
  is now in the prompt. In 1 of 3 runs, a comparison had labeled series with empty filters, so both
  bars would have counted every trial. The validator now rejects that.
- **Found by the live bucket-sum check:** 5 expanded-access status values were missing, which silently
  dropped 18 glioblastoma trials from exact counts.
- **Found by reading real output:** drug networks were dominated by non-drug interventions (which led to
  the `drug` dimension), and salt stripping turned "Sodium bicarbonate" into "bicarbonate".
- **Other:** year bounds relied on where the API sorts undated trials, `build` time was missing from
  `timings_ms`, and planner calls could wait up to 10 minutes (now `PLANNER_TIMEOUT_S`, 30 s).

---

## How this was built (AI tool usage)

**Planning.** Before writing any code, I planned the stack (Python, LangGraph, Pydantic, FastAPI),
the pipeline shape, and which bonuses to go after: deep citations, network graphs, and a frontend.

**Building.** I used Claude Code to review my plan and implement it, including the tests and the web UI.
The most important change came out of that review: my original plan had GPT turn the aggregated data
into the final chart, which left room for invented numbers. I switched to having GPT only produce a
constrained query plan, with every number computed in code. That became the core of the design. I
also went with exact per-bucket counting (the API's stats endpoint can't be filtered), a custom schema
instead of Vega-Lite (Vega-Lite has no network graphs), a separate `drug` dimension, and syntactic
name cleanup instead of MeSH mapping.

**Checking it.** I ran the system end to end with the real planner, generated the example outputs, and
went through each pipeline step on a real query to make sure I understood what it was doing and why.
Every API assumption (filter syntax, page limits, sort order, enum values) was tested against the
live API before the code relied on it. Beyond the offline and live test suites, I checked results by
hand, for example that the pancreatic-cancer drug network recovers known regimens like gemcitabine +
nab-paclitaxel. Several of the fixes listed above came from looking at real output rather than from
tests.

**Iterating.** I committed in reviewable steps, did a cleanup pass to remove dead code and noise,
used the web UI to try questions interactively, including ones the system should refuse or ask about,
and added the planner evals, which caught two bugs the tests had missed.

GPT (`gpt-5.4`) is used at runtime only as the query planner.
