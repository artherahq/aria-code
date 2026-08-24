"""spreadsheet_tools 回归测试：公式范围、Python 复核、防线、注册。"""

from datetime import date
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from aria_code.tools.spreadsheet_tools import (  # noqa: E402
    MAX_SHEETS,
    register_spreadsheet_tools,
    tool_write_spreadsheet,
    write_workbook,
)


def _bill_spec():
    return {
        "filename": "test_bill",
        "sheets": [
            {
                "name": "明细",
                "headers": ["日期", "供应商", "币种", "金额"],
                "rows": [
                    ["2025-07-14", "Anthropic", "GBP", 18.0],
                    ["2025-10-17", "Companies House", "GBP", 50.0],
                    ["2025-10-27", "Runway", "USD", 18.0],
                    ["2026-02-28", "OpenAI", "USD", 6.0],
                ],
                "number_formats": {"D": "#,##0.00"},
                "col_widths": [12, 24, 8, 12],
                "highlight_rows": [4],
                "totals": [
                    {"label": "合计GBP", "type": "sumif", "value_col": "D",
                     "key_col": "C", "key": "GBP", "label_col": "B"},
                    {"label": "合计USD", "type": "sumif", "value_col": "D",
                     "key_col": "C", "key": "USD", "label_col": "B"},
                    {"label": "全部", "type": "sum", "value_col": "D",
                     "label_col": "B"},
                    {"label": "GBP笔数", "type": "countif", "value_col": "D",
                     "key_col": "C", "key": "GBP", "label_col": "B"},
                ],
            },
        ],
    }


def test_write_workbook_formulas_and_verification(tmp_path: Path):
    out = tmp_path / "bill.xlsx"
    result = write_workbook(_bill_spec(), out_path=out)

    assert result["success"] and out.exists()
    verified = {v["label"]: v for v in result["verified_totals"]}
    # Python 复核值正确
    assert verified["合计GBP"]["python_value"] == 68.0
    assert verified["合计USD"]["python_value"] == 24.0
    assert verified["全部"]["python_value"] == 92.0
    assert verified["GBP笔数"]["python_value"] == 2.0
    # 公式范围精确覆盖数据区（4 行数据 → 2..5）
    assert verified["合计GBP"]["formula"] == '=SUMIFS(D2:D5,C2:C5,"GBP")'
    assert verified["全部"]["formula"] == "=SUM(D2:D5)"

    wb = openpyxl.load_workbook(out)
    ws = wb["明细"]
    # ISO 字符串已转真实日期 + 冻结首行 + 标黄行
    assert isinstance(ws.cell(2, 1).value, date)
    assert ws.freeze_panes == "A2"
    assert ws.cell(5, 1).fill.fgColor.rgb.endswith("FEF3C7")
    # 合计单元格是公式而非硬编码数值
    formulas = [ws.cell(r, 4).value for r in range(7, 11)]
    assert all(str(f).startswith("=") for f in formulas)


def test_tool_guards():
    assert tool_write_spreadsheet({})["success"] is False
    too_many = {"sheets": [{"name": f"s{i}", "headers": ["a"], "rows": []}
                           for i in range(MAX_SHEETS + 1)]}
    assert "上限" in tool_write_spreadsheet(too_many)["error"]
    bad_total = {"sheets": [{"name": "s", "headers": ["a"], "rows": [[1]],
                             "totals": [{"type": "median", "value_col": "A"}]}]}
    assert "不支持" in tool_write_spreadsheet(bad_total)["error"]


def test_register_never_overwrites():
    tools, schemas = {}, []
    assert register_spreadsheet_tools(tools, schemas) == 1
    assert "write_spreadsheet" in tools
    assert schemas[0]["function"]["name"] == "write_spreadsheet"
    # 二次注册不重复
    assert register_spreadsheet_tools(tools, schemas) == 0
    assert len(schemas) == 1
