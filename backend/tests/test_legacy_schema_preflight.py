"""Deterministic contracts for frozen legacy-schema preflight."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.legacy_schema_catalog import (  # noqa: E402
    CATALOG_FORMAT,
    LegacySchemaCatalog,
    canonical_json_bytes,
    catalog_sha256,
)
from core.legacy_schema_preflight import (  # noqa: E402
    ALEMBIC_MANIFEST_ID,
    APPLICATION_TABLES,
    BASE_MANIFEST_ID,
    DEMO_MANIFEST_ID,
    DOCKER_MANIFEST_ID,
    HEAD_REVISION,
    LANGGRAPH_MANIFEST_ID,
    LANGGRAPH_PROVIDER_MIGRATIONS,
    MANIFEST_FORMAT,
    PreflightStatus,
    UUID_OSSP_MANIFEST_ID,
    FrozenCatalogManifest,
    LegacyPreflightError,
    classify_legacy_schema,
    diff_catalog,
    load_frozen_manifests,
    project_catalog_component,
)
from scripts import preflight_legacy_schema as preflight_cli  # noqa: E402


EXPECTED_MANIFESTS = {
    BASE_MANIFEST_ID,
    DOCKER_MANIFEST_ID,
    DEMO_MANIFEST_ID,
    LANGGRAPH_MANIFEST_ID,
    ALEMBIC_MANIFEST_ID,
    UUID_OSSP_MANIFEST_ID,
}


def _observation(snapshot):
    snapshot = copy.deepcopy(snapshot)
    snapshot.setdefault("format", CATALOG_FORMAT)
    snapshot.setdefault("schemas", [{
        "schema_name": "public", "owner_class": "pg_database_owner",
    }])
    for section in (
        "relations", "columns", "constraints", "indexes", "triggers", "policies",
        "rules", "relation_privileges", "column_privileges", "extensions",
        "routines", "routine_privileges", "default_privileges", "types",
        "sequences", "event_triggers", "dependency_edges",
    ):
        snapshot.setdefault(section, [])
    snapshot.setdefault("schema_privileges", [{
        "schema_name": "public", "grantee_class": "PUBLIC",
        "grantor_class": "owner", "privilege_type": "USAGE",
        "is_grantable": False,
    }])
    return LegacySchemaCatalog(
        target_identity={
            "database_name": "legacy_test",
            "database_oid": 16384,
            "current_user": "preflight_reader",
            "server_address": "127.0.0.1",
            "server_port": 5432,
            "server_version": "15.18",
            "server_version_num": "150018",
            "server_encoding": "UTF8",
            "lc_collate": "C",
            "lc_ctype": "C",
        },
        snapshot=snapshot,
        snapshot_sha256=catalog_sha256(snapshot),
    )


def _full_snapshot(*catalogs):
    snapshot = {
        "format": CATALOG_FORMAT,
        "schemas": [{"schema_name": "public", "owner_class": "pg_database_owner"}],
        "relations": [],
        "columns": [],
        "constraints": [],
        "indexes": [],
        "triggers": [],
        "policies": [],
        "rules": [],
        "schema_privileges": [{
            "schema_name": "public", "grantee_class": "PUBLIC",
            "grantor_class": "owner", "privilege_type": "USAGE",
            "is_grantable": False,
        }],
        "relation_privileges": [],
        "column_privileges": [],
        "extensions": [],
        "routines": [],
        "routine_privileges": [],
        "default_privileges": [],
        "types": [],
        "sequences": [],
        "event_triggers": [],
        "dependency_edges": [],
    }
    for catalog in catalogs:
        for section, rows in catalog.items():
            if section != "format":
                snapshot[section].extend(copy.deepcopy(rows))
    for section in snapshot:
        if isinstance(snapshot[section], list):
            snapshot[section].sort(key=canonical_json_bytes)
    return snapshot


def test_all_frozen_manifests_are_digest_verified_and_ascii_transport_safe():
    manifests = load_frozen_manifests()
    assert set(manifests) == EXPECTED_MANIFESTS
    assert manifests[BASE_MANIFEST_ID].classification == "adoptable"
    assert manifests[DOCKER_MANIFEST_ID].classification == "known_incompatible"
    manifest_dir = BACKEND / "migrations" / "legacy_fingerprints"
    for path in manifest_dir.glob("*.json"):
        raw = path.read_bytes()
        assert raw.isascii()
        payload = json.loads(raw)
        assert payload["format"] == MANIFEST_FORMAT
        assert payload["catalog_sha256"] == catalog_sha256(payload["catalog"])


def test_base_full_is_the_only_exact_adoptable_profile():
    manifests = load_frozen_manifests()
    snapshot = _full_snapshot(manifests[BASE_MANIFEST_ID].catalog)
    report = classify_legacy_schema(_observation(snapshot), manifests)
    assert report.status is PreflightStatus.EXACT_ADOPTABLE
    assert report.profile_id == BASE_MANIFEST_ID
    assert report.may_enter_adoption_approval is True
    assert report.differences == ()
    assert report.target_identity["database_name"] == "legacy_test"
    assert len(report.preflight_sha256) == 64

    other_role = _observation(snapshot)
    other_role.target_identity["current_user"] = "maintenance_writer"
    same_target = classify_legacy_schema(other_role, manifests)
    assert same_target.preflight_sha256 == report.preflight_sha256


def test_empty_and_already_managed_are_not_adoption_candidates():
    manifests = load_frozen_manifests()
    empty = classify_legacy_schema(_observation({}), manifests)
    assert empty.status is PreflightStatus.UPGRADE_REQUIRED
    assert empty.may_enter_adoption_approval is False

    versioned = _full_snapshot(
        manifests[BASE_MANIFEST_ID].catalog,
        manifests[ALEMBIC_MANIFEST_ID].catalog,
    )
    managed = classify_legacy_schema(
        _observation(versioned),
        manifests,
        alembic_versions=(HEAD_REVISION,),
    )
    assert managed.status is PreflightStatus.ALREADY_MANAGED
    assert managed.may_enter_adoption_approval is False


def test_docker_known_profile_is_recognized_but_never_adoptable():
    manifests = load_frozen_manifests()
    base_remaining = project_catalog_component(
        manifests[BASE_MANIFEST_ID].catalog,
        relation_names=APPLICATION_TABLES - manifests[DOCKER_MANIFEST_ID].relation_names,
    )
    snapshot = _full_snapshot(
        manifests[DOCKER_MANIFEST_ID].catalog,
        base_remaining,
        manifests[DEMO_MANIFEST_ID].catalog,
        manifests[UUID_OSSP_MANIFEST_ID].catalog,
    )
    report = classify_legacy_schema(_observation(snapshot), manifests)
    assert report.status is PreflightStatus.KNOWN_INCOMPATIBLE
    assert report.profile_id == "docker_hybrid_v1"
    assert report.may_enter_adoption_approval is False


def test_langgraph_is_allowed_only_with_exact_structure_and_migration_rows():
    manifests = load_frozen_manifests()
    snapshot = _full_snapshot(manifests[LANGGRAPH_MANIFEST_ID].catalog)
    valid = classify_legacy_schema(
        _observation(snapshot),
        manifests,
        langgraph_migrations=LANGGRAPH_PROVIDER_MIGRATIONS,
    )
    assert valid.status is PreflightStatus.UPGRADE_REQUIRED
    assert valid.unmanaged_packages == (LANGGRAPH_MANIFEST_ID,)

    partial = copy.deepcopy(snapshot)
    partial["relations"] = [
        row for row in partial["relations"]
        if row["relation_name"] != "checkpoint_writes"
    ]
    invalid = classify_legacy_schema(
        _observation(partial),
        manifests,
        langgraph_migrations=None,
    )
    assert invalid.status is PreflightStatus.SCHEMA_DRIFT
    assert any(item.issue == "missing_package_relations" for item in invalid.differences)


def test_single_column_drift_has_a_precise_path_and_invalidates_version_state():
    manifests = load_frozen_manifests()
    snapshot = _full_snapshot(manifests[BASE_MANIFEST_ID].catalog)
    target = next(
        row for row in snapshot["columns"]
        if row["relation_name"] == "users" and row["column_name"] == "is_active"
    )
    target["default_sql"] = "true"
    report = classify_legacy_schema(_observation(snapshot), manifests)
    assert report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(
        item.issue == "changed"
        and item.path.startswith("columns[")
        and item.path.endswith(".default_sql")
        for item in report.differences
    )

    versioned = _full_snapshot(snapshot, manifests[ALEMBIC_MANIFEST_ID].catalog)
    invalid = classify_legacy_schema(
        _observation(versioned), manifests, alembic_versions=(HEAD_REVISION,),
    )
    assert invalid.status is PreflightStatus.VERSION_STATE_INVALID


def test_rls_policy_and_event_trigger_security_semantics_fail_closed():
    manifests = load_frozen_manifests()
    snapshot = _full_snapshot(manifests[BASE_MANIFEST_ID].catalog)
    users = next(
        row for row in snapshot["relations"]
        if row["relation_name"] == "users" and row["relation_kind"] == "r"
    )
    users["row_security"] = True
    snapshot["policies"].append({
        "schema_name": "public",
        "relation_name": "users",
        "policy_name": "reader",
        "command": "r",
        "permissive": True,
        "roles": ["PUBLIC"],
        "using_expression": "true",
        "check_expression": None,
    })
    snapshot["event_triggers"].append({
        "event_trigger_name": "guard_ddl",
        "event": "ddl_command_start",
        "enabled": "O",
        "tags": None,
        "function_schema": "public",
        "function_name": "guard_ddl",
        "function_arguments": "",
    })

    report = classify_legacy_schema(_observation(snapshot), manifests)
    assert report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(item.path.endswith(".row_security") for item in report.differences)
    assert any(item.path.startswith("policies[") for item in report.differences)
    assert any(item.issue == "unexpected_event_trigger" for item in report.differences)


def test_rule_acl_owner_type_and_dependency_security_gaps_fail_closed():
    manifests = load_frozen_manifests()
    snapshot = _full_snapshot(manifests[BASE_MANIFEST_ID].catalog)
    users = next(row for row in snapshot["relations"] if row["relation_name"] == "users")
    users["owner_class"] = "other"
    snapshot["rules"].append({
        "schema_name": "public", "relation_name": "users",
        "rule_name": "block_insert", "event_type": "3", "enabled": "O",
        "is_instead": True,
        "definition": "CREATE RULE block_insert AS ON INSERT TO public.users DO INSTEAD NOTHING",
    })
    snapshot["relation_privileges"].append({
        "schema_name": "public", "relation_name": "documents",
        "grantee_class": "PUBLIC", "grantor_class": "owner",
        "privilege_type": "SELECT", "is_grantable": False,
    })
    snapshot["types"].append({
        "schema_name": "public", "type_name": "risk_level",
        "type_kind": "e", "category": "E", "type_sql": "risk_level",
        "owner_class": "database_owner", "not_null": False,
        "default_sql": None, "domain_base_type": None,
        "collation_schema": None, "collation_name": None,
        "enum_labels": ["low", "high"], "range_subtype": None,
        "composite_attributes": [],
    })
    snapshot["dependency_edges"].append({
        "edge_kind": "routine_relation", "source_schema": "public",
        "source_relation": None, "source_routine": "read_users",
        "source_arguments": "", "source_routine_kind": "f", "source_rule": None,
        "target_schema": "public", "target_relation": "users",
        "target_column_number": 0, "target_column": None, "dependency_kind": "n",
    })

    report = classify_legacy_schema(_observation(snapshot), manifests)
    assert report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(item.path.endswith(".owner_class") for item in report.differences)
    assert any(item.path.startswith("rules[") for item in report.differences)
    assert any(item.path.startswith("relation_privileges[") for item in report.differences)
    assert any(item.issue == "unexpected_standalone_type" for item in report.differences)
    assert any(item.issue == "unexpected_dependency" for item in report.differences)
    rendered_report = json.dumps(report.to_dict(), sort_keys=True)
    assert "CREATE RULE" not in rendered_report
    assert '"redacted": true' in rendered_report


def test_managed_unmanaged_fk_and_trigger_edges_are_explicitly_rejected():
    manifests = load_frozen_manifests()
    base_with_demo = _full_snapshot(
        manifests[BASE_MANIFEST_ID].catalog,
        manifests[DEMO_MANIFEST_ID].catalog,
    )
    base_with_demo["constraints"].append({
        "schema_name": "public", "relation_name": "users",
        "constraint_name": "users_restaurant_fk", "constraint_type": "f",
        "target_schema": "public", "target_relation": "restaurants",
    })
    fk_report = classify_legacy_schema(_observation(base_with_demo), manifests)
    assert fk_report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(
        item.path == "cross_boundary.constraint.users_restaurant_fk"
        and item.issue == "managed_unmanaged_dependency"
        for item in fk_report.differences
    )

    docker_with_demo = _full_snapshot(
        manifests[DOCKER_MANIFEST_ID].catalog,
        manifests[DEMO_MANIFEST_ID].catalog,
        manifests[UUID_OSSP_MANIFEST_ID].catalog,
    )
    docker_routine = next(iter(manifests[DOCKER_MANIFEST_ID].routine_identities))
    routine_head = docker_routine.removesuffix(":f")
    qualified_name, arguments = routine_head[:-1].split("(", 1)
    function_schema, function_name = qualified_name.split(".", 1)
    docker_with_demo["triggers"].append({
        "schema_name": "public", "relation_name": "restaurants",
        "trigger_name": "restaurant_touch", "function_schema": function_schema,
        "function_name": function_name, "function_arguments": arguments,
    })
    trigger_report = classify_legacy_schema(_observation(docker_with_demo), manifests)
    assert trigger_report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(
        item.path == "cross_boundary.trigger.restaurant_touch"
        and item.issue == "managed_unmanaged_dependency"
        for item in trigger_report.differences
    )


def test_manifest_digest_mismatch_fails_closed(tmp_path):
    source_dir = BACKEND / "migrations" / "legacy_fingerprints"
    for source in source_dir.glob("*.json"):
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload["manifest_id"] == BASE_MANIFEST_ID:
            payload["catalog"]["columns"][0]["column_name"] = "tampered"
        (tmp_path / source.name).write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8",
        )
    with pytest.raises(LegacyPreflightError, match="digest mismatch"):
        load_frozen_manifests(tmp_path)


def test_manifest_id_cannot_be_silently_rebound_to_a_new_valid_digest(tmp_path):
    source_dir = BACKEND / "migrations" / "legacy_fingerprints"
    for source in source_dir.glob("*.json"):
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload["manifest_id"] == BASE_MANIFEST_ID:
            payload["catalog"]["columns"][0]["column_name"] = "tampered"
            payload["catalog_sha256"] = catalog_sha256(payload["catalog"])
        (tmp_path / source.name).write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8",
        )
    with pytest.raises(LegacyPreflightError, match="frozen digest mismatch"):
        load_frozen_manifests(tmp_path)


def test_manifest_declarations_must_exactly_describe_the_catalog(tmp_path):
    source_dir = BACKEND / "migrations" / "legacy_fingerprints"
    for source in source_dir.glob("*.json"):
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload["manifest_id"] == BASE_MANIFEST_ID:
            payload["relation_names"].remove("users")
        (tmp_path / source.name).write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8",
        )
    with pytest.raises(LegacyPreflightError, match="relation declarations"):
        load_frozen_manifests(tmp_path)


def test_cli_reports_permission_denied_without_echoing_connection_details(
    monkeypatch, capsys,
):
    secret_url = "postgresql+psycopg2://reader:do-not-print@db.invalid/legacy"
    permission_error = RuntimeError(f"permission denied while using {secret_url}")
    permission_error.orig = SimpleNamespace(pgcode="42501")

    monkeypatch.setattr(
        preflight_cli,
        "resolve_database_urls",
        lambda: SimpleNamespace(sqlalchemy_url=secret_url),
    )

    def deny_connection(*args, **kwargs):
        raise permission_error

    monkeypatch.setattr(preflight_cli, "create_engine", deny_connection)
    assert preflight_cli.main([]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert secret_url not in captured.err
    payload = json.loads(captured.err)
    assert payload == {
        "status": "preflight_error",
        "error_code": "legacy_preflight_permission_denied",
        "error_type": "RuntimeError",
    }


def test_diff_catalog_rejects_duplicate_identity_rows():
    manifests = load_frozen_manifests()
    base = copy.deepcopy(manifests[BASE_MANIFEST_ID].catalog)
    base["relations"].append(copy.deepcopy(base["relations"][0]))
    with pytest.raises(LegacyPreflightError, match="duplicate identities"):
        diff_catalog(manifests[BASE_MANIFEST_ID].catalog, base)
