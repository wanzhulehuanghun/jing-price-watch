from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

from .browser_session import BrowserSessionManager
from .catalog import parse_product_input, validate_price_selector
from .db import Database
from .jd import LoginRequiredError, ScrapeError
from .notifier import send_webhook
from .timeutil import now, now_iso


class TrackerService:
    def __init__(self, db: Database, browser_session: BrowserSessionManager | None = None):
        self.db = db
        self.browser_session = browser_session
        self._job_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._job_thread: threading.Thread | None = None
        self._job_state: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "completed": 0,
            "success": 0,
            "failed": 0,
            "trigger": None,
            "error": None,
        }

    def add_product(self, value: str, custom_name: str = "", price_selector: str = "") -> dict[str, Any]:
        descriptor = parse_product_input(value)
        selector = validate_price_selector(price_selector)
        if self.db.get_product_by_key(descriptor.product_key):
            raise ValueError("该商品链接已在监控列表中")
        seed = {
            "sku": descriptor.internal_sku,
            "product_key": descriptor.product_key,
            "url": descriptor.url,
            "site": descriptor.site,
            "site_label": descriptor.site_label,
            "external_id": descriptor.external_id,
            "adapter": descriptor.adapter,
            "currency": descriptor.default_currency,
            "price_selector": selector,
            "name": custom_name.strip() or f"{descriptor.site_label} 商品",
        }
        observation: dict[str, Any] | None = None
        scrape_error: str | None = None
        try:
            observation = self._fetch_product(seed)
        except (ScrapeError, ValueError) as exc:
            scrape_error = str(exc)
        name = custom_name.strip() or (observation or {}).get("title") or seed["name"]
        currency = (observation or {}).get("currency") or descriptor.default_currency
        product = self.db.add_product(
            descriptor.internal_sku,
            descriptor.url,
            str(name),
            (observation or {}).get("image_url"),
            product_key=descriptor.product_key,
            site=descriptor.site,
            site_label=descriptor.site_label,
            external_id=descriptor.external_id,
            adapter=descriptor.adapter,
            currency=str(currency),
            price_selector=selector,
        )
        if observation:
            self.db.update_product_metadata(
                product["id"],
                resolved_url=observation.get("final_url"),
                source=observation.get("source"),
            )
            self.db.record_price(
                product["id"],
                float(observation["price"]),
                currency=str(observation["currency"]),
                source=str(observation.get("source") or ""),
            )
        elif scrape_error:
            self.db.mark_check_error(product["id"], scrape_error)
        return self.db.get_product(product["id"]) or product

    def check_product(self, product_id: int) -> dict[str, Any]:
        product = self.db.get_product(product_id)
        if not product:
            raise KeyError("商品不存在")
        try:
            observation = self._fetch_product(product)
            self.db.update_product_metadata(
                product_id,
                resolved_url=observation.get("final_url"),
                source=observation.get("source"),
            )
            price = float(observation["price"])
            result = self.db.record_price(
                product_id,
                price,
                currency=str(observation["currency"]),
                source=str(observation.get("source") or ""),
            )
            alert = result.get("alert")
            if alert:
                webhook_url = self.db.get_settings().get("webhook_url", "")
                if webhook_url:
                    try:
                        send_webhook(webhook_url, alert)
                        self.db.set_alert_delivery(alert["id"], True)
                    except Exception as exc:
                        self.db.set_alert_delivery(alert["id"], False, str(exc))
            return {"ok": True, "price": price, **result}
        except (ScrapeError, ValueError) as exc:
            self.db.mark_check_error(product_id, str(exc))
            return {"ok": False, "error": str(exc)}

    def _fetch_product(self, product: dict[str, Any]) -> dict[str, Any]:
        if not self.browser_session:
            raise LoginRequiredError("登录抓价组件不可用")
        return self.browser_session.fetch_product(product)

    def start_check_all(self, trigger: str = "manual") -> bool:
        if self._stop_event.is_set():
            return False
        if not self._job_lock.acquire(blocking=False):
            return False
        try:
            thread = threading.Thread(target=self._check_all_locked, args=(trigger,), daemon=True, name="price-check")
            self._job_thread = thread
            thread.start()
            return True
        except Exception:
            self._job_thread = None
            self._job_lock.release()
            raise

    def _check_all_locked(self, trigger: str) -> None:
        fatal_error: str | None = None
        completed_normally = False
        try:
            products = [p for p in self.db.list_products() if p["enabled"]]
            with self._state_lock:
                self._job_state.update(
                    running=True,
                    started_at=now_iso(),
                    finished_at=None,
                    total=len(products),
                    completed=0,
                    success=0,
                    failed=0,
                    trigger=trigger,
                    error=None,
                )
            for product in products:
                if self._stop_event.is_set():
                    break
                try:
                    result = self.check_product(product["id"])
                except Exception as exc:
                    self.db.mark_check_error(product["id"], str(exc))
                    result = {"ok": False, "error": str(exc)}
                with self._state_lock:
                    self._job_state["completed"] += 1
                    self._job_state["success" if result["ok"] else "failed"] += 1
                time.sleep(0.8)
            completed_normally = not self._stop_event.is_set()
        except Exception as exc:
            fatal_error = str(exc)[:500]
        finally:
            if trigger == "schedule" and completed_normally:
                try:
                    self.db.set_meta("last_daily_date", now().date().isoformat())
                except Exception as exc:
                    fatal_error = str(exc)[:500]
            with self._state_lock:
                self._job_state["running"] = False
                self._job_state["finished_at"] = now_iso()
                self._job_state["error"] = fatal_error
            self._job_lock.release()

    def shutdown(self, timeout: float = 70) -> None:
        self._stop_event.set()
        thread = self._job_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def job_state(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._job_state)


class DailyScheduler(threading.Thread):
    def __init__(self, db: Database, service: TrackerService, poll_seconds: int = 30):
        super().__init__(daemon=True, name="daily-scheduler")
        self.db = db
        self.service = service
        self.poll_seconds = poll_seconds
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                settings = self.db.get_settings()
                if settings.get("daily_enabled", True):
                    current = now()
                    scheduled = str(settings.get("daily_time", "09:00"))
                    hour, minute = (int(part) for part in scheduled.split(":", 1))
                    today = current.date().isoformat()
                    due = (current.hour, current.minute) >= (hour, minute)
                    if due and self.db.get_meta("last_daily_date") != today:
                        self.service.start_check_all("schedule")
            except Exception as exc:
                self.db.set_meta("scheduler_error", str(exc)[:500])
            self._stop_event.wait(self.poll_seconds)


def next_run_text(settings: dict[str, Any]) -> str | None:
    if not settings.get("daily_enabled", True):
        return None
    current = now()
    hour, minute = (int(part) for part in str(settings.get("daily_time", "09:00")).split(":", 1))
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target.isoformat(timespec="minutes")
