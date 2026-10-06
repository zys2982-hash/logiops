"""把车辆 / 司机的状态对齐到订单现状（用户口径 2026-10-06）。

规则与运行时一致（见 `app/services/crew.py`）：
· 未结束订单（DISPATCHED / IN_TRANSIT）→ 车 IN_TRANSIT、司机 ON_TRIP；
· 没有未结束订单的车若还停在 IN_TRANSIT → IDLE；司机若还停在 ON_TRIP → AVAILABLE；
· 车 REPAIRING（维修中，由异常单把着）和司机 OFF_DUTY（停班）**不动**。

默认**只打印**不落库；确认无误再加 `--apply`。

用法：
  PYTHONPATH=backend backend/.venv/Scripts/python.exe scripts/sync_crew_status.py
  PYTHONPATH=backend backend/.venv/Scripts/python.exe scripts/sync_crew_status.py --apply
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.db.session import session_scope
from app.models.master import Driver, Vehicle
from app.models.transport import Order
from app.repositories import Repos
from app.services import crew


def main() -> int:
    parser = argparse.ArgumentParser(description="按订单现状对齐车辆 / 司机状态")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认只打印）")
    parser.add_argument("--workspace-id", type=int, default=1)
    args = parser.parse_args()

    with session_scope() as session:
        repos = Repos(session, workspace_id=args.workspace_id)
        orders = list(session.scalars(select(Order).order_by(Order.id)))
        live = [o for o in orders if str(o.status) in crew.LIVE_ORDER_STATUSES]
        print(f"=== 订单 {len(orders)} 张（未结束 {len(live)} 张）===")
        for order in orders:
            if str(order.status) in crew.LIVE_ORDER_STATUSES:
                vehicle = session.get(Vehicle, order.vehicle_id) if order.vehicle_id else None
                driver = session.get(Driver, order.driver_id) if order.driver_id else None
                print(
                    f"  {order.order_no} {order.status:<11} 车={vehicle.plate_no if vehicle else '—'}"
                    f"({vehicle.status if vehicle else '—'}) 司机={driver.name if driver else '—'}"
                    f"({driver.status if driver else '—'})"
                )

        changes = crew.sync_with_orders(repos)
        print(f"\n=== 需要对齐 {len(changes)} 处 ===")
        for change in changes:
            print(f"  {change['kind']:<7} {change['name']:<12} {change['from']} → {change['to']}")
        if not changes:
            print("  （本来就一致）")

        if not args.apply:
            # session_scope 正常退出会 commit：干跑必须显式回滚，否则"只打印"也会写库
            session.rollback()
            print("\n这是干跑（未写库）。确认无误后加 --apply 落库。")
            return 0
        session.commit()
        print(f"\n已写入 {len(changes)} 处变更。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
