class SEIError(Exception):
    """Base error for expected user-facing failures."""


class ConfigurationError(SEIError):
    """Configuration is missing, malformed, or unsafe."""


class ProjectStateError(SEIError):
    """The target project has no usable SEI state."""


class ProviderError(SEIError):
    """An optional LLM provider failed or returned an invalid response."""
