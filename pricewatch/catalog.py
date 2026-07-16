from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .jd import _trusted_host, canonical_url, resolve_sku


SITE_LABELS = {
    "jd.com": "京东",
    "taobao.com": "淘宝",
    "tmall.com": "天猫",
    "pinduoduo.com": "拼多多",
    "vip.com": "唯品会",
    "suning.com": "苏宁易购",
    "dangdang.com": "当当",
    "amazon.cn": "亚马逊中国",
    "amazon.com": "Amazon",
}

CHINESE_SHOP_DOMAINS = {
    "jd.com",
    "taobao.com",
    "tmall.com",
    "pinduoduo.com",
    "vip.com",
    "suning.com",
    "dangdang.com",
}

BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")


@dataclass(slots=True)
class ProductDescriptor:
    product_key: str
    internal_sku: str
    url: str
    site: str
    site_label: str
    external_id: str
    adapter: str
    default_currency: str


def registrable_hint(hostname: str) -> str:
    """Return a stable display/adapter hint without pretending to be a PSL parser."""
    host = hostname.lower().strip(".")
    for domain in sorted(SITE_LABELS, key=len, reverse=True):
        if host == domain or host.endswith("." + domain):
            return domain
    return host[4:] if host.startswith("www.") else host


def site_label(hostname: str) -> str:
    hint = registrable_hint(hostname)
    return SITE_LABELS.get(hint, hint)


def _blocked_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not address.is_global


def validate_public_url(value: str, *, resolve_dns: bool = False) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("请输入商品链接")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("商品链接格式不正确") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("仅支持 http:// 或 https:// 商品链接")
    if parsed.username or parsed.password:
        raise ValueError("商品链接不能包含用户名或密码")
    if not parsed.hostname:
        raise ValueError("商品链接缺少网站域名")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().strip(".")
    except UnicodeError as exc:
        raise ValueError("商品链接域名无效") from exc
    if (
        hostname == "localhost"
        or hostname.endswith(BLOCKED_HOST_SUFFIXES)
        or _blocked_ip(hostname)
    ):
        raise ValueError("不能监控本机、局域网或保留地址")
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        raise ValueError("商品链接只能使用标准的 HTTP/HTTPS 端口")

    if resolve_dns:
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    hostname,
                    port or (443 if parsed.scheme.lower() == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as exc:
            raise ValueError("商品网站域名暂时无法解析") from exc
        if not addresses or any(_blocked_ip(address) for address in addresses):
            raise ValueError("商品网站解析到了不安全的本机或局域网地址")

    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = display_host if port is None else f"{display_host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def validate_price_selector(value: str) -> str:
    selector = value.strip()
    if not selector:
        return ""
    if len(selector) > 300:
        raise ValueError("CSS 价格选择器不能超过 300 个字符")
    if any(ord(char) < 32 for char in selector):
        raise ValueError("CSS 价格选择器包含无效控制字符")
    if re.search(r":has\s*\(|(?:^|[\s>,+~])(?:script|style|iframe|object|embed)(?:$|[\s.#:[>,+~])", selector, re.I):
        raise ValueError("CSS 价格选择器包含不允许的高开销或非价格元素")
    return selector


def parse_product_input(value: str) -> ProductDescriptor:
    raw = value.strip()
    if re.fullmatch(r"\d{5,20}", raw):
        sku = resolve_sku(raw)
        return ProductDescriptor(
            product_key=f"jd:{sku}",
            internal_sku=sku,
            url=canonical_url(sku),
            site="jd.com",
            site_label="京东",
            external_id=sku,
            adapter="jd",
            default_currency="CNY",
        )

    normalized = validate_public_url(raw)
    hostname = (urlsplit(normalized).hostname or "").lower()
    if _trusted_host(hostname):
        sku = resolve_sku(normalized)
        return ProductDescriptor(
            product_key=f"jd:{sku}",
            internal_sku=sku,
            url=canonical_url(sku),
            site="jd.com",
            site_label="京东",
            external_id=sku,
            adapter="jd",
            default_currency="CNY",
        )

    site = registrable_hint(hostname)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    default_currency = "CNY" if site in CHINESE_SHOP_DOMAINS or hostname.endswith(".cn") else "XXX"
    return ProductDescriptor(
        product_key=f"web:{digest}",
        internal_sku=f"web_{digest}",
        url=normalized,
        site=site,
        site_label=site_label(hostname),
        external_id=hostname,
        adapter="generic",
        default_currency=default_currency,
    )


def clean_product_title(value: str, fallback: str) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    return title[:300] if title else fallback


def normalize_currency(value: object, fallback: str = "XXX") -> str:
    currency = str(value or "").strip().upper()
    return currency if re.fullmatch(r"[A-Z]{3}", currency) else fallback
