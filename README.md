# malg

Campaign discovery for a later lead-generation workflow.

This repository provides NOOA-based campaign and ICP research plus typed account-candidate and
independent account-validation contracts for Noel Schwabenland's independent AI practice. Account
research records public business endpoints only; it does not initiate outreach.

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

Browser-capable agents also receive `self.web_search`, a per-agent SearXNG client for source
discovery. Call `await self.web_search.search("concise research question", language="en")` before
visiting a small number of selected result URLs with `self.browser`. Search titles and snippets
are untrusted discovery hints rather than evidence. Each agent has a configured request budget
and minimum interval; failed requests consume that budget to prevent retry storms.

The default endpoint, `http://lightpanda:9223/mcp`, is the Compose service DNS name. Override it
outside Compose with `MALG_BROWSER__URL`; set `MALG_BROWSER__ENABLED=false` to reject browser-agent
construction. `MALG_BROWSER__TIMEOUT_SECONDS` controls each MCP request timeout.

SearXNG is private to the Compose network at `http://searxng:8080`; it has no published host port.
Configure it through `[default.search]` or `MALG_SEARCH__...`: `timeout_seconds`, `max_results`,
`max_requests_per_run`, `min_interval_seconds`, `languages`, and `categories`. Set
`SEARXNG_SECRET` in the environment before starting tools outside local development. The service
exposes JSON results only and has no public-instance features, image proxy, or autocomplete.

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
grounded in Noel's positioning: production-ready agent systems, RAG applications, and full-stack
AI products for European organizations. This includes agent and multi-agent workflows, retrieval
and knowledge systems, voice interfaces, APIs and backend services, usable frontends, private/
on-premise AI infrastructure, and practical operational improvement. The campaign selects the
relevant strengths for its evidence-backed boundary rather than treating sensitive data or critical
workflows as mandatory constraints.

The initial research hypothesis is DACH industrial, infrastructure, or security-service
organizations with critical workflows and data-control needs. The agent uses SearXNG and
Lightpanda to discover and inspect a small number of external context sources, but must validate
the campaign hypothesis and returned evidence using Eurostat only. It uses cached Eurostat tools
to discover datasets and work with narrow aggregates. Its result describes the campaign boundary, workflow, positioning, buyer-role hypotheses,
qualification signals, exclusions, small entry-offer hypothesis, sources, confidence, assumptions,
unknowns, and questions for later ICP research. Every evidence item must be Eurostat-backed and
include its dataset code.

It is an analyst, not the freelancer. It does not name individual companies, accounts, contacts,
leads, or prospects; it does not create ICPs, messages, rankings, or quotas.

## Persistent campaign-research memory

Campaign research has explicit, durable NOOA memory backed by PostgreSQL. The agent can recall,
search, refine, archive, and associate its own findings, and `remember_source()` stores a concise
finding with a direct HTTP(S) source URL and optional Eurostat dataset code. Memory is scoped per
agent type and persists across API and worker restarts; no repository memory directory or SQLite
memory files are used.

The local Compose stack stores PostgreSQL state in its named `postgres-data` volume. Apply
migrations before using the API or workers:

```bash
docker compose -f docker/compose.yaml up -d postgres
DATABASE_URL=postgresql+psycopg://malg:malg-local-password@localhost:5432/malg \
  uv run alembic upgrade head
```

The read-only Memory page is available at `/memory` when the frontend is running. The trace
viewer uses a temporary database and does not inspect agent memory storage.

## ICP batches with durable non-overlap

`ICPResearchAgent` is stateless between runs. PostgreSQL uniqueness constraints are the sole
durable authority for campaign-scoped deduplication, so a restarted run cannot accept the same
structured segment identity again.

The host generates ICPs with at most three concurrent agent calls by default, validates each one,
and retries duplicates up to the configured limit. `[default.icp]` configures `batch_size`,
`concurrency`, attempt limits, and bounded exclusion cards; `MALG_ICP__...` environment variables
override those settings. Accepted campaigns and ICPs are persisted in PostgreSQL rather than
written to result files.

Each `research_one` call uses five smaller structured generations: segment identity, operations,
evidence and fit, buyer signals, then entry planning. Python assembles those validated sections
into the canonical `ICPResult` and rechecks cross-section evidence references. This avoids
requiring the model endpoint to produce the complete 19-definition ICP schema in one response.

## Campaign ICP entry point

The CLI operates on campaigns persisted in PostgreSQL. Configure `DATABASE_URL` and run:

```bash
uv run python -m malg
```

## Setup

Requires Python 3.12 or 3.13.

```bash
uv sync --group dev
```

Set `MALG_LLM__API_KEY` and other settings in the local ignored `.env`, or export them in the
shell. Makefile Compose targets explicitly load `.env` for interpolation and inject it into the API
and worker services; image builds receive neither local configuration nor secrets. For an
OpenAI-compatible endpoint:

```dotenv
MALG_LLM__MODEL=your-model
MALG_LLM__API_BASE=https://your-litellm-endpoint.example/v1
MALG_LLM__API_KEY=your-litellm-virtual-key
MALG_LLM__REQUEST_TIMEOUT_SECONDS=300
# Additional retries for client timeouts and HTTP 408, 504, or 524 responses.
MALG_LLM__MAX_RETRIES=3
# Supply limits known for your gateway/model. They are not inferred.
MALG_LLM__CONTEXT_WINDOW=131072
MALG_LLM__MAX_TOKENS=4096
# Optional for Qwen-compatible endpoints. Omit for providers that do not support it.
MALG_LLM__ENABLE_THINKING=false
```

MALG uses the official OpenAI Python SDK directly. It sends configured model names unchanged, so a
gateway alias such as `nc-medium` remains `nc-medium` rather than gaining a client-side provider
prefix. LiteLLM may still be the server behind an OpenAI-compatible endpoint; it is not MALG's
request client.

Configuration precedence is:

1. `default.config.toml`
2. `.env`
3. exported environment variables

Environment overrides use the `MALG_` prefix. For example,
`MALG_LLM__MODEL=another-model` overrides the model without modifying a file.
`MALG_LLM__REQUEST_TIMEOUT_SECONDS` sets the maximum time allowed while waiting
for the non-streaming OpenAI SDK response; the default is five minutes.
`MALG_LLM__MAX_RETRIES` controls the number of additional attempts for a timeout;
the default is three. MALG honors a gateway-provided `retry_after` delay and otherwise
uses a bounded exponential backoff.

## Verify

The regular test suite does not call an LLM endpoint:

```bash
make check
make test
```

## API

The persistence-only FastAPI service exposes campaign, ICP, account, contact, entrypoint, and
validation-record reads under `/api/v1`. Every versioned endpoint requires an HTTP bearer token;
`/health` and `/ready` remain unauthenticated for liveness probes. Authentication is one
configuration-backed account, defaulting to username `admin` with no password.

Set the credentials through environment variables or the local ignored `.env` (Makefile Compose
targets inject it into the API and worker):

```bash
export MALG_AUTH__USERNAME=admin
export MALG_AUTH__PASSWORD='replace-with-a-secret'
```

Acquire a token and use it on API requests:

```bash
TOKEN=$(curl -s http://127.0.0.1:8000/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"replace-with-a-secret"}' | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/dashboard
```

When `MALG_AUTH__PASSWORD` is blank or absent, authentication is fail-closed: login returns
`503` and versioned routes remain inaccessible. Changing the password invalidates existing tokens.
The API owns no schema creation at runtime; its container applies Alembic migrations before
starting. Start it with:

```bash
make api-up
curl http://127.0.0.1:8000/ready
```

The PostgreSQL data volume is local to Docker. Configure the database name, user, and password
with `MALG_POSTGRES_DB`, `MALG_POSTGRES_USER`, and `MALG_POSTGRES_PASSWORD` before startup.

## Frontend

`frontend/` is a standalone Angular application using Taiga UI. Visit `/login` to authenticate.
The Angular `ApiService` is the exclusive same-origin MALG API client; it stores the bearer token
only in `sessionStorage`, attaches it to every versioned request, and owns logout and expiry
redirect behavior. The read-only Memory page loads durable records through that service.

Start it locally with:

```bash
cd frontend
npm start
```

Or build and serve the production image at <http://127.0.0.1:4200>:

```bash
make frontend-up
```


NOOA generation methods can execute LLM-generated Python. Run the live smoke test only in an
appropriately isolated environment.

## Sandboxed NOOA smoke test

Backend, worker, and trace-viewer Compose services run as UID 65532 with read-only image
filesystems, all Linux capabilities dropped, and bounded writable `tmpfs` mounts for `/tmp` and
`/home/malg`. They do not mount repository memory, results, or local configuration paths. Docker's
default seccomp and (on Linux hosts) AppArmor profiles remain in force.

Configure secrets through environment variables or a local ignored `.env`, then start the detached
browser and trace tools:

```bash
make restart-tools
```

`make restart-tools` rebuilds the local tool image and recreates Lightpanda, the trace viewer,
and the trace proxy. Lightpanda uses `restart: unless-stopped`, so an internal browser crash is
restarted automatically.

```bash
make up
```

`make up` builds and starts the tools, API, frontend, and three workers; `make workers` rebuilds
and recreates only those three worker replicas. Open
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

## Production images and GitHub configuration

MALG publishes the immutable backend image `ghcr.io/noxels/malg` and standalone frontend image
`ghcr.io/noxels/malg-frontend`. Flux should consume a release tag such as `v0.2.0` together with
the digest recorded in that GitHub Release; the workflows also publish the exact commit-SHA tag.
There is intentionally no mutable `latest` tag. The backend image runs the API with
`uvicorn malg.api.app:app --host 0.0.0.0 --port 8000` or the worker with `python -m malg.worker`.

Before enabling releases, grant GitHub Actions repository contents write permission and configure
the Actions release actor (`github-actions[bot]`, or the configured GitHub App/bot token) to bypass
main branch protection for its generated version commit. The release workflow runs only for a
merged pull request targeting `main`, increments `pyproject.toml` from `0.<minor>.0` to the next
minor, then creates the matching annotated tag. It does not run for unmerged pull requests.

Grant Flux pull access to both GHCR packages, or make the packages public. Cluster configuration
owns image selection, digest pinning, and rollout; this repository does not apply Kubernetes
resources.
