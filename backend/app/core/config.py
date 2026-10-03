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
    # 演示数据规模：compact（默认，3 单，每种异常类型一单）| full（1000 单完整规模）
    seed_scale: str = "compact"
    # AI 分析限流窗口（秒，按"工作区+用户"计；0 = 关闭）。基线 §10 承诺 1 次/5 秒 → 429 RATE_LIMITED
    rate_limit_ai_analyze_seconds: float = 5.0
    # AI 总开关（默认 **关闭**）：项目拥有者决定先停掉全部 AI 逻辑，后续重建。
    # 关闭时不调用任何大模型、不产出建议；代码保留以便重建。测试经 conftest 置 true。
    ai_enabled: bool = False
    # ETA 重算总开关（默认 **关闭**，docs/08）：不再按车速/里程模拟到达时间。
    # 关闭后 recalc_order_eta 直接沿用既有 ETA 快照（不再变动）；代码保留以便回溯。
    eta_enabled: bool = False
    # 时钟：system = **真实时间**（项目现行口径，默认）；replay = 演示虚拟时钟
    # （DEMO_BASE_DATE + /demo/actions/tick 推进）。replay 代码保留：单元测试与一键验收仍用它保证确定性。
    clock_mode: str = "system"
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


AI_MODES: tuple[str, ...] = ("replay", "live")


def set_ai_mode(mode: str) -> str:
    """运行时切换 AI 模式（顶部横幅开关用）。

    - 立即生效：``get_settings()`` 是 lru_cache 单例，``build_provider`` 每次读它的
      ``ai_replay_enabled``，所以改完下一个分析就走新模式，**不需要重启**；
    - 不写库：重启后回到 ``.env`` 的 ``AI_MODE``（避免"库里写着 live、实际跑 replay"的错觉）；
    - 只接受 replay / live，其余抛 ``ValueError``（由接口层转 422）。
    """
    normalized = (mode or "").strip().lower()
    if normalized not in AI_MODES:
        raise ValueError(f"ai_mode 只能是 {AI_MODES} 之一，收到 {mode!r}")
    get_settings().ai_mode = normalized
    return normalized


def current_ai_mode() -> str:
    return get_settings().ai_mode.lower()


def reset_settings_cache() -> None:
    """测试用：清掉 lru_cache 重新读环境变量。"""
    get_settings.cache_clear()
