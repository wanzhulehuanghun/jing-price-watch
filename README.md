# 京价守望（JingPriceWatch）

一个本地优先的多购物网站价格监控系统。粘贴商品详情页链接后，系统每天检查一次价格、保存完整历史，并在价格严格低于此前最低记录时提醒。

京东使用经过 SKU 和主售价容器校验的严格适配器；其他网站优先读取标准 `Product → Offer` JSON-LD、语义价格标签，必要时可为商品填写一个 CSS 主价选择器。系统无法唯一确认当前售价、币种或目标商品时，会拒绝写入而不是猜价。

## 功能

- 任意公开 HTTP(S) 购物网站商品链接
- 京东严格 SKU / 最终地址 / 主售价校验
- JSON-LD、Microdata、OpenGraph 语义价格解析
- 每件商品可配置 CSS 主价选择器
- CNY、USD、EUR、GBP、JPY、KRW 等多币种显示与隔离比较
- 每个购物网站独立的 Edge 登录会话
- 每日定时检查、手动单件/全部检查
- 历史最低提醒、浏览器通知与 Webhook
- 价格曲线、详细记录和 CSV 导出
- SQLite 自动迁移、正式库迁移前备份
- URL、DNS、私网访问、跨站 API 与 Cookie 泄漏防护

## 适用边界

“多站点”不等于绕过所有网站限制。以下情况可能需要登录、CSS 选择器，或无法无人值守抓取：

- 验证码、滑块、频繁访问保护
- 强制使用 App 或完全不在网页中呈现价格
- 地区价、会员价、优惠券后价、秒杀资格
- 同页存在多个规格或价格区间，且无法确定当前变体
- 网站频繁改版或禁止自动化访问

请合理控制频率并遵守目标网站的服务条款。默认每件商品每天检查一次；不确定价格不会进入历史，也不会触发提醒。

## Windows 快速开始

要求：Python 3.11+、Node.js 22+、Microsoft Edge。

1. 下载并解压项目源码（或使用 Git 克隆），进入 `jd_price_tracker` 目录。
2. 双击 `start.bat`；启动器会先核对 Python、Node.js 与 Edge 版本。
3. 打开 `http://127.0.0.1:8765`。
4. 粘贴商品详情页链接，点击“添加并首次检查”。
5. 保持服务运行；需要登录 Windows 后自动启动时，在 PowerShell 中执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-autostart.ps1
```

项目也会优先使用 Codex Desktop 自带的 Python/Node 运行时（如果存在）。

## 特殊网站与 CSS 选择器

标准商品页通常无需配置。自动解析失败时：

1. 在商品网站中打开详情页。
2. 使用 Edge 开发者工具检查“当前主售价”元素。
3. 复制一个只匹配该主售价的 CSS 选择器。
4. 在商品行点击“抓价设置”，填写选择器并保存。

选择器结果必须可见且只有一个明确价格。划线价、市场价、优惠券、月供/分期和推荐商品价格会被排除；多个不同候选价格会被拒绝。

示例测试站点：`books.toscrape.com` 的价格选择器为 `.price_color`。

## 网站登录

商品需要登录时，点击商品行的“登录商品网站”按钮：

1. 系统打开独立 Edge 窗口并进入该商品页。
2. 在网站页面正常登录。
3. 回到设置窗口点击“我已完成登录”。
4. 系统关闭登录窗口并重新检查该商品。

密码由目标网站直接处理。不同网站使用相互隔离的 Edge 配置目录；Cookie 不进入 SQLite、API、日志或 Git。

## 提醒

- 站内提醒：保存在右侧“新低提醒”。
- 浏览器提醒：页面打开并授权通知时可用。
- Webhook：支持企业微信机器人、钉钉机器人、Server酱；其他公网 HTTP(S) 标准端口地址收到标准 JSON。为防止访问本机网络，Webhook 不跟随重定向。

首次有效价格只建立基准。相同价或更高价不会提醒；只有严格低于旧历史最低价才生成新提醒。

## 数据与后台运行

- 数据库：`data/pricewatch.db`
- 后台日志：`data/service.log`、`data/service-error.log`
- 网站会话：`%LOCALAPPDATA%\JingPriceWatch\`

数据库、日志、备份和浏览器会话均已被 `.gitignore` 排除。备份 `pricewatch.db` 即可保留商品、历史与提醒；恢复前先停止服务。

后台启动：

```powershell
powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File .\start-background.ps1
```

## 安全设计

- 服务硬性限制为 IPv4 回环地址（默认 `127.0.0.1`），拒绝绑定局域网或公网地址。
- 写接口同时校验客户端地址、本机 Host、Origin、`Sec-Fetch-Site` 和 JSON Content-Type。
- 拒绝 `file:`、`javascript:`、带账号密码、非标准端口、本机、私网和保留地址。
- Python 在导航前校验 DNS；浏览器层拦截全部请求并再次拒绝非公网目标。
- 每个购物网站使用独立浏览器 Profile。
- 通用站点的远程图片不会由本地管理页面重新加载。

更完整的说明见 [SECURITY.md](SECURITY.md)。

## 开发与测试

Python 测试：

```powershell
python -B -m unittest discover -s tests -v
```

JavaScript 语法与浏览器提取测试：

```powershell
npm ci
npm run check
npm run test:browser
```

测试覆盖 URL 安全、旧数据库迁移、币种隔离、严格新低、京东 SKU、通用 JSON-LD、自定义 CSS、歧义价格、登录页识别和私网阻断。GitHub Actions 会在 Linux/Chromium 和 Windows/Edge 上执行核心检查，并在 Windows 启动真实本地服务验证 API 与页面。

## 目录

```text
app.py                         本地 HTTP/API 服务
browser_helper.mjs             Edge CDP、网络拦截与页面价格提取
pricewatch/catalog.py          多站点 URL、站点身份与安全策略
pricewatch/browser_session.py  隔离登录会话与抓价编排
pricewatch/db.py               SQLite 模型、迁移、历史和提醒
pricewatch/service.py          任务、检查和每日调度
web/                           响应式本地管理界面
tests/                         Python 与浏览器回归测试
```

## License

[MIT](LICENSE)
