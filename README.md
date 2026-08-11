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

From the repository root, configure `user.config.toml` as described in [Setup](#setup), then run:

```bash
make run
```

`make run` rebuilds the image first. The Dockerfile uses the locked dependency set and a
persistent BuildKit uv cache, so source-only changes reuse the dependency layer and cached
package downloads.

The live agent needs outbound network access to its configured LLM endpoint, so the service
does not publish ports but cannot use `network_mode: none`. Docker Compose alone cannot restrict
egress to one hostname; add a dedicated egress proxy/network policy service before treating
network access as allowlisted. For a stronger isolation boundary than a Linux container, run
the stack with Docker Desktop's enhanced isolation or on a dedicated VM.
