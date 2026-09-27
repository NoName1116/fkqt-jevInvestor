import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.jev_market import JevEvaluationCommand
from fkqt_jevinvestor.domain.market_features import MarketSnapshot
from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.evaluation_labels import mature_labels
from fkqt_jevinvestor.operations.evaluation_metrics import (
    nav_metrics,
    number,
    probability_metrics,
    usage_cost,
)
from fkqt_jevinvestor.operations.run_identity import read_run_identities
from fkqt_jevinvestor.persistence.models import (
    DecisionAttemptRecord,
    DecisionEvaluationRecord,
    DecisionRunLinkRecord,
    ForwardRecoveryRecord,
    ForwardStageRecord,
    JevAttemptRecord,
    JevEvaluationRecord,
    JevQuestionResultRecord,
    JevRunLinkRecord,
    MarketSnapshotRecord,
    NavRecord,
    PortfolioRecord,
    PortfolioSnapshotRecord,
    PositionSizingRunRecord,
    VirtualFillRecord,
    VirtualOrderRecord,
)
from fkqt_jevinvestor.providers.diagnostics import normalize_usage, sanitize_diagnostic
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS

SessionFactory = async_sessionmaker[AsyncSession]
PNL_QUESTIONS = ("next_session_pnl", "profitability_5d", "drawdown_risk_5d", "payoff_asymmetry_5d")


def _mode_for_day(data: dict[str, Any], day: date) -> str:
    modes = {row.outcome_json.get("_run_mode", "UNVERIFIED") for row in data["stages"]
             if row.trade_date == day and row.stage == "CALENDAR" and row.status == "SUCCESS"}
    allowed = {"LIVE_FORWARD", "REAL_SOURCE_REPLAY"}
    return next(iter(modes)) if len(modes) == 1 and modes <= allowed else "UNVERIFIED"


async def _records(factory: SessionFactory, config: ForwardOperationConfig, first: date, last: date) -> dict[str, Any]:
    all_identities, invalid_identity_count = read_run_identities(config.data_root, config.portfolio_id)
    identities = [identity for identity in all_identities if first.isoformat() <= identity["date"] <= last.isoformat()]
    async with factory() as session, session.begin():
        decisions = list((await session.scalars(select(DecisionEvaluationRecord).where(
            DecisionEvaluationRecord.portfolio_id == config.portfolio_id,
            DecisionEvaluationRecord.decision_date.between(first, last)))).all())
        sizing = list((await session.scalars(select(PositionSizingRunRecord).where(
            PositionSizingRunRecord.portfolio_id == config.portfolio_id,
            PositionSizingRunRecord.decision_date.between(first, last)))).all())
        stages = list((await session.scalars(select(ForwardStageRecord).where(
            ForwardStageRecord.portfolio_id == config.portfolio_id, ForwardStageRecord.trade_date.between(first, last)))).all())
        run_ids = {row.run_id for row in sizing}
        run_ids.update(identity["run_id"] for identity in identities)
        run_ids.update((await session.scalars(select(DecisionRunLinkRecord.run_id).where(
            DecisionRunLinkRecord.evaluation_id.in_([row.id for row in decisions])))).all())
        run_ids.update(row.outcome_json["run_id"] for row in stages if row.stage == "DECIDE"
                       and isinstance(row.outcome_json.get("run_id"), str))
        jev_ids = {value for row in decisions for value in (row.universe_jev_evaluation_id, row.symbol_jev_evaluation_id)}
        jev_ids.update((await session.scalars(select(JevRunLinkRecord.evaluation_id).where(JevRunLinkRecord.run_id.in_(run_ids)))).all())
        return {"decisions": decisions, "sizing": sizing, "stages": stages, "run_ids": sorted(run_ids),
            "run_identities": identities, "invalid_identity_count": invalid_identity_count,
            "jev": list((await session.scalars(select(JevEvaluationRecord).where(JevEvaluationRecord.id.in_(jev_ids)))).all()),
            "questions": list((await session.scalars(select(JevQuestionResultRecord).where(JevQuestionResultRecord.evaluation_id.in_(jev_ids)))).all()),
            "attempts": [*(await session.scalars(select(JevAttemptRecord).where(JevAttemptRecord.run_id.in_(run_ids)))).all(),
                         *(await session.scalars(select(DecisionAttemptRecord).where(DecisionAttemptRecord.run_id.in_(run_ids)))).all()],
            "snapshots": list((await session.scalars(select(MarketSnapshotRecord).where(MarketSnapshotRecord.decision_date <= last))).all()),
            "nav": list((await session.scalars(select(NavRecord).where(NavRecord.portfolio_id == config.portfolio_id,
                    NavRecord.valuation_date <= last).order_by(NavRecord.valuation_date))).all()),
            "portfolio": await session.get(PortfolioRecord, config.portfolio_id),
            "portfolio_snapshots": list((await session.scalars(select(PortfolioSnapshotRecord).where(
                PortfolioSnapshotRecord.portfolio_id == config.portfolio_id,
                PortfolioSnapshotRecord.snapshot_type == "POST_EXECUTION").order_by(PortfolioSnapshotRecord.as_of))).all()),
            "fills": list((await session.scalars(select(VirtualFillRecord).join(VirtualOrderRecord, VirtualOrderRecord.id == VirtualFillRecord.order_id).where(VirtualOrderRecord.portfolio_id == config.portfolio_id,
                  VirtualFillRecord.trade_date.between(first, last)))).all()),
            "recoveries": list((await session.scalars(select(ForwardRecoveryRecord).where(
                ForwardRecoveryRecord.portfolio_id == config.portfolio_id, ForwardRecoveryRecord.trade_date.between(first, last)))).all())}


def _load_snapshots(records: list[MarketSnapshotRecord]) -> tuple[dict[str, MarketSnapshot], list[str]]:
    loaded: dict[str, MarketSnapshot] = {}
    unavailable: list[str] = []
    for record in records:
        try:
            snapshot = MarketSnapshotStore(Path(record.storage_path).parent.parent).load(record.decision_date, record.content_hash)
            if snapshot.decision_date != record.decision_date or snapshot.universe_snapshot_hash != record.universe_snapshot_hash:
                raise ValueError("REPORT_SNAPSHOT_IDENTITY_MISMATCH")
            loaded[record.content_hash] = snapshot
        except (OSError, RuntimeError, ValueError):
            unavailable.append(record.content_hash)
    return loaded, sorted(unavailable)


def _providers(data: dict[str, Any], pricing: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    evaluations = {row.id: row for row in [*data["jev"], *data["decisions"]]}
    groups: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    formal_groups: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    for row in evaluations.values():
        scope = row.scope if isinstance(row, JevEvaluationRecord) else "LLM"
        formal_groups[(scope, row.provider_name, row.model_id, row.provider_version, _mode_for_day(data, row.decision_date))].append(row)
    for row in data["attempts"]:
        evaluation = evaluations.get(row.evaluation_id)
        scope = evaluation.scope if isinstance(evaluation, JevEvaluationRecord) else "LLM"
        groups[(scope, row.provider_name, row.model_id, row.provider_version, _mode_for_day(data, evaluation.decision_date) if evaluation else "UNVERIFIED")].append(row)
    result: list[dict[str, Any]] = []
    for scope, provider, model, version, mode in sorted(set(groups) | set(formal_groups)):
        rows = groups[(scope, provider, model, version, mode)]
        formal = formal_groups[(scope, provider, model, version, mode)]
        costs: list[dict[str, Any]] = []
        for row in rows:
            day = evaluations[row.evaluation_id].decision_date.isoformat()
            identity = {"provider": provider, "model": model, "version": version, "date": day}
            matching = [price for price in pricing or [] if all(price.get(key) == identity[key] for key in ("provider", "model", "version"))
                        and price.get("effective_from", "") <= day <= price.get("effective_to", "")]
            if len(matching) > 1:
                raise ValueError("REPORT_PRICING_OVERLAP")
            costs.append(usage_cost(normalize_usage(row.usage_json), identity, matching[0] if matching else None))
        known = [cost for cost in costs if cost["estimate"] is not None]
        currencies = sorted({cost["currency"] for cost in known})
        result.append({"scope": scope, "provider": provider, "model": model, "version": version, "mode": mode,
            "attempt_count": len(rows), "formal_result_count": len(formal),
            "formal_status_counts": dict(sorted(Counter(row.status for row in formal).items())),
            "local_skip_count": sum(row.status == "DATA_UNAVAILABLE" for row in rows),
            "status_counts": dict(sorted(Counter(row.status for row in rows).items())),
            "error_counts": dict(sorted(Counter(row.error_code for row in rows if row.error_code).items())),
            "latency_ms_total": sum(row.latency_ms for row in rows),
            "diagnostic_count": sum(row.diagnostic_json is not None for row in rows),
            "usage_count": sum(normalize_usage(row.usage_json) is not None for row in rows),
            "cost_coverage": number(Decimal(len(known)) / len(rows)) if rows else None,
            "estimated_cost": number(sum((Decimal(cost["estimate"]) for cost in known), Decimal(0))) if rows and len(known) == len(rows) and len(currencies) == 1 else None,
            "known_partial_cost_by_currency": {currency: number(sum((Decimal(cost["estimate"]) for cost in known if cost["currency"] == currency), Decimal(0))) for currency in currencies},
            "currency": currencies[0] if len(currencies) == 1 else None,
            "billing_status": "ESTIMATED" if len(known) == len(rows) and len(currencies) == 1 else "UNKNOWN"})
    return result


def _probabilities(data: dict[str, Any], loaded: dict[str, MarketSnapshot], last: date) -> dict[str, Any]:
    dated: dict[date, list[MarketSnapshot]] = defaultdict(list)
    for snapshot in loaded.values():
        dated[snapshot.decision_date].append(snapshot)
    groups: dict[tuple[str, ...], list[tuple[dict[str, str], str]]] = defaultdict(list)
    counts: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    bindings: list[dict[str, Any]] = []
    evaluations = {row.id: row for row in data["jev"] if row.scope == "SYMBOL"}
    for question in data["questions"]:
        if question.question_id not in PNL_QUESTIONS or question.evaluation_id not in evaluations:
            continue
        row = evaluations[question.evaluation_id]
        key = (question.question_id, row.model_id, row.provider_version, question.question_version, question.criteria_version,
               _mode_for_day(data, row.decision_date), row.state_schema_version, row.question_set_version,
               str(row.state_json.get("header", {}).get("feature_set_version")))
        counts[key]["total_predictions"] += 1
        definition = QUESTION_DEFINITIONS[question.question_id]
        if (question.question_version != definition.question_version or question.criteria_version != definition.criteria_version
                or tuple(question.label_order_json) != definition.label_order):
            counts[key]["VERSION_MISMATCH"] += 1
            continue
        snapshot_hash = row.state_json.get("header", {}).get("market_snapshot_hash")
        base = loaded.get(snapshot_hash)
        if base is None or base.decision_date != row.decision_date or row.status != "AVAILABLE" or row.symbol is None:
            counts[key]["LABEL_UNAVAILABLE"] += 1
            continue
        try:
            command = JevEvaluationCommand.model_validate({"scope": row.scope, "state": row.state_json,
                "provider_name": row.provider_name, "provider_version": row.provider_version, "model_id": row.model_id,
                "question_set_version": row.question_set_version})
            if command.input_hash != row.input_hash or command.formal_key != row.formal_key:
                raise ValueError("REPORT_PREDICTION_IDENTITY_MISMATCH")
            if (base.decision_cutoff.astimezone(UTC) != command.state.header.decision_cutoff.astimezone(UTC)
                    or base.universe_snapshot_hash != command.state.header.candidate_universe_hash):
                raise ValueError("REPORT_PREDICTION_IDENTITY_MISMATCH")
        except ValueError:
            counts[key]["IDENTITY_MISMATCH"] += 1
            continue
        labels = mature_labels(base, row.symbol, dated, last)
        actual = labels.get(question.question_id)
        if labels["status"] == "AVAILABLE" and isinstance(actual, str) and actual in question.label_order_json:
            groups[key].append((question.distribution_json, actual))
            counts[key]["scored"] += 1
        else:
            counts[key][labels["status"]] += 1
        bindings.append({"evaluation_id": row.id, "input_hash": row.input_hash, "symbol": row.symbol,
                         "question_id": question.question_id, "market_snapshot_hash": snapshot_hash, "labels": labels})
    results = [{"question_id": key[0], "model": key[1], "provider_version": key[2], "question_version": key[3],
        "criteria_version": key[4], "mode": key[5], "state_schema_version": key[6], "question_set_version": key[7],
        "feature_set_version": key[8], "coverage": dict(counts[key]), **probability_metrics(groups[key])}
        for key in sorted(counts)]
    return {"status": "AVAILABLE" if any(row["sample_count"] for row in results) else "PENDING_LABELS",
        "brier_definition": "unhalved_multiclass_mean_sum_squared_error", "groups": results, "bindings": bindings,
        "symbol_formal_count": len(evaluations),
        "missing_question_count": {key: len(evaluations) - sum(question.question_id == key and question.evaluation_id in evaluations for question in data["questions"]) for key in PNL_QUESTIONS},
        "unscored_questions": ["data_sufficiency", "universe_risk_regime"]}


async def build_report(factory: SessionFactory, config: ForwardOperationConfig, from_date: date, to_date: date,
                       pricing: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if from_date > to_date or from_date < config.start_date:
        raise ValueError("REPORT_DATE_RANGE_INVALID")
    # R64：价格配置即使未匹配到调用也先验证，不能静默接受错误计费配置。
    for price in pricing or []:
        usage_cost(None, {"provider": str(price.get("provider", "")), "model": str(price.get("model", "")),
                         "version": str(price.get("version", "")), "date": from_date.isoformat()}, price)
    data = await _records(factory, config, from_date, to_date)
    loaded, unavailable = _load_snapshots(data["snapshots"])
    decisions = data["decisions"]
    own_hashes = {row.input_json.get("market_snapshot_hash") for row in decisions}
    own_hashes.update(row.state_json.get("header", {}).get("market_snapshot_hash") for row in data["jev"])
    expected: dict[date, set[str]] = defaultdict(set)
    for identity in data["run_identities"]:
        expected[date.fromisoformat(identity["date"])].update(identity["required_symbols"])
        own_hashes.add(identity["market_snapshot_hash"])
    for content_hash in own_hashes:
        if content_hash in loaded:
            snapshot = loaded[content_hash]
            expected[snapshot.decision_date].update(set(snapshot.security_states) | set(snapshot.daily_bars))
    calendars: dict[date, bool] = {}
    config_hashes = {row.config_hash for row in data["stages"]}
    for stage in data["stages"]:
        if stage.stage == "CALENDAR" and stage.status == "SUCCESS":
            open_dates = stage.outcome_json.get("open_dates")
            if isinstance(open_dates, list):
                for i in range((stage.trade_date - config.start_date).days + 1):
                    calendars.setdefault(config.start_date + timedelta(days=i), False)
                for value in cast(list[str], open_dates):
                    calendars[date.fromisoformat(value)] = True
            if stage.outcome_json.get("is_open") is False:
                calendars[stage.trade_date] = False
    daily: list[dict[str, Any]] = []
    day = from_date
    unknown_dates: list[str] = []
    while day <= to_date:
        rows = [row for row in decisions if row.decision_date == day]
        symbols = expected.get(day)
        if symbols is None and calendars.get(day) is not False:
            unknown_dates.append(day.isoformat())
        stages_on_day = [row for row in data["stages"] if row.trade_date == day]
        if symbols is not None or rows or stages_on_day:
            identities = Counter(row.symbol for row in rows)
            daily.append({"date": day.isoformat(), "expected_symbols": sorted(symbols) if symbols is not None else None,
                "expected_count": len(symbols) if symbols is not None else None,
                "missing_symbols": sorted(symbols - set(identities)) if symbols is not None else None,
                "unexpected_symbols": sorted(set(identities) - symbols) if symbols is not None else None,
                "duplicate_symbols": sorted(s for s, n in identities.items() if n > 1),
                "status_counts": dict(sorted(Counter(row.status for row in rows).items())),
                "action_counts": dict(sorted(Counter(row.action for row in rows).items())),
                "stages": [{"stage": row.stage, "status": row.status, "error_code": row.error_code} for row in sorted(stages_on_day, key=lambda row: row.stage)],
                "association_status": "LINKED" if symbols is not None else "FROZEN_POPULATION_UNAVAILABLE"})
        day += timedelta(days=1)
    denominator = sum(len(symbols) for symbols in expected.values())
    valid = sum(row.status == "AVAILABLE" and row.symbol in expected.get(row.decision_date, set()) for row in decisions)
    duplicate = any(row["duplicate_symbols"] or row["unexpected_symbols"] for row in daily)
    expected_nav = sorted(day for day, opened in calendars.items() if opened and from_date <= day <= to_date)
    # 无完整日历证明时只展示 NAV 原始点，不把漏日算作零收益。
    calendar_known = all((from_date + timedelta(days=i)) in calendars for i in range((to_date - from_date).days + 1))
    previous = [row for row in data["nav"] if row.valuation_date < from_date]
    portfolio = data["portfolio"]
    baseline = previous[-1].total_equity if previous else portfolio.initial_cash if portfolio else None
    nav = [row for row in data["nav"] if row.valuation_date >= from_date]
    metrics = nav_metrics(baseline, [(row.valuation_date, row.total_equity) for row in nav], expected_nav if calendar_known else None)
    model_versions = {(row.model_id, row.provider_version, row.prompt_version, row.output_schema_version) for row in decisions}
    modes = {_mode_for_day(data, from_date + timedelta(days=i)) for i in range((to_date - from_date).days + 1)
             if calendars.get(from_date + timedelta(days=i)) is not False or any(row.trade_date == from_date + timedelta(days=i) for row in data["stages"])}
    sizing_versions = {(row.sizing_version, row.config_hash) for row in data["sizing"]}
    jev_versions = {(row.model_id, row.provider_version, row.state_schema_version, row.question_set_version,
                     str(row.state_json.get("header", {}).get("feature_set_version"))) for row in data["jev"]}
    mixed = (len(config_hashes) > 1 or (config_hashes and config_hashes != {config.content_hash()}) or len(model_versions) > 1
             or len(sizing_versions) > 1 or len(jev_versions) > 1 or len(modes) > 1)
    if mixed:
        metrics.update(status="MIXED_CONFIGURATION", interval_return=None, max_drawdown=None)
    snapshots = [row for row in data["portfolio_snapshots"] if row.as_of.date() <= to_date]
    end = snapshots[-1] if snapshots else None
    before = [row for row in snapshots if row.as_of.date() < from_date]
    start = before[-1] if before else None
    metrics.update(baseline_date=previous[-1].valuation_date.isoformat() if previous else "INITIAL_CASH",
        base_currency=portfolio.base_currency if portfolio else None,
        nav_points=[{"date": row.valuation_date.isoformat(), "equity": str(row.total_equity), "unit_nav": str(row.unit_nav)} for row in nav],
        fill_count=len(data["fills"]), simulation_fees=str(sum(row.total_fees for row in data["fills"])),
        traded_gross_value=str(sum(row.gross_value for row in data["fills"])),
        turnover=number(sum(row.gross_value for row in data["fills"]) / baseline) if baseline else None,
        realized_pnl_change=str(end.realized_pnl - (start.realized_pnl if start else Decimal(0))) if end else None,
        dividend_income_change=str(end.dividend_income - (start.dividend_income if start else Decimal(0))) if end else None,
        dividend_receivable=str(end.dividend_receivable) if end else None,
        cash_balance=str(end.cash_balance) if end else None,
        available_cash=str(end.cash_balance - end.frozen_cash) if end else None,
        after_model_cost_return=None, after_model_cost_reason="MODEL_COST_OR_CURRENCY_OR_NAV_NOT_VERIFIED")
    providers = _providers(data, pricing)
    if (metrics["interval_return"] is not None and portfolio is not None and portfolio.base_currency == "CNY" and providers and all(
            row["estimated_cost"] is not None and row["currency"] == "CNY" for row in providers) and baseline):
        model_cost = sum((Decimal(row["estimated_cost"]) for row in providers), Decimal(0))
        metrics.update(after_model_cost_return=number(Decimal(metrics["interval_return"]) - model_cost / baseline),
                       after_model_cost_reason=None, estimated_model_cost_cny=number(model_cost))
    report: dict[str, Any] = {"report_version": "forward-evaluation-v1", "portfolio_id": config.portfolio_id,
        "from_date": from_date.isoformat(), "to_date": to_date.isoformat(), "config_hash": config.content_hash(),
        "mode": next(iter(modes)) if len(modes) == 1 else "MIXED" if modes else "UNVERIFIED",
        "modes": sorted(modes), "mode_reason": "RUNNER_CLOCK_ATTESTATION_OR_UNVERIFIED_LEGACY", "tax_policy_version": "GROSS_V1",
        "pricing_hash": sha256_json(pricing) if pricing is not None else None,
        "identities": {"run_ids": data["run_ids"], "decision_ids": sorted(row.id for row in decisions),
            "jev_ids": sorted(row.id for row in data["jev"]), "attempt_ids": sorted(row.id for row in data["attempts"]),
            "snapshot_hashes": sorted(loaded), "unavailable_snapshot_hashes": unavailable,
            "configuration_hashes": sorted(config_hashes), "sizing_versions": sorted({row.sizing_version for row in data["sizing"]}),
            "recovery_ids": sorted(row.id for row in data["recoveries"])},
        "run_identities": data["run_identities"], "invalid_run_identity_count": data["invalid_identity_count"],
        "attempt_attribution": {"status": "PARTIAL_OR_UNKNOWN" if unknown_dates or data["invalid_identity_count"] else "LINKED",
            "reason": "旧记录缺运行身份时不能把未关联调用当作零次或免费",
            "linked_run_count": len(data["run_ids"])},
        "completeness": {"expected_count": denominator if not unknown_dates else None,
            "known_expected_count": denominator, "available_count": valid, "unknown_dates": unknown_dates,
            "coverage": number(Decimal(valid) / denominator) if denominator and not unknown_dates and not duplicate else None,
            "daily": daily}, "providers": providers, "portfolio": metrics,
        "probabilities": _probabilities(data, loaded, to_date),
        "limitations": ["仅描述冻结样本，不证明策略有效", "旧记录缺运行模式证明时为UNVERIFIED", "不计算年化或Sharpe", "无外部模型或行情调用"]}
    report["report_hash"] = sha256_json(report)
    report["generated_at"] = datetime.now(UTC).isoformat()
    return report


async def list_diagnostics(factory: SessionFactory, config: ForwardOperationConfig, trade_date: date) -> dict[str, Any]:
    data = await _records(factory, config, trade_date, trade_date)
    return {"portfolio_id": config.portfolio_id, "date": trade_date.isoformat(),
        "invalid_run_identity_count": data["invalid_identity_count"],
        "association_status": "LINKED" if data["run_ids"] else "RUN_IDENTITY_UNAVAILABLE",
        "stages": [{"stage": row.stage, "status": row.status, "error_code": row.error_code} for row in data["stages"]],
        "attempts": [
        {"id": row.id, "evaluation_id": row.evaluation_id, "run_id": row.run_id, "status": row.status,
         "error_code": row.error_code, "response_hash": row.raw_response_hash,
         "diagnostic": sanitize_diagnostic(row.diagnostic_json), "usage": normalize_usage(row.usage_json)}
        for row in sorted(data["attempts"], key=lambda row: row.id)]}


def write_report(report: dict[str, Any], root: Path) -> tuple[Path, Path]:
    if not root.is_absolute():
        raise ValueError("REPORT_ABSOLUTE_PATH_REQUIRED")
    payload = {key: value for key, value in report.items() if key not in {"report_hash", "generated_at"}}
    if sha256_json(payload) != report["report_hash"]:
        raise ValueError("REPORT_HASH_MISMATCH")
    json_path = root / f"{report['report_hash']}.json"
    if json_path.exists():
        existing = json.loads(json_path.read_text(encoding="utf-8"))
        if existing.get("report_hash") != report["report_hash"] or {key: value for key, value in existing.items() if key not in {"report_hash", "generated_at"}} != payload:
            raise ValueError("REPORT_OUTPUT_CONFLICT")
        report = existing
    text = canonical_json(report) + "\n"
    markdown = f"# 前向评估报告\n\n组合：{report['portfolio_id']}\n\n区间：{report['from_date']} 至 {report['to_date']}\n\n模式：{report['mode']}；税务：{report['tax_policy_version']}\n\n报告哈希：{report['report_hash']}\n\n"
    for title, key in (("完整性", "completeness"), ("Provider质量与费用", "providers"), ("资产与收益", "portfolio"),
                       ("成熟标签与概率", "probabilities"), ("输入身份", "identities"), ("限制", "limitations")):
        markdown += f"## {title}\n\n```json\n{json.dumps(report[key], ensure_ascii=False, sort_keys=True, indent=2)}\n```\n\n"
    paths = (root / f"{report['report_hash']}.json", root / f"{report['report_hash']}.md")
    root.mkdir(parents=True, exist_ok=True)
    for path, content in zip(paths, (text, markdown), strict=True):
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("REPORT_OUTPUT_CONFLICT") from None
    return paths
