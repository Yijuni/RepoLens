from formatter import format_report
from storage import normalize_items


def build_report(items):
    normalized = normalize_items(items)
    return format_report(normalized)
