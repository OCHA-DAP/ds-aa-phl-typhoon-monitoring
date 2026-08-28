"""CMA tropical cyclone intensity categories.

CMA classifies on 2-minute sustained wind, which is the wind its bulletins
report. The bands below are the China Meteorological Administration national
standard (GB/T 19201-2006).

Note that the readiness threshold of 177 kph 1-minute (88.9 kt 2-minute) sits
in the Severe Typhoon band, one step below Super Typhoon. A storm can reach the
readiness threshold without being forecast to make landfall as a super typhoon.
"""

# (label, lower bound in m/s, lower bound in kt on 2-min wind)
CMA_CATEGORIES = [
    ("Super Typhoon", 51.0),
    ("Severe Typhoon", 41.5),
    ("Typhoon", 32.7),
    ("Severe Tropical Storm", 24.5),
    ("Tropical Storm", 17.2),
    ("Tropical Depression", 10.8),
]

MS_TO_KT = 1.944

SUPER_TYPHOON = "Super Typhoon"
SUPER_TYPHOON_MS = 51.0
SUPER_TYPHOON_KT = SUPER_TYPHOON_MS * MS_TO_KT  # 99.1 kt


def category_from_ms(wind_ms: float) -> str:
    """CMA category for a 2-minute sustained wind in m/s."""
    if wind_ms is None:
        return "Unknown"
    for label, lower in CMA_CATEGORIES:
        if wind_ms >= lower:
            return label
    return "Below tropical depression"


def category_from_kt(wind_kt: float) -> str:
    """CMA category for a 2-minute sustained wind in knots."""
    if wind_kt is None:
        return "Unknown"
    return category_from_ms(wind_kt / MS_TO_KT)


def is_super_typhoon(wind_kt: float) -> bool:
    """Is this 2-minute sustained wind at super typhoon strength?"""
    return wind_kt is not None and wind_kt >= SUPER_TYPHOON_KT


# CMA writes the category as an abbreviation in the bulletin header, e.g.
# "SuperTY SINLAKU 2604". These are always expanded before display.
CMA_HEADER_ABBREVIATIONS = {
    "SUPERTY": "Super Typhoon",
    "STY": "Severe Typhoon",
    "TY": "Typhoon",
    "STS": "Severe Tropical Storm",
    "TS": "Tropical Storm",
    "TD": "Tropical Depression",
}


def expand_category(abbreviation: str) -> str:
    """Full category name for a CMA bulletin header abbreviation."""
    if not abbreviation:
        return "Tropical cyclone"
    return CMA_HEADER_ABBREVIATIONS.get(
        abbreviation.upper(), abbreviation
    )
