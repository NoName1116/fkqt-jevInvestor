import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.operations.alerts import dispatch_outbox
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.runner import ForwardRunner
from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio


def operation_environment(config: ForwardOperationConfig) -> tuple[str, dict[str, str]]:
    settings = Settings(_env_file=config.jev_cwd / ".env")  # pyright: ignore[reportCallIssue]
    url = make_url(settings.database_url)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        path = Path(url.database)
        if not path.is_absolute():
            url = url.set(database=str((config.jev_cwd / path).resolve()))
    value = url.render_as_string(hide_password=False)
    from fkqt_jevinvestor.operations.live_validation import credential_environment

    env = {**credential_environment(config), "JEV_INVESTOR_DATABASE_URL": value,
           "DEEPSEEK_MODEL": config.deepseek_model, "TYPESAFE_MODEL": config.typesafe_model,
           "DEEPSEEK_REASONING_EFFORT": config.deepseek_reasoning_effort}
    for name, secret in (("TYPESAFE_API_KEY", settings.typesafe_api_key), ("DEEPSEEK_API_KEY", settings.deepseek_api_key)):
        if secret is not None:
            env[name] = secret.get_secret_value()
    return value, env


async def execute_forward(args: argparse.Namespace) -> int:
    engine = None
    try:
        config = ForwardOperationConfig.load(Path(args.config))
        url, env = operation_environment(config)
        engine = create_engine(url)
        factory = create_session_factory(engine)
        repository = ForwardOperationRepository(factory)
        if args.forward_command == "recover":
            from fkqt_jevinvestor.operations.recovery import RecoveryService

            service = RecoveryService(factory)
            if args.commit:
                result = await service.commit(config, args.trade_date, args.stage, args.reason,
                    args.expected_stage_hash or "", datetime.now(UTC))
            else:
                result = await service.preview(config, args.trade_date, args.stage)
            print(json.dumps(result, default=str, sort_keys=True))
            return 0 if result.get("allowed", result.get("status") == "RECOVERED") else 2
        if args.forward_command == "diagnostics":
            from fkqt_jevinvestor.operations.evaluation import list_diagnostics

            print(json.dumps(await list_diagnostics(factory, config, args.trade_date), default=str, sort_keys=True))
            return 0
        if args.forward_command == "init":
            state = await PortfolioRepository(factory).create(CreatePortfolio(
                portfolio_id=config.portfolio_id, name=f"Forward {config.portfolio_id}", initial_cash=args.initial_cash))
            print(json.dumps({"portfolio_id": state.portfolio_id, "version": state.version, "tax_policy_version": "GROSS_V1"}, sort_keys=True))
            return 0
        if args.forward_command == "status":
            rows = await repository.status(config.portfolio_id, args.trade_date, config.content_hash())
            print(json.dumps({"portfolio_id": config.portfolio_id, "trade_date": args.trade_date.isoformat(),
                              "tax_policy_version": "GROSS_V1", "stages": [{"stage": row.stage, "status": row.status,
                              "attempts": row.attempts, "error_code": row.error_code, "next_retry_at": row.next_retry_at} for row in rows]}, sort_keys=True))
            return 0
        if args.forward_command == "run":
            result = await ForwardRunner(repository, env=env).run_once(config, args.trade_date, datetime.now(UTC))
            delivery = await dispatch_outbox(config, factory=factory)
            print(json.dumps({**asdict(result), "tax_policy_version": "GROSS_V1", "alerts": asdict(delivery)}, default=str, sort_keys=True))
            return result.exit_code
        if args.forward_command == "validate":
            from fkqt_jevinvestor.operations.live_validation import validate_live

            result = await validate_live(config, mode=args.mode, trade_date=args.trade_date, live=args.live)
            print(result.model_dump_json())
            return 0 if result.status in {"SUCCESS", "LIVE_FORWARD_PENDING"} else 2
        raise ValueError("FORWARD_COMMAND_INVALID")
    except (OSError, RuntimeError, ValueError):
        print("FORWARD_COMMAND_FAILED", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            await engine.dispose()
