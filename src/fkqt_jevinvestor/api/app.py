from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.api.routes.market import build_market_router
from fkqt_jevinvestor.api.routes.portfolios import PortfolioService, build_portfolio_router
from fkqt_jevinvestor.config import Settings, get_settings
from fkqt_jevinvestor.domain.enums import ProviderStatus
from fkqt_jevinvestor.domain.market import MarketExecutionProvider
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.providers.base import ProviderHealth
from fkqt_jevinvestor.providers.jev import JevSemanticFactorProvider
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline


class HealthProvider(Protocol):
    async def health(self) -> ProviderHealth:
        ...


DatabaseProbe = Callable[[], Awaitable[bool]]


def _database_probe(database_url: str) -> DatabaseProbe:
    async def probe() -> bool:
        engine = create_engine(database_url)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        finally:
            await engine.dispose()

    return probe


def create_app(
    settings: Settings | None = None,
    provider: HealthProvider | None = None,
    database_probe: DatabaseProbe | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    market_execution_provider: MarketExecutionProvider | None = None,
    market_pipeline: MarketPipeline | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    active_database_probe = database_probe or _database_probe(active_settings.database_url)
    active_provider = provider
    if active_provider is None and active_settings.typesafe_api_key is not None:
        api_key = active_settings.typesafe_api_key.get_secret_value().strip()
        if api_key:
            active_provider = JevSemanticFactorProvider.from_api_key(
                active_settings.typesafe_api_key,
                active_settings.typesafe_model,
            )

    app = FastAPI(title="fkqt-jevInvestor", version="0.1.0")
    active_session_factory = session_factory
    if active_session_factory is None:
        active_session_factory = create_session_factory(create_engine(active_settings.database_url))
    portfolio_service = PortfolioService(
        PortfolioRepository(active_session_factory),
        market_execution_provider,
    )
    app.include_router(
        build_portfolio_router(
            portfolio_service,
            include_fixture_routes=active_settings.environment in {"local", "test"},
        )
    )
    app.include_router(
        build_market_router(MarketSnapshotRepository(active_session_factory), market_pipeline)
    )

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            {"error_code": "INTERNAL_ERROR"},
            status_code=500,
        )

    @app.get("/api/v1/health/live")
    async def live() -> dict[str, str]:
        return {"status": "UP", "service": "fkqt-jevinvestor"}

    @app.get("/api/v1/health/ready")
    async def ready() -> dict[str, object]:
        try:
            database_available = await active_database_probe()
        except Exception:  # noqa: BLE001 - 健康边界必须屏蔽驱动异常详情
            database_available = False
        database_status = (
            {"status": "AVAILABLE"}
            if database_available
            else {"status": "UNAVAILABLE", "error_code": "DATABASE_UNAVAILABLE"}
        )

        if active_provider is None:
            jev_status = {"status": "UNAVAILABLE"}
        else:
            try:
                health = await active_provider.health()
                jev_status = {"status": health.status.value}
            except Exception:  # noqa: BLE001 - 健康边界必须屏蔽 SDK 异常详情
                jev_status = {"status": "UNAVAILABLE", "error_code": "JEV_UNAVAILABLE"}

        if not database_available:
            status = "DOWN"
        elif jev_status["status"] != ProviderStatus.AVAILABLE.value:
            status = "DEGRADED"
        else:
            status = "UP"

        return {
            "status": status,
            "providers": {"jev": jev_status, "database": database_status},
        }

    return app
