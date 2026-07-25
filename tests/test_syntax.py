from eve.layers.syntax import check_syntax


def test_accepts_normal_address():
    r = check_syntax("John.Doe@Gmail.com")
    assert r.valid
    assert r.domain == "gmail.com"
    assert r.local_part == "John.Doe"


def test_rejects_no_at_sign():
    assert not check_syntax("plainaddress").valid


def test_rejects_missing_local_part():
    assert not check_syntax("@example.com").valid


def test_rejects_space_in_local():
    assert not check_syntax("john doe@example.com").valid


def test_rejects_domain_without_dot():
    assert not check_syntax("john@localhost").valid


def test_rejects_double_at():
    assert not check_syntax("a@@b.com").valid


def test_rejects_empty():
    assert not check_syntax("").valid
