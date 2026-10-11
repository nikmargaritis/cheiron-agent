"""Deep citations: every datum links back to verbatim values in source records.

Citations are built from the same ``FieldValue`` that put the study into the bucket,
so a citation can't disagree with the count it supports. ``verify_citation`` re-reads
the raw study as a belt-and-braces check (used in tests and optionally at runtime).
"""

from typing import Any

from trialviz.ctgov.fields import FieldValue, brief_title, nct_id
from trialviz.schemas.response import Citation

STUDY_URL = "https://clinicaltrials.gov/study/{}"


def make_citation(study: dict[str, Any], fv: FieldValue) -> Citation:
    nid = nct_id(study)
    return Citation(
        nct_id=nid,
        field=fv.field,
        excerpt=fv.excerpt,
        title=brief_title(study),
        url=STUDY_URL.format(nid),
    )


def citations_for(
    evidence: list[tuple[dict[str, Any], FieldValue]], limit: int
) -> list[Citation]:
    """First ``limit`` citations, de-duplicated by NCT ID."""
    out: list[Citation] = []
    seen: set[str] = set()
    for study, fv in evidence:
        nid = nct_id(study)
        if nid in seen:
            continue
        seen.add(nid)
        out.append(make_citation(study, fv))
        if len(out) >= limit:
            break
    return out


def _walk(node: Any, path: list[str]) -> list[Any]:
    """Resolve a dotted path, fanning out over lists (locations.country -> all countries)."""
    if not path:
        return node if isinstance(node, list) else [node]
    if isinstance(node, list):
        return [v for item in node for v in _walk(item, path)]
    if isinstance(node, dict) and path[0] in node:
        return _walk(node[path[0]], path[1:])
    return []


def verify_citation(study: dict[str, Any], c: Citation) -> bool:
    """True iff ``c.excerpt`` is literally the value at ``c.field`` in ``study``."""
    return nct_id(study) == c.nct_id and c.excerpt in {
        _text(v) for v in _walk(study, c.field.split("."))
    }


def _text(v: Any) -> str:
    """Values as they appear in the API's JSON (``true``, not Python's ``True``)."""
    return ("true" if v else "false") if isinstance(v, bool) else str(v)
