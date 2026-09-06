from datetime import datetime, timedelta, timezone

import pytest

from app.security.tokens import (
    TokenError,
    TokenSettings,
    decode_access_token,
    extract_bearer_subprotocol,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
    verify_password,
)

SETTINGS = TokenSettings(secret="test-secret-key-that-is-long-enough!!")


def test_password_round_trip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong", stored)


def test_password_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_corrupt_hash_is_rejected_not_crashed():
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "md5$1$2$3$4$5")


def test_short_secret_is_refused():
    with pytest.raises(ValueError):
        TokenSettings(secret="too-short")


def test_access_token_round_trip():
    token = issue_access_token("user-1", SETTINGS)
    claims = decode_access_token(token, SETTINGS)
    assert claims.user_id == "user-1"
    assert claims.expires_at > datetime.now(timezone.utc)


def test_tampered_token_is_rejected():
    token = issue_access_token("user-1", SETTINGS)
    with pytest.raises(TokenError):
        decode_access_token(token[:-2] + "xx", SETTINGS)


def test_token_from_another_deployment_is_rejected():
    other = TokenSettings(secret="a-completely-different-secret-key!!!!")
    with pytest.raises(TokenError):
        decode_access_token(issue_access_token("user-1", other), SETTINGS)


def test_expired_token_is_rejected():
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    with pytest.raises(TokenError, match="expired"):
        decode_access_token(issue_access_token("user-1", SETTINGS, now=past), SETTINGS)


def test_refresh_token_is_opaque_and_hashed_consistently():
    token, digest = new_refresh_token()
    assert len(token) >= 43 and "." not in token       # not a JWT
    assert hash_refresh_token(token) == digest
    assert hash_refresh_token("other") != digest


@pytest.mark.parametrize(
    "header,expected",
    [
        ("bearer, abc.def.ghi", "abc.def.ghi"),
        ("bearer,abc", "abc"),
        ("bearer", None),
        ("basic, abc", None),
        ("bearer, ", None),
        (None, None),
        ("bearer, a, b", None),
    ],
)
def test_subprotocol_parsing(header, expected):
    assert extract_bearer_subprotocol(header) == expected
