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


def test_there_is_no_unreachable_spam_trap_status():
    """The status was removed because nothing could ever emit it.

    If it comes back, it comes back with a producer — otherwise every
    spam-trap figure the product reports is a structural zero that reads as
    'we looked and found none'.
    """
    from eve.verdict import Status

    assert not hasattr(Status, "SPAM_TRAP")
    assert "spam_trap" not in {s.value for s in Status}
