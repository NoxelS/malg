"""Safe host-side failures for unsupported request and tool contracts."""


class LLMRequestContractError(RuntimeError):
    """The configured endpoint rejected the required research tool grammar."""

    code = "llm_request_unsupported"
    message = (
        "Configured endpoint rejected the research tool grammar. Verify native function tools, "
        "required tool choice, and the supplied schemas on the configured gateway/provider; "
        "no fallback was attempted."
    )

    def __init__(self) -> None:
        super().__init__(self.message)


class ResearchToolContractError(RuntimeError):
    """The bounded allowance for malformed or repeated model actions was exhausted."""

    code = "invalid_tool_action_limit"

    def __init__(self, *, rejected_actions: int, correction_limit: int) -> None:
        self.rejected_actions = rejected_actions
        self.correction_limit = correction_limit
        super().__init__("Research tool correction allowance exhausted.")
