from __future__ import annotations

from datetime import datetime, timedelta, timezone


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now() -> datetime:
    return datetime.now(CHINA_TZ)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")
