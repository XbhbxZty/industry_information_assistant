"""Read-only PostgreSQL catalog capture for legacy-schema preflight.

The adoption workflow needs an observation that is stable enough to compare to
reviewed manifests, but this module deliberately knows nothing about those
manifests, SQLAlchemy models, application engines, or database URLs.  It only
reads PostgreSQL's system catalog through a caller-owned connection.

The returned observation has two deliberately separate parts:

* ``target_identity`` describes the PostgreSQL server/session and database
  identity without exposing a connection URL or password;
* ``snapshot`` is a canonical, structural description of non-system schemas.

Keeping this boundary narrow makes it safe to use in a preflight transaction:
there is no data-table query, mutation, manifest write, or implicit connection
creation in this file.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterator, Mapping, Protocol, Sequence


CATALOG_FORMAT = "postgresql-legacy-schema-catalog/v1"


class LegacySchemaCatalogError(ValueError):
    """The caller did not provide a safe, repeatable read-only observation."""


class _Result(Protocol):
    def mappings(self) -> Any: ...


class _Transaction(Protocol):
    def __enter__(self) -> Any: ...
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any: ...


class CatalogConnection(Protocol):
    """The small SQLAlchemy ``Connection`` surface this module requires."""

    def begin(self) -> _Transaction: ...
    def exec_driver_sql(self, statement: str) -> _Result: ...


@dataclass(frozen=True)
class LegacySchemaCatalog:
    """A detached, digest-addressable PostgreSQL catalog observation."""

    target_identity: dict[str, Any]
    snapshot: dict[str, Any]
    snapshot_sha256: str


_SYSTEM_SCHEMA_PREDICATE = """
    n.nspname <> 'information_schema'
    AND n.nspname !~ '^pg_'
"""

_TARGET_IDENTITY_SQL = """
SELECT pg_catalog.current_database() AS database_name,
       d.oid::bigint AS database_oid,
       CURRENT_USER AS current_user,
       pg_catalog.host(pg_catalog.inet_server_addr()) AS server_address,
       pg_catalog.inet_server_port() AS server_port
FROM pg_catalog.pg_database AS d
WHERE d.datname = pg_catalog.current_database()
"""

_SCHEMAS_SQL = f"""
SELECT n.nspname AS schema_name,
       CASE
         WHEN n.nspowner = d.datdba THEN 'database_owner'
         WHEN pg_catalog.pg_get_userbyid(n.nspowner) = 'pg_database_owner'
           THEN 'pg_database_owner'
         ELSE 'other'
       END AS owner_class
FROM pg_catalog.pg_namespace AS n
JOIN pg_catalog.pg_database AS d ON d.datname = pg_catalog.current_database()
WHERE {_SYSTEM_SCHEMA_PREDICATE}
ORDER BY n.nspname
"""

_RELATIONS_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       c.relkind AS relation_kind, c.relpersistence AS persistence,
       c.relispartition AS is_partition,
       c.relrowsecurity AS row_security,
       c.relforcerowsecurity AS force_row_security,
       CASE WHEN c.relowner = d.datdba THEN 'database_owner' ELSE 'other' END
         AS owner_class
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_database AS d ON d.datname = pg_catalog.current_database()
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
ORDER BY n.nspname, c.relname, c.relkind
"""

_COLUMNS_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       a.attnum AS ordinal_position, a.attname AS column_name,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS type_sql,
       a.attnotnull AS not_null,
       pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_sql,
       a.attidentity AS identity_kind, a.attgenerated AS generated_kind,
       cn.nspname AS collation_schema, coll.collname AS collation_name
FROM pg_catalog.pg_attribute AS a
JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef AS d
       ON d.adrelid = a.attrelid AND d.adnum = a.attnum
LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = a.attcollation
LEFT JOIN pg_catalog.pg_namespace AS cn ON cn.oid = coll.collnamespace
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY n.nspname, c.relname, a.attnum
"""

_CONSTRAINTS_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       con.conname AS constraint_name, con.contype AS constraint_type,
       pg_catalog.pg_get_constraintdef(con.oid, true) AS definition,
       con.condeferrable AS deferrable, con.condeferred AS initially_deferred,
       con.convalidated AS validated,
       con.conkey AS local_column_numbers,
       con.confkey AS target_column_numbers,
       tn.nspname AS target_schema, tc.relname AS target_relation
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_class AS tc ON tc.oid = con.confrelid
LEFT JOIN pg_catalog.pg_namespace AS tn ON tn.oid = tc.relnamespace
WHERE {_SYSTEM_SCHEMA_PREDICATE}
ORDER BY n.nspname, c.relname, con.conname, con.contype
"""

_INDEXES_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       idx.relname AS index_name,
       pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
       i.indisunique AS is_unique, i.indisprimary AS is_primary,
       i.indisexclusion AS is_exclusion, i.indislive AS is_live,
       i.indisvalid AS is_valid, i.indisready AS is_ready,
       am.amname AS access_method,
       CASE WHEN idx.relowner = d.datdba THEN 'database_owner' ELSE 'other' END
         AS owner_class
FROM pg_catalog.pg_index AS i
JOIN pg_catalog.pg_class AS c ON c.oid = i.indrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_class AS idx ON idx.oid = i.indexrelid
JOIN pg_catalog.pg_am AS am ON am.oid = idx.relam
JOIN pg_catalog.pg_database AS d ON d.datname = pg_catalog.current_database()
WHERE {_SYSTEM_SCHEMA_PREDICATE}
ORDER BY n.nspname, c.relname, idx.relname
"""

_TRIGGERS_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       t.tgname AS trigger_name,
       pg_catalog.pg_get_triggerdef(t.oid, true) AS definition,
       t.tgenabled AS enabled,
       pn.nspname AS function_schema, p.proname AS function_name,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS function_arguments,
       CASE WHEN p.proowner = d.datdba THEN 'database_owner' ELSE 'other' END
         AS function_owner_class
FROM pg_catalog.pg_trigger AS t
JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
JOIN pg_catalog.pg_namespace AS pn ON pn.oid = p.pronamespace
JOIN pg_catalog.pg_database AS d ON d.datname = pg_catalog.current_database()
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND NOT t.tgisinternal
ORDER BY n.nspname, c.relname, t.tgname
"""

_POLICIES_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       pol.polname AS policy_name, pol.polcmd AS command,
       pol.polpermissive AS permissive,
       ARRAY(
           SELECT CASE
                    WHEN role_oid = 0 THEN 'PUBLIC'
                    ELSE pg_catalog.pg_get_userbyid(role_oid)
                  END
           FROM pg_catalog.unnest(pol.polroles) AS role_oid
           ORDER BY 1
       ) AS roles,
       pg_catalog.pg_get_expr(pol.polqual, pol.polrelid) AS using_expression,
       pg_catalog.pg_get_expr(pol.polwithcheck, pol.polrelid) AS check_expression
FROM pg_catalog.pg_policy AS pol
JOIN pg_catalog.pg_class AS c ON c.oid = pol.polrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE {_SYSTEM_SCHEMA_PREDICATE}
ORDER BY n.nspname, c.relname, pol.polname
"""

_RULES_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       r.rulename AS rule_name, r.ev_type AS event_type,
       r.ev_enabled AS enabled, r.is_instead AS is_instead,
       pg_catalog.pg_get_ruledef(r.oid, false) AS definition
FROM pg_catalog.pg_rewrite AS r
JOIN pg_catalog.pg_class AS c ON c.oid = r.ev_class
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND NOT (c.relkind IN ('v', 'm') AND r.rulename = '_RETURN')
ORDER BY n.nspname, c.relname, r.rulename
"""

_SCHEMA_PRIVILEGES_SQL = f"""
SELECT n.nspname AS schema_name,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantee) END
         AS grantee_class,
       CASE WHEN acl.grantor = n.nspowner THEN 'owner'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantor) END
         AS grantor_class,
       acl.privilege_type, acl.is_grantable
FROM pg_catalog.pg_namespace AS n
CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) AS acl
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND acl.grantee <> n.nspowner
ORDER BY n.nspname, grantee_class, acl.privilege_type, acl.is_grantable
"""

_RELATION_PRIVILEGES_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantee) END
         AS grantee_class,
       CASE WHEN acl.grantor = c.relowner THEN 'owner'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantor) END
         AS grantor_class,
       acl.privilege_type, acl.is_grantable
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) AS acl
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
  AND acl.grantee <> c.relowner
ORDER BY n.nspname, c.relname, grantee_class,
         acl.privilege_type, acl.is_grantable
"""

_COLUMN_PRIVILEGES_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS relation_name,
       a.attnum AS ordinal_position, a.attname AS column_name,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantee) END
         AS grantee_class,
       CASE WHEN acl.grantor = c.relowner THEN 'owner'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantor) END
         AS grantor_class,
       acl.privilege_type, acl.is_grantable
FROM pg_catalog.pg_attribute AS a
JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) AS acl
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND a.attnum > 0 AND NOT a.attisdropped
  AND acl.grantee <> c.relowner
ORDER BY n.nspname, c.relname, a.attnum, grantee_class,
         acl.privilege_type, acl.is_grantable
"""

_EXTENSIONS_SQL = """
SELECT e.extname AS extension_name, n.nspname AS schema_name,
       e.extversion AS extension_version,
       CASE WHEN e.extowner = d.datdba THEN 'database_owner' ELSE 'other' END
         AS owner_class
FROM pg_catalog.pg_extension AS e
JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
JOIN pg_catalog.pg_database AS d ON d.datname = pg_catalog.current_database()
WHERE e.extname <> 'plpgsql'
ORDER BY e.extname
"""

_ROUTINES_SQL = f"""
SELECT n.nspname AS schema_name, p.proname AS routine_name,
       p.prokind AS routine_kind, l.lanname AS language_name,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
       pg_catalog.pg_get_function_result(p.oid) AS result_type,
       pg_catalog.pg_get_functiondef(p.oid) AS definition,
       p.prosecdef AS security_definer, p.proleakproof AS leakproof,
       p.proisstrict AS strict, p.provolatile AS volatility,
       p.proparallel AS parallel_safety, p.proconfig AS configuration,
       p.procost::text AS estimated_cost, p.prorows::text AS estimated_rows,
       CASE WHEN p.proowner = d.datdba THEN 'database_owner' ELSE 'other' END
         AS owner_class
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
JOIN pg_catalog.pg_language AS l ON l.oid = p.prolang
LEFT JOIN pg_catalog.pg_depend AS dep
       ON dep.classid = 'pg_proc'::pg_catalog.regclass
      AND dep.objid = p.oid
      AND dep.deptype = 'e'
LEFT JOIN pg_catalog.pg_extension AS ext ON ext.oid = dep.refobjid
JOIN pg_catalog.pg_database AS d ON d.datname = pg_catalog.current_database()
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND ext.oid IS NULL
ORDER BY n.nspname, p.proname,
         pg_catalog.pg_get_function_identity_arguments(p.oid), p.prokind
"""

_ROUTINE_PRIVILEGES_SQL = f"""
SELECT n.nspname AS schema_name, p.proname AS routine_name,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
       p.prokind AS routine_kind,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantee) END
         AS grantee_class,
       CASE WHEN acl.grantor = p.proowner THEN 'owner'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantor) END
         AS grantor_class,
       acl.privilege_type, acl.is_grantable
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
LEFT JOIN pg_catalog.pg_depend AS dep
       ON dep.classid = 'pg_proc'::pg_catalog.regclass
      AND dep.objid = p.oid AND dep.deptype = 'e'
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))
) AS acl
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND dep.objid IS NULL
  AND acl.grantee <> p.proowner
ORDER BY n.nspname, p.proname,
         pg_catalog.pg_get_function_identity_arguments(p.oid), p.prokind,
         grantee_class, acl.privilege_type, acl.is_grantable
"""

_DEFAULT_PRIVILEGES_SQL = """
SELECT n.nspname AS schema_name, d.defaclobjtype AS object_type,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
            WHEN acl.grantee = d.defaclrole THEN 'creator'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantee) END
         AS grantee_class,
       CASE WHEN acl.grantor = d.defaclrole THEN 'creator'
            ELSE 'ROLE:' || pg_catalog.pg_get_userbyid(acl.grantor) END
         AS grantor_class,
       acl.privilege_type, acl.is_grantable
FROM pg_catalog.pg_default_acl AS d
LEFT JOIN pg_catalog.pg_namespace AS n ON n.oid = d.defaclnamespace
CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) AS acl
ORDER BY n.nspname NULLS FIRST, d.defaclobjtype, grantee_class,
         acl.privilege_type, acl.is_grantable
"""

_TYPES_SQL = f"""
SELECT n.nspname AS schema_name, t.typname AS type_name,
       t.typtype AS type_kind, t.typcategory AS category,
       pg_catalog.format_type(t.oid, NULL) AS type_sql,
       CASE WHEN t.typowner = d.datdba THEN 'database_owner' ELSE 'other' END
         AS owner_class,
       t.typnotnull AS not_null,
       COALESCE(pg_catalog.pg_get_expr(t.typdefaultbin, 0), t.typdefault)
         AS default_sql,
       CASE WHEN t.typbasetype = 0 THEN NULL
            ELSE pg_catalog.format_type(t.typbasetype, t.typtypmod) END
         AS domain_base_type,
       coll_n.nspname AS collation_schema, coll.collname AS collation_name,
       ARRAY(
         SELECT e.enumlabel
         FROM pg_catalog.pg_enum AS e
         WHERE e.enumtypid = t.oid
         ORDER BY e.enumsortorder
       ) AS enum_labels,
       CASE WHEN rng.rngsubtype = 0 THEN NULL
            ELSE pg_catalog.format_type(rng.rngsubtype, NULL) END
         AS range_subtype,
       ARRAY(
         SELECT a.attname || ':' ||
                pg_catalog.format_type(a.atttypid, a.atttypmod) || ':' ||
                a.attnotnull::text
         FROM pg_catalog.pg_attribute AS a
         WHERE a.attrelid = t.typrelid
           AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
       ) AS composite_attributes
FROM pg_catalog.pg_type AS t
JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace
JOIN pg_catalog.pg_database AS d ON d.datname = pg_catalog.current_database()
LEFT JOIN pg_catalog.pg_class AS cls ON cls.oid = t.typrelid
LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = t.typcollation
LEFT JOIN pg_catalog.pg_namespace AS coll_n ON coll_n.oid = coll.collnamespace
LEFT JOIN pg_catalog.pg_range AS rng
       ON rng.rngtypid = t.oid OR rng.rngmultitypid = t.oid
LEFT JOIN pg_catalog.pg_depend AS ext_dep
       ON ext_dep.classid = 'pg_type'::pg_catalog.regclass
      AND ext_dep.objid = t.oid AND ext_dep.deptype = 'e'
WHERE {_SYSTEM_SCHEMA_PREDICATE}
  AND t.typtype IN ('b', 'c', 'd', 'e', 'r', 'm')
  AND NOT (t.typelem <> 0 AND t.typarray = 0)
  AND (t.typtype <> 'c' OR cls.relkind = 'c')
  AND ext_dep.objid IS NULL
ORDER BY n.nspname, t.typname
"""

_SEQUENCES_SQL = f"""
SELECT n.nspname AS schema_name, c.relname AS sequence_name,
       s.seqstart AS start_value, s.seqincrement AS increment_by,
       s.seqmin AS minimum_value, s.seqmax AS maximum_value,
       s.seqcache AS cache_size, s.seqcycle AS cycles,
       owned_n.nspname AS owned_by_schema,
       owned_c.relname AS owned_by_relation,
       owned_a.attname AS owned_by_column
FROM pg_catalog.pg_sequence AS s
JOIN pg_catalog.pg_class AS c ON c.oid = s.seqrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_depend AS dep
       ON dep.classid = 'pg_class'::pg_catalog.regclass
      AND dep.objid = s.seqrelid
      AND dep.deptype IN ('a', 'i')
LEFT JOIN pg_catalog.pg_class AS owned_c ON owned_c.oid = dep.refobjid
LEFT JOIN pg_catalog.pg_namespace AS owned_n ON owned_n.oid = owned_c.relnamespace
LEFT JOIN pg_catalog.pg_attribute AS owned_a
       ON owned_a.attrelid = dep.refobjid
      AND owned_a.attnum = dep.refobjsubid
WHERE {_SYSTEM_SCHEMA_PREDICATE}
ORDER BY n.nspname, c.relname
"""

_EVENT_TRIGGERS_SQL = """
SELECT evt.evtname AS event_trigger_name, evt.evtevent AS event,
       evt.evtenabled AS enabled, evt.evttags AS tags,
       n.nspname AS function_schema, p.proname AS function_name,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS function_arguments
FROM pg_catalog.pg_event_trigger AS evt
JOIN pg_catalog.pg_proc AS p ON p.oid = evt.evtfoid
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
ORDER BY evt.evtname
"""

_DEPENDENCY_EDGES_SQL = """
SELECT 'routine_relation' AS edge_kind,
       sn.nspname AS source_schema, NULL::name AS source_relation,
       p.proname AS source_routine,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS source_arguments,
       p.prokind AS source_routine_kind, NULL::name AS source_rule,
       tn.nspname AS target_schema, tc.relname AS target_relation,
       dep.refobjsubid AS target_column_number, ta.attname AS target_column,
       dep.deptype AS dependency_kind
FROM pg_catalog.pg_depend AS dep
JOIN pg_catalog.pg_proc AS p
  ON dep.classid = 'pg_proc'::pg_catalog.regclass AND dep.objid = p.oid
JOIN pg_catalog.pg_namespace AS sn ON sn.oid = p.pronamespace
JOIN pg_catalog.pg_class AS tc
  ON dep.refclassid = 'pg_class'::pg_catalog.regclass AND dep.refobjid = tc.oid
JOIN pg_catalog.pg_namespace AS tn ON tn.oid = tc.relnamespace
LEFT JOIN pg_catalog.pg_attribute AS ta
  ON ta.attrelid = tc.oid AND ta.attnum = dep.refobjsubid
WHERE sn.nspname <> 'information_schema' AND sn.nspname !~ '^pg_'
  AND tn.nspname <> 'information_schema' AND tn.nspname !~ '^pg_'
  AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_depend AS ext_dep
    WHERE ext_dep.classid = 'pg_proc'::pg_catalog.regclass
      AND ext_dep.objid = p.oid AND ext_dep.deptype = 'e'
  )
UNION ALL
SELECT 'rewrite_relation' AS edge_kind,
       sn.nspname AS source_schema, sc.relname AS source_relation,
       NULL::name AS source_routine, NULL::text AS source_arguments,
       NULL::"char" AS source_routine_kind, r.rulename AS source_rule,
       tn.nspname AS target_schema, tc.relname AS target_relation,
       dep.refobjsubid AS target_column_number, ta.attname AS target_column,
       dep.deptype AS dependency_kind
FROM pg_catalog.pg_depend AS dep
JOIN pg_catalog.pg_rewrite AS r
  ON dep.classid = 'pg_rewrite'::pg_catalog.regclass AND dep.objid = r.oid
JOIN pg_catalog.pg_class AS sc ON sc.oid = r.ev_class
JOIN pg_catalog.pg_namespace AS sn ON sn.oid = sc.relnamespace
JOIN pg_catalog.pg_class AS tc
  ON dep.refclassid = 'pg_class'::pg_catalog.regclass AND dep.refobjid = tc.oid
JOIN pg_catalog.pg_namespace AS tn ON tn.oid = tc.relnamespace
LEFT JOIN pg_catalog.pg_attribute AS ta
  ON ta.attrelid = tc.oid AND ta.attnum = dep.refobjsubid
WHERE sn.nspname <> 'information_schema' AND sn.nspname !~ '^pg_'
  AND tn.nspname <> 'information_schema' AND tn.nspname !~ '^pg_'
ORDER BY edge_kind, source_schema, source_relation, source_routine,
         source_arguments, source_routine_kind, source_rule,
         target_schema, target_relation, target_column_number, dependency_kind
"""


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON-safe catalog data deterministically and reject ambiguity."""
    normalized = _json_safe(value)
    try:
        return json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:  # defensive: _json_safe is strict
        raise LegacySchemaCatalogError("catalog observation is not canonical JSON") from exc


def catalog_sha256(snapshot: Mapping[str, Any]) -> str:
    """Return the content address of the structural snapshot only."""
    if not isinstance(snapshot, Mapping):
        raise LegacySchemaCatalogError("catalog snapshot must be an object")
    return hashlib.sha256(canonical_json_bytes(dict(snapshot))).hexdigest()


def capture_legacy_schema_catalog(connection: CatalogConnection) -> LegacySchemaCatalog:
    """Capture non-system PostgreSQL structure in one read-only repeatable view.

    The caller owns the SQLAlchemy connection.  ``SET TRANSACTION`` is issued
    only after starting a transaction, and both isolation/read-only settings are
    observed again before any catalog read.  A server that refuses either guard
    fails closed rather than yielding a potentially inconsistent fingerprint.
    """
    if not hasattr(connection, "begin") or not hasattr(connection, "exec_driver_sql"):
        raise LegacySchemaCatalogError("catalog capture requires a SQLAlchemy Connection")

    with legacy_schema_read_transaction(connection):
        return capture_legacy_schema_catalog_in_transaction(connection)


@contextmanager
def legacy_schema_read_transaction(connection: CatalogConnection) -> Iterator[None]:
    """Open the exact read-only snapshot shared by catalog and metadata reads."""
    if not hasattr(connection, "begin") or not hasattr(connection, "exec_driver_sql"):
        raise LegacySchemaCatalogError("catalog capture requires a SQLAlchemy Connection")
    with connection.begin():
        connection.exec_driver_sql(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        _verify_transaction_guards(connection)
        yield


def capture_legacy_schema_catalog_in_transaction(
    connection: CatalogConnection,
) -> LegacySchemaCatalog:
    """Capture inside a caller-owned guarded transaction without nesting one."""
    _verify_transaction_guards(connection)
    target_identity = {
        **_one_row(connection.exec_driver_sql(_TARGET_IDENTITY_SQL)),
        "server_version": _scalar(connection.exec_driver_sql("SHOW server_version")),
        "server_version_num": _scalar(connection.exec_driver_sql("SHOW server_version_num")),
        "server_encoding": _scalar(connection.exec_driver_sql("SHOW server_encoding")),
        "lc_collate": _scalar(connection.exec_driver_sql("SHOW lc_collate")),
        "lc_ctype": _scalar(connection.exec_driver_sql("SHOW lc_ctype")),
    }
    snapshot = {
        "format": CATALOG_FORMAT,
        "schemas": _rows(connection.exec_driver_sql(_SCHEMAS_SQL)),
        "relations": _rows(connection.exec_driver_sql(_RELATIONS_SQL)),
        "columns": _rows(connection.exec_driver_sql(_COLUMNS_SQL)),
        "constraints": _rows(connection.exec_driver_sql(_CONSTRAINTS_SQL)),
        "indexes": _rows(connection.exec_driver_sql(_INDEXES_SQL)),
        "triggers": _rows(connection.exec_driver_sql(_TRIGGERS_SQL)),
        "policies": _rows(connection.exec_driver_sql(_POLICIES_SQL)),
        "rules": _rows(connection.exec_driver_sql(_RULES_SQL)),
        "schema_privileges": _rows(
            connection.exec_driver_sql(_SCHEMA_PRIVILEGES_SQL)
        ),
        "relation_privileges": _rows(
            connection.exec_driver_sql(_RELATION_PRIVILEGES_SQL)
        ),
        "column_privileges": _rows(
            connection.exec_driver_sql(_COLUMN_PRIVILEGES_SQL)
        ),
        "extensions": _rows(connection.exec_driver_sql(_EXTENSIONS_SQL)),
        "routines": _rows(connection.exec_driver_sql(_ROUTINES_SQL)),
        "routine_privileges": _rows(
            connection.exec_driver_sql(_ROUTINE_PRIVILEGES_SQL)
        ),
        "default_privileges": _rows(
            connection.exec_driver_sql(_DEFAULT_PRIVILEGES_SQL)
        ),
        "types": _rows(connection.exec_driver_sql(_TYPES_SQL)),
        "sequences": _rows(connection.exec_driver_sql(_SEQUENCES_SQL)),
        "event_triggers": _rows(connection.exec_driver_sql(_EVENT_TRIGGERS_SQL)),
        "dependency_edges": _rows(
            connection.exec_driver_sql(_DEPENDENCY_EDGES_SQL)
        ),
    }

    # Sort again after conversion.  PostgreSQL ORDER BY is the source of truth,
    # while this protects the digest if a driver/fake result iterator is odd.
    snapshot = _sorted_snapshot(snapshot)
    target_identity = _json_safe(target_identity)
    return LegacySchemaCatalog(
        target_identity=target_identity,
        snapshot=snapshot,
        snapshot_sha256=catalog_sha256(snapshot),
    )


def _verify_transaction_guards(connection: CatalogConnection) -> None:
    if _scalar(connection.exec_driver_sql("SHOW transaction_isolation")) != "repeatable read":
        raise LegacySchemaCatalogError("catalog capture requires REPEATABLE READ")
    if _scalar(connection.exec_driver_sql("SHOW transaction_read_only")) != "on":
        raise LegacySchemaCatalogError("catalog capture requires a read-only transaction")


def _scalar(result: Any) -> str:
    """Read one scalar without accepting a missing/non-text PostgreSQL setting."""
    if hasattr(result, "scalar_one"):
        value = result.scalar_one()
    elif hasattr(result, "scalar"):
        value = result.scalar()
    else:
        raise LegacySchemaCatalogError("catalog setting result has no scalar reader")
    if not isinstance(value, str) or not value:
        raise LegacySchemaCatalogError("catalog setting is missing or malformed")
    return value


def _rows(result: Any) -> list[dict[str, Any]]:
    """Detach SQLAlchemy mapping rows and keep only JSON-safe basic values."""
    if not hasattr(result, "mappings"):
        raise LegacySchemaCatalogError("catalog query result has no mapping reader")
    mapping_result = result.mappings()
    raw_rows: Sequence[Any]
    if hasattr(mapping_result, "all"):
        raw_rows = mapping_result.all()
    else:
        raw_rows = list(mapping_result)

    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        try:
            mapping = dict(raw_row)
        except (TypeError, ValueError) as exc:
            raise LegacySchemaCatalogError("catalog row is not a mapping") from exc
        rows.append(_json_safe(mapping))
    return rows


def _one_row(result: Any) -> dict[str, Any]:
    rows = _rows(result)
    if len(rows) != 1:
        raise LegacySchemaCatalogError("catalog identity query must return exactly one row")
    return rows[0]


def _json_safe(value: Any) -> Any:
    """Normalize database scalar values without allowing a lossy fallback."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise LegacySchemaCatalogError("catalog decimal must be integral")
        return int(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise LegacySchemaCatalogError("catalog object key must be a string")
            normalized[key] = _json_safe(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise LegacySchemaCatalogError(
        f"catalog value has unsupported type {type(value).__name__}"
    )


def _sorted_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return canonical key order and stable row order for every relation list."""
    normalized = _json_safe(snapshot)
    if not isinstance(normalized, dict):  # pragma: no cover - kept for type safety
        raise LegacySchemaCatalogError("catalog snapshot must be an object")
    for key, value in normalized.items():
        if key == "format":
            continue
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise LegacySchemaCatalogError(f"catalog section {key} must be a row list")
        value.sort(key=lambda row: canonical_json_bytes(row))
    return normalized
