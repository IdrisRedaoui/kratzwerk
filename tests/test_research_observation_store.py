from __future__ import annotations

import sqlite3

import pytest

from database_safety import PROTECTED_PRODUCTION_DATABASE_PATH
from sensors.safe_http_transport import FetchMetadata, FetchResult
from sensors.store_observation_provenance import FieldEvidence, ObservationProvenance
from sensors.store_product_collection import ProductCollectionResult
from research_observation_store import (
    CandidateUrl,
    ObservationInput,
    ResearchObservationStore,
    RetentionPolicy,
    StoreSafetyError,
    observation_from_collection_result,
)


def policy(days: int = 30) -> RetentionPolicy:
    return RetentionPolicy(
        policy_id="synthetic-retention-v1",
        retention_days=days,
        approved_by="synthetic-fixture-operator",
        approval_reference="fixture-policy-001",
        approved_at_utc="2026-01-01T00:00:00Z",
    )


def candidate(
    *,
    merchant_id: str = "merchant-fixture-01",
    candidate_url_id: str = "candidate-fixture-01",
    url: str = "https://merchant.example/products/widget?variant=blue&session=private&uid=person-fixture",
) -> CandidateUrl:
    return CandidateUrl(
        candidate_url_id=candidate_url_id,
        merchant_id=merchant_id,
        merchant_display_name="Synthetic Shop",
        operator_identity_confidence="UNKNOWN",
        candidate_url=url,
        url_classification="PRODUCT_PAGE_CANDIDATE",
        eligibility_status="REVIEW_REQUIRED",
        eligibility_reason="synthetic fixture only",
        created_at_utc="2026-01-02T00:00:00Z",
        discovery_provenance={
            "kind": "synthetic_fixture",
            "source_ad_record_id": 42,
            "source_ad_observation_id": 43,
            "source_ad_library_id": "4200798420153598",
        },
        source_ad_record_id=42,
        source_ad_observation_id=43,
        source_ad_library_id="fixture-ad-42",
        source_ad_url="https://www.facebook.com/ads/library/?id=fixture-ad-42",
    )


def observation(**changes) -> ObservationInput:
    values = {
        "candidate_url_id": "candidate-fixture-01",
        "merchant_id": "merchant-fixture-01",
        "idempotency_key": "fixture-attempt-01",
        "observed_at_utc": "2026-01-03T00:00:00Z",
        "requested_url": "https://merchant.example/products/widget?variant=blue&session=private&uid=person-fixture",
        "final_url": "https://merchant.example/products/widget?variant=blue",
        "http_status": 200,
        "fetch_outcome": "FETCHED",
        "extraction_outcome": "PARTIAL",
        "raw_observed_values": {"products": [{"fields": {"name": "Widget", "displayed_price": "99.00"}}]},
        "normalized_interpretations": {},
        "content_sha256": "a" * 64,
        "fetcher_version": "fixture-fetcher-v1",
        "extraction_version": "fixture-extractor-v1",
    }
    values.update(changes)
    return ObservationInput(**values)


def create_store(tmp_path):
    store = ResearchObservationStore.create(tmp_path / "pilot-research.sqlite", retention_policy=policy())
    store.add_candidate(candidate())
    return store


def test_store_requires_an_isolated_path_and_explicit_approved_retention(tmp_path):
    with pytest.raises(StoreSafetyError, match="production Database.sqlite"):
        ResearchObservationStore.create(PROTECTED_PRODUCTION_DATABASE_PATH, retention_policy=policy())

    with pytest.raises(ValueError, match="explicitly approved"):
        RetentionPolicy("p", 0, "operator", "approval", "2026-01-01T00:00:00Z")

    store = create_store(tmp_path)
    try:
        assert store.path != PROTECTED_PRODUCTION_DATABASE_PATH
        assert store.path.name == "pilot-research.sqlite"
    finally:
        store.close()


def test_append_is_duplicate_safe_and_keeps_fetch_and_extraction_outcomes_separate(tmp_path):
    store = create_store(tmp_path)
    try:
        first = store.append_observation(observation())
        second = store.append_observation(observation())
        assert first == second

        row = store.get_by_idempotency_key("fixture-attempt-01")
        assert row["observation_id"] == first
        assert row["fetch_outcome"] == "FETCHED"
        assert row["extraction_outcome"] == "PARTIAL"
        assert row["merchant_id"] == "merchant-fixture-01"
        assert row["candidate_url_id"] == "candidate-fixture-01"
        assert row["source_ad_record_id"] == 42
        assert row["source_ad_observation_id"] == 43
        assert row["requested_url"] == "https://merchant.example/products/widget?variant=blue"
        assert "session" not in row["requested_url"]
        assert "uid" not in row["requested_url"]
        assert row["retention_due_at_utc"] == "2026-02-02T00:00:00.000000Z"
        assert row["raw_observed_values"]["products"][0]["fields"]["displayed_price"] == "99.00"
        assert row["normalized_interpretations"] == {}

        with pytest.raises(ValueError, match="different observation inputs"):
            store.append_observation(observation(raw_observed_values={"products": []}))
    finally:
        store.close()


def test_store_reopens_with_the_same_approved_policy_and_rejects_a_different_one(tmp_path):
    store = create_store(tmp_path)
    store.append_observation(observation())
    path = store.path
    store.close()

    reopened = ResearchObservationStore.open(path, retention_policy=policy())
    try:
        assert reopened.get_by_idempotency_key("fixture-attempt-01") is not None
    finally:
        reopened.close()

    with pytest.raises(StoreSafetyError, match="retention policy"):
        ResearchObservationStore.open(path, retention_policy=policy(days=60))


def test_observations_are_append_only_and_have_no_html_or_trusted_price_columns(tmp_path):
    store = create_store(tmp_path)
    try:
        observation_id = store.append_observation(observation())
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("UPDATE observations SET extraction_outcome='SUCCESS' WHERE observation_id=?", (observation_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("DELETE FROM observations WHERE observation_id=?", (observation_id,))

        columns = {row[1] for row in store._connection.execute("PRAGMA table_info(observations)")}
        assert not any("html" in column or "trusted" in column or "approved_price" in column for column in columns)
    finally:
        store.close()


def test_rejects_html_and_sensitive_private_fields(tmp_path):
    store = create_store(tmp_path)
    try:
        with pytest.raises(ValueError, match="raw HTML"):
            store.append_observation(observation(raw_observed_values={"html": "<html>secret</html>"}))
        with pytest.raises(ValueError, match="private"):
            store.append_observation(observation(raw_observed_values={"products": [{"session_token": "secret"}]}))
        with pytest.raises(ValueError, match="sensitive"):
            store.append_observation(observation(raw_observed_values={"products": [{"contact": "0555 123 4567"}]}))
    finally:
        store.close()


def test_observation_must_match_registered_merchant_and_candidate(tmp_path):
    store = create_store(tmp_path)
    try:
        with pytest.raises(ValueError, match="known candidate URL"):
            store.append_observation(observation(merchant_id="other-merchant"))
        with pytest.raises(ValueError, match="match the registered candidate"):
            store.append_observation(observation(
                requested_url="https://other.example/products/widget",
                final_url="https://other.example/products/widget",
            ))
    finally:
        store.close()


def test_redirect_chain_is_validated_and_preserved(tmp_path):
    store = create_store(tmp_path)
    try:
        item = observation(
            requested_url="https://merchant.example/products/widget?variant=blue&session=private&uid=person-fixture",
            redirect_history=(
                {
                    "from_url": "https://merchant.example/products/widget?variant=blue",
                    "status_code": 302,
                    "to_url": "https://www.merchant.example/products/widget?variant=blue",
                    "to_eligibility_reference": "review-host-02",
                },
            ),
            final_url="https://www.merchant.example/products/widget?variant=blue",
        )
        store.append_observation(item)
        row = store.get_by_idempotency_key(item.idempotency_key)
        assert row["redirect_history"] == [{
            "from_url": "https://merchant.example/products/widget?variant=blue",
            "status": 302,
            "to_url": "https://www.merchant.example/products/widget?variant=blue",
            "destination_review_reference": "review-host-02",
        }]
        with pytest.raises(ValueError, match="contiguous"):
            store.append_observation(observation(
                idempotency_key="bad-redirect",
                redirect_history=({
                    "from_url": "https://other.example/start",
                    "status": 302,
                    "to_url": "https://merchant.example/products/widget?variant=blue",
                },),
            ))
    finally:
        store.close()


def test_existing_store_product_collector_result_maps_to_isolated_observation(tmp_path):
    store = create_store(tmp_path)
    try:
        requested_url = "https://merchant.example/products/widget?variant=blue"
        metadata = FetchMetadata(
            merchant_id="merchant-fixture-01",
            source_eligibility_reference="fixture-source-review",
            requested_url=requested_url,
            redirect_hops=(),
            final_url=requested_url,
            retrieved_at_utc="2026-01-03T00:00:00Z",
            status_code=200,
            content_type="text/html; charset=utf-8",
            response_size=128,
            sha256="b" * 64,
            fetcher_version="store-fetcher-v1",
            transport_configuration_version="safe-transport-v1",
            outcome="FETCHED",
            stop_reason=None,
        )
        provenance = ObservationProvenance(
            requested_url=requested_url,
            retrieved_at_utc="2026-01-03T00:00:00Z",
            transport_outcome="FETCHED",
            eligibility_decision="REVIEW_REQUIRED",
            discovered_product_url=requested_url,
            final_url=requested_url,
            http_status=200,
            content_type="text/html; charset=utf-8",
            response_size=128,
            content_sha256="b" * 64,
            merchant_id="merchant-fixture-01",
            fields=(FieldEvidence(
                field_name="name",
                status="OBSERVED_STATICALLY",
                value="Synthetic Widget",
                source_url=requested_url,
                source_kind="PUBLIC_PRODUCT_PAGE_HTML",
                evidence_locator="h1",
            ),),
        )
        result = ProductCollectionResult(
            fetch_result=FetchResult(metadata, body=None),
            extraction_status="SUCCESS",
            products=({"fields": {"name": "Synthetic Widget"}},),
            provenance=provenance,
        )
        mapped = observation_from_collection_result(
            result,
            candidate_url_id="candidate-fixture-01",
            idempotency_key="collector-attempt-01",
        )
        observation_id = store.append_observation(mapped)
        row = store.get_by_idempotency_key("collector-attempt-01")
        assert row["observation_id"] == observation_id
        assert row["fetch_outcome"] == "FETCHED"
        assert row["extraction_outcome"] == "SUCCESS"
        assert row["raw_observed_values"]["products"][0]["fields"]["name"] == "Synthetic Widget"
        assert row["raw_observed_values"]["field_provenance"][0]["field_name"] == "name"
        assert row["normalized_interpretations"] == {}
        assert "body" not in row
    finally:
        store.close()
