import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { GENERIC_SCRAPE_FUNCTION, safeNetworkUrl } from "../browser_helper.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

assert.equal(await safeNetworkUrl("http://localhost/product"), false);
assert.equal(await safeNetworkUrl("http://127.0.0.1/product"), false);
assert.equal(await safeNetworkUrl("http://[::1]/product"), false);
assert.equal(await safeNetworkUrl("file:///etc/passwd"), false);

const browser = await chromium.launch(process.platform === "win32" ? { channel: "msedge", headless: true } : { headless: true });
const page = await browser.newPage();
let fixture = "";
await page.route("https://shop.example/**", route => route.fulfill({
  status: 200,
  contentType: "text/html; charset=utf-8",
  body: fixture,
}));

async function extract(html, config = {}) {
  fixture = html;
  await page.goto("https://shop.example/products/widget", { waitUntil: "domcontentloaded" });
  return page.evaluate(
    `(${GENERIC_SCRAPE_FUNCTION})(${JSON.stringify({
      adapter: "generic",
      url: "https://shop.example/products/widget",
      expectedCurrency: "XXX",
      priceSelector: "",
      ...config,
    })})`,
  );
}

const semantic = await extract(`<!doctype html><html><head>
  <title>Semantic Widget</title>
  <link rel="canonical" href="https://shop.example/products/widget">
  <script type="application/ld+json">{
    "@context":"https://schema.org","@type":"Product","name":"Semantic Widget",
    "url":"https://shop.example/products/widget",
    "offers":{"@type":"Offer","price":"19.99","priceCurrency":"USD"}
  }</script>
</head><body><main>Widget</main></body></html>`);
assert.equal(semantic.validated, true);
assert.equal(semantic.price, 19.99);
assert.equal(semantic.currency, "USD");
assert.equal(semantic.source, "jsonld-offer");

const custom = await extract(`<!doctype html><html><head><title>Book</title></head>
  <body><main><span class="current-price">£51.77</span></main></body></html>`, {
  priceSelector: ".current-price",
  expectedCurrency: "GBP",
});
assert.equal(custom.validated, true);
assert.equal(custom.price, 51.77);
assert.equal(custom.currency, "GBP");
assert.equal(custom.source, "custom-css");

const ambiguous = await extract(`<!doctype html><html><body><main>
  <span class="price">$10.00</span><span class="price">$12.00</span>
</main></body></html>`, { priceSelector: ".price", expectedCurrency: "USD" });
assert.equal(ambiguous.validated, false);
assert.equal(ambiguous.errorCode, "ambiguous_price");

const currentOnly = await extract(`<!doctype html><html><body><main>
  <del class="old-price price">$15.00</del><span class="price">$12.00</span>
</main></body></html>`, { priceSelector: ".price", expectedCurrency: "USD" });
assert.equal(currentOnly.validated, true);
assert.equal(currentOnly.price, 12);

const login = await extract(`<!doctype html><html><body><form><input type="password"></form></body></html>`, {
  priceSelector: ".price",
  expectedCurrency: "USD",
});
assert.equal(login.validated, false);
assert.equal(login.errorCode, "login_required");

await browser.close();
console.log("browser helper tests: OK");
