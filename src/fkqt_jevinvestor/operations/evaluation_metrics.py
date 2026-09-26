from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


def number(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 50
        return str(value.quantize(Decimal("0.00000001")))


def probability_metrics(samples: Sequence[tuple[Mapping[str, str], str]]) -> dict[str, Any]:
    bins: list[list[tuple[Decimal, int]]] = [[] for _ in range(10)]
    scores: list[Decimal] = []
    hits = 0
    with localcontext() as context:
        context.prec = 50
        for distribution, actual in samples:
            probabilities = {key: Decimal(value) for key, value in distribution.items()}
            if (actual not in probabilities or not probabilities or any(
                    not value.is_finite() or not 0 <= value <= 1 for value in probabilities.values())
                    or abs(sum(probabilities.values()) - 1) > Decimal("0.000001")):
                raise ValueError("REPORT_PROBABILITY_INVALID")
            selected = max(probabilities, key=lambda key: probabilities[key])
            confidence = probabilities[selected]
            hit = int(selected == actual)
            hits += hit
            scores.append(sum(((p - int(key == actual)) ** 2 for key, p in probabilities.items()), Decimal(0)))
            bins[min(9, int(confidence * 10))].append((confidence, hit))
        return {"sample_count": len(scores), "brier": number(sum(scores, Decimal(0)) / len(scores)) if scores else None,
                "accuracy": number(Decimal(hits) / len(scores)) if scores else None,
                "reliability": [{"lower": number(Decimal(i) / 10), "upper": number(Decimal(i + 1) / 10),
                    "upper_inclusive": i == 9, "count": len(bucket),
                    "mean_confidence": number(sum((p for p, _ in bucket), Decimal(0)) / len(bucket)) if bucket else None,
                    "hit_rate": number(Decimal(sum(h for _, h in bucket)) / len(bucket)) if bucket else None}
                    for i, bucket in enumerate(bins)]}


def nav_metrics(baseline: Decimal | None, rows: Sequence[tuple[date, Decimal]],
                expected_dates: Sequence[date] | None) -> dict[str, Any]:
    observed = {day for day, _ in rows}
    missing = sorted(set(expected_dates or ()) - observed)
    status = "AVAILABLE"
    if not rows or baseline is None or baseline <= 0:
        status = "NAV_UNAVAILABLE"
    elif expected_dates is None:
        status = "CALENDAR_UNAVAILABLE"
    elif missing or observed - set(expected_dates):
        status = "DATA_GAP"
    result: dict[str, Any] = {"status": status, "baseline": str(baseline) if baseline is not None else None,
        "nav_count": len(rows), "missing_dates": [day.isoformat() for day in missing],
        "interval_return": None, "max_drawdown": None}
    if status == "AVAILABLE":
        assert baseline is not None
        with localcontext() as context:
            context.prec = 50
            peak = baseline
            drawdown = Decimal(0)
            for _, equity in sorted(rows):
                if equity < 0 or not equity.is_finite():
                    raise ValueError("REPORT_NAV_INVALID")
                peak = max(peak, equity)
                drawdown = max(drawdown, (peak - equity) / peak)
            result.update(interval_return=number(max(rows)[1] / baseline - 1), max_drawdown=number(drawdown))
    return result


def usage_cost(usage: Mapping[str, int] | None, identity: Mapping[str, str],
               price: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "UNKNOWN", "estimate": None, "currency": None}
    if price is None:
        return result
    token_keys = {"input_per_million", "output_per_million"}
    request = "per_request" in price
    if request == bool(token_keys & price.keys()) or (not request and not token_keys <= price.keys()):
        raise ValueError("REPORT_PRICING_INVALID")
    required = {"provider", "model", "version", "effective_from", "effective_to", "currency"}
    if set(price) != required | ({"per_request"} if request else token_keys):
        raise ValueError("REPORT_PRICING_INVALID")
    try:
        first, last = date.fromisoformat(price["effective_from"]), date.fromisoformat(price["effective_to"])
        rates = [Decimal(str(price[key])) for key in ({"per_request"} if request else token_keys)]
        if first > last or price["currency"] not in {"CNY", "USD"} or any(not r.is_finite() or r < 0 for r in rates):
            raise ValueError("REPORT_PRICING_INVALID")
    except (TypeError, InvalidOperation, ValueError):
        raise ValueError("REPORT_PRICING_INVALID") from None
    if any(price[key] != identity[key] for key in ("provider", "model", "version")):
        return result
    if not first <= date.fromisoformat(identity["date"]) <= last:
        return result
    if usage is None or set(usage) != {"input_tokens", "output_tokens"} or any(type(v) is not int or v < 0 for v in usage.values()):
        return result
    with localcontext() as context:
        context.prec = 50
        amount = Decimal(str(price["per_request"])) if request else (
            Decimal(usage["input_tokens"]) * Decimal(str(price["input_per_million"]))
            + Decimal(usage["output_tokens"]) * Decimal(str(price["output_per_million"]))) / 1000000
        return {"status": "ESTIMATED", "estimate": number(amount), "currency": price["currency"]}
