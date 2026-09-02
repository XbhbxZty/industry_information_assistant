"""Contract-only tests for controlled legacy-schema adoption approval data."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.legacy_schema_adoption import (  # noqa: E402
    ADOPTABLE_PROFILE_ID,
    APPROVAL_FORMAT,
    CONFIRMATION_PHRASE,
    TRUSTED_TARGET_POLICY_FORMAT,
    AdoptionValidationCode,
    LegacySchemaAdoptionError,
    compute_database_identity_sha256,
    compute_server_identity_sha256,
    load_adoption_approval,
    load_trusted_target_policy,
    serialize_adoption_approval,
    serialize_trusted_target_policy,
    trusted_target_policy_sha256,
    validate_adoption_contract,
    validate_approved_preflight,
    validate_adoption_approval,
    verify_observed_target,
)
from core.legacy_schema_preflight import (  # noqa: E402
    BASE_MANIFEST_ID,
    LegacyPreflightReport,
    PreflightStatus,
)


_POLICY_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_POLICY_ID = "22222222-2222-4222-8222-222222222222"
_APPROVAL_ID = "33333333-3333-4333-8333-333333333333"
_OTHER_DIGEST = "b" * 64
_SERVER_DIGEST = compute_server_identity_sha256(
    system_identifier="7528870134234189765",
    server_address="127.0.0.1",
    server_port=5432,
    server_version_num="150018",
)
_DATABASE_DIGEST = compute_database_identity_sha256(
    database_name="industry_assistant",
    database_oid=16384,
    database_owner="postgres",
)


def _policy(**overrides):
    value = {
        "format": TRUSTED_TARGET_POLICY_FORMAT,
        "policy_id": _POLICY_ID,
        "server_identity_sha256": _SERVER_DIGEST,
        "database_identity_sha256": _DATABASE_DIGEST,
        "environment_id": "production",
    }
    value.update(overrides)
    return value


def _approval(**overrides):
    policy = load_trusted_target_policy(_json(_policy()))
    value = {
        "format": APPROVAL_FORMAT,
        "approval_id": _APPROVAL_ID,
        "operator_reference": "admin:42",
        "profile_id": ADOPTABLE_PROFILE_ID,
        "target_policy_id": _POLICY_ID,
        "expected_target_policy_sha256": trusted_target_policy_sha256(policy),
        "expected_preflight_sha256": _OTHER_DIGEST,
        "operator_attested_backup_reference": "CHG-100: backup-asset-7",
        "operator_attested_maintenance_window_reference": "CHG-100: 2026-09-01T02:00Z",
        "confirmation_phrase": CONFIRMATION_PHRASE,
        "confirmed_at": "2026-09-01T02:00:00.000000Z",
        "expires_at": "2026-09-01T02:15:00.000000Z",
    }
    value.update(overrides)
    return value


def _json(value):
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _preflight(**overrides):
    values = {
        "status": PreflightStatus.EXACT_ADOPTABLE,
        "profile_id": BASE_MANIFEST_ID,
        "message": "exact base schema",
        "target_identity": {},
        "snapshot_sha256": "c" * 64,
        "preflight_sha256": _OTHER_DIGEST,
        "unmanaged_packages": (),
        "differences": (),
    }
    values.update(overrides)
    return LegacyPreflightReport(**values)


def test_strict_round_trip_keeps_policy_independent_from_approval():
    assert ADOPTABLE_PROFILE_ID == BASE_MANIFEST_ID
    policy = load_trusted_target_policy(_json(_policy()))
    approval = load_adoption_approval(_json(_approval()))

    policy_json = serialize_trusted_target_policy(policy)
    approval_json = serialize_adoption_approval(approval)
    assert "server_identity_sha256" in policy_json
    assert "server_identity_sha256" not in approval_json
    assert "database_identity_sha256" in policy_json
    assert "database_identity_sha256" not in approval_json
    assert "operator_reference" in approval_json
    assert "expected_target_policy_sha256" in approval_json
    assert "operator_attested_backup_reference" in approval_json
    assert "backup_verified" not in approval_json
    assert load_adoption_approval(approval_json) == approval
    assert load_trusted_target_policy(policy_json) == policy


@pytest.mark.parametrize("mutator", [
    lambda value: value | {"extra": True},
    lambda value: {key: item for key, item in value.items() if key != "target_policy_id"},
    lambda value: value | {"confirmed_at": "2026-09-01T02:00:00Z"},
    lambda value: value | {"profile_id": "base-full"},
    lambda value: value | {"confirmation_phrase": CONFIRMATION_PHRASE + "!"},
    lambda value: value | {"operator_attested_backup_reference": " verified-backup "},
])
def test_approval_rejects_shape_and_strict_value_mutations(mutator):
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        load_adoption_approval(_json(mutator(_approval())))
    assert raised.value.code in {
        AdoptionValidationCode.INVALID_SHAPE,
        AdoptionValidationCode.INVALID_TIMESTAMP,
        AdoptionValidationCode.CONFIRMATION_MISMATCH,
        AdoptionValidationCode.INVALID_VALUE,
    }


def test_loader_rejects_duplicate_keys_and_non_json_constants():
    duplicate = _json(_approval())[:-1] + ',"target_policy_id":"' + _OTHER_POLICY_ID + '"}'
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        load_adoption_approval(duplicate)
    assert raised.value.code == AdoptionValidationCode.INVALID_JSON

    with pytest.raises(LegacySchemaAdoptionError) as raised:
        load_trusted_target_policy('{"value":NaN}')
    assert raised.value.code == AdoptionValidationCode.INVALID_JSON


def test_expiry_future_and_ttl_use_supplied_server_clock_only():
    policy = load_trusted_target_policy(_json(_policy()))
    approval = load_adoption_approval(_json(_approval()))
    now = datetime(2026, 9, 1, 2, 10, tzinfo=timezone.utc)
    validate_adoption_approval(approval, policy, server_now=now)

    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_adoption_approval(approval, policy, server_now=now + timedelta(minutes=5))
    assert raised.value.code == AdoptionValidationCode.APPROVAL_EXPIRED

    future = load_adoption_approval(_json(_approval(
        confirmed_at="2026-09-01T02:11:00.000000Z",
        expires_at="2026-09-01T02:12:00.000000Z",
    )))
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_adoption_approval(future, policy, server_now=now)
    assert raised.value.code == AdoptionValidationCode.APPROVAL_FUTURE

    with pytest.raises(LegacySchemaAdoptionError) as raised:
        load_adoption_approval(_json(_approval(expires_at="2026-09-01T02:15:00.000001Z")))
    assert raised.value.code == AdoptionValidationCode.APPROVAL_TTL_EXCEEDED

    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_adoption_approval(
            approval,
            policy,
            server_now=datetime(2026, 9, 1, 10, 5, tzinfo=timezone(timedelta(hours=8))),
        )
    assert raised.value.code == AdoptionValidationCode.INVALID_TIMESTAMP


def test_policy_and_runtime_target_mismatch_fail_closed():
    policy = load_trusted_target_policy(_json(_policy()))
    other_approval = load_adoption_approval(_json(_approval(target_policy_id=_OTHER_POLICY_ID)))
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_adoption_approval(
            other_approval, policy,
            server_now=datetime(2026, 9, 1, 2, 5, tzinfo=timezone.utc),
        )
    assert raised.value.code == AdoptionValidationCode.TARGET_POLICY_MISMATCH

    with pytest.raises(LegacySchemaAdoptionError) as raised:
        verify_observed_target(
            policy, observed_server_identity_sha256=_OTHER_DIGEST,
            observed_database_identity_sha256=_DATABASE_DIGEST,
        )
    assert raised.value.code == AdoptionValidationCode.TARGET_UNVERIFIABLE

    changed_policy = load_trusted_target_policy(_json(_policy(environment_id="staging")))
    approval = load_adoption_approval(_json(_approval()))
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_adoption_approval(
            approval,
            changed_policy,
            server_now=datetime(2026, 9, 1, 2, 5, tzinfo=timezone.utc),
        )
    assert raised.value.code == AdoptionValidationCode.TARGET_POLICY_MISMATCH


def test_target_cannot_be_called_verified_from_approval_declaration_alone():
    approval = load_adoption_approval(_json(_approval()))
    policy = load_trusted_target_policy(_json(_policy()))
    validate_adoption_approval(
        approval, policy, server_now=datetime(2026, 9, 1, 2, 5, tzinfo=timezone.utc),
    )
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        verify_observed_target(
            policy, observed_server_identity_sha256="not-a-digest",
            observed_database_identity_sha256=_DATABASE_DIGEST,
        )
    assert raised.value.code == AdoptionValidationCode.INVALID_VALUE
    assert "operator_attested" in serialize_adoption_approval(approval)


def test_approved_preflight_requires_fresh_exact_report_and_digest():
    approval = load_adoption_approval(_json(_approval()))
    validate_approved_preflight(approval, _preflight())

    for report in (
        _preflight(status=PreflightStatus.SCHEMA_DRIFT),
        _preflight(profile_id=None),
        _preflight(preflight_sha256="c" * 64),
        _preflight(unmanaged_packages=("unexpected_package",)),
        _preflight(differences=(object(),)),
    ):
        with pytest.raises(LegacySchemaAdoptionError) as raised:
            validate_approved_preflight(approval, report)
        assert raised.value.code == AdoptionValidationCode.PREFLIGHT_MISMATCH


def test_approved_preflight_rejects_untyped_or_malformed_report():
    approval = load_adoption_approval(_json(_approval()))
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_approved_preflight(approval, object())
    assert raised.value.code == AdoptionValidationCode.INVALID_VALUE


def test_composite_contract_applies_approval_target_and_preflight_gates():
    approval = load_adoption_approval(_json(_approval()))
    policy = load_trusted_target_policy(_json(_policy()))
    validate_adoption_contract(
        approval,
        policy,
        _preflight(),
        server_now=datetime(2026, 9, 1, 2, 5, tzinfo=timezone.utc),
        observed_server_identity_sha256=_SERVER_DIGEST,
        observed_database_identity_sha256=_DATABASE_DIGEST,
    )

    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_adoption_contract(
            approval,
            policy,
            _preflight(),
            server_now=datetime(2026, 9, 1, 2, 5, tzinfo=timezone.utc),
            observed_server_identity_sha256=_SERVER_DIGEST,
            observed_database_identity_sha256=_OTHER_DIGEST,
        )
    assert raised.value.code == AdoptionValidationCode.TARGET_UNVERIFIABLE


def test_direct_dataclass_construction_is_revalidated_at_public_boundaries():
    approval = load_adoption_approval(_json(_approval()))
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        serialize_adoption_approval(replace(approval, operator_reference=" forged admin "))
    assert raised.value.code == AdoptionValidationCode.INVALID_VALUE

    policy = load_trusted_target_policy(_json(_policy()))
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        trusted_target_policy_sha256(replace(policy, server_identity_sha256="A" * 64))
    assert raised.value.code == AdoptionValidationCode.INVALID_VALUE

    with pytest.raises(LegacySchemaAdoptionError) as raised:
        validate_approved_preflight(approval, _preflight(preflight_sha256="not-a-digest"))
    assert raised.value.code == AdoptionValidationCode.INVALID_VALUE


def test_canonical_target_identity_changes_when_any_bound_value_changes():
    assert _SERVER_DIGEST == (
        "1c4b97df11b755f240a2b1a649c3ab727f820bc3965668e1476b79a73c78cbb7"
    )
    assert _DATABASE_DIGEST == (
        "93b79ab8151857b9017ff00d5824046ebc999f4b0985fc0635e97e08d8b2932d"
    )
    assert compute_database_identity_sha256(
        database_name="企业档案",
        database_oid=16384,
        database_owner="管理员",
    ) == "94911d2127a7bbc63b60ea75eb0d4b233852d81f421bdbaffcf01cf43d448020"
    assert compute_server_identity_sha256(
        system_identifier="7528870134234189765",
        server_address="127.0.0.1",
        server_port=5433,
        server_version_num="150018",
    ) != _SERVER_DIGEST
    assert compute_database_identity_sha256(
        database_name="industry_assistant_copy",
        database_oid=16384,
        database_owner="postgres",
    ) != _DATABASE_DIGEST


@pytest.mark.parametrize(
    ("function", "kwargs"),
    [
        (compute_server_identity_sha256, {
            "system_identifier": "not-numeric",
            "server_address": " 127.0.0.1",
            "server_port": True,
            "server_version_num": "15",
        }),
        (compute_database_identity_sha256, {
            "database_name": "industry_assistant",
            "database_oid": True,
            "database_owner": "postgres",
        }),
    ],
)
def test_canonical_target_identity_rejects_ambiguous_values(function, kwargs):
    with pytest.raises(LegacySchemaAdoptionError) as raised:
        function(**kwargs)
    assert raised.value.code == AdoptionValidationCode.INVALID_VALUE


def test_import_is_safe_and_does_not_load_sqlalchemy_or_alembic():
    code = (
        f"import sys; sys.path.insert(0, {str(APP)!r}); import core.legacy_schema_adoption; "
        "assert 'sqlalchemy' not in sys.modules; assert 'alembic' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
