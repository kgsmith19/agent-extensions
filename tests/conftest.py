"""Suite-wide isolation for the AGENT_* bundle stage (issue #60).

`ae bootstrap` reads the machine identity from the INFISICAL_* environment
when no seed is given. A developer or cloud session often has those set, so
every test starts without them: no test may reach the real secret provider
or write real bundle values into a temporary HOME. Tests that exercise the
bundle stage pass an explicit fake identity and a fake client.
"""

import pytest

_IDENTITY_KEYS = ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID")


@pytest.fixture(autouse=True)
def _no_real_secret_provider_identity(monkeypatch):
    """Remove the machine identity from every test's environment."""
    for key in _IDENTITY_KEYS:
        monkeypatch.delenv(key, raising=False)
