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
# → http://localhost:8000/docs  (interactive OpenAPI docs)

curl -s localhost:8000/v1/query -H 'content-type: application/json' \
  -d '{"query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab"}'
```

| Command | What it does |
|---|---|
| `pytest` | Offline suite (schemas, query building, extraction, aggregation, full graph against a mocked API) |
| `pytest -m live` | Checks against the real API: citations re-verified against independently fetched records, bucket-sum invariants, enum drift |
| `python scripts/smoke_live.py [out_dir]` | Runs every chart type end to end on live data with scripted plans (no LLM tokens) |
| `python scripts/run_examples.py` | Runs the example queries with the real planner and writes `examples/*.json` |

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
  api/          FastAPI app
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


```jsonc
{
  "status": "ok",                        // ok | needs_clarification | unsupported | no_results
  "visualization": {
    "type": "bar_chart",                 // bar_chart | grouped_bar_chart | time_series |
                                         // histogram | scatter_plot | network_graph
    "title": "Trials by phase",
    "subtitle": "Intervention: Pembrolizumab",
    "orientation": "vertical",
    "encoding": {
      "x": {"field": "phase", "type": "ordinal", "title": "Phase", "sort": ["Early Phase 1", "Phase 1", "..."]},
      "y": {"field": "trial_count", "type": "quantitative", "title": "Number of trials", "unit": "trials"}
    },
    "data": [
      {"phase": "Phase 3", "trial_count": 41,
       "citations": [{"nct_id": "NCT…", "field": "protocolSection.designModule.phases",
                      "excerpt": "PHASE3", "title": "…", "url": "https://clinicaltrials.gov/study/NCT…"}]}
    ]
  },
  "message": null,                       // clarification question / reason when status != ok
  "meta": {
    "source": "clinicaltrials.gov",
    "query": "…",
    "filters": {"intervention": "Pembrolizumab"},
    "plan": { /* the validated QueryPlan */ },
    "total_matching_trials": 1834,
    "trials_analyzed": 1834,
    "count_method": "exact",             // exact | sample
    "time_granularity": null,            // "year" for start-year axes
    "assumptions": [],                   // planner's interpretation choices
    "notes": [],                         // data caveats: sampling, multi-valued fields, pruning
    "api_requests": ["https://clinicaltrials.gov/api/v2/studies?…"],
    "timings_ms": {"plan": 812.4, "validate": 0.1, "fetch": 640.1, "build": 3.2},
    "generated_at": "2026-10-10T18:00:00Z"
  }
}
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
   Vega-Lite/Recharts is mechanical.
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
- Open-ended dimensions are sampled above `CTGOV_MAX_RECORDS`, in API default order.
- Comparisons count a trial in every series it matches. Series totals are reported in `meta.notes`.
- Start-year axes show at most 30 years unless `start_year` is given. Earlier and undated trials are
  counted in a note.
- No caching: identical queries re-hit the API.

## Validation

- Offline: 87 tests, including the full graph against a mocked API that interprets the same
  filter syntax the code emits.
- Live (`pytest -m live`): citations re-verified against records fetched separately. Single-valued
  bucket counts must sum to `totalCount` (this caught 5 missing expanded-access status values that were
  silently dropping 18 glioblastoma trials). Registry enums are compared against `/stats/field/values`
  to catch API drift.

