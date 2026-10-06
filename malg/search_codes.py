"""Stable reason codes shared by search and persistence layers."""

SEARCH_OUTAGE_REASON_CODES = frozenset(
    {
        "search_unavailable",
        "search_captcha",
        "search_rate_limited",
        "search_provider_blocked",
        "search_provider_failure",
        "search_http_error",
        "search_transport_error",
        "search_parser_failure",
    }
)
