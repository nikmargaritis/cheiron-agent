# trialviz — ClinicalTrials.gov Query-to-Visualization Agent

Turns a natural-language question about clinical trials into a **validated, frontend-ready
visualization spec**, computed from live [ClinicalTrials.gov v2 API](https://clinicaltrials.gov/data-api/api)
data, with **deep citations** on every data point.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # set OPENAI_API_KEY
uvicorn trialviz.api.app:app --reload
```

Open **http://localhost:8000** for the web UI, or call the API directly:

```bash
curl -s localhost:8000/v1/query -H 'content-type: application/json' \
  -d '{"query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab"}'
```

| Command | What it does |
|---|---|
| `pytest` | Offline suite (schemas, query building, extraction, aggregation, HTTP routes, full graph against a mocked API) |
| `pytest -m live` | Checks against the real API: citations re-verified against independently fetched records, bucket-sum invariants, enum drift |
| `python scripts/smoke_live.py [out_dir]` | Runs every chart type end to end on live data with scripted plans (no LLM tokens) |
| `python scripts/run_examples.py` | Runs the example queries with the real planner and writes `examples/*.json` |

## Web UI

A single page served by the API at `/`. It's one file (`src/trialviz/web/index.html`) with
Chart.js and vis-network loaded from a CDN, and no build step.

- **Ask** in plain English, or click a suggestion. Optional filters (drug, condition, sponsor,
  country, years, phase, status) override anything in the question.
- **Saved runs:** pick any file from `examples/` to render it instantly without an LLM call, or
  open any saved response JSON.
- **Network graphs** are interactive: drag nodes and scroll to zoom. Dot size is trial count and
  line thickness is shared trials.
- **Click any bar, point, node, or connection** to list the trials behind it. Each entry shows the
  NCT ID linked to ClinicalTrials.gov, the trial title, and the exact field/value quoted from the
  record.
- **Badges** show matching trials, exact vs. sampled counts, and response time. Tabs show data
  notes, how the question was interpreted, every API call (clickable), and the raw JSON.
- Clarification and unsupported answers appear as a message instead of a chart.

The UI renders purely from the response's `encoding` and contains no per-question logic. It's a
working check that the response schema is enough to build a frontend from.

## Architecture

```
            ┌────────── LLM ──────────┐   ┌──────────────── deterministic ────────────────┐
request ──► plan ──► validate ──ok──► fetch ──► build ──► QueryResponse (status=ok)
             ▲          │                 │
             └─ retry ──┤ (1x, with       └─ 0 trials ─┐
                        │  error feedback)             ▼
                        └─ clarify / unsupported ──► respond ──► QueryResponse (status≠ok)
```

| Node | Kind | Responsibility |
|---|---|---|
| `plan` | LLM (strict structured output) | NL → `QueryPlan`: filters + analysis kind + dimensions. **Never emits data.** |
| `validate` | code | Semantic checks the JSON schema can't express; one retry with the errors fed back. |
| `fetch` | code | Chooses an ingestion strategy (exact bucket counts vs. capped download) and calls the API. |
| `build` | code | Aggregation, citations, and chart construction through validated Pydantic models. |
| `respond` | code | Graceful non-chart outcomes: clarification, unsupported, no results. |

```
src/trialviz/
  schemas/      request / plan / response contracts (the public API surface)
  ctgov/        API client, Filters→params, dimension registry (the only code that knows the payload shape)
  agent/        LangGraph wiring, nodes, prompt, LLM wrapper
  analysis/     counting, co-occurrence networks, histograms, citations, chart builders
  api/          FastAPI app (query endpoint, saved examples, serves the UI)
  web/          single-file web UI
```

### Query coverage

Every question is reduced to **filters × one analysis kind × dimension(s)**. A new question class
doesn't need a new handler:

| Analysis | Chart | Example |
|---|---|---|
| `time_trend` | `time_series` | "How many trials started each year for breast cancer?" |
| `distribution` | `bar_chart` | "How are melanoma trials distributed across phases?" / "Which countries have the most recruiting T2D trials?" |
| `comparison` | `grouped_bar_chart` (or multi-series `time_series`) | "Compare phases for nivolumab vs ipilimumab" |
| `histogram` | `histogram` | "What's the typical enrollment size for melanoma trials?" |
| `scatter` | `scatter_plot` | "Enrollment vs. start year for glioblastoma trials" |
| `network` | `network_graph` | "Sponsors ↔ drugs for glioblastoma" / "Which drugs co-occur in pancreatic cancer trials?" |

Dimensions: `phase, status, start_year, country, sponsor, sponsor_class, drug, intervention,
intervention_type, condition, study_type`. Numeric fields: `enrollment, start_year`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/query` | Question → visualization spec (schemas below) |
| `GET` | `/v1/examples` | Saved example runs: `[{name, query}]` |
| `GET` | `/v1/examples/{name}` | One saved run: `{request, response}` |
| `GET` | `/` | Web UI |
| `GET` | `/docs` | Interactive OpenAPI docs |
| `GET` | `/health` | Liveness + version |

Errors: `422` for invalid requests (field-level messages); `502` if ClinicalTrials.gov or the LLM fails.

## Request schema — `POST /v1/query`

| Field | Type | Required | Validation / mapping |
|---|---|---|---|
| `query` | string | **yes** | 3–1000 chars |
| `drug_name` | string | no | → `query.intr` |
| `condition` | string | no | → `query.cond` |
| `sponsor` | string | no | → `query.spons` |
| `country` | string | no | → `query.locn` |
| `trial_phase` | `Phase[]` | no | `EARLY_PHASE1, PHASE1, PHASE2, PHASE3, PHASE4, NA` → `AREA[Phase](… OR …)` |
| `status` | `OverallStatus[]` | no | API status enum → `filter.overallStatus` |
| `start_year` / `end_year` | int | no | 1900–2100, `start_year ≤ end_year` → `AREA[StartDate]RANGE[…]` |

Unknown fields are rejected (`422`). Structured fields **override** anything the planner
extracts from `query`.

## Response schema

Abridged from a real response (`examples/02_…json`: "How are breast cancer trials distributed
across phases?"):

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

Renderer contract:
- Switch on `visualization.type`.
- Every key named in `encoding` is guaranteed to exist on every `data` row (enforced server-side).
- `encoding.x.sort` is the explicit category order for categorical axes. Rows already come in that order.
- `grouped_bar_chart` contains every category × series combination (zeros included), keyed by `encoding.color.field`.
- `histogram` rows have `bin_start` (inclusive) and `bin_end` (exclusive). The last bin can be an overflow bin (`bin_label` `"≥ N"`), where `bin_end` is the maximum value.
- `scatter_plot` has one row per trial, with tooltip fields `nct_id` and `brief_title`.
- `network_graph` uses `nodes` / `edges` instead of `data`. Node ids are namespaced (`drug:gemcitabine`), `group` is the dimension, and edges always reference existing node ids.
- Every datum carries `trial_count` (all trials behind it) and up to `MAX_CITATIONS_PER_DATUM` citations. Edge citations come in pairs (one per endpoint) for each supporting trial.

## Example runs

Actual outputs from `python scripts/run_examples.py` (real planner, live API), in [`examples/`](examples/).
They can also be opened in the web UI from the "saved run" dropdown.

| # | Request | Plan → chart | Result | API calls |
|---|---|---|---|---|
| 1 | "How has the number of trials for this drug changed over time?" + `drug_name: Pembrolizumab` | `time_trend` → `time_series` | 2,974 trials, exact. 1 trial in 2008, rising to 299 in 2022; 5 undated trials noted | 24 |
| 2 | "How are breast cancer trials distributed across phases?" | `distribution` → `bar_chart` | 16,899 trials, exact. Phase 2 = 4,590, Phase 3 = 1,559 | 7 |
| 3 | "Which countries have the most recruiting trials for type 2 diabetes?" | `distribution` → horizontal `bar_chart` | 870 recruiting trials, exact. US 312, China 122, Canada 69 (top 15 shown) | 1 |
| 4 | "Compare phases for trials involving nivolumab vs ipilimumab." | `comparison` → `grouped_bar_chart` | Phase 2: nivolumab 1,164 vs ipilimumab 542 | 14 |
| 5 | "Show a network of sponsors and drugs for glioblastoma trials." | `network` (sponsor ↔ drug) → `network_graph` | 36 nodes, 56 edges. Strongest link: NCI ↔ temozolomide (30 trials) | 3 |

In #1 the planner resolved "this drug" from the structured field and recorded that in `assumptions`.
In #5 it chose the `drug` dimension over `intervention`, as the prompt directs.

## Key design decisions

1. **The LLM plans; code computes.** The only LLM call outputs a `QueryPlan` made of enums and
   filter strings. Counts, buckets, chart type, and citations are all deterministic. The model never
   sees or produces data values, so there are no data values for it to hallucinate.
2. **Chart type follows from the analysis kind** (`analysis.specs`), so the same question class
   always yields the same chart shape.
3. **Dimension registry** (`ctgov/fields.py`) is the only code that knows the API payload shape.
   Adding a dimension is one enum member plus one registry entry.
4. **Exact counts at any scale.** `/stats/field/values` can't be filtered, so for enumerable dimensions
   (phase, status, sponsor class, intervention type, study type, and start year via one RANGE per year)
   the service sends one request per bucket with `countTotal=true&pageSize=<citations>`. Each response
   carries the bucket's exact count *and* the records to cite for it. Cost depends on the number of
   buckets, not the number of trials: a 16,899-trial phase distribution takes 7 requests and about 250 ms.
5. **Capped download for everything else** (sponsor, country, drug, networks, histograms): token pagination
   with a minimal `fields=` list, up to `CTGOV_MAX_RECORDS` (about 1 s per 1,000 records). If more trials
   match, `count_method` is `"sample"` and a note says how many were analyzed.
6. **Citations come from the same extraction that bucketed the trial**, so a citation can't contradict its
   count. `verify_citation` re-reads the raw record. Tests run it for every dimension, and the live suite
   checks citations against records fetched independently.
7. **Reproducibility:** every API URL called is returned in `meta.api_requests`.
8. **Custom schema instead of Vega-Lite:** Vega-Lite has no network graph and a very large spec surface.
   The encoding borrows its vocabulary (`field` + `nominal|ordinal|quantitative|temporal`), so mapping to
   Vega-Lite/Recharts is mechanical. The web UI does exactly that mapping for Chart.js and vis-network.
9. **`drug` vs `intervention`:** the registry's `intervention` field includes devices, imaging, biospecimen
   collection, etc. These dominated the first drug networks ("laboratory biomarker analysis" was a top node).
   `drug` keeps only `DRUG`/`BIOLOGICAL` types, and networks drop placebo / standard-of-care arms.
10. **Name normalization is syntactic, not MeSH.** The API's `interventionBrowseModule` MeSH terms looked
    like free canonicalization, but on inspection they drop drugs (capecitabine was missing from a
    4-drug regimen) and mis-map others (an NK-cell therapy was mapped to "Carboxylesterase"). Instead,
    `normalize_intervention` strips brackets, doses, schedules, trailing salt forms and hyphen variants
    ("Gemcitabine Hydrochloride" → "gemcitabine").
11. **pandas isn't used:** the aggregations are `Counter`s over multi-valued fields.

## Limitations / future work

- **Synonyms:** "5-FU", "5-Fluorouracil" and "Fluorouracil" stay separate nodes, as do "Leucovorin" and
  "Folinic acid". Next step: an RxNorm lookup, cached.
- `query.intr` / `query.cond` are full-text searches, so they match synonyms and loosely related trials
  (a `pembrolizumab` search returned a fecal-transplant trial that mentions it).
- Open-ended dimensions are sampled above `CTGOV_MAX_RECORDS`, in API default order, so rankings like
  "top sponsors for cancer" are approximate. The response marks these as `sample`.
- One chart per question. Only enrollment and start year are numeric fields, so trial duration and outcome
  data aren't modeled.
- Comparisons count a trial in every series it matches. Series totals are reported in `meta.notes`.
- Start-year axes show at most 30 years unless `start_year` is given. Earlier and undated trials are
  counted in a note.
- The planner has only been checked on the example questions above. Typos, non-English questions, and
  brand vs. generic drug names haven't been evaluated systematically.
- No caching: identical queries re-hit the API. The web UI loads its chart libraries from a CDN.

## Validation

- **Offline (`pytest`, 91 tests):** schema contracts, OpenAI strict-mode compatibility of the plan schema,
  filter → query-param mapping, field extraction on real API records, aggregation and network pruning,
  HTTP routes, and the full graph against a mocked API that interprets the same filter syntax the code emits.
- **Live (`pytest -m live`, 7 tests):** citations re-verified against records fetched separately.
  Single-valued bucket counts must sum to `totalCount`. Registry enums are compared against
  `/stats/field/values` to catch API drift.
- **End to end:** `scripts/smoke_live.py` runs all six chart types on live data. The web UI was tested
  in a browser on the saved network, comparison, and country examples, and on live histogram,
  drug-network, and clarification questions.

Bugs found during development and fixed:
- The status enum was missing 5 expanded-access values, which silently dropped 18 glioblastoma trials
  from exact counts. Caught by the bucket-sum invariant.
- Drug networks were dominated by non-drug interventions (imaging, biospecimen collection). This led to the `drug` dimension.
- Salt stripping turned "Sodium bicarbonate" into "bicarbonate". Now only trailing salt names are stripped.
- Year bounds relied on where the API sorts undated trials. Now they're excluded explicitly.
- `build` time was missing from `timings_ms`, because the response was assembled before the step finished.

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

**Iterating.** I committed in reviewable steps, did a cleanup pass to remove dead code and noise, and
used the web UI to try questions interactively, including ones the system should refuse or ask about.

GPT (`gpt-5.4`) is used at runtime only as the query planner.
