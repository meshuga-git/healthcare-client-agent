import copy
import pytest
from pydantic import ValidationError
from harbor.config import load_packs, load_profile, Pack, answer_faq


def test_six_validated_verticals():
    packs = load_packs()
    assert len(packs) == 6
    assert all(p["intake_templates"] and p["faq"] and p["providers"] and p["resources"] for p in packs.values())
    assert packs["aesthetics"]["reminders"]["discreet"]
    assert load_profile()["country"] == "USA"


def test_reference_and_duration_validation():
    pack = copy.deepcopy(load_packs()["dental"])
    pack["services"][0]["skill"] = "invented"
    with pytest.raises(ValidationError):
        Pack.model_validate(pack)
    pack = copy.deepcopy(load_packs()["dental"])
    pack["services"][0]["duration_minutes"] = -10
    with pytest.raises(ValidationError):
        Pack.model_validate(pack)


def test_faq_cites_authored_pack_not_generated_answer():
    result = answer_faq(load_packs(), "dental", "What are your opening hours?")
    assert result["citation"] == "config://dental/location/hours"
    assert answer_faq(load_packs(), "dental", "quantum zebras") is None
