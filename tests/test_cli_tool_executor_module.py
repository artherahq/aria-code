import importlib


def test_extracted_tool_executor_is_importable_independently():
    module = importlib.import_module("apps.cli.tool_executor")
    assert callable(module.execute_aria_tool)
