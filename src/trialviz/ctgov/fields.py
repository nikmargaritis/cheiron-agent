"""Dimension registry: the single place that knows the ClinicalTrials.gov payload shape.

For every ``Dimension`` we record:
  * which API "pieces" to request via ``fields=`` (keeps payloads small),
  * how to extract (bucket value, field path, verbatim excerpt) triples from a study,
  * whether the dimension is *enumerable* — if so, the executor can get exact
    server-side counts with one ``countTotal`` request per bucket instead of
    downloading and counting records.

All AREA[...] names and enum values below were verified against the live API.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from trialviz.schemas.common import Dimension, NumericField, OverallStatus, Phase

Study = dict[str, Any]

# Always requested: needed for citations regardless of the dimension.
BASE_PIECES: tuple[str, ...] = ("NCTId", "BriefTitle")


@dataclass(frozen=True, slots=True)
class FieldValue:
    """One value read from a study. ``value`` is the bucket key, ``excerpt`` is verbatim."""

    value: str
    field: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    key: Dimension
    label: str
    pieces: tuple[str, ...]
    extract: Callable[[Study], list[FieldValue]]
    field_type: Literal["nominal", "ordinal", "temporal"] = "nominal"
    # Enumerable dimensions: fixed bucket values + how to filter the API to one bucket.
    enum_values: tuple[str, ...] | None = None
    area: str | None = None  # Essie AREA name, used as AREA[{area}]{value}
    # Display labels for enum values; free-text dimensions use the commonest verbatim form.
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def enumerable(self) -> bool:
        return self.enum_values is not None and self.area is not None

    @property
    def multi_valued(self) -> bool:
        return self.key in MULTI_VALUED

    def bucket_filter(self, value: str) -> str:
        if self.area is None:
            raise ValueError(f"{self.key} has no exact-count filter")
        return f"AREA[{self.area}]{value}"

    def display(self, value: str) -> str:
        return self.labels.get(value, value)


MULTI_VALUED = {
    Dimension.PHASE, Dimension.COUNTRY, Dimension.INTERVENTION, Dimension.DRUG,
    Dimension.INTERVENTION_TYPE, Dimension.CONDITION,
}


# --------------------------------------------------------------------------- helpers


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _ps(study: Study) -> Study:
    return study.get("protocolSection", {})


def nct_id(study: Study) -> str:
    return _get(_ps(study), "identificationModule", "nctId")


def brief_title(study: Study) -> str | None:
    return _get(_ps(study), "identificationModule", "briefTitle")


def _scalar(path: str, *keys: str, normalize: Callable[[str], str] = str) -> Callable:
    def extract(study: Study) -> list[FieldValue]:
        raw = _get(_ps(study), *keys)
        return [FieldValue(normalize(raw), path, str(raw))] if raw not in (None, "") else []

    return extract


def _list(
    path: str, list_keys: tuple[str, ...], item_key: str | None = None,
    normalize: Callable[[str], str] = str,
) -> Callable:
    """Extract from a list field; de-duplicates so a trial counts once per bucket."""

    def extract(study: Study) -> list[FieldValue]:
        items = _get(_ps(study), *list_keys) or []
        seen: dict[str, FieldValue] = {}
        for item in items:
            raw = item.get(item_key) if item_key else item
            if raw in (None, ""):
                continue
            key = normalize(raw)
            if key:
                seen.setdefault(key, FieldValue(key, path, str(raw)))
        return list(seen.values())

    return extract


def normalize_name(raw: str) -> str:
    """Bucket key for free-text names: whitespace-collapsed, case-folded."""
    return " ".join(raw.split()).casefold()


_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_DOSE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mg|g|mcg|µg|ug|ml|iu|units?|mg/kg|mg/m2|mg/m²|%)(?:/\w+)?\b.*$",
    re.IGNORECASE,
)
_SCHEDULE = re.compile(
    r"\b(?:orally|po|daily|once|twice|days?|weekly|every|q\d+w|cycles?)\b.*$", re.IGNORECASE
)
# Salt forms trail the active ingredient ('Leucovorin Calcium'); only strip them there so
# 'Sodium bicarbonate' survives.
_SALTS = re.compile(
    r"(?:\s+(?:hydrochloride|hcl|dihydrochloride|calcium|sodium|potassium|sulfate|mesylate|"
    r"acetate|citrate|phosphate|tartrate|maleate|besylate|tosylate|succinate|fumarate))+\s*$",
    re.IGNORECASE,
)
_FORM_WORDS = re.compile(
    r"\b(?:injection|infusion|tablets?|capsules?|oral|intravenous|iv|i\.v\.|"
    r"subcutaneous|sc|solution|dose|dosing|regimen)\b\.?",
    re.IGNORECASE,
)


def normalize_intervention(raw: str) -> str:
    """Collapse naming noise so 'Pembrolizumab 50 MG Injection [Keytruda]',
    'Gemcitabine Hydrochloride' and 'nab paclitaxel' share buckets with their plain
    forms. Deliberately syntactic: brackets, doses, schedules, salt forms and dosage-form
    words are stripped; there is no synonym mapping ('5-FU' stays separate from
    'fluorouracil'). The API's MeSH mapping was evaluated and rejected — it drops drugs
    and mis-maps others (see README).
    """
    s = _BRACKETS.sub(" ", raw)
    s = _DOSE.sub(" ", s)
    s = _SCHEDULE.sub(" ", s)
    s = _FORM_WORDS.sub(" ", s)
    s = _SALTS.sub("", s.strip())
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", normalize_name(s))
    s = " ".join(s.split()).strip(" ,;:-/")
    return s or normalize_name(raw)


DRUG_TYPES = {"DRUG", "BIOLOGICAL"}


def _drugs(study: Study) -> list[FieldValue]:
    """Interventions typed DRUG or BIOLOGICAL, normalized by name."""
    items = _get(_ps(study), "armsInterventionsModule", "interventions") or []
    seen: dict[str, FieldValue] = {}
    for item in items:
        raw = item.get("name")
        if item.get("type") in DRUG_TYPES and raw and (key := normalize_intervention(raw)):
            seen.setdefault(
                key,
                FieldValue(key, "protocolSection.armsInterventionsModule.interventions.name", raw),
            )
    return list(seen.values())


def _start_year(study: Study) -> list[FieldValue]:
    raw = _get(_ps(study), "statusModule", "startDateStruct", "date")
    if not raw:
        return []
    return [FieldValue(raw[:4], "protocolSection.statusModule.startDateStruct.date", raw)]


def _titled(values: Iterable[str]) -> dict[str, str]:
    return {v: v.replace("_", " ").capitalize() for v in values}


# --------------------------------------------------------------------------- registry

_PHASE_LABELS = {
    "EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1", "PHASE2": "Phase 2",
    "PHASE3": "Phase 3", "PHASE4": "Phase 4", "NA": "Not applicable",
}
_SPONSOR_CLASSES = (
    "INDUSTRY", "NIH", "FED", "OTHER_GOV", "INDIV", "NETWORK", "OTHER", "AMBIG", "UNKNOWN",
)
_SPONSOR_CLASS_LABELS = {
    "INDUSTRY": "Industry", "NIH": "NIH", "FED": "Other U.S. federal", "OTHER_GOV":
    "Other government", "INDIV": "Individual", "NETWORK": "Network", "OTHER": "Other",
    "AMBIG": "Ambiguous", "UNKNOWN": "Unknown",
}
_INTERVENTION_TYPES = (
    "DRUG", "BIOLOGICAL", "DEVICE", "PROCEDURE", "RADIATION", "BEHAVIORAL", "GENETIC",
    "DIETARY_SUPPLEMENT", "COMBINATION_PRODUCT", "DIAGNOSTIC_TEST", "OTHER",
)
_STUDY_TYPES = ("INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS")

DIMENSIONS: dict[Dimension, DimensionSpec] = {
    spec.key: spec
    for spec in [
        DimensionSpec(
            Dimension.PHASE, "Phase", ("Phase",),
            _list("protocolSection.designModule.phases", ("designModule", "phases")),
            field_type="ordinal", enum_values=tuple(Phase), area="Phase", labels=_PHASE_LABELS,
        ),
        DimensionSpec(
            Dimension.STATUS, "Overall status", ("OverallStatus",),
            _scalar("protocolSection.statusModule.overallStatus", "statusModule", "overallStatus"),
            enum_values=tuple(OverallStatus), area="OverallStatus",
            labels=_titled(OverallStatus),
        ),
        DimensionSpec(
            Dimension.START_YEAR, "Start year", ("StartDate",), _start_year,
            field_type="temporal",
            # Enumerable via RANGE filters, but the bucket set depends on the plan's
            # year bounds, so the fetch node builds those filters itself.
        ),
        DimensionSpec(
            Dimension.STUDY_TYPE, "Study type", ("StudyType",),
            _scalar("protocolSection.designModule.studyType", "designModule", "studyType"),
            enum_values=_STUDY_TYPES, area="StudyType", labels=_titled(_STUDY_TYPES),
        ),
        DimensionSpec(
            Dimension.SPONSOR_CLASS, "Lead sponsor class", ("LeadSponsorClass",),
            _scalar(
                "protocolSection.sponsorCollaboratorsModule.leadSponsor.class",
                "sponsorCollaboratorsModule", "leadSponsor", "class",
            ),
            enum_values=_SPONSOR_CLASSES, area="LeadSponsorClass", labels=_SPONSOR_CLASS_LABELS,
        ),
        DimensionSpec(
            Dimension.INTERVENTION_TYPE, "Intervention type", ("InterventionType",),
            _list(
                "protocolSection.armsInterventionsModule.interventions.type",
                ("armsInterventionsModule", "interventions"), "type",
            ),
            enum_values=_INTERVENTION_TYPES, area="InterventionType",
            labels=_titled(_INTERVENTION_TYPES),
        ),
        DimensionSpec(
            Dimension.SPONSOR, "Lead sponsor", ("LeadSponsorName",),
            _scalar(
                "protocolSection.sponsorCollaboratorsModule.leadSponsor.name",
                "sponsorCollaboratorsModule", "leadSponsor", "name", normalize=normalize_name,
            ),
        ),
        DimensionSpec(
            Dimension.COUNTRY, "Country", ("LocationCountry",),
            _list(
                "protocolSection.contactsLocationsModule.locations.country",
                ("contactsLocationsModule", "locations"), "country",
            ),
        ),
        DimensionSpec(
            Dimension.INTERVENTION, "Intervention", ("InterventionName",),
            _list(
                "protocolSection.armsInterventionsModule.interventions.name",
                ("armsInterventionsModule", "interventions"), "name",
                normalize=normalize_intervention,
            ),
        ),
        DimensionSpec(
            Dimension.DRUG, "Drug / biologic", ("InterventionName", "InterventionType"), _drugs,
        ),
        DimensionSpec(
            Dimension.CONDITION, "Condition", ("Condition",),
            _list(
                "protocolSection.conditionsModule.conditions", ("conditionsModule", "conditions"),
                normalize=normalize_name,
            ),
        ),
    ]
}

assert set(DIMENSIONS) == set(Dimension), "every Dimension needs a registry entry"

# Interventions that dominate co-occurrence networks without being informative.
_NETWORK_NOISE = re.compile(
    r"\bplacebos?\b|^saline$|^normal saline$|standard of care|^best supportive care$|^control$",
    re.IGNORECASE,
)


def is_network_noise(dim: Dimension, key: str) -> bool:
    return dim in (Dimension.INTERVENTION, Dimension.DRUG) and bool(_NETWORK_NOISE.search(key))


# --------------------------------------------------------------------------- numeric fields


@dataclass(frozen=True, slots=True)
class NumericSpec:
    key: NumericField
    label: str
    unit: str
    pieces: tuple[str, ...]
    extract: Callable[[Study], tuple[float, FieldValue] | None]


def _enrollment(study: Study) -> tuple[float, FieldValue] | None:
    v = _get(_ps(study), "designModule", "enrollmentInfo", "count")
    if not isinstance(v, int | float) or isinstance(v, bool):
        return None
    return float(v), FieldValue(str(v), "protocolSection.designModule.enrollmentInfo.count", str(v))


def _start_year_num(study: Study) -> tuple[float, FieldValue] | None:
    vals = _start_year(study)
    return (float(vals[0].value), vals[0]) if vals else None


NUMERIC_FIELDS: dict[NumericField, NumericSpec] = {
    NumericField.ENROLLMENT: NumericSpec(
        NumericField.ENROLLMENT, "Enrollment", "participants", ("EnrollmentCount",), _enrollment,
    ),
    NumericField.START_YEAR: NumericSpec(
        NumericField.START_YEAR, "Start year", "year", ("StartDate",), _start_year_num,
    ),
}


def pieces_for(
    dims: Iterable[Dimension | None] = (), numerics: Iterable[NumericField | None] = ()
) -> list[str]:
    """Minimal ``fields=`` list for a plan (``None`` entries are ignored)."""
    out = list(BASE_PIECES)
    for d in dims:
        if d is not None:
            out.extend(DIMENSIONS[d].pieces)
    for n in numerics:
        if n is not None:
            out.extend(NUMERIC_FIELDS[n].pieces)
    return list(dict.fromkeys(out))
