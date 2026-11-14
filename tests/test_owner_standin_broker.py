"""Owner Stand-In AC2a broker protocol boundary (#111, parent #98)."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_extensions.owner_standin.broker import (
    AssertionRejected,
    BrokerProtocol,
    GrantRejected,
    VerifiedAssertion,
)

NOW = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value

    def advance(self, seconds: int):
        self.value += timedelta(seconds=seconds)


class Entropy:
    def __init__(self):
        self.counter = 0

    def __call__(self, size: int) -> bytes:
        self.counter += 1
        seed = self.counter.to_bytes(4, "big")
        return (seed * ((size // len(seed)) + 1))[:size]


class RecordingVerifier:
    def __init__(self, *, accepted: bool = True):
        self.accepted = accepted
        self.calls = []

    def verify(self, assertion, *, challenge, rp_id, origin, identity):
        self.calls.append(
            {
                "assertion": assertion,
                "challenge": challenge,
                "rp_id": rp_id,
                "origin": origin,
                "identity": identity,
            }
        )
        if not self.accepted:
            raise ValueError("signature invalid: do-not-reflect-assertion")
        return VerifiedAssertion(
            rp_id=rp_id,
            origin=origin,
            identity=identity,
            user_verified=True,
            credential_digest="sha256:" + "a" * 64,
        )


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def verifier():
    return RecordingVerifier()


@pytest.fixture
def broker(clock, verifier):
    return BrokerProtocol(clock=clock, entropy=Entropy(), verifier=verifier)


def issue(broker):
    return broker.issue_challenge(
        rp_id="broker.kylegsmith.com",
        origin="https://broker.kylegsmith.com",
        identity="owner-standin",
        client_session_id="client-session-value",
        ttl_seconds=120,
    )


def authenticate(broker, ceremony, **overrides):
    values = {
        "challenge_id": ceremony.challenge_id,
        "challenge": ceremony.challenge,
        "assertion": {"opaque": "assertion-payload"},
        "rp_id": "broker.kylegsmith.com",
        "origin": "https://broker.kylegsmith.com",
        "identity": "owner-standin",
        "client_session_id": "client-session-value",
        "scopes": ("github:admin", "infisical:owner"),
        "grant_ttl_seconds": 300,
    }
    values.update(overrides)
    return broker.authenticate(**values)


def test_challenge_state_persists_digests_not_raw_values(broker):
    ceremony = issue(broker)
    snapshot = broker.snapshot()
    rendered = repr(snapshot)

    assert ceremony.challenge not in rendered
    assert "client-session-value" not in rendered
    assert snapshot["challenges"][0]["challenge_digest"].startswith("sha256:")
    assert snapshot["challenges"][0]["client_session_digest"].startswith(
        "sha256:"
    )


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"challenge": "wrong"}, "challenge_mismatch"),
        ({"rp_id": "evil.example"}, "rp_id_mismatch"),
        ({"origin": "https://evil.example"}, "origin_mismatch"),
        ({"identity": "ai-coding-harness"}, "identity_mismatch"),
        ({"client_session_id": "other-session"}, "client_session_mismatch"),
    ],
)
def test_all_challenge_bindings_fail_closed_and_consume_attempt(
    broker, verifier, overrides, code
):
    ceremony = issue(broker)

    with pytest.raises(AssertionRejected) as caught:
        authenticate(broker, ceremony, **overrides)

    assert caught.value.code == code
    assert str(caught.value) == code
    assert verifier.calls == []
    with pytest.raises(AssertionRejected, match="challenge_not_usable"):
        authenticate(broker, ceremony)


def test_verified_assertion_is_single_use_and_bound_before_grant(broker, verifier):
    ceremony = issue(broker)
    access = authenticate(broker, ceremony)

    assert verifier.calls[0]["challenge"] == ceremony.challenge
    assert access.grant_token not in repr(broker.snapshot())
    assert broker.authorize(
        grant_id=access.grant_id,
        grant_token=access.grant_token,
        session_id=access.session_id,
        client_session_id="client-session-value",
        required_scope="github:admin",
    ).outcome == "allowed"
    with pytest.raises(AssertionRejected, match="challenge_not_usable"):
        authenticate(broker, ceremony)


def test_verifier_failure_is_sanitized_and_challenge_cannot_be_retried(clock):
    verifier = RecordingVerifier(accepted=False)
    broker = BrokerProtocol(clock=clock, entropy=Entropy(), verifier=verifier)
    ceremony = issue(broker)

    with pytest.raises(AssertionRejected) as caught:
        authenticate(
            broker,
            ceremony,
            assertion={"secret": "github_pat_examplecredential123456789"},
        )

    assert caught.value.code == "assertion_rejected"
    assert "do-not-reflect" not in str(caught.value)
    assert caught.value.__context__ is None
    assert "github_pat_" not in repr(broker.snapshot())
    with pytest.raises(AssertionRejected, match="challenge_not_usable"):
        authenticate(broker, ceremony)


def test_assertion_result_must_match_all_expected_bindings(clock):
    class ConfusedVerifier(RecordingVerifier):
        def verify(self, assertion, *, challenge, rp_id, origin, identity):
            result = super().verify(
                assertion,
                challenge=challenge,
                rp_id=rp_id,
                origin=origin,
                identity=identity,
            )
            return replace(result, origin="https://evil.example")

    broker = BrokerProtocol(
        clock=clock, entropy=Entropy(), verifier=ConfusedVerifier()
    )
    ceremony = issue(broker)

    with pytest.raises(AssertionRejected, match="verified_binding_mismatch"):
        authenticate(broker, ceremony)


def test_malformed_verifier_result_fails_with_safe_code(clock):
    class MalformedVerifier(RecordingVerifier):
        def verify(self, assertion, *, challenge, rp_id, origin, identity):
            result = super().verify(
                assertion,
                challenge=challenge,
                rp_id=rp_id,
                origin=origin,
                identity=identity,
            )
            return replace(result, credential_digest=None)

    broker = BrokerProtocol(
        clock=clock, entropy=Entropy(), verifier=MalformedVerifier()
    )
    with pytest.raises(AssertionRejected, match="verified_binding_mismatch"):
        authenticate(broker, issue(broker))


def test_challenge_and_grant_expiry_boundaries(clock, verifier):
    broker = BrokerProtocol(clock=clock, entropy=Entropy(), verifier=verifier)
    ceremony = issue(broker)
    clock.advance(120)
    with pytest.raises(AssertionRejected, match="challenge_expired"):
        authenticate(broker, ceremony)

    ceremony = issue(broker)
    access = authenticate(broker, ceremony, grant_ttl_seconds=60)
    clock.advance(60)
    with pytest.raises(GrantRejected, match="grant_expired"):
        broker.authorize(
            grant_id=access.grant_id,
            grant_token=access.grant_token,
            session_id=access.session_id,
            client_session_id="client-session-value",
            required_scope="github:admin",
        )


def test_challenge_expiring_during_verification_cannot_mint_access(clock):
    class SlowVerifier(RecordingVerifier):
        def verify(self, assertion, *, challenge, rp_id, origin, identity):
            result = super().verify(
                assertion,
                challenge=challenge,
                rp_id=rp_id,
                origin=origin,
                identity=identity,
            )
            clock.advance(120)
            return result

    broker = BrokerProtocol(clock=clock, entropy=Entropy(), verifier=SlowVerifier())
    ceremony = issue(broker)

    with pytest.raises(AssertionRejected, match="challenge_expired"):
        authenticate(broker, ceremony)
    assert broker.snapshot()["grants"] == []


def test_grant_scope_session_client_binding_and_revocation(broker):
    access = authenticate(broker, issue(broker))

    attempts = [
        ({"required_scope": "google:admin"}, "scope_denied"),
        ({"session_id": "wrong"}, "session_mismatch"),
        ({"client_session_id": "wrong"}, "client_session_mismatch"),
        ({"grant_token": "wrong"}, "grant_token_mismatch"),
    ]
    base = {
        "grant_id": access.grant_id,
        "grant_token": access.grant_token,
        "session_id": access.session_id,
        "client_session_id": "client-session-value",
        "required_scope": "github:admin",
    }
    for overrides, code in attempts:
        with pytest.raises(GrantRejected) as caught:
            broker.authorize(**{**base, **overrides})
        assert caught.value.code == code

    broker.revoke_session(access.session_id, reason="owner-takeover")
    with pytest.raises(GrantRejected, match="session_revoked"):
        broker.authorize(**base)

    with pytest.raises(ValueError, match="invalid revocation reason"):
        broker.revoke_session(access.session_id, reason="sk-examplecredential1234567890")


@pytest.mark.parametrize("ttl", [0, -1, 901, True, 1.5])
def test_ttl_policy_is_strict_and_bounded(broker, ttl):
    with pytest.raises(ValueError, match="ttl_seconds"):
        broker.issue_challenge(
            rp_id="broker.kylegsmith.com",
            origin="https://broker.kylegsmith.com",
            identity="owner-standin",
            client_session_id="client-session-value",
            ttl_seconds=ttl,
        )


def test_inputs_and_scopes_are_strictly_normalized(broker):
    ceremony = issue(broker)
    with pytest.raises(AssertionRejected, match="invalid_scopes"):
        authenticate(broker, ceremony, scopes=("github:admin", "github:admin"))

    with pytest.raises(ValueError, match="https origin"):
        broker.issue_challenge(
            rp_id="broker.kylegsmith.com",
            origin="http://broker.kylegsmith.com",
            identity="owner-standin",
            client_session_id="client-session-value",
            ttl_seconds=60,
        )

    with pytest.raises(ValueError, match="origin outside rp_id"):
        broker.issue_challenge(
            rp_id="kylegsmith.com",
            origin="https://evil.example",
            identity="owner-standin",
            client_session_id="client-session-value",
            ttl_seconds=60,
        )

    with pytest.raises(ValueError, match="invalid identity"):
        broker.issue_challenge(
            rp_id="broker.kylegsmith.com",
            origin="https://broker.kylegsmith.com",
            identity="sk-examplecredential1234567890",
            client_session_id="client-session-value",
            ttl_seconds=60,
        )

    with pytest.raises(ValueError, match="invalid rp_id"):
        broker.issue_challenge(
            rp_id="sk-examplecredential1234567890.test",
            origin="https://sk-examplecredential1234567890.test",
            identity="owner-standin",
            client_session_id="client-session-value",
            ttl_seconds=60,
        )


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"challenge": None}, "challenge_mismatch"),
        ({"client_session_id": None}, "client_session_mismatch"),
        ({"grant_ttl_seconds": 901}, "invalid_ttl"),
    ],
)
def test_malformed_authentication_inputs_fail_with_safe_codes(
    broker, overrides, code
):
    ceremony = issue(broker)
    with pytest.raises(AssertionRejected) as caught:
        authenticate(broker, ceremony, **overrides)
    assert caught.value.code == code
    assert str(caught.value) == code


@pytest.mark.parametrize("field", ["grant_token", "client_session_id"])
def test_malformed_authorization_inputs_fail_with_safe_codes(broker, field):
    access = authenticate(broker, issue(broker))
    values = {
        "grant_id": access.grant_id,
        "grant_token": access.grant_token,
        "session_id": access.session_id,
        "client_session_id": "client-session-value",
        "required_scope": "github:admin",
    }
    values[field] = None
    with pytest.raises(GrantRejected) as caught:
        broker.authorize(**values)
    assert caught.value.code.endswith("_mismatch")


def test_receipts_are_secret_free_and_versioned(broker):
    assertion_secret = "github_pat_examplecredential123456789"
    ceremony = issue(broker)
    access = authenticate(
        broker, ceremony, assertion={"opaque": assertion_secret}
    )
    receipt = broker.authorize(
        grant_id=access.grant_id,
        grant_token=access.grant_token,
        session_id=access.session_id,
        client_session_id="client-session-value",
        required_scope="github:admin",
    )
    rendered = repr(broker.receipts)

    assert receipt.protocol_version == "1.0.0"
    assert assertion_secret not in rendered
    assert ceremony.challenge not in rendered
    assert access.grant_token not in rendered
    assert "client-session-value" not in rendered


def test_collision_exhaustion_fails_closed_without_overwrite(clock, verifier):
    constant = lambda size: b"x" * size
    broker = BrokerProtocol(clock=clock, entropy=constant, verifier=verifier)
    issue(broker)

    with pytest.raises(RuntimeError, match="identifier_collision"):
        issue(broker)
    assert len(broker.snapshot()["challenges"]) == 1


def test_reconcile_revokes_orphans_and_expires_stale_records(broker, clock):
    access = authenticate(broker, issue(broker), grant_ttl_seconds=300)
    broker._sessions.pop(access.session_id)  # simulate corrupted backing state

    report = broker.reconcile()

    assert report.operation == "owner_standin.broker_protocol_reconcile"
    assert access.grant_id in report.revoked_grant_ids
    with pytest.raises(GrantRejected, match="grant_revoked"):
        broker.authorize(
            grant_id=access.grant_id,
            grant_token=access.grant_token,
            session_id=access.session_id,
            client_session_id="client-session-value",
            required_scope="github:admin",
        )


def test_detector_names_inconsistent_state_without_secret_values(broker):
    access = authenticate(broker, issue(broker))
    broker._sessions.pop(access.session_id)

    report = broker.conformance()

    assert report.check == "owner_standin.broker_protocol_conformance"
    assert report.healthy is False
    assert report.problem_ids == (access.grant_id,)


def test_detector_and_resolver_revoke_grant_that_outlives_session(broker):
    access = authenticate(broker, issue(broker))
    broker._grants[access.grant_id].expires_at += timedelta(seconds=1)

    assert broker.conformance().problem_ids == (access.grant_id,)
    assert broker.reconcile().revoked_grant_ids == (access.grant_id,)
    assert broker.conformance().healthy is True


def test_detector_finds_unrevoked_grant_on_revoked_session(broker):
    access = authenticate(broker, issue(broker))
    broker.revoke_session(access.session_id, reason="owner-takeover")
    broker._grants[access.grant_id].revoked_at = None  # simulate partial write

    assert broker.conformance().problem_ids == (access.grant_id,)
    assert broker.reconcile().revoked_grant_ids == (access.grant_id,)
    assert broker.conformance().healthy is True


def test_slow_verifier_does_not_block_unrelated_session_revocation(clock):
    entered = threading.Event()
    release = threading.Event()

    class BlockingVerifier(RecordingVerifier):
        block = False

        def verify(self, assertion, *, challenge, rp_id, origin, identity):
            if self.block:
                entered.set()
                assert release.wait(2)
            return super().verify(
                assertion,
                challenge=challenge,
                rp_id=rp_id,
                origin=origin,
                identity=identity,
            )

    verifier = BlockingVerifier()
    broker = BrokerProtocol(clock=clock, entropy=Entropy(), verifier=verifier)
    existing = authenticate(broker, issue(broker))
    verifier.block = True
    ceremony = issue(broker)
    auth_thread = threading.Thread(target=lambda: authenticate(broker, ceremony))
    auth_thread.start()
    assert entered.wait(1)

    revoked = threading.Event()
    revoke_thread = threading.Thread(
        target=lambda: (
            broker.revoke_session(existing.session_id, reason="owner-takeover"),
            revoked.set(),
        )
    )
    revoke_thread.start()
    try:
        assert revoked.wait(0.5), "verifier held the global broker lock"
    finally:
        release.set()
        auth_thread.join(2)
        revoke_thread.join(2)


def test_documentation_preserves_non_authorizing_provider_boundary():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    design = (ROOT / "docs" / "OWNER_STANDIN_BROKER_CORE.md").read_text(
        encoding="utf-8"
    ).lower()

    assert "not a deployed webauthn verifier or live authority path" in " ".join(
        readme.split()
    )
    for phrase in (
        "does not enroll a passkey",
        "cryptographic webauthn verification runs outside",
        "adapter inputs are transient",
        "owner_standin.broker_protocol_conformance",
        "owner_standin.broker_protocol_reconcile",
        "consumed challenges are never reset",
    ):
        assert phrase in design
