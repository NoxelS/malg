"""Core utilities for Multi Agent Lead Generation."""


def build_campaign_name(product: str, region: str) -> str:
    """Build a normalized campaign name."""
    return f"{product.strip()}-{region.strip()}".lower()
