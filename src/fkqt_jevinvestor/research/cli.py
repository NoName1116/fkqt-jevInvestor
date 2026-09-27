import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, cast

from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.domain.backtest import BacktestConfig, BacktestResult, ExperimentArm
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.providers.deepseek_decision import ResponsesClient
from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider
from fkqt_jevinvestor.research.comparison import compare_results
from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
from fkqt_jevinvestor.research.export import ExportRequest, export_dataset
from fkqt_jevinvestor.research.llm import ResearchDeepSeekProvider
from fkqt_jevinvestor.research.models import ResearchStrategyConfig
from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider


def register_research(subparsers: Any) -> None:
    parser = subparsers.add_parser("research")
    commands = parser.add_subparsers(dest="research_command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--request", type=Path, required=True)
    export.add_argument("--output-root", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    target = commands.add_parser("targets")
    target.add_argument("--manifest", type=Path, required=True)
    target.add_argument(
        "--arm",
        choices=[
            a.value
            for a in (
                ExperimentArm.A_RULE,
                ExperimentArm.B_LLM,
                ExperimentArm.C_JEV_LLM,
                ExperimentArm.D_JEV_DIRECT,
            )
        ],
        required=True,
    )
    target.add_argument("--date", type=date.fromisoformat, required=True)
    target.add_argument("--portfolio", type=Path, required=True)
    target.add_argument("--audit-root", type=Path, required=True)
    target.add_argument("--strategy-config", type=Path)
    target.add_argument("--backtest-config", type=Path)
    target.add_argument("--run-id", default="research-cli")
    mode = target.add_mutually_exclusive_group()
    mode.add_argument("--allow-live-models", action="store_true")
    mode.add_argument("--replay-only", action="store_true")
    compare = commands.add_parser("compare")
    compare.add_argument("--results", nargs=4, type=Path, required=True)
    compare.add_argument("--audit-root", type=Path, required=True)


async def execute_research(args: argparse.Namespace) -> int:
    client = None
    try:
        if args.research_command == "export":
            request = ExportRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
            path = export_dataset(request, args.output_root)
            print(json.dumps({"status": "EXPORTED", "manifest": str(path)}, ensure_ascii=False))
            return 0
        if args.research_command == "compare":
            results = tuple(
                BacktestResult.model_validate_json(p.read_text(encoding="utf-8"))
                for p in args.results
            )
            print(
                json.dumps(
                    compare_results(results, audit_root=args.audit_root).model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        manifest = args.manifest.resolve()
        replay = FrozenReplayDataProvider(DatasetStore(manifest.parent.parent.parent), manifest)
        if args.research_command == "verify":
            print(
                json.dumps(
                    {
                        "status": "VERIFIED",
                        "dataset_hash": replay.manifest.dataset_hash,
                        "days": len(replay.manifest.days),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        arm = ExperimentArm(args.arm)
        if arm is not ExperimentArm.A_RULE and not args.allow_live_models and not args.replay_only:
            raise ValueError("RESEARCH_LIVE_MODEL_OPT_IN_REQUIRED")
        portfolio = PortfolioState.model_validate_json(args.portfolio.read_text(encoding="utf-8"))
        strategy = (
            ResearchStrategyConfig.model_validate_json(
                args.strategy_config.read_text(encoding="utf-8")
            )
            if args.strategy_config
            else ResearchStrategyConfig()
        )
        llm = None
        jev = None
        if args.allow_live_models:
            settings = Settings()
            if args.strategy_config is None:
                strategy = strategy.model_copy(
                    update={
                        "jev_model": settings.typesafe_model,
                        "llm_model": settings.deepseek_model,
                        "llm_base_url": settings.deepseek_base_url,
                        "reasoning_effort": settings.deepseek_reasoning_effort,
                        "timeout_seconds": settings.deepseek_timeout_seconds,
                    }
                )
            if arm in {ExperimentArm.B_LLM, ExperimentArm.C_JEV_LLM}:
                if settings.deepseek_api_key is None:
                    raise ValueError("RESEARCH_DEEPSEEK_KEY_MISSING")
                from openai import AsyncOpenAI

                client = AsyncOpenAI(
                    api_key=settings.deepseek_api_key.get_secret_value(),
                    base_url=strategy.llm_base_url,
                    timeout=strategy.timeout_seconds,
                    max_retries=0,
                )
                llm = ResearchDeepSeekProvider(cast(ResponsesClient, client), strategy)
            if arm in {ExperimentArm.C_JEV_LLM, ExperimentArm.D_JEV_DIRECT}:
                if settings.typesafe_api_key is None:
                    raise ValueError("RESEARCH_TYPESAFE_KEY_MISSING")
                jev = TypeSafeJevMarketProvider.from_api_key(
                    settings.typesafe_api_key, strategy.jev_model, strategy.jev_version
                )
        config = (
            BacktestConfig.model_validate_json(args.backtest_config.read_text(encoding="utf-8"))
            if args.backtest_config
            else BacktestConfig(
                run_id=args.run_id,
                experiment_arm=arm,
                start_date=args.date,
                end_date=args.date,
                warmup_trading_days=0,
                initial_cash=portfolio.total_equity,
                benchmark_symbol="000300.SH",
                dataset_id=replay.manifest.dataset_id,
                dataset_hash=replay.manifest.dataset_hash,
                execution_policy_version="execution-v1",
                sizing_version="position-sizing-v1",
            )
        )
        if config.experiment_arm is not arm:
            raise ValueError("RESEARCH_ARM_CONFIG_MISMATCH")
        day = (await replay.load_day(args.date)).decision_view()
        targets = await ExperimentTargetProvider(
            replay, args.audit_root, strategy, llm=llm, jev=jev, replay_only=args.replay_only
        ).build_targets(config, day, portfolio)
        print(json.dumps(targets.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        message = str(exc)
        print(
            message if message.isupper() and len(message) <= 150 else type(exc).__name__,
            file=sys.stderr,
        )
        return 2
    finally:
        if client is not None:
            await client.close()
