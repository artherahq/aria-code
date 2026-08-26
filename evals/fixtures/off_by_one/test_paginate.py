import pytest

from paginate import page_count, slice_for


def test_exact_fit():
    assert page_count(20, 10) == 2


def test_partial_last_page():
    # 21 items at 10 per page needs 3 pages, not 2.
    assert page_count(21, 10) == 3
    assert page_count(1, 10) == 1
    assert page_count(0, 10) == 0


def test_rejects_bad_page_size():
    with pytest.raises(ValueError):
        page_count(10, 0)


def test_slice():
    items = list(range(25))
    assert slice_for(items, 3, 10) == list(range(20, 25))
