def normalize_items(items):
    return sorted({str(item).strip() for item in items if str(item).strip()})
