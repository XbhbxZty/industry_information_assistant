from __future__ import annotations

import copy
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from service.company_profile_audit_integrity import (
    AUDIT_CHAIN_FORMAT,
    CHAIN_START_GENESIS,
    CHAIN_START_LEGACY_ANCHOR,
    AuditChainHead,
    AuditKeyring,
    AuditSnapshotObservation,
    CompanyProfileAuditIntegrityError,
    canonical_json_bytes,
    canonical_utc_timestamp,
    issue_audit_record,
    issue_legacy_anchor,
    legacy_history_sha256,
    snapshot_sha256,
    verify_audit_chain,
    verify_audit_record,
    verify_legacy_anchor,
    verify_legacy_anchor_observation,
    verify_observed_audit_chain,
)


PROFILE_ID = "11111111-1111-4111-8111-111111111111"
OTHER_PROFILE_ID = "22222222-2222-4222-8222-222222222222"
AUDIT_IDS = (
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4",
)
OLD_KEY = b"o" * 32
NEW_KEY = b"n" * 32
T0 = "2026-08-31T12:00:00.000000Z"
T1 = "2026-08-31T12:01:00.000000Z"
T2 = "2026-08-31T12:02:00.000000Z"
T3 = "2026-08-31T12:03:00.000000Z"


def _ring(active: str = "audit-old", *, include_old: bool = True) -> AuditKeyring:
    keys = {"audit-new": NEW_KEY}
    if include_old:
        keys["audit-old"] = OLD_KEY
    return AuditKeyring(active_key_id=active, keys=keys)


def _snapshot(revision: int, *, status: str = "active", name: str = "测试企业") -> dict:
    return {
        "id": PROFILE_ID,
        "name": name,
        "status": status,
        "revision": revision,
        "content_sha256": hashlib.sha256(f"content-{revision}".encode()).hexdigest(),
        "profile": {"name": name, "tags": ["中文", None]},
        "scenario": "factoring",
        "scenario_data": {},
        "field_sources": [],
        "materials": [],
    }


def _issue(
    *,
    keyring: AuditKeyring,
    revision: int,
    action: str,
    after_sha: str,
    before_sha: str | None,
    previous_mac: str | None,
    chain_start: str = CHAIN_START_GENESIS,
    profile_id: str = PROFILE_ID,
    created_at: str | None = None,
    audit_id: str | None = None,
) -> dict:
    times = {1: T0, 2: T1, 3: T2, 4: T3}
    return issue_audit_record(
        keyring=keyring,
        profile_id=profile_id,
        audit_id=audit_id or AUDIT_IDS[revision - 1],
        revision=revision,
        action=action,
        actor_id="admin-1",
        change_reason=f"第 {revision} 次变更",
        created_at=created_at or times[revision],
        content_sha256=hashlib.sha256(f"content-{revision}".encode()).hexdigest(),
        before_snapshot_sha256=before_sha,
        after_snapshot_sha256=after_sha,
        previous_audit_mac=previous_mac,
        chain_start=chain_start,
    )


def _native_chain() -> tuple[AuditKeyring, list[dict], list[str]]:
    old_ring = _ring("audit-old")
    hashes = [snapshot_sha256(_snapshot(index)) for index in (1, 2, 3)]
    first = _issue(
        keyring=old_ring,
        revision=1,
        action="created",
        before_sha=None,
        after_sha=hashes[0],
        previous_mac=None,
    )
    rotated = _ring("audit-new")
    second = _issue(
        keyring=rotated,
        revision=2,
        action="updated",
        before_sha=hashes[0],
        after_sha=hashes[1],
        previous_mac=first["audit_mac"],
    )
    third = _issue(
        keyring=rotated,
        revision=3,
        action="updated",
        before_sha=hashes[1],
        after_sha=hashes[2],
        previous_mac=second["audit_mac"],
    )
    return rotated, [first, second, third], hashes


def _head(record: dict, snapshot_digest: str) -> AuditChainHead:
    return AuditChainHead(
        profile_id=PROFILE_ID,
        revision=record["revision"],
        snapshot_sha256=snapshot_digest,
        mac=record["audit_mac"],
    )


def _validate_test_snapshot(snapshot: dict) -> str:
    required = {
        "id", "name", "status", "revision", "content_sha256", "profile",
        "scenario", "scenario_data", "field_sources", "materials",
    }
    if set(snapshot) != required or not isinstance(snapshot["profile"], dict):
        raise ValueError("invalid test snapshot domain")
    expected = hashlib.sha256(f"content-{snapshot['revision']}".encode()).hexdigest()
    if snapshot["content_sha256"] != expected:
        raise ValueError("invalid test content digest")
    return expected


def test_canonical_json_snapshot_and_audit_mac_have_stable_fixed_vectors():
    assert canonical_json_bytes({"z": None, "a": ["中文", True, 1]}) == (
        b'{"a":["\xe4\xb8\xad\xe6\x96\x87",true,1],"z":null}'
    )
    snapshot_digest = snapshot_sha256(_snapshot(1))
    assert snapshot_digest == "ffe4f79a6b9535daaf875201e41847ed9e70ab32f6751487360c6b2213de8c20"

    record = _issue(
        keyring=_ring("audit-old"),
        revision=1,
        action="created",
        before_sha=None,
        after_sha=snapshot_digest,
        previous_mac=None,
    )
    assert record["format"] == AUDIT_CHAIN_FORMAT
    assert record["key_id"] == "audit-old"
    assert record["audit_mac"] == "32d09b3591f05562b3708edc300928c9f37578839533d12505114b20a6a2d677"
    verify_audit_record(record, _ring("audit-old"))


def test_v1_float_rendering_is_explicitly_locked_for_python_consumers():
    assert canonical_json_bytes({
        "negative_zero": -0.0,
        "one": 1.0,
        "small": 1e-7,
    }) == b'{"negative_zero":-0.0,"one":1.0,"small":1e-07}'


def test_timestamp_normalization_is_fixed_width_utc_and_accepts_legacy_naive_utc():
    assert canonical_utc_timestamp(datetime(2026, 8, 31, 12, 0, 0, 123)) == (
        "2026-08-31T12:00:00.000123Z"
    )
    aware = datetime(2026, 8, 31, 5, 0, tzinfo=timezone(timedelta(hours=-7)))
    assert canonical_utc_timestamp(aware) == T0


@pytest.mark.parametrize(
    ("active_key_id", "keys"),
    [
        ("missing", {"audit-old": OLD_KEY}),
        ("bad key id", {"bad key id": OLD_KEY}),
        ("audit-old", {}),
        ("audit-old", {"audit-old": b"short"}),
        ("audit-old", {"audit-old": bytearray(OLD_KEY)}),
    ],
)
def test_keyring_rejects_missing_weak_or_ambiguous_keys(active_key_id, keys):
    with pytest.raises(CompanyProfileAuditIntegrityError):
        AuditKeyring(active_key_id=active_key_id, keys=keys)


@pytest.mark.parametrize(
    "bad_value",
    [
        {1: "not-a-string-key"},
        {"tuple": (1, 2)},
        {"nan": float("nan")},
        {"infinity": float("inf")},
        {"bytes": b"not-json"},
    ],
)
def test_canonical_json_rejects_non_strict_python_values(bad_value):
    with pytest.raises(CompanyProfileAuditIntegrityError):
        canonical_json_bytes(bad_value)


def test_canonical_json_rejects_cycles():
    cyclic: list = []
    cyclic.append(cyclic)
    with pytest.raises(CompanyProfileAuditIntegrityError):
        canonical_json_bytes(cyclic)


def test_snapshot_digest_covers_relational_and_domain_fields():
    original = _snapshot(1)
    changed_status = {**original, "status": "archived"}
    changed_name = {**original, "name": "另一企业"}
    assert snapshot_sha256(original) != snapshot_sha256(changed_status)
    assert snapshot_sha256(original) != snapshot_sha256(changed_name)
    with pytest.raises(CompanyProfileAuditIntegrityError):
        snapshot_sha256([original])


def test_native_chain_crosses_key_rotation_without_resigning_history():
    rotated, records, hashes = _native_chain()
    verify_audit_chain(
        records,
        keyring=rotated,
        expected_profile_id=PROFILE_ID,
        expected_head=_head(records[-1], hashes[-1]),
    )
    assert records[0]["key_id"] == "audit-old"
    assert records[1]["key_id"] == records[2]["key_id"] == "audit-new"

    with pytest.raises(CompanyProfileAuditIntegrityError, match="未知 key id"):
        verify_audit_chain(
            records,
            keyring=_ring("audit-new", include_old=False),
            expected_profile_id=PROFILE_ID,
            expected_head=_head(records[-1], hashes[-1]),
        )


def test_observed_chain_binds_actual_snapshots_domain_semantics_and_current_row():
    ring, records, hashes = _native_chain()
    snapshots = [_snapshot(1), _snapshot(2), _snapshot(3)]
    observations = [
        AuditSnapshotObservation(records[0], None, snapshots[0]),
        AuditSnapshotObservation(records[1], snapshots[0], snapshots[1]),
        AuditSnapshotObservation(records[2], snapshots[1], snapshots[2]),
    ]
    verify_observed_audit_chain(
        observations,
        keyring=ring,
        expected_profile_id=PROFILE_ID,
        expected_head=_head(records[-1], hashes[-1]),
        expected_current_snapshot=snapshots[-1],
        validate_snapshot=_validate_test_snapshot,
    )

    with pytest.raises(CompanyProfileAuditIntegrityError, match="当前企业档案"):
        verify_observed_audit_chain(
            observations,
            keyring=ring,
            expected_profile_id=PROFILE_ID,
            expected_head=_head(records[-1], hashes[-1]),
            expected_current_snapshot={**snapshots[-1], "name": "被改写的当前行"},
            validate_snapshot=_validate_test_snapshot,
        )


def test_crypto_only_chain_cannot_replace_observed_action_and_status_validation():
    ring = _ring("audit-old")
    first_snapshot = _snapshot(1)
    active_second_snapshot = _snapshot(2, status="active")
    first = _issue(
        keyring=ring, revision=1, action="created",
        before_sha=None, after_sha=snapshot_sha256(first_snapshot), previous_mac=None,
    )
    signed_but_semantically_wrong = _issue(
        keyring=ring,
        revision=2,
        action="archived",
        before_sha=snapshot_sha256(first_snapshot),
        after_sha=snapshot_sha256(active_second_snapshot),
        previous_mac=first["audit_mac"],
    )
    expected_head = _head(
        signed_but_semantically_wrong, snapshot_sha256(active_second_snapshot),
    )
    verify_audit_chain(
        [first, signed_but_semantically_wrong],
        keyring=ring,
        expected_profile_id=PROFILE_ID,
        expected_head=expected_head,
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="action"):
        verify_observed_audit_chain(
            [
                AuditSnapshotObservation(first, None, first_snapshot),
                AuditSnapshotObservation(
                    signed_but_semantically_wrong,
                    first_snapshot,
                    active_second_snapshot,
                ),
            ],
            keyring=ring,
            expected_profile_id=PROFILE_ID,
            expected_head=expected_head,
            expected_current_snapshot=active_second_snapshot,
            validate_snapshot=_validate_test_snapshot,
        )


def test_unknown_historical_key_never_falls_back_to_jwt_or_active_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "must-not-be-used-for-audit-verification")
    old_ring = _ring("audit-old")
    digest = snapshot_sha256(_snapshot(1))
    record = _issue(
        keyring=old_ring, revision=1, action="created",
        before_sha=None, after_sha=digest, previous_mac=None,
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="未知 key id"):
        verify_audit_record(record, _ring("audit-new", include_old=False))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "unsupported/v9"),
        ("integrity_version", 2),
        ("algorithm", "sha256"),
        ("key_id", "unknown-key"),
        ("profile_id", OTHER_PROFILE_ID),
        ("audit_id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("revision", 2),
        ("action", "updated"),
        ("actor_id", "admin-2"),
        ("change_reason", "伪造原因"),
        ("created_at", T1),
        ("content_sha256", "1" * 64),
        ("before_snapshot_sha256", "2" * 64),
        ("after_snapshot_sha256", "3" * 64),
        ("previous_audit_mac", "4" * 64),
        ("chain_start", CHAIN_START_LEGACY_ANCHOR),
        ("audit_mac", "5" * 64),
    ],
)
def test_mutating_any_persisted_audit_field_fails_closed(field, value):
    digest = snapshot_sha256(_snapshot(1))
    record = _issue(
        keyring=_ring("audit-old"), revision=1, action="created",
        before_sha=None, after_sha=digest, previous_mac=None,
    )
    damaged = {**record, field: value}
    with pytest.raises(CompanyProfileAuditIntegrityError):
        verify_audit_record(damaged, _ring("audit-old"))


def test_record_shape_timestamp_actor_and_reason_are_strict():
    digest = snapshot_sha256(_snapshot(1))
    record = _issue(
        keyring=_ring("audit-old"), revision=1, action="created",
        before_sha=None, after_sha=digest, previous_mac=None,
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="字段形状"):
        verify_audit_record({**record, "unexpected": True}, _ring("audit-old"))
    missing = dict(record)
    missing.pop("audit_id")
    with pytest.raises(CompanyProfileAuditIntegrityError, match="字段形状"):
        verify_audit_record(missing, _ring("audit-old"))

    common = dict(
        keyring=_ring("audit-old"), profile_id=PROFILE_ID, audit_id=AUDIT_IDS[0],
        revision=1, action="created", content_sha256="1" * 64,
        before_snapshot_sha256=None, after_snapshot_sha256=digest,
        previous_audit_mac=None, chain_start=CHAIN_START_GENESIS,
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="actor_id"):
        issue_audit_record(
            **common, actor_id=" admin-1", change_reason="创建", created_at=T0,
        )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="change_reason"):
        issue_audit_record(
            **common, actor_id="admin-1", change_reason="", created_at=T0,
        )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="created_at"):
        issue_audit_record(
            **common, actor_id="admin-1", change_reason="创建",
            created_at="2026-08-31T12:00:00Z",
        )


@pytest.mark.parametrize("variant", ["truncate", "reorder", "duplicate", "cross_profile"])
def test_chain_rejects_truncation_reorder_insertion_and_cross_profile_copy(variant):
    ring, records, hashes = _native_chain()
    expected_head = _head(records[-1], hashes[-1])
    if variant == "truncate":
        damaged = records[:-1]
    elif variant == "reorder":
        damaged = [records[1], records[0], records[2]]
    elif variant == "duplicate":
        damaged = [records[0], records[1], records[1], records[2]]
    else:
        copied = _issue(
            keyring=ring,
            profile_id=OTHER_PROFILE_ID,
            revision=2,
            action="updated",
            before_sha=hashes[0],
            after_sha=hashes[1],
            previous_mac=records[0]["audit_mac"],
        )
        damaged = [records[0], copied, records[2]]
    with pytest.raises(CompanyProfileAuditIntegrityError):
        verify_audit_chain(
            damaged,
            keyring=ring,
            expected_profile_id=PROFILE_ID,
            expected_head=expected_head,
        )


def test_chain_rejects_creation_after_genesis_time_regression_and_post_archive_write():
    ring, records, hashes = _native_chain()
    with pytest.raises(CompanyProfileAuditIntegrityError, match="不得是 created"):
        _issue(
            keyring=ring,
            revision=2,
            action="created",
            before_sha=hashes[0],
            after_sha=hashes[1],
            previous_mac=records[0]["audit_mac"],
        )

    regressed = _issue(
        keyring=ring,
        revision=2,
        action="updated",
        before_sha=hashes[0],
        after_sha=hashes[1],
        previous_mac=records[0]["audit_mac"],
        created_at="2026-08-31T11:59:00.000000Z",
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="时间倒退"):
        verify_audit_chain(
            [records[0], regressed], keyring=ring,
            expected_profile_id=PROFILE_ID,
            expected_head=_head(regressed, hashes[1]),
        )


def test_chain_rejects_a_validly_signed_duplicate_audit_id():
    ring, records, hashes = _native_chain()
    duplicate_id_third = _issue(
        keyring=ring,
        revision=3,
        action="updated",
        before_sha=hashes[1],
        after_sha=hashes[2],
        previous_mac=records[1]["audit_mac"],
        audit_id=records[1]["audit_id"],
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="audit_id 重复"):
        verify_audit_chain(
            [records[0], records[1], duplicate_id_third], keyring=ring,
            expected_profile_id=PROFILE_ID,
            expected_head=_head(duplicate_id_third, hashes[2]),
        )

    archived_hash = snapshot_sha256(_snapshot(2, status="archived"))
    archived = _issue(
        keyring=ring,
        revision=2,
        action="archived",
        before_sha=hashes[0],
        after_sha=archived_hash,
        previous_mac=records[0]["audit_mac"],
    )
    after_archive = _issue(
        keyring=ring,
        revision=3,
        action="updated",
        before_sha=archived_hash,
        after_sha=hashes[2],
        previous_mac=archived["audit_mac"],
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="归档"):
        verify_audit_chain(
            [records[0], archived, after_archive], keyring=ring,
            expected_profile_id=PROFILE_ID,
            expected_head=_head(after_archive, hashes[2]),
        )


def test_legacy_anchor_supports_anchor_only_state_and_new_key_continuation():
    legacy_snapshots = [_snapshot(1), _snapshot(2)]
    legacy_rows = [
        {"revision": 1, "action": "created", "after_snapshot": legacy_snapshots[0]},
        {
            "revision": 2,
            "action": "updated",
            "before_snapshot": legacy_snapshots[0],
            "after_snapshot": legacy_snapshots[1],
        },
    ]
    terminal_sha = snapshot_sha256(legacy_snapshots[-1])
    old_ring = _ring("audit-old")
    anchor = issue_legacy_anchor(
        keyring=old_ring,
        profile_id=PROFILE_ID,
        legacy_cutover_revision=2,
        legacy_chain_sha256=legacy_history_sha256(legacy_rows),
        legacy_terminal_snapshot_sha256=terminal_sha,
        anchored_at=T1,
        migration_run_id="migration-20260831-001",
    )
    rotated = _ring("audit-new")
    verify_legacy_anchor(anchor, rotated)
    verify_legacy_anchor_observation(
        anchor,
        rotated,
        legacy_audits=legacy_rows,
        terminal_snapshot=legacy_snapshots[-1],
    )
    verify_audit_chain(
        [], keyring=rotated, expected_profile_id=PROFILE_ID,
        expected_head=AuditChainHead(
            profile_id=PROFILE_ID,
            revision=2,
            snapshot_sha256=terminal_sha,
            mac=anchor["anchor_mac"],
        ),
        legacy_anchor=anchor,
    )

    next_snapshot_sha = snapshot_sha256(_snapshot(3))
    third = _issue(
        keyring=rotated,
        revision=3,
        action="updated",
        before_sha=terminal_sha,
        after_sha=next_snapshot_sha,
        previous_mac=anchor["anchor_mac"],
        chain_start=CHAIN_START_LEGACY_ANCHOR,
    )
    verify_audit_chain(
        [third], keyring=rotated, expected_profile_id=PROFILE_ID,
        expected_head=_head(third, next_snapshot_sha), legacy_anchor=anchor,
    )
    assert anchor["key_id"] == "audit-old"
    assert third["key_id"] == "audit-new"
    verify_observed_audit_chain(
        [AuditSnapshotObservation(third, legacy_snapshots[-1], _snapshot(3))],
        keyring=rotated,
        expected_profile_id=PROFILE_ID,
        expected_head=_head(third, next_snapshot_sha),
        expected_current_snapshot=_snapshot(3),
        validate_snapshot=_validate_test_snapshot,
        legacy_anchor=anchor,
        legacy_audits=legacy_rows,
        legacy_terminal_snapshot=legacy_snapshots[-1],
    )

    with pytest.raises(CompanyProfileAuditIntegrityError, match="必须同时复验"):
        verify_observed_audit_chain(
            [AuditSnapshotObservation(third, legacy_snapshots[-1], _snapshot(3))],
            keyring=rotated,
            expected_profile_id=PROFILE_ID,
            expected_head=_head(third, next_snapshot_sha),
            expected_current_snapshot=_snapshot(3),
            validate_snapshot=_validate_test_snapshot,
            legacy_anchor=anchor,
        )


def test_legacy_anchor_and_first_link_mutations_fail_closed():
    terminal_sha = snapshot_sha256(_snapshot(2))
    ring = _ring("audit-old")
    anchor = issue_legacy_anchor(
        keyring=ring,
        profile_id=PROFILE_ID,
        legacy_cutover_revision=2,
        legacy_chain_sha256=legacy_history_sha256([{"revision": 1}, {"revision": 2}]),
        legacy_terminal_snapshot_sha256=terminal_sha,
        anchored_at=T1,
        migration_run_id="migration-1",
    )
    damaged_anchor = {**anchor, "legacy_cutover_revision": 1}
    with pytest.raises(CompanyProfileAuditIntegrityError):
        verify_legacy_anchor(damaged_anchor, ring)

    next_sha = snapshot_sha256(_snapshot(3))
    wrong_link = _issue(
        keyring=ring,
        revision=3,
        action="updated",
        before_sha=terminal_sha,
        after_sha=next_sha,
        previous_mac="0" * 64,
        chain_start=CHAIN_START_LEGACY_ANCHOR,
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="前序 MAC"):
        verify_audit_chain(
            [wrong_link], keyring=ring, expected_profile_id=PROFILE_ID,
            expected_head=_head(wrong_link, next_sha), legacy_anchor=anchor,
        )


def test_legacy_anchor_observation_rejects_changed_history_and_terminal_snapshot():
    legacy_rows = [{"revision": 1}, {"revision": 2}]
    terminal = _snapshot(2)
    ring = _ring("audit-old")
    anchor = issue_legacy_anchor(
        keyring=ring,
        profile_id=PROFILE_ID,
        legacy_cutover_revision=2,
        legacy_chain_sha256=legacy_history_sha256(legacy_rows),
        legacy_terminal_snapshot_sha256=snapshot_sha256(terminal),
        anchored_at=T1,
        migration_run_id="migration-observation-1",
    )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="旧审计历史"):
        verify_legacy_anchor_observation(
            anchor,
            ring,
            legacy_audits=[{"revision": 1}, {"revision": 2, "forged": True}],
            terminal_snapshot=terminal,
        )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="终态快照"):
        verify_legacy_anchor_observation(
            anchor,
            ring,
            legacy_audits=legacy_rows,
            terminal_snapshot={**terminal, "name": "被改写"},
        )


def test_legacy_history_digest_changes_for_content_or_order_and_rejects_empty_history():
    first = [{"revision": 1}, {"revision": 2}]
    changed = [{"revision": 1}, {"revision": 2, "actor_id": "forged"}]
    assert legacy_history_sha256(first) != legacy_history_sha256(changed)
    assert legacy_history_sha256(first) != legacy_history_sha256(list(reversed(first)))
    with pytest.raises(CompanyProfileAuditIntegrityError):
        legacy_history_sha256([])


def test_invalid_or_wrong_expected_head_fails_closed():
    ring, records, hashes = _native_chain()
    with pytest.raises(CompanyProfileAuditIntegrityError, match="head revision"):
        verify_audit_chain(
            records, keyring=ring, expected_profile_id=PROFILE_ID,
            expected_head=AuditChainHead(
                profile_id=PROFILE_ID,
                revision=2,
                snapshot_sha256=hashes[-1],
                mac=records[-1]["audit_mac"],
            ),
        )
    with pytest.raises(CompanyProfileAuditIntegrityError, match="head MAC"):
        verify_audit_chain(
            records, keyring=ring, expected_profile_id=PROFILE_ID,
            expected_head=AuditChainHead(
                profile_id=PROFILE_ID,
                revision=3,
                snapshot_sha256=hashes[-1],
                mac="0" * 64,
            ),
        )


def test_copying_a_record_between_profiles_cannot_be_hidden_by_changing_expected_id():
    ring, records, hashes = _native_chain()
    copied = copy.deepcopy(records)
    with pytest.raises(CompanyProfileAuditIntegrityError, match="profile_id"):
        verify_audit_chain(
            copied, keyring=ring, expected_profile_id=OTHER_PROFILE_ID,
            expected_head=AuditChainHead(
                profile_id=OTHER_PROFILE_ID,
                revision=3,
                snapshot_sha256=hashes[-1],
                mac=records[-1]["audit_mac"],
            ),
        )
