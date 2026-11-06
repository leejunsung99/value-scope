"""Daily API call budget.

DART/KRX keys are shared with dart-market-monitor, so ValueScope limits its own daily usage
(DART_DAILY_CALL_LIMIT / KRX_DAILY_CALL_LIMIT). Every call is logged in api_call_log.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models_analysis import ApiCallLog

KST = timezone(timedelta(hours=9))


class BudgetExceeded(Exception):
    pass


def kst_day_start() -> datetime:
    now = datetime.now(KST)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class CallBudget:
    def __init__(self, session_factory: sessionmaker, provider: str, limit: int):
        self.session_factory = session_factory
        self.provider = provider
        self.limit = limit
        self._lock = threading.Lock()
        self._used: int | None = None

    def used_today(self) -> int:
        with self.session_factory() as s:
            return s.scalar(select(func.count()).select_from(ApiCallLog).where(
                ApiCallLog.provider == self.provider, ApiCallLog.called_at >= kst_day_start())) or 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used_today())

    def __call__(self, endpoint: str) -> None:
        with self._lock:
            if self._used is None:
                self._used = self.used_today()
            if self._used >= self.limit:
                raise BudgetExceeded(
                    f"{self.provider} 일일 호출 상한({self.limit}회)에 도달해 수집을 중단했습니다. "
                    f"(공시알람과 API 키를 공유하므로 상한을 두고 있습니다. .env의 {self.provider}_DAILY_CALL_LIMIT로 조정)")
            self._used += 1
            with self.session_factory() as s:
                s.add(ApiCallLog(provider=self.provider, endpoint=endpoint[:60]))
                s.commit()


def usage_summary(db: Session, limits: dict[str, int]) -> dict:
    out = {}
    for provider, limit in limits.items():
        used = db.scalar(select(func.count()).select_from(ApiCallLog).where(
            ApiCallLog.provider == provider, ApiCallLog.called_at >= kst_day_start())) or 0
        out[provider] = {"used_today": used, "limit": limit}
    return out
