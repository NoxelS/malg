"""Offline regression checks against the pinned SearXNG DuckDuckGo parser.

Run inside the patched image; these fixtures never contact a search provider.
"""

# The provider image has unittest but no pytest; keep its regression suite dependency-free.
# ruff: noqa: PT009, PT027
import unittest
from types import SimpleNamespace

from searx.engines.duckduckgo import response
from searx.exceptions import SearxEngineCaptchaException


def parse_results(body: str) -> list:
    """Parse synthetic provider HTML through the real installed engine."""
    return list(
        response(
            SimpleNamespace(
                status_code=200,
                text=f"<html><body>{body}</body></html>",
                search_params={"data": {"kl": "de-de"}},
            )
        )
    )


class DuckDuckGoParserTests(unittest.TestCase):
    """Keep usable results while preserving genuine provider failures."""

    def test_malformed_results_do_not_discard_valid_siblings(self) -> None:
        """Missing and empty destinations must not crash a mixed response."""
        results = parse_results(
            '<div id="links">'
            '<div class="web-result"><h2>Missing anchor</h2></div>'
            '<div class="web-result"><h2><a>Missing href</a></h2></div>'
            '<div class="web-result"><h2><a href="">Empty href</a></h2></div>'
            '<div class="web-result"><h2><a href=" ">Blank href</a></h2></div>'
            '<div class="web-result"><h2><a href="https://example.test/first">First</a></h2>'
            '<a class="result__snippet">First snippet</a></div>'
            '<div class="web-result"><h2>Another placeholder</h2></div>'
            '<div class="web-result"><h2><a href="https://example.test/second">Second</a></h2>'
            '<a class="result__snippet">Second snippet</a></div>'
            "</div>"
        )
        self.assertEqual(
            [(result.url, result.title, result.content) for result in results],
            [
                ("https://example.test/first", "First", "First snippet"),
                ("https://example.test/second", "Second", "Second snippet"),
            ],
        )

    def test_only_malformed_results_returns_empty(self) -> None:
        """An unusable result block has no invented destination."""
        self.assertEqual(
            parse_results('<div id="links"><div class="web-result"><h2>Missing</h2></div></div>'),
            [],
        )

    def test_captcha_remains_a_provider_failure(self) -> None:
        """Do not disguise a CAPTCHA response as a healthy empty search."""
        with self.assertRaises(SearxEngineCaptchaException):
            parse_results('<form id="challenge-form"></form>')


if __name__ == "__main__":
    unittest.main()
