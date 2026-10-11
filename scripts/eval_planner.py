"""Score the planner against the eval cases and write the results to evals/.

Only the plan -> validate loop runs, so this makes no ClinicalTrials.gov calls.

Usage:
    python scripts/eval_planner.py                     # all cases once
    python scripts/eval_planner.py --only network      # cases whose id or tag matches
    python scripts/eval_planner.py --repeat 3          # check consistency across runs
    python scripts/eval_planner.py --input-price 1.25 --output-price 10   # USD per 1M tokens
"""

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from trialviz.agent.llm import OpenAIStructuredLLM
from trialviz.agent.state import Deps
from trialviz.config import get_settings
from trialviz.ctgov.client import CTGovClient
from trialviz.evals.cases import CASES
from trialviz.evals.grading import Case, Check, grade, plan_query

OUT = Path(__file__).resolve().parent.parent / "evals" / "planner_results.json"


async def run_case(case: Case, deps: Deps, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.perf_counter()
        try:
            state = await plan_query(case.to_request(), deps)
        except Exception as e:  # one failing call shouldn't sink the whole run
            checks = [Check("error", False, f"{type(e).__name__}: {e}")]
            return {"passed": False, "checks": checks, "seconds": time.perf_counter() - t0}
        checks = grade(case, state["plan"], state["plan_errors"])
        return {
            "passed": all(c.passed for c in checks),
            "checks": checks,
            "attempts": state["plan_attempts"],
            "seconds": time.perf_counter() - t0,
            "plan": state["plan"].model_dump(mode="json"),
        }


def first_failure(runs: list[dict]) -> str:
    for run in runs:
        for c in run["checks"]:
            if not c.passed:
                return f"{c.name}: {c.detail}"
    return ""


def report(cases: list[Case], results: list[list[dict]], usage: Counter, args) -> dict:
    n_runs = len(cases) * args.repeat
    passed_runs = sum(r["passed"] for runs in results for r in runs)

    print(f"\nPlanner eval  model={get_settings().planner_model}  cases={len(cases)}"
          f"  repeat={args.repeat}\n")
    for case, runs in zip(cases, results, strict=True):
        ok = sum(r["passed"] for r in runs)
        mark = "✓" if ok == len(runs) else ("~" if ok else "✗")
        score = f"{ok}/{len(runs)}" if args.repeat > 1 else ""
        print(f"  {mark} {case.id:<36} {score:>5}  {first_failure(runs)}")

    by_check: dict[str, list[bool]] = defaultdict(list)
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for case, runs in zip(cases, results, strict=True):
        for r in runs:
            for c in r["checks"]:
                by_check[c.name].append(c.passed)
            for tag in case.tags:
                by_tag[tag].append(r["passed"])

    def frac(xs: list[bool]) -> str:
        return f"{sum(xs)}/{len(xs)}"

    print(f"\nPassed {passed_runs}/{n_runs} ({passed_runs / n_runs:.0%})\n")
    print("  By check: " + "  ".join(f"{k} {frac(v)}" for k, v in by_check.items()))
    print("  By tag:   " + "  ".join(f"{k} {frac(v)}" for k, v in sorted(by_tag.items())))

    runs = [r for rs in results for r in rs]
    seconds = [r["seconds"] for r in runs]
    retried = sum(r.get("attempts", 1) > 1 for r in runs)
    print(f"  Retries:  {retried} run(s) needed a second plan")
    print(f"  Latency:  median {statistics.median(seconds):.1f}s, max {max(seconds):.1f}s")
    print(f"  Tokens:   {usage['input_tokens']:,} in ({usage['cached_tokens']:,} cached) / "
          f"{usage['output_tokens']:,} out ({usage['reasoning_tokens']:,} reasoning) "
          f"over {usage['calls']} calls")

    cost = None
    if args.input_price is not None and args.output_price is not None:
        cost = (usage["input_tokens"] * args.input_price
                + usage["output_tokens"] * args.output_price) / 1e6
        print(f"  Cost:     ${cost:.2f}")

    return {
        "model": get_settings().planner_model,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "repeat": args.repeat,
        "summary": {
            "passed": passed_runs,
            "total": n_runs,
            "by_check": {k: frac(v) for k, v in by_check.items()},
            "by_tag": {k: frac(v) for k, v in sorted(by_tag.items())},
            "retried_runs": retried,
            "median_seconds": round(statistics.median(seconds), 2),
            "usage": dict(usage),
            "cost_usd": round(cost, 4) if cost is not None else None,
        },
        "cases": [
            {
                "id": case.id,
                "query": case.query,
                "tags": list(case.tags),
                "runs": [
                    {**r, "seconds": round(r["seconds"], 2),
                     "checks": [vars(c) for c in r["checks"]]}
                    for r in runs
                ],
            }
            for case, runs in zip(cases, results, strict=True)
        ],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="run cases whose id or a tag contains this text")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--input-price", type=float, help="USD per 1M input tokens")
    parser.add_argument("--output-price", type=float, help="USD per 1M output tokens")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    cases = [c for c in CASES
             if not args.only or args.only in c.id or any(args.only in t for t in c.tags)]
    if not cases:
        parser.error(f"no cases match {args.only!r}")

    settings = get_settings()
    llm = OpenAIStructuredLLM(settings)
    sem = asyncio.Semaphore(args.concurrency)
    async with CTGovClient(settings) as ctgov:  # required by Deps; never called here
        deps = Deps(settings=settings, llm=llm, ctgov=ctgov)
        flat = await asyncio.gather(
            *(run_case(c, deps, sem) for c in cases for _ in range(args.repeat))
        )
    results = [flat[i:i + args.repeat] for i in range(0, len(flat), args.repeat)]

    out = report(cases, results, llm.usage, args)
    args.out.parent.mkdir(exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
