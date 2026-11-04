from tracker.models import ALLOWED_COUNTRIES, role_id


def test_role_id_stable():
    assert role_id("ubs", "https://x/1") == role_id("ubs", "https://x/1")


def test_role_id_is_16_hex_and_differs_per_input():
    a = role_id("ubs", "https://x/1")
    assert len(a) == 16 and int(a, 16) >= 0
    assert a != role_id("ubs", "https://x/2")
    assert a != role_id("gs", "https://x/1")


def test_allowed_has_31_and_no_uk():
    assert len(ALLOWED_COUNTRIES) == 31 and "CH" in ALLOWED_COUNTRIES and "GB" not in ALLOWED_COUNTRIES
