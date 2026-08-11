from malg import build_campaign_name


def test_build_campaign_name_normalizes_value() -> None:
    assert build_campaign_name("  ProductX  ", "  EU  ") == "productx-eu"
