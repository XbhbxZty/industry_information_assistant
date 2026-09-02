"""Pure cryptographic protocol for administrator company-profile audit chains.

This module intentionally has no database, ORM, router, settings, or environment
dependency.  Phase 3.4D1 freezes the bytes that later migrations and persistence
code may sign; it does not enable the protocol in production by itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence


AUDIT_CHAIN_FORMAT = "admin-company-profile-audit-hmac/v1"
LEGACY_ANCHOR_FORMAT = "admin-company-profile-audit-legacy-anchor-hmac/v1"
LEGACY_HISTORY_FORMAT = "admin-company-profile-audit-legacy-history/v1"
INTEGRITY_VERSION = 1
INTEGRITY_ALGORITHM = "hmac-sha256"

CHAIN_START_GENESIS = "genesis"
CHAIN_START_LEGACY_ANCHOR = "legacy_anchor"

_ALLOWED_CHAIN_STARTS = frozenset({CHAIN_START_GENESIS, CHAIN_START_LEGACY_ANCHOR})
_ALLOWED_ACTIONS = frozenset({"created", "updated", "archived"})
_KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MIGRATION_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z"
)

_ROOT_CONTEXT = b"admin-company-profile-audit-integrity/v1"
_ENTRY_DOMAIN = b"audit-entry"
_ANCHOR_DOMAIN = b"legacy-anchor"
_SNAPSHOT_HASH_CONTEXT = b"admin-company-profile-audit-snapshot/v1\x00"
_LEGACY_HISTORY_HASH_CONTEXT = b"admin-company-profile-audit-legacy-history/v1\x00"

_ENTRY_PAYLOAD_KEYS = frozenset({
    "format",
    "integrity_version",
    "algorithm",
    "key_id",
    "profile_id",
    "audit_id",
    "revision",
    "action",
    "actor_id",
    "change_reason",
    "created_at",
    "content_sha256",
    "before_snapshot_sha256",
    "after_snapshot_sha256",
    "previous_audit_mac",
    "chain_start",
})
_ENTRY_RECORD_KEYS = _ENTRY_PAYLOAD_KEYS | {"audit_mac"}

_ANCHOR_PAYLOAD_KEYS = frozenset({
    "format",
    "integrity_version",
    "algorithm",
    "key_id",
    "profile_id",
    "legacy_cutover_revision",
    "legacy_chain_sha256",
    "legacy_terminal_snapshot_sha256",
    "anchored_at",
    "migration_run_id",
})
_ANCHOR_RECORD_KEYS = _ANCHOR_PAYLOAD_KEYS | {"anchor_mac"}


class CompanyProfileAuditIntegrityError(ValueError):
    """The keyring, canonical payload, MAC, or chain failed validation."""


def _require_strict_json(value: Any, *, path: str = "$") -> None:
    """Reject Python values that do not have an unambiguous JSON meaning."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise CompanyProfileAuditIntegrityError(f"{path} 含有非有限浮点数")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _require_strict_json(item, path=f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise CompanyProfileAuditIntegrityError(f"{path} 的对象字段名必须是字符串")
            _require_strict_json(item, path=f"{path}.{key}")
        return
    raise CompanyProfileAuditIntegrityError(f"{path} 含有不支持的 JSON 类型")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize strict JSON to the exact UTF-8 representation used by v1."""
    try:
        _require_strict_json(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except CompanyProfileAuditIntegrityError:
        raise
    except (RecursionError, TypeError, ValueError) as exc:
        raise CompanyProfileAuditIntegrityError("审计内容必须是无环的严格 JSON") from exc


def canonical_utc_timestamp(value: datetime) -> str:
    """Return a fixed-width UTC timestamp; naive datetimes mean legacy UTC."""
    if not isinstance(value, datetime):
        raise CompanyProfileAuditIntegrityError("审计时间必须是 datetime")
    if value.tzinfo is None:
        utc_value = value.replace(tzinfo=timezone.utc)
    else:
        utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise CompanyProfileAuditIntegrityError(f"{label} 必须是 UTC 微秒时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise CompanyProfileAuditIntegrityError(f"{label} 非法") from exc
    if canonical_utc_timestamp(parsed) != value:
        raise CompanyProfileAuditIntegrityError(f"{label} 不是规范时间")
    return value


def _require_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CompanyProfileAuditIntegrityError(f"{label} 必须是规范 UUID")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise CompanyProfileAuditIntegrityError(f"{label} 必须是规范 UUID") from exc
    if str(parsed) != value:
        raise CompanyProfileAuditIntegrityError(f"{label} 必须是小写连字符 UUID")
    return value


def _require_digest(value: Any, label: str, *, nullable: bool = False) -> Optional[str]:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _HEX_SHA256_RE.fullmatch(value) is None:
        raise CompanyProfileAuditIntegrityError(f"{label} 必须是小写 SHA-256 十六进制摘要")
    return value


def _require_revision(value: Any, label: str = "revision") -> int:
    if type(value) is not int or value < 1:
        raise CompanyProfileAuditIntegrityError(f"{label} 必须是正整数")
    return value


def _require_text(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > maximum:
        raise CompanyProfileAuditIntegrityError(f"{label} 必须是 1～{maximum} 个规范字符")
    return value


def _require_key_id(value: Any) -> str:
    if not isinstance(value, str) or _KEY_ID_RE.fullmatch(value) is None:
        raise CompanyProfileAuditIntegrityError("审计 key id 非法")
    return value


def _require_migration_run_id(value: Any) -> str:
    if not isinstance(value, str) or _MIGRATION_RUN_ID_RE.fullmatch(value) is None:
        raise CompanyProfileAuditIntegrityError("迁移批次标识非法")
    return value


def _require_exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value.keys()) != expected:
        raise CompanyProfileAuditIntegrityError(f"{label}字段形状非法")
    return value


class AuditKeyring:
    """Explicit signing/verification keys with no settings or JWT fallback."""

    __slots__ = ("_active_key_id", "_keys")

    def __init__(self, *, active_key_id: str, keys: Mapping[str, bytes]) -> None:
        active = _require_key_id(active_key_id)
        if not isinstance(keys, Mapping) or not keys:
            raise CompanyProfileAuditIntegrityError("审计 keyring 不能为空")
        copied: dict[str, bytes] = {}
        for key_id, material in keys.items():
            canonical_key_id = _require_key_id(key_id)
            if type(material) is not bytes or len(material) < 32:
                raise CompanyProfileAuditIntegrityError("审计密钥必须是至少 32 字节的 bytes")
            copied[canonical_key_id] = material
        if active not in copied:
            raise CompanyProfileAuditIntegrityError("active key id 不在审计 keyring 中")
        self._active_key_id = active
        self._keys = copied

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    @property
    def key_ids(self) -> frozenset[str]:
        """Expose verification identifiers, never key material."""
        return frozenset(self._keys)

    def _derived_key(self, key_id: str, domain: bytes) -> bytes:
        canonical_key_id = _require_key_id(key_id)
        material = self._keys.get(canonical_key_id)
        if material is None:
            raise CompanyProfileAuditIntegrityError("审计记录引用未知 key id")
        return hmac.new(
            material,
            _ROOT_CONTEXT + b"/" + domain + b"/" + canonical_key_id.encode("ascii"),
            hashlib.sha256,
        ).digest()

    def _signing_key(self, domain: bytes) -> bytes:
        return self._derived_key(self._active_key_id, domain)

    def _verification_key(self, key_id: str, domain: bytes) -> bytes:
        return self._derived_key(key_id, domain)


def snapshot_sha256(snapshot: Any) -> str:
    """Hash a complete audit snapshot, not only its domain-content subset."""
    if type(snapshot) is not dict:
        raise CompanyProfileAuditIntegrityError("完整审计快照必须是 JSON 对象")
    return hashlib.sha256(
        _SNAPSHOT_HASH_CONTEXT + canonical_json_bytes(snapshot)
    ).hexdigest()


def legacy_history_sha256(audits: Any) -> str:
    """Hash the complete ordered legacy sequence for a migration-time anchor."""
    if type(audits) is not list or not audits:
        raise CompanyProfileAuditIntegrityError("旧审计历史必须是非空 JSON 数组")
    wrapped = {"format": LEGACY_HISTORY_FORMAT, "audits": audits}
    return hashlib.sha256(
        _LEGACY_HISTORY_HASH_CONTEXT + canonical_json_bytes(wrapped)
    ).hexdigest()


def _entry_payload(record: Any) -> dict[str, Any]:
    mapping = _require_exact_keys(record, _ENTRY_RECORD_KEYS, "审计记录")
    payload = {key: mapping[key] for key in _ENTRY_PAYLOAD_KEYS}
    if payload["format"] != AUDIT_CHAIN_FORMAT:
        raise CompanyProfileAuditIntegrityError("审计记录格式不受支持")
    if payload["integrity_version"] != INTEGRITY_VERSION:
        raise CompanyProfileAuditIntegrityError("审计记录版本不受支持")
    if payload["algorithm"] != INTEGRITY_ALGORITHM:
        raise CompanyProfileAuditIntegrityError("审计记录算法不受支持")
    _require_key_id(payload["key_id"])
    _require_uuid(payload["profile_id"], "profile_id")
    _require_uuid(payload["audit_id"], "audit_id")
    _require_revision(payload["revision"])
    if payload["action"] not in _ALLOWED_ACTIONS:
        raise CompanyProfileAuditIntegrityError("审计 action 非法")
    _require_text(payload["actor_id"], "actor_id", maximum=64)
    _require_text(payload["change_reason"], "change_reason", maximum=500)
    _require_timestamp(payload["created_at"], "created_at")
    _require_digest(payload["content_sha256"], "content_sha256")
    _require_digest(
        payload["before_snapshot_sha256"], "before_snapshot_sha256", nullable=True,
    )
    _require_digest(payload["after_snapshot_sha256"], "after_snapshot_sha256")
    _require_digest(payload["previous_audit_mac"], "previous_audit_mac", nullable=True)
    if payload["chain_start"] not in _ALLOWED_CHAIN_STARTS:
        raise CompanyProfileAuditIntegrityError("审计链起点类型非法")
    if payload["revision"] == 1:
        if payload["chain_start"] != CHAIN_START_GENESIS:
            raise CompanyProfileAuditIntegrityError("首条审计记录必须从 genesis 开始")
        if payload["action"] != "created":
            raise CompanyProfileAuditIntegrityError("首条审计记录必须是 created")
        if payload["before_snapshot_sha256"] is not None:
            raise CompanyProfileAuditIntegrityError("首条审计记录不得包含 before snapshot")
        if payload["previous_audit_mac"] is not None:
            raise CompanyProfileAuditIntegrityError("首条审计记录不得包含前序 MAC")
    else:
        if payload["action"] == "created":
            raise CompanyProfileAuditIntegrityError("非首条审计记录不得是 created")
        if payload["before_snapshot_sha256"] is None:
            raise CompanyProfileAuditIntegrityError("后续审计记录必须包含 before snapshot")
        if payload["previous_audit_mac"] is None:
            raise CompanyProfileAuditIntegrityError("后续审计记录必须包含前序 MAC")
    _require_digest(mapping["audit_mac"], "audit_mac")
    return payload


def issue_audit_record(
    *,
    keyring: AuditKeyring,
    profile_id: str,
    audit_id: str,
    revision: int,
    action: str,
    actor_id: str,
    change_reason: str,
    created_at: str,
    content_sha256: str,
    before_snapshot_sha256: Optional[str],
    after_snapshot_sha256: str,
    previous_audit_mac: Optional[str],
    chain_start: str,
) -> dict[str, Any]:
    """Issue one strict v1 audit record with the active key."""
    if not isinstance(keyring, AuditKeyring):
        raise CompanyProfileAuditIntegrityError("缺少合法审计 keyring")
    payload: dict[str, Any] = {
        "format": AUDIT_CHAIN_FORMAT,
        "integrity_version": INTEGRITY_VERSION,
        "algorithm": INTEGRITY_ALGORITHM,
        "key_id": keyring.active_key_id,
        "profile_id": profile_id,
        "audit_id": audit_id,
        "revision": revision,
        "action": action,
        "actor_id": actor_id,
        "change_reason": change_reason,
        "created_at": created_at,
        "content_sha256": content_sha256,
        "before_snapshot_sha256": before_snapshot_sha256,
        "after_snapshot_sha256": after_snapshot_sha256,
        "previous_audit_mac": previous_audit_mac,
        "chain_start": chain_start,
    }
    candidate = {**payload, "audit_mac": "0" * 64}
    payload = _entry_payload(candidate)
    audit_mac = hmac.new(
        keyring._signing_key(_ENTRY_DOMAIN),
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return {**payload, "audit_mac": audit_mac}


def verify_audit_record(record: Any, keyring: AuditKeyring) -> None:
    """Verify one record strictly by its persisted key id."""
    if not isinstance(keyring, AuditKeyring):
        raise CompanyProfileAuditIntegrityError("缺少合法审计 keyring")
    payload = _entry_payload(record)
    expected = hmac.new(
        keyring._verification_key(payload["key_id"], _ENTRY_DOMAIN),
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(record["audit_mac"], expected):
        raise CompanyProfileAuditIntegrityError("审计记录 MAC 不一致")


def _anchor_payload(record: Any) -> dict[str, Any]:
    mapping = _require_exact_keys(record, _ANCHOR_RECORD_KEYS, "旧历史锚点")
    payload = {key: mapping[key] for key in _ANCHOR_PAYLOAD_KEYS}
    if payload["format"] != LEGACY_ANCHOR_FORMAT:
        raise CompanyProfileAuditIntegrityError("旧历史锚点格式不受支持")
    if payload["integrity_version"] != INTEGRITY_VERSION:
        raise CompanyProfileAuditIntegrityError("旧历史锚点版本不受支持")
    if payload["algorithm"] != INTEGRITY_ALGORITHM:
        raise CompanyProfileAuditIntegrityError("旧历史锚点算法不受支持")
    _require_key_id(payload["key_id"])
    _require_uuid(payload["profile_id"], "profile_id")
    _require_revision(payload["legacy_cutover_revision"], "legacy_cutover_revision")
    _require_digest(payload["legacy_chain_sha256"], "legacy_chain_sha256")
    _require_digest(
        payload["legacy_terminal_snapshot_sha256"],
        "legacy_terminal_snapshot_sha256",
    )
    _require_timestamp(payload["anchored_at"], "anchored_at")
    _require_migration_run_id(payload["migration_run_id"])
    _require_digest(mapping["anchor_mac"], "anchor_mac")
    return payload


def issue_legacy_anchor(
    *,
    keyring: AuditKeyring,
    profile_id: str,
    legacy_cutover_revision: int,
    legacy_chain_sha256: str,
    legacy_terminal_snapshot_sha256: str,
    anchored_at: str,
    migration_run_id: str,
) -> dict[str, Any]:
    """Attest what a migration observed without claiming original authenticity."""
    if not isinstance(keyring, AuditKeyring):
        raise CompanyProfileAuditIntegrityError("缺少合法审计 keyring")
    payload: dict[str, Any] = {
        "format": LEGACY_ANCHOR_FORMAT,
        "integrity_version": INTEGRITY_VERSION,
        "algorithm": INTEGRITY_ALGORITHM,
        "key_id": keyring.active_key_id,
        "profile_id": profile_id,
        "legacy_cutover_revision": legacy_cutover_revision,
        "legacy_chain_sha256": legacy_chain_sha256,
        "legacy_terminal_snapshot_sha256": legacy_terminal_snapshot_sha256,
        "anchored_at": anchored_at,
        "migration_run_id": migration_run_id,
    }
    candidate = {**payload, "anchor_mac": "0" * 64}
    payload = _anchor_payload(candidate)
    anchor_mac = hmac.new(
        keyring._signing_key(_ANCHOR_DOMAIN),
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return {**payload, "anchor_mac": anchor_mac}


def verify_legacy_anchor(anchor: Any, keyring: AuditKeyring) -> None:
    """Verify a migration-time anchor strictly by its persisted key id."""
    if not isinstance(keyring, AuditKeyring):
        raise CompanyProfileAuditIntegrityError("缺少合法审计 keyring")
    payload = _anchor_payload(anchor)
    expected = hmac.new(
        keyring._verification_key(payload["key_id"], _ANCHOR_DOMAIN),
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(anchor["anchor_mac"], expected):
        raise CompanyProfileAuditIntegrityError("旧历史锚点 MAC 不一致")


def verify_legacy_anchor_observation(
    anchor: Any,
    keyring: AuditKeyring,
    *,
    legacy_audits: Any,
    terminal_snapshot: Any,
) -> None:
    """Verify both the anchor MAC and the exact legacy data it attests."""
    verify_legacy_anchor(anchor, keyring)
    payload = _anchor_payload(anchor)
    if legacy_history_sha256(legacy_audits) != payload["legacy_chain_sha256"]:
        raise CompanyProfileAuditIntegrityError("旧审计历史与迁移锚点不一致")
    if snapshot_sha256(terminal_snapshot) != payload["legacy_terminal_snapshot_sha256"]:
        raise CompanyProfileAuditIntegrityError("旧审计终态快照与迁移锚点不一致")


@dataclass(frozen=True)
class AuditChainHead:
    """Caller-supplied expected terminal state used to detect missing suffixes.

    Storing this head in the same mutable database does not make rollback attacks
    impossible.  D2 must pair it with database append-only controls, and stronger
    anti-rollback requirements need an external monotonic anchor.
    """

    profile_id: str
    revision: int
    snapshot_sha256: str
    mac: str


@dataclass(frozen=True)
class AuditSnapshotObservation:
    """One signed record together with the full snapshots stored beside it."""

    record: Mapping[str, Any]
    before_snapshot: Optional[dict[str, Any]]
    after_snapshot: dict[str, Any]


def _validate_expected_head(head: Any, profile_id: str) -> AuditChainHead:
    if not isinstance(head, AuditChainHead):
        raise CompanyProfileAuditIntegrityError("缺少合法预期审计链 head")
    if _require_uuid(head.profile_id, "head.profile_id") != profile_id:
        raise CompanyProfileAuditIntegrityError("审计链 head 的 profile_id 不一致")
    _require_revision(head.revision, "head.revision")
    _require_digest(head.snapshot_sha256, "head.snapshot_sha256")
    _require_digest(head.mac, "head.mac")
    return head


def verify_audit_chain(
    records: Sequence[Mapping[str, Any]],
    *,
    keyring: AuditKeyring,
    expected_profile_id: str,
    expected_head: AuditChainHead,
    legacy_anchor: Optional[Mapping[str, Any]] = None,
) -> None:
    """Verify the cryptographic envelope, links, and expected head only.

    This lower-level function cannot bind a digest to the business semantics of
    the actual snapshots.  Persistence boundaries must use
    :func:`verify_observed_audit_chain`, not this function alone.
    """
    profile_id = _require_uuid(expected_profile_id, "expected_profile_id")
    head = _validate_expected_head(expected_head, profile_id)
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise CompanyProfileAuditIntegrityError("审计链必须是有序记录序列")
    ordered = list(records)

    if legacy_anchor is None:
        if not ordered:
            raise CompanyProfileAuditIntegrityError("原生审计链不能为空")
        next_revision = 1
        previous_mac: Optional[str] = None
        previous_snapshot: Optional[str] = None
        chain_start = CHAIN_START_GENESIS
        previous_timestamp: Optional[str] = None
    else:
        verify_legacy_anchor(legacy_anchor, keyring)
        anchor_payload = _anchor_payload(legacy_anchor)
        if anchor_payload["profile_id"] != profile_id:
            raise CompanyProfileAuditIntegrityError("旧历史锚点 profile_id 不一致")
        next_revision = anchor_payload["legacy_cutover_revision"] + 1
        previous_mac = legacy_anchor["anchor_mac"]
        previous_snapshot = anchor_payload["legacy_terminal_snapshot_sha256"]
        chain_start = CHAIN_START_LEGACY_ANCHOR
        previous_timestamp = anchor_payload["anchored_at"]

    archived = False
    seen_audit_ids: set[str] = set()
    for index, record in enumerate(ordered):
        verify_audit_record(record, keyring)
        payload = _entry_payload(record)
        if payload["profile_id"] != profile_id:
            raise CompanyProfileAuditIntegrityError("审计记录 profile_id 不一致")
        if payload["revision"] != next_revision:
            raise CompanyProfileAuditIntegrityError("审计记录 revision 不连续")
        if payload["chain_start"] != chain_start:
            raise CompanyProfileAuditIntegrityError("审计记录链起点声明不一致")
        if payload["previous_audit_mac"] != previous_mac:
            raise CompanyProfileAuditIntegrityError("审计记录前序 MAC 不连续")
        if payload["before_snapshot_sha256"] != previous_snapshot:
            raise CompanyProfileAuditIntegrityError("审计记录前后快照摘要不连续")
        if payload["audit_id"] in seen_audit_ids:
            raise CompanyProfileAuditIntegrityError("审计记录 audit_id 重复")
        seen_audit_ids.add(payload["audit_id"])
        if previous_timestamp is not None and payload["created_at"] < previous_timestamp:
            raise CompanyProfileAuditIntegrityError("审计记录时间倒退")
        if archived:
            raise CompanyProfileAuditIntegrityError("归档后不得存在后续审计记录")
        if next_revision == 1:
            if payload["action"] != "created":
                raise CompanyProfileAuditIntegrityError("原生审计链首条必须是 created")
        elif payload["action"] == "created":
            raise CompanyProfileAuditIntegrityError("非首条审计记录不得是 created")
        if payload["action"] == "archived":
            archived = True
            if index != len(ordered) - 1:
                raise CompanyProfileAuditIntegrityError("归档记录必须是审计链末条")
        previous_mac = record["audit_mac"]
        previous_snapshot = payload["after_snapshot_sha256"]
        previous_timestamp = payload["created_at"]
        next_revision += 1

    terminal_revision = next_revision - 1
    if terminal_revision != head.revision:
        raise CompanyProfileAuditIntegrityError("审计链与预期 head revision 不一致")
    if previous_snapshot != head.snapshot_sha256:
        raise CompanyProfileAuditIntegrityError("审计链与预期 head 快照不一致")
    if previous_mac != head.mac:
        raise CompanyProfileAuditIntegrityError("审计链与预期 head MAC 不一致")


def _validate_observed_snapshot(
    snapshot: Any,
    *,
    validate_snapshot: Callable[[dict[str, Any]], str],
    profile_id: str,
    revision: int,
    label: str,
) -> tuple[dict[str, Any], str]:
    if type(snapshot) is not dict:
        raise CompanyProfileAuditIntegrityError(f"{label}必须是完整 JSON 对象")
    if _require_uuid(snapshot.get("id"), f"{label}.id") != profile_id:
        raise CompanyProfileAuditIntegrityError(f"{label} profile_id 不一致")
    if _require_revision(snapshot.get("revision"), f"{label}.revision") != revision:
        raise CompanyProfileAuditIntegrityError(f"{label} revision 不一致")
    if snapshot.get("status") not in {"active", "archived"}:
        raise CompanyProfileAuditIntegrityError(f"{label} status 非法")
    embedded_content_sha = _require_digest(
        snapshot.get("content_sha256"), f"{label}.content_sha256",
    )
    digest_before_validation = snapshot_sha256(snapshot)
    try:
        validated_content_sha = validate_snapshot(snapshot)
    except CompanyProfileAuditIntegrityError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CompanyProfileAuditIntegrityError(f"{label}未通过领域校验") from exc
    validated_content_sha = _require_digest(
        validated_content_sha, f"{label}领域 content_sha256",
    )
    if validated_content_sha != embedded_content_sha:
        raise CompanyProfileAuditIntegrityError(f"{label}领域内容摘要不一致")
    if snapshot_sha256(snapshot) != digest_before_validation:
        raise CompanyProfileAuditIntegrityError(f"{label}领域校验器不得修改快照")
    return snapshot, digest_before_validation


def verify_observed_audit_chain(
    observations: Sequence[AuditSnapshotObservation],
    *,
    keyring: AuditKeyring,
    expected_profile_id: str,
    expected_head: AuditChainHead,
    expected_current_snapshot: dict[str, Any],
    validate_snapshot: Callable[[dict[str, Any]], str],
    legacy_anchor: Optional[Mapping[str, Any]] = None,
    legacy_audits: Any = None,
    legacy_terminal_snapshot: Any = None,
) -> None:
    """Verify cryptography, actual snapshots, domain semantics, and current row.

    ``validate_snapshot`` is deliberately mandatory.  D2 must pass the existing
    strict company-profile reconstruction validator so a valid MAC can never
    replace field-shape or domain validation.
    """
    profile_id = _require_uuid(expected_profile_id, "expected_profile_id")
    if not callable(validate_snapshot):
        raise CompanyProfileAuditIntegrityError("缺少领域快照校验器")
    if (
        not isinstance(observations, Sequence)
        or isinstance(observations, (str, bytes, bytearray))
        or any(not isinstance(item, AuditSnapshotObservation) for item in observations)
    ):
        raise CompanyProfileAuditIntegrityError("完整审计观测序列非法")
    ordered = list(observations)

    if legacy_anchor is None:
        if legacy_audits is not None or legacy_terminal_snapshot is not None:
            raise CompanyProfileAuditIntegrityError("原生审计链不得携带旧历史观测")
        previous_after: Optional[dict[str, Any]] = None
    else:
        if legacy_audits is None or legacy_terminal_snapshot is None:
            raise CompanyProfileAuditIntegrityError("旧历史锚点必须同时复验旧序列和终态快照")
        verify_legacy_anchor_observation(
            legacy_anchor,
            keyring,
            legacy_audits=legacy_audits,
            terminal_snapshot=legacy_terminal_snapshot,
        )
        anchor_payload = _anchor_payload(legacy_anchor)
        previous_after, terminal_digest = _validate_observed_snapshot(
            legacy_terminal_snapshot,
            validate_snapshot=validate_snapshot,
            profile_id=profile_id,
            revision=anchor_payload["legacy_cutover_revision"],
            label="旧历史终态快照",
        )
        if terminal_digest != anchor_payload["legacy_terminal_snapshot_sha256"]:
            raise CompanyProfileAuditIntegrityError("旧历史终态快照摘要与锚点不一致")

    verify_audit_chain(
        [item.record for item in ordered],
        keyring=keyring,
        expected_profile_id=profile_id,
        expected_head=expected_head,
        legacy_anchor=legacy_anchor,
    )

    for observation in ordered:
        payload = _entry_payload(observation.record)
        after, after_digest = _validate_observed_snapshot(
            observation.after_snapshot,
            validate_snapshot=validate_snapshot,
            profile_id=profile_id,
            revision=payload["revision"],
            label=f"revision {payload['revision']} after_snapshot",
        )
        if after_digest != payload["after_snapshot_sha256"]:
            raise CompanyProfileAuditIntegrityError("审计 after snapshot 与签名摘要不一致")
        if after["content_sha256"] != payload["content_sha256"]:
            raise CompanyProfileAuditIntegrityError("审计记录与 after snapshot 内容摘要不一致")
        expected_status = "archived" if payload["action"] == "archived" else "active"
        if after["status"] != expected_status:
            raise CompanyProfileAuditIntegrityError("审计 action 与 after snapshot 状态不一致")

        before = observation.before_snapshot
        if previous_after is None:
            if before is not None:
                raise CompanyProfileAuditIntegrityError("原生首条审计不得包含 before snapshot")
        else:
            validated_before, before_digest = _validate_observed_snapshot(
                before,
                validate_snapshot=validate_snapshot,
                profile_id=profile_id,
                revision=payload["revision"] - 1,
                label=f"revision {payload['revision']} before_snapshot",
            )
            if validated_before["status"] != "active":
                raise CompanyProfileAuditIntegrityError("归档快照之后不得继续修订")
            if before_digest != payload["before_snapshot_sha256"]:
                raise CompanyProfileAuditIntegrityError("审计 before snapshot 与签名摘要不一致")
            if validated_before != previous_after:
                raise CompanyProfileAuditIntegrityError("实际审计前后快照不连续")
        previous_after = after

    if previous_after is None:
        raise CompanyProfileAuditIntegrityError("完整审计观测缺少终态快照")
    current, current_digest = _validate_observed_snapshot(
        expected_current_snapshot,
        validate_snapshot=validate_snapshot,
        profile_id=profile_id,
        revision=expected_head.revision,
        label="当前企业档案快照",
    )
    if current_digest != expected_head.snapshot_sha256:
        raise CompanyProfileAuditIntegrityError("当前企业档案与审计链 head 摘要不一致")
    if current != previous_after:
        raise CompanyProfileAuditIntegrityError("当前企业档案与审计链终态不一致")
