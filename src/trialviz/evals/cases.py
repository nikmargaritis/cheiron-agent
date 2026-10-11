"""Planner eval cases: questions with the plan we expect for each.

Tags group cases in the report: trend, distribution, comparison, numeric, network,
caller (structured request fields), filters (tricky extraction), clarify, unsupported.
"""

from datetime import date

from trialviz.evals.grading import Case
from trialviz.schemas.common import Dimension as D
from trialviz.schemas.common import NumericField as N
from trialviz.schemas.common import OverallStatus as S
from trialviz.schemas.common import Phase as P
from trialviz.schemas.plan import AnalysisKind as A
from trialviz.schemas.plan import PlanStatus

YEAR = date.today().year
ALZHEIMERS = ("alzheimer's disease", "alzheimer disease", "alzheimer's", "alzheimers")
T2D = ("type 2 diabetes", "type 2 diabetes mellitus", "diabetes mellitus, type 2")
USA = ("united states", "usa", "us", "united states of america")

CASES: list[Case] = [
    # Time trends
    Case("pembro_trend_since_2015",
         "How has the number of pembrolizumab trials changed since 2015?",
         tags=("trend",), analysis=A.TIME_TREND, group_by=D.START_YEAR,
         filters={"intervention": "pembrolizumab", "start_year_min": 2015}),
    Case("alzheimers_trend_range",
         "How many Alzheimer's disease trials started each year between 2010 and 2020?",
         tags=("trend",), analysis=A.TIME_TREND, group_by=D.START_YEAR,
         filters={"condition": ALZHEIMERS, "start_year_min": 2010, "start_year_max": 2020}),
    Case("obesity_trend_last_decade",
         "How many obesity trials started each year over the last decade?",
         tags=("trend", "filters"), analysis=A.TIME_TREND, group_by=D.START_YEAR,
         # Up to this year, or the last 10 complete years; both are reasonable readings.
         filters={"condition": "obesity", "start_year_min": (YEAR - 10, YEAR - 9),
                  "start_year_max": (YEAR, YEAR - 1, None)}),
    Case("hiv_phases_before_2000",
         "What phases were HIV trials that started before 2000 in?",
         tags=("distribution", "filters"), analysis=A.DISTRIBUTION, group_by=D.PHASE,
         filters={"condition": ("hiv", "hiv infections", "hiv infection"),
                  "start_year_max": 1999}),

    # Distributions
    Case("breast_cancer_phases",
         "How are breast cancer trials distributed across phases?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.PHASE,
         filters={"condition": "breast cancer"}),
    Case("t2d_recruiting_countries",
         "Which countries have the most recruiting trials for type 2 diabetes?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.COUNTRY,
         filters={"condition": T2D, "statuses": [S.RECRUITING]}),
    Case("lung_cancer_top_sponsors",
         "Who are the top 10 sponsors of lung cancer trials?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.SPONSOR,
         filters={"condition": "lung cancer"}, top_n=10),
    Case("covid_sponsor_class",
         "What share of COVID-19 trials are funded by industry versus academia or government?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.SPONSOR_CLASS,
         filters={"condition": ("covid-19", "covid 19", "covid")}),
    Case("melanoma_drugs",
         "What drugs are most commonly tested in melanoma trials?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.DRUG,
         filters={"condition": "melanoma"}),
    Case("alzheimers_phase3_status",
         "What is the status breakdown of phase 3 Alzheimer's trials?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.STATUS,
         filters={"condition": ALZHEIMERS, "phases": [P.PHASE3]}),
    Case("depression_intervention_types",
         "What types of interventions are used in depression trials?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.INTERVENTION_TYPE,
         filters={"condition": ("depression", "major depressive disorder")}),
    Case("pfizer_conditions",
         "Which conditions does Pfizer run the most trials for?",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.CONDITION,
         filters={"sponsor": "pfizer"}),
    Case("germany_recruiting_phases",
         "Phase breakdown of currently recruiting trials in Germany",
         tags=("distribution",), analysis=A.DISTRIBUTION, group_by=D.PHASE,
         filters={"country": "germany", "statuses": [S.RECRUITING]}),
    Case("countries_no_filters",
         "Which countries run the most clinical trials?",
         tags=("distribution", "filters"), analysis=A.DISTRIBUTION, group_by=D.COUNTRY),

    # Comparisons
    Case("nivolumab_vs_ipilimumab_phases",
         "Compare phases for trials involving nivolumab vs ipilimumab.",
         tags=("comparison",), analysis=A.COMPARISON, group_by=D.PHASE,
         series=[{"intervention": "nivolumab"}, {"intervention": "ipilimumab"}]),
    Case("breast_vs_prostate_trend",
         "Compare how many breast cancer and prostate cancer trials started each year since 2010",
         tags=("comparison", "trend"), analysis=A.COMPARISON, group_by=D.START_YEAR,
         filters={"start_year_min": 2010},
         series=[{"condition": "breast cancer"}, {"condition": "prostate cancer"}]),
    Case("lung_cancer_us_vs_china_status",
         "Compare trial statuses for lung cancer trials in the United States vs China",
         tags=("comparison",), analysis=A.COMPARISON, group_by=D.STATUS,
         filters={"condition": "lung cancer"},
         series=[{"country": USA}, {"country": "china"}]),

    # Numeric charts
    Case("diabetes_phase3_enrollment_hist",
         "What is the distribution of enrollment sizes in phase 3 diabetes trials?",
         tags=("numeric",), analysis=A.HISTOGRAM, numeric={N.ENROLLMENT},
         filters={"condition": "diabetes", "phases": [P.PHASE3]}),
    Case("ms_enrollment_vs_year_scatter",
         "Plot enrollment against start year for multiple sclerosis trials",
         tags=("numeric",), analysis=A.SCATTER, numeric={N.ENROLLMENT, N.START_YEAR},
         filters={"condition": "multiple sclerosis"}),

    # Networks
    Case("glioblastoma_sponsor_drug_network",
         "Show a network of sponsors and drugs for glioblastoma trials.",
         tags=("network",), analysis=A.NETWORK, network={D.SPONSOR, D.DRUG},
         filters={"condition": "glioblastoma"}),
    Case("lung_cancer_drug_combinations",
         "Which drugs are tested together in lung cancer combination trials? Show it as a network.",
         tags=("network",), analysis=A.NETWORK, network={D.DRUG},
         filters={"condition": "lung cancer"}, ignore=frozenset({"keywords"})),
    Case("malaria_sponsor_country_network",
         "Show how sponsors and countries are connected in malaria trials",
         tags=("network",), analysis=A.NETWORK, network={D.SPONSOR, D.COUNTRY},
         filters={"condition": "malaria"}),

    # Structured request fields (caller fields must end up in the filters)
    Case("caller_drug_trend",
         "How has the number of trials for this drug changed over time?",
         tags=("caller", "trend"), request={"drug_name": "Pembrolizumab"},
         analysis=A.TIME_TREND, group_by=D.START_YEAR,
         filters={"intervention": "pembrolizumab"}),
    Case("caller_condition_phase_sponsors",
         "Which sponsors run the most trials?",
         tags=("caller", "distribution"),
         request={"condition": "asthma", "trial_phase": ["PHASE2"]},
         analysis=A.DISTRIBUTION, group_by=D.SPONSOR,
         filters={"condition": "asthma", "phases": [P.PHASE2]}),

    # Tricky extraction
    Case("brand_name_kept_verbatim",
         "How are Keytruda trials distributed by phase?",
         tags=("filters",), analysis=A.DISTRIBUTION, group_by=D.PHASE,
         filters={"intervention": "keytruda"}),

    # Needs clarification
    Case("clarify_this_drug",
         "How has the number of trials for this drug changed over time?",
         tags=("clarify",), status=PlanStatus.NEEDS_CLARIFICATION),
    Case("clarify_compare_them",
         "Compare them by phase",
         tags=("clarify",), status=PlanStatus.NEEDS_CLARIFICATION),
    Case("clarify_which_disease",
         "Which sponsors are most active for that disease?",
         tags=("clarify",), status=PlanStatus.NEEDS_CLARIFICATION),

    # Unsupported (not answerable from registry metadata)
    Case("unsupported_efficacy",
         "Does Keytruda work better than chemotherapy?",
         tags=("unsupported",), status=PlanStatus.UNSUPPORTED),
    Case("unsupported_survival",
         "What is the 5-year survival rate in pancreatic cancer trials?",
         tags=("unsupported",), status=PlanStatus.UNSUPPORTED),
    Case("unsupported_medical_advice",
         "Should I join a clinical trial for my diabetes?",
         tags=("unsupported",), status=PlanStatus.UNSUPPORTED),
    Case("unsupported_cost",
         "How much does it cost to run a phase 3 oncology trial?",
         tags=("unsupported",), status=PlanStatus.UNSUPPORTED),
]
