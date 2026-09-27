from datetime import date
from typing import Any

from fkqt_jevinvestor.domain.market_features import DailyBar, MarketSnapshot
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.services.forward_labels import (
    PNL_LABEL_ROUND_TRIP_COST_V1,
    build_forward_pnl_labels,
)


def mature_labels(base: MarketSnapshot, symbol: str, snapshots: dict[date, list[MarketSnapshot]],
                  to_date: date) -> dict[str, Any]:
    chain: list[MarketSnapshot] = []
    next_day = base.next_trade_date
    for _ in range(5):
        if next_day > to_date:
            return {"status": "PENDING_LABELS", "reason": "FIVE_SESSION_WINDOW_NOT_MATURE"}
        options = snapshots.get(next_day, [])
        if len(options) != 1:
            return {"status": "LABEL_UNAVAILABLE", "reason": "FUTURE_SNAPSHOT_MISSING_OR_AMBIGUOUS"}
        snapshot = options[0]
        chain.append(snapshot)
        next_day = snapshot.next_trade_date
    bars: list[DailyBar] = []
    for snapshot in chain:
        matches = [bar for bar in chain[-1].daily_bars.get(symbol, ()) if bar.trade_date == snapshot.decision_date]
        if len(matches) != 1 or matches[0].symbol != symbol:
            return {"status": "LABEL_UNAVAILABLE", "reason": "FUTURE_BAR_MISSING_OR_AMBIGUOUS"}
        bars.append(matches[0])
    state = chain[0].security_states.get(symbol)
    if state is None or state.symbol != symbol:
        return {"status": "LABEL_UNAVAILABLE", "reason": "D1_SECURITY_STATE_MISSING"}
    labels = build_forward_pnl_labels(decision_date=base.decision_date, future_bars=tuple(bars),
        expected_trade_dates=tuple(s.decision_date for s in chain), d1_state=state,
        round_trip_cost=PNL_LABEL_ROUND_TRIP_COST_V1,
        source_snapshot_hash=sha256_json([s.content_hash for s in chain]),
        calendar_snapshot_hash=sha256_json([{"day": s.decision_date.isoformat(), "next": s.next_trade_date.isoformat(),
                                            "complete": s.calendar_complete_through.isoformat()} for s in [base, *chain]]))
    return labels.model_dump(mode="json")
