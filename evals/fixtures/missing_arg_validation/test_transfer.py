import pytest

from transfer import InsufficientFunds, transfer


def test_happy_path():
    assert transfer({"a": 100, "b": 0}, "a", "b", 40) == {"a": 60, "b": 40}


def test_rejects_overdraft():
    with pytest.raises(InsufficientFunds):
        transfer({"a": 10}, "a", "b", 40)


def test_rejects_negative_amount():
    with pytest.raises(ValueError):
        transfer({"a": 100, "b": 0}, "a", "b", -5)


def test_rejects_unknown_source():
    with pytest.raises(KeyError):
        transfer({"a": 100}, "zzz", "a", 5)


def test_does_not_mutate_input():
    original = {"a": 100, "b": 0}
    transfer(original, "a", "b", 10)
    assert original == {"a": 100, "b": 0}
