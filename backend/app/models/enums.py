from enum import Enum


class AnalysisSignal(str, Enum):
    INTERESTING_FOR_ANALYSIS = "interesting_for_analysis"
    NEUTRAL = "neutral"
    ELEVATED_RISK = "elevated_risk"
    INCREASED_RISK = "increased_risk"
    HIGH_RISK = "high_risk"
    INSUFFICIENT_DATA = "insufficient_data"


ANALYSIS_SIGNAL_SQL = (
    "signal in ('interesting_for_analysis', 'neutral', "
    "'elevated_risk', 'increased_risk', 'high_risk', 'insufficient_data')"
)
