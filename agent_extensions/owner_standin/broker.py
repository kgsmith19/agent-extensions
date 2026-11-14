"""Replay-resistant Owner Stand-In broker protocol core.

This module is deliberately provider-neutral and non-deployed.  A provider
adapter must perform WebAuthn cryptographic verification and return a
``VerifiedAssertion``.  The core persists only identifiers, digests,
timestamps, scopes, and state; raw challenges, assertion payloads, client
session values, and bearer grant tokens are never retained.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from agent_extensions.schemas.owner_standin_v1 import assert_no_sensitive_values

PROTOCOL_VERSION = "1.0.0"
MAX_TTL_SECONDS = 900
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RP_ID = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SCOPE = re.compile(r"^[a-z][a-z0-9-]{0,39}:[a-z][a-z0-9-]{0,39}$")
_REVOCATION_REASONS = {"owner-takeover", "operator-request", "reconcile"}


class AssertionVerifier(Protocol):
    """Provider boundary; implementations perform full WebAuthn verification."""

    def verify(
        self,
        assertion: Any,
        *,
        challenge: str,
        rp_id: str,
        origin: str,
        identity: str,
    ) -> VerifiedAssertion: ...


class _SafeError(Exception):
    """An error whose rendered form is a fixed, non-sensitive code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


class AssertionRejected(_SafeError):
    """A challenge or verified assertion failed closed."""


class GrantRejected(_SafeError):
    """An expiring capability grant failed closed."""


@dataclass(frozen=True)
class VerifiedAssertion:
    rp_id: str
    origin: str
    identity: str
    user_verified: bool
    credential_digest: str


@dataclass(frozen=True)
class ChallengeCeremony:
    challenge_id: str
    challenge: str
    expires_at: datetime


@dataclass(frozen=True)
class BrokerAccess:
    session_id: str
    grant_id: str
    grant_token: str
    expires_at: datetime
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class BrokerReceipt:
    protocol_version: str
    event_id: str
    action: str
    outcome: Literal["allowed", "denied", "recorded"]
    subject_id: str
    at: datetime
    detail_code: str


@dataclass(frozen=True)
class ConformanceReport:
    check: Literal["owner_standin.broker_protocol_conformance"]
    healthy: bool
    problem_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReconcileReport:
    operation: Literal["owner_standin.broker_protocol_reconcile"]
    revoked_grant_ids: tuple[str, ...]
    expired_challenge_ids: tuple[str, ...]
    expired_session_ids: tuple[str, ...]


@dataclass
class _ChallengeRecord:
    challenge_id: str
    challenge_digest: str
    rp_id: str
    origin: str
    identity: str
    client_session_digest: str
    issued_at: datetime
    expires_at: datetime
    state: Literal["issued", "consumed", "expired", "revoked"] = "issued"


@dataclass
class _SessionRecord:
    session_id: str
    identity: str
    client_session_digest: str
    credential_digest: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


@dataclass
class _GrantRecord:
    grant_id: str
    token_digest: str
    session_id: str
    scopes: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _require_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeError("clock_not_utc")
    return value.astimezone(UTC)


def _require_ttl(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_TTL_SECONDS:
        raise ValueError("ttl_seconds")
    return value


def _require_rp_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not _RP_ID.fullmatch(value)
        or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            for label in value.split(".")
        )
    ):
        raise ValueError("invalid rp_id")
    try:
        assert_no_sensitive_values(value)
    except ValueError:
        raise ValueError("invalid rp_id") from None
    return value


def _require_origin(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("https origin required")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("https origin required") from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("https origin required")
    # Reading the property validates a numeric, in-range port.
    _ = port
    try:
        assert_no_sensitive_values(value)
    except ValueError:
        raise ValueError("https origin required") from None
    return value


def _require_origin_for_rp(origin: str, rp_id: str) -> str:
    origin = _require_origin(origin)
    hostname = urlsplit(origin).hostname
    if hostname != rp_id and not hostname.endswith("." + rp_id):
        raise ValueError("origin outside rp_id")
    return origin


def _require_identity(value: str) -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError("invalid identity")
    try:
        assert_no_sensitive_values(value)
    except ValueError:
        raise ValueError("invalid identity") from None
    return value


def _scopes(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("invalid_scopes")
    result = tuple(values)
    if (
        not result
        or len(result) > 32
        or len(set(result)) != len(result)
        or any(not isinstance(value, str) or not _SCOPE.fullmatch(value) for value in result)
    ):
        raise ValueError("invalid_scopes")
    try:
        assert_no_sensitive_values(result)
    except ValueError:
        raise ValueError("invalid_scopes") from None
    return tuple(sorted(result))


class BrokerProtocol:
    """In-memory AC2 protocol state machine with no live authority."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        entropy: Callable[[int], bytes] | None = None,
        verifier: AssertionVerifier,
    ):
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entropy = entropy or secrets.token_bytes
        self._verifier = verifier
        self._challenges: dict[str, _ChallengeRecord] = {}
        self._sessions: dict[str, _SessionRecord] = {}
        self._grants: dict[str, _GrantRecord] = {}
        self._receipts: list[BrokerReceipt] = []
        self._event_ids: dict[str, bool] = {}
        self._lock = threading.RLock()

    @property
    def receipts(self) -> tuple[BrokerReceipt, ...]:
        return tuple(self._receipts)

    def _now(self) -> datetime:
        return _require_utc(self._clock())

    def _random_token(self, size: int = 32) -> str:
        raw = self._entropy(size)
        if not isinstance(raw, bytes) or len(raw) != size:
            raise RuntimeError("entropy_failure")
        return _token(raw)

    def _unique_id(self, namespace: dict[str, Any], prefix: str) -> str:
        for _ in range(4):
            candidate = prefix + self._random_token(18)
            if candidate not in namespace:
                return candidate
        raise RuntimeError("identifier_collision")

    def _receipt(
        self,
        *,
        action: str,
        outcome: Literal["allowed", "denied", "recorded"],
        subject_id: str,
        detail_code: str,
        at: datetime,
    ) -> BrokerReceipt:
        receipt = BrokerReceipt(
            protocol_version=PROTOCOL_VERSION,
            event_id=self._unique_id(self._event_ids, "evt_"),
            action=action,
            outcome=outcome,
            subject_id=subject_id,
            at=at,
            detail_code=detail_code,
        )
        self._event_ids[receipt.event_id] = True
        self._receipts.append(receipt)
        return receipt

    def issue_challenge(
        self,
        *,
        rp_id: str,
        origin: str,
        identity: str,
        client_session_id: str,
        ttl_seconds: int,
    ) -> ChallengeCeremony:
        rp_id = _require_rp_id(rp_id)
        origin = _require_origin_for_rp(origin, rp_id)
        identity = _require_identity(identity)
        ttl_seconds = _require_ttl(ttl_seconds)
        if not isinstance(client_session_id, str) or not client_session_id:
            raise ValueError("invalid client_session_id")
        with self._lock:
            now = self._now()
            challenge_id = self._unique_id(self._challenges, "chl_")
            challenge = self._random_token(32)
            expires_at = now + timedelta(seconds=ttl_seconds)
            self._challenges[challenge_id] = _ChallengeRecord(
                challenge_id=challenge_id,
                challenge_digest=_digest(challenge),
                rp_id=rp_id,
                origin=origin,
                identity=identity,
                client_session_digest=_digest(client_session_id),
                issued_at=now,
                expires_at=expires_at,
            )
            self._receipt(
                action="challenge.issue",
                outcome="recorded",
                subject_id=challenge_id,
                detail_code="challenge_issued",
                at=now,
            )
            return ChallengeCeremony(challenge_id, challenge, expires_at)

    def _assertion_denied(
        self, record: _ChallengeRecord, code: str, now: datetime
    ) -> None:
        self._receipt(
            action="assertion.verify",
            outcome="denied",
            subject_id=record.challenge_id,
            detail_code=code,
            at=now,
        )
        raise AssertionRejected(code) from None

    def authenticate(
        self,
        *,
        challenge_id: str,
        challenge: str,
        assertion: Any,
        rp_id: str,
        origin: str,
        identity: str,
        client_session_id: str,
        scopes: Sequence[str],
        grant_ttl_seconds: int,
    ) -> BrokerAccess:
        with self._lock:
            now = self._now()
            record = self._challenges.get(challenge_id)
            if record is None:
                raise AssertionRejected("unknown_challenge")
            if record.state != "issued":
                raise AssertionRejected("challenge_not_usable")
            if now >= record.expires_at:
                record.state = "expired"
                self._assertion_denied(record, "challenge_expired", now)

            # Every presented ceremony is one attempt, even when a later
            # binding or verifier check fails.  A retry requires fresh entropy.
            record.state = "consumed"
            checks = (
                (
                    isinstance(challenge, str)
                    and secrets.compare_digest(
                        _digest(challenge), record.challenge_digest
                    ),
                    "challenge_mismatch",
                ),
                (rp_id == record.rp_id, "rp_id_mismatch"),
                (origin == record.origin, "origin_mismatch"),
                (identity == record.identity, "identity_mismatch"),
                (
                    isinstance(client_session_id, str)
                    and secrets.compare_digest(
                        _digest(client_session_id), record.client_session_digest
                    ),
                    "client_session_mismatch",
                ),
            )
            for accepted, code in checks:
                if not accepted:
                    self._assertion_denied(record, code, now)
            try:
                checked_scopes = _scopes(scopes)
            except (TypeError, ValueError):
                self._assertion_denied(record, "invalid_scopes", now)
            try:
                ttl = _require_ttl(grant_ttl_seconds)
            except (TypeError, ValueError):
                self._assertion_denied(record, "invalid_ttl", now)

            expected_rp_id = record.rp_id
            expected_origin = record.origin
            expected_identity = record.identity

        # Provider cryptography can wait for hardware or a remote service. The
        # ceremony is already consumed, so release the broker lock while the
        # trusted adapter verifies it; unrelated revocation must remain live.
        verification_failed = False
        try:
            verified = self._verifier.verify(
                assertion,
                challenge=challenge,
                rp_id=expected_rp_id,
                origin=expected_origin,
                identity=expected_identity,
            )
        except Exception:  # noqa: BLE001 - verifier failures cross a trust boundary
            verification_failed = True
            verified = None

        with self._lock:
            now = self._now()
            current = self._challenges.get(challenge_id)
            if current is not record or record.state != "consumed":
                raise AssertionRejected("challenge_not_usable")
            if verification_failed:
                self._assertion_denied(record, "assertion_rejected", now)
            if now >= record.expires_at:
                self._assertion_denied(record, "challenge_expired", now)

            if (
                not isinstance(verified, VerifiedAssertion)
                or verified.rp_id != record.rp_id
                or verified.origin != record.origin
                or verified.identity != record.identity
                or verified.user_verified is not True
                or not isinstance(verified.credential_digest, str)
                or not _DIGEST.fullmatch(verified.credential_digest)
            ):
                self._assertion_denied(record, "verified_binding_mismatch", now)

            session_id = self._unique_id(self._sessions, "ses_")
            grant_id = self._unique_id(self._grants, "grt_")
            grant_token = self._random_token(32)
            expires_at = now + timedelta(seconds=ttl)
            self._sessions[session_id] = _SessionRecord(
                session_id=session_id,
                identity=record.identity,
                client_session_digest=record.client_session_digest,
                credential_digest=verified.credential_digest,
                issued_at=now,
                expires_at=expires_at,
            )
            self._grants[grant_id] = _GrantRecord(
                grant_id=grant_id,
                token_digest=_digest(grant_token),
                session_id=session_id,
                scopes=checked_scopes,
                issued_at=now,
                expires_at=expires_at,
            )
            self._receipt(
                action="grant.issue",
                outcome="allowed",
                subject_id=grant_id,
                detail_code="verified_session_grant",
                at=now,
            )
            return BrokerAccess(
                session_id=session_id,
                grant_id=grant_id,
                grant_token=grant_token,
                expires_at=expires_at,
                scopes=checked_scopes,
            )

    def _grant_denied(self, grant_id: str, code: str, now: datetime) -> None:
        self._receipt(
            action="grant.authorize",
            outcome="denied",
            subject_id=grant_id,
            detail_code=code,
            at=now,
        )
        raise GrantRejected(code)

    def authorize(
        self,
        *,
        grant_id: str,
        grant_token: str,
        session_id: str,
        client_session_id: str,
        required_scope: str,
    ) -> BrokerReceipt:
        with self._lock:
            now = self._now()
            grant = self._grants.get(grant_id)
            if grant is None:
                raise GrantRejected("unknown_grant")
            session = self._sessions.get(grant.session_id)
            if session is not None and session.revoked_at is not None:
                self._grant_denied(grant_id, "session_revoked", now)
            if grant.revoked_at is not None:
                self._grant_denied(grant_id, "grant_revoked", now)
            if session is None:
                self._grant_denied(grant_id, "session_missing", now)
            if now >= session.expires_at or now >= grant.expires_at:
                self._grant_denied(grant_id, "grant_expired", now)
            if session_id != grant.session_id:
                self._grant_denied(grant_id, "session_mismatch", now)
            if not isinstance(client_session_id, str) or not secrets.compare_digest(
                _digest(client_session_id), session.client_session_digest
            ):
                self._grant_denied(grant_id, "client_session_mismatch", now)
            if not isinstance(grant_token, str) or not secrets.compare_digest(
                _digest(grant_token), grant.token_digest
            ):
                self._grant_denied(grant_id, "grant_token_mismatch", now)
            if required_scope not in grant.scopes:
                self._grant_denied(grant_id, "scope_denied", now)
            return self._receipt(
                action="grant.authorize",
                outcome="allowed",
                subject_id=grant_id,
                detail_code="scope_allowed",
                at=now,
            )

    def revoke_session(self, session_id: str, *, reason: str) -> BrokerReceipt:
        if reason not in _REVOCATION_REASONS:
            raise ValueError("invalid revocation reason")
        with self._lock:
            now = self._now()
            session = self._sessions.get(session_id)
            if session is None:
                raise GrantRejected("unknown_session")
            if session.revoked_at is None:
                session.revoked_at = now
                for grant in self._grants.values():
                    if grant.session_id == session_id:
                        grant.revoked_at = now
            return self._receipt(
                action="session.revoke",
                outcome="recorded",
                subject_id=session_id,
                detail_code=reason,
                at=now,
            )

    def conformance(self) -> ConformanceReport:
        with self._lock:
            problems = []
            for grant in self._grants.values():
                session = self._sessions.get(grant.session_id)
                if grant.revoked_at is None and (
                    session is None
                    or session.revoked_at is not None
                    or grant.expires_at > session.expires_at
                ):
                    problems.append(grant.grant_id)
            problems.sort()
            return ConformanceReport(
                check="owner_standin.broker_protocol_conformance",
                healthy=not problems,
                problem_ids=tuple(problems),
            )

    def reconcile(self) -> ReconcileReport:
        with self._lock:
            now = self._now()
            revoked: list[str] = []
            expired_challenges: list[str] = []
            expired_sessions: list[str] = []
            for challenge in self._challenges.values():
                if challenge.state == "issued" and now >= challenge.expires_at:
                    challenge.state = "expired"
                    expired_challenges.append(challenge.challenge_id)
            for session in self._sessions.values():
                if session.revoked_at is None and now >= session.expires_at:
                    session.revoked_at = now
                    expired_sessions.append(session.session_id)
            for grant in self._grants.values():
                session = self._sessions.get(grant.session_id)
                inconsistent = (
                    session is None
                    or session.revoked_at is not None
                    or grant.expires_at > session.expires_at
                )
                if grant.revoked_at is None and (inconsistent or now >= grant.expires_at):
                    grant.revoked_at = now
                    revoked.append(grant.grant_id)
            self._receipt(
                action="broker.reconcile",
                outcome="recorded",
                subject_id="broker-state",
                detail_code="state_reconciled",
                at=now,
            )
            return ReconcileReport(
                operation="owner_standin.broker_protocol_reconcile",
                revoked_grant_ids=tuple(sorted(revoked)),
                expired_challenge_ids=tuple(sorted(expired_challenges)),
                expired_session_ids=tuple(sorted(expired_sessions)),
            )

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return persisted/loggable state; transient bearer material is absent."""
        with self._lock:
            return {
                "challenges": [
                    asdict(self._challenges[key]) for key in sorted(self._challenges)
                ],
                "sessions": [
                    asdict(self._sessions[key]) for key in sorted(self._sessions)
                ],
                "grants": [asdict(self._grants[key]) for key in sorted(self._grants)],
            }
