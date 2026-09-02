"""Configuration boundaries for the D4b1 persistence service, without a DB."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from service.review_claim_service import ReviewClaimService  # noqa: E402


@pytest.mark.parametrize("seconds", [True, False, 0, -1, 3601, 1.5, "300", None])
def test_lease_configuration_requires_bounded_integer(seconds):
    with pytest.raises((TypeError, ValueError)):
        ReviewClaimService(lease_seconds=seconds)


@pytest.mark.parametrize("seconds", [1, 300, 3600])
def test_service_construction_does_not_open_a_database_session(seconds):
    def forbidden():
        pytest.fail("constructing a service must not open a transaction")

    ReviewClaimService(session_factory=forbidden, lease_seconds=seconds)
