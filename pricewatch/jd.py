from __future__ import annotations

import html
import json
import re
import ssl
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class ScrapeError(RuntimeError):
    pass


class LoginRequiredError(ScrapeError):
    pass


@dataclass(slots=True)
class ProductSnapshot:
    sku: str
    url: str
    name: str
    image_url: str | None
    price: float | None = None


def _trusted_host(host: str) -> bool:
    host = host.lower().split(":")[0]
    return host in {"jd.com", "3.cn"} or host.endswith((".jd.com", ".3.cn"))


class _TrustedJdRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme.lower() != "https" or not _trusted_host(parsed.hostname or ""):
            raise ValueError("京东短链接跳转到了非京东或非 HTTPS 地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def extract_sku(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("请输入京东商品链接或 SKU")
    if re.fullmatch(r"\d{5,20}", value):
        return value
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower().split(":")[0]
    if not _trusted_host(host):
        raise ValueError("仅支持 jd.com 或 3.cn 域名下的京东商品链接")
    patterns = [
        r"/(\d{5,20})\.html(?:[/?#]|$)",
        r"/(\d{5,20})(?:[/?#]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, parsed.path)
        if match:
            return match.group(1)
    query = parse_qs(parsed.query)
    for key in ("sku", "skuId", "wareId", "productId"):
        if key in query and query[key] and re.fullmatch(r"\d{5,20}", query[key][0]):
            return query[key][0]
    raise ValueError("未能从链接中识别商品 SKU；请粘贴 item.jd.com 商品详情页链接")


def resolve_sku(value: str) -> str:
    """识别直链 SKU；对京东短链接再安全地跟随一次跳转。"""
    try:
        return extract_sku(value)
    except ValueError as direct_error:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if not parsed.netloc or not _trusted_host(parsed.netloc):
            raise direct_error
        request = Request(
            parsed.geturl(),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        try:
            with build_opener(_TrustedJdRedirects()).open(request, timeout=15) as response:
                final_url = response.geturl()
                try:
                    return extract_sku(final_url)
                except ValueError:
                    page = response.read(512_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"京东短链接解析失败：{exc}") from exc
        match = re.search(r"(?:item\.jd\.com/|item\.m\.jd\.com/product/)(\d{5,20})\.html", page)
        if match:
            return match.group(1)
        raise direct_error


def canonical_url(sku: str) -> str:
    return f"https://item.jd.com/{sku}.html"


def _request_text(url: str, timeout: int = 15, referer: str = "https://www.jd.com/") -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer,
        },
    )
    context = ssl.create_default_context()
    try:
        with build_opener().open(request, timeout=timeout) as response:
            data = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
    except Exception as exc:
        raise ScrapeError(f"请求京东失败：{exc}") from exc


def _clean_title(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"【.*?】-京东$|【.*?】.*?-京东$|\s*-\s*京东$", "", value).strip()


def fetch_product_info(sku: str) -> ProductSnapshot:
    url = canonical_url(sku)
    page = _request_text(url)
    title_patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:title["\']',
        r"<title[^>]*>(.*?)</title>",
    ]
    name = ""
    for pattern in title_patterns:
        match = re.search(pattern, page, re.I | re.S)
        if match:
            name = _clean_title(match.group(1))
            if name:
                break
    if not name or "京东-欢迎登录" in name:
        name = f"京东商品 {sku}"
    image = None
    image_patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
        r'id=["\']spec-img["\'][^>]+data-origin=["\'](.*?)["\']',
        r'id=["\']spec-img["\'][^>]+src=["\'](.*?)["\']',
    ]
    for pattern in image_patterns:
        match = re.search(pattern, page, re.I | re.S)
        if match:
            image = html.unescape(match.group(1)).strip()
            if image.startswith("//"):
                image = "https:" + image
            elif image.startswith("/"):
                image = "https://item.jd.com" + image
            break
    return ProductSnapshot(sku=sku, url=url, name=name, image_url=image)


def _valid_price(value: object) -> float | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not price.is_finite() or price <= 0 or price >= Decimal("100000000"):
        return None
    return float(price.quantize(Decimal("0.01")))


def _extract_mobile_price(page: str) -> float | None:
    floor_match = re.search(
        r'"priceFloor"\s*:\s*\{.{0,5000}?"price"\s*:\s*"([^"]+)"',
        page,
        re.I | re.S,
    )
    if floor_match:
        raw_price = floor_match.group(1).strip()
        price = _valid_price(raw_price)
        if price is not None:
            return price
        if "?" in raw_price:
            raise LoginRequiredError("该商品要求登录后查看价格，请在设置中完成京东登录")
    if re.search(r'priceLoginText.{0,120}登录查看价格', page, re.I | re.S):
        raise LoginRequiredError("该商品要求登录后查看价格，请在设置中完成京东登录")
    return None


def fetch_price(sku: str) -> float:
    product_url = canonical_url(sku)
    errors: list[str] = []
    mobile_url = f"https://item.m.jd.com/product/{sku}.html"
    try:
        mobile_page = _request_text(mobile_url, referer=product_url)
        mobile_price = _extract_mobile_price(mobile_page)
        if mobile_price is not None:
            return mobile_price
        errors.append("移动商品页未返回公开价格")
    except LoginRequiredError:
        raise
    except ScrapeError as exc:
        errors.append(str(exc))

    endpoint = f"https://p.3.cn/prices/mgets?skuIds={quote('J_' + sku)}&type=1"
    try:
        payload = json.loads(_request_text(endpoint, timeout=7, referer=product_url))
        if isinstance(payload, list) and payload:
            for key in ("p", "op", "m"):
                price = _valid_price(payload[0].get(key))
                if price is not None:
                    return price
        errors.append("价格接口未返回有效售价")
    except (ScrapeError, json.JSONDecodeError, AttributeError) as exc:
        errors.append(str(exc))

    try:
        page = _request_text(product_url)
        structured_patterns = [
            r'<meta[^>]+property=["\']product:price:amount["\'][^>]+content=["\']([0-9.]+)',
            r'"offers"\s*:\s*\{.*?"price"\s*:\s*"?([0-9.]+)',
            r'"price"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"',
        ]
        for pattern in structured_patterns:
            match = re.search(pattern, page, re.I | re.S)
            if match:
                price = _valid_price(match.group(1))
                if price is not None:
                    return price
        errors.append("商品页也未包含可验证的结构化价格")
    except ScrapeError as exc:
        errors.append(str(exc))
    raise ScrapeError("；".join(errors) + "。京东可能要求登录或触发了风控，请稍后重试。")


def snapshot_from_input(value: str, custom_name: str = "", *, fetch_current: bool = True) -> ProductSnapshot:
    sku = resolve_sku(value)
    try:
        snapshot = fetch_product_info(sku)
    except ScrapeError:
        snapshot = ProductSnapshot(sku=sku, url=canonical_url(sku), name=f"京东商品 {sku}", image_url=None)
    if custom_name.strip():
        snapshot.name = custom_name.strip()
    if fetch_current:
        try:
            snapshot.price = fetch_price(sku)
        except ScrapeError:
            snapshot.price = None
    return snapshot
