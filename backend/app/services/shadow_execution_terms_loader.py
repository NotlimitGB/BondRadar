"""One narrow Security Master SELECT; no source acquisition or legacy fallbacks."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.services.shadow_execution_planner import validate_strategy, make_terms, make_terms_batch


class ShadowExecutionTermsLoader:
    def __init__(self, db: Session):
        self.db = db

    def build(self, strategy):
        validate_strategy(strategy)
        ids = tuple(p.bond_id for p in strategy.positions)
        with self.db.no_autoflush:
            rows = []
            if ids:
                model = BondSecurityMasterProfile
                rows = self.db.execute(select(model.id,model.bond_id,model.contract_version,
                    model.currency_state,model.currency_code,model.nominal_state,model.nominal_value,
                    model.lot_size_state,model.lot_size,model.trading_board_state,model.trading_board)
                    .where(model.bond_id.in_(ids)).order_by(model.bond_id)).mappings().all()
            by_id = {}
            for row in rows:
                if type(row["bond_id"]) is not int or row["bond_id"] not in ids or row["bond_id"] in by_id:
                    raise ValueError("Contradictory Security Master projection")
                by_id[row["bond_id"]] = row
            return make_terms_batch(strategy,tuple(make_terms(p,by_id.get(p.bond_id)) for p in strategy.positions))
