from __future__ import annotations

import json
import hashlib
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from .catalog import clean_product_title, normalize_currency, validate_public_url
from .db import Database
from .jd import LoginRequiredError, ScrapeError, _valid_price
from .timeutil import now_iso


class BrowserSessionManager:
    def __init__(self, db: Database, project_root: Path):
        self.db = db
        self.project_root = project_root
        local_app_data = os.environ.get("LOCALAPPDATA")
        self.profile_dir = (
            Path(local_app_data) / "JingPriceWatch" / "jd-edge-profile"
            if local_app_data
            else project_root / "data" / "jd-edge-profile"
        )
        self.profile_root = (
            Path(local_app_data) / "JingPriceWatch" / "site-profiles"
            if local_app_data
            else project_root / "data" / "site-profiles"
        )
        self.helper = project_root / "browser_helper.mjs"
        self.edge = self._find_edge()
        self.node = self._find_node()
        self._lock = threading.Lock()
        self._login_process: subprocess.Popen | None = None
        self._login_port: int | None = None
        self._login_target_site: str | None = None
        self._login_target_label: str | None = None
        self._login_product_id: int | None = None
        self._shutdown = False

    @staticmethod
    def _find_edge() -> str | None:
        candidates = [
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return shutil.which("msedge")

    @staticmethod
    def _find_node() -> str | None:
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        candidates = [str(bundled)] if bundled.is_file() else []
        discovered = shutil.which("node")
        if discovered:
            candidates.append(discovered)
        for candidate in dict.fromkeys(candidates):
            try:
                result = subprocess.run(
                    [
                        candidate,
                        "-e",
                        "const m=Number(process.versions.node.split('.')[0]);"
                        "process.exit(m>=22&&typeof WebSocket==='function'?0:1)",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                return candidate
        return None

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _clean_env() -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key.lower() != "path"}
        env["PATH"] = os.environ.get("PATH") or os.environ.get("Path", "")
        return env

    def supported(self) -> tuple[bool, str]:
        missing: list[str] = []
        if not self.edge:
            missing.append("Microsoft Edge")
        if not self.node:
            missing.append("Node.js 22+")
        if not self.helper.is_file():
            missing.append("浏览器辅助脚本")
        return (not missing, "、".join(missing))

    def _debug_ready(self, port: int) -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                return response.status == 200
        except Exception:
            return False

    def _wait_debug(self, port: int, process: subprocess.Popen, timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._debug_ready(port):
                return
            if process.poll() is not None:
                raise ScrapeError("Edge 登录窗口启动失败")
            time.sleep(0.35)
        raise ScrapeError("等待 Edge 登录窗口超时")

    def _profile_for_site(self, site: str) -> Path:
        if site == "jd.com":
            return self.profile_dir
        digest = hashlib.sha256(site.lower().encode("utf-8")).hexdigest()[:24]
        return self.profile_root / digest

    def _launch_edge(self, port: int, url: str, *, visible: bool, site: str = "jd.com") -> subprocess.Popen:
        supported, missing = self.supported()
        if not supported:
            raise ScrapeError(f"登录抓价不可用，缺少：{missing}")
        profile_dir = self._profile_for_site(site)
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            self.edge or "msedge",
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=msEdgeSidebarV2,msEdgeFirstRunExperience",
        ]
        if not visible:
            args.extend(["--window-position=-32000,-32000", "--window-size=1280,900"])
        args.append(url)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        return subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self._clean_env(),
            creationflags=creationflags,
        )

    def _helper(self, mode: str, port: int, argument: str = "", timeout: int = 45) -> dict[str, Any]:
        if not self.node:
            raise ScrapeError("找不到 Node.js，无法读取 Edge 登录会话")
        command = [self.node, str(self.helper), mode, str(port)]
        if argument:
            command.append(argument)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=self._clean_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ScrapeError("读取 Edge 页面超时") from exc
        if result.returncode != 0:
            raise ScrapeError((result.stderr or "Edge 页面读取失败").strip()[:500])
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ScrapeError("Edge 返回了无法识别的数据") from exc

    def _close(self, port: int, process: subprocess.Popen | None) -> None:
        graceful_close_requested = False
        try:
            if self._debug_ready(port):
                result = self._helper("close", port, timeout=8)
                graceful_close_requested = bool(result.get("closed"))
        except Exception:
            pass
        if process and process.poll() is None:
            try:
                if graceful_close_requested:
                    process.wait(timeout=8)
                else:
                    process.terminate()
                    process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

    def status(self) -> dict[str, Any]:
        supported, missing = self.supported()
        login_open = bool(self._login_port and self._debug_ready(self._login_port))
        try:
            verified_sites = json.loads(self.db.get_meta("browser_sites_verified", "{}"))
        except json.JSONDecodeError:
            verified_sites = {}
        return {
            "implementation_version": "multisite-session-v1",
            "supported": supported,
            "missing": missing,
            "configured": self.db.get_meta("browser_session_ready") == "1",
            "profile_ready": self.profile_dir.is_dir(),
            "login_window_open": login_open,
            "login_target_site": self._login_target_site,
            "login_target_label": self._login_target_label,
            "login_product_id": self._login_product_id,
            "verified_sites": sorted(verified_sites),
            "last_verified_at": self.db.get_meta("browser_session_verified_at"),
        }

    def start_login(
        self,
        target_url: str | None = None,
        *,
        site: str = "jd.com",
        label: str = "京东",
        product_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._shutdown:
                raise ScrapeError("服务正在关闭，不能再打开京东登录窗口")
            if self._login_port and self._debug_ready(self._login_port):
                return self.status()
            if self._login_port or self._login_process:
                self._close(self._login_port or 0, self._login_process)
                self._login_port = None
                self._login_process = None
            port = self._free_port()
            login_url = target_url or (
                "https://passport.jd.com/new/login.aspx?ReturnUrl="
                "https%3A%2F%2Fitem.m.jd.com%2F"
            )
            login_url = validate_public_url(login_url, resolve_dns=True)
            process: subprocess.Popen | None = None
            try:
                process = self._launch_edge(port, login_url, visible=True, site=site)
                self._wait_debug(port, process)
            except Exception:
                if process:
                    self._close(port, process)
                raise
            self._login_port = port
            self._login_process = process
            self._login_target_site = site
            self._login_target_label = label
            self._login_product_id = product_id
            return self.status()

    def complete_login(self) -> dict[str, Any]:
        with self._lock:
            if not self._login_port or not self._debug_ready(self._login_port):
                raise LoginRequiredError("登录窗口未打开，请先点击“打开京东登录”")
            target_site = self._login_target_site or "jd.com"
            target_label = self._login_target_label or target_site
            product_id = self._login_product_id
            if target_site == "jd.com":
                result = self._helper("status", self._login_port, timeout=15)
                if not result.get("loggedIn"):
                    raise LoginRequiredError("尚未检测到京东登录状态，请在 Edge 中完成登录后再确认")
                self.db.set_meta("browser_session_ready", "1")
            else:
                page = self._helper("page-status", self._login_port, timeout=15)
                if page.get("loginLike"):
                    raise LoginRequiredError(f"{target_label} 页面仍显示登录表单，请完成登录后再确认")
            self._close(self._login_port, self._login_process)
            self._login_port = None
            self._login_process = None
            self._login_target_site = None
            self._login_target_label = None
            self._login_product_id = None
            try:
                verified_sites = json.loads(self.db.get_meta("browser_sites_verified", "{}"))
            except json.JSONDecodeError:
                verified_sites = {}
            verified_sites[target_site] = now_iso()
            self.db.set_meta("browser_sites_verified", json.dumps(verified_sites, ensure_ascii=False))
            self.db.set_meta("browser_session_verified_at", now_iso())
            status = self.status()
            status["completed_product_id"] = product_id
            status["completed_site"] = target_site
            status["completed_label"] = target_label
            return status

    def cancel_login(self) -> dict[str, Any]:
        with self._lock:
            if self._login_port:
                self._close(self._login_port, self._login_process)
            self._login_port = None
            self._login_process = None
            self._login_target_site = None
            self._login_target_label = None
            self._login_product_id = None
            return self.status()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            port = self._login_port
            process = self._login_process
            self._login_port = None
            self._login_process = None
            self._login_target_site = None
            self._login_target_label = None
            self._login_product_id = None
            if port or process:
                self._close(port or 0, process)

    def fetch_product(self, product: dict[str, Any]) -> dict[str, Any]:
        adapter = str(product.get("adapter") or "generic")
        site = str(product.get("site") or "")
        site_label = str(product.get("site_label") or site or "商品网站")
        if adapter == "jd" and self.db.get_meta("browser_session_ready") != "1":
            raise LoginRequiredError("京东要求登录查看价格，请先打开该商品的网站登录窗口")
        target_url = validate_public_url(str(product.get("url") or ""), resolve_dns=True)
        expected_sku = str(product.get("external_id") or product.get("sku") or "") if adapter == "jd" else ""
        config = {
            "url": target_url,
            "adapter": adapter,
            "expectedSku": expected_sku,
            "priceSelector": str(product.get("price_selector") or ""),
            "expectedCurrency": str(product.get("currency") or "XXX"),
            "site": site,
        }
        with self._lock:
            if self._shutdown:
                raise ScrapeError("服务正在关闭，已取消本次抓价")
            if self._login_port and self._debug_ready(self._login_port):
                raise ScrapeError("京东登录窗口仍然打开，请先完成或取消登录")
            port = self._free_port()
            process: subprocess.Popen | None = None
            try:
                process = self._launch_edge(port, "about:blank", visible=False, site=site or "generic")
                self._wait_debug(port, process)
                result = self._helper("scrape", port, json.dumps(config, ensure_ascii=False), timeout=65)
                self.db.set_meta(
                    "browser_last_result",
                    json.dumps(
                        {
                            "price": result.get("price"),
                            "source": result.get("source"),
                            "currency": result.get("currency"),
                            "adapter": result.get("adapter"),
                            "loginRequired": result.get("loginRequired"),
                            "riskBlocked": result.get("riskBlocked"),
                            "title": result.get("title"),
                            "url": result.get("url"),
                            "validated": result.get("validated"),
                            "expectedSku": result.get("expectedSku"),
                            "finalSku": result.get("finalSku"),
                            "errorCode": result.get("errorCode"),
                            "candidateCount": result.get("candidateCount"),
                        },
                        ensure_ascii=False,
                    ),
                )
                final_url = validate_public_url(str(result.get("url") or target_url), resolve_dns=True)
                if result.get("loginRequired"):
                    if adapter == "jd":
                        self.db.set_meta("browser_session_ready", "0")
                    raise LoginRequiredError(f"{site_label} 要求登录后查看价格，请点击该商品的“登录网站”按钮")
                if result.get("riskBlocked"):
                    raise ScrapeError(f"{site_label} 触发验证码或访问保护，请稍后重试；本次价格未写入")
                if adapter == "jd":
                    if (
                        result.get("validated") is not True
                        or str(result.get("expectedSku") or "") != expected_sku
                        or str(result.get("finalSku") or "") != expected_sku
                        or not str(result.get("source") or "").startswith("main:")
                    ):
                        raise ScrapeError("京东页面未通过目标商品与主售价校验；系统未写入本次价格")
                elif result.get("validated") is not True:
                    code = str(result.get("errorCode") or "price_missing")
                    messages = {
                        "selector_invalid": "自定义 CSS 价格选择器格式无效，请编辑后重试",
                        "selector_missing": "自定义 CSS 价格选择器没有匹配到可见价格，请编辑后重试",
                        "ambiguous_price": "页面存在多个不同的候选价格，请填写只指向主售价的 CSS 选择器",
                        "currency_ambiguous": "已找到价格但无法确认币种，请填写更精确的 CSS 选择器",
                        "price_missing": "网站未提供可验证的结构化价格，请在商品设置中填写主售价 CSS 选择器",
                        "unsafe_url": "商品页面跳转到了不安全地址，本次抓价已阻止",
                    }
                    raise ScrapeError(messages.get(code, "网站没有返回唯一且可验证的主售价，本次价格未写入"))
                price = _valid_price(result.get("price"))
                if price is None:
                    raise ScrapeError(f"{site_label} 没有返回有效价格")
                currency = normalize_currency(result.get("currency"), str(product.get("currency") or "XXX"))
                if currency == "XXX":
                    raise ScrapeError("已找到价格，但无法确认币种；本次价格未写入")
                source = str(result.get("source") or "")
                title = clean_product_title(str(result.get("title") or ""), str(product.get("name") or f"{site_label} 商品"))
                try:
                    verified_sites = json.loads(self.db.get_meta("browser_sites_verified", "{}"))
                except json.JSONDecodeError:
                    verified_sites = {}
                verified_sites[site or "unknown"] = now_iso()
                self.db.set_meta("browser_sites_verified", json.dumps(verified_sites, ensure_ascii=False))
                self.db.set_meta("browser_session_verified_at", now_iso())
                return {
                    "price": price,
                    "currency": currency,
                    "source": source,
                    "title": title,
                    # Generic remote images are intentionally not reloaded by the local dashboard.
                    "image_url": None if adapter == "generic" else result.get("imageUrl"),
                    "final_url": final_url,
                    "adapter": adapter,
                }
            finally:
                self._close(port, process)

    def fetch_price(self, sku: str) -> float:
        observation = self.fetch_product(
            {
                "sku": sku,
                "external_id": sku,
                "url": f"https://item.jd.com/{sku}.html",
                "adapter": "jd",
                "site": "jd.com",
                "site_label": "京东",
                "currency": "CNY",
            }
        )
        return float(observation["price"])
