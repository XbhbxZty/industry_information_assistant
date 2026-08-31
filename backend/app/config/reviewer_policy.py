"""Human-review authorization policy.

The user table deliberately has no reviewer role yet.  Until a migration adds
one, production can grant the *additional* review capability to a small,
explicit list of user UUIDs.  The policy is built once at process import time:
changing the environment requires a restart, which makes authorization changes
auditable and avoids a request-time configuration race.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import FrozenSet, Optional
from uuid import UUID


logger = logging.getLogger(__name__)

_REVIEWER_IDS_ENV = "DD_HUMAN_REVIEWER_USER_IDS"


def parse_human_reviewer_user_ids(raw: Optional[str]) -> FrozenSet[UUID]:
    """Parse a comma-separated, canonical UUID allowlist.

    Empty/unset means no *additional* reviewer.  Any other malformed value is
    rejected as a whole by the caller; accepting its valid subset would make a
    deployment typo silently change who is able to approve a decision.
    """
    if raw is None or not raw.strip():
        return frozenset()

    parsed = set()
    for value in raw.split(","):
        candidate = value.strip()
        if not candidate:
            raise ValueError("empty reviewer UUID entry")
        user_id = UUID(candidate)
        # ``UUID`` accepts convenient non-canonical spellings such as braces
        # and 32-digit strings.  Deployment configuration must be unambiguous.
        if str(user_id) != candidate.lower():
            raise ValueError("reviewer ID is not a canonical UUID")
        parsed.add(user_id)
    return frozenset(parsed)


@dataclass(frozen=True)
class ReviewerPolicy:
    """Frozen review authorization policy for this server process."""

    additional_reviewer_ids: FrozenSet[UUID]

    @classmethod
    def from_environment(cls) -> "ReviewerPolicy":
        raw = os.getenv(_REVIEWER_IDS_ENV)
        try:
            return cls(parse_human_reviewer_user_ids(raw))
        except (TypeError, ValueError, AttributeError):
            # Do not include ``raw`` in this log: environment variables can
            # accidentally contain sensitive deployment data.
            logger.error(
                "%s is invalid; disabling all additional human reviewers",
                _REVIEWER_IDS_ENV,
            )
            return cls(frozenset())

    def can_review(self, user_id: object, *, is_superuser: bool) -> bool:
        """Superusers retain break-glass review; allowlisted users get only it."""
        if is_superuser:
            return True
        try:
            return UUID(str(user_id)) in self.additional_reviewer_ids
        except (TypeError, ValueError, AttributeError):
            return False


# Intentionally frozen for the process lifetime.  Change the environment and
# restart the API process to change review authorization.
REVIEWER_POLICY = ReviewerPolicy.from_environment()
