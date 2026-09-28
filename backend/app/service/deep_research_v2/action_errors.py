"""Bounded, server-authored recovery hints; never an authorization shortcut."""
from copy import deepcopy
import re


DETERMINISTIC_ERROR_CODES = frozenset({
    "citation_source_unavailable", "citation_source_unread", "citation_quote_not_found",
    "citation_changed", "calculation_reference_invalid", "calculation_required",
})


def safe_reference(value):
    """Only echo small identifier-shaped arguments, never arbitrary model text."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else None


class ActionError(ValueError):
    """A failed action with a safe message and optional actionable repair data.

    Callers create messages/hints from fixed server templates and scope-checked
    receipts, not exception text. This remains a ValueError for legacy callers.
    """

    def __init__(self, error_code, message, *, repair=None, field_path=None):
        super().__init__(message)
        self.error_code = error_code
        self.repair = deepcopy(repair or {})
        self.field_path = field_path

    def at(self, field_path, *, prefix=""):
        return ActionError(self.error_code, prefix + str(self), repair=self.repair,
                           field_path=field_path)

    def as_result(self):
        result = {"ok": False, "progress": False, "error": str(self),
                  "error_code": self.error_code, "repair": deepcopy(self.repair)}
        if self.field_path:
            result["field_path"] = self.field_path
        return result


def resolve_at(resolve_citation, source_id, quote_id, field_path):
    """Preserve controlled resolver errors; hide all unexpected resolver detail."""
    try:
        return resolve_citation(source_id, quote_id)
    except ActionError as exc:
        raise exc.at(field_path) from None
    except Exception:
        raise ActionError(
            "citation_resolution_failed", "引文校验未完成，不能据此形成计算或答复。",
            repair={"instruction": "核对已返回的授权来源及已读 quote_options；不能猜测引文或认定材料不存在。",
                    "actions": []}, field_path=field_path,
        ) from None
