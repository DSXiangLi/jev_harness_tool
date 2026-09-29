from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_TOTAL_TOKEN_BUDGET = 64_000
DEFAULT_SINGLE_TOKEN_BUDGET = 32_000

DEFAULT_NOUL_PASS_THRESHOLD = 0.8
DEFAULT_NOUL_FAIL_THRESHOLD = 0.2
DEFAULT_CHOICE_TOP_THRESHOLD = 0.6
DEFAULT_CHOICE_MIN_MARGIN = 0.15


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str = "https://jevtypesafeai.com"
    model: str = "jev-latest"
    timeout_s: float = 60.0
    max_retries: int = 3
    backoff_base_s: float = 0.5

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        api_key = os.environ.get("JV_API_KEY")
        if not api_key:
            raise ConfigError("JV_API_KEY is required; set it in the project .env file or environment")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("JEV_BASE_URL", cls.base_url),
            model=os.environ.get("JEV_MODEL", cls.model),
            timeout_s=float(os.environ.get("JEV_TIMEOUT_S", cls.timeout_s)),
            max_retries=int(os.environ.get("JEV_MAX_RETRIES", cls.max_retries)),
        )
