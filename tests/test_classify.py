from eve.layers.classify import classify


def test_disposable_domain():
    assert classify("bob", "mailinator.com").is_disposable


def test_role_local_part():
    assert classify("sales", "acme.io").is_role
    assert classify("info", "acme.io").is_role


def test_non_role_person():
    assert not classify("john", "acme.io").is_role


def test_free_provider():
    assert classify("john", "gmail.com").is_free
    assert not classify("john", "acme.io").is_free


def test_role_is_case_insensitive():
    assert classify("SALES", "acme.io").is_role
