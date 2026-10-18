from __future__ import annotations

from pathlib import Path
import unittest

from sensors.store_product_sensor import extract_store_product


FIXTURES = Path(__file__).parent / "fixtures" / "store_products"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class StoreProductSensorTests(unittest.TestCase):
    def test_shopify_like_product_metadata(self):
        records = extract_store_product(fixture("shopify_like.html"), "https://shop.example/products/phone", retrieved_at="2026-10-18T00:00:00Z")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.fields["name"], "Example Phone")
        self.assertEqual(record.fields["canonical_url"], "https://shop.example/products/phone")
        self.assertEqual(record.fields["offers"][0]["currency"], "EUR")
        self.assertEqual(record.fields["offers"][0]["price"], "299.00")
        self.assertEqual(record.fields["image_urls"], ["https://shop.example/images/phone.jpg"])
        self.assertEqual(record.provenance["name"][0].source, "og:title")

    def test_jsonld_variant_offers_remain_separate(self):
        records = extract_store_product(fixture("jsonld_variants.html"), "https://shop.example/family")
        by_sku = {record.fields.get("sku"): record for record in records}
        self.assertEqual(set(by_sku), {"P-128-B", "P-256-BL"})
        self.assertEqual(by_sku["P-128-B"].fields["offers"][0]["currency"], "USD")
        self.assertEqual(by_sku["P-256-BL"].fields["offers"][0]["currency"], "EUR")
        self.assertEqual(by_sku["P-128-B"].fields["offers"][0]["price"], "500")
        self.assertEqual(by_sku["P-128-B"].fields["options"]["color"], "Black")
        self.assertEqual(by_sku["P-128-B"].fields["options"]["size"], "128 GB")

    def test_custom_meta_fields_are_explicit(self):
        record = extract_store_product(fixture("custom_store.html"), "https://store.example/item")[0]
        for key, expected in (("brand", "Acme"), ("model", "A-1"), ("sku", "SKU-1"), ("gtin", "1234567890123")):
            self.assertEqual(record.fields[key], expected)

    def test_conflicts_are_preserved_and_offers_keep_currency_pairs(self):
        record = extract_store_product(fixture("conflicting.html"), "https://store.example/item")[0]
        self.assertIsNone(record.fields["name"])
        self.assertEqual(record.unresolved["name"], ("Structured Product", "Page Product"))
        self.assertEqual([(o["price"], o["currency"]) for o in record.fields["offers"]], [("10", "USD"), ("20", "EUR")])

    def test_category_and_non_product_pages_return_no_records(self):
        self.assertEqual(extract_store_product(fixture("category.html"), "https://store.example/phones"), ())
        self.assertEqual(extract_store_product("<html><title>Hello</title></html>", "https://example.test/"), ())

    def test_malformed_jsonld_does_not_create_product(self):
        self.assertEqual(extract_store_product('<script type="application/ld+json">{bad}</script>', "https://example.test/p"), ())

    def test_unsafe_urls_rejected_and_output_is_deterministic(self):
        with self.assertRaises(ValueError):
            extract_store_product("<html/>", "file:///etc/passwd")
        html = fixture("shopify_like.html")
        first = extract_store_product(html, "https://shop.example/products/phone", retrieved_at="2026-10-18T00:00:00Z")[0].to_dict()
        second = extract_store_product(html, "https://shop.example/products/phone", retrieved_at="2026-10-18T00:00:00Z")[0].to_dict()
        self.assertEqual(first, second)
        self.assertNotIn("trusted_price_evidence", str(first))


if __name__ == "__main__":
    unittest.main()
