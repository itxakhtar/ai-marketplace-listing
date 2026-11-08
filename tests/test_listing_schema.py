import pytest
from pydantic import ValidationError

from app.models.enums import Category, Condition
from app.models.listing import ListingResponse


def valid_payload(**overrides):
    data = {
        "detected_item": "Smartphone",
        "category": "Mobiles and Tablets",
        "condition_estimate": "Used - Good Condition",
        "suggested_title": "Black Smartphone With Dual Rear Camera",
        "suggested_description": "Black smartphone with a dual rear camera.",
        "extracted_features": ["Black", "Dual Camera"],
        "tags": ["smartphone", "mobile", "phone", "black", "camera"],
    }
    data.update(overrides)
    return data


def test_valid_payload_parses():
    listing = ListingResponse.model_validate(valid_payload())
    assert listing.category is Category.MOBILES_AND_TABLETS
    assert listing.condition_estimate is Condition.USED_GOOD


def test_title_over_70_chars_rejected():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(suggested_title="x" * 71))


def test_title_exactly_70_chars_accepted():
    assert ListingResponse.model_validate(valid_payload(suggested_title="x" * 70))


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(category="Pets"))


def test_invalid_condition_rejected():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(condition_estimate="Mint"))


def test_empty_detected_item_rejected():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(detected_item="   "))


def test_empty_description_rejected():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(suggested_description=""))


def test_too_few_tags_rejected():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(tags=["a", "b", "c", "d"]))


def test_duplicate_tags_collapse_then_fail_minimum():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(tags=["Phone", "phone", " PHONE ", "#phone", "x"]))


def test_tags_normalized_and_truncated_to_eight():
    tags = [f" #Tag{i} " for i in range(12)]
    listing = ListingResponse.model_validate(valid_payload(tags=tags))
    assert len(listing.tags) == 8
    assert all(t == t.lower() and t == t.strip() and not t.startswith("#") for t in listing.tags)


def test_features_deduplicated_case_insensitive():
    listing = ListingResponse.model_validate(
        valid_payload(extracted_features=["Black", "black", "  Dual   Camera "])
    )
    assert listing.extracted_features == ["Black", "Dual Camera"]


def test_too_many_features_rejected():
    with pytest.raises(ValidationError):
        ListingResponse.model_validate(valid_payload(extracted_features=[f"f{i}" for i in range(11)]))


def test_unknown_keys_ignored():
    listing = ListingResponse.model_validate(valid_payload(price="$500", warranty="1 year"))
    assert not hasattr(listing, "price")
