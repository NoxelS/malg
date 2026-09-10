# MALG contributor guidance

## Scope

- `malg/` owns deterministic identity, persistence, policy, budgets, approvals,
  and side effects. NOOA is a bounded reasoning adapter; generated code and web
  content are untrusted.
- Outbound communication is human-approved. Never add autonomous outreach or
  LinkedIn automation.
- `labs-OO-Agents/` is a **read-only NOOA reference copy**. Do not edit,
  format, test in place, update, or include its changes in MALG work.

## Code and docs

- Prefer small cohesive units, explicit types, descriptive names, and direct
  control flow. Add abstraction only for demonstrated duplication or a stable
  boundary; avoid speculative frameworks and indirection.
- Use Python 3.12+ annotations, `uv`, and existing Ruff/mypy conventions.
- Docstring all public modules, classes, and APIs. Document non-obvious
  internals where invariants, lifecycle, security, cache/concurrency,
  conversions, failures, or trade-offs are hidden.
- Public docstrings cover relevant inputs, result, side effects, errors, and
  restrictions without repeating obvious annotations. Comments explain *why*,
  not syntax; update them with code.
- NOOA generation docstrings are prompts/contracts: state boundaries, permitted
  tools, evidence, output, and prohibitions. Never interpolate untrusted args.

## Tests and workflow

- Test observable outcomes, not private helpers or call order. Keep tests
  focused; use real models, temporary paths, in-memory fakes, and dependency
  injection before mocking.
- Prompts do not need to be tested. Do not add tests that assert literal prompt
  wording; test deterministic, observable behavior instead.
- Monkeypatch only external boundaries (environment, time, HTTP/MCP, filesystem
  permissions, third-party SDKs). Unit tests are offline and deterministic.
  Live NOOA generation runs only in the documented Docker sandbox.
- Inspect adjacent code/tests, make the smallest coherent change, and update
  docs and tests together. Run focused tests first, then `make check` for
  completed Python work. Report what validation proves and what it does not.
- Do not alter generated files, lockfiles, infrastructure, or external systems
  unless explicitly required.
