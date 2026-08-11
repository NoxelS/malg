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
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

After configuring a reachable endpoint and its key, run the explicit NOOA smoke test:

```bash
uv run python -m malg
```

NOOA generation methods can execute LLM-generated Python. Run the live smoke test only in an
appropriately isolated environment.
