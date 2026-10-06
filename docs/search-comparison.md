# Direct and Tor-backed search comparison

This procedure evaluates the direct and isolated Tor-backed SearXNG instances without changing MALG's configured production route. Run it only after Sisyphus has deployed the private experimental endpoint and verified its Tor-only egress controls. The query set contains 100 public German and English research queries. Each route receives each query once, for 200 SearXNG requests total, spaced over 24 hours by default. The output stores counts and failure categories, not query result text.

## Run

Run from an environment that can reach both private service URLs. Create a new output path for each evaluation; an existing file is resumed, and completed query/route pairs are skipped to prevent duplicate traffic.

```sh
uv run python scripts/search_comparison.py \
  --direct-url http://searxng:8080 \
  --tor-url http://searxng-tor:8080 \
  --queries scripts/search_comparison_queries.jsonl \
  --output artifacts/search-comparison-2026-10-06.jsonl \
  --window-hours 24
```

Use the actual private service URL supplied by Sisyphus for the Tor route. Do not publish either service. The runner alternates which route goes first and keeps paired requests close together, then waits before the next query pair. If interrupted, rerunning with the same output resumes missing route/query pairs. Use a fresh output file only for a deliberately new measurement window.

Each JSONL record includes UTC time, route, query ID and language, HTTP status, latency, count of results with HTTP(S) URLs, distinct engines represented in results, SearXNG-reported unresponsive-engine categories, and a bounded outcome (`usable`, `empty`, `invalid_response`, `rate_limit`, `http_error`, or `transport_error`). The runner never stores returned titles, snippets, result URLs, or raw error bodies. CAPTCHA and rate-limit classifications from `unresponsive_engines` depend on what SearXNG reports; treat unclassified provider failures as unknown, not as healthy results.

## Collect upstream counts and verify isolation

The client result payload cannot establish how many upstream requests SearXNG made: engines may fan out, fail before returning results, or be deduplicated internally. For the same UTC window, record the direct and Tor SearXNG instance's upstream request counters from the deployed SearXNG metrics/logs, split by engine where available. Record counter start/end values and the collection source with the evaluation results. Do not substitute MALG request counts or the number of engine names in returned results for actual upstream requests.

Before and during the run, have the Sisyphus operator verify the Tor workload's egress and DNS path, confirm direct egress remains blocked when Tor is unavailable, and confirm the existing direct instance remains available. Capture the evidence source and timestamps; this benchmark itself does not prove network isolation.

For a controlled recovery check, ask the operator to temporarily make only the experimental SearXNG endpoint unavailable, issue one designated probe through MALG's retrieval client, and confirm it reports search unavailable without treating the failure as an empty result. Restore the endpoint, repeat that same probe once, and confirm usable search results return. Record the failure and recovery timestamps. This is a single diagnostic probe outside the 200-query comparison budget; do not restart the full query set for the recovery check.

## Evaluate

Compare routes by language and query category. Report usable-result rate, empty-result rate, explicit HTTP 429s, SearXNG-reported CAPTCHA/rate-limit engine failures, other unresponsive engines, transport/HTTP failures, and median/p95 latency. Include the actual upstream request deltas and provider-level error counts collected by the operator. Preserve the query set, JSONL output, observation window, deployment revision, and metric source with the recommendation.

Recommend adoption only if the measured privacy controls are verified and usable discovery remains within MALG's research deadline. An improvement in availability is an empirical result, not an assumption about Tor. This limited workload does not establish capacity for large-scale daily traffic, and routing through Tor does not hide query contents from search providers.
