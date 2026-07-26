def merge_intents(intents: list[str]) -> str:
    """Join intent strings with '; ', removing duplicates and blanks while preserving order."""
    seen = set()
    result = []
    for intent in intents:
        item = intent.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return "; ".join(result)
