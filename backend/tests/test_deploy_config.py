"""部署相关：演示口令必须能被环境变量覆盖（公网部署的安全前提）。

背景（2026-10-06）：`github.com/zys2982-hash/logiops` 是**公开仓库**，`catalog.DEMO_PASSWORD`
这个默认值（Demo@12345）等于已经公开。所以 seed 建号时要读 `DEMO_PASSWORD` 环境变量：
服务器 `.env` 里设强口令 → `scripts/deploy-env.sh` 生成 → 重新 seed 后，仓库里的默认口令就登不进来了。

这条测试把该机制钉住：覆盖环境变量 → 重新 seed → 新口令能登录、旧口令 401。
"""

from __future__ import annotations

from app.core.config import get_settings
from app.seed import reset_demo_data
from app.seed.catalog import DEMO_PASSWORD

LOGIN = "/api/v1/auth/login"
OWNER_EMAIL = "owner@logiops.dev"


def _login(client, password: str):
    return client.post(LOGIN, json={"email": OWNER_EMAIL, "password": password})


def test_seed_password_can_be_overridden_by_env(client, db_session, bootstrap, monkeypatch):
    strong = "Deploy-2026-Strong-Pw"
    monkeypatch.setenv("DEMO_PASSWORD", strong)
    get_settings.cache_clear()  # get_settings 是 lru_cache 单例，必须清掉才读到新环境变量
    try:
        reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
        db_session.commit()

        assert _login(client, strong).status_code == 200, "环境变量里的新口令应该能登录"
        assert _login(client, DEMO_PASSWORD).status_code == 401, "仓库里的默认口令必须已经失效"
    finally:
        monkeypatch.delenv("DEMO_PASSWORD", raising=False)
        get_settings.cache_clear()


def test_seed_password_falls_back_to_repo_default(client, db_session, bootstrap):
    """没设 DEMO_PASSWORD 时（本地开发/CI）仍用仓库默认口令，开箱即用。"""
    reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()
    assert _login(client, DEMO_PASSWORD).status_code == 200
