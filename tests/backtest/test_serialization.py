"""回测结果规范化输出测试。"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fkqt_jevinvestor.backtest.metrics import build_daily_record, summarize
from fkqt_jevinvestor.backtest.serialization import canonical_result_json, write_result
from fkqt_jevinvestor.domain.backtest import BacktestResult
from tests.backtest.fixtures import make_config, make_execution_result


def _sample_result() -> BacktestResult:
    config = make_config(
        run_id="回测样例",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 6),
    )
    record = build_daily_record(
        Decimal(1000000),
        Decimal(1000000),
        Decimal(1000000),
        make_execution_result(total_equity=Decimal(1010000), total_fees=Decimal("12.3400")),
    )
    summary = summarize(config, (record,))
    return BacktestResult(config=config, daily_records=(record,), summary=summary)


def test_canonical_json_is_deterministic() -> None:
    result = _sample_result()
    assert canonical_result_json(result) == canonical_result_json(result)


def test_keys_are_sorted() -> None:
    payload = json.loads(canonical_result_json(_sample_result()))
    assert list(payload.keys()) == sorted(payload.keys())
    assert list(payload["config"].keys()) == sorted(payload["config"].keys())


def test_matches_standard_json_dump() -> None:
    original = canonical_result_json(_sample_result())
    payload = json.loads(original)
    redumped = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert original == redumped


def test_amounts_and_ratios_are_strings() -> None:
    payload = json.loads(canonical_result_json(_sample_result()))
    assert isinstance(payload["config"]["initial_cash"], str)
    assert isinstance(payload["summary"]["total_fees"], str)
    assert isinstance(payload["daily_records"][0]["daily_return"], str)


def test_dates_are_iso_format() -> None:
    payload = json.loads(canonical_result_json(_sample_result()))
    assert payload["config"]["start_date"] == "2026-01-05"


def test_chinese_is_not_escaped() -> None:
    text = canonical_result_json(_sample_result())
    assert "回测样例" in text
    assert "\\u" not in text


def test_write_result_ends_with_single_newline(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    write_result(_sample_result(), destination)
    content = destination.read_bytes().decode("utf-8")
    assert content.endswith("\n")
    assert not content.endswith("\n\n")


def test_write_result_matches_function_and_is_utf8(tmp_path: Path) -> None:
    result = _sample_result()
    destination = tmp_path / "result.json"
    write_result(result, destination)
    raw = destination.read_bytes()
    assert raw.decode("utf-8") == canonical_result_json(result) + "\n"
