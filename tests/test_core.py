from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from app import normalize_bind_host
from pricewatch.catalog import parse_product_input, validate_price_selector, validate_public_url
from pricewatch.db import Database
from pricewatch.jd import LoginRequiredError, _extract_mobile_price, extract_sku
from pricewatch.service import TrackerService


class ExtractSkuTests(unittest.TestCase):
    def test_item_url(self):
        self.assertEqual(extract_sku("https://item.jd.com/100012043978.html"), "100012043978")

    def test_mobile_query(self):
        self.assertEqual(extract_sku("https://item.m.jd.com/product/123456.html?sku=99887766"), "123456")

    def test_bare_sku(self):
        self.assertEqual(extract_sku("100012043978"), "100012043978")

    def test_reject_other_domain(self):
        with self.assertRaises(ValueError):
            extract_sku("https://example.com/100012043978.html")

    def test_extract_mobile_price_floor(self):
        page = '<script>window.data={"priceFloor":{"demoteEnable":false,"price":"4299.00"}}</script>'
        self.assertEqual(_extract_mobile_price(page), 4299.0)

    def test_masked_mobile_price_requires_login(self):
        page = '<script>window.data={"priceFloor":{"price":"4??9"},"priceLoginText":"登录查看价格"}</script>'
        with self.assertRaises(LoginRequiredError):
            _extract_mobile_price(page)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        self.product = self.db.add_product("100012043978", "https://item.jd.com/100012043978.html", "测试商品", None)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_strict_new_low_alerts(self):
        first = self.db.record_price(self.product["id"], 100.00, "2026-07-10T09:00:00+08:00")
        same = self.db.record_price(self.product["id"], 100.00, "2026-07-11T09:00:00+08:00")
        higher = self.db.record_price(self.product["id"], 105.00, "2026-07-12T09:00:00+08:00")
        lower = self.db.record_price(self.product["id"], 99.99, "2026-07-13T09:00:00+08:00")
        self.assertFalse(first["is_new_low"])
        self.assertFalse(same["is_new_low"])
        self.assertFalse(higher["is_new_low"])
        self.assertTrue(lower["is_new_low"])
        self.assertEqual(len(self.db.list_alerts()), 1)
        self.assertEqual(self.db.get_product(self.product["id"])["lowest_price"], 99.99)

    def test_delete_cascades_history_and_alerts(self):
        self.db.record_price(self.product["id"], 100)
        self.db.record_price(self.product["id"], 90)
        self.assertTrue(self.db.delete_product(self.product["id"]))
        self.assertEqual(self.db.get_history(self.product["id"]), [])
        self.assertEqual(self.db.list_alerts(), [])


class SessionRequiredTests(unittest.TestCase):
    def test_price_write_path_never_falls_back_to_public_scrape(self):
        class UnconfiguredSession:
            def fetch_product(self, product):
                raise LoginRequiredError("需要登录")

        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(Database(Path(directory) / "test.db"), UnconfiguredSession())
            with self.assertRaises(LoginRequiredError):
                service._fetch_product({"adapter": "jd", "url": "https://item.jd.com/100012043978.html"})


class CatalogTests(unittest.TestCase):
    def test_server_bind_is_loopback_only(self):
        self.assertEqual(normalize_bind_host("localhost"), "127.0.0.1")
        self.assertEqual(normalize_bind_host("127.0.0.2"), "127.0.0.2")
        for value in ("0.0.0.0", "192.168.1.20", "example.com", "::1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_bind_host(value)

    def test_generic_product_identity_is_stable_and_site_aware(self):
        product = parse_product_input("https://shop.example/products/blue-shoe?variant=42#reviews")
        self.assertEqual(product.site, "shop.example")
        self.assertEqual(product.adapter, "generic")
        self.assertTrue(product.product_key.startswith("web:"))
        self.assertNotIn("#", product.url)
        self.assertEqual(product.default_currency, "XXX")

    def test_jd_is_kept_on_strict_adapter(self):
        product = parse_product_input("https://item.jd.com/100012043978.html")
        self.assertEqual(product.product_key, "jd:100012043978")
        self.assertEqual(product.adapter, "jd")
        self.assertEqual(product.default_currency, "CNY")

    def test_unsafe_urls_are_rejected(self):
        unsafe = [
            "file:///C:/Windows/win.ini",
            "javascript:alert(1)",
            "http://localhost/product",
            "http://127.0.0.1/product",
            "http://192.168.1.10/product",
            "http://100.64.0.1/product",
            "https://user:pass@example.com/product",
            "https://example.com:8443/product",
        ]
        for value in unsafe:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_public_url(value)

    def test_selector_validation(self):
        self.assertEqual(validate_price_selector("  .buy-box .price  "), ".buy-box .price")
        with self.assertRaises(ValueError):
            validate_price_selector(".price\nscript")


class MultiSiteDatabaseTests(unittest.TestCase):
    def test_currency_mismatch_is_never_written(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            product = db.add_product(
                "web_a",
                "https://shop.example/p/a",
                "Example",
                None,
                product_key="web:a",
                site="shop.example",
                site_label="shop.example",
                external_id="a",
                adapter="generic",
                currency="USD",
            )
            db.record_price(product["id"], 19.99, currency="USD", source="jsonld-offer")
            with self.assertRaises(ValueError):
                db.record_price(product["id"], 19.99, currency="CNY", source="custom-css")
            history = db.get_history(product["id"])
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["currency"], "USD")
            self.assertEqual(history[0]["source"], "jsonld-offer")

    def test_v1_database_migrates_without_losing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE products(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
                    name TEXT NOT NULL, image_url TEXT, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_checked_at TEXT,
                    last_price REAL, lowest_price REAL, lowest_at TEXT, check_error TEXT
                );
                CREATE TABLE price_records(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    price REAL NOT NULL, checked_at TEXT NOT NULL, is_new_low INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE alerts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    old_low REAL NOT NULL, new_price REAL NOT NULL, created_at TEXT NOT NULL,
                    is_read INTEGER NOT NULL DEFAULT 0, delivered INTEGER, delivery_error TEXT
                );
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE app_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO products VALUES(7,'100012043978','https://item.jd.com/100012043978.html','旧商品',NULL,1,'t0','t0','t1',88,88,'t1',NULL);
                INSERT INTO price_records VALUES(9,7,88,'t1',0);
                """
            )
            conn.commit()
            conn.close()

            db = Database(path)
            self.assertTrue(path.with_suffix(path.suffix + ".pre-v2.bak").is_file())
            product = db.get_product(7)
            self.assertIsNotNone(product)
            self.assertEqual(product["product_key"], "jd:100012043978")
            self.assertEqual(product["site"], "jd.com")
            self.assertEqual(product["currency"], "CNY")
            self.assertEqual(db.get_history(7)[0]["price"], 88)
            check = sqlite3.connect(path)
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(check.execute("PRAGMA foreign_key_check").fetchall(), [])
            check.close()


if __name__ == "__main__":
    unittest.main()
