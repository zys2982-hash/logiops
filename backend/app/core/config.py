"""全局配置：唯一的环境变量入口（对应基线文档 §5.2）。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 应用
    app_env: str = "local"
    api_prefix: str = "/api/v1"
    secret_key: str = "dev-only-change-me"
    access_token_ttl_minutes: int = 720
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 数据库
    database_url: str = "mysql+pymysql://logiops:logiops@127.0.0.1:3306/logiops?charset=utf8mb4"
    test_database_url: str = ""

    # 演示与可复现
    demo_base_date: str = "2026-09-30T09:00:00+08:00"
    demo_random_seed: int = 20260930
    clock_mode: str = "replay"
    ai_mode: str = "replay"
    ai_replay_dir: str = "tests/fixtures/ai"

    # LLM
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature_analysis: float = 0.2
    llm_temperature_notice: float = 0.3
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    llm_max_input_tokens: int = 8000
    llm_max_output_tokens: int = 4000
    llm_daily_cost_limit_cny: float = 20.0

    # 规则阈值
    detect_stall_minutes: int = 120
    detect_debounce_minutes: int = 30
    risk_vip_upgrade: bool = True
    knowledge_top_k: int = 5

    @property
    def is_local(self) -> bool:
        return self.app_env.lower() in {"local", "dev", "development", "test"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def ai_replay_enabled(self) -> bool:
        return self.ai_mode.lower() != "live"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """测试用：清掉 lru_cache 重新读环境变量。"""
    get_settings.cache_clear()
