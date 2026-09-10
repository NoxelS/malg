# malg

Campaign discovery for a later lead-generation workflow.

This repository provides a NOOA-based `CampaignResearchAgent` that finds one evidence-backed
campaign candidate for Noel Schwabenland's independent AI practice. It currently does not
research ICPs, accounts, contacts, leads, or outreach.

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

## Campaign research brief

`CampaignResearchAgent` finds exactly one coherent, evidence-backed campaign boundary. It is
grounded in Noel's positioning: governed, production-ready RAG, voice, agent, and private/on-premise
AI systems for European organizations with sensitive data or critical workflows.

The initial research hypothesis is DACH industrial, infrastructure, or security-service
organizations with critical workflows and data-control needs. The agent must validate or reject
that hypothesis using Eurostat only. It uses cached Eurostat tools to discover datasets and work
with narrow aggregates; it has no browser, web-search, or other external-research capability. Its
result describes the campaign boundary, workflow, positioning, buyer-role hypotheses,
qualification signals, exclusions, small entry-offer hypothesis, sources, confidence, assumptions,
unknowns, and questions for later ICP research. Every evidence item must be Eurostat-backed and
include its dataset code.

It is an analyst, not the freelancer. It does not name individual companies, accounts, contacts,
leads, or prospects; it does not create ICPs, messages, rankings, or quotas.

## One-campaign entry point

The entry point calls only `CampaignResearchAgent.find_campaign()` and prints the validated JSON
result. It does not write result files or invoke the dormant ICP/account research modules:

```bash
uv run python -m malg
```

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
`MALG_LLM__MODEL=another-model` overrides the model without modifying a file.

## Verify

The regular test suite does not call an LLM endpoint:

```bash
make check
make test
```

After configuring a reachable endpoint and its key, run the bounded one-campaign smoke test:

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
