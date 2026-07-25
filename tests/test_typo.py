import pytest

from eve.layers.typo import suggest_domain


@pytest.mark.parametrize(
    "bad,expected",
    [
        ("gmial.com", "gmail.com"),
        ("gmai.com", "gmail.com"),
        ("gmail.con", "gmail.com"),
        ("yahooo.com", "yahoo.com"),
        ("hotmial.com", "hotmail.com"),
        ("outlok.com", "outlook.com"),
    ],
)
def test_suggests_correction(bad, expected):
    assert suggest_domain(bad) == expected


@pytest.mark.parametrize("good", ["gmail.com", "yahoo.com", "outlook.com", "some-company.io"])
def test_no_suggestion_for_valid_domains(good):
    assert suggest_domain(good) is None


def test_no_false_suggestion_for_unrelated_domain():
    # A legitimate but uncommon domain must not snap to a top domain.
    assert suggest_domain("company.com") is None
