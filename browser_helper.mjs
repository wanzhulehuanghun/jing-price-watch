import { lookup } from "node:dns/promises";
import { isIP } from "node:net";
import { pathToFileURL } from "node:url";

const [mode, portText, argument = ""] = process.argv.slice(2);
const port = Number(portText);

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

class CDP {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
    this.socket = new WebSocket(url);
    this.ready = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject, timer } = this.pending.get(message.id);
        this.pending.delete(message.id);
        clearTimeout(timer);
        if (message.error) reject(new Error(message.error.message || "CDP request failed"));
        else resolve(message.result || {});
      } else if (message.method && this.events.has(message.method)) {
        for (const listener of this.events.get(message.method)) {
          Promise.resolve(listener(message.params || {})).catch(() => {});
        }
      }
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`CDP timeout: ${method}`));
      }, 20000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, listener) {
    if (!this.events.has(method)) this.events.set(method, []);
    this.events.get(method).push(listener);
  }

  close() {
    try { this.socket.close(); } catch (_) { /* already closed */ }
  }
}

async function localJson(path) {
  const response = await fetch(`http://127.0.0.1:${port}${path}`);
  if (!response.ok) throw new Error(`DevTools HTTP ${response.status}`);
  return response.json();
}

async function pageClient() {
  const targets = await localJson("/json/list");
  const pages = targets.filter(target => target.type === "page" && target.webSocketDebuggerUrl);
  const target = pages.find(item => item.url === "about:blank") || pages[0];
  if (!target) throw new Error("No browser page target found");
  const client = new CDP(target.webSocketDebuggerUrl);
  await client.ready;
  return client;
}

function publicIPv4(address) {
  const parts = address.split(".").map(Number);
  if (parts.length !== 4 || parts.some(part => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  const [a, b, c] = parts;
  if (a === 0 || a === 10 || a === 127 || a >= 224) return false;
  if (a === 100 && b >= 64 && b <= 127) return false;
  if (a === 169 && b === 254) return false;
  if (a === 172 && b >= 16 && b <= 31) return false;
  if (a === 192 && ((b === 0 && [0, 2].includes(c)) || b === 168)) return false;
  if (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) return false;
  if (a === 203 && b === 0 && c === 113) return false;
  return true;
}

function publicIPv6(address) {
  const value = address.toLowerCase().split("%")[0];
  if (value === "::" || value === "::1") return false;
  if (/^f[cd]/.test(value) || /^fe[89ab]/.test(value) || value.startsWith("ff") || value.startsWith("2001:db8")) return false;
  if (value.startsWith("::ffff:")) return publicIPv4(value.slice(7));
  return true;
}

function publicAddress(address) {
  const version = isIP(address);
  return version === 4 ? publicIPv4(address) : version === 6 ? publicIPv6(address) : false;
}

export async function safeNetworkUrl(rawUrl) {
  let parsed;
  try { parsed = new URL(rawUrl); } catch (_) { return false; }
  if (["about:", "blob:", "data:"].includes(parsed.protocol)) return true;
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return false;
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (!host || host === "localhost" || /\.(?:localhost|local|internal|lan|home)$/.test(host)) return false;
  const port = parsed.port ? Number(parsed.port) : (parsed.protocol === "https:" ? 443 : 80);
  if (!((parsed.protocol === "https:" && port === 443) || (parsed.protocol === "http:" && port === 80))) return false;
  if (isIP(host)) return publicAddress(host);
  try {
    const addresses = await lookup(host, { all: true, verbatim: true });
    return addresses.length > 0 && addresses.every(item => publicAddress(item.address));
  } catch (_) {
    return false;
  }
}

async function guardNetwork(client) {
  await client.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
  client.on("Fetch.requestPaused", async params => {
    const requestId = params.requestId;
    try {
      if (await safeNetworkUrl(params.request?.url || "")) {
        await client.send("Fetch.continueRequest", { requestId });
      } else {
        await client.send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" });
      }
    } catch (_) {
      try { await client.send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" }); } catch (_) { /* target closed */ }
    }
  });
}

async function loginStatus() {
  const client = await pageClient();
  try {
    const result = await client.send("Storage.getCookies");
    const names = new Set((result.cookies || [])
      .filter(cookie => /(^|\.)jd\.com$/.test(cookie.domain || ""))
      .map(cookie => cookie.name));
    const legacyLogin = names.has("pt_key") && names.has("pt_pin");
    const currentLogin = names.has("pin") && (names.has("thor") || names.has("TrackID"));
    return {
      loggedIn: legacyLogin || currentLogin,
      hasPin: names.has("pt_pin") || names.has("pin"),
      cookieCount: names.size,
    };
  } finally {
    client.close();
  }
}

async function closeBrowser() {
  const version = await localJson("/json/version");
  if (!version.webSocketDebuggerUrl) return { closed: false };
  const client = new CDP(version.webSocketDebuggerUrl);
  try {
    await client.send("Browser.close");
    return { closed: true };
  } catch (_) {
    return { closed: false };
  }
}

async function pageStatus() {
  const client = await pageClient();
  try {
    const evaluated = await client.send("Runtime.evaluate", {
      expression: String.raw`(() => {
        const visible = element => Boolean(element?.getClientRects().length);
        const url = location.href;
        const loginLike = /(?:login|signin|sign-in|passport|auth)(?:[/.?_-]|$)/i.test(url)
          || [...document.querySelectorAll('input[type="password"]')].some(visible);
        return { url, title: document.title, loginLike };
      })()`,
      returnByValue: true,
    });
    return evaluated.result?.value || {};
  } finally {
    client.close();
  }
}

export const JD_SCRAPE_FUNCTION = String.raw`(expectedSku => {
  const html = document.documentElement?.innerHTML || "";
  const body = document.body?.innerText || "";
  const rawFloor = html.match(/"priceFloor"\s*:\s*\{[\s\S]{0,5000}?"price"\s*:\s*"([^"]+)"/i)?.[1] || "";
  const numeric = value => {
    const text = String(value || "").replace(/,/g, "").trim();
    return /^\d{1,8}(?:\.\d{1,2})?$/.test(text) ? Number(text) : null;
  };
  const finalUrl = new URL(location.href);
  const finalSku = finalUrl.pathname.match(/^\/(\d{5,20})\.html$/)?.[1] || "";
  const candidates = [];
  const mainRoots = [
    document.querySelector(".product-price-panel"),
    document.querySelector(".product-price"),
    document.querySelector("#summary-price"),
    document.querySelector(".summary-price-wrap"),
  ].filter(Boolean);
  const mainHtml = mainRoots.map(root => root.innerHTML || "").join("\n");
  const mainText = mainRoots.map(root => root.innerText || "").join("\n");
  const loginRequired = /plogin\.m\.jd\.com|passport\.jd\.com/.test(location.href)
    || /priceLoginText[\s\S]{0,100}登录查看价格/.test(mainHtml)
    || /登录后查看(?:价格|专享价)/.test(mainText);
  const riskBlocked = /pc-frequent-pro/.test(location.href)
    || /暂时无法展示该商品的信息/.test(body)
    || /京东验证/.test(document.title);
  const skuMatched = finalUrl.hostname === "item.jd.com" && finalSku === expectedSku;

  if (skuMatched && !loginRequired && !riskBlocked) {
    const probes = [
      [document.querySelector(".product-price-panel"), ".product-price--value"],
      [document.querySelector(".product-price"), ".product-price--value"],
      [document.querySelector("#summary-price"), ".price"],
      [document.querySelector(".summary-price-wrap"), ".p-price .price"],
    ];
    for (const [root, selector] of probes) {
      if (!root) continue;
      const elements = [
        ...(root.matches(selector) ? [root] : []),
        ...root.querySelectorAll(selector),
      ];
      for (const element of elements) {
        if (!element.getClientRects().length) continue;
        if (element.closest("del,s,[class*='old-price'],[class*='market-price'],[class*='original-price'],[class*='coupon'],[class*='recommend']")) continue;
        const text = (element.innerText || element.textContent || "").replace(/,/g, "").replace(/\s+/g, " ").trim();
        const match = text.match(/^(?:¥|￥)?\s*(\d{1,8}(?:\.\d{1,2})?)$/);
        const candidate = match ? numeric(match[1]) : null;
        if (candidate != null) candidates.push({ selector, text: text.slice(0, 100), price: candidate });
      }
      if (candidates.length) break;
    }
  }

  const distinctPrices = [...new Set(candidates.map(item => item.price))];
  const validated = skuMatched && !loginRequired && !riskBlocked && distinctPrices.length === 1;
  return {
    validated,
    price: validated ? distinctPrices[0] : null,
    currency: "CNY",
    adapter: "jd",
    source: validated ? "main:" + candidates[0].selector : "",
    expectedSku,
    finalSku,
    skuMatched,
    errorCode: validated ? "" : (!skuMatched ? "sku_mismatch" : distinctPrices.length > 1 ? "ambiguous_price" : "main_price_missing"),
    rawFloor,
    loginRequired,
    riskBlocked,
    title: document.title,
    url: location.href,
    candidates: candidates.slice(0, 8),
  };
})`;

export const GENERIC_SCRAPE_FUNCTION = String.raw`(config => {
  const finalUrl = new URL(location.href);
  const bodyText = document.body?.innerText || "";
  const pageTitle = (
    document.querySelector('meta[property="og:title"]')?.content
    || document.querySelector('meta[name="twitter:title"]')?.content
    || document.title
    || ""
  ).replace(/\s+/g, " ").trim();
  const pageImage = (
    document.querySelector('meta[property="og:image"]')?.content
    || document.querySelector('meta[name="twitter:image"]')?.content
    || ""
  ).trim();
  const canonicalUrl = document.querySelector('link[rel="canonical"]')?.href || finalUrl.href;
  const expectedCurrency = /^[A-Z]{3}$/.test(String(config.expectedCurrency || "").toUpperCase())
    ? String(config.expectedCurrency).toUpperCase()
    : "XXX";

  const visible = element => {
    if (!element || !element.getClientRects().length) return false;
    const style = getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
  };
  const amount = value => {
    const text = String(value ?? "").replace(/[\u00a0\u202f]/g, " ").trim();
    if (!text || /%|每月|\/月|分期|\d+\s*期|installment|per\s+month/i.test(text)) return null;
    const tokens = text.match(/\d[\d\s.,]*\d|\d/g) || [];
    if (tokens.length !== 1) return null;
    let token = tokens[0].replace(/\s/g, "");
    const comma = token.lastIndexOf(",");
    const dot = token.lastIndexOf(".");
    if (comma >= 0 && dot >= 0) {
      token = comma > dot ? token.replace(/\./g, "").replace(",", ".") : token.replace(/,/g, "");
    } else if (comma >= 0) {
      const decimals = token.length - comma - 1;
      token = decimals > 0 && decimals <= 2 ? token.replace(/\./g, "").replace(",", ".") : token.replace(/,/g, "");
    } else if (dot >= 0 && (token.match(/\./g) || []).length > 1) {
      const decimals = token.length - dot - 1;
      token = decimals > 0 && decimals <= 2 ? token.slice(0, dot).replace(/\./g, "") + token.slice(dot) : token.replace(/\./g, "");
    }
    if (!/^\d{1,10}(?:\.\d{1,4})?$/.test(token)) return null;
    const parsed = Number(token);
    return Number.isFinite(parsed) && parsed > 0 && parsed < 100000000 ? parsed : null;
  };
  const currency = (explicit, rawText = "") => {
    const code = String(explicit || "").trim().toUpperCase();
    if (/^[A-Z]{3}$/.test(code)) return code;
    const text = String(rawText || "");
    if (/US\$|\bUSD\b/i.test(text)) return "USD";
    if (/CA\$|\bCAD\b/i.test(text)) return "CAD";
    if (/AU\$|\bAUD\b/i.test(text)) return "AUD";
    if (/€|\bEUR\b/i.test(text)) return "EUR";
    if (/£|\bGBP\b/i.test(text)) return "GBP";
    if (/₩|\bKRW\b/i.test(text)) return "KRW";
    if (/₹|\bINR\b/i.test(text)) return "INR";
    if (/₽|\bRUB\b/i.test(text)) return "RUB";
    if (/¥|￥|\bCNY\b|\bRMB\b/i.test(text)) {
      if (expectedCurrency === "JPY" || finalUrl.hostname.endsWith(".jp")) return "JPY";
      return "CNY";
    }
    if (/\$/.test(text)) return expectedCurrency !== "XXX" ? expectedCurrency : "USD";
    return expectedCurrency !== "XXX" ? expectedCurrency : "";
  };
  const rejectedPriceNode = element => Boolean(element.closest(
    "del,s,[class*='old-price'],[class*='oldPrice'],[class*='list-price'],[class*='market-price'],[class*='original-price'],[class*='coupon'],[class*='installment'],[class*='monthly'],[class*='recommend']"
  ));
  const candidates = [];
  const addCandidate = (raw, explicitCurrency, source, element = null) => {
    if (element && (!visible(element) || rejectedPriceNode(element))) return;
    const parsed = amount(raw);
    if (parsed == null) return;
    candidates.push({ price: parsed, currency: currency(explicitCurrency, raw), source, raw: String(raw).slice(0, 120) });
  };

  let selectorError = "";
  const selector = String(config.priceSelector || "").trim();
  if (selector) {
    try {
      const elements = [...document.querySelectorAll(selector)].filter(visible);
      for (const element of elements) {
        addCandidate(element.getAttribute("content") || element.getAttribute("value") || element.textContent || "", "", "custom-css", element);
      }
      if (!elements.length) selectorError = "selector_missing";
    } catch (_) {
      selectorError = "selector_invalid";
    }
  }

  let structuredName = "";
  let structuredImage = "";
  if (!selector) {
    const products = [];
    let visited = 0;
    const visit = value => {
      if (visited++ > 1000 || value == null) return;
      if (Array.isArray(value)) { value.forEach(visit); return; }
      if (typeof value !== "object") return;
      const types = Array.isArray(value["@type"]) ? value["@type"] : [value["@type"]];
      if (types.some(type => String(type || "").toLowerCase().endsWith("product"))) products.push(value);
      if (Array.isArray(value["@graph"])) value["@graph"].forEach(visit);
    };
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      if ((script.textContent || "").length > 524288) continue;
      try { visit(JSON.parse(script.textContent || "null")); } catch (_) { /* malformed JSON-LD */ }
    }
    const normalizedPath = url => {
      try { return new URL(url, finalUrl.href).pathname.replace(/\/$/, "") || "/"; } catch (_) { return ""; }
    };
    const targetPath = normalizedPath(canonicalUrl);
    const scoreProduct = product => {
      let score = 0;
      const productPath = normalizedPath(product.url || product["@id"] || "");
      if (productPath && productPath === targetPath) score += 5;
      const productName = String(product.name || "").replace(/\s+/g, " ").trim();
      if (productName && pageTitle && (pageTitle.includes(productName) || productName.includes(pageTitle))) score += 3;
      if (product.offers) score += 1;
      return score;
    };
    const bestScore = products.length ? Math.max(...products.map(scoreProduct)) : -1;
    const mainProducts = products.filter(product => scoreProduct(product) === bestScore);
    for (const product of mainProducts) {
      structuredName ||= String(product.name || "").replace(/\s+/g, " ").trim();
      const image = Array.isArray(product.image) ? product.image[0] : product.image;
      structuredImage ||= typeof image === "string" ? image : String(image?.url || image?.contentUrl || "");
      const offers = Array.isArray(product.offers) ? product.offers : [product.offers].filter(Boolean);
      for (const offer of offers) {
        const types = Array.isArray(offer?.["@type"]) ? offer["@type"] : [offer?.["@type"]];
        const aggregate = types.some(type => String(type || "").toLowerCase().endsWith("aggregateoffer"));
        if (aggregate) {
          const low = amount(offer.lowPrice);
          const high = amount(offer.highPrice);
          if (low != null && high != null && low === high) addCandidate(offer.lowPrice, offer.priceCurrency, "jsonld-aggregate");
          continue;
        }
        const specs = Array.isArray(offer?.priceSpecification) ? offer.priceSpecification : [offer?.priceSpecification].filter(Boolean);
        if (offer?.price != null) addCandidate(offer.price, offer.priceCurrency, "jsonld-offer");
        else if (specs.length === 1) addCandidate(specs[0].price, specs[0].priceCurrency || offer?.priceCurrency, "jsonld-price-specification");
      }
    }

    if (!candidates.length) {
      const metaCurrency = (
        document.querySelector('meta[property="product:price:currency"]')?.content
        || document.querySelector('[itemprop="priceCurrency"]')?.getAttribute("content")
        || ""
      );
      for (const element of document.querySelectorAll(
        'meta[property="product:price:amount"],meta[property="og:price:amount"],[itemprop="price"][content]'
      )) {
        const semanticRoot = element.closest('[itemtype*="/Product"],[itemtype*="/Offer"]');
        if (element.matches('[itemprop="price"]') && !semanticRoot) continue;
        addCandidate(element.getAttribute("content") || "", metaCurrency, "semantic-meta", element);
      }
    }
  }

  const loginRequired = /(?:login|signin|sign-in|passport|auth)(?:[/.?_-]|$)/i.test(finalUrl.href)
    || [...document.querySelectorAll('input[type="password"]')].some(visible);
  const riskBlocked = /captcha|verify|challenge|robot/i.test(finalUrl.pathname)
    || /验证码|安全验证|访问过于频繁|verify you are human|unusual traffic/i.test(document.title + "\n" + bodyText.slice(0, 5000));
  const unsafeUrl = !["http:", "https:"].includes(finalUrl.protocol)
    || finalUrl.hostname === "localhost"
    || /\.(?:localhost|local|internal|lan|home)$/.test(finalUrl.hostname);
  const distinctPrices = [...new Set(candidates.map(item => item.price))];
  const currencies = [...new Set(candidates.map(item => item.currency).filter(Boolean))];
  const chosenCurrency = currencies.length === 1 ? currencies[0] : (currencies.length === 0 ? (expectedCurrency === "XXX" ? "" : expectedCurrency) : "");
  const validated = !unsafeUrl && !loginRequired && !riskBlocked && !selectorError && distinctPrices.length === 1 && Boolean(chosenCurrency);
  let errorCode = "";
  if (unsafeUrl) errorCode = "unsafe_url";
  else if (loginRequired) errorCode = "login_required";
  else if (riskBlocked) errorCode = "captcha_or_risk";
  else if (selectorError) errorCode = selectorError;
  else if (distinctPrices.length > 1 || currencies.length > 1) errorCode = "ambiguous_price";
  else if (!distinctPrices.length) errorCode = "price_missing";
  else if (!chosenCurrency) errorCode = "currency_ambiguous";

  return {
    validated,
    adapter: "generic",
    price: validated ? distinctPrices[0] : null,
    currency: validated ? chosenCurrency : "",
    source: validated ? candidates[0].source : "",
    title: structuredName || pageTitle,
    imageUrl: structuredImage || pageImage,
    url: finalUrl.href,
    canonicalUrl,
    loginRequired,
    riskBlocked,
    errorCode,
    candidateCount: candidates.length,
    candidates: candidates.slice(0, 8),
  };
})`;

function parseScrapeConfig(raw) {
  try {
    const value = JSON.parse(raw);
    if (value && typeof value === "object") return value;
  } catch (_) { /* legacy URL argument */ }
  return { url: raw, adapter: "jd", expectedSku: new URL(raw).pathname.match(/^\/(\d{5,20})\.html$/)?.[1] || "" };
}

async function scrape(rawConfig) {
  const config = parseScrapeConfig(rawConfig);
  const url = String(config.url || "");
  const target = new URL(url);
  if (!["http:", "https:"].includes(target.protocol)) throw new Error("Invalid target product URL");
  const expectedSku = String(config.expectedSku || (target.hostname === "item.jd.com"
    ? target.pathname.match(/^\/(\d{5,20})\.html$/)?.[1]
    : ""));
  if (config.adapter === "jd" && !expectedSku) throw new Error("Invalid target JD product URL");
  const client = await pageClient();
  try {
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await guardNetwork(client);
    const navigation = await client.send("Page.navigate", { url });
    if (navigation.errorText) throw new Error(`JD navigation failed: ${navigation.errorText}`);
    for (let i = 0; i < 30; i++) {
      await sleep(500);
      const state = await client.send("Runtime.evaluate", {
        expression: "document.readyState",
        returnByValue: true,
      });
      if (state.result?.value === "complete") break;
    }
    await sleep(5000);
    const scrapeFunction = config.adapter === "jd" ? JD_SCRAPE_FUNCTION : GENERIC_SCRAPE_FUNCTION;
    const scrapeArgument = config.adapter === "jd" ? expectedSku : config;
    const evaluated = await client.send("Runtime.evaluate", {
      expression: `(${scrapeFunction})(${JSON.stringify(scrapeArgument)})`,
      returnByValue: true,
      awaitPromise: true,
    });
    return evaluated.result?.value || {};
  } finally {
    client.close();
  }
}

async function main() {
  if (!Number.isInteger(port) || port <= 0) throw new Error("Invalid DevTools port");
  let result;
  if (mode === "status") result = await loginStatus();
  else if (mode === "page-status") result = await pageStatus();
  else if (mode === "close") result = await closeBrowser();
  else if (mode === "scrape") result = await scrape(argument);
  else throw new Error(`Unknown mode: ${mode}`);
  process.stdout.write(JSON.stringify(result));
}

const isMain = Boolean(process.argv[1]) && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main().catch(error => {
    process.stderr.write(String(error?.stack || error));
    process.exit(1);
  });
}
