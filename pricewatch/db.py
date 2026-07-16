from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .timeutil import now_iso


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    product_key TEXT,
    site TEXT NOT NULL DEFAULT 'jd.com',
    site_label TEXT NOT NULL DEFAULT '京东',
    external_id TEXT,
    adapter TEXT NOT NULL DEFAULT 'jd',
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    image_url TEXT,
    currency TEXT NOT NULL DEFAULT 'CNY',
    price_selector TEXT,
    last_price_source TEXT,
    last_resolved_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_price REAL,
    lowest_price REAL,
    lowest_at TEXT,
    check_error TEXT
);

CREATE TABLE IF NOT EXISTS price_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price REAL NOT NULL CHECK(price >= 0),
    currency TEXT NOT NULL DEFAULT 'CNY',
    source TEXT,
    checked_at TEXT NOT NULL,
    is_new_low INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_price_records_product_time
ON price_records(product_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    old_low REAL NOT NULL,
    new_price REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    created_at TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    delivered INTEGER,
    delivery_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


DEFAULT_SETTINGS: dict[str, Any] = {
    "daily_enabled": True,
    "daily_time": "09:00",
    "webhook_url": "",
}


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self._backup_legacy_if_needed()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )

    def _backup_legacy_if_needed(self) -> None:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return
        source = sqlite3.connect(self.path, timeout=15)
        try:
            has_products = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'products'"
            ).fetchone()
            if not has_products:
                return
            columns = {row[1] for row in source.execute("PRAGMA table_info(products)").fetchall()}
            if "product_key" in columns:
                return
            backup_path = self.path.with_suffix(self.path.suffix + ".pre-v2.bak")
            if backup_path.exists():
                return
            target = sqlite3.connect(backup_path)
            try:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("旧数据库自动备份完整性检查失败")
            finally:
                target.close()
        finally:
            source.close()

    @staticmethod
    def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _migrate(self, conn: sqlite3.Connection) -> None:
        product_columns = self._column_names(conn, "products")
        additions = {
            "product_key": "TEXT",
            "site": "TEXT NOT NULL DEFAULT 'jd.com'",
            "site_label": "TEXT NOT NULL DEFAULT '京东'",
            "external_id": "TEXT",
            "adapter": "TEXT NOT NULL DEFAULT 'jd'",
            "currency": "TEXT NOT NULL DEFAULT 'CNY'",
            "price_selector": "TEXT",
            "last_price_source": "TEXT",
            "last_resolved_url": "TEXT",
        }
        for column, definition in additions.items():
            if column not in product_columns:
                conn.execute(f"ALTER TABLE products ADD COLUMN {column} {definition}")
        conn.execute(
            """
            UPDATE products
            SET product_key = COALESCE(product_key, 'jd:' || sku),
                external_id = COALESCE(external_id, sku),
                site = COALESCE(NULLIF(site, ''), 'jd.com'),
                site_label = COALESCE(NULLIF(site_label, ''), '京东'),
                adapter = COALESCE(NULLIF(adapter, ''), 'jd'),
                currency = COALESCE(NULLIF(currency, ''), 'CNY')
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_product_key ON products(product_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_site_enabled ON products(site, enabled)")

        record_columns = self._column_names(conn, "price_records")
        if "currency" not in record_columns:
            conn.execute("ALTER TABLE price_records ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY'")
        if "source" not in record_columns:
            conn.execute("ALTER TABLE price_records ADD COLUMN source TEXT")

        alert_columns = self._column_names(conn, "alerts")
        if "currency" not in alert_columns:
            conn.execute("ALTER TABLE alerts ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY'")
        conn.execute("PRAGMA user_version = 2")

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def add_product(
        self,
        sku: str,
        url: str,
        name: str,
        image_url: str | None,
        *,
        product_key: str | None = None,
        site: str = "jd.com",
        site_label: str = "京东",
        external_id: str | None = None,
        adapter: str = "jd",
        currency: str = "CNY",
        price_selector: str = "",
    ) -> dict[str, Any]:
        stamp = now_iso()
        product_key = product_key or f"jd:{sku}"
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO products(
                    sku, product_key, site, site_label, external_id, adapter,
                    url, name, image_url, currency, price_selector, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sku,
                    product_key,
                    site,
                    site_label,
                    external_id,
                    adapter,
                    url,
                    name,
                    image_url,
                    currency,
                    price_selector or None,
                    stamp,
                    stamp,
                ),
            )
            row = conn.execute("SELECT * FROM products WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._dict(row) or {}

    def get_product_by_key(self, product_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM products WHERE product_key = ?", (product_key,)).fetchone()
        return self._dict(row)

    def get_product(self, product_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return self._dict(row)

    def list_products(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       COUNT(r.id) AS record_count,
                       MAX(r.price) AS highest_price
                FROM products p
                LEFT JOIN price_records r ON r.product_id = p.id
                GROUP BY p.id
                ORDER BY p.enabled DESC, p.created_at DESC
                """
            ).fetchall()
            products = [dict(row) for row in rows]
            for product in products:
                history = conn.execute(
                    """
                    SELECT price, checked_at, is_new_low
                    FROM price_records
                    WHERE product_id = ?
                    ORDER BY checked_at DESC LIMIT 30
                    """,
                    (product["id"],),
                ).fetchall()
                product["recent_history"] = [dict(row) for row in reversed(history)]
        return products

    def update_product(
        self,
        product_id: int,
        *,
        enabled: bool | None = None,
        name: str | None = None,
        price_selector: str | None = None,
    ) -> dict[str, Any] | None:
        fields: list[str] = []
        values: list[Any] = []
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)
        if name is not None and name.strip():
            fields.append("name = ?")
            values.append(name.strip())
        if price_selector is not None:
            fields.append("price_selector = ?")
            values.append(price_selector.strip() or None)
        if not fields:
            return self.get_product(product_id)
        fields.append("updated_at = ?")
        values.append(now_iso())
        values.append(product_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return self._dict(row)

    def update_product_metadata(
        self,
        product_id: int,
        *,
        name: str | None = None,
        image_url: str | None = None,
        resolved_url: str | None = None,
        source: str | None = None,
    ) -> None:
        fields = ["updated_at = ?"]
        values: list[Any] = [now_iso()]
        if name:
            fields.append("name = ?")
            values.append(name[:300])
        if image_url:
            fields.append("image_url = ?")
            values.append(image_url[:2000])
        if resolved_url:
            fields.append("last_resolved_url = ?")
            values.append(resolved_url[:4000])
        if source:
            fields.append("last_price_source = ?")
            values.append(source[:120])
        values.append(product_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = ?", values)

    def delete_product(self, product_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return cursor.rowcount > 0

    def record_price(
        self,
        product_id: int,
        price: float,
        checked_at: str | None = None,
        *,
        currency: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        checked_at = checked_at or now_iso()
        with self.connect() as conn:
            product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if product is None:
                raise KeyError(f"商品 {product_id} 不存在")
            stored_currency = str(product["currency"] or "XXX").upper()
            observed_currency = str(currency or stored_currency).upper()
            if observed_currency == "XXX":
                raise ValueError("无法确认商品价格的币种")
            if stored_currency == "XXX":
                stored_currency = observed_currency
                conn.execute("UPDATE products SET currency = ? WHERE id = ?", (stored_currency, product_id))
            elif observed_currency != stored_currency:
                raise ValueError(f"币种从 {stored_currency} 变为 {observed_currency}，已拒绝写入")
            old_low = product["lowest_price"]
            is_new_low = old_low is not None and price < float(old_low) - 0.004
            conn.execute(
                """
                INSERT INTO price_records(product_id, price, currency, source, checked_at, is_new_low)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (product_id, round(price, 2), stored_currency, source, checked_at, 1 if is_new_low else 0),
            )
            lowest_price = price if old_low is None or price < float(old_low) else float(old_low)
            lowest_at = checked_at if old_low is None or price < float(old_low) else product["lowest_at"]
            conn.execute(
                """
                UPDATE products
                SET last_price = ?, last_checked_at = ?, lowest_price = ?, lowest_at = ?,
                    check_error = NULL, last_price_source = COALESCE(?, last_price_source), updated_at = ?
                WHERE id = ?
                """,
                (round(price, 2), checked_at, round(lowest_price, 2), lowest_at, source, checked_at, product_id),
            )
            alert = None
            if is_new_low:
                cursor = conn.execute(
                    """
                    INSERT INTO alerts(product_id, old_low, new_price, currency, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (product_id, float(old_low), round(price, 2), stored_currency, checked_at),
                )
                alert = {
                    "id": cursor.lastrowid,
                    "product_id": product_id,
                    "old_low": float(old_low),
                    "new_price": round(price, 2),
                    "created_at": checked_at,
                    "currency": stored_currency,
                    "name": product["name"],
                    "url": product["url"],
                }
        return {"is_new_low": is_new_low, "alert": alert}

    def mark_check_error(self, product_id: int, error: str, checked_at: str | None = None) -> None:
        stamp = checked_at or now_iso()
        with self.connect() as conn:
            conn.execute(
                "UPDATE products SET last_checked_at = ?, check_error = ?, updated_at = ? WHERE id = ?",
                (stamp, error[:500], stamp, product_id),
            )

    def get_history(self, product_id: int, limit: int = 365) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, price, currency, source, checked_at, is_new_low
                FROM price_records WHERE product_id = ?
                ORDER BY checked_at DESC LIMIT ?
                """,
                (product_id, max(1, min(limit, 2000))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def list_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, p.name, p.url, p.sku, p.external_id, p.site, p.site_label, p.image_url,
                       COALESCE(a.currency, p.currency) AS currency
                FROM alerts a JOIN products p ON p.id = a.product_id
                ORDER BY a.created_at DESC LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_alerts_read(self) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE alerts SET is_read = 1 WHERE is_read = 0")

    def set_alert_delivery(self, alert_id: int, delivered: bool, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE alerts SET delivered = ?, delivery_error = ? WHERE id = ?",
                (1 if delivered else 0, error[:500] if error else None, alert_id),
            )

    def get_settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        with self.connect() as conn:
            for key, value in updates.items():
                if key in allowed:
                    conn.execute(
                        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, json.dumps(value, ensure_ascii=False)),
                    )
        return self.get_settings()

    def get_meta(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO app_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def dashboard_stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            counts = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
                       SUM(CASE WHEN check_error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
                       COUNT(DISTINCT site) AS sites,
                       MAX(last_checked_at) AS last_checked_at
                FROM products
                """
            ).fetchone()
            unread = conn.execute("SELECT COUNT(*) AS value FROM alerts WHERE is_read = 0").fetchone()["value"]
            lows_30d = conn.execute(
                "SELECT COUNT(*) AS value FROM alerts WHERE created_at >= datetime('now', '-30 days')"
            ).fetchone()["value"]
        stats = dict(counts)
        stats["unread_alerts"] = unread
        stats["new_lows_30d"] = lows_30d
        return stats
