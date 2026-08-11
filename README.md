# malg

Multi Agent Lead Generation.

This repository currently proves the NVIDIA Object-Oriented Agents (NOOA) integration with
a deliberately trivial `MarketResearchAgent`. It does not yet perform market research.

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

After configuring a reachable endpoint and its key, run the explicit NOOA smoke test:

```bash
uv run python -m malg
```

NOOA generation methods can execute LLM-generated Python. Run the live smoke test only in an
appropriately isolated environment.

## Sandboxed NOOA smoke test

The `docker/compose.yaml` service mounts only the gitignored `user.config.toml`, read-only, so
the smoke agent has its usual configuration without environment-variable setup. It has no Docker
socket access, Linux capabilities, writable image filesystem, or root user. Its only writable
locations are size-limited `tmpfs` mounts. It also limits the process count, memory, and CPU.
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
