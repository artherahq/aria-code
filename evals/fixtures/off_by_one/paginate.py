def page_count(total_items, per_page):
    """Number of pages needed to show total_items at per_page each."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return total_items // per_page


def slice_for(items, page, per_page):
    start = (page - 1) * per_page
    return items[start:start + per_page]
