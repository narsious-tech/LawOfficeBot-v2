from __future__ import annotations

from ai.config import AIConfig


def is_ai_authorized(user_id: int | None, config: AIConfig | None = None) -> bool:
    if user_id is None:
        return False
    cfg = config or AIConfig.from_env()
    return bool(cfg.admin_user_ids) and user_id in cfg.admin_user_ids
