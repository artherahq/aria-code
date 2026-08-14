from pathlib import Path


def test_data_service_import_does_not_mutate_python_path_for_a_developer_machine():
    source = (Path(__file__).resolve().parents[1] / "data_service.py").read_text()

    assert "/Users/mac/Desktop/aria-code/packages" not in source
    assert "sys.path.append" not in source
