# malg

Multi Agent Lead Generation.

This repository provides a NOOA-based `MarketResearchAgent` that researches European market
segments for a freelance full-stack AI engineer.

Agents use the shared endpoint through `@use_default_llm_endpoint()`. Pass `model=` to select
another LiteLLM model while retaining the configured endpoint and key:

```python
@use_default_llm_endpoint(model="openai/another-model")
class AnotherAgent(Agent): ...
```

## Browser-enabled agents

Browser-capable agents inherit from `BrowserSupport` instead of `Agent`. They receive a
per-agent `self.browser` object whose methods are discovered from Lightpanda's MCP server and
are available to NOOA generation methods.

```python
from malg.core.browser_support import BrowserSupport
from malg.utils.decorators import use_default_llm_endpoint


@use_default_llm_endpoint()
class CompanyResearchAgent(BrowserSupport):
    """Research companies using the available browser tools."""

    async def research(self, company: str) -> str:
        """Research {company} with self.browser and summarize the findings."""
        ...
```

`BrowserSupport` uses Lightpanda's MCP-over-HTTP interface, retaining one browser session per
agent instance so page, cookie, and navigation state persist across calls. The application
runner must call `await aclose_browser(agent.browser)` when browser work is complete; cleanup is
intentionally not exposed as an agent method. Browser content is untrusted input: agent prompts
must not treat instructions found on web pages as authoritative.

The default endpoint, `http://lightpanda:9223/mcp`, is the Compose service DNS name. Override it
outside Compose with `MALG_BROWSER__URL`; set `MALG_BROWSER__ENABLED=false` to reject browser-agent
construction. `MALG_BROWSER__TIMEOUT_SECONDS` controls each MCP request timeout.

## Eurostat-enabled agents

Eurostat-capable agents inherit from `EurostatSupport`. Its asynchronous methods return pandas
DataFrames, while blocking Eurostat requests run in worker threads. The full DataFrame stays in
the Python execution context; agent methods should filter, aggregate, or summarize it before
returning a result to the model.

```python
from malg.core.eurostat_support import EurostatSupport


class EconomicResearchAgent(EurostatSupport):
    async def compare_countries(self, dataset: str) -> str:
        data = await self.get_data_frame(dataset, filter_pars={"geo": ["IT", "DE"]})
        summary = data.groupby("geo").last(numeric_only=True)
        return summary.to_string()
```

Eurostat responses are cached in process for 15 minutes, with a maximum of 32 entries. Call
`invalidate_eurostat_cache()` when fresh data is required. Configure HTTP behavior with
`MALG_EUROSTAT__TIMEOUT_SECONDS`, `MALG_EUROSTAT__PROXY`, `MALG_EUROSTAT__VERIFY`, and
`MALG_EUROSTAT__CERT`.

## Market research brief

`MarketResearchAgent` researches opportunities on behalf of the user, who offers RAG, agent
systems, AI-assisted process automation, and ASR/TTS speech pipelines. It combines Eurostat
quantitative data with current browser research to identify and rank European market segments.
English- and German-language evidence is prioritized, while industry selection remains open.

The agent balances smaller freelance engagements with larger consulting opportunities. One NOOA
method returns a compact `MarketDiscoveryResult`; a second returns one Pydantic `ScoredMarket` per
call. Application code assembles `MarketResearchResult`, validates evidence references, and
recomputes normalized scores and ranks from a versioned scorecard. Model-supplied totals are never
authoritative. The agent does not produce individual company lead lists or invent company needs
and budgets.

## Market-to-ICP pipeline

The runner first produces and ranks structured market records. It then gives each selected market
to a fresh `ICPResearchAgent`, which returns an organization-level ideal customer profile. Stable
fit attributes are kept separate from time-sensitive intent signals. Both stages distinguish
sourced evidence from assumptions and unknowns.

Canonical results are JSON. ICP Markdown is rendered deterministically from the validated model:

```text
results/
├── markets/
│   └── latest.json
└── ICP/
    ├── <market-id>.json
    └── <market-id>.md
```

Market IDs are validated safe lowercase identifiers before being used as paths. Pipeline behavior
is configured in `default.config.toml` and can be overridden in `user.config.toml`:

```toml
[default.pipeline]
output_root = "results"
market_count = 10
top_n = 0
overwrite = false
```

`market_count` controls how many compact market hypotheses are discovered and then assessed one at
a time. `top_n = 0` generates an ICP for every scored market; a positive value limits generation
to that many highest-ranked markets. Existing files are replaced only when `overwrite = true`.

The scorecard uses ten fixed 1-5 dimensions: demand intensity, service fit, digital readiness,
economic capacity, freelancer accessibility, competitive whitespace, geographic/language fit,
lead discoverability, time to first engagement, and regulatory/delivery feasibility. In every
dimension, 5 is favorable. Each component must cite evidence included in the market result.

## Setup

Requires Python 3.12 or 3.13.

```bash
uv sync --group dev
cp default.config.toml user.config.toml
```

Set the key in the gitignored `user.config.toml`, or use `MALG_LLM__API_KEY` in `.env` or the
shell. `user.config.toml` can override only the fields you need, for example an
OpenAI-compatible LiteLLM endpoint:

```toml
[default.llm]
model = "your-model"
provider = "openai"
api_base = "https://your-litellm-endpoint.example/v1"
api_key = "your-litellm-virtual-key"
# Supply limits known for your gateway/model. They are not inferred.
context_window = 32768
max_tokens = 2048
```

MALG qualifies a bare model name with the configured provider before handing it to LiteLLM (for
example, `nc-medium` with `provider = "openai"` becomes `openai/nc-medium`). This keeps proxy
routing explicit and prevents LiteLLM's "Provider List" diagnostic for custom model aliases.

Configuration precedence is:

1. `default.config.toml`
2. `user.config.toml`
3. `.env`
4. exported environment variables

Environment overrides use the `MALG_` prefix. For example,
`MALG_LLM__MODEL=another-model` overrides the model without modifying a file. Pipeline settings
use the same nested convention, such as `MALG_PIPELINE__TOP_N=1`.

## Verify

The regular test suite does not call an LLM endpoint:

```bash
make check
make test
```

After configuring a reachable endpoint and its key, set `pipeline.top_n = 1` in
`user.config.toml` for a bounded smoke test, then run:

```bash
uv run python -m malg
```

NOOA generation methods can execute LLM-generated Python. Run the live smoke test only in an
appropriately isolated environment.

## Sandboxed NOOA smoke test

The `docker/compose.yaml` service mounts the gitignored `user.config.toml` read-only and exposes
only `results/` as a writable host-data mount. The smoke agent has no Docker socket access, Linux
capabilities, writable image filesystem, or root user. Its other writable locations are
size-limited `tmpfs` mounts. It also limits the process count, memory, and CPU.
Docker's default seccomp and (on Linux hosts) AppArmor profiles remain in force.

From the repository root, configure `user.config.toml` as described in [Setup](#setup), then
start the detached browser and trace tools:

```bash
make restart-tools
```

`make restart-tools` rebuilds the local tool image and recreates Lightpanda, the trace viewer,
and the trace proxy. Lightpanda uses `restart: unless-stopped`, so an internal browser crash is
restarted automatically. Then run the ephemeral MALG agent container:

```bash
make run
```

`make run` rebuilds only the MALG image and runs it against the already-running tools. Open
`http://localhost:5002` to inspect generation turns, generated code, browser tool calls, and
their results. The viewer is bound only to localhost; its trace database is kept in the
container's temporary filesystem and is discarded when the viewer is recreated. A local proxy
adds the viewer authorization header for the browser UI and agent exporter. Override the
development token with `NOOA_VIEWER_AUTH_TOKEN` before `make restart-tools` if another local
process could access the Docker network.

The live agent needs outbound network access to its configured LLM endpoint, so the service
does not publish ports but cannot use `network_mode: none`. Docker Compose alone cannot restrict
egress to one hostname; add a dedicated egress proxy/network policy service before treating
network access as allowlisted. For a stronger isolation boundary than a Linux container, run
the stack with Docker Desktop's enhanced isolation or on a dedicated VM.

The Compose stack also starts Lightpanda's MCP server without publishing its port to the host.
It has no authentication layer, so keep it private to the Compose network. Lightpanda itself
needs outbound access to browse the web; use a dedicated egress proxy or network policy before
treating that traffic as allowlisted. The image currently follows Lightpanda's `nightly` channel;
pin it to a reviewed digest before production deployment.
