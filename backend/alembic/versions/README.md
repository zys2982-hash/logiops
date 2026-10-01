# Alembic 迁移目录

首个迁移在数据库可用后由 autogenerate 生成（不要手写）：

```powershell
cd backend
uv run alembic revision --autogenerate -m "init schema"
uv run alembic upgrade head
```

当前 `DATABASE_URL` 指向本机 MySQL（3306，账号 logiops，需先执行 `scripts/init_db.sql`）。
MySQL 未就绪时可先用 `uv run python -m app.cli init-schema` 直接建表（等价于迁移结果），
或把 `DATABASE_URL` 指到 sqlite 做本地冒烟。
