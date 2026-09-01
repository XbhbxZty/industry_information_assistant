# Frozen legacy PostgreSQL fingerprints

These manifests are reviewed migration inputs, not generated application state.
Runtime preflight loads them from this directory and verifies each embedded
canonical SHA-256 before classifying a database.  It never derives an adoption
profile from current ORM metadata or the current Docker SQL.

All JSON files use ASCII transport with JSON Unicode escapes.  This is required
because the Windows console/patch boundary once replaced a Chinese default
value while the in-memory digest still described the original PostgreSQL text.
The loader rejected that file; ASCII transport makes the reviewed bytes stable
across terminals while decoding to the original Unicode value.

Captured on PostgreSQL 15 from disposable databases on 2026-09-01:

| Manifest | Meaning | Canonical catalog SHA-256 |
|---|---|---|
| `application_base_full_v1.json` | All 17 tables produced by importing `models` then `Base.metadata.create_all()` | `42e7c7604e8c74ebacc79a3aa82ec34fa488d03fa60ba0f985cb78f034f82044` |
| `application_docker_overlap_v1.json` | The six application tables created first by `docker/init-db/01-init.sql`, including their managed triggers/routine | `da89500e22607213fd0006a0ccff149e9f4520536f3e2d26d5fb91bc6fab4910` |
| `unmanaged_docker_demo_v1.json` | Seven Docker demo tables and their owned sequences | `87bc3bb786cd31a49da9b14db02dca068025008d25a3f5e66f545a1a6bbfe9c5` |
| `unmanaged_langgraph_postgres_3_1_2.json` | Four LangGraph tables and three provider indexes; metadata rows must be exactly migrations 0 through 9 | `3f61fe21c2abc98d39afe51d0ef43a817b5fe6ab9269dc5d814c01b065dda547` |
| `alembic_version_v1.json` | Alembic version table created by the current environment | `03bc2a00e9b18b92255e52f4cfa3f1c9a780ff7ad679707445f6706508c5cafa` |
| `extension_uuid_ossp_v1.json` | Optional `uuid-ossp` extension identity; extension-owned routines are excluded from routine comparison | `fc6d065a3e0b08b9685340fbb567a84d258cbf39c339f4a0b69b7a527c24495f` |

Only the exact base-full application component may proceed to a later adoption
approval.  Docker-only and Docker-hybrid components are recognized solely to
produce a precise `known_incompatible` result: their TIMESTAMPTZ columns,
server defaults, indexes, and update triggers are not equivalent to revision
`20260831_0001`.

The Docker demo and LangGraph manifests are exact unmanaged packages, never
prefix allowlists.  Table RLS flags and policies are part of each exact
fingerprint, while every database event trigger is rejected as unreviewed
global DDL behavior.  Partial packages, a provider migration-row mismatch, an
unknown table/schema/extension/routine, or any cross-boundary dependency causes
preflight to fail closed.  This directory contains no stamp or normalization
procedure; transactional adoption belongs to phase 3.4D2a2.2.
