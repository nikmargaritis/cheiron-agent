"""Planner evals: how often the LLM turns a question into the right QueryPlan.

Only the plan -> validate loop runs (no ClinicalTrials.gov calls), so a full run is
cheap. Run with ``python scripts/eval_planner.py``.
"""
