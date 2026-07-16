from __future__ import annotations

import argparse
import csv
import ipaddress
import io
import json
import mimetypes
import os
import re
import socket
import sqlite3
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pricewatch.browser_session import BrowserSessionManager
from pricewatch.catalog import validate_price_selector, validate_public_url
from pricewatch.db import Database
from pricewatch.service import DailyScheduler, TrackerService, next_run_text


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_DIR = ROOT / "data"


def configure_output_streams() -> None:
    """Keep redirected Windows logs from crashing on Chinese status text."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def normalize_bind_host(value: str) -> str:
    host = str(value or "").strip().lower()
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("服务只能绑定本机回环地址 127.0.0.1") from exc
    if address.version != 4 or not address.is_loopback:
        raise ValueError("服务只能绑定本机回环地址 127.0.0.1")
    return str(address)


class PriceWatchHandler(BaseHTTPRequestHandler):
    server_version = "JingPriceWatch/1.0"

    @property
    def db(self) -> Database:
        return self.server.db  # type: ignore[attr-defined]

    @property
    def service(self) -> TrackerService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        try:
            print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)
        except (BrokenPipeError, OSError, UnicodeError, ValueError):
            # A detached Windows process may outlive the console/pipe that
            # launched it. Request handling must never depend on stdout.
            pass

    def _json(self, payload: dict | list, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 1_000_000:
            raise ValueError("请求内容过大")
        if length == 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 格式不正确") from exc
        if not isinstance(value, dict):
            raise ValueError("请求内容必须是对象")
        return value

    def _download(self, body: bytes, content_type: str, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _is_loopback(hostname: str | None) -> bool:
        if not hostname:
            return False
        if hostname.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return False

    def _require_local_mutation(self) -> None:
        self._require_local_host()
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            raise PermissionError("已拒绝跨站请求")
        origin_text = self.headers.get("Origin", "")
        if origin_text:
            origin = urlparse(origin_text)
            origin_port = origin.port or (443 if origin.scheme == "https" else 80)
            if not self._is_loopback(origin.hostname) or origin_port != self.server.server_port:  # type: ignore[attr-defined]
                raise PermissionError("已拒绝非本机来源请求")
        length = int(self.headers.get("Content-Length", "0") or 0)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if length and content_type != "application/json":
            raise ValueError("请求必须使用 application/json")

    def _require_local_host(self) -> None:
        if not self._is_loopback(self.client_address[0] if self.client_address else None):
            raise PermissionError("仅允许本机连接")
        host = urlparse(f"//{self.headers.get('Host', '')}")
        if not self._is_loopback(host.hostname):
            raise PermissionError("仅允许本机页面操作")

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = candidate.read_bytes()
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                self._require_local_host()
            if path == "/api/dashboard":
                settings = self.db.get_settings()
                self._json(
                    {
                        "products": self.db.list_products(),
                        "alerts": self.db.list_alerts(),
                        "stats": self.db.dashboard_stats(),
                        "settings": settings,
                        "job": self.service.job_state(),
                        "next_run": next_run_text(settings),
                        "scheduler_error": self.db.get_meta("scheduler_error"),
                        "browser_session": self.service.browser_session.status() if self.service.browser_session else None,
                    }
                )
                return
            export_match = re.fullmatch(r"/api/products/(\d+)/history\.csv", path)
            if export_match:
                product_id = int(export_match.group(1))
                product = self.db.get_product(product_id)
                if not product:
                    self._json({"error": "商品不存在"}, 404)
                    return
                buffer = io.StringIO(newline="")
                writer = csv.writer(buffer)
                writer.writerow(["checked_at", "price", "currency", "source", "is_new_low"])
                for item in self.db.get_history(product_id, 2000):
                    writer.writerow(
                        [item["checked_at"], item["price"], item.get("currency"), item.get("source"), item["is_new_low"]]
                    )
                self._download(
                    ("\ufeff" + buffer.getvalue()).encode("utf-8"),
                    "text/csv; charset=utf-8",
                    f"price-history-{product_id}.csv",
                )
                return
            match = re.fullmatch(r"/api/products/(\d+)/history", path)
            if match:
                product_id = int(match.group(1))
                product = self.db.get_product(product_id)
                if not product:
                    self._json({"error": "商品不存在"}, 404)
                    return
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["365"])[0])
                self._json({"product": product, "history": self.db.get_history(product_id, limit)})
                return
            if path.startswith("/api/"):
                self._json({"error": "接口不存在"}, 404)
                return
            self._serve_static(path)
        except PermissionError as exc:
            self._json({"error": str(exc)}, 403)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            self._require_local_mutation()
            data = self._read_json()
            if path == "/api/products":
                value = str(data.get("url", "")).strip()
                name = str(data.get("name", "")).strip()
                selector = validate_price_selector(str(data.get("price_selector", "")))
                product = self.service.add_product(value, name, selector)
                self._json({"product": product}, 201)
                return
            match = re.fullmatch(r"/api/products/(\d+)/check", path)
            if match:
                result = self.service.check_product(int(match.group(1)))
                self._json(result, 200 if result["ok"] else 502)
                return
            if path == "/api/check-all":
                started = self.service.start_check_all("manual")
                self._json({"started": started}, 202 if started else 409)
                return
            if path == "/api/browser/login/start":
                if not self.service.browser_session:
                    raise RuntimeError("浏览器登录功能未初始化")
                product_id = int(data["product_id"]) if data.get("product_id") is not None else None
                if product_id is not None:
                    product = self.db.get_product(product_id)
                    if not product:
                        raise KeyError("商品不存在")
                    session = self.service.browser_session.start_login(
                        str(product["url"]),
                        site=str(product.get("site") or ""),
                        label=str(product.get("site_label") or product.get("site") or "商品网站"),
                        product_id=product_id,
                    )
                else:
                    session = self.service.browser_session.start_login()
                self._json({"browser_session": session})
                return
            if path == "/api/browser/login/complete":
                if not self.service.browser_session:
                    raise RuntimeError("浏览器登录功能未初始化")
                self._json({"browser_session": self.service.browser_session.complete_login()})
                return
            if path == "/api/browser/login/cancel":
                if not self.service.browser_session:
                    raise RuntimeError("浏览器登录功能未初始化")
                self._json({"browser_session": self.service.browser_session.cancel_login()})
                return
            if path == "/api/settings":
                updates: dict = {}
                if "daily_enabled" in data:
                    updates["daily_enabled"] = bool(data["daily_enabled"])
                if "daily_time" in data:
                    daily_time = str(data["daily_time"])
                    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_time):
                        raise ValueError("每日时间格式必须为 HH:MM")
                    updates["daily_time"] = daily_time
                if "webhook_url" in data:
                    webhook = str(data["webhook_url"]).strip()
                    updates["webhook_url"] = validate_public_url(webhook) if webhook else ""
                self._json({"settings": self.db.update_settings(updates)})
                return
            if path == "/api/alerts/read":
                self.db.mark_alerts_read()
                self._json({"ok": True})
                return
            self._json({"error": "接口不存在"}, 404)
        except (ValueError, sqlite3.IntegrityError) as exc:
            message = "该商品已在监控列表中" if isinstance(exc, sqlite3.IntegrityError) else str(exc)
            self._json({"error": message}, 400)
        except PermissionError as exc:
            self._json({"error": str(exc)}, 403)
        except KeyError as exc:
            self._json({"error": str(exc).strip("'\"")}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        try:
            self._require_local_mutation()
            match = re.fullmatch(r"/api/products/(\d+)", path)
            if not match:
                self._json({"error": "接口不存在"}, 404)
                return
            data = self._read_json()
            product = self.db.update_product(
                int(match.group(1)),
                enabled=bool(data["enabled"]) if "enabled" in data else None,
                name=str(data["name"]) if "name" in data else None,
                price_selector=validate_price_selector(str(data["price_selector"])) if "price_selector" in data else None,
            )
            if not product:
                self._json({"error": "商品不存在"}, 404)
                return
            self._json({"product": product})
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except PermissionError as exc:
            self._json({"error": str(exc)}, 403)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            self._require_local_mutation()
        except (ValueError, PermissionError) as exc:
            self._json({"error": str(exc)}, 403 if isinstance(exc, PermissionError) else 400)
            return
        match = re.fullmatch(r"/api/products/(\d+)", path)
        if not match:
            self._json({"error": "接口不存在"}, 404)
            return
        if self.db.delete_product(int(match.group(1))):
            self._json({"ok": True})
        else:
            self._json({"error": "商品不存在"}, 404)


class PriceWatchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, handler, db: Database | None = None, service: TrackerService | None = None):
        super().__init__(address, handler)
        self.db = db
        self.service = service


def main() -> None:
    configure_output_streams()
    parser = argparse.ArgumentParser(description="京价守望 - 京东历史低价提醒")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(DATA_DIR / "pricewatch.db"))
    parser.add_argument("--open-browser", action="store_true", help="服务就绪后打开浏览器")
    args = parser.parse_args()
    try:
        args.host = normalize_bind_host(args.host)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        # Bind the exclusive local port before opening or migrating SQLite so a
        # duplicate process cannot race the active instance during startup.
        server = PriceWatchServer((args.host, args.port), PriceWatchHandler)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) in {48, 98, 10048}:
            print(f"京价守望已在运行：http://{args.host}:{args.port}")
            if args.open_browser:
                webbrowser.open(f"http://{args.host}:{args.port}")
            return
        raise
    db = Database(args.db)
    browser_session = BrowserSessionManager(db, ROOT)
    service = TrackerService(db, browser_session)
    server.db = db
    server.service = service
    scheduler = DailyScheduler(db, service)
    scheduler.start()
    print(f"京价守望已启动：http://{args.host}:{args.port}")
    print("保持此窗口运行，系统会按设置的时间每日检查一次。按 Ctrl+C 停止。")
    if args.open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        scheduler.join(timeout=5)
        service.shutdown()
        browser_session.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
