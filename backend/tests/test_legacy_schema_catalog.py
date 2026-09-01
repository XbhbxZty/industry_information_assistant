"""Pure/fake-connection coverage for legacy PostgreSQL catalog capture."""
from __future__ import annotations

from decimal import Decimal
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.legacy_schema_catalog import (  # noqa: E402
    CATALOG_FORMAT,
    LegacySchemaCatalogError,
    canonical_json_bytes,
    capture_legacy_schema_catalog,
)


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self.value = scalar
        self.rows = [] if rows is None else rows

    def scalar_one(self):
        return self.value

    def mappings(self):
        return _Mappings(self.rows)


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.in_transaction = True
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        self.connection.in_transaction = False
        self.connection.closed_with = exc_type
        return False


class _Connection:
    def __init__(self, *, read_only="on", isolation="repeatable read", sections=None):
        self.read_only = read_only
        self.isolation = isolation
        self.sections = sections or {}
        self.statements = []
        self.in_transaction = False
        self.closed_with = None

    def begin(self):
        return _Transaction(self)

    def exec_driver_sql(self, statement):
        self.statements.append(statement)
        normalized = " ".join(statement.split())
        if normalized.startswith("SET TRANSACTION"):
            assert self.in_transaction
            return _Result()
        if normalized == "SHOW transaction_isolation":
            return _Result(scalar=self.isolation)
        if normalized == "SHOW transaction_read_only":
            return _Result(scalar=self.read_only)
        if normalized == "SHOW server_version":
            return _Result(scalar="16.3")
        if normalized == "SHOW server_version_num":
            return _Result(scalar="160003")
        if normalized == "SHOW server_encoding":
            return _Result(scalar="UTF8")
        if normalized == "SHOW lc_collate":
            return _Result(scalar="C")
        if normalized == "SHOW lc_ctype":
            return _Result(scalar="C")
        if "FROM pg_catalog.pg_database AS d" in statement:
            return _Result(rows=[{
                "database_name": "legacy_test",
                "database_oid": 16384,
                "current_user": "preflight_reader",
                "server_address": "127.0.0.1",
                "server_port": 5432,
            }])
        for section, marker in (
            ("schema_privileges", "CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl)"),
            ("relation_privileges", "CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl)"),
            ("column_privileges", "CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl)"),
            ("routine_privileges", "COALESCE(p.proacl, pg_catalog.acldefault"),
            ("default_privileges", "FROM pg_catalog.pg_default_acl AS d"),
            ("dependency_edges", "SELECT 'routine_relation' AS edge_kind"),
            ("rules", "FROM pg_catalog.pg_rewrite AS r"),
            ("types", "FROM pg_catalog.pg_type AS t"),
            ("schemas", "FROM pg_catalog.pg_namespace AS n"),
            ("relations", "FROM pg_catalog.pg_class AS c"),
            ("columns", "FROM pg_catalog.pg_attribute AS a"),
            ("constraints", "FROM pg_catalog.pg_constraint AS con"),
            ("indexes", "FROM pg_catalog.pg_index AS i"),
            ("triggers", "FROM pg_catalog.pg_trigger AS t"),
            ("policies", "FROM pg_catalog.pg_policy AS pol"),
            ("extensions", "FROM pg_catalog.pg_extension AS e"),
            ("routines", "FROM pg_catalog.pg_proc AS p"),
            ("sequences", "FROM pg_catalog.pg_sequence AS s"),
            ("event_triggers", "FROM pg_catalog.pg_event_trigger AS evt"),
        ):
            if marker in statement:
                return _Result(rows=self.sections.get(section, []))
        raise AssertionError(f"unexpected SQL: {statement}")


def _connection(**kwargs):
    sections = {
        "schemas": [
            {"schema_name": "public", "owner_class": "pg_database_owner"},
            {"schema_name": "app", "owner_class": "database_owner"},
        ],
        "relations": [
            {"schema_name": "public", "relation_name": "z_table", "relation_kind": "r", "persistence": "p", "is_partition": False, "row_security": True, "force_row_security": False, "owner_class": "database_owner"},
            {"schema_name": "public", "relation_name": "a_view", "relation_kind": "v", "persistence": "p", "is_partition": False, "row_security": False, "force_row_security": False, "owner_class": "database_owner"},
            {"schema_name": "app", "relation_name": "id_seq", "relation_kind": "S", "persistence": "p", "is_partition": False, "row_security": False, "force_row_security": False, "owner_class": "database_owner"},
        ],
        "columns": [{"schema_name": "public", "relation_name": "z_table", "ordinal_position": Decimal("1"), "column_name": "id", "type_sql": "uuid", "not_null": True, "default_sql": None, "identity_kind": "", "generated_kind": "", "collation_schema": None, "collation_name": None}],
        "constraints": [{"schema_name": "public", "relation_name": "z_table", "constraint_name": "z_table_pkey", "constraint_type": "p", "definition": "PRIMARY KEY (id)", "deferrable": False, "initially_deferred": False, "validated": True, "local_column_numbers": [1], "target_column_numbers": None, "target_schema": None, "target_relation": None}],
        "indexes": [{"schema_name": "public", "relation_name": "z_table", "index_name": "z_table_pkey", "definition": "CREATE UNIQUE INDEX z_table_pkey ON public.z_table USING btree (id)", "is_unique": True, "is_primary": True, "is_exclusion": False, "is_live": True, "is_valid": True, "is_ready": True, "access_method": "btree", "owner_class": "database_owner"}],
        "triggers": [{"schema_name": "public", "relation_name": "z_table", "trigger_name": "touch", "definition": "CREATE TRIGGER touch BEFORE UPDATE ON public.z_table FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at()", "enabled": "O", "function_schema": "public", "function_name": "touch_updated_at", "function_arguments": "", "function_owner_class": "database_owner"}],
        "policies": [{"schema_name": "public", "relation_name": "z_table", "policy_name": "reader", "command": "r", "permissive": True, "roles": ["PUBLIC"], "using_expression": "true", "check_expression": None}],
        "rules": [{"schema_name": "public", "relation_name": "z_table", "rule_name": "block_insert", "event_type": "3", "enabled": "O", "is_instead": True, "definition": "CREATE RULE block_insert AS ON INSERT TO public.z_table DO INSTEAD NOTHING"}],
        "schema_privileges": [{"schema_name": "public", "grantee_class": "PUBLIC", "grantor_class": "owner", "privilege_type": "USAGE", "is_grantable": False}],
        "relation_privileges": [{"schema_name": "public", "relation_name": "z_table", "grantee_class": "PUBLIC", "grantor_class": "owner", "privilege_type": "SELECT", "is_grantable": False}],
        "column_privileges": [{"schema_name": "public", "relation_name": "z_table", "ordinal_position": 1, "column_name": "id", "grantee_class": "PUBLIC", "grantor_class": "owner", "privilege_type": "SELECT", "is_grantable": False}],
        "extensions": [{"extension_name": "uuid-ossp", "schema_name": "public", "extension_version": "1.1", "owner_class": "database_owner"}],
        "routines": [{"schema_name": "public", "routine_name": "touch_updated_at", "routine_kind": "f", "language_name": "plpgsql", "identity_arguments": "", "result_type": "trigger", "definition": "CREATE FUNCTION public.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$", "security_definer": False, "leakproof": False, "strict": False, "volatility": "v", "parallel_safety": "u", "configuration": None, "estimated_cost": "100", "estimated_rows": "0", "owner_class": "database_owner"}],
        "routine_privileges": [{"schema_name": "public", "routine_name": "touch_updated_at", "identity_arguments": "", "routine_kind": "f", "grantee_class": "PUBLIC", "grantor_class": "owner", "privilege_type": "EXECUTE", "is_grantable": False}],
        "default_privileges": [{"schema_name": None, "object_type": "r", "grantee_class": "PUBLIC", "grantor_class": "creator", "privilege_type": "SELECT", "is_grantable": False}],
        "types": [{"schema_name": "public", "type_name": "risk_level", "type_kind": "e", "category": "E", "type_sql": "risk_level", "owner_class": "database_owner", "not_null": False, "default_sql": None, "domain_base_type": None, "collation_schema": None, "collation_name": None, "enum_labels": ["low", "high"], "range_subtype": None, "composite_attributes": []}],
        "sequences": [{"schema_name": "app", "sequence_name": "id_seq", "start_value": 1, "increment_by": 1, "minimum_value": 1, "maximum_value": 9223372036854775807, "cache_size": 1, "cycles": False, "owned_by_schema": "public", "owned_by_relation": "z_table", "owned_by_column": "id"}],
        "event_triggers": [{"event_trigger_name": "guard_ddl", "event": "ddl_command_start", "enabled": "O", "tags": ["CREATE TABLE"], "function_schema": "public", "function_name": "guard_ddl", "function_arguments": ""}],
        "dependency_edges": [{"edge_kind": "routine_relation", "source_schema": "public", "source_relation": None, "source_routine": "touch_updated_at", "source_arguments": "", "source_routine_kind": "f", "source_rule": None, "target_schema": "public", "target_relation": "z_table", "target_column_number": 0, "target_column": None, "dependency_kind": "n"}],
    }
    sections.update(kwargs.pop("sections", {}))
    return _Connection(sections=sections, **kwargs)


def test_capture_is_read_only_repeatable_and_contains_all_structural_sections():
    connection = _connection()
    observed = capture_legacy_schema_catalog(connection)

    assert connection.closed_with is None
    assert observed.snapshot["format"] == CATALOG_FORMAT
    assert set(observed.snapshot) == {
        "format", "schemas", "relations", "columns", "constraints", "indexes",
        "triggers", "policies", "rules", "schema_privileges",
        "relation_privileges", "column_privileges", "extensions", "routines",
        "routine_privileges", "default_privileges", "types", "sequences",
        "event_triggers", "dependency_edges",
    }
    assert observed.target_identity == {
        "database_name": "legacy_test", "database_oid": 16384,
        "current_user": "preflight_reader", "server_address": "127.0.0.1",
        "server_port": 5432,
        "server_version": "16.3", "server_version_num": "160003", "server_encoding": "UTF8",
        "lc_collate": "C", "lc_ctype": "C",
    }
    assert observed.snapshot["relations"][0]["relation_name"] == "id_seq"
    assert observed.snapshot["columns"][0]["ordinal_position"] == 1
    assert len(observed.snapshot_sha256) == 64
    assert any("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in item for item in connection.statements)
    assert all(item.lstrip().upper().startswith(("SELECT", "SHOW", "SET")) for item in connection.statements)
    rendered = canonical_json_bytes({"identity": observed.target_identity, "snapshot": observed.snapshot})
    assert b"postgresql://" not in rendered
    assert b"legacy_test" in rendered
    assert b"password" not in rendered


@pytest.mark.parametrize(
    ("read_only", "isolation", "message"),
    [("off", "repeatable read", "read-only"), ("on", "read committed", "REPEATABLE READ")],
)
def test_capture_fails_closed_when_transaction_guards_are_not_effective(read_only, isolation, message):
    connection = _connection(read_only=read_only, isolation=isolation)
    with pytest.raises(LegacySchemaCatalogError, match=message):
        capture_legacy_schema_catalog(connection)
    assert connection.closed_with is LegacySchemaCatalogError
    assert not any("FROM pg_catalog.pg_class" in item for item in connection.statements)


def test_digest_is_stable_despite_driver_row_order_and_contains_no_oid_field():
    first = _connection()
    second = _connection(sections={
        "relations": list(reversed(first.sections["relations"])),
        "schemas": list(reversed(first.sections["schemas"])),
    })
    observed_first = capture_legacy_schema_catalog(first)
    observed_second = capture_legacy_schema_catalog(second)

    assert observed_first.snapshot == observed_second.snapshot
    assert observed_first.snapshot_sha256 == observed_second.snapshot_sha256
    assert b'"oid"' not in canonical_json_bytes(observed_first.snapshot)


def test_canonical_json_rejects_lossy_catalog_values():
    with pytest.raises(LegacySchemaCatalogError, match="unsupported type"):
        canonical_json_bytes({"value": object()})
    with pytest.raises(LegacySchemaCatalogError, match="must be integral"):
        canonical_json_bytes({"value": Decimal("1.5")})
