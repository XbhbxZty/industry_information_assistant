# Database migration authority

Alembic revisions in this directory are the sole authority for the application
schema. Runtime startup and seed scripts never call `create_all()` and never
run Alembic implicitly: they only verify that `public.alembic_version` exactly
matches this checkout's dynamically resolved Alembic head.

Run from `backend/`:

```text
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

Run `upgrade head` explicitly before every backend startup. The Docker image and
`start-app.ps1` do this before launching Uvicorn; a direct local Uvicorn command
must do the same. Do not use `alembic stamp` to make a legacy database appear
current. A legacy database needs the reviewed adoption procedure; an unversioned
or out-of-date database will fail the read-only startup guard with the upgrade
command instead of being modified automatically.

The old `docker/init-db/01-init.sql` remains in the repository only for manual
legacy investigation. Root `docker-compose.yml` no longer mounts it, preserving
existing PostgreSQL volumes while preventing automatic legacy business DDL. The
seven restaurant/stock/legal/transport demo tables, if present in an old volume,
remain outside this managed application schema; Alembic never drops them.

## Company-profile audit chain (0002)

The audit-chain revision is `20260902_0002`; current head is `20260902_0003`.
Configure both settings from `.env.example`
before upgrading a database containing company profiles or using profile CRUD:

- `COMPANY_PROFILE_AUDIT_KEYS_JSON`: JSON object mapping key IDs to standard
  Base64-encoded, independently generated random keys of at least 32 bytes.
- `COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID`: the ID used to sign new records; keep
  all keys still referenced by historical audits/anchors in the JSON object.

There is no default key or fallback to JWT/checkpoint secrets. Do not commit
keys or print them in logs. The example value is deliberately not a usable key.
An empty schema can upgrade without keys, but profile writes/reads requiring
verification return 503 until valid keys are configured. Corrupt histories
return 409 without profile content. Templates and empty lists remain available.

0002 must run online: it first validates all existing profile/audit continuity
and domain data, then creates one migration-time anchor per legacy profile.
Original audit rows are not re-signed or rewritten. The anchor attests the
history observed at migration, not the authenticity of earlier events. A bad
history, orphan audit, or missing key rolls back the entire PostgreSQL upgrade,
including added columns, anchors and version-table changes. Investigate and
restore the correct data explicitly; never bypass the check using `stamp`.

New create/update/archive operations sign the actual before/after snapshots
and advance the profile head in one transaction. Detail/list/history/material
search and research-input reads validate the observed chain. PostgreSQL guards
reject UPDATE, DELETE and TRUNCATE on audit/anchor tables. Database owners or
superusers can bypass these guards; whole-database rollback is not detectable
without an external trusted head. This is not a claim of production hardening.

Before migrating a real database, stop writers and preserve a restorable backup
and the signing keys. Git rollback alone does not revert database schema.
`downgrade 20260831_0001` removes signatures and anchors (not domain snapshots),
so it is intended for disposable development data; restore the reviewed backup
for a real rollback. Re-upgrading after a downgrade cannot recover the original
signatures and can only anchor the then-observed history again.

Key rotation does not require another schema revision. See
[`docs/KEY_ROTATION.md`](../../docs/KEY_ROTATION.md) for the read-only reference
inventory, checkpoint v1/v2 compatibility, active-key switch and retirement checks.

## Checkpoint context and review claims (0003)

0003 makes checkpoint sessions unique, restricts non-null workflow statuses,
adds an actual UUID/pair foreign key for integrity metadata, and persists a
separate context seal over checkpoint ID, session ID, owner, status, business
revision and business MAC. Existing v1 graph/business signatures do not change.
The existing `business_revision` also advances on status changes; normal writes
take a short checkpoint row lock and PostgreSQL rejects non-increasing updates.

Before upgrading existing checkpoints, stop writers, preserve a restorable backup
and all historical checkpoint keys, and independently review current ownership
and status. See `docs/KEY_ROTATION.md` for checkpoint keyring configuration.
The migration rejects duplicate sessions, missing/mismatched integrity, invalid
status/UUID/version, missing keys and invalid graph/business seals. It never
deletes duplicates, repairs records or silently treats them as unsigned legacy.
Any failure rolls back both data changes and DDL; investigate offline, without
`stamp`. An empty database still upgrades without signing keys.

Valid old rows receive an `origin=migration_observed_v1` context seal: it attests
only what was observed during migration, NOT ownership/status authenticity before
that moment. Old MACs and revisions are preserved. The next normal write creates
a new `native_v1` context seal. All runtime checkpoint reads require valid paired
metadata and context; there is no missing-integrity compatibility bypass.

`research_review_claims` supplies one checkpoint-keyed row with separate reviewer,
opaque token, lease, basis version/seal, decision fields and lifecycle timestamps.
This revision provides the schema only. Atomic claiming/finalization, idempotency
and interrupted-stream recovery are D4b work, not yet exposed by this checkpoint.
The table is not available through the database explorer/Text2SQL demo allowlist.

`downgrade 20260902_0002` drops claim records and context signatures as well as
their constraints. Use it only on disposable development data; real rollback
requires a matching reviewed backup, code and keys. Git checkout alone does not
roll back a database, and upgrading again cannot recover original observations.

## Local legacy adoption (development/maintenance only)

This is a local command, not a web API or a signed approval service. Host access
and the configured PostgreSQL maintenance credentials are the authority boundary.
It supports only the reviewed PostgreSQL 15 `base-full` legacy schema. Docker
legacy variants and drift are rejected, never automatically repaired. Stop all
external writers and independently check the target and backup before applying.

Both commands load `backend/.env`; existing process environment variables take
precedence. Inspect the selected `DATABASE_URL` target before any apply:

```text
python app/scripts/adopt_legacy_schema.py inspect
```

Inspect performs no writes. Check `preflight.target_identity` and require
`preflight.status` to be `exact_adoptable`. Save `policy_template` and
`approval_template` as separate JSON files. Keep the reviewed policy in local
maintenance configuration (not an API request or source control), and set
`LEGACY_ADOPTION_POLICY_FILE` to its absolute path. Fill the blank approval fields:
operator reference, backup reference, maintenance-window reference, and the exact
confirmation phrase `I CONFIRM LEGACY SCHEMA ADOPTION`. These are attestations,
not proof that a backup is recoverable or that other writers have stopped.

The generated approval lasts 15 minutes; inspect again if expired. Do not alter
the policy ID/content or digest fields after review. Then run:

```text
python app/scripts/adopt_legacy_schema.py apply --approval /absolute/path/approval.json
python -m alembic upgrade head
```

Apply compares the policy, target and fresh preflight inside one locked writer
transaction, stamps only `20260831_0001`, and verifies before commit. JSON output
includes the approval ID, operator reference and outcome; save it with the local
maintenance record if needed. Exit 0 means adopted/already managed; 2 means
rejected; 3 means configuration/connection failure; 4 means outcome unknown.
For outcome unknown, **do not blindly retry**: run inspect read-only and check
whether the version is already managed. No persistent approval ledger or
production deployment automation is provided by this development tool.

The adoption catalog stays frozen at 0001. Do not run adoption against databases
already upgraded to 0002 or later; check those with `alembic current/check` and
the runtime head guard. New columns/tables are not legacy schema drift to repair.

For a fresh development environment, use a separately named empty database and
`upgrade head`; never remove/reinitialize an existing PostgreSQL volume.

Build the backend image from the repository root with
`docker build -f backend/app/Dockerfile backend`. The backend build context
contains migrations; `.dockerignore` excludes local environment secrets. Supply
configuration at container runtime instead of baking `.env` into the image.

To add those optional Text2SQL demonstration tables to a database that is
already at Alembic head, run this manual, one-time command from `backend/`:

```text
python app/scripts/seed_demo_data.py
```

It first uses the same read-only head guard, then creates and inserts all seven
demo tables in one transaction. It refuses if any demo table already exists;
it never replaces, appends to, or runs automatically for existing data.
