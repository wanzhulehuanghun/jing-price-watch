from __future__ import annotations

import json
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .catalog import validate_public_url


class _NoWebhookRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def format_money(value: float, currency: str) -> str:
    symbols = {"CNY": "¥", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "KRW": "₩"}
    code = str(currency or "XXX").upper()
    amount = f"{value:.0f}" if code in {"JPY", "KRW"} else f"{value:.2f}"
    return f"{symbols[code]}{amount}" if code in symbols else f"{code} {amount}"


def send_webhook(url: str, alert: dict) -> None:
    if not url.strip():
        return
    safe_url = validate_public_url(url, resolve_dns=True)
    name = alert["name"]
    old_low = alert["old_low"]
    new_price = alert["new_price"]
    drop = old_low - new_price
    currency = str(alert.get("currency") or "CNY")
    product_url = alert["url"]
    new_text = format_money(new_price, currency)
    old_text = format_money(old_low, currency)
    drop_text = format_money(drop, currency)
    title = f"历史新低：{name} {new_text}"
    message = (
        f"{name}\n当前价 {new_text}，低于原历史最低 {old_text}，"
        f"再降 {drop_text}。\n{product_url}"
    )
    host = (urlparse(safe_url).hostname or "").lower()
    headers = {"User-Agent": "JingPriceWatch/1.0"}
    if host == "qyapi.weixin.qq.com":
        body = json.dumps({"msgtype": "markdown", "markdown": {"content": f"**{title}**\n>{message}"}}, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif host == "oapi.dingtalk.com":
        body = json.dumps({"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n{message}"}}, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif host == "sctapi.ftqq.com":
        body = urlencode({"title": title, "desp": message}).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(
            {"event": "price_new_low", "title": title, "content": message, "product": alert},
            ensure_ascii=False,
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(safe_url, data=body, headers=headers, method="POST")
    with build_opener(_NoWebhookRedirects()).open(request, timeout=12) as response:
        if response.status >= 400:
            raise RuntimeError(f"Webhook 返回 HTTP {response.status}")
