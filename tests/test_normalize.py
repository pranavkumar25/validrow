from eve.layers.normalize import normalize


def test_gmail_dot_and_plus_collapse():
    r = normalize("J.Doe+promo", "gmail.com")
    assert r.dedupe_key == "jdoe@gmail.com"
    assert r.normalized_email == "j.doe+promo@gmail.com"


def test_googlemail_aliases_to_gmail():
    r = normalize("jdoe", "googlemail.com")
    assert r.dedupe_key == "jdoe@gmail.com"


def test_outlook_strips_plus_but_keeps_dots():
    r = normalize("first.last+tag", "outlook.com")
    assert r.dedupe_key == "first.last@outlook.com"


def test_corporate_domain_keeps_everything():
    r = normalize("first.last", "company.com")
    assert r.dedupe_key == "first.last@company.com"


def test_two_gmail_variants_dedupe_together():
    a = normalize("j.doe+a", "gmail.com").dedupe_key
    b = normalize("jdoe+b", "googlemail.com").dedupe_key
    assert a == b
