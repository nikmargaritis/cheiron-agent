"""Enums shared by the request, plan, and response schemas.

Values mirror the ClinicalTrials.gov v2 API enums exactly so they can be passed
through to the API without translation.
"""

from enum import StrEnum


class Phase(StrEnum):
    EARLY_PHASE1 = "EARLY_PHASE1"
    PHASE1 = "PHASE1"
    PHASE2 = "PHASE2"
    PHASE3 = "PHASE3"
    PHASE4 = "PHASE4"
    NA = "NA"


class OverallStatus(StrEnum):
    NOT_YET_RECRUITING = "NOT_YET_RECRUITING"
    RECRUITING = "RECRUITING"
    ENROLLING_BY_INVITATION = "ENROLLING_BY_INVITATION"
    ACTIVE_NOT_RECRUITING = "ACTIVE_NOT_RECRUITING"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"
    # Expanded-access records use their own status vocabulary.
    AVAILABLE = "AVAILABLE"
    NO_LONGER_AVAILABLE = "NO_LONGER_AVAILABLE"
    TEMPORARILY_NOT_AVAILABLE = "TEMPORARILY_NOT_AVAILABLE"
    APPROVED_FOR_MARKETING = "APPROVED_FOR_MARKETING"
    WITHHELD = "WITHHELD"


class Dimension(StrEnum):
    """Categorical axes the system knows how to group trials by.

    Each value has an entry in ``trialviz.ctgov.fields.DIMENSIONS`` describing where
    it lives in the API payload and how to extract it. Adding a dimension = adding an
    enum member + a registry entry; nothing else in the pipeline changes.
    """

    PHASE = "phase"
    STATUS = "status"
    START_YEAR = "start_year"
    COUNTRY = "country"
    SPONSOR = "sponsor"
    SPONSOR_CLASS = "sponsor_class"
    INTERVENTION = "intervention"  # any intervention type (drugs, devices, procedures, ...)
    DRUG = "drug"  # interventions typed DRUG or BIOLOGICAL only
    INTERVENTION_TYPE = "intervention_type"
    CONDITION = "condition"
    STUDY_TYPE = "study_type"


class NumericField(StrEnum):
    """Per-trial numeric values, used by histograms and scatter plots."""

    ENROLLMENT = "enrollment"
    START_YEAR = "start_year"
