const state = { data: null, query: "", polling: null, loginBusy: false, loginProductId: null };

const icons = {
  image: '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>',
  chart: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 3v18h18M7 16l4-5 3 3 5-7"/></svg>',
  refresh: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 6v5h-5M4 18v-5h5M6.1 9A7 7 0 0 1 18.5 6M17.9 15A7 7 0 0 1 5.5 18"/></svg>',
  pause: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></svg>',
  play: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m7 4 13 8-13 8Z"/></svg>',
  trash: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m5 4v6m4-6v6"/></svg>',
  alert: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 9v4m0 4h.01M10.3 3.7 2.4 17.4A2 2 0 0 0 4.1 20h15.8a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z"/></svg>',
  check: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m4 12 5 5L20 6"/></svg>',
  login: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5m5 5H3"/></svg>',
  edit: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m4 20 4.2-1 10.9-10.9a2.1 2.1 0 0 0-3-3L5.2 16 4 20ZM14.5 6.5l3 3"/></svg>'
};

async function api(path, options = {}) {
  const { timeoutMs = 30000, ...fetchOptions } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(path, {
      ...fetchOptions,
      signal: fetchOptions.signal || controller.signal,
      headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) },
    });
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请重试");
    throw error;
  } finally {
    clearTimeout(timer);
  }
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* ignore */ }
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function money(value, currency = "CNY") {
  if (value == null) return "—";
  const code = /^[A-Z]{3}$/.test(String(currency || "").toUpperCase()) ? String(currency).toUpperCase() : "CNY";
  try {
    return new Intl.NumberFormat("zh-CN", { style: "currency", currency: code, maximumFractionDigits: code === "JPY" || code === "KRW" ? 0 : 2 }).format(Number(value));
  } catch (_) {
    return `${code} ${Number(value).toFixed(2)}`;
  }
}

function sourceLabel(source) {
  const labels = {
    "jsonld-offer": "通用 JSON-LD",
    "jsonld-price-specification": "通用 JSON-LD",
    "jsonld-aggregate": "结构化单一售价",
    "semantic-meta": "语义价格",
    "custom-css": "自定义 CSS",
  };
  if (String(source || "").startsWith("main:")) return "京东严格适配器";
  return labels[source] || (source ? "站点解析" : "等待首次价格");
}

function dateTime(value, short = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const opts = short
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" };
  return new Intl.DateTimeFormat("zh-CN", opts).format(date);
}

function relativeTime(value) {
  if (!value) return "尚未检查";
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.max(0, Math.floor(diff / 60000));
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function sparkline(history) {
  if (!history || history.length < 2) return '<span class="muted-chart">数据积累中</span>';
  const values = history.map(item => Number(item.price));
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const w = 155, h = 52, pad = 4;
  const points = values.map((v, i) => `${pad + i * (w - pad * 2) / (values.length - 1)},${pad + (max - v) * (h - pad * 2) / range}`);
  const lowIndex = values.lastIndexOf(min);
  const [lx, ly] = points[lowIndex].split(",");
  const area = `${pad},${h - pad} ${points.join(" ")} ${w - pad},${h - pad}`;
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" aria-label="最近 ${values.length} 次价格趋势"><path class="grid" d="M4 ${h - 5}H${w - 4}"/><polygon class="area" points="${area}"/><polyline class="line" points="${points.join(" ")}"/><circle class="low-point" cx="${lx}" cy="${ly}" r="3.5"/></svg>`;
}

function productHTML(product) {
  const history = product.recent_history || [];
  const change = product.highest_price && product.last_price ? ((product.highest_price - product.last_price) / product.highest_price * 100) : 0;
  const site = product.site_label || product.site || "商品网站";
  const external = product.adapter === "jd" && product.external_id ? `<span>商品号 ${escapeHTML(product.external_id)}</span>` : "";
  const errorRecovery = product.check_error ? `<div class="recovery-actions" role="group" aria-label="抓价失败恢复操作">
    ${/登录/.test(product.check_error) ? `<button class="button button-secondary" type="button" data-action="login" data-focus-key="recovery-login">登录 ${escapeHTML(site)}</button>` : ""}
    ${/CSS|选择器|主售价|结构化价格|多个不同/.test(product.check_error) ? '<button class="button button-secondary" type="button" data-action="edit" data-focus-key="recovery-edit">配置价格选择器</button>' : ""}
    <button class="button button-ghost" type="button" data-action="check" data-focus-key="recovery-check">重试</button>
  </div>` : "";
  return `<article class="product-row ${product.enabled ? "" : "disabled"}" data-product-id="${product.id}">
    <div class="product-main">
      <div class="product-image">${product.image_url ? `<img src="${escapeHTML(product.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.hidden=true">` : icons.image}</div>
      <div class="product-copy">
        <h3 title="${escapeHTML(product.name)}"><a href="${escapeHTML(product.url)}" target="_blank" rel="noopener noreferrer" data-focus-key="product-link">${escapeHTML(product.name)}</a></h3>
        <div class="product-meta"><span class="site-chip">${escapeHTML(site)}</span>${external}<span>${escapeHTML(product.currency || "—")}</span><span>${product.record_count} 条记录</span><span class="source-chip">${escapeHTML(sourceLabel(product.last_price_source))}</span><span class="state-label ${product.enabled ? "" : "paused"}">${product.enabled ? "监控中" : "已暂停"}</span></div>
        ${product.check_error ? `<div class="error-line">${icons.alert}<span>${escapeHTML(product.check_error)}</span></div>` : ""}
        ${errorRecovery}
      </div>
    </div>
    <div class="price-block"><span>当前价格</span><strong>${money(product.last_price, product.currency)}</strong><small>${relativeTime(product.last_checked_at)}</small></div>
    <div class="price-block low"><span>历史最低</span><strong>${money(product.lowest_price, product.currency)}</strong><small>${change > 0 ? `较区间高点低 ${change.toFixed(1)}%` : "当前基准"}</small></div>
    <div class="chart-block"><span>最近趋势</span>${sparkline(history)}</div>
    <div class="row-actions">
      <button class="action-button" data-action="history" data-focus-key="row-history" aria-label="查看 ${escapeHTML(product.name)} 的价格历史" title="价格历史">${icons.chart}</button>
      <button class="action-button" data-action="check" data-focus-key="row-check" aria-label="立即检查 ${escapeHTML(product.name)}" title="立即检查">${icons.refresh}</button>
      <button class="action-button" data-action="login" data-focus-key="row-login" aria-label="登录 ${escapeHTML(site)}" title="登录商品网站">${icons.login}</button>
      <button class="action-button" data-action="edit" data-focus-key="row-edit" aria-label="编辑 ${escapeHTML(product.name)} 的抓价设置" title="抓价设置">${icons.edit}</button>
      <button class="action-button" data-action="toggle" data-focus-key="row-toggle" aria-label="${product.enabled ? "暂停" : "恢复"}监控 ${escapeHTML(product.name)}" title="${product.enabled ? "暂停监控" : "恢复监控"}">${product.enabled ? icons.pause : icons.play}</button>
      <button class="action-button danger" data-action="delete" data-focus-key="row-delete" aria-label="删除 ${escapeHTML(product.name)}" title="删除">${icons.trash}</button>
    </div>
  </article>`;
}

function renderProducts() {
  const list = document.querySelector("#products-list");
  const focusedElement = document.activeElement?.closest?.("[data-product-id] [data-focus-key]");
  const focusKey = focusedElement ? {
    productId: focusedElement.closest("[data-product-id]")?.dataset.productId,
    key: focusedElement.dataset.focusKey,
  } : null;
  const query = state.query.trim().toLowerCase();
  const products = (state.data?.products || []).filter(product => {
    const haystack = [product.name, product.site, product.site_label, product.external_id, product.currency].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });
  if (!products.length) {
    list.innerHTML = `<div class="empty-state"><span class="empty-icon">${icons.chart}</span><strong>${query ? "没有匹配的商品" : "还没有监控商品"}</strong><p>${query ? "换个名称、网站或商品编号试试。" : "在上方粘贴商品详情页链接；第一次可信价格会成为历史基准。"}</p></div>`;
    document.querySelector("#products-status").textContent = query ? "没有搜索结果" : "监控列表为空";
    return;
  }
  list.innerHTML = products.map(productHTML).join("");
  document.querySelector("#products-status").textContent = `已显示 ${products.length} 件商品`;
  if (focusKey?.productId && focusKey.key) {
    list.querySelector(`[data-product-id="${focusKey.productId}"] [data-focus-key="${focusKey.key}"]`)?.focus();
  }
}

function renderAlerts() {
  const list = document.querySelector("#alerts-list");
  const focusedAlert = document.activeElement?.closest?.("[data-alert-id] [data-focus-key]");
  const focusKey = focusedAlert ? {
    alertId: focusedAlert.closest("[data-alert-id]")?.dataset.alertId,
    key: focusedAlert.dataset.focusKey,
  } : null;
  const alerts = state.data?.alerts || [];
  const unread = alerts.filter(a => !a.is_read).length;
  const badge = document.querySelector("#alert-badge");
  badge.hidden = unread === 0;
  badge.textContent = unread;
  if (!alerts.length) {
    list.innerHTML = `<div class="empty-state"><span class="empty-icon">${icons.check}</span><strong>暂时没有新低</strong><p>价格低于此前所有有效记录时，提醒会出现在这里。</p></div>`;
    return;
  }
  list.innerHTML = alerts.map(alert => `<article class="alert-item ${alert.is_read ? "" : "unread"}" data-alert-id="${alert.id}">
    <div class="alert-top"><strong>${escapeHTML(alert.name)}</strong><time datetime="${escapeHTML(alert.created_at)}">${dateTime(alert.created_at, true)}</time></div>
    <div class="alert-price">${money(alert.new_price, alert.currency)}</div>
    <div class="alert-detail">原最低 <del>${money(alert.old_low, alert.currency)}</del>，再降 <strong>${money(alert.old_low - alert.new_price, alert.currency)}</strong>${alert.delivered === 0 ? "<br><span class=\"error-line\">Webhook 发送失败</span>" : ""}</div>
    <a href="${escapeHTML(alert.url)}" target="_blank" rel="noopener noreferrer" data-focus-key="alert-link">去商品页查看 →</a>
  </article>`).join("");
  if (focusKey?.alertId && focusKey.key) {
    list.querySelector(`[data-alert-id="${focusKey.alertId}"] [data-focus-key="${focusKey.key}"]`)?.focus();
  }
}

function renderDashboard() {
  const { stats, job, settings, next_run: nextRun } = state.data;
  document.querySelector("#metric-enabled").textContent = stats.enabled || 0;
  document.querySelector("#metric-total").textContent = `共 ${stats.total || 0} 件 · ${stats.sites || 0} 个网站`;
  document.querySelector("#metric-lows").textContent = stats.new_lows_30d || 0;
  document.querySelector("#metric-errors").textContent = stats.errors || 0;
  document.querySelector("#metric-checked").textContent = stats.last_checked_at ? relativeTime(stats.last_checked_at) : "尚无记录";
  document.querySelector("#metric-job").textContent = job.running ? `正在检查 ${job.completed}/${job.total}` : "等待每日任务";
  const next = document.querySelector("#next-run");
  const pill = document.querySelector("#scheduler-pill");
  next.textContent = settings.daily_enabled && nextRun ? `下次检查 ${dateTime(nextRun, true)}` : "自动检查已关闭";
  pill.classList.toggle("offline", !settings.daily_enabled);
  const progress = document.querySelector("#job-progress");
  progress.hidden = !job.running;
  if (job.running) {
    document.querySelector("#job-progress-label").textContent = job.trigger === "schedule" ? "每日任务正在检查价格…" : "正在检查全部商品…";
    document.querySelector("#job-progress-count").textContent = `${job.completed} / ${job.total}`;
    document.querySelector("#job-progress-bar").value = job.total ? job.completed / job.total * 100 : 0;
  }
  renderProducts();
  renderAlerts();
  renderBrowserSession(state.data.browser_session);
  maybeNotify(alertsUnread());
}

function renderBrowserSession(session) {
  if (!session) return;
  const badge = document.querySelector("#session-badge");
  const title = document.querySelector("#session-title");
  const description = document.querySelector("#session-description");
  const start = document.querySelector("#login-start-button");
  const complete = document.querySelector("#login-complete-button");
  const cancel = document.querySelector("#login-cancel-button");
  if (!badge || !title) return;
  badge.className = "session-badge";
  start.hidden = false;
  complete.hidden = true;
  cancel.hidden = true;
  start.disabled = false;
  complete.disabled = state.loginBusy;
  cancel.disabled = state.loginBusy;
  if (!session.supported) {
    badge.classList.add("error");
    badge.querySelector("b").textContent = "不可用";
    title.textContent = "当前电脑缺少登录抓价组件";
    description.textContent = `缺少：${session.missing || "未知组件"}`;
    start.disabled = true;
  } else if (session.login_window_open) {
    badge.classList.add("waiting");
    badge.querySelector("b").textContent = "等待登录";
    title.textContent = `请在新打开的 Edge 窗口中登录${session.login_target_label || "商品网站"}`;
    description.textContent = "登录成功后回到这里，点击“我已完成登录”。";
    state.loginProductId = session.login_product_id ?? state.loginProductId;
    start.hidden = true;
    complete.hidden = false;
    cancel.hidden = false;
  } else if (session.configured) {
    badge.classList.add("ready");
    badge.querySelector("b").textContent = "已就绪";
    title.textContent = "专用浏览器会话可用";
    const siteCount = (session.verified_sites || []).length;
    description.textContent = session.last_verified_at ? `最近验证：${dateTime(session.last_verified_at)}${siteCount ? ` · ${siteCount} 个网站` : ""}` : "每日任务会使用各网站保存在本机的登录状态。";
    if (!state.loginBusy) start.innerHTML = `${icons.refresh}更新登录状态`;
  } else if (session.profile_ready) {
    badge.querySelector("b").textContent = "可用";
    title.textContent = "专用浏览器目录已就绪";
    description.textContent = "通用公开价格可直接抓取；京东或其他受限网站可从商品行打开登录。";
    if (!state.loginBusy) start.innerHTML = `${icons.login}打开京东登录`;
  } else {
    badge.querySelector("b").textContent = "未登录";
    title.textContent = "可按商品登录对应购物网站";
    description.textContent = "登录窗口使用隔离的本机 Edge 会话，不会读取账号密码。";
    if (!state.loginBusy) start.innerHTML = `${icons.login}打开京东登录`;
  }
  if (state.loginBusy) start.disabled = true;
}

function alertsUnread() {
  return (state.data?.alerts || []).filter(a => !a.is_read);
}

async function loadDashboard(silent = false) {
  try {
    state.data = await api("/api/dashboard");
    renderDashboard();
    document.querySelector("#settings-button").disabled = false;
    document.querySelector("#connection-banner").hidden = true;
    document.body.classList.remove("service-offline");
  } catch (error) {
    if (!silent) toast(error.message, "error");
    document.querySelector("#scheduler-pill")?.classList.add("offline");
    document.querySelector("#next-run").textContent = "服务连接失败";
    document.querySelector("#connection-banner").hidden = false;
    document.querySelector("#settings-button").disabled = true;
    document.body.classList.add("service-offline");
  }
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.innerHTML = `${kind === "error" ? icons.alert : icons.check}<div>${escapeHTML(message)}</div>`;
  document.querySelector("#toast-region").append(el);
  setTimeout(() => el.remove(), 4200);
}

function setBusy(button, busy, label = "处理中…") {
  if (busy) {
    button.dataset.original = button.innerHTML;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = `<span class="spinner" style="width:18px;height:18px;border-width:2px"></span>${label}`;
  } else {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    if (button.dataset.original) button.innerHTML = button.dataset.original;
  }
}

function validateSelectorInput(input, errorField) {
  const selector = input.value.trim();
  input.removeAttribute("aria-invalid");
  errorField.hidden = true;
  if (!selector) return true;
  try {
    document.createElement("div").matches(selector);
    if (/:has\s*\(|(?:^|[\s>,+~])(?:script|style|iframe|object|embed)(?:$|[\s.#:[>,+~])/i.test(selector)) throw new Error("unsafe selector");
    return true;
  } catch (_) {
    errorField.textContent = "CSS 选择器格式无效，或包含不允许的页面元素。";
    errorField.hidden = false;
    input.setAttribute("aria-invalid", "true");
    return false;
  }
}

async function addProduct(event) {
  event.preventDefault();
  const button = document.querySelector("#add-button");
  const url = document.querySelector("#product-url").value.trim();
  const name = document.querySelector("#product-name").value.trim();
  const priceSelector = document.querySelector("#price-selector").value.trim();
  const errorField = document.querySelector("#product-url-error");
  const selectorInput = document.querySelector("#price-selector");
  const selectorError = document.querySelector("#price-selector-error");
  errorField.hidden = true;
  document.querySelector("#product-url").removeAttribute("aria-invalid");
  if (!validateSelectorInput(selectorInput, selectorError)) {
    document.querySelector("#advanced-fields").hidden = false;
    document.querySelector("#advanced-toggle").setAttribute("aria-expanded", "true");
    selectorInput.focus();
    return;
  }
  setBusy(button, true, "正在识别…");
  try {
    const result = await api("/api/products", { method: "POST", body: JSON.stringify({ url, name, price_selector: priceSelector }), timeoutMs: 90000 });
    event.target.reset();
    document.querySelector("#advanced-fields").hidden = true;
    document.querySelector("#advanced-toggle").setAttribute("aria-expanded", "false");
    toast(result.product.last_price != null ? `已添加，首次价格 ${money(result.product.last_price, result.product.currency)}` : "已添加；请按商品行提示完成登录或配置主价选择器", result.product.last_price != null ? "success" : "");
    await loadDashboard(true);
  } catch (error) {
    if (priceSelector && /CSS|选择器/.test(error.message)) {
      selectorError.textContent = error.message;
      selectorError.hidden = false;
      selectorInput.setAttribute("aria-invalid", "true");
      document.querySelector("#advanced-fields").hidden = false;
      document.querySelector("#advanced-toggle").setAttribute("aria-expanded", "true");
      selectorInput.focus();
    } else {
      errorField.textContent = `${error.message}。请检查商品详情页链接；特殊网站可展开“高级选项”填写主售价 CSS 选择器。`;
      errorField.hidden = false;
      document.querySelector("#product-url").setAttribute("aria-invalid", "true");
      document.querySelector("#product-url").focus();
    }
    toast(error.message, "error");
  }
  finally { setBusy(button, false); }
}

async function productAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const row = button.closest("[data-product-id]");
  const id = Number(row.dataset.productId);
  const product = state.data.products.find(p => p.id === id);
  const action = button.dataset.action;
  if (action === "history") return openHistory(product);
  if (action === "edit") return openProductEditor(product, button);
  if (action === "login") {
    state.loginProductId = product.id;
    if (!document.querySelector("#settings-dialog").open) openSettings();
    return startSiteLogin(product);
  }
  if (action === "delete") {
    if (!confirm(`删除“${product.name}”及其全部价格记录？`)) return;
    try { await api(`/api/products/${id}`, { method: "DELETE" }); toast("商品及历史记录已删除"); await loadDashboard(true); } catch (error) { toast(error.message, "error"); }
    return;
  }
  const showProgress = action === "check";
  if (showProgress) setBusy(button, true, "");
  else button.disabled = true;
  try {
    if (action === "toggle") {
      await api(`/api/products/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: !product.enabled }) });
      toast(product.enabled ? "已暂停每日监控" : "已恢复每日监控");
    } else if (action === "check") {
      const result = await api(`/api/products/${id}/check`, { method: "POST", body: "{}" });
      toast(result.is_new_low ? `命中历史新低：${money(result.price, product.currency)}` : `检查完成：${money(result.price, product.currency)}`, result.is_new_low ? "success" : "");
    }
    await loadDashboard(true);
  } catch (error) { toast(error.message, "error"); await loadDashboard(true); }
  finally {
    if (showProgress) setBusy(button, false);
    else button.disabled = false;
  }
}

function openProductEditor(product, trigger) {
  state.editReturnFocus = trigger || null;
  document.querySelector("#product-edit-id").value = product.id;
  document.querySelector("#product-edit-name").value = product.name || "";
  document.querySelector("#product-edit-selector").value = product.price_selector || "";
  document.querySelector("#product-edit-selector").removeAttribute("aria-invalid");
  document.querySelector("#product-edit-selector-error").hidden = true;
  document.querySelector("#product-edit-link").href = product.url;
  document.querySelector("#product-dialog").showModal();
  document.querySelector("#product-edit-name").focus();
}

function closeProductEditor() {
  document.querySelector("#product-dialog").close();
  state.editReturnFocus?.focus();
  state.editReturnFocus = null;
}

async function saveProductSettings(event) {
  event.preventDefault();
  const button = document.querySelector("#product-save");
  const id = Number(document.querySelector("#product-edit-id").value);
  const name = document.querySelector("#product-edit-name").value.trim();
  const priceSelector = document.querySelector("#product-edit-selector").value.trim();
  const selectorInput = document.querySelector("#product-edit-selector");
  const selectorError = document.querySelector("#product-edit-selector-error");
  if (!validateSelectorInput(selectorInput, selectorError)) {
    selectorInput.focus();
    return;
  }
  setBusy(button, true, "正在保存…");
  try {
    await api(`/api/products/${id}`, { method: "PATCH", body: JSON.stringify({ name, price_selector: priceSelector }) });
    closeProductEditor();
    toast("设置已保存，正在重新检查商品", "success");
    const result = await api(`/api/products/${id}/check`, { method: "POST", body: "{}", timeoutMs: 90000 });
    const product = state.data.products.find(item => item.id === id);
    toast(`检查完成：${money(result.price, product?.currency)}`, result.is_new_low ? "success" : "");
    await loadDashboard(true);
  } catch (error) {
    if (/CSS|选择器/.test(error.message) && document.querySelector("#product-dialog").open) {
      selectorError.textContent = error.message;
      selectorError.hidden = false;
      selectorInput.setAttribute("aria-invalid", "true");
      selectorInput.focus();
    }
    toast(error.message, "error");
    await loadDashboard(true);
  } finally {
    setBusy(button, false);
  }
}

async function checkAll() {
  const button = document.querySelector("#check-all-button");
  setBusy(button, true, "已启动");
  try { await api("/api/check-all", { method: "POST", body: "{}" }); toast("全部商品检查任务已启动"); await loadDashboard(true); }
  catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

function openSettings() {
  if (!state.data) {
    toast("监控数据仍在加载，请稍后重试", "error");
    loadDashboard();
    return;
  }
  const settings = state.data.settings;
  document.querySelector("#daily-enabled").checked = settings.daily_enabled;
  document.querySelector("#daily-time").value = settings.daily_time;
  document.querySelector("#webhook-url").value = settings.webhook_url || "";
  renderBrowserSession(state.data.browser_session);
  document.querySelector("#settings-dialog").showModal();
}

async function startSiteLogin(product = null) {
  if (state.loginBusy) return;
  const button = document.querySelector("#login-start-button");
  let focusTarget = null;
  state.loginBusy = true;
  renderBrowserSession(state.data.browser_session);
  setBusy(button, true, "正在打开…");
  try {
    const result = await api("/api/browser/login/start", {
      method: "POST",
      body: JSON.stringify(product ? { product_id: product.id } : {}),
      timeoutMs: 45000,
    });
    state.data.browser_session = result.browser_session;
    state.loginProductId = product?.id ?? result.browser_session.login_product_id ?? null;
    renderBrowserSession(result.browser_session);
    focusTarget = "#login-complete-button";
    toast(`Edge 登录窗口已打开，请在其中完成${product?.site_label || "京东"}登录`);
  } catch (error) { toast(error.message, "error"); }
  finally {
    state.loginBusy = false;
    setBusy(button, false);
    renderBrowserSession(state.data.browser_session);
    document.querySelector(focusTarget || "#login-start-button")?.focus();
  }
}

async function completeSiteLogin() {
  if (state.loginBusy) return;
  const button = document.querySelector("#login-complete-button");
  let focusTarget = null;
  state.loginBusy = true;
  renderBrowserSession(state.data.browser_session);
  setBusy(button, true, "正在验证…");
  try {
    const result = await api("/api/browser/login/complete", { method: "POST", body: "{}" });
    state.data.browser_session = result.browser_session;
    renderBrowserSession(result.browser_session);
    focusTarget = "#login-start-button";
    const productId = result.browser_session.completed_product_id ?? state.loginProductId;
    toast(`${result.browser_session.completed_label || "网站"}登录会话已保存，正在重新检查`, "success");
    try {
      if (productId != null) await api(`/api/products/${productId}/check`, { method: "POST", body: "{}", timeoutMs: 90000 });
      else await api("/api/check-all", { method: "POST", body: "{}" });
    } catch (error) { toast(error.message, "error"); }
    state.loginProductId = null;
    await loadDashboard(true);
  } catch (error) { toast(error.message, "error"); }
  finally {
    state.loginBusy = false;
    setBusy(button, false);
    renderBrowserSession(state.data.browser_session);
    document.querySelector(focusTarget || "#login-complete-button")?.focus();
  }
}

async function cancelSiteLogin() {
  if (state.loginBusy) return;
  const button = document.querySelector("#login-cancel-button");
  let cancelled = false;
  state.loginBusy = true;
  renderBrowserSession(state.data.browser_session);
  button.disabled = true;
  try {
    const result = await api("/api/browser/login/cancel", { method: "POST", body: "{}" });
    state.data.browser_session = result.browser_session;
    state.loginProductId = null;
    renderBrowserSession(result.browser_session);
    cancelled = true;
    toast("已取消本次登录");
  } catch (error) { toast(error.message, "error"); }
  finally {
    state.loginBusy = false;
    button.disabled = false;
    renderBrowserSession(state.data.browser_session);
    document.querySelector(cancelled ? "#login-start-button" : "#login-complete-button")?.focus();
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const button = document.querySelector("#save-settings");
  setBusy(button, true, "保存中…");
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({
      daily_enabled: document.querySelector("#daily-enabled").checked,
      daily_time: document.querySelector("#daily-time").value,
      webhook_url: document.querySelector("#webhook-url").value.trim(),
    }) });
    document.querySelector("#settings-dialog").close();
    toast("设置已保存", "success");
    await loadDashboard(true);
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

function chartSVG(history, currency = "CNY") {
  if (history.length < 2) return `<div class="empty-state"><strong>至少需要 2 条记录才能绘制趋势</strong><p>每日检查后会逐渐形成完整曲线。</p></div>`;
  const compact = window.matchMedia("(max-width: 480px)").matches;
  const width = compact ? 400 : 740, height = compact ? 260 : 280, left = compact ? 58 : 60, right = 18, top = 18, bottom = 38;
  const values = history.map(x => Number(x.price));
  const min = Math.min(...values), max = Math.max(...values), margin = Math.max((max - min) * .15, max * .025, 1);
  const low = Math.max(0, min - margin), high = max + margin, range = high - low;
  const x = i => left + i * (width - left - right) / (history.length - 1);
  const y = v => top + (high - v) * (height - top - bottom) / range;
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const area = `${left},${height - bottom} ${points.join(" ")} ${width - right},${height - bottom}`;
  const grids = Array.from({ length: 5 }, (_, i) => {
    const v = low + (high - low) * (4 - i) / 4;
    const yy = top + i * (height - top - bottom) / 4;
    return `<path class="gridline" d="M${left} ${yy}H${width-right}"/><text x="${left-8}" y="${yy+4}" text-anchor="end">${escapeHTML(money(v, currency))}</text>`;
  }).join("");
  const labelIndexes = [...new Set([0, Math.floor((history.length - 1) / 2), history.length - 1])];
  const labels = labelIndexes.map((i, n) => `<text x="${x(i)}" y="${height-12}" text-anchor="${n === 0 ? "start" : n === labelIndexes.length - 1 ? "end" : "middle"}">${dateTime(history[i].checked_at, true).split(" ")[0]}</text>`).join("");
  const circles = history.map((item, i) => `<circle class="${item.is_new_low ? "low" : ""}" cx="${x(i)}" cy="${y(item.price)}" r="${item.is_new_low ? 4.5 : 3}"><title>${dateTime(item.checked_at)} · ${money(item.price, item.currency || currency)}</title></circle>`).join("");
  return `<svg class="${compact ? "compact-chart" : ""}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${grids}<path class="axis" d="M${left} ${height-bottom}H${width-right}"/><polygon class="area" points="${area}"/><polyline class="line" points="${points.join(" ")}"/>${circles}${labels}</svg>`;
}

async function openHistory(product) {
  const dialog = document.querySelector("#history-dialog");
  document.querySelector("#history-title").textContent = product.name;
  document.querySelector("#history-export").href = `/api/products/${product.id}/history.csv`;
  document.querySelector("#history-summary").innerHTML = '<div><span>正在读取</span><strong>—</strong></div>';
  document.querySelector("#history-chart").innerHTML = '<div class="loading-state small"><span class="spinner"></span></div>';
  document.querySelector("#history-table").innerHTML = "";
  if (!dialog.open) dialog.showModal();
  try {
    const result = await api(`/api/products/${product.id}/history?limit=365`);
    const history = result.history;
    const prices = history.map(x => Number(x.price));
    const current = prices.at(-1), low = prices.length ? Math.min(...prices) : null, high = prices.length ? Math.max(...prices) : null;
    document.querySelector("#history-summary").innerHTML = `<div><span>当前价格</span><strong>${money(current, product.currency)}</strong></div><div><span>历史最低</span><strong>${money(low, product.currency)}</strong></div><div><span>记录区间最高</span><strong>${money(high, product.currency)}</strong></div>`;
    const chart = document.querySelector("#history-chart");
    chart.setAttribute("aria-label", `${product.name} 价格趋势，币种 ${product.currency}，历史最低 ${money(low, product.currency)}`);
    chart.innerHTML = chartSVG(history, product.currency);
    document.querySelector("#history-table").innerHTML = [...history].reverse().map(item => `<tr><td>${dateTime(item.checked_at)}</td><td>${money(item.price, item.currency || product.currency)}</td><td>${escapeHTML(sourceLabel(item.source))}</td><td class="${item.is_new_low ? "new-low-cell" : ""}">${item.is_new_low ? "历史新低" : "正常记录"}</td></tr>`).join("") || '<tr><td colspan="4">暂无记录</td></tr>';
  } catch (error) {
    document.querySelector("#history-summary").innerHTML = '<div><span>读取状态</span><strong>失败</strong></div>';
    document.querySelector("#history-chart").innerHTML = `<div class="empty-state"><strong>价格历史读取失败</strong><p>${escapeHTML(error.message)}</p><button class="button button-secondary" id="history-retry" type="button">重新读取</button></div>`;
    document.querySelector("#history-table").innerHTML = '<tr><td colspan="4">读取失败，请重试</td></tr>';
    document.querySelector("#history-retry")?.addEventListener("click", () => openHistory(product), { once: true });
    toast(error.message, "error");
  }
}

async function enableNotifications() {
  if (!("Notification" in window)) return toast("当前浏览器不支持系统通知", "error");
  const permission = await Notification.requestPermission();
  if (permission === "granted") { localStorage.setItem("pricewatch-notify", "on"); toast("浏览器提醒已开启", "success"); }
  else toast("未获得浏览器通知权限", "error");
}

function maybeNotify(alerts) {
  if (!("Notification" in window) || Notification.permission !== "granted" || localStorage.getItem("pricewatch-notify") !== "on") return;
  let stored = [];
  try {
    const parsed = JSON.parse(localStorage.getItem("pricewatch-seen-alerts") || "[]");
    if (Array.isArray(parsed)) stored = parsed;
  } catch (_) {
    localStorage.removeItem("pricewatch-seen-alerts");
  }
  const seen = new Set(stored);
  const unseen = alerts.filter(a => !seen.has(a.id));
  unseen.forEach(alert => {
    new Notification("京价守望 · 历史新低", { body: `${alert.name}\n${money(alert.new_price, alert.currency)}（原最低 ${money(alert.old_low, alert.currency)}）`, tag: `price-alert-${alert.id}` });
    seen.add(alert.id);
  });
  localStorage.setItem("pricewatch-seen-alerts", JSON.stringify([...seen].slice(-200)));
}

document.querySelector("#add-form").addEventListener("submit", addProduct);
document.querySelector("#advanced-toggle").addEventListener("click", event => {
  const fields = document.querySelector("#advanced-fields");
  fields.hidden = !fields.hidden;
  event.currentTarget.setAttribute("aria-expanded", String(!fields.hidden));
  if (!fields.hidden) document.querySelector("#product-name").focus();
});
document.querySelector("#product-url").addEventListener("input", event => {
  document.querySelector("#product-url-error").hidden = true;
  event.currentTarget.removeAttribute("aria-invalid");
});
document.querySelector("#price-selector").addEventListener("blur", event => {
  validateSelectorInput(event.currentTarget, document.querySelector("#price-selector-error"));
});
document.querySelector("#price-selector").addEventListener("input", event => validateSelectorInput(event.currentTarget, document.querySelector("#price-selector-error")));
document.querySelector("#product-edit-selector").addEventListener("input", event => validateSelectorInput(event.currentTarget, document.querySelector("#product-edit-selector-error")));
document.querySelector("#products-list").addEventListener("click", productAction);
document.querySelector("#check-all-button").addEventListener("click", checkAll);
document.querySelector("#search-input").addEventListener("input", event => { state.query = event.target.value; renderProducts(); });
document.querySelector("#retry-connection").addEventListener("click", () => loadDashboard());
document.querySelector("#settings-button").addEventListener("click", openSettings);
document.querySelector("#settings-form").addEventListener("submit", saveSettings);
document.querySelector("#settings-close").addEventListener("click", () => document.querySelector("#settings-dialog").close());
document.querySelector("#settings-cancel").addEventListener("click", () => document.querySelector("#settings-dialog").close());
document.querySelector("#login-start-button").addEventListener("click", () => startSiteLogin());
document.querySelector("#login-complete-button").addEventListener("click", completeSiteLogin);
document.querySelector("#login-cancel-button").addEventListener("click", cancelSiteLogin);
document.querySelector("#product-form").addEventListener("submit", saveProductSettings);
document.querySelector("#product-dialog-close").addEventListener("click", closeProductEditor);
document.querySelector("#product-dialog-cancel").addEventListener("click", closeProductEditor);
document.querySelector("#history-close").addEventListener("click", () => document.querySelector("#history-dialog").close());
document.querySelector("#notification-button").addEventListener("click", enableNotifications);
document.querySelector("#read-all-button").addEventListener("click", async () => { try { await api("/api/alerts/read", { method: "POST", body: "{}" }); await loadDashboard(true); } catch (error) { toast(error.message, "error"); } });
["settings-dialog", "history-dialog"].forEach(id => document.querySelector(`#${id}`).addEventListener("click", event => { if (event.target === event.currentTarget) event.currentTarget.close(); }));
document.querySelector("#product-dialog").addEventListener("click", event => { if (event.target === event.currentTarget) closeProductEditor(); });

loadDashboard();
state.polling = setInterval(() => loadDashboard(true), 15000);
