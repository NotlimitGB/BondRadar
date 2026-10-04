"""Read-only delegation to the single-capital Task304 Genesis authority."""
from app.services.shadow_experiment_genesis_service import ShadowExperimentGenesisService
from app.services.shadow_scale_experiment_evaluator import validate_inputs, make_case, assemble_genesis


class ShadowScaleExperimentGenesisService:
    def __init__(self, db):
        self.db = db

    def build(self, *, cases, scale_policy):
        ordered = validate_inputs(cases, scale_policy)
        results = []
        with self.db.no_autoflush:
            for case in ordered:
                experiment = ShadowExperimentGenesisService(self.db).build(
                    reviewed_genesis_plan=case.reviewed_strategy_genesis,
                    shadow_execution=case.source_shadow_execution,
                    policy=scale_policy.experiment_policy)
                results.append(make_case(case, experiment))
        return assemble_genesis(tuple(results), scale_policy)
