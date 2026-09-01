"""Frozen-manifest classification for legacy PostgreSQL application schemas.

This phase is intentionally read-only.  It can explain whether a database is
empty, already managed, exactly adoptable, a known incompatible Docker legacy,
or drifted.  It never creates ``alembic_version`` and contains no stamp/ALTER/
DROP path; transactional adoption is a separate phase.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .legacy_schema_catalog import (
    CATALOG_FORMAT,
    LegacySchemaCatalog,
    LegacySchemaCatalogError,
    canonical_json_bytes,
    capture_legacy_schema_catalog_in_transaction,
    catalog_sha256,
    legacy_schema_read_transaction,
)


MANIFEST_FORMAT = "postgresql-legacy-schema-manifest/v1"
HEAD_REVISION = "20260831_0001"
LANGGRAPH_PROVIDER_MIGRATIONS = tuple(range(10))
SUPPORTED_POSTGRESQL_MAJOR = "15"
EXPECTED_SCHEMAS = ({
    "schema_name": "public",
    "owner_class": "pg_database_owner",
},)
EXPECTED_SCHEMA_PRIVILEGES = ({
    "schema_name": "public",
    "grantee_class": "PUBLIC",
    "grantor_class": "owner",
    "privilege_type": "USAGE",
    "is_grantable": False,
},)

APPLICATION_TABLES = frozenset({
    "admin_company_profile_audits",
    "admin_company_profiles",
    "bidding_info",
    "chat_attachments",
    "chat_messages",
    "chat_sessions",
    "company_data",
    "documents",
    "industry_news",
    "industry_stats",
    "knowledge_bases",
    "long_term_memories",
    "news_collection_tasks",
    "policy_data",
    "research_checkpoint_integrities",
    "research_checkpoints",
    "users",
})
DOCKER_OVERLAP_TABLES = frozenset({
    "users",
    "chat_sessions",
    "chat_messages",
    "knowledge_bases",
    "documents",
    "long_term_memories",
})
DOCKER_DEMO_RELATIONS = frozenset({
    "restaurants",
    "restaurant_orders",
    "stocks",
    "stock_daily",
    "legal_cases",
    "vehicles",
    "transport_records",
    "restaurants_id_seq",
    "restaurant_orders_id_seq",
    "stocks_id_seq",
    "stock_daily_id_seq",
    "legal_cases_id_seq",
    "vehicles_id_seq",
    "transport_records_id_seq",
})
LANGGRAPH_RELATIONS = frozenset({
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
})
ALEMBIC_RELATIONS = frozenset({"alembic_version"})

BASE_MANIFEST_ID = "application_base_full_v1"
DOCKER_MANIFEST_ID = "application_docker_overlap_v1"
DEMO_MANIFEST_ID = "unmanaged_docker_demo_v1"
LANGGRAPH_MANIFEST_ID = "unmanaged_langgraph_postgres_3_1_2"
ALEMBIC_MANIFEST_ID = "alembic_version_v1"
UUID_OSSP_MANIFEST_ID = "extension_uuid_ossp_v1"

_MANIFEST_FILENAMES = {
    BASE_MANIFEST_ID: "application_base_full_v1.json",
    DOCKER_MANIFEST_ID: "application_docker_overlap_v1.json",
    DEMO_MANIFEST_ID: "unmanaged_docker_demo_v1.json",
    LANGGRAPH_MANIFEST_ID: "unmanaged_langgraph_postgres_3_1_2.json",
    ALEMBIC_MANIFEST_ID: "alembic_version_v1.json",
    UUID_OSSP_MANIFEST_ID: "extension_uuid_ossp_v1.json",
}
_EXPECTED_CATALOG_SHA256 = {
    BASE_MANIFEST_ID: "42e7c7604e8c74ebacc79a3aa82ec34fa488d03fa60ba0f985cb78f034f82044",
    DOCKER_MANIFEST_ID: "da89500e22607213fd0006a0ccff149e9f4520536f3e2d26d5fb91bc6fab4910",
    DEMO_MANIFEST_ID: "87bc3bb786cd31a49da9b14db02dca068025008d25a3f5e66f545a1a6bbfe9c5",
    LANGGRAPH_MANIFEST_ID: "3f61fe21c2abc98d39afe51d0ef43a817b5fe6ab9269dc5d814c01b065dda547",
    ALEMBIC_MANIFEST_ID: "03bc2a00e9b18b92255e52f4cfa3f1c9a780ff7ad679707445f6706508c5cafa",
    UUID_OSSP_MANIFEST_ID: "fc6d065a3e0b08b9685340fbb567a84d258cbf39c339f4a0b69b7a527c24495f",
}
_EXPECTED_MANIFEST_CLASSIFICATIONS = {
    BASE_MANIFEST_ID: "adoptable",
    DOCKER_MANIFEST_ID: "known_incompatible",
    DEMO_MANIFEST_ID: "unmanaged_exact",
    LANGGRAPH_MANIFEST_ID: "unmanaged_exact",
    ALEMBIC_MANIFEST_ID: "version_metadata",
    UUID_OSSP_MANIFEST_ID: "unmanaged_exact",
}
_DEFAULT_MANIFEST_DIR = Path(__file__).resolve().parents[2] / "migrations" / "legacy_fingerprints"


class LegacyPreflightError(ValueError):
    """A frozen manifest or preflight observation is malformed."""


class PreflightStatus(str, Enum):
    UPGRADE_REQUIRED = "upgrade_required"
    EXACT_ADOPTABLE = "exact_adoptable"
    KNOWN_INCOMPATIBLE = "known_incompatible"
    ALREADY_MANAGED = "already_managed"
    VERSION_STATE_INVALID = "version_state_invalid"
    SCHEMA_DRIFT = "schema_drift"


@dataclass(frozen=True)
class CatalogDifference:
    path: str
    issue: str
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "issue": self.issue,
            "expected": _report_safe_catalog_value(self.path, self.expected),
            "actual": _report_safe_catalog_value(self.path, self.actual),
        }


@dataclass(frozen=True)
class FrozenCatalogManifest:
    manifest_id: str
    classification: str
    relation_names: frozenset[str]
    extension_names: frozenset[str]
    routine_identities: frozenset[str]
    catalog: dict[str, Any]
    catalog_sha256: str


@dataclass(frozen=True)
class LegacyPreflightReport:
    status: PreflightStatus
    profile_id: str | None
    message: str
    target_identity: dict[str, Any]
    snapshot_sha256: str
    preflight_sha256: str
    unmanaged_packages: tuple[str, ...]
    differences: tuple[CatalogDifference, ...]

    @property
    def may_enter_adoption_approval(self) -> bool:
        """Return candidacy only; this report is never an adoption authorization."""
        return self.status is PreflightStatus.EXACT_ADOPTABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "profile_id": self.profile_id,
            "message": self.message,
            "target_identity": self.target_identity,
            "snapshot_sha256": self.snapshot_sha256,
            "preflight_sha256": self.preflight_sha256,
            "unmanaged_packages": list(self.unmanaged_packages),
            "differences": [item.to_dict() for item in self.differences],
            "may_enter_adoption_approval": self.may_enter_adoption_approval,
        }


_REPORT_SENSITIVE_FIELDS = frozenset({
    "definition",
    "default_sql",
    "using_expression",
    "check_expression",
    "configuration",
})


def _report_safe_catalog_value(path: str, value: Any) -> Any:
    """Keep structural diffs useful without echoing embedded DDL literals."""
    field = path.rsplit(".", 1)[-1]
    if field in _REPORT_SENSITIVE_FIELDS and value is not None:
        return {
            "redacted": True,
            "sha256": hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
        }
    if isinstance(value, Mapping):
        return {
            key: _report_safe_catalog_value(f"{path}.{key}", item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_report_safe_catalog_value(path, item) for item in value]
    return value


def routine_identity(row: Mapping[str, Any]) -> str:
    return (
        f"{row.get('schema_name')}.{row.get('routine_name')}"
        f"({row.get('identity_arguments') or ''}):{row.get('routine_kind')}"
    )


def project_catalog_component(
    snapshot: Mapping[str, Any],
    *,
    relation_names: Iterable[str] = (),
    extension_names: Iterable[str] = (),
    routine_identities: Iterable[str] = (),
) -> dict[str, Any]:
    """Project a self-contained relation/extension/routine component."""
    relations = frozenset(relation_names)
    extensions = frozenset(extension_names)
    routines = frozenset(routine_identities)
    projected: dict[str, Any] = {"format": CATALOG_FORMAT}
    for section in (
        "relations", "columns", "constraints", "indexes", "triggers", "policies",
        "rules", "relation_privileges", "column_privileges", "sequences",
    ):
        rows = snapshot.get(section, [])
        if not isinstance(rows, list):
            raise LegacyPreflightError(f"catalog section {section} must be a list")
        name_key = "sequence_name" if section == "sequences" else "relation_name"
        projected[section] = [
            dict(row) for row in rows
            if isinstance(row, Mapping) and row.get(name_key) in relations
        ]
    extension_rows = snapshot.get("extensions", [])
    routine_rows = snapshot.get("routines", [])
    if not isinstance(extension_rows, list) or not isinstance(routine_rows, list):
        raise LegacyPreflightError("catalog extension/routine sections must be lists")
    projected["extensions"] = [
        dict(row) for row in extension_rows
        if isinstance(row, Mapping) and row.get("extension_name") in extensions
    ]
    projected["routines"] = [
        dict(row) for row in routine_rows
        if isinstance(row, Mapping) and routine_identity(row) in routines
    ]
    routine_privilege_rows = snapshot.get("routine_privileges", [])
    if not isinstance(routine_privilege_rows, list):
        raise LegacyPreflightError("catalog routine_privileges must be a list")
    projected["routine_privileges"] = [
        dict(row) for row in routine_privilege_rows
        if isinstance(row, Mapping) and routine_identity(row) in routines
    ]
    # Event triggers are database-global DDL behavior, not an object owned by
    # any relation package.  Classification rejects every observed event
    # trigger separately rather than letting a component projection hide it.
    for section in (
        "schema_privileges", "default_privileges", "types", "event_triggers",
        "dependency_edges",
    ):
        projected[section] = []
    for section in projected:
        if section != "format":
            projected[section].sort(key=canonical_json_bytes)
    return projected


def load_frozen_manifests(
    manifest_dir: Path | None = None,
) -> dict[str, FrozenCatalogManifest]:
    root = _DEFAULT_MANIFEST_DIR if manifest_dir is None else manifest_dir
    manifests: dict[str, FrozenCatalogManifest] = {}
    for expected_id, filename in _MANIFEST_FILENAMES.items():
        path = root / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LegacyPreflightError(f"cannot load frozen manifest {filename}") from exc
        if not isinstance(payload, dict) or payload.get("format") != MANIFEST_FORMAT:
            raise LegacyPreflightError(f"manifest {filename} has an unsupported format")
        if payload.get("manifest_id") != expected_id:
            raise LegacyPreflightError(f"manifest {filename} has the wrong id")
        classification = payload.get("classification")
        if classification != _EXPECTED_MANIFEST_CLASSIFICATIONS[expected_id]:
            raise LegacyPreflightError(f"manifest {filename} has the wrong classification")
        catalog = payload.get("catalog")
        expected_sha = payload.get("catalog_sha256")
        if not isinstance(catalog, dict) or not isinstance(expected_sha, str):
            raise LegacyPreflightError(f"manifest {filename} is incomplete")
        actual_sha = catalog_sha256(catalog)
        if actual_sha != expected_sha:
            raise LegacyPreflightError(f"manifest {filename} digest mismatch")
        if actual_sha != _EXPECTED_CATALOG_SHA256[expected_id]:
            raise LegacyPreflightError(f"manifest {filename} frozen digest mismatch")
        relation_names = _string_set(payload.get("relation_names"), filename)
        extension_names = _string_set(payload.get("extension_names"), filename)
        routine_identities = _string_set(payload.get("routine_identities"), filename)
        if relation_names != _observed_relation_names(catalog):
            raise LegacyPreflightError(
                f"manifest {filename} relation declarations do not match its catalog"
            )
        catalog_extensions = catalog.get("extensions", [])
        catalog_routines = catalog.get("routines", [])
        if not isinstance(catalog_extensions, list) or not isinstance(catalog_routines, list):
            raise LegacyPreflightError(
                f"manifest {filename} extension/routine sections must be lists"
            )
        observed_extensions = frozenset(
            row.get("extension_name") for row in catalog_extensions
            if isinstance(row, Mapping) and isinstance(row.get("extension_name"), str)
        )
        observed_routines = frozenset(
            routine_identity(row) for row in catalog_routines
            if isinstance(row, Mapping)
        )
        if extension_names != observed_extensions or routine_identities != observed_routines:
            raise LegacyPreflightError(
                f"manifest {filename} object declarations do not match its catalog"
            )
        projected_catalog = project_catalog_component(
            catalog,
            relation_names=relation_names,
            extension_names=extension_names,
            routine_identities=routine_identities,
        )
        if projected_catalog != catalog:
            raise LegacyPreflightError(
                f"manifest {filename} contains undeclared or unsorted catalog objects"
            )
        manifests[expected_id] = FrozenCatalogManifest(
            manifest_id=expected_id,
            classification=classification,
            relation_names=relation_names,
            extension_names=extension_names,
            routine_identities=routine_identities,
            catalog=catalog,
            catalog_sha256=actual_sha,
        )
    return manifests


def preflight_legacy_schema(
    connection: Any,
    *,
    manifest_dir: Path | None = None,
) -> LegacyPreflightReport:
    """Observe and classify one target in a single read-only transaction."""
    manifests = load_frozen_manifests(manifest_dir)
    with legacy_schema_read_transaction(connection):
        observation = capture_legacy_schema_catalog_in_transaction(connection)
        relation_names = _observed_relation_names(observation.snapshot)
        alembic_versions: tuple[str, ...] | None = None
        langgraph_migrations: tuple[int, ...] | None = None
        if ALEMBIC_RELATIONS <= relation_names and _component_matches(
            observation.snapshot, manifests[ALEMBIC_MANIFEST_ID]
        ):
            alembic_versions = tuple(_scalar_rows(
                connection.exec_driver_sql(
                    "SELECT version_num FROM public.alembic_version ORDER BY version_num"
                )
            ))
        if LANGGRAPH_RELATIONS <= relation_names and _component_matches(
            observation.snapshot, manifests[LANGGRAPH_MANIFEST_ID]
        ):
            values = _scalar_rows(connection.exec_driver_sql(
                "SELECT v FROM public.checkpoint_migrations ORDER BY v"
            ))
            if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
                langgraph_migrations = tuple(values)
    return classify_legacy_schema(
        observation,
        manifests,
        alembic_versions=alembic_versions,
        langgraph_migrations=langgraph_migrations,
    )


def classify_legacy_schema(
    observation: LegacySchemaCatalog,
    manifests: Mapping[str, FrozenCatalogManifest],
    *,
    alembic_versions: tuple[str, ...] | None = None,
    langgraph_migrations: tuple[int, ...] | None = None,
) -> LegacyPreflightReport:
    required = set(_MANIFEST_FILENAMES)
    if set(manifests) != required:
        raise LegacyPreflightError("preflight requires the complete frozen manifest set")
    snapshot = observation.snapshot
    differences: list[CatalogDifference] = []
    packages: list[str] = []

    server_version_num = observation.target_identity.get("server_version_num")
    if not isinstance(server_version_num, str) or not server_version_num.startswith(
        SUPPORTED_POSTGRESQL_MAJOR
    ):
        differences.append(CatalogDifference(
            "target_identity.server_version_num", "unsupported_postgresql_major",
            f"{SUPPORTED_POSTGRESQL_MAJOR}xxxx", server_version_num,
        ))

    schemas = snapshot.get("schemas")
    if schemas != list(EXPECTED_SCHEMAS):
        differences.append(CatalogDifference(
            "schemas", "unexpected_non_system_schema",
            list(EXPECTED_SCHEMAS), schemas,
        ))

    schema_privileges = snapshot.get("schema_privileges")
    if schema_privileges != list(EXPECTED_SCHEMA_PRIVILEGES):
        differences.append(CatalogDifference(
            "schema_privileges", "unexpected_schema_privilege",
            list(EXPECTED_SCHEMA_PRIVILEGES), schema_privileges,
        ))

    for section, issue in (
        ("default_privileges", "unexpected_default_privilege"),
        ("types", "unexpected_standalone_type"),
        ("dependency_edges", "unexpected_dependency"),
    ):
        rows = snapshot.get(section, [])
        if not isinstance(rows, list):
            raise LegacyPreflightError(f"catalog {section} must be a list")
        if rows:
            differences.append(CatalogDifference(section, issue, [], rows))

    relation_names = _observed_relation_names(snapshot)
    allowed_relation_names = (
        APPLICATION_TABLES | DOCKER_DEMO_RELATIONS | LANGGRAPH_RELATIONS | ALEMBIC_RELATIONS
    )
    unknown_relations = sorted(relation_names - allowed_relation_names)
    if unknown_relations:
        differences.append(CatalogDifference(
            "relations", "unexpected_relation", [], unknown_relations,
        ))

    demo_present = bool(relation_names & DOCKER_DEMO_RELATIONS)
    if demo_present:
        packages.append(DEMO_MANIFEST_ID)
        differences.extend(_manifest_differences(snapshot, manifests[DEMO_MANIFEST_ID]))

    langgraph_present = bool(relation_names & LANGGRAPH_RELATIONS)
    if langgraph_present:
        packages.append(LANGGRAPH_MANIFEST_ID)
        differences.extend(_manifest_differences(snapshot, manifests[LANGGRAPH_MANIFEST_ID]))
        if langgraph_migrations != LANGGRAPH_PROVIDER_MIGRATIONS:
            differences.append(CatalogDifference(
                "unmanaged.langgraph.migrations", "provider_state_mismatch",
                list(LANGGRAPH_PROVIDER_MIGRATIONS),
                None if langgraph_migrations is None else list(langgraph_migrations),
            ))

    observed_extensions = {
        row.get("extension_name") for row in snapshot.get("extensions", [])
        if isinstance(row, Mapping)
    }
    if "uuid-ossp" in observed_extensions:
        packages.append(UUID_OSSP_MANIFEST_ID)
        differences.extend(_manifest_differences(snapshot, manifests[UUID_OSSP_MANIFEST_ID]))
    unexpected_extensions = sorted(
        name for name in observed_extensions if name not in {"uuid-ossp"}
    )
    if unexpected_extensions:
        differences.append(CatalogDifference(
            "extensions", "unexpected_extension", [], unexpected_extensions,
        ))

    event_triggers = snapshot.get("event_triggers", [])
    if not isinstance(event_triggers, list):
        raise LegacyPreflightError("catalog event_triggers must be a list")
    if event_triggers:
        differences.append(CatalogDifference(
            "event_triggers", "unexpected_event_trigger", [], event_triggers,
        ))

    differences.extend(_cross_boundary_differences(snapshot, manifests))

    version_present = bool(relation_names & ALEMBIC_RELATIONS)
    version_structure_differences: list[CatalogDifference] = []
    if version_present:
        version_structure_differences = _manifest_differences(
            snapshot, manifests[ALEMBIC_MANIFEST_ID]
        )

    actual_app_names = relation_names & APPLICATION_TABLES
    actual_routines = frozenset(
        routine_identity(row) for row in snapshot.get("routines", [])
        if isinstance(row, Mapping)
    )
    base_manifest = manifests[BASE_MANIFEST_ID]
    actual_base_component = project_catalog_component(
        snapshot,
        relation_names=actual_app_names,
        routine_identities=actual_routines,
    )
    base_differences = diff_catalog(base_manifest.catalog, actual_base_component)

    # Package and global-object errors are independent of which managed profile
    # is closest.  They must block every otherwise exact classification.
    global_differences = list(differences)

    if version_present:
        version_errors = global_differences + version_structure_differences + base_differences
        if alembic_versions != (HEAD_REVISION,):
            version_errors.append(CatalogDifference(
                "alembic_version.rows", "revision_state_mismatch",
                [HEAD_REVISION], None if alembic_versions is None else list(alembic_versions),
            ))
        if version_errors:
            return _report(
                PreflightStatus.VERSION_STATE_INVALID,
                "alembic_version_present_but_invalid",
                "The database has version metadata but its revision or schema is invalid.",
                observation, packages, version_errors,
            )
        return _report(
            PreflightStatus.ALREADY_MANAGED,
            BASE_MANIFEST_ID,
            "The database is already at the application schema head.",
            observation, packages, (),
        )

    if not actual_app_names:
        empty_errors = global_differences + _unexpected_routine_differences(actual_routines)
        if empty_errors:
            return _report(
                PreflightStatus.SCHEMA_DRIFT,
                None,
                "The database has no application schema but contains unknown objects.",
                observation, packages, empty_errors,
            )
        return _report(
            PreflightStatus.UPGRADE_REQUIRED,
            None,
            "The application schema is empty; run Alembic upgrade instead of adoption.",
            observation, packages, (),
        )

    if not global_differences and not base_differences:
        return _report(
            PreflightStatus.EXACT_ADOPTABLE,
            BASE_MANIFEST_ID,
            "The unversioned schema exactly matches the reviewed base-full manifest; "
            "this is only a candidate for separate D2a2.2 approval, not authorization.",
            observation, packages, (),
        )

    docker_manifest = manifests[DOCKER_MANIFEST_ID]
    docker_component = project_catalog_component(
        snapshot,
        relation_names=actual_app_names & DOCKER_OVERLAP_TABLES,
        routine_identities=actual_routines,
    )
    docker_differences = diff_catalog(docker_manifest.catalog, docker_component)
    remaining_tables = APPLICATION_TABLES - DOCKER_OVERLAP_TABLES
    expected_remaining = project_catalog_component(
        base_manifest.catalog,
        relation_names=remaining_tables,
    )
    actual_remaining = project_catalog_component(
        snapshot,
        relation_names=actual_app_names & remaining_tables,
    )
    remaining_differences = diff_catalog(expected_remaining, actual_remaining)
    required_docker_packages = {DEMO_MANIFEST_ID, UUID_OSSP_MANIFEST_ID}
    docker_exact = (
        not global_differences
        and not docker_differences
        and required_docker_packages <= set(packages)
        and (
            actual_app_names == DOCKER_OVERLAP_TABLES
            or (
                actual_app_names == APPLICATION_TABLES
                and not remaining_differences
            )
        )
    )
    if docker_exact:
        profile = (
            "docker_only_v1"
            if actual_app_names == DOCKER_OVERLAP_TABLES
            else "docker_hybrid_v1"
        )
        return _report(
            PreflightStatus.KNOWN_INCOMPATIBLE,
            profile,
            "The Docker legacy is recognized but is not equivalent to Alembic 0001.",
            observation, packages, (),
        )

    closest_profile = BASE_MANIFEST_ID
    closest_differences = global_differences + base_differences
    docker_candidate = global_differences + docker_differences + remaining_differences
    if len(docker_candidate) < len(closest_differences):
        closest_profile = "docker_legacy_v1"
        closest_differences = docker_candidate
    return _report(
        PreflightStatus.SCHEMA_DRIFT,
        closest_profile,
        "The schema does not exactly match any frozen legacy profile.",
        observation, packages, closest_differences,
    )


def diff_catalog(
    expected: Mapping[str, Any], actual: Mapping[str, Any], *, limit: int = 100,
) -> list[CatalogDifference]:
    """Return identity-keyed catalog differences without list-shift noise."""
    differences: list[CatalogDifference] = []
    section_keys = {
        "relations": ("schema_name", "relation_name", "relation_kind"),
        "columns": ("schema_name", "relation_name", "ordinal_position", "column_name"),
        "constraints": ("schema_name", "relation_name", "constraint_name", "constraint_type"),
        "indexes": ("schema_name", "relation_name", "index_name"),
        "triggers": ("schema_name", "relation_name", "trigger_name"),
        "policies": ("schema_name", "relation_name", "policy_name"),
        "rules": ("schema_name", "relation_name", "rule_name"),
        "schema_privileges": ("schema_name", "grantee_class", "privilege_type"),
        "relation_privileges": (
            "schema_name", "relation_name", "grantee_class", "privilege_type",
        ),
        "column_privileges": (
            "schema_name", "relation_name", "ordinal_position", "column_name",
            "grantee_class", "privilege_type",
        ),
        "extensions": ("extension_name",),
        "routines": ("schema_name", "routine_name", "identity_arguments", "routine_kind"),
        "routine_privileges": (
            "schema_name", "routine_name", "identity_arguments", "routine_kind",
            "grantee_class", "privilege_type",
        ),
        "default_privileges": (
            "schema_name", "object_type", "grantee_class", "privilege_type",
        ),
        "types": ("schema_name", "type_name"),
        "sequences": ("schema_name", "sequence_name"),
        "event_triggers": ("event_trigger_name",),
        "dependency_edges": (
            "edge_kind", "source_schema", "source_relation", "source_routine",
            "source_arguments", "source_routine_kind", "source_rule",
            "target_schema", "target_relation", "target_column_number",
            "dependency_kind",
        ),
    }
    for section, keys in section_keys.items():
        expected_rows = _keyed_rows(expected.get(section, []), keys, section)
        actual_rows = _keyed_rows(actual.get(section, []), keys, section)
        for identity in sorted(set(expected_rows) | set(actual_rows)):
            path = f"{section}[{identity}]"
            if identity not in actual_rows:
                differences.append(CatalogDifference(path, "missing", expected_rows[identity], None))
            elif identity not in expected_rows:
                differences.append(CatalogDifference(path, "unexpected", None, actual_rows[identity]))
            elif expected_rows[identity] != actual_rows[identity]:
                expected_row = expected_rows[identity]
                actual_row = actual_rows[identity]
                for field in sorted(set(expected_row) | set(actual_row)):
                    if expected_row.get(field) != actual_row.get(field):
                        differences.append(CatalogDifference(
                            f"{path}.{field}", "changed",
                            expected_row.get(field), actual_row.get(field),
                        ))
            if len(differences) >= limit:
                return differences[:limit]
    return differences


def _report(
    status: PreflightStatus,
    profile_id: str | None,
    message: str,
    observation: LegacySchemaCatalog,
    packages: Sequence[str],
    differences: Sequence[CatalogDifference],
) -> LegacyPreflightReport:
    package_tuple = tuple(sorted(set(packages)))
    difference_tuple = tuple(differences[:100])
    # Preflight is expected to run with a read-only role while D2a2.2 adoption
    # needs a writer.  Bind the physical/logical target, not the session role.
    target_binding = {
        key: value for key, value in observation.target_identity.items()
        if key != "current_user"
    }
    digest_payload = {
        "target_identity": target_binding,
        "snapshot_sha256": observation.snapshot_sha256,
        "status": status.value,
        "profile_id": profile_id,
        "unmanaged_packages": package_tuple,
    }
    preflight_sha = hashlib.sha256(canonical_json_bytes(digest_payload)).hexdigest()
    return LegacyPreflightReport(
        status=status,
        profile_id=profile_id,
        message=message,
        target_identity=dict(observation.target_identity),
        snapshot_sha256=observation.snapshot_sha256,
        preflight_sha256=preflight_sha,
        unmanaged_packages=package_tuple,
        differences=difference_tuple,
    )


def _manifest_differences(
    snapshot: Mapping[str, Any], manifest: FrozenCatalogManifest,
) -> list[CatalogDifference]:
    actual = project_catalog_component(
        snapshot,
        relation_names=manifest.relation_names,
        extension_names=manifest.extension_names,
        routine_identities=manifest.routine_identities,
    )
    # A partial package must fail: projection alone cannot detect missing names.
    observed_names = _observed_relation_names(snapshot) & manifest.relation_names
    missing_names = sorted(manifest.relation_names - observed_names)
    differences = []
    if missing_names:
        differences.append(CatalogDifference(
            f"manifest.{manifest.manifest_id}.relations",
            "missing_package_relations", sorted(manifest.relation_names), sorted(observed_names),
        ))
    differences.extend(diff_catalog(manifest.catalog, actual))
    return differences


def _component_matches(
    snapshot: Mapping[str, Any], manifest: FrozenCatalogManifest,
) -> bool:
    return not _manifest_differences(snapshot, manifest)


def _observed_relation_names(snapshot: Mapping[str, Any]) -> frozenset[str]:
    rows = snapshot.get("relations", [])
    if not isinstance(rows, list):
        raise LegacyPreflightError("catalog relations must be a list")
    names = {
        row.get("relation_name") for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("relation_name"), str)
    }
    return frozenset(names)


def _cross_boundary_differences(
    snapshot: Mapping[str, Any],
    manifests: Mapping[str, FrozenCatalogManifest],
) -> list[CatalogDifference]:
    """Reject explicit managed/unmanaged FK and trigger dependency edges."""
    unmanaged_relations = DOCKER_DEMO_RELATIONS | LANGGRAPH_RELATIONS | ALEMBIC_RELATIONS

    def relation_zone(name: Any) -> str | None:
        if name in APPLICATION_TABLES:
            return "managed"
        if name in unmanaged_relations:
            return "unmanaged"
        return None

    managed_routines = (
        manifests[BASE_MANIFEST_ID].routine_identities
        | manifests[DOCKER_MANIFEST_ID].routine_identities
    )
    unmanaged_routines = (
        manifests[DEMO_MANIFEST_ID].routine_identities
        | manifests[LANGGRAPH_MANIFEST_ID].routine_identities
    )

    differences: list[CatalogDifference] = []
    constraints = snapshot.get("constraints", [])
    triggers = snapshot.get("triggers", [])
    if not isinstance(constraints, list) or not isinstance(triggers, list):
        raise LegacyPreflightError("catalog constraint/trigger sections must be lists")

    for row in constraints:
        if not isinstance(row, Mapping) or row.get("constraint_type") != "f":
            continue
        source = row.get("relation_name")
        target = row.get("target_relation")
        zones = {relation_zone(source), relation_zone(target)}
        if zones == {"managed", "unmanaged"}:
            differences.append(CatalogDifference(
                f"cross_boundary.constraint.{row.get('constraint_name')}",
                "managed_unmanaged_dependency",
                {"source": source, "target": target},
                None,
            ))

    for row in triggers:
        if not isinstance(row, Mapping):
            continue
        source = row.get("relation_name")
        routine = (
            f"{row.get('function_schema')}.{row.get('function_name')}"
            f"({row.get('function_arguments') or ''}):f"
        )
        relation_side = relation_zone(source)
        routine_side = (
            "managed" if routine in managed_routines
            else "unmanaged" if routine in unmanaged_routines
            else None
        )
        if {relation_side, routine_side} == {"managed", "unmanaged"}:
            differences.append(CatalogDifference(
                f"cross_boundary.trigger.{row.get('trigger_name')}",
                "managed_unmanaged_dependency",
                {"source": source, "target": routine},
                None,
            ))
    return differences


def _unexpected_routine_differences(routines: Iterable[str]) -> list[CatalogDifference]:
    values = sorted(set(routines))
    if not values:
        return []
    return [CatalogDifference("routines", "unexpected_routine", [], values)]


def _string_set(value: Any, filename: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LegacyPreflightError(f"manifest {filename} has an invalid string set")
    if len(value) != len(set(value)):
        raise LegacyPreflightError(f"manifest {filename} has duplicate names")
    return frozenset(value)


def _keyed_rows(rows: Any, keys: tuple[str, ...], section: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise LegacyPreflightError(f"catalog section {section} must be a list")
    keyed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise LegacyPreflightError(f"catalog section {section} contains a non-object")
        identity_values = tuple(row.get(key) for key in keys)
        identity = canonical_json_bytes(identity_values).decode("utf-8")
        if identity in keyed:
            raise LegacyPreflightError(f"catalog section {section} has duplicate identities")
        keyed[identity] = dict(row)
    return keyed


def _scalar_rows(result: Any) -> list[Any]:
    if hasattr(result, "scalars"):
        scalars = result.scalars()
        return list(scalars.all() if hasattr(scalars, "all") else scalars)
    if hasattr(result, "fetchall"):
        return [row[0] for row in result.fetchall()]
    raise LegacySchemaCatalogError("metadata query result has no scalar reader")
