def _convert_value(original_header: str, canonical_header: str, value: Optional[float]) -> Optional[float]:
    """Apply unit conversions using centralized mapping. Returns None for None values."""
    if value is None:
        return None

    # Check if there's a specific conversion for this (original, canonical) pair
    key: Tuple[str, str] = (original_header, canonical_header)
    if key in UNIT_CONVERSION_MAP:
        return value * UNIT_CONVERSION_MAP[key]

    return value
