"""回测结果的规范化序列化。"""

import json
from pathlib import Path

from fkqt_jevinvestor.domain.backtest import BacktestResult


def canonical_result_json(result: BacktestResult) -> str:
    """把回测结果转成确定、可复现的规范 JSON 字符串。

    ``model_dump(mode="json")`` 会把 Decimal 转成字符串、把日期转成 ISO 格式；
    键排序并去掉多余空白，保证同一份数据在任何机器上产生完全相同的字符串。
    """
    payload = result.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_result(result: BacktestResult, destination: Path) -> None:
    """把回测结果写成 UTF-8 文件，末尾一个换行。

    用 ``write_bytes`` 直接写字节，避免 Windows 下 ``write_text`` 把换行转成
    ``\\r\\n`` 导致的跨平台字节不一致；父目录不存在时抛原生文件系统异常。
    """
    content = canonical_result_json(result) + "\n"
    destination.write_bytes(content.encode("utf-8"))
