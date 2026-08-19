import importlib.util
from datetime import date

import pytest

from apps.cli.commands.ashare_prediction_cmds import (
    build_prediction_service,
    load_universe_file,
    parse_ashare_predict_args,
    prediction_freshness,
)

# packages.quant_engine.services 只存在于私有的 Arthera 仓；aria-code 通过
# packages/__init__.py 把 __path__ 指向同级的 Arthera checkout 来桥接。
# 有 checkout 的开发机上这条链路是通的，公开仓库的 CI 和任何外部贡献者那里
# 则必然缺失——此前这两个用例因此在 3.10/3.11/3.12 三个矩阵上全部
# ModuleNotFoundError（该 import 自 2026-08-09 的 8ecd3bf 起就在，CI 一直红）。
# 它们测的是"平台存在时"的行为（第一个用例的名字本身就这么写的），
# 所以正确做法是缺失时跳过，而不是把公开仓库的测试绑死在私有仓库上。
def _has_platform_services() -> bool:
    # find_spec() 会先 import 父包，父包(packages.quant_engine.services)不存在时
    # 它抛 ModuleNotFoundError 而不是返回 None——所以必须捕获，否则 collection
    # 阶段就整个文件报错，比原来的两个 FAILED 还糟。
    try:
        return importlib.util.find_spec(
            "packages.quant_engine.services.daily_ashare_prediction_service"
        ) is not None
    except (ImportError, ValueError):
        return False


_HAS_PLATFORM_SERVICES = _has_platform_services()

requires_platform = pytest.mark.skipif(
    not _HAS_PLATFORM_SERVICES,
    reason="需要同级的 Arthera checkout 提供 packages.quant_engine.services（私有仓）",
)


def test_parse_prediction_args_preserves_explicit_scope():
    parsed = parse_ashare_predict_args(
        "all --universe-file /tmp/universe.txt --limit 5000 --period 2y --no-ai --no-ml"
    )
    assert parsed.universe == "all"
    assert parsed.universe_file == "/tmp/universe.txt"
    assert parsed.limit == 5000
    assert parsed.period == "2y"
    assert parsed.include_ai_analysis is False
    assert parsed.include_ml_confirmation is False


def test_parse_live_universe_flag_for_a_real_all_market_request():
    parsed = parse_ashare_predict_args("all --live-universe --limit 100")
    assert parsed.universe == "all"
    assert parsed.live_universe is True
    assert parsed.limit == 100


@pytest.mark.parametrize("args", ["bad", "broad --limit 0", "core50 --bogus"])
def test_parse_prediction_args_rejects_unsafe_or_unknown_scope(args):
    with pytest.raises(ValueError):
        parse_ashare_predict_args(args)


def test_universe_file_keeps_leading_zeroes_and_deduplicates(tmp_path):
    universe = tmp_path / "universe.txt"
    universe.write_text("000001\n600519,000001\n", encoding="utf-8")
    assert load_universe_file(str(universe)) == ["000001", "600519"]


def test_prediction_freshness_marks_old_report_as_not_current():
    result = {"dataQuality": {"latestDataDate": "20260604", "qualityStatus": "pass"}}
    freshness = prediction_freshness(result, today=date(2026, 8, 9))
    assert freshness["is_quality_pass"] is True
    assert freshness["is_current"] is False
    assert freshness["age_days"] == 66


@requires_platform
def test_quant_engine_facade_exposes_private_services_when_platform_is_present():
    from packages.quant_engine.services.daily_ashare_prediction_service import DailyASharePredictionService

    assert DailyASharePredictionService.__name__ == "DailyASharePredictionService"


@requires_platform
def test_prediction_service_prefers_explicit_report_directory(tmp_path):
    service = build_prediction_service({"ashare_prediction_dir": str(tmp_path)})
    assert service.storage_dir == tmp_path
