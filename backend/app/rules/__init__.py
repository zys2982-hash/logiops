"""业务规则层：确定性、可单测、纯函数优先。

分层：Router → Service → Rule（本包）→ 数据由调用方传入。
LLM 只读这些规则的输出，永远不能覆盖规则的结论（尤其风险等级）。
"""

from __future__ import annotations

from app.rules import detection, eta, risk, sla, state_machine

__all__ = ["detection", "eta", "risk", "sla", "state_machine"]
