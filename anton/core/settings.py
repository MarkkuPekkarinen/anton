from pydantic_settings import BaseSettings


class CoreSettings(BaseSettings):
    model_config = {"env_prefix": "ANTON_", "extra": "ignore"}

    # Session orchestration tuning
    max_tool_rounds: int = 25
    max_continuations: int = 3
    context_pressure_threshold: float = 0.7
    max_consecutive_errors: int = 5
    resilience_nudge_at: int = 2
    token_status_cache_ttl: float = 60.0
