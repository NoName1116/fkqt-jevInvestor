from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.persistence.market_repository import (
    MarketSnapshotNotFound,
    MarketSnapshotRepository,
)
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline


class FreezeMarketSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: tuple[str, ...] = Field(min_length=1)
    decision_date: date
    decision_cutoff: datetime
    lookback_trading_days: int = Field(default=61, gt=0)


def build_market_router(
    repository: MarketSnapshotRepository,
    pipeline: MarketPipeline | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/market-snapshots", tags=["market"])

    @router.post("/freeze", status_code=201)
    async def freeze(request: FreezeMarketSnapshotRequest) -> dict[str, object]:
        if pipeline is None:
            raise HTTPException(status_code=503, detail={"error_code": "MARKET_PROVIDER_UNAVAILABLE"})
        snapshot, features = await pipeline.freeze_and_compute(
            request.symbols,
            request.decision_date,
            request.decision_cutoff,
            request.lookback_trading_days,
        )
        return {
            "snapshot_id": snapshot.snapshot_id,
            "content_hash": snapshot.content_hash,
            "decision_date": snapshot.decision_date,
            "next_trade_date": snapshot.next_trade_date,
            "feature_symbols": sorted(features),
        }

    @router.get("/{snapshot_id}")
    async def get_snapshot(snapshot_id: str) -> dict[str, object]:
        try:
            stored = await repository.load_run_inputs_by_id(snapshot_id)
        except MarketSnapshotNotFound as exc:
            raise HTTPException(status_code=404, detail={"error_code": str(exc)}) from exc
        return stored.reference.model_dump(mode="json")

    @router.get("/{snapshot_id}/features")
    async def get_features(snapshot_id: str) -> dict[str, object]:
        try:
            stored = await repository.load_run_inputs_by_id(snapshot_id)
        except MarketSnapshotNotFound as exc:
            raise HTTPException(status_code=404, detail={"error_code": str(exc)}) from exc
        return {
            symbol: feature.model_dump(mode="json")
            for symbol, feature in stored.features.items()
        }

    return router
